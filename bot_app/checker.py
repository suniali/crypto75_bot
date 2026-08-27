import asyncio
import os
import sys
import django
import requests
from pathlib import Path
from decouple import config

from asgiref.sync import sync_to_async
from telegram import Bot

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from bot_app.models import UserAlert

TOKEN=config('TELEGRAM_BOT_TOKEN')

# Get Price From Binance
def get_crypto_price(symbol):
    try:
        url=f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}"
        responce=requests.get(url,timeout=5)
        if responce.status_code==200:
            data=responce.json()
            return float(data["price"])
    except Exception as e:
        print(f"Error fetching price for {symbol}: {e}")

    return None

# Get User Alerts From DB
@sync_to_async
def get_active_alerts():
    return list(UserAlert.objects.filter(is_active=True))

@sync_to_async
def deactive_alert(alert):
    alert.is_active=False
    alert.save()

async def check_alerts_loop():
    bot=Bot(token=TOKEN)
    print("سرویس بررسی قیمت‌ها و ارسال هشدار روشن شد...")

    while True:
        alerts=await get_active_alerts()

        for alert in alerts:
            current_price=get_crypto_price(alert.symbol)
            print(f"Checking {alert.symbol}: Current={current_price} | Target={alert.target_price}")

            if current_price >= alert.target_price:
                msg = (
                    f"🚨 **هشدار قیمت رسید!** 🚨\n\n"
                    f"📌 نماد: `{alert.symbol}`\n"
                    f"🎯 قیمت هدف شما: {alert.target_price}\n"
                    f"📈 قیمت فعلی بازار: {current_price}"
                )

                try:
                    # Send Message To Telegram
                    await bot.send_message(chat_id=alert.chat_id, text=msg,parse_mode="Markdown")
                    # Deactivate Alert From Database
                    await deactive_alert(alert)
                    print(f"Alert sent to {alert.chat_id} for {alert.symbol}")
                except Exception as e:
                    print(f"Error sending telegram message: {e}")

        await asyncio.sleep(30)

if __name__ == "__main__":
    asyncio.run(check_alerts_loop())