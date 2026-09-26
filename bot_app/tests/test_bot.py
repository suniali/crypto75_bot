import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from telegram import Update, User, Chat, CallbackQuery, Message, Bot
from telegram.ext import ContextTypes, ConversationHandler

import bot_app.bot


# ------------------------------------------------------------------
# Fixtures (تدارک اشیاء مورد نیاز تلگرام)
# ------------------------------------------------------------------
@pytest.fixture
def mock_user():
    """ایجاد یک کاربر فرضی تلگرام"""
    return User(id=123456789, first_name="Ali", is_bot=False, username="ali_test")


@pytest.fixture
def mock_chat():
    """ایجاد یک چت فرضی تلگرام"""
    return Chat(id=123456789, type="private")


@pytest.fixture
def mock_update(mock_user, mock_chat):
    """ایجاد شیء Update به همراه Message فرضی"""
    update = MagicMock(spec=Update)
    update.effective_user = mock_user
    update.effective_chat = mock_chat

    message = AsyncMock(spec=Message)
    message.reply_text = AsyncMock()
    message.reply_photo = AsyncMock()
    message.text = "test"
    update.message = message
    update.callback_query = None
    return update


@pytest.fixture
def mock_callback_update(mock_user):
    """ایجاد شیء Update به همراه CallbackQuery و Mock برای effective_chat"""
    update = MagicMock(spec=Update)
    update.effective_user = mock_user

    # ساخت یک effective_chat اختصاصی با متدهای AsyncMock
    effective_chat = MagicMock(spec=Chat)
    effective_chat.id = 123456789
    effective_chat.type = "private"
    effective_chat.send_document = AsyncMock()  # <--- متد send_document به AsyncMock تبدیل می‌شود
    effective_chat.send_message = AsyncMock()

    update.effective_chat = effective_chat

    # ساخت CallbackQuery و Message
    query = AsyncMock(spec=CallbackQuery)
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.data = ""

    cb_message = AsyncMock(spec=Message)
    cb_message.reply_text = AsyncMock()
    query.message = cb_message

    update.callback_query = query
    update.message = None

    return update


@pytest.fixture
def mock_context():
    """ایجاد شیء Context واقعی/فرضی برای نگهداری داده‌ها"""
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.user_data = {}
    context.bot = AsyncMock()
    context.job_queue = MagicMock()
    return context


# ------------------------------------------------------------------
# 1. Tests for Start & Stop Handlers
# ------------------------------------------------------------------
@pytest.mark.asyncio
@patch("bot_app.bot.set_users_blocked_status", new_callable=AsyncMock)
async def test_start_command(mock_set_status, mock_update, mock_context):
    """تست دستور /start و ارسال پیام خوش‌آمدگویی"""
    await bot_app.bot.start(mock_update, mock_context)

    # بررسی ثبت کاربر به عنوان unblocked در دیتابیس
    mock_set_status.assert_called_once_with(123456789, False)
    # بررسی ارسال پیام پاسخ
    mock_update.message.reply_text.assert_called_once()
    args, kwargs = mock_update.message.reply_text.call_args
    assert "خوش آمدید" in args[0] or "سلام" in args[0]


@pytest.mark.asyncio
@patch("bot_app.bot.set_users_blocked_status", new_callable=AsyncMock)
async def test_stop_command(mock_set_status, mock_update, mock_context):
    """تست دستور /stop و لغو تسک‌ها"""
    # ساخت یک Mock غیر async برای Task جهت جلوگیری از RuntimeWarning
    fake_task = MagicMock()
    fake_task.done.return_value = False  # متد done به صورت همگام مقدار False می‌دهد
    fake_task.cancel = MagicMock()

    bot_app.bot.ACTIVE_WORKERS["123456789_BTCUSDT_5m_CRYPTO"] = fake_task

    await bot_app.bot.stop_command_handler(mock_update, mock_context)

    # بررسی اجرای لغو تسک
    fake_task.cancel.assert_called_once()
    assert "123456789_BTCUSDT_5m_CRYPTO" not in bot_app.bot.ACTIVE_WORKERS


# ------------------------------------------------------------------
# 2. Tests for Alert Creation Wizard
# ------------------------------------------------------------------
@pytest.mark.asyncio
async def test_start_alert_wizard(mock_update, mock_context):
    """تست شروع مراحل ایجاد هشدار جدید"""
    state = await bot_app.bot.start_alert_wizard(mock_update, mock_context)

    assert state == bot_app.bot.ADD_ALERT_MARKET
    mock_update.message.reply_text.assert_called_once()


