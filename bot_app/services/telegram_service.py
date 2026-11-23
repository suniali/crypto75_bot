import asyncio
import logging
import html

from decouple import config

from telegram import Bot, Update
from telegram.ext import ContextTypes
from telegram.request import HTTPXRequest

from bot_app.utils.keyboard_helper import MAIN_KEYBOARD
from bot_app.models import TelegramUser

logger = logging.getLogger(__name__)

# کاهش تایم‌اوت‌ها برای اینکه در صورت قطعی شبکه، سریع شکست بخورد و برود سراغ Retry
request_config = HTTPXRequest(connection_pool_size=8, read_timeout=10.0, connect_timeout=10.0)
bot = Bot(token=config('TELEGRAM_BOT_TOKEN'), request=request_config)


# ==========================================
# بخش ۱: توابع امنیتی (برای ربات تلگرام)
# ==========================================
async def security_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await TelegramUser.objects.filter(is_blocked=False).afirst()
    if not user or update.effective_user.id != user.chat_id:
        user_id = update.effective_user.id if update.effective_user else 'Unknown'
        logger.warning(f'⚠️ Unauthorized access attempt by user ID: {user_id}')
        if update.message:
            await update.message.reply_text('⛔ Access Denied.')
        return False
    return True


# ==========================================
# بخش ۲: ساخت متن پیام
# ==========================================
def build_nds_message(signal_data: dict) -> str:
    """متن HTML سیگنال NDS: توضیح کامل «چرا» + حجم پیشنهادی."""
    f = signal_data.get('features') or {}
    explanation = str(f.get('nds_explanation') or '')[:3000]
    risk_pct = f.get('risk_percentage', 1.0)
    return '\n'.join([
        html.escape(explanation),
        '',
        f"📊 <b>حجم پیشنهادی (ریسک {risk_pct}%):</b> <code>{signal_data.get('calculated_volume', 0)}</code> لوت",
        f"💵 <b>مبلغ ریسک:</b> <code>{signal_data.get('risk_amount_usd', 0)}</code>$",
        f"🔖 <i>شناسه لاگ: #{signal_data.get('log_id', 'N/A')}</i>",
    ])


# ==========================================
# بخش ۳: موتور ارسال با قابلیت Retry (Outbox)
# ==========================================
async def _try_send_async(message: str, reply_markup=None) -> tuple:
    """برمی‌گرداند: (موفقیت، message_id، متن خطا)"""
    try:
        # نکته: در نسخه‌های جدید python-telegram-bot استفاده از async with bot ضروری نیست
        # مگر اینکه ربات را در حال خاموش کردن باشد. اما برای امنیت نگه می‌داریم.
        user = await TelegramUser.objects.filter(is_blocked=False).afirst()
        if not user:
            return False, None, "No active user found"
            
        msg = await bot.send_message(
            chat_id=user.chat_id,
            text=message,
            parse_mode='HTML',
            reply_markup=reply_markup,
        )
        return True, msg.message_id, None
    except Exception as e:
        return False, None, str(e)


def try_send_signal(signal_data: dict) -> tuple:
    """ارسال سیگنال (توسط notification_service.py صدا زده می‌شود)"""
    try:
        message = build_nds_message(signal_data)
        return asyncio.run(_try_send_async(message, reply_markup=MAIN_KEYBOARD))
    except Exception as e:
        return False, None, f"asyncio wrapper error: {e}"


def try_send_text(text: str) -> tuple:
    """ارسال پیام متنی ساده (مثلاً گزارش آموزش مدل توسط nds_trainer.py)"""
    try:
        return asyncio.run(_try_send_async(html.escape(text)))
    except Exception as e:
        return False, None, f"asyncio wrapper error: {e}"