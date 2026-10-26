import asyncio
from django.conf import settings
from telegram import Bot
from telegram.request import HTTPXRequest
from decouple import config

from bot_app.utils.keyboard_helper import MAIN_KEYBOARD

# اگر رباتت پروکسی نیاز داره یا request سفارشی داری تنظیم می‌کنی
request_config = HTTPXRequest(connection_pool_size=8, read_timeout=30.0)
bot = Bot(token=config('TELEGRAM_BOT_TOKEN'), request=request_config)

def send_telegram_trade_alert(signal_data):

    action_emoji = '🟢 BUY SIGNAL' if signal_data['action'] == 'BUY' else '🔴 SELL SIGNAL'

    features = signal_data['features']
    
    # قالب‌بندی پیام با استایل مناسب تلگرام (Markdown)
    message = f"""
    {action_emoji} | <b>{signal_data['symbol']}</b>

    🤖 <b>هوش مصنوعی (XGBoost):</b> تایید شد ({signal_data['ai_confidence']}٪ اطمینان)
    💰 <b>قیمت ورود:</b> <code>{signal_data['entry_price']}</code>
    🎯 <b>حد سود (TP):</b> <code>{signal_data['take_profit']}</code>
    🛡 <b>حد ضرر (SL):</b> <code>{signal_data['stop_loss']}</code>
    📊 <b>حجم پیشنهادی (Risk 1%):</b> <code>{signal_data['calculated_volume']}</code> لوت

    📉 <b>شاخص‌ها و وضعیت بازار در لحظه تصمیم:</b>
    • <b>RSI (14):</b> <code>{features.get('rsi', 0):.2f}</code>
    • <b>ATR (14):</b> <code>{features.get('atr', 0):.5f}</code>
    • <b>روند کلی (Trend):</b> <code>{'صعودی 📈' if features.get('trend_direction') == 1 else 'نزولی 📉'}</code>
    • <b>تغییرات قیمت:</b> <code>{features.get('price_change', 0):.2f}%</code>

    🔖 <i>شناسه لاگ تصمیم‌گیری: #{signal_data.get('log_id', 'N/A')}</i>
    """

    async def _send():
        async with bot:
            await bot.send_message(
                chat_id=settings.TELEGRAM_CHAT_ID,
                text=message,
                parse_mode='Markdown',
                reply_markup=MAIN_KEYBOARD,
            ) 
    try:
        # اجرا کردن تابع async درون محیط همزمان جنگو/تسک
        asyncio.run(_send())
        print('✅ Telegram signal alert sent successfully!')
    except Exception as e:
        print(f'❌ Telegram Send Error: {e}')