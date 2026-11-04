import io
import os
import json
import math
import logging
import asyncio
import httpx

from decimal import Decimal,ROUND_CEILING,ROUND_HALF_UP
from datetime import timedelta
from django.utils import timezone
from django.db.models import DecimalField, ExpressionWrapper, F, Sum
from django.db.models.functions import Abs, Coalesce
from collections import defaultdict
from dateutil.relativedelta import relativedelta 

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
from bot_app.utils.math_helpers import calculate_symbol_breakdown
from bot_app.utils.month_calender_data import get_monthly_calendar_data,calculate_streaks
from bot_app.utils.ai_modules import AI_MODULES

from bot_app.models import TradeJournal, TelegramUser

API_KEY = config('GEMINI_API_KEY', default='')
MIN_RESERVE_HEARTS = 4   # حداقل قلب ذخیره در شروع مرحله
CURENCY_UNIT=config("CURENCY_UNIT", default="$")

logger = logging.getLogger(__name__)

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

        ax.plot(cumulative_profit, color=line_color, linewidth=2, label=f"سود/زیان انباشته ({CURENCY_UNIT})")
        ax.fill_between(range(len(cumulative_profit)), cumulative_profit, color=fill_color, alpha=0.15)

        ax.set_title("Equity Curve (رشد انباشته حساب)", fontsize=11, color='white', pad=10)
        ax.set_xlabel("تعداد معاملات", fontsize=9, color='#CCCCCC')
        ax.set_ylabel(f"سود/زیان ({CURENCY_UNIT})", fontsize=9, color='#CCCCCC')
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

    # تابع کمکی برای استخراج امن زمان و سود از هر نوع دیتایی (چه Dict چه Object)
    def get_trade_time(t):
        if isinstance(t, dict):
            return t.get('time') or t.get('entry_time') or ''
        else:
            return getattr(t, 'entry_time', None) or getattr(t, 'created_at', '')

    def get_trade_profit(t):
        if isinstance(t, dict):
            val = t.get('profit') if t.get('profit') is not None else t.get('net_profit', 0.0)
            return float(val or 0.0)
        else:
            val = getattr(t, 'net_profit', None)
            return float(val if val is not None else 0.0)

    # مرتب‌سازی ایمن معاملات بر اساس زمان
    try:
        sorted_trades = sorted(trades, key=lambda x: str(get_trade_time(x)))
    except Exception:
        sorted_trades = trades

    profits = [get_trade_profit(t) for t in sorted_trades]

    # 📈 نمودار ۱: Equity Curve & Drawdown
    fig1 = None
    try:
        fig1, (ax1, ax2) = plt.subplots(2, 1, figsize=(7, 4), sharex=True, gridspec_kw={'height_ratios': [3, 1]})
        cum_profits = np.cumsum(profits) if profits else np.array([0.0])
        peaks = np.maximum.accumulate(cum_profits)
        drawdowns = cum_profits - peaks

        ax1.plot(cum_profits, color='#00E676', linewidth=1.5, label='Equity')
        ax1.set_title("Equity Curve & Drawdown Analysis", fontsize=10, color='white')
        ax1.grid(True, linestyle='--', alpha=0.2)

        ax2.fill_between(range(len(drawdowns)), drawdowns, color='#FF5252', alpha=0.5)
        ax2.set_ylabel(f"DD ({CURENCY_UNIT})", fontsize=7, color='#CCCCCC')
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
                ax.text(bar.get_x() + bar.get_width() / 2, yval + 2, f"{yval}%", ha='center', va='bottom', fontsize=8, color='white')

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
        colors_list = ['#00E676' if p > 0 else '#FF5252' for p in profits]

        ax.bar(range(len(profits)), profits, color=colors_list, alpha=0.8)
        ax.axhline(0, color='white', linewidth=0.8, linestyle='--')
        ax.set_title(f"Trade-by-Trade PnL Distribution ({CURENCY_UNIT})", fontsize=10, color='white')
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
# Calculate Tradeing Functions
# ------------------------------------------------------------------
# ---------- تنظیمات ----------
MIN_HEARTS_STRONG = 4        # قلب لازم برای تریدر قوی
MAX_HEARTS_WEAK = 10         # قلب لازم برای تریدر ضعیف
START_REQUIRED_HEARTS = 7    # نقطه‌ی شروع (خنثی)
MIN_TRADES_FOR_SCORE = 20    # زیر این تعداد، امتیاز خنثی
SCORE_WINDOW = 50            # امتیاز فقط روی N معامله‌ی آخر
EVAL_EVERY = 10              # هر چند معامله یک بار بازبینی شود
GROWTH_CAP = Decimal('1.5')  # حداکثر رشد ریسک نسبت به مرحله‌ی قبل
TARGET_HEARTS_PER_LEVEL = 4  # سود هر مرحله بر حسب قلب
PAYOUT_EVERY = 4             # هر چند مرحله برداشت
PAYOUT_RATIO = Decimal('0.10')


