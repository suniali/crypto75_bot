import asyncio
import logging
import os
import sys
from pathlib import Path

import django
import httpx
from asgiref.sync import sync_to_async
from decouple import config
from telegram import Bot

# ------------------------------------------------------------------
# Bootstrap Django Settings
# ------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from bot_app.models import UserAlert
from bot_app.mt5_service import get_forex_price

# ------------------------------------------------------------------
# Logging Configuration
# ------------------------------------------------------------------
logger = logging.getLogger("price_checker")
logger.setLevel(logging.INFO)

formatter = logging.Formatter(
    fmt="%(asctime)s | %(levelname)-7s | %(funcName)s:%(lineno)d - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# Console Handler
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

# File Handler
file_handler = logging.FileHandler("price_alerts.log", encoding="utf-8")
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

# ------------------------------------------------------------------
# Configuration Constants
# ------------------------------------------------------------------
TOKEN = config("TELEGRAM_BOT_TOKEN")


# ------------------------------------------------------------------
# Helper & Service Functions
# ------------------------------------------------------------------
async def get_crypto_price(client: httpx.AsyncClient, symbol: str):
    url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}"
    try:
        res = await client.get(url, timeout=5.0)
        if res.status_code == 200:
            data = res.json()
            price = float(data["price"])
            logger.debug("Fetched Crypto price for %s: %s", symbol, price)
            return price
        logger.warning("Binance API returned status code %s for %s", res.status_code, symbol)
    except Exception as e:
        logger.error("Error fetching crypto price for (%s): %s", symbol, e)
    return None


@sync_to_async
def fetch_active_alerts():
    return list(UserAlert.objects.filter(is_active=True))


@sync_to_async
def deactivate_alert(alert: UserAlert):
    alert.is_active = False
    alert.save(update_fields=["is_active"])
    logger.info("Alert ID #%s for symbol %s set to inactive.", alert.id, alert.symbol)


def check_target_reached(current_price: float, target_price: float, alert_type: str) -> bool:
    if alert_type == "ABOVE":
        return current_price >= target_price
    elif alert_type == "BELOW":
        return current_price <= target_price

    return abs(current_price - target_price) <= (target_price * 0.0001)


async def process_alert(bot: Bot, client: httpx.AsyncClient, alert: UserAlert) -> None:
    logger.debug("Processing alert ID #%s (%s)", alert.id, alert.symbol)

    if alert.is_forex:
        loop = asyncio.get_running_loop()
        current_price = await loop.run_in_executor(None, get_forex_price, alert.symbol)
    else:
        current_price = await get_crypto_price(client, alert.symbol)

    if current_price is None:
        logger.warning("Could not retrieve current price for %s (Alert ID #%s)", alert.symbol, alert.id)
        return

    target_price = float(alert.target_price)
    alert_type = getattr(alert, "alert_type", "BOTH")

    if check_target_reached(current_price, target_price, alert_type):
        logger.info("Target price reached for %s! Current: %s | Target: %s", alert.symbol, current_price, target_price)
        msg = (
            f"🚨 **هشدار قیمت رسید!** 🚨\n\n"
            f"📌 نماد: `{alert.symbol}`\n"
            f"🎯 قیمت هدف: `{target_price}`\n"
            f"📈 قیمت فعلی: `{current_price}`"
        )
        try:
            await bot.send_message(chat_id=alert.chat_id, text=msg, parse_mode="Markdown")
            logger.info("Telegram notification sent successfully to chat_id: %s", alert.chat_id)
            await deactivate_alert(alert)
        except Exception as e:
            logger.exception("Failed to send Telegram notification to %s: %s", alert.chat_id, e)


async def check_alerts_loop() -> None:
    bot = Bot(token=TOKEN)
    logger.info("🚀 Price Monitoring Worker started...")

    async with httpx.AsyncClient() as client:
        while True:
            try:
                alerts = await fetch_active_alerts()
                if alerts:
                    logger.debug("Checking %s active alerts...", len(alerts))
                    tasks = [process_alert(bot, client, alert) for alert in alerts]
                    await asyncio.gather(*tasks)
            except Exception as e:
                logger.exception("Unexpected error in main alert loop: %s", e)

            await asyncio.sleep(2)


if __name__ == "__main__":
    try:
        asyncio.run(check_alerts_loop())
    except KeyboardInterrupt:
        logger.info("🛑 Price Monitoring Worker stopped manually.")