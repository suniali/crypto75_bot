# tests/test_alerts.py
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from bot_app.bot import (
    start_alert_wizard,
    add_alert_market_selected,
    add_alert_symbol_received,
    add_alert_price_received,
    ADD_ALERT_MARKET,
    ADD_ALERT_SYMBOL,
    ADD_ALERT_PRICE
)
from telegram.ext import ConversationHandler
from conftest import create_mock_update


@pytest.mark.asyncio
async def test_alert_wizard_full_flow(mock_user_and_chat, mock_context):
    user, chat = mock_user_and_chat

    # STEP 1: شروع و انتخاب بازار
    update1 = create_mock_update(user, chat, text="ثبت هشدار قیمت 🔔")
    next_state = await start_alert_wizard(update1, mock_context)

    assert next_state == ADD_ALERT_MARKET
    update1.message.reply_text.assert_called_once()

    # STEP 2: کلیک روی دکمه فارکس
    update2 = create_mock_update(user, chat, callback_data="market_forex")
    with patch("bot_app.bot.get_market_watch_symbols", return_value=["XAUUSD", "EURUSD"]):
        next_state = await add_alert_market_selected(update2, mock_context)

        assert next_state == ADD_ALERT_SYMBOL
        assert mock_context.user_data["is_forex"] is True

    # STEP 3: ارسال نماد XAUUSD
    update3 = create_mock_update(user, chat, callback_data="select_sym:XAUUSD")
    with patch("bot_app.bot.check_symbol_info", return_value=True):
        next_state = await add_alert_symbol_received(update3, mock_context)

        assert next_state == ADD_ALERT_PRICE
        assert mock_context.user_data["symbol"] == "XAUUSD"

    # STEP 4: ورود قیمت و تکمیل فرآیند ثبت
    update4 = create_mock_update(user, chat, text="2050.50")

    mock_user_db = MagicMock()
    mock_alert = MagicMock()

    with patch("bot_app.bot.get_or_create_user", new_callable=AsyncMock, return_value=mock_user_db), \
            patch("bot_app.bot.create_user_alert", new_callable=AsyncMock, return_value=(mock_alert, True)), \
            patch("httpx.AsyncClient.get", new_callable=AsyncMock), \
            patch("bot_app.bot.fetch_recent_klines", new_callable=AsyncMock, return_value=MagicMock(empty=False)), \
            patch("bot_app.bot.create_pending_alert_chart", return_value=b"fake_bytes_image"):
        final_state = await add_alert_price_received(update4, mock_context)

        assert final_state == ConversationHandler.END
        update4.message.reply_photo.assert_called_once()
        assert len(mock_context.user_data) == 0  # Context باید پاکسازی شود