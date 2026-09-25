import logging
from asgiref.sync import sync_to_async

from bot_app.models import TelegramUser

logger = logging.getLogger("alert_service")

# ------------------------------------------------------------------
# TelegramUser Services
# ------------------------------------------------------------------

@sync_to_async
def get_or_create_user(chat_id: int, username: str = None, first_name: str = None) -> TelegramUser:
    """دریافت یا ثبت کاربر جدید در ربات تلگرام"""
    user, created = TelegramUser.objects.get_or_create(
        chat_id=chat_id,
        defaults={
            'username': username,
            'first_name': first_name
        }
    )
    if not created and (user.username != username or user.first_name != first_name):
        user.username = username
        user.first_name = first_name
        user.save(update_fields=['username', 'first_name'])
    return user

@sync_to_async
def set_users_blocked_status(user_id:int,is_blocked: bool):
    TelegramUser.objects.filter(chat_id=user_id).update(is_blocked=is_blocked)