@pytest.mark.asyncio
@patch("bot_app.bot.get_market_watch_symbols")
async def test_add_alert_market_selected_forex(mock_get_symbols, mock_callback_update, mock_context):
    """تست انتخاب بازار فارکس و دریافت لیست نمادها"""
    mock_get_symbols.return_value = ["EURUSD", "GBPUSD"]
    mock_callback_update.callback_query.data = "market_forex"

    state = await bot_app.bot.add_alert_market_selected(mock_callback_update, mock_context)

    assert state == bot_app.bot.ADD_ALERT_SYMBOL
    assert mock_context.user_data["is_forex"] is True
    mock_callback_update.callback_query.edit_message_text.assert_called_once()


@pytest.mark.asyncio
@patch("bot_app.bot.get_or_create_user", new_callable=AsyncMock)
@patch("bot_app.bot.create_user_alert", new_callable=AsyncMock)
@patch("bot_app.bot.fetch_recent_klines", new_callable=AsyncMock)
async def test_add_alert_price_received_success(
        mock_klines, mock_create_alert, mock_get_user, mock_update, mock_context
):
    """تست مرحله نهایی ثبت قیمت و ذخیره آلرت"""
    mock_update.message.text = "2050.5"
    mock_context.user_data = {"symbol": "XAUUSD", "is_forex": True, "last_message_id": 99}

    mock_get_user.return_value = MagicMock()
    mock_create_alert.return_value = (MagicMock(), True)  # alert_obj, created=True
    mock_klines.return_value = None  # فرستادن نان برای چارت جهت ساده‌تر شدن تست

    state = await bot_app.bot.add_alert_price_received(mock_update, mock_context)

    assert state == ConversationHandler.END
    mock_create_alert.assert_called_once()
    mock_update.message.reply_text.assert_called_once()


# ------------------------------------------------------------------
# 3. Tests for Position & Trade Handlers
# ------------------------------------------------------------------
@pytest.mark.asyncio
@patch("bot_app.bot.get_open_positions")
async def test_show_positions_handler_empty(mock_get_positions, mock_update, mock_context):
    """تست نمایش لیست پوزیشن‌ها زمانی که هیچ پوزیشن بازی وجود ندارد"""
    mock_get_positions.return_value = (True, [])

    await bot_app.bot.show_positions_handler(mock_update, mock_context)

    mock_update.message.reply_text.assert_called_once()
    args, _ = mock_update.message.reply_text.call_args
    assert "هیچ پوزیشن بازی وجود ندارد" in args[0]


@pytest.mark.asyncio
@patch("bot_app.bot.get_open_positions")
async def test_show_positions_handler_with_data(mock_get_positions, mock_update, mock_context):
    """تست نمایش پوزیشن‌های باز و شروع Job لایو"""
    fake_positions = [
        {"ticket": 1001, "symbol": "EURUSD", "type": "BUY", "volume": 0.1, "price_open": 1.08, "price_current": 1.085,
         "profit": 50.0}
    ]
    mock_get_positions.return_value = (True, fake_positions)
    mock_update.message.reply_text.return_value = MagicMock(message_id=555)

    await bot_app.bot.show_positions_handler(mock_update, mock_context)

    # بررسی ثبت تایمر (Job) لایو برای بروزرسانی
    mock_context.job_queue.run_repeating.assert_called_once()
    mock_update.message.reply_text.assert_called_once()


# ------------------------------------------------------------------
# 4. Tests for Report Generation
# ------------------------------------------------------------------
@pytest.mark.asyncio
@patch("bot_app.bot.get_trades_history")
@patch("bot_app.bot.analyze_trades_with_gemini", new_callable=AsyncMock)
@patch("bot_app.bot.generate_pdf_report")
@patch("os.path.exists", return_value=True)
@patch("os.remove")
@patch("builtins.open", create=True)
async def test_process_report_generation(
        mock_open, mock_remove, mock_exists, mock_pdf, mock_gemini, mock_history, mock_callback_update, mock_context
):
    """تست کامل فرآیند تولید و ارسال گزارش PDF"""
    mock_callback_update.callback_query.data = "rep_7"
    mock_history.return_value = ([{"ticket": 1, "profit": 10}], None)  # trades, error
    mock_gemini.return_value = "تحلیل هوش مصنوعی مثبت است."

    state = await bot_app.bot.process_report_generation(mock_callback_update, mock_context)

    assert state == ConversationHandler.END
    mock_history.assert_called_once_with(7)
    mock_gemini.assert_called_once()
    mock_pdf.assert_called_once()
    # بررسی ارسال فایل PDF به تلگرام
    mock_callback_update.effective_chat.send_document.assert_called_once()