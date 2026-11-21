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


def _format_strategy_details(action: str, features: dict) -> tuple[str, str, str]:
    """استخراج و فارسی‌سازی جزئیات اردربلاک و واگرایی RSI برای نمایش در تلگرام"""
    ob_type_label = 'صعودی (Bullish OB)' if action == 'BUY' else 'نزولی (Bearish OB)'

    ob_low = features.get('ob_low')
    ob_high = features.get('ob_high')
    if ob_low is not None and ob_high is not None:
        ob_zone_label = f'{float(ob_low):.2f} - {float(ob_high):.2f}'
    else:
        ob_zone_label = 'نامشخص'

    div_info = features.get('divergence') or {}
    if isinstance(div_info, dict) and div_info.get('direction'):
        div_dir = 'صعودی (Bullish)' if div_info.get('direction') == 'BULLISH' else 'نزولی (Bearish)'
        div_type = 'منظم (Regular)' if div_info.get('div_type') == 'REGULAR' else 'مخفی (Hidden)'
        div_strength = float(div_info.get('strength', 0)) * 100
        div_label = f'{div_dir} - {div_type} ({div_strength:.0f}%)'
    else:
        div_label = 'تایید شده'

    return ob_type_label, ob_zone_label, div_label


def send_telegram_trade_alert(signal_data: dict):
    action = signal_data.get('action', 'BUY')
    action_emoji = '🟢 BUY SIGNAL (خرید)' if action == 'BUY' else '🔴 SELL SIGNAL (فروش)'

    features = signal_data.get('features') or {}
    symbol_safe = html.escape(str(signal_data.get('symbol', '')))

    ob_type_label, ob_zone_label, div_label = _format_strategy_details(action, features)

    risk_pct = features.get('risk_percentage', 1.0)
    ml_prob_pct = float(features.get('ml_probability', 0.5)) * 100
    trend_label = 'صعودی 📈' if features.get('trend_direction') == 1 else 'نزولی 📉'

    message = (
        f"{action_emoji} | <b>{symbol_safe}</b>\n\n"
        f"🧩 <b>استراتژی:</b> اردربلاک معتبر + واگرایی RSI\n"
        f"🧱 <b>نوع اردربلاک:</b> <code>{html.escape(ob_type_label)}</code>\n"
        f"📐 <b>محدوده ناحیه OB:</b> <code>{html.escape(ob_zone_label)}</code>\n"
        f"🔀 <b>واگرایی RSI:</b> <code>{html.escape(div_label)}</code>\n\n"
        f"🤖 <b>امتیاز نهایی سیستم:</b> <code>{signal_data.get('ai_confidence', 0)}٪</code> (تایید ML: <code>{ml_prob_pct:.1f}٪</code>)\n"
        f"💰 <b>قیمت ورود:</b> <code>{signal_data.get('entry_price', 0)}</code>\n"
        f"🎯 <b>حد سود (TP):</b> <code>{signal_data.get('take_profit', 0)}</code>\n"
        f"🛡 <b>حد ضرر (SL):</b> <code>{signal_data.get('stop_loss', 0)}</code>\n"
        f"📊 <b>حجم پیشنهادی (Risk {risk_pct}%):</b> <code>{signal_data.get('calculated_volume', 0)}</code> لوت\n\n"
        f"📉 <b>وضعیت اندیکاتورها در لحظه ورود:</b>\n"
        f"• <b>RSI (14):</b> <code>{float(features.get('rsi', 0)):.2f}</code>\n"
        f"• <b>ATR (14):</b> <code>{float(features.get('atr', 0)):.2f}</code>\n"
        f"• <b>روند کلان (SMA 50/200):</b> <code>{trend_label}</code>\n\n"
        f"🔖 <i>شناسه لاگ تصمیم‌گیری: #{signal_data.get('log_id', 'N/A')}</i>"
    )

    async def _send():
        async with bot:
            await asyncio.sleep(1)
            user = await TelegramUser.objects.filter(is_blocked=False).afirst()
            if user:
                await bot.send_message(
                    chat_id=user.chat_id,
                    text=message,
                    parse_mode='HTML',
                    reply_markup=MAIN_KEYBOARD,
                )
                logger.info('✅ Telegram signal alert sent successfully!')
            else:
                logger.warning('⚠️ Telegram signal not sent! No active user found.')

    try:
        asyncio.run(_send())
    except Exception as e:
        logger.error(f'❌ Telegram Send Error: {e}')