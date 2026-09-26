# tests/test_positions.py
import pytest
from unittest.mock import patch, MagicMock,AsyncMock
from telegram.ext import ConversationHandler
from conftest import create_mock_update
from bot_app.bot import (
    show_positions_handler,
    handle_position_actions,
    receive_pending_exit_price,
    receive_pending_exit_ratio,
    INPUT_PENDING_EXIT_PRICE,
    INPUT_PENDING_EXIT_RATIO
)
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


@pytest.mark.asyncio
async def test_pending_exit_full_flow_success(mock_user_and_chat, mock_context):
    """تست سناریوی موفقیت‌آمیز جریان کامل پندینگ خروج (قیمت -> انتخاب نسبت -> ثبت در MT5)"""
    user, chat = mock_user_and_chat

    # ----------------------------------------------------
    # گام ۱: کاربر روی دکمه "⏰ پندینگ خروج حجمی" کلیک می‌کند
    # ----------------------------------------------------
    update_step1 = create_mock_update(user, chat, callback_data="action_pendingexit_1001")

    with patch("bot_app.bot.stop_all_live_jobs", new_callable=AsyncMock):
        next_state = await handle_position_actions(update_step1, mock_context)

        assert next_state == INPUT_PENDING_EXIT_PRICE
        assert mock_context.user_data["action_ticket"] == 1001
        update_step1.callback_query.edit_message_text.assert_called_once()

    # ----------------------------------------------------
    # گام ۲: کاربر قیمت Target خروج را ارسال می‌کند (مثلاً 2050.50)
    # ----------------------------------------------------
    update_step2 = create_mock_update(user, chat, text="2050.50")

    next_state = await receive_pending_exit_price(update_step2, mock_context)

    assert next_state == INPUT_PENDING_EXIT_RATIO
    assert mock_context.user_data["exit_price"] == 2050.50
    update_step2.message.reply_text.assert_called_once()

    # ----------------------------------------------------
    # گام ۳: کاربر درصد خروج را انتخاب می‌کند (مثلاً ۵۰٪ یا ratio_0.5)
    # ----------------------------------------------------
    update_step3 = create_mock_update(user, chat, callback_data="ratio_0.5_1001")

    # موک کردن تابع ثبت در متاتریدر
    mock_mt5_response = (True, "✅ **سفارش پندینگ خروج ثبت شد.**\n\n📦 **حجم:** `0.05` لات\n🎯 **قیمت:** `2050.5`")

    with patch("bot_app.bot.place_partial_exit_pending_order", return_value=mock_mt5_response) as mock_place_order:
        final_state = await receive_pending_exit_ratio(update_step3, mock_context)

        # ۱. بررسی خروج موفق از Conversation
        assert final_state == ConversationHandler.END

        # ۲. بررسی فراخوانی تابع MT5 با آرگومان‌های درست (ticket, price, ratio)
        mock_place_order.assert_called_once_with(1001, 2050.50, 0.5)

        # ۳. بررسی پاکسازی context
        assert "action_ticket" not in mock_context.user_data
        assert "exit_price" not in mock_context.user_data


@pytest.mark.asyncio
async def test_pending_exit_invalid_price_handling(mock_user_and_chat, mock_context):
    """تست مدیریت خطای ورود قیمت نامعتبر (مثلاً متن یا عدد منفی)"""
    user, chat = mock_user_and_chat
    mock_context.user_data["action_ticket"] = 1001

    # ارسال قیمت نامعتبر
    update = create_mock_update(user, chat, text="-100")

    next_state = await receive_pending_exit_price(update, mock_context)

    # سیستم باید در همان گام بماند و پیام هشدار بدهد
    assert next_state == INPUT_PENDING_EXIT_PRICE
    update.message.reply_text.assert_called_once()
    assert "قیمت معتبر" in update.message.reply_text.call_args[0][0]


@pytest.mark.asyncio
async def test_pending_exit_broker_failure_handling(mock_user_and_chat, mock_context):
    """تست هندلینگ زمانی که بروکر یا متاتریدر خطا برمی‌گرداند"""
    user, chat = mock_user_and_chat
    mock_context.user_data["action_ticket"] = 1001
    mock_context.user_data["exit_price"] = 2050.50

    update = create_mock_update(user, chat, callback_data="ratio_0.5_1001")

    # موک کردن پاسخ خطای متاتریدر
    mock_mt5_error = (False, "❌ خطای بروکر در ثبت سفارش پندینگ: `10013`")

    with patch("bot_app.bot.place_partial_exit_pending_order", return_value=mock_mt5_error):
        final_state = await receive_pending_exit_ratio(update, mock_context)

        assert final_state == ConversationHandler.END
        # پیام خطا باید به کاربر نمایش داده شود
        args, _ = update.callback_query.edit_message_text.call_args
        assert "خطای بروکر" in args[0]