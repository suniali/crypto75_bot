import asyncio
import logging
import html
import time

from decouple import config

from telegram import Bot
from telegram.request import HTTPXRequest

from bot_app.utils.keyboard_helper import MAIN_KEYBOARD

from bot_app.models import TelegramUser

logger = logging.getLogger(__name__)

# اگر رباتت پروکسی نیاز داره یا request سفارشی داری تنظیم می‌کنی
request_config = HTTPXRequest(connection_pool_size=8, read_timeout=30.0)
bot = Bot(token=config('TELEGRAM_BOT_TOKEN'), request=request_config)

def send_telegram_trade_alert(signal_data):

    action_emoji = '🟢 BUY SIGNAL' if signal_data['action'] == 'BUY' else '🔴 SELL SIGNAL'

    features = signal_data['features']

    symbol_safe = html.escape(str(signal_data.get('symbol', '')))
    
    # قالب‌بندی پیام با استایل مناسب تلگرام (Markdown)
    message = f"""
        {action_emoji} | <b>{symbol_safe}</b>

        🤖 <b>هوش مصنوعی (XGBoost):</b> تایید شد ({signal_data.get('ai_confidence', 0)}٪ اطمینان)
        💰 <b>قیمت ورود:</b> <code>{signal_data.get('entry_price', 0)}</code>
        🎯 <b>حد سود (TP):</b> <code>{signal_data.get('take_profit', 0)}</code>
        🛡 <b>حد ضرر (SL):</b> <code>{signal_data.get('stop_loss', 0)}</code>
        📊 <b>حجم پیشنهادی (Risk 1%):</b> <code>{signal_data.get('calculated_volume', 0)}</code> لوت

        📉 <b>شاخص‌ها و وضعیت بازار در لحظه تصمیم:</b>
        • <b>RSI (14):</b> <code>{features.get('rsi', 0):.2f}</code>
        • <b>ATR (14):</b> <code>{features.get('atr', 0):.5f}</code>
        • <b>روند کلی (Trend):</b> <code>{'صعودی 📈' if features.get('trend_direction') == 1 else 'نزولی 📉'}</code>
        • <b>تغییرات قیمت:</b> <code>{features.get('price_change', 0):.2f}%</code>

        🔖 <i>شناسه لاگ تصمیم‌گیری: #{signal_data.get('log_id', 'N/A')}</i>
    """

    async def _send():
        async with bot:
            time.sleep(2)
            user = await TelegramUser.objects.afirst()
            if user:
                await bot.send_message(
                    chat_id=user.chat_id,
                    text=message,
                    parse_mode='HTML',
                    reply_markup=MAIN_KEYBOARD,
                ) 
                logging.info('✅ Telegram signal alert sent successfully!')
            else:
                logging.warning('⚠️ Telegram signal not sent !! No User Exists yet !!')
    
    try:
        # اجرا کردن تابع async درون محیط همزمان جنگو/تسک
        asyncio.run(_send())
    except Exception as e:
        logging.error(f'❌ Telegram Send Error: {e}')
