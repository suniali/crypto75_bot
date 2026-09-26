# tests/test_positions.py
import pytest
from unittest.mock import patch, MagicMock
from bot_app.bot import show_positions_handler, handle_position_actions
from conftest import create_mock_update

@pytest.mark.asyncio
async def test_show_positions_handler_success(mock_user_and_chat, mock_context):
    user, chat = mock_user_and_chat
    update = create_mock_update(user, chat, text="📊 پوزیشن‌های باز")

    fake_positions = [
        {
            "ticket": 1001,
            "symbol": "EURUSD",
            "type": "BUY",
            "volume": 0.1,
            "price_open": 1.08500,
            "price_current": 1.08700,
            "profit": 20.0,
            "sl": 1.08000,
            "tp": 1.09000,
        }
    ]

    with patch("bot_app.bot.get_open_positions", return_value=(True, fake_positions)):
        await show_positions_handler(update, mock_context)

        # ۱. ارسال لیست پوزیشن‌ها
        update.message.reply_text.assert_called_once()
        text_sent = update.message.reply_text.call_args[0][0]
        assert "EURUSD" in text_sent
        assert "20.0$" in text_sent

        # ۲. تنظیم JobQueue برای لایو آپدیت ۳ ثانیه‌ای
        mock_context.job_queue.run_repeating.assert_called_once()
        job_kwargs = mock_context.job_queue.run_repeating.call_args[1]
        assert job_kwargs["interval"] == 3


@pytest.mark.asyncio
async def test_handle_position_close_action(mock_user_and_chat, mock_context):
    user, chat = mock_user_and_chat
    # کالبک بستن پوزیشن شماره 1001
    update = create_mock_update(user, chat, callback_data="pos_detail_1001")
    update.callback_query.data = "close_pos_1001"

    await handle_position_actions(update, mock_context)

    # پیام تایید برای کاربر ارسال می‌شود
    update.callback_query.edit_message_text.assert_called_once()
    caption = update.callback_query.edit_message_text.call_args[0][0]
    assert "آیا از بستن کامل این پوزیشن اطمینان دارید؟" in caption