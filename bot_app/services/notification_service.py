"""Outbox Pattern: مدیریت مطمئن ارسال سیگنال‌ها به تلگرام با Retry خودکار."""
import logging
from datetime import timedelta

from django.db import close_old_connections, transaction
from django.utils import timezone

from bot_app.models import TradeDecisionLog
from bot_app.services.telegram_service import try_send_signal,try_send_super_scalp_signal

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 10                # حداکثر 10 بار تلاش
RETRY_WINDOW_MINUTES = 120      # فقط سیگنال‌های 2 ساعت اخیر را retry کن
BATCH_SIZE = 10                 # در هر اجرا حداکثر 10 سیگنال


MAX_ATTEMPTS = 10                # حداکثر 10 بار تلاش
RETRY_WINDOW_MINUTES = 120      # فقط سیگنال‌های 2 ساعت اخیر را retry کن
BATCH_SIZE = 10                 # در هر اجرا حداکثر 10 سیگنال


def enqueue_signal_for_notification(log_id: int):
    """سیگنال را برای ارسال در پس‌زمینه علامت‌گذاری می‌کند."""
    TradeDecisionLog.objects.filter(id=log_id).update(
        notification_status='pending',
        notification_attempts=0,
    )
    logger.info(f"📤 Signal #{log_id} enqueued for notification.")


def _compute_backoff_seconds(attempts: int) -> int:
    """زمان انتظار قبل از تلاش مجدد: 2, 4, 8, 16, 32, 64, 128, 256, 300, 300 ثانیه"""
    return min(2 ** attempts, 300)


def process_pending_notifications():
    """این تابع توسط APScheduler هر 30 ثانیه صدا زده می‌شود."""
    close_old_connections()
    cutoff = timezone.now() - timedelta(minutes=RETRY_WINDOW_MINUTES)

    pending = TradeDecisionLog.objects.filter(
        notification_status='pending',
        notification_attempts__lt=MAX_ATTEMPTS,
        timestamp__gte=cutoff,
    ).order_by('timestamp')[:BATCH_SIZE]

    for log in pending:
        # چک Exponential Backoff
        if log.notification_attempts > 0:
            wait_seconds = _compute_backoff_seconds(log.notification_attempts)
            last_try = log.notification_sent_at or log.created_at
            if timezone.now() < last_try + timedelta(seconds=wait_seconds):
                continue

        snapshot = log.features_snapshot or {}
        strategy_type = str(snapshot.get('strategy_type') or '').upper()

        signal_data = {
            'log_id': log.id,
            'symbol': log.symbol,
            'action': log.action,
            'timeframe': snapshot.get('timeframe', 'M1'),
            'features': snapshot,
            'calculated_volume': snapshot.get('calculated_volume', 0),
            'risk_amount_usd': snapshot.get('risk_amount_usd', 0),
        }

        # انتخاب فرستنده بر اساس نوع استراتژی
        if strategy_type == 'SUPER_SCALP':
            success, msg_id, error = try_send_super_scalp_signal(signal_data)
        else:
            success, msg_id, error = try_send_signal(signal_data)

        # به‌روزرسانی دیتابیس
        try:
            with transaction.atomic():
                log.refresh_from_db()
                log.notification_attempts += 1
                log.notification_sent_at = timezone.now()
                if success:
                    log.notification_status = 'sent'
                    log.telegram_message_id = msg_id
                    log.last_notification_error = None
                    logger.info(f"✅ Signal #{log.id} ({strategy_type or 'NDS'}) sent successfully (msg_id={msg_id}).")
                else:
                    log.last_notification_error = str(error)[:500]
                    if log.notification_attempts >= MAX_ATTEMPTS:
                        log.notification_status = 'failed'
                        logger.error(f"❌ Signal #{log.id} failed permanently after {MAX_ATTEMPTS} attempts: {error}")
                    else:
                        logger.warning(f"⚠️ Signal #{log.id} attempt {log.notification_attempts} failed: {error}")
                log.save(update_fields=[
                    'notification_status', 'notification_attempts',
                    'last_notification_error', 'notification_sent_at',
                    'telegram_message_id',
                ])
        except Exception as e:
            logger.exception(f"Failed to update notification status for log {log.id}:{e}")