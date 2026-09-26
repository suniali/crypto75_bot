# tests/test_handlers.py
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from bot_app.bot import process_trade_image_handler, CONFIRM_JOURNAL_DATA
from telegram.ext import ConversationHandler
from conftest import create_mock_update


@pytest.mark.asyncio
async def test_process_trade_image_success(mock_user_and_chat, mock_context):
    user, chat = mock_user_and_chat
    update = create_mock_update(user, chat)

    # 1. ساخت فایل موک با متد async برای download_to_drive
    mock_file = AsyncMock()
    mock_file.download_to_drive = AsyncMock(return_value="fake_path.jpg")

    # 2. متد get_file در تلگرام یک کو-روتین است
    mock_photo = MagicMock()
    mock_photo.get_file = AsyncMock(return_value=mock_file)

    update.message.photo = [mock_photo]

    fake_ai_output = "SYMBOL: BTCUSDT\nTYPE: BUY\nENTRY: 65000"

    with patch("os.path.exists", return_value=True), \
            patch("os.remove"), \
            patch("PIL.Image.open"), \
            patch("bot_app.bot.extract_trade_from_image", new_callable=AsyncMock, return_value=fake_ai_output):
        next_state = await process_trade_image_handler(update, mock_context)

        # اکنون دانلود فایل و استخراج بدون استثنا انجام شده و کانورسیشن وارد گام بعدی می‌شود
        assert next_state == CONFIRM_JOURNAL_DATA

@pytest.mark.asyncio
async def test_process_trade_image_failure_handling(mock_user_and_chat, mock_context):
    user, chat = mock_user_and_chat
    update = create_mock_update(user, chat)

    mock_photo = MagicMock()
    mock_file = AsyncMock()
    mock_photo.get_file.return_value = mock_file
    update.message.photo = [mock_photo]

    # هوش مصنوعی خطا برمی‌گرداند
    with patch("os.path.exists", return_value=False), \
            patch("PIL.Image.open"), \
            patch("bot_app.bot.extract_trade_from_image", new_callable=AsyncMock, return_value="⚠️ تصویر خوانا نیست"):
        next_state = await process_trade_image_handler(update, mock_context)

        # باید بدون شکست کد، گفتگو متوقف شده و به منو برگردد
        assert next_state == ConversationHandler.END
        assert 'extracted_journal_data' not in mock_context.user_data