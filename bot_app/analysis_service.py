import os
import logging
import httpx
import asyncio
import numpy as np
import pandas as pd
import pandas_ta as ta
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from scipy.signal import argrelextrema
from google import genai
from google.genai import types
from decouple import config

from bot_app.mt5_service import get_rates_data

# ------------------------------------------------------------------
# Logging Configuration
# ------------------------------------------------------------------
logger = logging.getLogger("analysis_service")
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
file_handler = logging.FileHandler("analysis_service.log", encoding="utf-8")
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)


# ------------------------------------------------------------------
# Indicator & Analysis Functions
# ------------------------------------------------------------------
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
    df_recent = df.tail(lookback).copy()
    df_recent['index_orig'] = df_recent.index
    df_recent = df_recent.reset_index(drop=True)

    price = df_recent['close']
    rsi = df_recent['rsi']

    p_highs, p_lows = find_pivots(price, order=2)

    div_status = "بدون واگرایی"
    div_type = None
    p1_idx, p2_idx = None, None

    # ۱. بررسی واگرایی منفی (Bearish Divergence) - روی قله‌ها
    if len(p_highs) >= 2:
        last_high = p_highs[-1]
        prev_high = p_highs[-2]

        if price.iloc[last_high] > price.iloc[prev_high] and rsi.iloc[last_high] < rsi.iloc[prev_high]:
            div_status = "⚠️ **واگرایی منفی (Bearish Divergence)** ➔ احتمال ریزش"
            div_type = "BEARISH"
            p1_idx = df_recent.loc[prev_high, 'index_orig']
            p2_idx = df_recent.loc[last_high, 'index_orig']
            logger.info("Bearish Divergence detected between index %s and %s", p1_idx, p2_idx)

    # ۲. بررسی واگرایی مثبت (Bullish Divergence) - روی دره‌ها
    if len(p_lows) >= 2:
        last_low = p_lows[-1]
        prev_low = p_lows[-2]

        if price.iloc[last_low] < price.iloc[prev_low] and rsi.iloc[last_low] > rsi.iloc[prev_low]:
            div_status = "🚀 **واگرایی مثبت (Bullish Divergence)** ➔ احتمال صعود"
            div_type = "BULLISH"
            p1_idx = df_recent.loc[prev_low, 'index_orig']
            p2_idx = df_recent.loc[last_low, 'index_orig']
            logger.info("Bullish Divergence detected between index %s and %s", p1_idx, p2_idx)

    return div_status, div_type, p1_idx, p2_idx, df_recent


async def calculate_rsi(symbol, timeframe, market_type="CRYPTO"):
    logger.debug("Calculating RSI for %s | Timeframe: %s | Market: %s", symbol, timeframe, market_type)
    try:
        if market_type == "CRYPTO":
            url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={timeframe}&limit=100"
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.get(url)
                    if response.status_code != 200:
                        return None, None, "⚠️ **خطا در دریافت داده‌ها از بایننس!**", None
                    res = response.json()
            except Exception as net_err:
                return None, None, "📡 **خطای اتصال به اینترنت!**", None

            if not res or isinstance(res, dict):
                return None, None, f"⚠️ **داده‌ای برای نماد `{symbol}` یافت نشد!**", None

            df = pd.DataFrame(res, columns=['time', 'open', 'high', 'low', 'close', 'volume', '_', '_', '_', '_', '_', '_'])
            df['close'] = df['close'].astype(float)

        elif market_type == "FOREX":
            rates, msg = get_rates_data(symbol, timeframe)
            if rates is None:
                return None, None, msg, None
            df = pd.DataFrame(rates)
        else:
            return None, None, f"❌ **نوع بازار نامعتبر است:** `{market_type}`", None

        if len(df) < 30:
            return None, None, "⚠️ **تعداد کندل‌ها برای تشخیص واگرایی کافی نیست.**", None

        # محاسبه RSI
        df['rsi'] = ta.rsi(df['close'], length=14)
        latest_rsi = df['rsi'].iloc[-1]

        if pd.isna(latest_rsi):
            return None, None, "⚠️ **خطا در محاسبه RSI!**", None

        latest_rsi = round(latest_rsi, 2)

        # تعیین وضعیت اشباع
        if latest_rsi >= 70:
            status = "🔴 OVERBOUGHT (اشباع خرید)"
        elif latest_rsi <= 30:
            status = "🟢 OVERSOLD (اشباع فروش)"
        else:
            status = "⚪️ NORMAL (محدوده خنثی)"

        # تشخیص واگرایی
        div_status, div_type, p1_idx, p2_idx, df_recent = detect_divergence(df)
        chart_path = None

        # اگر واگرایی وجود داشت، عکس چارت را ایجاد کن
        if div_type in ["BULLISH", "BEARISH"]:
            loop = asyncio.get_running_loop()
            chart_path = await loop.run_in_executor(
                None, generate_divergence_chart, df_recent, symbol, timeframe, p1_idx, p2_idx, div_type
            )

        return latest_rsi, status, div_status, chart_path

    except Exception as e:
        logger.exception("Unexpected error calculating RSI for %s: %s", symbol, e)
        return None, None, f"🚨 **خطای غیرمنتظره:** `{e}`", None


