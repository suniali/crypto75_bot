import io
import os
import time
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

API_KEY = config('GEMINI_API_KEY', default='')

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
    رسم چارت مطمئن بدون صفحه مشکی/سفید با مدیریت دقیق اندیس‌ها
    """
    fig = None
    try:
        # ۱. آماده‌سازی دیتافریم ۳۵ کندل اخیر
        df_plot = df.tail(35).copy().reset_index(drop=True)

        # ۲. ساخت بوم و محورها با تنظیم مستقیم رنگ‌ها (بدون style.use)
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

        # ۳. رسم قیمت (خط اصلی)
        ax_price.plot(x_axis, df_plot['close'], color='#00bcff', linewidth=1.8, label='Price')
        ax_price.set_title(f"{symbol} - {timeframe} | Divergence Analysis", color='#ffffff', fontsize=12, pad=10)
        ax_price.set_ylabel("Price", color='#ffffff')

        # ۴. رسم RSI
        ax_rsi.plot(x_axis, df_plot['rsi'], color='#ff9900', linewidth=1.8, label='RSI')
        ax_rsi.axhline(70, color='#ff4d4d', linestyle='--', alpha=0.7)
        ax_rsi.axhline(30, color='#2ecc71', linestyle='--', alpha=0.7)
        ax_rsi.fill_between(x_axis, 70, 30, color='#ffffff', alpha=0.04)
        ax_rsi.set_ylabel("RSI", color='#ffffff')
        ax_rsi.set_ylim(0, 100)

        # ۵. رسم خطوط واگرایی با محاسبه ایمن اندیس‌ها
        line_color = '#2ecc71' if div_type == "BULLISH" else '#ff4d4d'

        # یافتن اندیس‌ها در ۳۵ کندل اخیر
        if 'index_orig' in df_plot.columns:
            idx1_matches = df_plot.index[df_plot['index_orig'] == p1_idx].tolist()
            idx2_matches = df_plot.index[df_plot['index_orig'] == p2_idx].tolist()

            if idx1_matches and idx2_matches:
                i1, i2 = idx1_matches[0], idx2_matches[0]

                # رسم خط قیمت
                ax_price.plot([i1, i2], [df_plot.loc[i1, 'close'], df_plot.loc[i2, 'close']],
                              color=line_color, linewidth=2.5, marker='o', markersize=5)

                # رسم خط RSI
                ax_rsi.plot([i1, i2], [df_plot.loc[i1, 'rsi'], df_plot.loc[i2, 'rsi']],
                            color=line_color, linewidth=2.5, marker='o', markersize=5)

        plt.tight_layout()

        # ۶. ذخیره‌سازی مطمئن
        chart_dir = "charts"
        os.makedirs(chart_dir, exist_ok=True)
        file_path = os.path.join(chart_dir, f"div_{symbol}_{timeframe}.png")

        fig.savefig(
            file_path,
            facecolor=bg_color,
            edgecolor='none',
            dpi=120,
            bbox_inches='tight'
        )
        return file_path

    except Exception as err:
        logger.error("Error in generate_divergence_chart: %s", err)
        return None
    finally:
        if fig is not None:
            plt.close(fig)
        plt.close('all')

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

def generate_equity_chart(trades: list) -> io.BytesIO:
    """رسم نمودار Equity Curve و خروجی در قالب BytesIO بدون ذخیره روی دیسک"""
    if not trades:
        return None

    # ۱. مرتب‌سازی بر اساس زمان و محاسبه سود انباشته
    sorted_trades = sorted(trades, key=lambda x: x['time'])
    cumulative_profit = []
    current_total = 0.0

    # نقطه شروع صفر
    cumulative_profit.append(0.0)

    for t in sorted_trades:
        current_total += t['profit']
        cumulative_profit.append(current_total)

    # ۲. ساخت نمودار با Matplotlib (تِم دارک و حرفه‌ای)
    plt.style.use('dark_background')
    fig, ax = plt.subplots(figsize=(7, 3.2), dpi=150)

    # تعیین رنگ نمودار بر اساس سودده یا زیان‌ده بودن کل
    line_color = '#00E676' if current_total >= 0 else '#FF5252'
    fill_color = '#00E676' if current_total >= 0 else '#FF5252'

    ax.plot(cumulative_profit, color=line_color, linewidth=2, label="سود/زیان انباشته ($)")
    ax.fill_between(range(len(cumulative_profit)), cumulative_profit, color=fill_color, alpha=0.15)

    # تنظیمات استایل و عناوین
    ax.set_title("Equity Curve (رشد انباشته حساب)", fontsize=11, color='white', pad=10)
    ax.set_xlabel("تعداد معاملات", fontsize=9, color='#CCCCCC')
    ax.set_ylabel("سود/زیان ($)", fontsize=9, color='#CCCCCC')
    ax.grid(True, linestyle='--', alpha=0.3, color='#555555')
    ax.axhline(0, color='white', linewidth=0.8, linestyle=':')

    plt.tight_layout()

    # ۳. ذخیره نمودار در حافظه موقت RAM
    img_buf = io.BytesIO()
    plt.savefig(img_buf, format='png', bbox_inches='tight', facecolor=fig.get_facecolor())
    img_buf.seek(0)
    plt.close(fig) # بستن شکل برای آزادسازی حافظه

    return img_buf


import numpy as np


def calculate_trading_metrics(trades: list) -> dict:
    if not trades:
        return {}

    profits = [t['profit'] for t in trades]
    wins = [p for p in profits if p > 0]
    losses = [abs(p) for p in profits if p < 0]

    total_profit_gross = sum(wins)
    total_loss_gross = sum(losses)

    # 1. Profit Factor & Payoff
    profit_factor = (total_profit_gross / total_loss_gross) if total_loss_gross > 0 else total_profit_gross
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    payoff_ratio = (avg_win / avg_loss) if avg_loss > 0 else avg_win

    # 2. Sharpe Ratio & Sortino Ratio (تنظیم سالانه صوری برای دیتای معاملاتی)
    returns = np.array(profits)
    std_dev = np.std(returns) if len(returns) > 1 else 0.0
    sharpe_ratio = (np.mean(returns) / std_dev * np.sqrt(252)) if std_dev > 0 else 0.0

    downside_returns = returns[returns < 0]
    downside_std = np.std(downside_returns) if len(downside_returns) > 1 else 0.0
    sortino_ratio = (np.mean(returns) / downside_std * np.sqrt(252)) if downside_std > 0 else 0.0

    # 3. Max Drawdown & Max Drawdown %
    sorted_trades = sorted(trades, key=lambda x: x['time'])
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0

    for t in sorted_trades:
        cumulative += t['profit']
        if cumulative > peak:
            peak = cumulative
        dd = peak - cumulative
        if dd > max_dd:
            max_dd = dd

    return {
        "profit_factor": round(profit_factor, 2),
        "max_drawdown": round(max_dd, 2),
        "payoff_ratio": round(payoff_ratio, 2),
        "sharpe_ratio": round(sharpe_ratio, 2),
        "sortino_ratio": round(sortino_ratio, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "expectancy": round((sum(profits) / len(trades)), 2)  # امید ریاضی هر معامله
    }

def calculate_symbol_breakdown(trades: list) -> list:
    """محاسبه آمار تفکیکی معاملات بر اساس جفت‌ارزها"""
    if not trades:
        return []

    breakdown = {}
    for t in trades:
        sym = t['symbol']
        profit = t['profit']

        if sym not in breakdown:
            breakdown[sym] = {
                "count": 0,
                "wins": 0,
                "total_profit": 0.0,
            }

        breakdown[sym]["count"] += 1
        breakdown[sym]["total_profit"] += profit
        if profit > 0:
            breakdown[sym]["wins"] += 1

    # تبدیل به لیست و محاسبه وین‌ریت و مرتب‌سازی بر اساس مجموع سود
    result = []
    for sym, data in breakdown.items():
        win_rate = (data["wins"] / data["count"]) * 100 if data["count"] > 0 else 0.0
        result.append({
            "symbol": sym,
            "count": data["count"],
            "win_rate": round(win_rate, 1),
            "profit": round(data["total_profit"], 2)
        })

    # مرتب‌سازی بر اساس بیشترین سود/زیان
    return sorted(result, key=lambda x: x['profit'], reverse=True)

# ------------------------------------------------------------------
# Reporting Functions
# ------------------------------------------------------------------
def generate_report_charts(trades: list) -> dict:
    if not trades:
        return {}

    plt.style.use('dark_background')
    charts = {}

    # 📈 نمودار ۱: Equity Curve & Drawdown
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(7, 4), sharex=True, gridspec_kw={'height_ratios': [3, 1]})
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
    plt.close()
    charts['equity'] = buf1

    # 📊 نمودار ۲: وین‌ریت و تعداد معاملات به تفکیک نماد
    breakdown = calculate_symbol_breakdown(trades)
    if breakdown:
        fig, ax = plt.subplots(figsize=(7, 2.5))
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
        plt.close()
        charts['win_rate_symbol'] = buf2

    # ⚖️ نمودار ۳: توزیع سود و زیان (PnL Distribution)
    fig, ax = plt.subplots(figsize=(7, 2.5))
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
    plt.close()
    charts['pnl_dist'] = buf3

    return charts

# ------------------------------------------------------------------
# AI Functions
# ------------------------------------------------------------------

async def get_ai_market_view(symbol, rsi_val, rsi_status, divergence, market_type="FOREX"):
    try:
        lower_tf = "1m"
        loop = asyncio.get_running_loop()

        # اصلاح اصلاحیه مهم: ارسال خود تابع get_lower_tf_candles به عنوان ورودی اول
        lower_candles = await loop.run_in_executor(
            None, get_lower_tf_candles, symbol, lower_tf, 8, market_type
        )

        if not API_KEY:
            return "\u200f⚠️ کلید API هوش مصنوعی تنظیم نشده است."

        client = genai.Client(api_key=API_KEY)
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
        models_to_try = ['gemini-3.6-flash','gemini-2.5-flash']
        max_retries_per_model = 2

        for model_name in models_to_try:
            for attempt in range(max_retries_per_model):
                try:
                    def call_api(m_name=model_name):
                        return client.models.generate_content(
                            model=m_name,
                            contents=prompt,
                            config=types.GenerateContentConfig(
                                temperature=0.2,
                            )
                        )

                    response = await loop.run_in_executor(None, call_api)

                    if response and response.text:
                        raw_text = response.text.strip()
                        formatted_text = "\u200f" + raw_text.replace("\n", "\n\u200f")
                        return formatted_text

                except Exception as api_err:
                    err_str = str(api_err)

                    # اگر مدل پیدا نشد (404)، بلافاصله به مدل بعدی سوئیچ کن
                    if "404" in err_str or "NOT_FOUND" in err_str:
                        logger.warning("مدل %s یافت نشد (404). سوئیچ به مدل بعدی...", model_name)
                        break

                    is_unavailable = any(
                        err in err_str for err in ["503", "UNAVAILABLE", "Overloaded", "ResourceExhausted"])
                    if is_unavailable:
                        logger.warning(
                            "Gemini %s 503/Overload (تلاش %d از %d روی این مدل). وقفه...",
                            model_name, attempt + 1, max_retries_per_model
                        )
                        await asyncio.sleep(1.5 * (attempt + 1))
                    else:
                        logger.error("خطای غیرمنتظره API: %s", api_err)
                        break

        return "\u200f⚠️ سرویس هوش مصنوعی به دلیل ترافیک بالای سرورها موقتاً در دسترس نیست."
    except Exception as e:
        logger.error("AI Analysis generation error: %s", e)
        return "\u200f⚠️ تحلیل هوش مصنوعی در دسترس نیست."


def extract_trade_from_image(image_path: str) -> str:
    if not API_KEY:
        return "‏⚠️ کلید API هوش مصنوعی تنظیم نشده است."

    client = genai.Client(api_key=API_KEY)
    from PIL import Image
    img = Image.open(image_path)

    prompt = """
