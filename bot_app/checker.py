import asyncio
import logging
import httpx
from asgiref.sync import sync_to_async

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
# Helper & Service Functions
# ------------------------------------------------------------------
async def get_crypto_price(client: httpx.AsyncClient, symbol: str):
    url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}"
    try:
        res = await client.get(url, timeout=5.0)
        if res.status_code == 200:
            data = res.json()
            price = float(data["price"])
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

@sync_to_async
def deactivate_alert_by_id(alert_id: int):
    try:
        alert = UserAlert.objects.get(id=alert_id)
        alert.is_active = False
        alert.save(update_fields=["is_active"])
        logger.info("Alert ID #%s for symbol %s set to inactive.", alert.id, alert.symbol)
        return True
    except UserAlert.DoesNotExist:
        logger.warning("Alert ID #%s not found for deactivation.", alert_id)
        return False

def check_target_reached(current_price: float, target_price: float, alert_type: str) -> bool:
    if alert_type == "ABOVE":
        return current_price >= target_price
    elif alert_type == "BELOW":
        return current_price <= target_price

    return abs(current_price - target_price) <= (target_price * 0.0001)


async def process_alert(client: httpx.AsyncClient, alert: UserAlert) -> str | None:
    """
    بررسی یک آلرت:
    - در صورت رسیدن قیمت به تارگت، آلرت را غیرفعال کرده و متن پیام را برمی‌گرداند.
    - در غیر این صورت یا هنگام بروز خطا، None برمی‌گرداند.
    """
    logger.debug("Processing alert ID #%s (%s)", alert.id, alert.symbol)

    try:
        # ۱. دریافت قیمت فعلی (فارکس یا کریپتو)
        if alert.is_forex:
            loop = asyncio.get_running_loop()
            current_price = await loop.run_in_executor(None, get_forex_price, alert.symbol)
        else:
            current_price = await get_crypto_price(client, alert.symbol)

        if current_price is None:
            logger.warning("Could not retrieve current price for %s (Alert ID #%s)", alert.symbol, alert.id)
            return None

        target_price = float(alert.target_price)
        alert_type = getattr(alert, "alert_type", "BOTH")

        # ۲. بررسی شرایط رسیدن به تارگت
        if check_target_reached(current_price, target_price, alert_type):
            logger.info("Target price reached for %s! Current: %s | Target: %s", alert.symbol, current_price, target_price)

            # ۳. غیرفعال‌سازی آلرت در دیتابیس
            await deactivate_alert(alert)

            # ۴. ساخت و بازگرداندن متن پیام
            msg = (
                f"🚨 **هشدار قیمت رسید!** 🚨\n\n"
                f"📌 نماد: `{alert.symbol}`\n"
                f"🎯 قیمت هدف: `{target_price}`\n"
                f"📈 قیمت فعلی: `{current_price}`"
            )
            return msg

    except Exception as e:
        logger.exception("Error processing alert ID #%s (%s): %s", alert.id, alert.symbol, e)

    return None
