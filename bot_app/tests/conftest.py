# tests/conftest.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from telegram import User, Chat, Message, Update, CallbackQuery
from telegram.ext import ContextTypes

@pytest.fixture(autouse=True)
def enable_db_access_for_all_tests(db):
    """فعالسازی دسترسی به دیتابیس جنگو برای همه تست‌ها"""
    pass

@pytest.fixture
def mock_bot():
    """موک ساختن شیء اصلی Bot"""
    bot = AsyncMock()
    bot.send_message = AsyncMock()
    bot.send_photo = AsyncMock()
    bot.edit_message_text = AsyncMock()
    return bot

@pytest.fixture
def mock_context(mock_bot):
    """ساخت Context ساختگی تلگرام"""
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.bot = mock_bot
    context.user_data = {}
    context.chat_data = {}
    context.job_queue = MagicMock()
    return context

@pytest.fixture
def mock_user_and_chat():
    """ساخت کاربر و چت نمونه"""
    user = User(id=123456, first_name="TestUser", is_bot=False, username="testuser")
    chat = Chat(id=123456, type="private")
    return user, chat


def create_mock_update(user, chat, text=None, callback_data=None):
    """تابع کمکی جهت ساخت Update سفارشی (پیام متنی یا کالبک دکمه)"""
    update = MagicMock(spec=Update)
    update.effective_user = user

    # ساخت موک async برای chat
    mock_chat = AsyncMock(spec=Chat)
    mock_chat.id = chat.id
    mock_chat.type = chat.type
    mock_chat.send_action = AsyncMock()  # حل خطای TypeError

    update.effective_chat = mock_chat

    if callback_data:
        query = AsyncMock(spec=CallbackQuery)
        query.data = callback_data
        query.message = AsyncMock(spec=Message)
        query.message.chat = mock_chat
        query.message.reply_text = AsyncMock()
        query.edit_message_text = AsyncMock()
        query.answer = AsyncMock()
        update.callback_query = query
        update.message = None
    else:
        message = AsyncMock(spec=Message)
        message.text = text
        message.message_id = 999
        message.chat = mock_chat
        message.reply_text = AsyncMock()
        message.reply_photo = AsyncMock()
        update.message = message
        update.callback_query = None

    return update