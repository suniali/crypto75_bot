import pandas as pd
import pandas_ta as ta
import httpx
from scipy.signal import argrelextrema
import numpy as np

from bot_app.mt5_service import get_data_for_rsi


def find_pivots(series, order=3):
    """
    پیدا کردن نقاط چرخش (قله‌ها و دره‌ها)
    order=3 یعنی حداقل ۳ کندل قبل و بعد باید از این نقطه پایین‌تر/بالاتر باشند.
    """
    pivots_high = argrelextrema(series.values, np.greater, order=order)[0]
    pivots_low = argrelextrema(series.values, np.less, order=order)[0]
    return pivots_high, pivots_low


def detect_divergence(df, lookback=30):
    """
    بررسی وجود واگرایی مثبت یا منفی در lookback کندل اخیر
    """
    # استفاده از کندل‌های اخیر برای تحلیل سریع‌تر
    df_recent = df.tail(lookback).copy().reset_index(drop=True)

    price = df_recent['close']
    rsi = df_recent['rsi']

    p_highs, p_lows = find_pivots(price, order=2)

    div_status = "بدون واگرایی"

    # ۱. بررسی واگرایی منفی (Bearish Divergence) - روی قله‌ها
    if len(p_highs) >= 2:
        last_high = p_highs[-1]
        prev_high = p_highs[-2]

        # شرط قیمت: سقف بالاتر | شرط RSI: سقف پایین‌تر
        if price.iloc[last_high] > price.iloc[prev_high] and rsi.iloc[last_high] < rsi.iloc[prev_high]:
            div_status = "⚠️ **واگرایی منفی (Bearish Divergence)** ➔ احتمال ریزش شدید"

    # ۲. بررسی واگرایی مثبت (Bullish Divergence) - روی دره‌ها
    if len(p_lows) >= 2:
        last_low = p_lows[-1]
        prev_low = p_lows[-2]

        # شرط قیمت: کف پایین‌تر | شرط RSI: کف بالاتر
        if price.iloc[last_low] < price.iloc[prev_low] and rsi.iloc[last_low] > rsi.iloc[prev_low]:
            div_status = "🚀 **واگرایی مثبت (Bullish Divergence)** ➔ احتمال صعود شدید"

    return div_status


async def calculate_rsi(symbol, timeframe, market_type="CRYPTO"):
    try:
        if market_type == "CRYPTO":
            url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={timeframe}&limit=100"
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url)
                if response.status_code != 200:
                    return None, None, f"⚠️ **خطا در دریافت داده‌ها از بایننس!**"
                res = response.json()

            if not res or isinstance(res, dict):
                return None, None, f"⚠️ **داده‌ای برای نماد `{symbol}` یافت نشد!**"

            df = pd.DataFrame(res,
                              columns=['time', 'open', 'high', 'low', 'close', 'volume', '_', '_', '_', '_', '_', '_'])
            df['close'] = df['close'].astype(float)

        elif market_type == "FOREX":
            rates, msg = get_data_for_rsi(symbol, timeframe)
            if rates is None:
                return None, None, msg
            df = pd.DataFrame(rates)

        else:
            return None, None, f"❌ **نوع بازار نامعتبر است:** `{market_type}`"

        if len(df) < 30:
            return None, None, "⚠️ **تعداد کندل‌ها برای تشخیص واگرایی کافی نیست.**"

        # محاسبه RSI
        df['rsi'] = ta.rsi(df['close'], length=14)
        latest_rsi = df['rsi'].iloc[-1]

        if pd.isna(latest_rsi):
            return None, None, "⚠️ **خطا در محاسبه RSI!**"

        latest_rsi = round(latest_rsi, 2)

        # تعیین وضعیت اشباع
        if latest_rsi >= 70:
            status = "🔴 OVERBOUGHT (اشباع خرید)"
        elif latest_rsi <= 30:
            status = "🟢 OVERSOLD (اشباع فروش)"
        else:
            status = "⚪️ NORMAL (محدوده خنثی)"

        # تشخیص واگرایی
        divergence = detect_divergence(df)

        return latest_rsi, status, divergence

    except Exception as e:
        return None, None, f"🚨 **خطای غیرمنتظره:** `{e}`"