def generate_divergence_chart(df, symbol, timeframe, p1_idx, p2_idx, div_type):
    """
    رسم چارت کندل‌استیک به همراه RSI و خطوط واگرایی قیمت و اندیکاتور
    """
    # ساخت یک کپی از ۳۰ کندل اخیر
    df_plot = df.tail(35).copy().reset_index(drop=True)

    # تنظیم ابعاد چارت
    fig, (ax_price, ax_rsi) = plt.subplots(2, 1, figsize=(10, 7), sharex=True, gridspec_kw={'height_ratios': [2, 1]})
    fig.patch.set_facecolor('#1e1e1e')

    # رنگ‌های چارت (تم تاریک)
    for ax in [ax_price, ax_rsi]:
        ax.set_facecolor('#1e1e1e')
        ax.tick_params(colors='white')
        ax.xaxis.label.set_color('white')
        ax.yaxis.label.set_color('white')
        ax.grid(True, color='#333333', linestyle='--', alpha=0.5)

    x_axis = range(len(df_plot))

    # ۱. رسم قیمت (Line / Close)
    ax_price.plot(x_axis, df_plot['close'], color='#00bcff', linewidth=1.5, label='Price (Close)')
    ax_price.set_title(f"{symbol} - {timeframe} | Divergence Analysis", color='white', fontsize=12, pad=10)
    ax_price.set_ylabel("Price", color='white')

    # ۲. رسم RSI
    ax_rsi.plot(x_axis, df_plot['rsi'], color='#ff9900', linewidth=1.5, label='RSI (14)')
    ax_rsi.axhline(70, color='#ff4d4d', linestyle='--', alpha=0.7)
    ax_rsi.axhline(30, color='#2ecc71', linestyle='--', alpha=0.7)
    ax_rsi.fill_between(x_axis, 70, 30, color='#ffffff', alpha=0.03)
    ax_rsi.set_ylabel("RSI", color='white')
    ax_rsi.set_ylim(0, 100)

    # ۳. رسم خطوط واگرایی روی چارت و RSI
    line_color = '#2ecc71' if div_type == "BULLISH" else '#ff4d4d'

    # اندیس‌های متناظر در دیتافریم برش‌خورده
    idx1 = df_plot.index[df_plot['index_orig'] == p1_idx][0]
    idx2 = df_plot.index[df_plot['index_orig'] == p2_idx][0]

    # خط واگرایی روی قیمت
    ax_price.plot([idx1, idx2], [df_plot.loc[idx1, 'close'], df_plot.loc[idx2, 'close']],
                  color=line_color, linewidth=2.5, marker='o', linestyle='-')

    # خط واگرایی روی RSI
    ax_rsi.plot([idx1, idx2], [df_plot.loc[idx1, 'rsi'], df_plot.loc[idx2, 'rsi']],
                color=line_color, linewidth=2.5, marker='o', linestyle='-')

    plt.tight_layout()

    # ذخیره فایل تصویر
    chart_dir = "charts"
    os.makedirs(chart_dir, exist_ok=True)
    file_path = os.path.join(chart_dir, f"div_{symbol}_{timeframe}.png")
    plt.savefig(file_path, facecolor=fig.get_facecolor(), edgecolor='none', dpi=150)
    plt.close(fig)

    return file_path