این تصویر یک چارت یا پوزیشن معاملاتی است. 
دقیقاً مقادیر زیر را استخراج کن و بدون هیچ مقدمه یا توضیح اضافی، فقط فرمت زیر را خروجی بده:

SYMBOL: <نام نماد>
TYPE: <BUY یا SELL>
ENTRY: <قیمت ورود>
SL: <حد ضرر یا 0>
TP: <حد سود یا 0>
"""

    # لیست مدل‌ها جهت Fallback در صورت ترافیک بالا
    models_to_try = ['gemini-3.6-flash','gemini-2.5-flash']

    for model_name in models_to_try:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=[img, prompt],
                config=types.GenerateContentConfig(temperature=0.1)
            )
            raw_text = response.text.strip()
            return "\u200f" + raw_text.replace("\n", "\n\u200f")
        except Exception as e:
            if "503" in str(e):
                logger.warning(f"Model {model_name} busy (503). Retrying with backup model...")
                time.sleep(1)  # وقفه کوتاه قبل از تلاش مجدد
                continue
            else:
                logger.error("Error extracting trade from image: %s", e)
                return "‏⚠️ خطا در پردازش تصویر چارت."

    return "‏⚠️ سرورهای هوش مصنوعی در حال حاضر شلوغ هستند. لطفاً چند لحظه بعد مجدداً تلاش کنید."


def analyze_trades_with_gemini(trades: list, period_name: str) -> str:

    if not trades:
        return "هیچ معامله‌ای برای تحلیل در این بازه یافت نشد."

    metrics = calculate_trading_metrics(trades)
    total_trades = len(trades)
    win_trades = sum(1 for t in trades if t['profit'] > 0)
    total_profit = sum(t['profit'] for t in trades)
    win_rate = (win_trades / total_trades) * 100 if total_trades > 0 else 0

    # استخراج سودده‌ترین و زیان‌ده‌ترین نماد
    symbols_summary = calculate_symbol_breakdown(trades)
    best_sym = symbols_summary[0]['symbol'] if symbols_summary else "N/A"
    worst_sym = symbols_summary[-1]['symbol'] if symbols_summary else "N/A"

    prompt = f"""
    تو یک تحلیل‌گر و مربی ارشد ترید هستی. گزارش عملکرد معامله‌گر را در بازه ({period_name}) بررسی کن.

    📊 **آمارهای کلیدی:**
    - تعداد کل معاملات: {total_trades}
    - وین‌ریت: {win_rate:.1f}%
    - سود/زیان خالص: ${total_profit:.2f}
    - ضریب سودآوری (Profit Factor): {metrics['profit_factor']}
    - بیشترین افت حساب (Max Drawdown): ${metrics['max_drawdown']}
    - نسبت میانگین سود به زیان (Payoff Ratio): {metrics['payoff_ratio']}
    - میانگین سود: ${metrics['avg_win']} | میانگین زیان: ${metrics['avg_loss']}

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
        # 📌 اصلاح اصلی: ساخت کلاینت بر اساس نسخه جدید SDK
        client = genai.Client(api_key=API_KEY)
        response = client.models.generate_content(
            model='gemini-3.6-flash',
            contents=prompt,
        )
        return response.text.strip()
    except Exception as e:
        return f"خطا در دریافت تحلیل: {str(e)}"