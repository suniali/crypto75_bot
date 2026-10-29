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
from collections import defaultdict

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

def risk_from_reserve(reserve, base_risk,max_reserve_hearts):
    """
    ریسک مرحله از روی ذخیره‌ی سود در شروع مرحله:
    کوچک‌ترین مضرب ریسک پایه که قلب ذخیره از 10 بیشتر نشه،
    و اگر کمتر از ۴ قلب موند، یک پله پایین‌تر.
    """
    if reserve <= 0:
        return base_risk
    k = int(reserve / (base_risk * Decimal(str(max_reserve_hearts + 1)))) + 1
    if k > 1 and int(reserve / (base_risk * k)) < MIN_RESERVE_HEARTS:
        k -= 1
    return base_risk * k

def get_user_progression_plan(user):
    all_trades = TradeJournal.active_objects.exclude(result='PENDING')

    # ۱. محاسبه وین‌ریت معامله‌گر برای تعیین پویا ی MAX_RESERVE_HEARTS
    total_trades = len(all_trades)
    if total_trades > 0:
        winning_trades = sum(1 for t in all_trades if Decimal(str(t.net_profit)) > 0)
        win_rate = (Decimal(str(winning_trades)) / Decimal(str(total_trades))) * Decimal('100')
    else:
        win_rate = Decimal('50.0') # مقدار پیش‌فرض برای حساب‌های تازه بدون معامله
        
    # ۲. تعیین پویا ی MAX_RESERVE_HEARTS بر اساس وین‌ریت (تریدر قوی‌تر = عدد کمتر، حداقل ۵)
    if win_rate >= Decimal('70.0'):
        max_reserve_hearts = 5    # تریدر بسیار قوی (حداقل مجاز)
    elif win_rate >= Decimal('60.0'):
        max_reserve_hearts = 6
    elif win_rate >= Decimal('50.0'):
        max_reserve_hearts = 8
    elif win_rate >= Decimal('40.0'):
        max_reserve_hearts = 10
    else:
        max_reserve_hearts = 12   # تریدر ضعیف‌تر (نیاز به احتیاط و ذخیره بیشتر)
    
    # تبدیل امن بالانس اولیه و ریسک پایه به Decimal
    base_risk = user.initial_balance / Decimal("100") if getattr(user, 'initial_balance', None) else Decimal("1.0")
    
    # جمع زدن سود کل معاملات
    total_net_profit = sum((Decimal(str(t.net_profit)) for t in all_trades), Decimal('0.0'))
    
    levels = []
    current_cumulative_profit = Decimal('0.0')
    reserve = Decimal('0.0')

    # ساخت جدول پویای مراحل (تا ۱۰۰ مرحله)
    for lvl in range(1, 101):
        risk_per_trade = risk_from_reserve(reserve, base_risk, max_reserve_hearts)
        reserve_hearts = int(reserve / risk_per_trade) if risk_per_trade > 0 else 0

        target_hearts = 4
        stage_profit = risk_per_trade * Decimal(str(target_hearts))
        current_cumulative_profit += stage_profit
        reserve += stage_profit

        has_payout = (lvl % 4 == 0)
        payout_amount = reserve * Decimal('0.10') if has_payout else Decimal('0.0')
        reserve -= payout_amount

        levels.append({
            'level_num': lvl,
            'risk_per_trade': risk_per_trade,
            'target_hearts': target_hearts,
            'reserve_hearts': reserve_hearts,
            'stage_profit': stage_profit,
            'cumulative_profit': current_cumulative_profit,
            'has_payout': has_payout,
            'payout_amount': payout_amount
        })

    earned_hearts_in_level = 0
    required_recovery_hearts = 0
    is_in_loss = False
    trend_status = ""
    trend_color = "text-slate-400"

    # 🛑 سناریوی اول: حساب در مجموع در ضرر است (منفی)
    if total_net_profit < 0:
        is_in_loss = True
        active_level_idx = 0
        current_level = levels[0]
        next_4_levels = levels[0:4]
        
        first_stage_target = levels[0]['cumulative_profit']
        calculated_drawdown = (abs(total_net_profit) / first_stage_target) * Decimal('100')
        completion_percentage = min(100, max(1, int(calculated_drawdown)))
        
        # قلب‌های لازم برای جبران بر اساس ریسک سطح فعلی
        profit_per_heart = current_level['risk_per_trade']
        if profit_per_heart > 0:
            div_result = abs(total_net_profit) / profit_per_heart
            required_recovery_hearts = int(div_result.to_integral_value(rounding=ROUND_CEILING))
        else:
            required_recovery_hearts = 1
            
        trend_status = f"حساب در ضرر (-{abs(total_net_profit):.1f}{CURENCY_UNIT}): نیازمند جبران ⚠️"
        trend_color = "text-rose-400"
        
    # 📈 سناریوی دوم: حساب در سود است (پیدا کردن سطح فعال بر اساس سود واقعی)
    else:
        is_in_loss = False
        active_level_idx = 0
        for i, lvl in enumerate(levels):
            if total_net_profit < lvl['cumulative_profit']:
                active_level_idx = i
                break
        else:
            active_level_idx = len(levels) - 1
            
        current_level = levels[active_level_idx]
        start_show_idx = max(0, active_level_idx)
        next_4_levels = levels[start_show_idx : start_show_idx + 4]
        
        prev_cumulative = levels[active_level_idx - 1]['cumulative_profit'] if active_level_idx > 0 else Decimal('0.0')
        progress_in_level = total_net_profit - prev_cumulative
        level_span = current_level['stage_profit']
        
        if level_span > 0:
            pct_calc = (progress_in_level / level_span) * Decimal('100')
            completion_percentage = min(100, max(0, int(pct_calc.quantize(Decimal('1'), rounding=ROUND_HALF_UP))))
        else:
            completion_percentage = 0
            
        profit_per_heart = current_level['risk_per_trade']
        if profit_per_heart > 0:
            earned_hearts_in_level = int(progress_in_level / profit_per_heart)
        else:
            earned_hearts_in_level = 0
            
        # تشخیص وضعیت روند برای نمایش پیام‌های جذاب
        if completion_percentage >= 75:
            trend_status = "نزدیک به پایان مرحله (عالی) 🚀"
            trend_color = "text-emerald-400"
        elif completion_percentage > 0:
            trend_status = "در حال پیشروی در مرحله 📈"
            trend_color = "text-sky-400"
        else:
            trend_status = "در آستانه شروع مرحله جدید 📊"
            trend_color = "text-amber-400"

    return {
        'current_level_num': current_level['level_num'],
        'current_risk': current_level['risk_per_trade'],
        'completion_percentage': completion_percentage,
        'is_in_loss': is_in_loss,
        'earned_hearts_in_level': earned_hearts_in_level,
        'required_recovery_hearts': required_recovery_hearts,
        'target_hearts': current_level['target_hearts'],
        'next_4_levels': next_4_levels,
        'trend_status': trend_status,
        'trend_color': trend_color,
    }

