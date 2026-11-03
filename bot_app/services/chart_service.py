import io
import logging
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import mplfinance as mpf
import matplotlib.gridspec as gridspec

from decouple import config


logger = logging.getLogger(__name__)
CURENCY_UNIT=config("CURENCY_UNIT", default="$")


def calculate_heikin_ashi(df_ohlc: pd.DataFrame) -> pd.DataFrame:
    """
    محاسبه بهینه کندل‌های Heikin-Ashi با استفاده از الگوریتم برداری
    """
    ha_df = pd.DataFrame(index=df_ohlc.index)

    # 1. Close
    ha_df['Close'] = (df_ohlc['Open'] + df_ohlc['High'] + df_ohlc['Low'] + df_ohlc['Close']) / 4

    # 2. Open
    ha_open = np.zeros(len(df_ohlc))
    ha_open[0] = (df_ohlc['Open'].iloc[0] + df_ohlc['Close'].iloc[0]) / 2

    ha_close_vals = ha_df['Close'].values
    for i in range(1, len(df_ohlc)):
        ha_open[i] = (ha_open[i - 1] + ha_close_vals[i - 1]) / 2

    ha_df['Open'] = ha_open

    # 3. High & Low (سریع‌تر و بدون join)
    ha_df['High'] = np.maximum.reduce([df_ohlc['High'].values, ha_df['Open'].values, ha_df['Close'].values])
    ha_df['Low'] = np.minimum.reduce([df_ohlc['Low'].values, ha_df['Open'].values, ha_df['Close'].values])

    return ha_df


def _get_base_style() -> dict:
    """تم پایه مشترک برای تمام چارت‌ها"""
    return mpf.make_mpf_style(
        base_mpf_style='charles',
        gridcolor='#2f3542',
        facecolor='#1e1e2e',
        figcolor='#1e1e2e',
        y_on_right=True,
        rc={
            'text.color': '#ffffff',
            'axes.labelcolor': '#ffffff',
            'axes.edgecolor': '#4a4d6d',
            'xtick.color': '#ffffff',
            'ytick.color': '#ffffff',
            'figure.titlesize': 'x-large'
        }
    )


def _render_chart(ha_df: pd.DataFrame, title_text: str, title_color: str, line_color: str, line_style: str,
                  target_price: float) -> io.BytesIO:
    """
    تابع داخلی جهت رسم و ذخیره‌سازی چارت در حافظه با مدیریت کامل RAM
    """
    style = _get_base_style()
    hlines_config = dict(
        hlines=[target_price],
        colors=[line_color],
        linestyle=line_style,
        linewidths=1.5
    )

    buf = io.BytesIO()

    # mpf.plot شیء figure را برمی‌گرداند تا بتوانیم آن را صریحاً بست و RAM را آزاد کرد
    fig, _ = mpf.plot(
        ha_df,
        type='candle',
        style=style,
        title=dict(title=title_text, color=title_color, fontsize=12),
        hlines=hlines_config,
        savefig=dict(fname=buf, dpi=150, bbox_inches='tight'),
        volume=False,
        returnfig=True
    )

    # 🔴 بسیار مهم: پاکسازی حافظه جهت جلوگیری از Memory Leak
    plt.close(fig)

    buf.seek(0)
    return buf



def create_heikin_ashi_chart(df_ohlc: pd.DataFrame, target_price: float, symbol: str) -> io.BytesIO:
    """رسم چارت برای آلرت فعال‌شده (خط قرمز)"""
    ha_df = calculate_heikin_ashi(df_ohlc)
    title_text = f"ALERT TRIGGERED: {symbol} @ {target_price}"
    return _render_chart(ha_df, title_text, '#ffffff', '#ff4757', '--', target_price)


def create_pending_alert_chart(df_ohlc: pd.DataFrame, target_price: float, symbol: str) -> io.BytesIO:
    """رسم چارت برای آلرت در انتظار (خط فیروزه‌ای)"""
    ha_df = calculate_heikin_ashi(df_ohlc)
    title_text = f"NEW ALERT CREATED: {symbol} @ {target_price}"
    return _render_chart(ha_df, title_text, '#00d2d3', '#00d2d3', ':', target_price)


def create_journal_heikin_ashi_chart(
        df_ohlc: pd.DataFrame,
        symbol: str,
        entry_price: float = 0.0,
        sl_price: float = 0.0,
        tp_price: float = 0.0
) -> io.BytesIO:
    ha_df = calculate_heikin_ashi(df_ohlc)

    plt.style.use('dark_background')
    fig, ax = plt.subplots(figsize=(12, 6), dpi=150)
    fig.patch.set_facecolor('#0d1117') # پس‌زمینه بسیار تیره نئونی
    ax.set_facecolor('#0d1117')

    up = ha_df[ha_df['Close'] >= ha_df['Open']]
    down = ha_df[ha_df['Close'] < ha_df['Open']]

    # پالت رنگی کندل‌های نئونی
    col_up = '#00ffaa'    # سبز نئونی
    col_down = '#ff2a6d'  # قرمز/صورتی نئونی

    # سایه‌ها و بدنه کندل‌ها
    ax.vlines(ha_df.index, ha_df['Low'], ha_df['High'], color='#6272a4', linewidth=0.8, alpha=0.5)
    ax.vlines(up.index, up['Open'], up['Close'], color=col_up, linewidth=2.8)
    ax.vlines(down.index, down['Open'], down['Close'], color=col_down, linewidth=2.8)

    # تابع کمکی برای رسم خطوط صاف با افکت درخشش نئونی (Glow)
    def draw_neon_line(y_val, color, label_text):
        # خط پهن پس‌زمینه جهت ایجاد درخشش (Glow Effect)
        ax.axhline(y=y_val, color=color, linestyle='-', linewidth=4.0, alpha=0.25)
        # خط اصلی صاف
        ax.axhline(y=y_val, color=color, linestyle='-', linewidth=1.6, alpha=0.95, label=label_text)

    # ترسیم خطوط قیمت (صاف و نئونی)
    if entry_price and float(entry_price) > 0:
        draw_neon_line(float(entry_price), '#00d2d3', f'Entry: {entry_price}') # آبی نئونی استاندارد

    if sl_price and float(sl_price) > 0:
        draw_neon_line(float(sl_price), '#ff0055', f'SL: {sl_price}') # قرمز/صورتی نئونی

    if tp_price and float(tp_price) > 0:
        draw_neon_line(float(tp_price), '#00ff66', f'TP: {tp_price}') # سبز فسفری نئونی

    # تنظیمات ظاهری چارت
    ax.set_title(f"AUTOMATED JOURNAL CHART: {symbol} (400 HA Candles)", fontsize=11, color='#f8f8f2', pad=12, fontweight='bold')

    handles, labels = ax.get_legend_handles_labels()
    if handles:
        ax.legend(handles, labels, loc='upper left', facecolor='#161b22', edgecolor='#30363d', labelcolor='#f8f8f2', framealpha=0.85)

    ax.grid(True, color='#21262d', linestyle='-', linewidth=0.7, alpha=0.6)

    fig.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format='png', bbox_inches='tight', facecolor=fig.get_facecolor(), edgecolor='none')
    plt.close(fig)
    buf.seek(0)
    return buf