def get_lower_tf_candles(symbol: str, lower_timeframe: str = "1m", count: int = 8, market_type: str = "FOREX") -> str:
    """دریافت کندل‌های تایم‌فریم پایین‌تر جهت بررسی الگوها"""
    logger.info("Fetching lower TF candles | Symbol: %s | TF: %s | Market: %s", symbol, lower_timeframe, market_type)
    df = None

    try:
        if market_type == "FOREX":
            rates, msg = get_rates_data(symbol, lower_timeframe)
            if rates is None or isinstance(rates, bool) or len(rates) == 0:
                logger.warning("Failed to get FOREX rates for %s: %s", symbol, msg)
                return "اطلاعات کندل‌های تایم پایین در دسترس نیست."

            # برش دادن به تعداد مورد نیاز (count)
            rates = rates[-count:]
            df = pd.DataFrame(rates)
            df['time'] = pd.to_datetime(df['time'], unit='s').dt.strftime('%H:%M')

        elif market_type == "CRYPTO":
            url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={lower_timeframe}&limit={count}"
            try:
                with httpx.Client(timeout=5.0) as client:
                    response = client.get(url)
                    if response.status_code != 200:
                        logger.warning("Binance API error for %s (%s): Status %s", symbol, lower_timeframe,
                                       response.status_code)
                        return "خطا در دریافت کندل‌های تایم پایین از بایننس."
                    res = response.json()

                if not res or isinstance(res, dict):
                    logger.warning("Empty response from Binance for %s", symbol)
                    return "داده‌ای برای کندل‌های تایم پایین بایننس یافت نشد."

                df = pd.DataFrame(res,
                                  columns=['time', 'open', 'high', 'low', 'close', 'volume', '_', '_', '_', '_', '_',
                                           '_'])
                df['time'] = pd.to_datetime(df['time'], unit='ms').dt.strftime('%H:%M')
                for col in ['open', 'high', 'low', 'close']:
                    df[col] = df[col].astype(float)

            except Exception as net_err:
                logger.error("Network error fetching lower TF candles for %s: %s", symbol, net_err)
                return "خطای ارتباط با شبکه هنگام دریافت کندل‌های بایننس."
        else:
            logger.error("Invalid market type: %s", market_type)
            return "نوع بازار برای دریافت کندل‌ها نامعتبر است."

        # ساخت متن خروجی برای پرامپت Gemini
        candles_text = []
        for idx, row in df.iterrows():
            candles_text.append(
                f"کندل {idx + 1} ({row['time']}): Open={row['open']}, High={row['high']}, Low={row['low']}, Close={row['close']}"
            )

        formatted_result = "\n".join(candles_text)
        logger.info("Successfully fetched %d lower TF candles for %s", len(df), symbol)
        return formatted_result

    except Exception as e:
        logger.exception("Error in get_lower_tf_candles for %s: %s", symbol, e)
        return "خطا در پردازش کندل‌های تایم پایین."


async def get_ai_market_view(symbol, rsi_val, rsi_status, divergence, market_type="FOREX"):
    try:
        lower_tf = "1m"
        loop = asyncio.get_running_loop()

        # اصلاح اصلاحیه مهم: ارسال خود تابع get_lower_tf_candles به عنوان ورودی اول
        lower_candles = await loop.run_in_executor(
            None, get_lower_tf_candles, symbol, lower_tf, 8, market_type
        )

        api_key = config('GEMINI_API_KEY', default='')
        if not api_key:
            return "\u200f⚠️ کلید API هوش مصنوعی تنظیم نشده است."

        client = genai.Client(api_key=api_key)
        prompt = f"""
تو یک تحلیل‌گر و مدیریت‌کننده ریسک ارشد در بازارهای مالی (Day Trading و Scalping) هستی.
اطلاعات لحظه‌ای زیر را بر اساس اصول پرایس‌اکشن و الگوهای کندلی تایم پایین تحلیل کن:

📌 اطلاعات دریافتی:
- نماد: {symbol}
- مقدار RSI: {rsi_val}
- وضعیت RSI: {rsi_status}
- وضعیت واگرایی: {divergence}
- ۱۰ کندل اخیر (Open, High, Low, Close):
{lower_candles}

🎯 وظیفه:
یک توصیه کاملاً جدی، فشرده و عملیاتی (دقیقاً در ۲ جمله) به زبان فارسی ارائه بده:
جمله ۱: وضعیت دقیق بازار، اعتبارسنجی سیگنال RSI/واگرایی و برچسب‌گذاری الگوهای کندلی بازگشتی دیده شده (مثل پین‌بار، انگالفینگ، دوجی یا عدم الگو).
جمله ۲: اقدام معامله‌گری صریح (شرط تایید ورود با شکست سطح، حد ضرر، یا هشدار صریح عدم ورود).

دستورالعمل نگارش:
- فقط متن فارسی بدون هیچ عنوان یا مقدمه‌چینی بنویس.
- جملات را کاملاً مرتب و روان بگو تا در فرمت راست‌چین تلگرام به شکل کاملاً تمیز دیده شوند.
"""

        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.2,
            )
        )
        raw_text = response.text.strip()
        formatted_text = "\u200f" + raw_text.replace("\n", "\n\u200f")
        return formatted_text
    except Exception as e:
        logger.error("AI Analysis generation error: %s", e)
        return "\u200f⚠️ تحلیل هوش مصنوعی در دسترس نیست."


def extract_trade_from_image(image_path):
    try:
        client = genai.Client(api_key=config('GEMINI_API_KEY', default=''))
        from PIL import Image
        img = Image.open(image_path)

        prompt = "این تصویر یک چارت یا پوزیشن معاملاتی است. مقادیر زیر را استخراج کن و دقیقاً با همین فرمت پاسخ بده:\nSYMBOL: <نام نماد>\nTYPE: <BUY یا SELL>\nENTRY: <قیمت ورود>\nSL: <حد ضرر یا 0>\nTP: <حد سود یا 0>"

        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[img, prompt]
        )
        return response.text
    except Exception as e:
        return f"خطا در پردازش تصویر: {e}"