def calculate_trading_metrics(period: str='weekly', req_year: int=None, req_month: int=None) -> dict:
    now = timezone.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    if period == 'daily':
        # از ساعت ۰۰:۰۰ امروز تا الان
        start_date = today_start
    elif period == 'weekly':
        # شروع هفته از روز شنبه (محاسبه دقیق فاصله تا شنبه گذشته)
        days_since_saturday = (now.weekday() - 5) % 7
        start_date = today_start - timedelta(days=days_since_saturday)
    elif period == 'monthly':
        start_date = now - timedelta(days=30)
    elif period == '3months':
        start_date = now - timedelta(days=90)
    elif period == '6months':
        start_date = now - timedelta(days=180)
    elif period == 'yearly':
        start_date = now - timedelta(days=365)
    elif period == 'all':
        start_date = None
    else:
        period = 'weekly'
        days_since_saturday = (now.weekday() - 5) % 7
        start_date = today_start - timedelta(days=days_since_saturday)

    trades_qs = TradeJournal.active_objects.all()
    if start_date:
        trades_qs = trades_qs.filter(entry_time__gte=start_date)

    # مرتب‌سازی صعودی برای محاسبه دقیق PnL تجمعی و نمودارها
    trades_ascending = trades_qs.order_by('entry_time', 'created_at')

    processed_trades = []
    symbol_pnl = defaultdict(Decimal)
    day_pnl = {i: Decimal('0.00') for i in range(7)}

    chart_labels = ['Start']
    cumulative_pnl_data = [0.0]
    drawdown_data = [0.0]

    planned_rr_list = [0.0]
    realized_rr_list = [0.0]

    running_total = Decimal('0.00')
    peak_pnl = Decimal('0.00')
    max_drawdown = Decimal('0.00')

    gross_wins = Decimal('0.00')
    gross_losses = Decimal('0.00')
    total_commission = Decimal('0.00')
    total_swap = Decimal('0.00')

    # ۲. پردازش دقیق معاملات
    for trade in trades_ascending:
        entry = Decimal(str(trade.entry_price)) if trade.entry_price is not None else None
        exit_p = Decimal(str(trade.exit_price)) if trade.exit_price is not None else None
        sl = Decimal(str(trade.stop_loss)) if trade.stop_loss is not None else None
        tp = Decimal(str(trade.take_profit)) if trade.take_profit is not None else None

        # R:R برنامه‌ریزی شده
        planned_val = 0.0
        planned_rr = "-"
        if entry and sl and tp:
            risk = abs(entry - sl)
            reward = abs(tp - entry)
            if risk > 0:
                planned_val = round(float(reward / risk), 2)
                planned_rr = f"1:{planned_val:g}"

        # R:R واقعی (محاسبه برای برآیند مثبت یا WIN)
        realized_val = 0.0
        realized_rr = "-"
        if trade.result == 'WIN' and entry and sl and exit_p:
            risk = abs(entry - sl)
            actual_reward = (exit_p - entry) if trade.trade_type == 'BUY' else (entry - exit_p)
            if risk > 0:
                realized_val = round(float(actual_reward / risk), 2)
                realized_rr = f"1:{realized_val:g}"

        # سود خالص
        pnl = Decimal(str(trade.net_profit)) if trade.net_profit is not None else Decimal('0.00')

        # کمیسیون و سواپ
        comm = Decimal(str(trade.commission)) if trade.commission is not None else Decimal('0.00')
        swp = Decimal(str(trade.swap)) if trade.swap is not None else Decimal('0.00')

        total_commission += comm
        total_swap += swp

        # تفکیک سود و زیان ناخالص
        if pnl > 0:
            gross_wins += pnl
        elif pnl < 0:
            gross_losses += abs(pnl)

        trade_time = trade.entry_time or trade.created_at
        symbol_pnl[trade.symbol] += pnl
        day_pnl[trade_time.weekday()] += pnl

        planned_rr_list.append(planned_val)
        realized_rr_list.append(realized_val)

        # محاسبه PnL تجمعی و Drawdown
        running_total += pnl
        if running_total > peak_pnl:
            peak_pnl = running_total

        current_dd = running_total - peak_pnl
        if current_dd < max_drawdown:
            max_drawdown = current_dd

        date_str = trade_time.strftime('%Y-%m-%d %H:%M')
        chart_labels.append(date_str)
        cumulative_pnl_data.append(float(round(running_total, 2)))
        drawdown_data.append(float(round(current_dd, 2)))

        # مقادیر فرمت‌شده برای تمپلیت
        trade.fmt_comm = float(comm)
        trade.fmt_entry = float(entry) if entry else None
        trade.fmt_exit = float(exit_p) if exit_p else None
        trade.fmt_sl = float(sl) if sl else None
        trade.fmt_tp = float(tp) if tp else None
        trade.calc_planned_rr = planned_rr
        trade.calc_realized_rr = realized_rr
        processed_trades.append(trade)

    # ۳. محاسبات نهایی آمار کل
    total_trades = len(processed_trades)
    wins = sum(1 for t in processed_trades if t.result == 'WIN')
    losses = sum(1 for t in processed_trades if t.result == 'LOSS')
    be_count = sum(1 for t in processed_trades if t.result == 'BE')

    win_rate = round((wins / total_trades * 100), 1) if total_trades > 0 else 0.0
    total_net_profit = float(round(running_total, 2))

    # میانگین سود و زیان
    avg_win = float(round(gross_wins / wins, 2)) if wins > 0 else 0.0
    avg_loss = float(round(gross_losses / losses, 2)) if losses > 0 else 0.0
    
    # نسبت Payoff (میانگین سود به میانگین زیان)
    payoff_ratio = round(avg_win / avg_loss, 2) if avg_loss > 0 else (avg_win if avg_win > 0 else 0.0)

    # امید ریاضی (Expectancy)
    win_prob = wins / total_trades if total_trades > 0 else 0.0
    loss_prob = losses / total_trades if total_trades > 0 else 0.0
    expectancy = round((win_prob * avg_win) - (loss_prob * avg_loss), 2) if total_trades > 0 else 0.0

    # محاسبه Sharpe Ratio بر اساس سود/زیان هر معامله
    trade_pnls = [float(t.net_profit) for t in processed_trades if t.net_profit is not None]
    if len(trade_pnls) > 1:
        mean_pnl = sum(trade_pnls) / len(trade_pnls)
        variance = sum((x - mean_pnl) ** 2 for x in trade_pnls) / len(trade_pnls)
        std_dev = math.sqrt(variance)
        sharpe_ratio = round(mean_pnl / std_dev, 2) if std_dev > 0 else 0.0
    else:
        sharpe_ratio = 0.0

    # محاسبه دقیق Profit Factor
    if gross_losses > 0:
        profit_factor = round(float(gross_wins / gross_losses), 2)
    elif gross_wins > 0:
        profit_factor = round(float(gross_wins), 2)
    else:
        profit_factor = 0.0

    # محاسبه متوالی‌ها (Winstreaks)
    streaks_data = calculate_streaks(processed_trades)

    # تعیین هوشمند نرخ پایداری سیستم بر اساس عملکرد کل
    if profit_factor >= 2.0 and win_rate >= 50:
        stability_label = "عالی و باثبات"
        stability_color = "text-emerald-400"
    elif profit_factor >= 1.2 and win_rate >= 40:
        stability_label = "بسیار خوب"
        stability_color = "text-emerald-400"
    elif profit_factor >= 1.0:
        stability_label = "نرمال / لب‌مرز"
        stability_color = "text-amber-400"
    else:
        stability_label = "نیاز به بازنگری ریسک"
        stability_color = "text-rose-400"

    # محاسبه امتیازات Radar (بر اساس ۵ معیار تخصصی)
    entry_score = min(100, int(win_rate * 1.5))
    exit_score = min(100, int(payoff_ratio * 35))
    risk_score = max(10, 100 - int(abs(float(max_drawdown)) / 50))
    stability_score = min(100, int((sharpe_ratio + 1) * 30))
    tempo_score = min(100, int(total_trades * 3))
    radar_score_overall = int((entry_score + exit_score + risk_score + stability_score + tempo_score) / 5)

    radar_data = [entry_score, risk_score, exit_score, stability_score, tempo_score]

    # بالانس اولیه پیش‌فرض جهت درصدها
    account = TelegramUser.objects.filter(is_blocked=False).first()
    if account:
        initial_balance = account.initial_balance
    else:
        initial_balance = Decimal('100.00')


    gain_percent = round((total_net_profit / float(initial_balance)) * 100, 2)

    # بازدهی بازه‌ها
    period_daily_return = round(total_net_profit / 30 / float(initial_balance) * 100, 2) if total_trades > 0 else 0.0
    period_weekly_return = round(total_net_profit / 4 / float(initial_balance) * 100, 2) if total_trades > 0 else 0.0
    period_monthly_return = gain_percent
    period_annual_return = round(gain_percent * 12, 2)

    days_names = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
    day_pnl_values = [float(round(day_pnl[i], 2)) for i in range(7)]

    symbols_list = list(symbol_pnl.keys())
    symbols_pnl_values = [float(round(symbol_pnl[s], 2)) for s in symbols_list]

    # محاسبه پویا و دقیق ارزیابی سطح ریسک حساب بر اساس Drawdown
    abs_dd = abs(float(max_drawdown))
    if abs_dd <= 5.0:
        risk_level_label = "پایین (ایمن)"
        risk_level_color = "text-emerald-400"
    elif abs_dd <= 15.0:
        risk_level_label = "متوسط (کنترل‌شده)"
        risk_level_color = "text-sky-400"
    elif abs_dd <= 25.0:
        risk_level_label = "بالا (هشدار)"
        risk_level_color = "text-amber-400"
    else:
        risk_level_label = "بسیار بالا (خطرناک)"
        risk_level_color = "text-rose-400"

    abs_gain_percent = abs(gain_percent)

    # تبدیل درصد به زاویه (۳۶۰ درجه) برای CSS conic-gradient
    # محدود کردن حداکثر زاویه به ۳۶۰ درجه
    gauge_angle = min(360, int((abs_gain_percent / 100) * 360)) if abs_gain_percent > 0 else 0
    # تبدیل درصد وین‌ریت به زاویه برای حلقه دایره‌ای (۳۶۰ درجه)
    win_rate_angle = int((win_rate / 100) * 360) if win_rate > 0 else 0
    # تبدیل Profit Factor به زاویه (فرض: حداکثر مطلوب ۳.۰ معادل دایره کامل ۳۶۰ درجه)
    pf_angle = min(360, int((float(profit_factor) / 3.0) * 360)) if profit_factor > 0 else 0
    # تبدیل حداکثر افت حساب به زاویه (فرض: حداکثر مقیاس بحرانی ۵۰٪ افت معادل ۳۶۰ درجه)
    # اگر خواستید حساس‌تر یا بازتر باشد می‌توانید عدد مخرج (50.0) را تغییر دهید
    dd_angle = min(360, int((abs(float(max_drawdown)) / 50.0) * 360)) if max_drawdown else 0

    # --- محاسبه پویای ضریب انضباط ذهنی (Mental Discipline Score) ---
    # فرمول ترکیبی بر اساس وین‌ریت، کنترل دراوداون و Payoff Ratio
    base_discipline = 50.0
    
    # تاثیر وین‌ریت (تا +۲۰ امتیاز)
    win_rate_bonus = min(20.0, (win_rate / 100.0) * 25.0)
    
    # تاثیر کنترل دراوداون (اگر دراوداون کمتر از ۱۰٪ باشد امتیاز کامل می‌گیرد)
    dd_val = abs(float(max_drawdown))
    dd_penalty = min(25.0, dd_val * 1.5)
    
    # تاثیر نسبت Payoff (اگر بالای ۱.۵ باشد نشان‌دهنده صبر و انضباط در خروج است)
    payoff_bonus = min(15.0, float(payoff_ratio) * 5.0) if payoff_ratio > 0 else 0.0

    mental_discipline_score = max(10, min(100, int(base_discipline + win_rate_bonus - dd_penalty + payoff_bonus)))
    
    # تعیین رنگ و برچسب وضعیت بر اساس امتیاز انضباط
    if mental_discipline_score >= 80:
        discipline_label = "فوق‌العاده و حرفه‌ای"
        discipline_color = "text-emerald-400"
    elif mental_discipline_score >= 60:
        discipline_label = "پایدار و کنترل‌شده"
        discipline_color = "text-cyan-400"
    elif mental_discipline_score >= 40:
        discipline_label = "نیاز به تمرکز بیشتر"
        discipline_color = "text-amber-400"
    else:
        discipline_label = "رفتار هیجانی / پرخطر"
        discipline_color = "text-rose-400"

    context = {
        'trades': list(processed_trades),
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
        'max_drawdown': float(round(max_drawdown, 2)),
        'total_commission': float(round(total_commission, 2)),
        'total_swap': float(round(total_swap, 2)),
        'streaks': streaks_data,
        'stability_label': stability_label,
        'stability_color': stability_color,
        'radar_score_overall': radar_score_overall,
        'radar_json': json.dumps(radar_data),
        'gain_percent': gain_percent,
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
        'gain_percent': gain_percent,
        'abs_gain_percent': abs_gain_percent,
        'gauge_angle': gauge_angle,
        'win_rate_angle': win_rate_angle,
        'pf_angle': pf_angle,
        'dd_angle': dd_angle,
        'mental_discipline_score': mental_discipline_score,
        'discipline_label': discipline_label,   
        'discipline_color': discipline_color,
    }

    if req_year and req_month:
        cal_data = get_monthly_calendar_data(req_year, req_month, initial_balance)
        context.update(cal_data)

    # دریافت پلن پیشرفت کاربر
    user_progression_plan = get_user_progression_plan(account) if account else {}
    context.update(user_progression_plan)
    
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