def _clamp01(x):
    return max(Decimal('0'), min(Decimal('1'), x))


def trader_quality_score(trades, base_risk):
    """امتیاز کیفیت تریدر بین 0 تا 1. ترتیب trades باید زمانی باشد."""
    n = len(trades)
    if n < MIN_TRADES_FOR_SCORE:
        return Decimal('0.5')

    profits = [Decimal(str(t.net_profit)) for t in trades]
    wins = [p for p in profits if p > 0]
    gross_win = sum(wins, Decimal('0'))
    gross_loss = -sum((p for p in profits if p < 0), Decimal('0'))

    # ۱) وین‌ریت: 40% -> 0 ، 70% -> 1
    win_rate = Decimal(len(wins)) / Decimal(n)
    wr_score = _clamp01((win_rate - Decimal('0.40')) / Decimal('0.30'))

    # ۲) پروفیت‌فاکتور: 1.0 -> 0 ، 2.0 -> 1
    if gross_loss == 0:
        pf_score = Decimal('1') if gross_win > 0 else Decimal('0')
    else:
        pf_score = _clamp01(gross_win / gross_loss - Decimal('1'))

    # ۳) ثبات: حداکثر افت سرمایه بر حسب قلب (0 قلب -> 1 ، 10 قلب -> 0)
    equity = peak = max_dd = Decimal('0')
    for p in profits:
        equity += p
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    dd_hearts = max_dd / base_risk if base_risk > 0 else Decimal('10')
    dd_score = _clamp01(Decimal('1') - dd_hearts / Decimal('10'))

    return (Decimal('0.30') * wr_score +
            Decimal('0.30') * pf_score +
            Decimal('0.40') * dd_score)


