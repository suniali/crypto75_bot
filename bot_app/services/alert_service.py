import logging
from asgiref.sync import sync_to_async
from django.db import IntegrityError
from bot_app.models import TelegramUser, UserAlert
from bot_app.choices import MarketType

logger = logging.getLogger("price_checker")


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


# ------------------------------------------------------------------
# UserAlert Services (استفاده‌شده در price_checker.py و Handlers)
# ------------------------------------------------------------------

@sync_to_async
def fetch_active_alerts():
    return list(UserAlert.objects.filter(is_active=True).select_related('user'))


@sync_to_async
def deactivate_alert(alert: UserAlert) -> None:
    """غیرفعال‌سازی یک آلرت با update_fields جهت کارایی بیشتر دیتابیس"""
    alert.is_active = False
    alert.save(update_fields=["is_active"])
    logger.info("Alert ID #%s for symbol %s set to inactive.", alert.id, alert.symbol)


@sync_to_async
def deactivate_alert_by_id(alert_id: int) -> bool:
    """غیرفعال‌سازی مستقیم آلرت با استفاده از ID"""
    updated_count = UserAlert.objects.filter(id=alert_id, is_active=True).update(is_active=False)
    if updated_count > 0:
        logger.info("Alert ID #%s set to inactive.", alert_id)
        return True
    logger.warning("Alert ID #%s not found or already inactive.", alert_id)
    return False


@sync_to_async
def create_user_alert(
    user: TelegramUser,
    symbol: str,
    target_price: float,
    market_type: str = MarketType.CRYPTO
) -> tuple[UserAlert | None, bool]:
    """
    ثبت آلرت جدید برای کاربر با هندل کردن UniqueConstraint
    خروجی: (آلرت ایجاد شده، وضعیت موفقیت)
    """
    try:
        alert = UserAlert.objects.create(
            user=user,
            symbol=symbol.upper(),
            target_price=target_price,
            market_type=market_type,
            is_active=True
        )
        return alert, True
    except IntegrityError:
        logger.warning("Duplicate active alert attempted for user %s on %s at %s", user.chat_id, symbol, target_price)
        return None, False


@sync_to_async
def get_user_active_alerts(user: TelegramUser) -> list[UserAlert]:
    """دریافت لیست آلرت‌های فعال یک کاربر مشخص"""
    return list(UserAlert.objects.filter(user=user, is_active=True).order_by('-created_at'))