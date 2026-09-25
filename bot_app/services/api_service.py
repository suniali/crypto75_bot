import asyncio
import logging
from logging.handlers import RotatingFileHandler
import httpx
import pandas as pd

from bot_app.services.mt5_service import get_rates_data

# ------------------------------------------------------------------
# Logging Configuration (Rotating File Handler)
# ------------------------------------------------------------------
logger = logging.getLogger("api_service")
logger.setLevel(logging.INFO)

formatter = logging.Formatter(
    fmt="%(asctime)s | %(levelname)-7s | %(funcName)s:%(lineno)d - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

if not logger.handlers:
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    # چرخش فایل لاگ پس از رسیدن به ۵ مگابایت
    file_handler = RotatingFileHandler(
        "api_service.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)


# ------------------------------------------------------------------
# API Services & Fetching Logic
# ------------------------------------------------------------------

async def fetch_with_retry(
        client: httpx.AsyncClient,
        url: str,
        retries: int = 2,
        timeout: float = 5.0
) -> httpx.Response | None:
    """دریافت داده‌های HTTP با قابلیت تلاش مجدد (Retry Mechanism)"""
    for attempt in range(retries + 1):
        try:
            res = await client.get(url, timeout=timeout)
            if res.status_code == 200:
                return res
            logger.warning("Attempt %d/%d: status %s for %s", attempt + 1, retries + 1, res.status_code, url)
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            logger.warning("Attempt %d/%d failed for %s: %s", attempt + 1, retries + 1, url, e)

        if attempt < retries:
            await asyncio.sleep(0.5 * (attempt + 1))  # 0.5s, 1.0s, ...
    return None


async def get_crypto_price(client: httpx.AsyncClient, symbol: str) -> float | None:
    """دریافت قیمت لحظه‌ای کریپتو از بایننس"""
    url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol.upper()}"
    try:
        res = await fetch_with_retry(client, url)
        if res:
            data = res.json()
            return float(data["price"])
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
    و خروجی به‌صورت pandas.DataFrame استاندارد شده برای mplfinance
    """
    symbol = symbol.upper()
    try:
        # -------------------------------------------------------------
        # ۱. بخش فارکس (با استفاده از MetaTrader5)
        # -------------------------------------------------------------
        if is_forex:
            # اجرا در ThreadPoolExecutor چون MetaTrader5 به‌صورت Sync کار می‌کند
            rates, msg = await asyncio.to_thread(get_rates_data, symbol, interval)

            if rates is False or rates is None or len(rates) == 0:
                logger.warning("Failed to fetch MT5 rates for %s: %s", symbol, msg)
                return None

            df = pd.DataFrame(rates)
            df['timestamp'] = pd.to_datetime(df['time'], unit='s')

            df.rename(columns={
                'open': 'Open',
                'high': 'High',
                'low': 'Low',
                'close': 'Close',
                'tick_volume': 'Volume'
            }, inplace=True)

            df.set_index('timestamp', inplace=True)
            return df[['Open', 'High', 'Low', 'Close']].tail(limit)

        # -------------------------------------------------------------
        # ۲. بخش کریپتو (با استفاده از Binance API با Retry)
        # -------------------------------------------------------------
        else:
            url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
            res = await fetch_with_retry(client, url)

            if res and res.status_code == 200:
                raw_data = res.json()
                if not raw_data:
                    logger.warning("Empty klines data returned for %s", symbol)
                    return None

                # تبدیل مستقیم لیست به دیتافریم بدون حلقه پایتونی (بسیار سریع‌تر)
                df = pd.DataFrame(raw_data, columns=[
                    'timestamp', 'Open', 'High', 'Low', 'Close', 'Volume',
                    'close_time', 'quote_asset_volume', 'number_of_trades',
                    'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
                ])

                df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
                df['Open'] = df['Open'].astype(float)
                df['High'] = df['High'].astype(float)
                df['Low'] = df['Low'].astype(float)
                df['Close'] = df['Close'].astype(float)

                df.set_index('timestamp', inplace=True)
                return df[['Open', 'High', 'Low', 'Close']]
            else:
                logger.warning("Failed to fetch Binance Klines for %s", symbol)

    except Exception as e:
        logger.exception("Error in fetch_recent_klines for %s (is_forex=%s): %s", symbol, is_forex, e)

    return None