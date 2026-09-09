import asyncio
import logging
import os
import sys
from pathlib import Path

import httpx
from asgiref.sync import sync_to_async
from decouple import config
import django
from pydantic.v1.typing import update_field_forward_refs
from telegram import Bot

# Bootstrap Django Settings
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from bot_app.models import UserAlert
from bot_app.mt5_service import get_forex_price

logging.basicConfig(level=logging.INFO,format="%(asctime)s - %(levelname)s - %(message)s")
TOKEN=config('TELEGRAM_BOT_TOKEN')

# Get Price From Binance
async def get_crypto_price(client:httpx.AsyncClient,symbol):
    url=f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}"
    try:
        res =await client.get(url,timeout=5.0)
        if res.status_code == 200:
            data=res.json()
            return float(data["price"])
    except Exception as e:
        logging.error(f"خطا در دریافت قیمت کریپتو ({symbol}): {e}")
    return None

@sync_to_async
def fetch_active_alerts():
    return list(UserAlert.objects.filter(is_active=True))

@sync_to_async
def deactivate_alert(alert:UserAlert):
    alert.is_active=False
    alert.save(update_fields=["is_active"])

def check_target_reached(current_price:float,target_price:float,alert_type:str) -> bool:
    if alert_type == "ABOVE":
        return current_price >= target_price
    elif alert_type == "BELOW":
        return current_price <= target_price

    return abs(current_price-target_price) <= (target_price * 0.0001)

async def process_alert(bot:Bot,client:httpx.AsyncClient,alert:UserAlert) -> None:
    if alert.is_forex:
        loop = asyncio.get_running_loop()
        current_price= await loop.run_in_executor(None,get_forex_price,alert.symbol)
    else:
        current_price= await get_crypto_price(client,alert.symbol)

    if current_price is None:
        return

    target_price=float(alert.target_price)
    alert_type=getattr(alert,'alert_type','BOTH')

    if check_target_reached(current_price, target_price, alert_type):
        msg = (
            f"🚨 **هشدار قیمت رسید!** 🚨\n\n"
            f"📌 نماد: `{alert.symbol}`\n"
            f"🎯 قیمت هدف: `{target_price}`\n"
            f"📈 قیمت فعلی: `{current_price}`"
        )
        try:
            await bot.send_message(chat_id=alert.chat_id, text=msg, parse_mode="Markdown")
            await deactivate_alert(alert)
            logging.info(f"هشدار ارسال شد: {alert.symbol} برای {alert.chat_id}")
        except Exception as e:
            logging.error(f"خطا در ارسال پیام تلگرام: {e}")

async def check_alerts_loop() -> None:
    bot=Bot(token=TOKEN)
    logging.info("سرویس بررسی قیمت‌ها روشن شد (HTTPX)...")

    async with httpx.AsyncClient() as client:
        while True:
            alerts = await fetch_active_alerts()
            if alerts:
                tasks=[process_alert(bot,client,alert) for alert in alerts]
                await asyncio.gather(*tasks)

            await asyncio.sleep(2)

if __name__ == "__main__":
    asyncio.run(check_alerts_loop())