import io
import logging
import pandas as pd
import numpy as np
import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt
import mplfinance as mpf

# خاموش کردن لوگ‌های غیرضروری فونت مت‌پلات‌لیب
logging.getLogger('matplotlib.font_manager').setLevel(logging.ERROR)


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