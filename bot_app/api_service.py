import io
import asyncio
import logging
import httpx
import pandas as pd

from bot_app.mt5_service import get_rates_data
# ------------------------------------------------------------------
# Logging Configuration
# ------------------------------------------------------------------
logger = logging.getLogger("price_checker")
logger.setLevel(logging.INFO)

formatter = logging.Formatter(
    fmt="%(asctime)s | %(levelname)-7s | %(funcName)s:%(lineno)d - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

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