def target_hearts_from_score(score):
    """امتیاز 1 -> 4 قلب ، امتیاز 0 -> 10 قلب"""
    span = MAX_HEARTS_WEAK - MIN_HEARTS_STRONG
    return MAX_HEARTS_WEAK - int((score * span).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def smoothed_required_hearts(trades, base_risk):
    """
    از ابتدای تاریخچه جلو می‌رود؛ هر EVAL_EVERY معامله، امتیاز را روی
    SCORE_WINDOW معامله‌ی آخر حساب می‌کند و required_hearts را حداکثر
    یک پله به سمت هدف می‌برد. بدون نیاز به ذخیره در دیتابیس.
    Returns: (required_hearts, آخرین امتیاز)
    """
    hearts = START_REQUIRED_HEARTS
    score = Decimal('0.5')
    for i in range(EVAL_EVERY, len(trades) + 1, EVAL_EVERY):
        window = trades[max(0, i - SCORE_WINDOW):i]
        score = trader_quality_score(window, base_risk)
        target = target_hearts_from_score(score)
        if target > hearts:
            hearts += 1
        elif target < hearts:
            hearts -= 1
    hearts = max(MIN_HEARTS_STRONG, min(MAX_HEARTS_WEAK, hearts))
    return hearts, score


def risk_from_reserve(reserve, base_risk, required_hearts, prev_risk=None):
    """
    بزرگ‌ترین ضریب k که ذخیره بعد از افزایش ریسک هنوز حداقل
    required_hearts قلب بدهد؛ با سقف رشد نسبت به مرحله‌ی قبل.
    """
    if reserve <= 0:
        return base_risk
    k = int(reserve / (base_risk * Decimal(required_hearts)))
    risk = base_risk * max(k, 1)
    if prev_risk is not None and prev_risk > 0:
        risk = min(risk, prev_risk * GROWTH_CAP)
    risk = max(risk, base_risk)
    return risk.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def get_user_progression_plan(user):
    all_trades = list(
        TradeJournal.active_objects
        .filter(user=user)
        .exclude(result='PENDING')
        .order_by('exit_time') 
    )

    base_risk = (user.initial_balance / Decimal("100")
                 if getattr(user, 'initial_balance', None) else Decimal("1.0"))

    # ۱) کیفیت تریدر -> قلب لازم برای مرحله‌ی بعد (بین 4 تا 10)
    required_hearts, quality_score = smoothed_required_hearts(all_trades, base_risk)

    total_net_profit = sum((Decimal(str(t.net_profit)) for t in all_trades), Decimal('0.0'))

    # ۲) ساخت جدول مراحل
    levels = []
    current_cumulative_profit = Decimal('0.0')
    reserve = Decimal('0.0')
    prev_risk = None

    for lvl in range(1, 101):
        risk_per_trade = risk_from_reserve(reserve, base_risk, required_hearts, prev_risk)
        prev_risk = risk_per_trade
        reserve_hearts = int(reserve / risk_per_trade) if risk_per_trade > 0 else 0

        stage_profit = risk_per_trade * Decimal(str(TARGET_HEARTS_PER_LEVEL))
        current_cumulative_profit += stage_profit
        reserve += stage_profit

        has_payout = (lvl % PAYOUT_EVERY == 0)
        payout_amount = reserve * PAYOUT_RATIO if has_payout else Decimal('0.0')
        reserve -= payout_amount

        levels.append({
            'level_num': lvl,
            'risk_per_trade': risk_per_trade,
            'target_hearts': TARGET_HEARTS_PER_LEVEL,
            'reserve_hearts': reserve_hearts,
            'stage_profit': stage_profit,
            'cumulative_profit': current_cumulative_profit,
            'has_payout': has_payout,
            'payout_amount': payout_amount,
        })

    earned_hearts_in_level = 0
    required_recovery_hearts = 0

    # 🛑 حساب در ضرر
    if total_net_profit < 0:
        is_in_loss = True
        current_level = levels[0]
        next_4_levels = levels[0:4]

        first_stage_target = levels[0]['cumulative_profit']
        calculated_drawdown = (abs(total_net_profit) / first_stage_target) * Decimal('100')
        completion_percentage = min(100, max(1, int(calculated_drawdown)))

        profit_per_heart = current_level['risk_per_trade']
        if profit_per_heart > 0:
            required_recovery_hearts = int(
                (abs(total_net_profit) / profit_per_heart).to_integral_value(rounding=ROUND_CEILING)
            )
        else:
            required_recovery_hearts = 1

        trend_status = f"حساب در ضرر (-{abs(total_net_profit):.1f}{CURENCY_UNIT}): نیازمند جبران ⚠️"
        trend_color = "text-rose-400"

    # 📈 حساب در سود
    else:
        is_in_loss = False
        active_level_idx = len(levels) - 1
        for i, lv in enumerate(levels):
            if total_net_profit < lv['cumulative_profit']:
                active_level_idx = i
                break

        current_level = levels[active_level_idx]
        next_4_levels = levels[active_level_idx: active_level_idx + 4]

        prev_cumulative = (levels[active_level_idx - 1]['cumulative_profit']
                           if active_level_idx > 0 else Decimal('0.0'))
        progress_in_level = total_net_profit - prev_cumulative
        level_span = current_level['stage_profit']

        if level_span > 0:
            pct_calc = (progress_in_level / level_span) * Decimal('100')
            completion_percentage = min(100, max(0, int(pct_calc.quantize(Decimal('1'), rounding=ROUND_HALF_UP))))
        else:
            completion_percentage = 0

        profit_per_heart = current_level['risk_per_trade']
        earned_hearts_in_level = int(progress_in_level / profit_per_heart) if profit_per_heart > 0 else 0

        if completion_percentage >= 75:
            trend_status = "نزدیک به پایان مرحله (عالی) 🚀"
            trend_color = "text-emerald-400"
        elif completion_percentage > 0:
            trend_status = "در حال پیشروی در مرحله 📈"
            trend_color = "text-sky-400"
        else:
            trend_status = "در آستانه شروع مرحله جدید 📊"
            trend_color = "text-amber-400"
    
    heart_slots = [i <= required_hearts for i in range(1, 11)]
    stage_heart_slots = [i <= earned_hearts_in_level for i in range(1, TARGET_HEARTS_PER_LEVEL + 1)]

    return {
        'current_level_num': current_level['level_num'],
        'current_risk': current_level['risk_per_trade'],
        'completion_percentage': completion_percentage,
        'is_in_loss': is_in_loss,
        'earned_hearts_in_level': earned_hearts_in_level,
        'required_recovery_hearts': required_recovery_hearts,
        'target_hearts': current_level['target_hearts'],
        'required_hearts': required_hearts,                 # جدید: 4 تا 10
        'quality_score': int(quality_score * 100),          # جدید: 0 تا 100
        'next_4_levels': next_4_levels,
        'trend_status': trend_status,
        'trend_color': trend_color,
        'heart_slots': heart_slots,
        'stage_heart_slots': stage_heart_slots, 
    }

RATIO_CAP = 99.0                       # سقف نسبت‌هایی که مخرج صفر دارند
CLOSED_RESULTS = ['WIN', 'LOSS', 'BE']  # PENDING وارد محاسبات نمی‌شود
 
# معادل دیتابیسیِ property مدل:  net_profit = profit - abs(commission) + swap
NET_PROFIT_EXPR = ExpressionWrapper(
    F('profit') - Abs(F('commission')) + F('swap'),
    output_field=DecimalField(max_digits=20, decimal_places=2),
)
 
 
def _dec(value) -> Decimal:
    return Decimal(str(value)) if value is not None else Decimal('0.00')
 
 
def _local(dt):
    if dt is not None and timezone.is_aware(dt):
        return timezone.localtime(dt)
    return dt
 
 
def compute_drawdown(pnls, start_equity: Decimal) -> dict:
    """
    منحنی اکوییتی و درادان را از لیست PnL معاملات (به ترتیب زمان بسته‌شدن) می‌سازد.
    قله از اکوییتی ابتدای بازه شروع می‌شود (نه از صفر).
    درادان درصدی نسبت به قله اکوییتی است؛ درادان دلاری هم جداگانه برگردانده می‌شود.
    """
    running = Decimal('0.00')
    peak_running = Decimal('0.00')
    peak_equity = start_equity
 
    cumulative, dd_pct_curve = [], []
    max_dd_usd = Decimal('0.00')
    max_dd_pct = Decimal('0.00')
 
    for pnl in pnls:
        running += pnl
        equity = start_equity + running
 
        peak_running = max(peak_running, running)
        peak_equity = max(peak_equity, equity)
 
        max_dd_usd = min(max_dd_usd, running - peak_running)
 
        dd_pct = ((equity - peak_equity) / peak_equity * 100) if peak_equity > 0 else Decimal('0.00')
        max_dd_pct = min(max_dd_pct, dd_pct)
 
        cumulative.append(running)
        dd_pct_curve.append(dd_pct)
 
    return {
        'cumulative': cumulative,
        'dd_pct_curve': dd_pct_curve,
        'max_dd_usd': max_dd_usd,
        'max_dd_pct': max_dd_pct,
        'total': running,
    }
 
 
def calculate_trading_metrics(period: str = 'weekly', req_year: int = None,
                              req_month: int = None, user=None) -> dict:
    now = timezone.localtime(timezone.now())
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = today_start.replace(day=1)
 
    # ------------------------------------------------------------------
    # ۱. بازه زمانی
    # ------------------------------------------------------------------
    def week_start():
        days_since_saturday = (now.weekday() - 5) % 7   # شروع هفته: شنبه
        return today_start - timedelta(days=days_since_saturday)
 
    if period == 'daily':
        start_date = today_start
    elif period == 'weekly':
        start_date = week_start()
    elif period == 'monthly':
        # از اول ماه جاری
        start_date = month_start
    elif period == '3months':
        # ماه جاری + 2 ماه قبل
        start_date = month_start - relativedelta(months=2)
    elif period == '6months':
        # 6 ماه اول سال (از اول ژانویه)
        start_date = today_start.replace(month=1, day=1)
    elif period == 'yearly':
        # از اول سال جاری
        start_date = today_start.replace(month=1, day=1)
    elif period == 'all':
        start_date = None
    else:
        period = 'weekly'
        start_date = week_start()
 
    # ------------------------------------------------------------------
    # ۲. حساب، بالانس اولیه و کوئری پایه
    # ------------------------------------------------------------------
    account = user or TelegramUser.objects.filter(is_blocked=False).order_by('id').first()
    initial_balance = _dec(account.initial_balance) if account else Decimal('100.00')
 
    # زمان تحقق سود = زمان خروج؛ اگر خالی بود ورود؛ اگر آن هم خالی بود زمان ثبت
    base_qs = (
        TradeJournal.active_objects
        .filter(result__in=CLOSED_RESULTS)
        .annotate(close_time=Coalesce('exit_time', 'entry_time', 'created_at'))
    )
    if account:
        base_qs = base_qs.filter(user=account)
 
    prior_pnl = Decimal('0.00')
    trades_qs = base_qs
    if start_date:
        prior_pnl = _dec(
            base_qs.filter(close_time__lt=start_date).aggregate(s=Sum(NET_PROFIT_EXPR))['s']
        )
        trades_qs = base_qs.filter(close_time__gte=start_date)
 
    start_equity = initial_balance + prior_pnl
    trades_ascending = list(trades_qs.order_by('close_time', 'id'))
 
    # ------------------------------------------------------------------
    # ۳. پردازش معاملات
    # ------------------------------------------------------------------
    processed_trades = []
    pnl_list = []
    symbol_pnl = defaultdict(Decimal)
    day_pnl = {i: Decimal('0.00') for i in range(7)}
 
    planned_rr_list = [0.0]
    realized_rr_list = [0.0]
    chart_labels = ['Start']
 
    gross_wins = Decimal('0.00')
    gross_losses = Decimal('0.00')
    total_commission = Decimal('0.00')
    total_swap = Decimal('0.00')
    wins = losses = be_count = 0
 
    for trade in trades_ascending:
        entry = _dec(trade.entry_price) if trade.entry_price is not None else None
        exit_p = _dec(trade.exit_price) if trade.exit_price is not None else None
        sl = _dec(trade.stop_loss) if trade.stop_loss is not None else None
        tp = _dec(trade.take_profit) if trade.take_profit is not None else None
 
        # R:R برنامه‌ریزی‌شده
        planned_val, planned_rr = 0.0, "-"
        if entry and sl and tp:
            risk = abs(entry - sl)
            if risk > 0:
                planned_val = round(float(abs(tp - entry) / risk), 2)
                planned_rr = f"1:{planned_val:g}"
 
        # R:R واقعی علامت‌دار (ضرر منفی)
        realized_val, realized_rr = 0.0, "-"
        if entry and sl and exit_p:
            risk = abs(entry - sl)
            if risk > 0:
                move = (exit_p - entry) if trade.trade_type == 'BUY' else (entry - exit_p)
                realized_val = round(float(move / risk), 2)
                realized_rr = f"{realized_val:+g}R"
 
        pnl = _dec(trade.net_profit)                 # profit - abs(commission) + swap
        comm = -abs(_dec(trade.commission))          # همیشه هزینه (منفی)، هماهنگ با net_profit
        swp = _dec(trade.swap)
 
        total_commission += comm
        total_swap += swp
 
        # شمارش و جمع ناخالص هر دو بر اساس result
        if trade.result == 'WIN':
            wins += 1
            gross_wins += pnl
        elif trade.result == 'LOSS':
            losses += 1
            gross_losses += abs(pnl)
        else:
            be_count += 1
 
        close_time = _local(trade.close_time)
        symbol_pnl[trade.symbol] += pnl
        day_pnl[close_time.weekday()] += pnl
 
        pnl_list.append(pnl)
        planned_rr_list.append(planned_val)
        realized_rr_list.append(realized_val)
        chart_labels.append(close_time.strftime('%Y-%m-%d %H:%M'))
 
        trade.fmt_comm = float(comm)
        trade.fmt_entry = float(entry) if entry else None
        trade.fmt_exit = float(exit_p) if exit_p else None
        trade.fmt_sl = float(sl) if sl else None
        trade.fmt_tp = float(tp) if tp else None
        trade.calc_planned_rr = planned_rr
        trade.calc_realized_rr = realized_rr
        processed_trades.append(trade)
 
    # ------------------------------------------------------------------
    # ۴. درادان و منحنی سود
    # ------------------------------------------------------------------
    dd = compute_drawdown(pnl_list, start_equity)
    cumulative_pnl_data = [0.0] + [float(round(x, 2)) for x in dd['cumulative']]
    drawdown_data = [0.0] + [float(round(x, 2)) for x in dd['dd_pct_curve']]
    max_drawdown_usd = dd['max_dd_usd']
    max_drawdown_pct = dd['max_dd_pct']
    abs_dd_pct = abs(float(max_drawdown_pct))
    running_total = dd['total']
 
    # ------------------------------------------------------------------
    # ۵. آمار کل
    # ------------------------------------------------------------------
    total_trades = len(processed_trades)
    win_rate = round(wins / total_trades * 100, 1) if total_trades > 0 else 0.0
    total_net_profit = float(round(running_total, 2))
 
    avg_win = float(round(gross_wins / wins, 2)) if wins > 0 else 0.0
    avg_loss = float(round(gross_losses / losses, 2)) if losses > 0 else 0.0
 
    if avg_loss > 0:
        payoff_ratio = round(avg_win / avg_loss, 2)
    elif avg_win > 0:
        payoff_ratio = RATIO_CAP
    else:
        payoff_ratio = 0.0
 
    win_prob = wins / total_trades if total_trades > 0 else 0.0
    loss_prob = losses / total_trades if total_trades > 0 else 0.0
    expectancy = round(win_prob * avg_win - loss_prob * avg_loss, 2) if total_trades > 0 else 0.0
 
    # Sharpe per-trade (annualize نشده، واریانس نمونه n-1)
    trade_pnls = [float(p) for p in pnl_list]
    if len(trade_pnls) > 1:
        mean_pnl = sum(trade_pnls) / len(trade_pnls)
        variance = sum((x - mean_pnl) ** 2 for x in trade_pnls) / (len(trade_pnls) - 1)
        std_dev = math.sqrt(variance)
        sharpe_ratio = round(mean_pnl / std_dev, 2) if std_dev > 0 else 0.0
    else:
        sharpe_ratio = 0.0
 
    if gross_losses > 0:
        profit_factor = round(min(float(gross_wins / gross_losses), RATIO_CAP), 2)
    elif gross_wins > 0:
        profit_factor = RATIO_CAP
    else:
        profit_factor = 0.0
 
    streaks_data = calculate_streaks(processed_trades)
 
    # ------------------------------------------------------------------
    # ۶. بازدهی بر اساس اکوییتی ابتدای بازه و روزهای واقعی
    # ------------------------------------------------------------------
    start_equity_f = float(start_equity)
    gain_percent = round(total_net_profit / start_equity_f * 100, 2) if start_equity_f > 0 else 0.0
 
    if start_date:
        period_days = max(1, (now - start_date).days)
    elif processed_trades:
        first_time = _local(processed_trades[0].close_time)
        period_days = max(1, (now - first_time).days)
    else:
        period_days = 1
 
    if total_trades > 0:
        period_daily_return = round(gain_percent / period_days, 2)
        period_weekly_return = round(gain_percent / period_days * 7, 2)
        period_monthly_return = round(gain_percent / period_days * 30, 2)
        period_annual_return = round(gain_percent / period_days * 365, 2)
    else:
        period_daily_return = period_weekly_return = period_monthly_return = period_annual_return = 0.0
 
    # ------------------------------------------------------------------
    # ۷. ریسک، پایداری، رادار (همه بر پایه درادان درصدی)
    # ------------------------------------------------------------------
    if abs_dd_pct <= 5.0:
        risk_level_label, risk_level_color = "پایین (ایمن)", "text-emerald-400"
    elif abs_dd_pct <= 15.0:
        risk_level_label, risk_level_color = "متوسط (کنترل‌شده)", "text-sky-400"
    elif abs_dd_pct <= 25.0:
        risk_level_label, risk_level_color = "بالا (هشدار)", "text-amber-400"
    else:
        risk_level_label, risk_level_color = "بسیار بالا (خطرناک)", "text-rose-400"
 
    if profit_factor >= 2.0 and win_rate >= 50:
        stability_label, stability_color = "عالی و باثبات", "text-emerald-400"
    elif profit_factor >= 1.2 and win_rate >= 40:
        stability_label, stability_color = "بسیار خوب", "text-emerald-400"
    elif profit_factor >= 1.0:
        stability_label, stability_color = "نرمال / لب‌مرز", "text-amber-400"
    else:
        stability_label, stability_color = "نیاز به بازنگری ریسک", "text-rose-400"
 
    entry_score = max(0, min(100, int(win_rate * 1.5)))
    exit_score = max(0, min(100, int(payoff_ratio * 35)))
    risk_score = max(10, min(100, 100 - int(abs_dd_pct * 2)))   # ۴۵٪ افت => ۱۰
    stability_score = max(0, min(100, int((sharpe_ratio + 1) * 30)))
    tempo_score = max(0, min(100, int(total_trades * 3)))
    radar_score_overall = int((entry_score + exit_score + risk_score + stability_score + tempo_score) / 5)
    radar_data = [entry_score, risk_score, exit_score, stability_score, tempo_score]
 
    # ------------------------------------------------------------------
    # ۸. زاویه گیج‌ها
    # ------------------------------------------------------------------
    abs_gain_percent = abs(gain_percent)
    gauge_angle = min(360, int((abs_gain_percent / 100) * 360)) if abs_gain_percent > 0 else 0
    win_rate_angle = int((win_rate / 100) * 360) if win_rate > 0 else 0
    pf_angle = min(360, int((profit_factor / 3.0) * 360)) if profit_factor > 0 else 0
    dd_angle = min(360, int((abs_dd_pct / 50.0) * 360)) if abs_dd_pct > 0 else 0   # ۵۰٪ افت = دایره کامل
 
    # ------------------------------------------------------------------
    # ۹. انضباط ذهنی
    # ------------------------------------------------------------------
    win_rate_bonus = min(20.0, (win_rate / 100.0) * 25.0)
    dd_penalty = min(25.0, abs_dd_pct * 1.5)
    payoff_bonus = min(15.0, float(payoff_ratio) * 5.0) if payoff_ratio > 0 else 0.0
    mental_discipline_score = max(10, min(100, int(50.0 + win_rate_bonus - dd_penalty + payoff_bonus)))
 
    if mental_discipline_score >= 80:
        discipline_label, discipline_color = "فوق‌العاده و حرفه‌ای", "text-emerald-400"
    elif mental_discipline_score >= 60:
        discipline_label, discipline_color = "پایدار و کنترل‌شده", "text-cyan-400"
    elif mental_discipline_score >= 40:
        discipline_label, discipline_color = "نیاز به تمرکز بیشتر", "text-amber-400"
    else:
        discipline_label, discipline_color = "رفتار هیجانی / پرخطر", "text-rose-400"
 
    # ------------------------------------------------------------------
    # ۱۰. داده‌های نمودار و context
    # ------------------------------------------------------------------
    days_names = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
    day_pnl_values = [float(round(day_pnl[i], 2)) for i in range(7)]
    symbols_list = list(symbol_pnl.keys())
    symbols_pnl_values = [float(round(symbol_pnl[s], 2)) for s in symbols_list]
 
    context = {
        'trades': processed_trades,
        'current_period': period,
        'total_trades': total_trades,
        'win_rate': win_rate,
        'wins': wins,
        'losses': losses,
        'be_count': be_count,
        'total_net_profit': total_net_profit,
        'profit_factor': profit_factor,
        'avg_win': avg_win,
        'avg_loss': avg_loss,
        'payoff_ratio': payoff_ratio,
        'expectancy': expectancy,
        'sharpe_ratio': sharpe_ratio,
        'max_drawdown': float(round(max_drawdown_usd, 2)),       # دلاری
        'max_drawdown_pct': float(round(max_drawdown_pct, 2)),   # درصدی
        'total_commission': float(round(total_commission, 2)),
        'total_swap': float(round(total_swap, 2)),
        'streaks': streaks_data,
        'stability_label': stability_label,
        'stability_color': stability_color,
        'radar_score_overall': radar_score_overall,
        'radar_json': json.dumps(radar_data),
        'gain_percent': gain_percent,
        'abs_gain_percent': abs_gain_percent,
        'period_returns': {
            'daily': period_daily_return,
            'weekly': period_weekly_return,
            'monthly': period_monthly_return,
            'annualized': period_annual_return,
        },
        'chart_labels_json': json.dumps(chart_labels),
        'chart_pnl_json': json.dumps(cumulative_pnl_data),
        'drawdown_json': json.dumps(drawdown_data),
        'win_loss_json': json.dumps([wins, losses, be_count]),
        'days_names_json': json.dumps(days_names),
        'day_pnl_json': json.dumps(day_pnl_values),
        'symbols_json': json.dumps(symbols_list),
        'symbols_pnl_json': json.dumps(symbols_pnl_values),
        'planned_rr_json': json.dumps(planned_rr_list),
        'realized_rr_json': json.dumps(realized_rr_list),
        'risk_level_label': risk_level_label,
        'risk_level_color': risk_level_color,
        'gauge_angle': gauge_angle,
        'win_rate_angle': win_rate_angle,
        'pf_angle': pf_angle,
        'dd_angle': dd_angle,
        'mental_discipline_score': mental_discipline_score,
        'discipline_label': discipline_label,
        'discipline_color': discipline_color,
    }
 
    if req_year and req_month:
        context.update(get_monthly_calendar_data(req_year, req_month, initial_balance))
 
    if account:
        context.update(get_user_progression_plan(account))
 
    return context

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
        for model_name in AI_MODULES:
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
                        logger.warning("Model %s not found. Switching to the next model...", model_name)
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
        models_to_try = ['gemini-3.8-flash','gemini-3.7-flash','gemini-3.6-flash','gemini-3.5-flash']

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


def analyze_trades_with_gemini(metrics: dict, period_name: str) -> str:
    """نسخه معمولی (Sync) بدون نیاز به async/await"""
    if metrics.get('total_trades', 0) == 0:
        return "هیچ معامله‌ای برای تحلیل در این بازه یافت نشد."

    if not API_KEY:
        return "کلید API هوش مصنوعی تنظیم نشده است."

    # استخراج مستقیم تمام متریک‌ها از خروجی تابع calculate_trading_metrics
    total_trades = metrics.get('total_trades', 0)
    win_rate = metrics.get('win_rate', 0.0)
    wins = metrics.get('wins', 0)
    losses = metrics.get('losses', 0)
    be_count = metrics.get('be_count', 0)
    total_profit = metrics.get('total_net_profit', 0.0)
    profit_factor = metrics.get('profit_factor', 0.0)
    max_drawdown = metrics.get('max_drawdown', 0.0)
    payoff_ratio = metrics.get('payoff_ratio', 0.0)
    avg_win = metrics.get('avg_win', 0.0)
    avg_loss = metrics.get('avg_loss', 0.0)
    expectancy = metrics.get('expectancy', 0.0)
    sharpe_ratio = metrics.get('sharpe_ratio', 0.0)
    total_commission = metrics.get('total_commission', 0.0)
    total_swap = metrics.get('total_swap', 0.0)
    streaks = metrics.get('streaks', {})
    
    # استخراج نمادها و تحلیل سود/زیان هرکدام از داده‌های موجود در metrics
    processed_trades = metrics.get('trades', [])
    symbol_pnl_map = defaultdict(Decimal)
    all_notes = []

    for t in processed_trades:
        if t.symbol and t.net_profit is not None:
            symbol_pnl_map[t.symbol] += Decimal(str(t.net_profit))
        if t.notes and t.notes.strip():
            all_notes.append(f"[{t.symbol} - {t.result}]: {t.notes}")

    best_sym = max(symbol_pnl_map, key=symbol_pnl_map.get) if symbol_pnl_map else "نامشخص"
    worst_sym = min(symbol_pnl_map, key=symbol_pnl_map.get) if symbol_pnl_map else "نامشخص"
    notes_summary = " | ".join(all_notes[:15]) if all_notes else "یادداشتی ثبت نشده است."

    prompt = f"""
    تو یک مربی ارشد روانشناسی معاملات و تحلیل‌گر کمّی بازارهای مالی (فارکس و کریپتو) در سال ۲۰۲۷ هستی. بر اساس داده‌های دقیق زیر که مستقیماً از ژورنال کاربر استخراج شده، عملکرد او را در بازه ({period_name}) تحلیل کن:

    📊 **آمار جامع و کمّی محاسبه‌شده:**
    - تعداد کل معاملات: {total_trades} (برد: {wins} | باخت: {losses} | سر به سر: {be_count})
    - نرخ پیروزی (Win Rate): {win_rate}%
    - سود/زیان خالص کل: {CURENCY_UNIT}{total_profit} (کمیسیون پرداختی: {CURENCY_UNIT}{total_commission} | سواپ: {CURENCY_UNIT}{total_swap})
    - ضریب سودآوری (Profit Factor): {profit_factor}
    - حداکثر افت حساب (Max Drawdown): %{max_drawdown}
    - نسبت Payoff (میانگین سود به زیان): {payoff_ratio}
    - میانگین سود: %{avg_win} | میانگین زیان: {CURENCY_UNIT}{avg_loss}
    - امید ریاضی (Expectancy): %{expectancy}
    - نسبت شارپ (Sharpe Ratio): %{sharpe_ratio}
    - زنجیره برد/باخت متوالی: بیشترین برد روزانه ({streaks.get('max_win_streak_days', 0)} روز) | بیشترین ترید موفق ({streaks.get('max_win_streak_trades', 0)} ترید)

    📌 **عملکرد نمادها:**
    - سودآورترین نماد: {best_sym}
    - زیان‌ده‌ترین نماد: {worst_sym}

    🧠 **یادداشت‌ها و روانشناسی ثبت‌شده در معاملات:**
    "{notes_summary}"

    لطفاً تحلیل پیشرفته خود را دقیقاً در ۴ بخش زیر ارائه بده:
    ۱. **ارزیابی پایداری استراتژی** (بر اساس Profit Factor، Expectancy و Drawdown)
    ۲. **روانشناسی رفتار و ریشه‌یابی یادداشت‌ها** (کشف تله‌های ذهنی، FOMO یا عجله بر اساس نوت‌های بالا)
    ۳. **مدیریت ریسک و انضباط اجرایی** (تحلیل Payoff و استریک‌های باخت)
    ۴. **نقشه راه عملیاتی برای بازه بعدی** (تمرکز سود روی {best_sym} و متوقف کردن خطاها در {worst_sym})

    پاسخ را کاملاً حرفه‌ای، بدون حشو، ساختاریافته با ایموجی‌های مناسب و به زبان فارسی روان بنویس.
    """
    client = genai.Client(api_key=API_KEY)
    for model_name in AI_MODULES:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(temperature=0.4)
            )
            if response and response.text:
                return response.text.strip()
        except Exception as e:
            logger.error("Error analyzing trades with Gemini: %s", e)
            continue

    return "`[SYSTEM_ALERT]:` ارتباط با هسته‌های عصبی هوش مصنوعی موقتاً قطع شد. لطفاً چند لحظه دیگر مجدداً تلاش کنید. ⚡️"