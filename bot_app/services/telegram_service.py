import asyncio
import logging
import html
import time

from decouple import config

from telegram import Bot,Update
from telegram.ext import ContextTypes
from telegram.request import HTTPXRequest

from bot_app.utils.keyboard_helper import MAIN_KEYBOARD

from bot_app.models import TelegramUser

logger = logging.getLogger(__name__)

# اگر رباتت پروکسی نیاز داره یا request سفارشی داری تنظیم می‌کنی
request_config = HTTPXRequest(connection_pool_size=8, read_timeout=30.0)
bot = Bot(token=config('TELEGRAM_BOT_TOKEN'), request=request_config)

async def security_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await TelegramUser.objects.filter(is_blocked=False).afirst()
    if not user or update.effective_user.id != user.chat_id:
        user_id = update.effective_user.id if update.effective_user else 'Unknown'
        logger.warning(f'⚠️ Unauthorized access attempt by user ID: {user_id}')
        if update.message:
            await update.message.reply_text('⛔ Access Denied.')
        return False
    return True

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


def _send_to_owner(message: str, reply_markup=None):
    async def _send():
        async with bot:
            await asyncio.sleep(1)
            user = await TelegramUser.objects.filter(is_blocked=False).afirst()
            if user:
                await bot.send_message(
                    chat_id=user.chat_id,
                    text=message,
                    parse_mode='HTML',
                    reply_markup=reply_markup,
                )
                logger.info('✅ Telegram message sent successfully!')
            else:
                logger.warning('⚠️ Telegram message not sent! No active user found.')

    try:
        asyncio.run(_send())
    except Exception as e:
        logger.error(f'❌ Telegram Send Error: {e}')


def send_telegram_trade_alert(signal_data: dict):
    _send_to_owner(build_nds_message(signal_data), reply_markup=MAIN_KEYBOARD)


def send_telegram_text(text: str):
    """پیام ساده (مثلاً گزارش آموزش مدل). توسط nds_trainer استفاده می‌شه."""
    _send_to_owner(html.escape(text))