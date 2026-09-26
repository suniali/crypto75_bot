import io
import os
import logging
import asyncio
import httpx
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
from PIL import Image

from bot_app.services.mt5_service import get_rates_data
from bot_app.utils.math_helpers import calculate_symbol_breakdown, calculate_trading_metrics

API_KEY = config('GEMINI_API_KEY', default='')

# ------------------------------------------------------------------
# Logging Configuration
# ------------------------------------------------------------------
logger = logging.getLogger("analysis_service")

if not logger.handlers:
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-7s | %(funcName)s:%(lineno)d - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    file_handler = logging.FileHandler("analysis_service.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)


# ------------------------------------------------------------------
# Indicator & Technical Analysis Functions
# ------------------------------------------------------------------

def find_pivots(series: pd.Series, order: int = 3):
    """پیدا کردن نقاط چرخش (قله‌ها و دره‌ها)"""
    pivots_high = argrelextrema(series.values, np.greater, order=order)[0]
    pivots_low = argrelextrema(series.values, np.less, order=order)[0]
    return pivots_high, pivots_low


def detect_divergence(df: pd.DataFrame, lookback: int = 30):
    """بررسی وجود واگرایی مثبت یا منفی در کندل‌های اخیر"""
    df_recent = df.tail(lookback).copy()
    df_recent['index_orig'] = df_recent.index
    df_recent = df_recent.reset_index(drop=True)

    price = df_recent['close']
    rsi = df_recent['rsi']

    p_highs, p_lows = find_pivots(price, order=2)

    div_status = "بدون واگرایی"
    div_type = None
    p1_idx, p2_idx = None, None

    # ۱. واگرایی منفی (Bearish Divergence)
    if len(p_highs) >= 2:
        last_high = p_highs[-1]
        prev_high = p_highs[-2]

        if price.iloc[last_high] > price.iloc[prev_high] and rsi.iloc[last_high] < rsi.iloc[prev_high]:
            div_status = "⚠️ **واگرایی منفی (Bearish Divergence)** ➔ احتمال ریزش"
            div_type = "BEARISH"
            p1_idx = df_recent.loc[prev_high, 'index_orig']
            p2_idx = df_recent.loc[last_high, 'index_orig']
            logger.info("Bearish Divergence detected between index %s and %s", p1_idx, p2_idx)

    # ۲. واگرایی مثبت (Bullish Divergence)
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


async def calculate_rsi(symbol: str, timeframe: str, market_type: str = "CRYPTO"):
    """محاسبه غیرهمزمان RSI و بررسی واگرایی‌ها"""
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
                logger.error("Binance network error for %s: %s", symbol, net_err)
                return None, None, "📡 **خطای اتصال به اینترنت!**", None

            if not res or isinstance(res, dict):
                return None, None, f"⚠️ **داده‌ای برای نماد `{symbol}` یافت نشد!**", None

            df = pd.DataFrame(res,
                              columns=['time', 'open', 'high', 'low', 'close', 'volume', '_', '_', '_', '_', '_', '_'])
            df['close'] = df['close'].astype(float)

        elif market_type == "FOREX":
            rates, msg = await asyncio.to_thread(get_rates_data, symbol, timeframe)
            if rates is None:
                return None, None, msg, None
            df = pd.DataFrame(rates)
        else:
            return None, None, f"❌ **نوع بازار نامعتبر است:** `{market_type}`", None

        if len(df) < 30:
            return None, None, "⚠️ **تعداد کندل‌ها برای تشخیص واگرایی کافی نیست.**", None

        df['rsi'] = ta.rsi(df['close'], length=14)
        latest_rsi = df['rsi'].iloc[-1]

        if pd.isna(latest_rsi):
            return None, None, "⚠️ **خطا در محاسبه RSI!**", None

        latest_rsi = round(latest_rsi, 2)

        if latest_rsi >= 70:
            status = "🔴 OVERBOUGHT (اشباع خرید)"
        elif latest_rsi <= 30:
            status = "🟢 OVERSOLD (اشباع فروش)"
        else:
            status = "⚪️ NORMAL (محدوده خنثی)"

        div_status, div_type, p1_idx, p2_idx, df_recent = detect_divergence(df)
        chart_path = None

        if div_type in ["BULLISH", "BEARISH"]:
            chart_path = await asyncio.to_thread(
                generate_divergence_chart, df_recent, symbol, timeframe, p1_idx, p2_idx, div_type
            )

        return latest_rsi, status, div_status, chart_path

    except Exception as e:
        logger.exception("Unexpected error calculating RSI for %s: %s", symbol, e)
        return None, None, f"🚨 **خطای غیرمنتظره:** `{e}`", None


def generate_divergence_chart(df: pd.DataFrame, symbol: str, timeframe: str, p1_idx, p2_idx, div_type: str):
    """رسم چارت واگرایی همراه با مدیریت کامل حافظه RAM"""
    fig = None
    try:
        df_plot = df.tail(35).copy().reset_index(drop=True)

        fig, (ax_price, ax_rsi) = plt.subplots(
            2, 1,
            figsize=(10, 6),
            sharex=True,
            gridspec_kw={'height_ratios': [2, 1]}
        )

        bg_color = '#1e1e1e'
        fig.set_facecolor(bg_color)

        for ax in (ax_price, ax_rsi):
            ax.set_facecolor(bg_color)
            ax.tick_params(colors='#ffffff', labelsize=9)
            ax.grid(True, color='#333333', linestyle='--', alpha=0.5)
            for spine in ax.spines.values():
                spine.set_color('#444444')

        x_axis = range(len(df_plot))

        ax_price.plot(x_axis, df_plot['close'], color='#00bcff', linewidth=1.8, label='Price')
        ax_price.set_title(f"{symbol} - {timeframe} | Divergence Analysis", color='#ffffff', fontsize=12, pad=10)
        ax_price.set_ylabel("Price", color='#ffffff')

        ax_rsi.plot(x_axis, df_plot['rsi'], color='#ff9900', linewidth=1.8, label='RSI')
        ax_rsi.axhline(70, color='#ff4d4d', linestyle='--', alpha=0.7)
        ax_rsi.axhline(30, color='#2ecc71', linestyle='--', alpha=0.7)
        ax_rsi.fill_between(x_axis, 70, 30, color='#ffffff', alpha=0.04)
        ax_rsi.set_ylabel("RSI", color='#ffffff')
        ax_rsi.set_ylim(0, 100)

        line_color = '#2ecc71' if div_type == "BULLISH" else '#ff4d4d'

        if 'index_orig' in df_plot.columns:
            idx1_matches = df_plot.index[df_plot['index_orig'] == p1_idx].tolist()
            idx2_matches = df_plot.index[df_plot['index_orig'] == p2_idx].tolist()

            if idx1_matches and idx2_matches:
                i1, i2 = idx1_matches[0], idx2_matches[0]

                ax_price.plot([i1, i2], [df_plot.loc[i1, 'close'], df_plot.loc[i2, 'close']],
                              color=line_color, linewidth=2.5, marker='o', markersize=5)
                ax_rsi.plot([i1, i2], [df_plot.loc[i1, 'rsi'], df_plot.loc[i2, 'rsi']],
                            color=line_color, linewidth=2.5, marker='o', markersize=5)

        plt.tight_layout()

        chart_dir = "charts"
        os.makedirs(chart_dir, exist_ok=True)
        file_path = os.path.join(chart_dir, f"div_{symbol}_{timeframe}.png")

        fig.savefig(file_path, facecolor=bg_color, edgecolor='none', dpi=120, bbox_inches='tight')
        return file_path

    except Exception as err:
        logger.error("Error in generate_divergence_chart: %s", err)
        return None
    finally:
        if fig is not None:
            plt.close(fig)


async def get_lower_tf_candles(symbol: str, lower_timeframe: str = "1m", count: int = 8,
                               market_type: str = "FOREX") -> str:
    """دریافت غیرهمزمان کندل‌های تایم‌فریم پایین‌تر"""
    logger.info("Fetching lower TF candles | Symbol: %s | TF: %s | Market: %s", symbol, lower_timeframe, market_type)

    try:
        if market_type == "FOREX":
            rates, msg = await asyncio.to_thread(get_rates_data, symbol, lower_timeframe)
            if rates is None or isinstance(rates, bool) or len(rates) == 0:
                return "اطلاعات کندل‌های تایم پایین در دسترس نیست."

            rates = rates[-count:]
            df = pd.DataFrame(rates)
            df['time'] = pd.to_datetime(df['time'], unit='s').dt.strftime('%H:%M')

        elif market_type == "CRYPTO":
            url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={lower_timeframe}&limit={count}"
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(url)
                if response.status_code != 200:
                    return "خطا در دریافت کندل‌های تایم پایین از بایننس."
                res = response.json()

            if not res or isinstance(res, dict):
                return "داده‌ای برای کندل‌های تایم پایین بایننس یافت نشد."

            df = pd.DataFrame(res,
                              columns=['time', 'open', 'high', 'low', 'close', 'volume', '_', '_', '_', '_', '_', '_'])
            df['time'] = pd.to_datetime(df['time'], unit='ms').dt.strftime('%H:%M')
            for col in ['open', 'high', 'low', 'close']:
                df[col] = df[col].astype(float)
        else:
            return "نوع بازار برای دریافت کندل‌ها نامعتبر است."

        candles_text = [
            f"کندل {idx + 1} ({row['time']}): Open={row['open']}, High={row['high']}, Low={row['low']}, Close={row['close']}"
            for idx, row in df.iterrows()
        ]
        return "\n".join(candles_text)

    except Exception as e:
        logger.exception("Error in get_lower_tf_candles for %s: %s", symbol, e)
        return "خطا در پردازش کندل‌های تایم پایین."


# ------------------------------------------------------------------
# Report & Chart Generation (RAM Protected)
# ------------------------------------------------------------------

def generate_equity_chart(trades: list) -> io.BytesIO | None:
    if not trades:
        return None

    fig = None
    try:
        sorted_trades = sorted(trades, key=lambda x: x['time'])
        cumulative_profit = [0.0]
        current_total = 0.0

        for t in sorted_trades:
            current_total += t['profit']
            cumulative_profit.append(current_total)

        plt.style.use('dark_background')
        fig, ax = plt.subplots(figsize=(7, 3.2), dpi=150)

        line_color = '#00E676' if current_total >= 0 else '#FF5252'
        fill_color = '#00E676' if current_total >= 0 else '#FF5252'

        ax.plot(cumulative_profit, color=line_color, linewidth=2, label="سود/زیان انباشته ($)")
        ax.fill_between(range(len(cumulative_profit)), cumulative_profit, color=fill_color, alpha=0.15)

        ax.set_title("Equity Curve (رشد انباشته حساب)", fontsize=11, color='white', pad=10)
        ax.set_xlabel("تعداد معاملات", fontsize=9, color='#CCCCCC')
        ax.set_ylabel("سود/زیان ($)", fontsize=9, color='#CCCCCC')
        ax.grid(True, linestyle='--', alpha=0.3, color='#555555')
        ax.axhline(0, color='white', linewidth=0.8, linestyle=':')

        plt.tight_layout()

        img_buf = io.BytesIO()
        plt.savefig(img_buf, format='png', bbox_inches='tight', facecolor=fig.get_facecolor())
        img_buf.seek(0)
        return img_buf
    except Exception as e:
        logger.error("Error generating equity chart: %s", e)
        return None
    finally:
        if fig is not None:
            plt.close(fig)


def generate_report_charts(trades: list) -> dict:
    if not trades:
        return {}

    plt.style.use('dark_background')
    charts = {}

    # 📈 نمودار ۱: Equity Curve & Drawdown
    fig1 = None
    try:
        fig1, (ax1, ax2) = plt.subplots(2, 1, figsize=(7, 4), sharex=True, gridspec_kw={'height_ratios': [3, 1]})
        sorted_trades = sorted(trades, key=lambda x: x['time'])
        cum_profits = np.cumsum([t['profit'] for t in sorted_trades])
        peaks = np.maximum.accumulate(cum_profits)
        drawdowns = cum_profits - peaks

        ax1.plot(cum_profits, color='#00E676', linewidth=1.5, label='Equity')
        ax1.set_title("Equity Curve & Drawdown Analysis", fontsize=10, color='white')
        ax1.grid(True, linestyle='--', alpha=0.2)

        ax2.fill_between(range(len(drawdowns)), drawdowns, color='#FF5252', alpha=0.5)
        ax2.set_ylabel("DD ($)", fontsize=7, color='#CCCCCC')
        ax2.grid(True, linestyle='--', alpha=0.2)

        plt.tight_layout()
        buf1 = io.BytesIO()
        plt.savefig(buf1, format='png', dpi=150, bbox_inches='tight')
        buf1.seek(0)
        charts['equity'] = buf1
    except Exception as e:
        logger.error("Error generating chart 1: %s", e)
    finally:
        if fig1 is not None:
            plt.close(fig1)

    # 📊 نمودار ۲: وین‌ریت نمادها
    breakdown = calculate_symbol_breakdown(trades)
    if breakdown:
        fig2 = None
        try:
            fig2, ax = plt.subplots(figsize=(7, 2.5))
            symbols = [item['symbol'] for item in breakdown]
            win_rates = [item['win_rate'] for item in breakdown]

            bars = ax.bar(symbols, win_rates, color='#29B6F6', width=0.4)
            ax.set_title("Win Rate by Symbol (%)", fontsize=10, color='white')
            ax.set_ylim(0, 100)
            ax.grid(axis='y', linestyle='--', alpha=0.2)

            for bar in bars:
                yval = bar.get_height()
                ax.text(bar.get_x() + bar.get_width() / 2, yval + 2, f"{yval}%", ha='center', va='bottom', fontsize=8,
                        color='white')

            plt.tight_layout()
            buf2 = io.BytesIO()
            plt.savefig(buf2, format='png', dpi=150, bbox_inches='tight')
            buf2.seek(0)
            charts['win_rate_symbol'] = buf2
        except Exception as e:
            logger.error("Error generating chart 2: %s", e)
        finally:
            if fig2 is not None:
                plt.close(fig2)

    # ⚖️ نمودار ۳: توزیع سود و زیان PnL
    fig3 = None
    try:
        fig3, ax = plt.subplots(figsize=(7, 2.5))
        profits = [t['profit'] for t in trades]
        colors_list = ['#00E676' if p > 0 else '#FF5252' for p in profits]

        ax.bar(range(len(profits)), profits, color=colors_list, alpha=0.8)
        ax.axhline(0, color='white', linewidth=0.8, linestyle='--')
        ax.set_title("Trade-by-Trade PnL Distribution ($)", fontsize=10, color='white')
        ax.grid(True, linestyle='--', alpha=0.2)

        plt.tight_layout()
        buf3 = io.BytesIO()
        plt.savefig(buf3, format='png', dpi=150, bbox_inches='tight')
        buf3.seek(0)
        charts['pnl_dist'] = buf3
    except Exception as e:
        logger.error("Error generating chart 3: %s", e)
    finally:
        if fig3 is not None:
            plt.close(fig3)

    return charts


# ------------------------------------------------------------------
# Asynchronous AI Functions (Gemini 2.5 SDK)
# ------------------------------------------------------------------

async def get_ai_market_view(symbol: str, rsi_val: float, rsi_status: str, divergence: str,
                             market_type: str = "FOREX") -> str:
    """تحلیل هوشمند مارکت با Gemini به صورت Async بدون بلاک کردن Event Loop"""
    try:
        if not API_KEY:
            return "\u200f⚠️ کلید API هوش مصنوعی تنظیم نشده است."

        lower_candles = await get_lower_tf_candles(symbol, "1m", 8, market_type)

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
        client = genai.Client(api_key=API_KEY)
        models_to_try = ['gemini-2.5-flash', 'gemini-2.5-pro']

        for model_name in models_to_try:
            for attempt in range(2):
                try:
                    response = await client.aio.models.generate_content(
                        model=model_name,
                        contents=prompt,
                        config=types.GenerateContentConfig(temperature=0.2)
                    )

                    if response and response.text:
                        raw_text = response.text.strip()
                        return "\u200f" + raw_text.replace("\n", "\n\u200f")

                except Exception as api_err:
                    err_str = str(api_err)
                    if "404" in err_str or "NOT_FOUND" in err_str:
                        logger.warning("مدل %s یافت نشد. سوئیچ به مدل بعدی...", model_name)
                        break

                    logger.warning("Gemini API Error (%s) attempt %d: %s", model_name, attempt + 1, api_err)
                    await asyncio.sleep(1.0)

        return "\u200f⚠️ سرویس هوش مصنوعی به دلیل ترافیک بالای سرورها موقتاً در دسترس نیست."

    except Exception as e:
        logger.error("AI Analysis generation error: %s", e)
        return "\u200f⚠️ تحلیل هوش مصنوعی در دسترس نیست."


async def extract_trade_from_image(image_path: str) -> str:
    """استخراج مشخصات پوزیشن از روی تصویر چارت به صورت Async"""
    if not API_KEY:
        return "\u200f⚠️ کلید API هوش مصنوعی تنظیم نشده است."

    try:
        img = await asyncio.to_thread(Image.open, image_path)

        prompt = """
این تصویر یک چارت یا پوزیشن معاملاتی است. 
دقیقاً مقادیر زیر را استخراج کن و بدون هیچ مقدمه یا توضیح اضافی، فقط فرمت زیر را خروجی بده:

SYMBOL: <نام نماد>
TYPE: <BUY یا SELL>
ENTRY: <قیمت ورود>
SL: <حد ضرر یا 0>
TP: <حد سود یا 0>
"""

        client = genai.Client(api_key=API_KEY)
        models_to_try = ['gemini-2.5-flash', 'gemini-2.5-pro']

        for model_name in models_to_try:
            try:
                response = await client.aio.models.generate_content(
                    model=model_name,
                    contents=[img, prompt],
                    config=types.GenerateContentConfig(temperature=0.1)
                )
                raw_text = response.text.strip()
                return "\u200f" + raw_text.replace("\n", "\n\u200f")
            except Exception as e:
                logger.warning("Error with model %s on image extraction: %s", model_name, e)
                await asyncio.sleep(1.0)

        return "\u200f⚠️ خطا در پردازش تصویر چارت با هوش مصنوعی."

    except Exception as e:
        logger.error("Error in extract_trade_from_image: %s", e)
        return "\u200f⚠️ پردازش تصویر ناموفق بود."


async def analyze_trades_with_gemini(trades: list, period_name: str) -> str:
    """تحلیل جامع رفتارشناسی معاملات توسط Gemini به صورت Async"""
    if not trades:
        return "هیچ معامله‌ای برای تحلیل در این بازه یافت نشد."

    if not API_KEY:
        return "کلید API هوش مصنوعی تنظیم نشده است."

    metrics = calculate_trading_metrics(trades)
    total_trades = len(trades)
    win_trades = sum(1 for t in trades if t['profit'] > 0)
    total_profit = sum(t['profit'] for t in trades)
    win_rate = (win_trades / total_trades) * 100 if total_trades > 0 else 0

    symbols_summary = calculate_symbol_breakdown(trades)
    best_sym = symbols_summary[0]['symbol'] if symbols_summary else "N/A"
    worst_sym = symbols_summary[-1]['symbol'] if symbols_summary else "N/A"

    prompt = f"""
        تو یک تحلیل‌گر و مربی ارشد ترید هستی. گزارش عملکرد معامله‌گر را در بازه ({period_name}) بررسی کن.

        📊 **آمارهای کلیدی:**
        - تعداد کل معاملات: {total_trades}
        - وین‌ریت: {win_rate:.1f}%
        - سود/زیان خالص: ${total_profit:.2f}
        - ضریب سودآوری (Profit Factor): {metrics.get('profit_factor', 0.0):.2f}
        - بیشترین افت حساب (Max Drawdown): ${metrics.get('max_drawdown', 0.0):.2f}
        - نسبت میانگین سود به زیان (Payoff Ratio): {metrics.get('payoff_ratio', 0.0):.2f}
        - میانگین سود: ${metrics.get('avg_win', 0.0):.2f} | میانگین زیان: ${metrics.get('avg_loss', 0.0):.2f}

        📌 **آمار نمادها:**
        - سودده‌ترین نماد: {best_sym}
        - پرریسک‌ترین/زیان‌ده‌ترین نماد: {worst_sym}

        لطفاً تحلیل روان‌شناختی و فنی خود را در ۳ بخش زیر ارائه بده:
        ۱. **ارزیابی پایداری استراتژی** (با توجه به Profit Factor و Max Drawdown)
        ۲. **مدیریت ریسک و R:R** (با توجه به Payoff Ratio، وین‌ریت و میانگین سود/زیان)
        ۳. **تحلیل نمادها و توصیه کلیدی برای بازه بعدی** (تمرکز روی {best_sym} و کنترل زیان در {worst_sym})

        پاسخ را فشرده، کاربردی، بدون مقدمه‌چینی اضافی و کاملاً به زبان فارسی بنویس.
        """

    try:
        client = genai.Client(api_key=API_KEY)
        response = await client.aio.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config=types.GenerateContentConfig(temperature=0.3)
        )
        return response.text.strip()
    except Exception as e:
        logger.error("Error analyzing trades with Gemini: %s", e)
        return f"خطا در دریافت تحلیل هوش مصنوعی: {str(e)}"