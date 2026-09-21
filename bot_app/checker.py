import io
import asyncio
import logging
import httpx
import pandas as pd
from asgiref.sync import sync_to_async

from bot_app.models import UserAlert
from bot_app.mt5_service import get_forex_price,get_rates_data
from bot_app.generate_hiken_chart import create_heikin_ashi_chart

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


async def fetch_recent_klines(
        client: httpx.AsyncClient,
        symbol: str,
        interval: str = "1h",
        limit: int = 50,
        is_forex: bool = False
) -> pd.DataFrame | None:
    """
    دریافت کندل‌های اخیر برای فارکس (MetaTrader5) یا کریپتو (Binance)
    و خروجی به صورت pandas.DataFrame آماده برای mplfinance
    """
    try:
        # -------------------------------------------------------------
        # ۱. بخش فارکس (با استفاده از تابع get_rates_data و MetaTrader5)
        # -------------------------------------------------------------
        if is_forex:
            loop = asyncio.get_running_loop()

            # اجرا در ترد جداگانه چون متاتریدر به صورت sync کار می‌کند
            rates, msg = await loop.run_in_executor(None, get_rates_data, symbol, interval)

            if rates is False or rates is None or len(rates) == 0:
                logger.warning("Failed to fetch MT5 rates for %s: %s", symbol, msg)
                return None

            # تبدیل خروجی copy_rates_from_pos به دیتافریم
            df = pd.DataFrame(rates)

            # تبدیل زمان (unix timestamp به datetime)
            df['timestamp'] = pd.to_datetime(df['time'], unit='s')

            # تغییر نام ستون‌ها به ساختار مورد نیاز mplfinance
            df.rename(columns={
                'open': 'Open',
                'high': 'High',
                'low': 'Low',
                'close': 'Close',
                'tick_volume': 'Volume'
            }, inplace=True)

            df.set_index('timestamp', inplace=True)

            # محدود کردن تعداد کندل‌ها به limit درخواست‌شده
            return df[['Open', 'High', 'Low', 'Close']].tail(limit)

        # -------------------------------------------------------------
        # ۲. بخش کریپتو (با استفاده از Binance API)
        # -------------------------------------------------------------
        else:
            url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
            res = await client.get(url, timeout=5.0)

            if res.status_code == 200:
                raw_data = res.json()
                data = []
                for item in raw_data:
                    data.append({
                        'timestamp': pd.to_datetime(item[0], unit='ms'),
                        'Open': float(item[1]),
                        'High': float(item[2]),
                        'Low': float(item[3]),
                        'Close': float(item[4]),
                    })
                df = pd.DataFrame(data)
                df.set_index('timestamp', inplace=True)
                return df
            else:
                logger.warning("Binance Klines API returned status %s for %s", res.status_code, symbol)

    except Exception as e:
        logger.exception("Error in fetch_recent_klines for %s (is_forex=%s): %s", symbol, is_forex, e)

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

def check_target_reached(current_price: float, target_price: float, alert_type: str, is_forex: bool = False) -> bool:
    """
    بررسی رسیدن قیمت به تارگت:
    - اگر ABOVE باشد: قیمت فعلی باید بزرگتر یا مساوی تارگت باشد.
    - اگر BELOW باشد: قیمت فعلی باید کوچکتر یا مساوی تارگت باشد.
    - اگر BOTH یا مشخص‌نشده باشد: فقط بر اساس برخورد یا رد شدن از مرز قیمت برخورد می‌شود.
    """
    if alert_type == "ABOVE":
        return current_price >= target_price
    elif alert_type == "BELOW":
        return current_price <= target_price

    # اگر alert_type روی BOTH یا خالی بود:
    # تلرانس کاملاً حذف شده تا از فعال شدن اشتباهی در فواصل دور جلوگیری شود.
    # فقط زمانی True می‌دهد که قیمت دقیقاً برابر با قیمت هدف شود.
    return current_price == target_price


async def process_alert(client: httpx.AsyncClient, alert: UserAlert) -> tuple[str, io.BytesIO | None] | None:
    """
    بررسی یک آلرت:
    در صورت رسیدن قیمت به تارگت، آلرت را غیرفعال کرده و (متن پیام, تصویر چارت) را برمی‌گرداند.
    """
    logger.debug("Processing alert ID #%s (%s)", alert.id, alert.symbol)

    try:
        # ۱. دریافت قیمت فعلی (فارکس یا کریپتو)
        if alert.is_forex:
            loop = asyncio.get_running_loop()
            current_price = None
            for attempt in range(2):
                current_price = await loop.run_in_executor(None, get_forex_price, alert.symbol)
                if current_price is not None:
                    break
                await asyncio.sleep(0.5)
        else:
            current_price = await get_crypto_price(client, alert.symbol)

        if current_price is None:
            logger.warning("Could not retrieve current price for %s (Alert ID #%s)", alert.symbol, alert.id)
            return None

        target_price = float(alert.target_price)
        alert_type = getattr(alert, "alert_type", "BOTH")

        # ۲. بررسی شرایط رسیدن به تارگت
        if check_target_reached(current_price, target_price, alert_type, alert.is_forex):
            logger.info("Target price reached for %s! Current: %s | Target: %s", alert.symbol, current_price, target_price)

            # ۳. غیرفعال‌سازی آلرت در دیتابیس
            await deactivate_alert(alert)

            # ۴. دریافت داده کندل‌ها و تولید چارت هیکن آشی
            df_klines = await fetch_recent_klines(
                client=client,
                symbol=alert.symbol,
                interval="30M",
                limit=50,
                is_forex=alert.is_forex
            )

            chart_buf = None
            if df_klines is not None and not df_klines.empty:
                chart_buf = create_heikin_ashi_chart(df_klines, target_price, alert.symbol)

            # ۵. ساخت متن پیام
            msg = (
                f"🚨 **هشدار قیمت فعال شد!** 🚨\n\n"
                f"📌 نماد: `{alert.symbol}`\n"
                f"🎯 قیمت هدف: `{target_price}`\n"
                f"📈 قیمت فعلی: `{current_price:.5f}`\n\n"
                f"📊 *چارت هیکن آشی با خط قرمز نشان‌دهنده تارگت فعال‌شده است.*"
            )
            return msg, chart_buf

    except Exception as e:
        logger.exception("Error processing alert ID #%s (%s): %s", alert.id, alert.symbol, e)

    return None
