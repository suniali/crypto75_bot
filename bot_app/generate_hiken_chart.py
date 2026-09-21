import io
import mplfinance as mpf
import pandas as pd


def create_heikin_ashi_chart(df_ohlc: pd.DataFrame, target_price: float, symbol: str) -> io.BytesIO:
    """
    دریافت دیتافریم قیمت (شامل Open, High, Low, Close با Index زمانی)
    تبدیل به Heikin Ashi و رسم نمودار همراه با خط افقی target_price
    """
    # ۱. محاسبه کندل‌های Heikin Ashi
    ha_df = pd.DataFrame(index=df_ohlc.index)

    ha_df['Close'] = (df_ohlc['Open'] + df_ohlc['High'] + df_ohlc['Low'] + df_ohlc['Close']) / 4

    # کندل اول هیکن آشی
    ha_open = [(df_ohlc['Open'].iloc[0] + df_ohlc['Close'].iloc[0]) / 2]
    for i in range(1, len(df_ohlc)):
        ha_open.append((ha_open[i - 1] + ha_df['Close'].iloc[i - 1]) / 2)

    ha_df['Open'] = ha_open
    ha_df['High'] = df_ohlc[['High']].join(ha_df[['Open', 'Close']]).max(axis=1)
    ha_df['Low'] = df_ohlc[['Low']].join(ha_df[['Open', 'Close']]).min(axis=1)

    # ۲. تنظیمات استایل چارت با فونت و اعدادی کاملاً روشن (سفید)
    style = mpf.make_mpf_style(
        base_mpf_style='charles',
        gridcolor='#2f3542',
        facecolor='#1e1e2e',  # پس‌زمینه چارت
        figcolor='#1e1e2e',   # پس‌زمینه اصلی
        y_on_right=True,
        rc={
            'text.color': '#ffffff',         # رنگ تمام متن‌ها
            'axes.labelcolor': '#ffffff',    # رنگ لیبل محورها
            'axes.edgecolor': '#4a4d6d',     # رنگ کادر دور چارت
            'xtick.color': '#ffffff',        # رنگ تاریخ‌ها در محور X
            'ytick.color': '#ffffff',        # رنگ قیمت‌ها در محور Y
            'figure.titlesize': 'x-large'
        }
    )

    # ۳. تعریف خط افقی قیمت آلرت همراه با نشانگر متن روی محور قیمت
    hlines_config = dict(
        hlines=[target_price],
        colors=['#ff4757'],  # رنگ قرمز برجسته برای خط آلرت
        linestyle='--',
        linewidths=1.5
    )

    # ۴. رسم چارت در حافظه (BytesIO)
    buf = io.BytesIO()

    title_text = f"🚨 ALERT TRIGGERED: {symbol} @ {target_price}"

    mpf.plot(
        ha_df,
        type='candle',
        style=style,
        title=dict(title=title_text, color='#ffffff', size=12),
        hlines=hlines_config,
        savefig=dict(fname=buf, dpi=150, bbox_inches='tight'),
        volume=False
    )

    buf.seek(0)
    return buf

def create_pending_alert_chart(df_ohlc: pd.DataFrame, target_price: float, symbol: str) -> io.BytesIO:
    """
    رسم چارت هیکن آشی برای آلرت جدید در انتظار (Pending Alert)
    با رنگ خط متمایز (آبی/فیروزه‌ای) و عنوان جدید
    """
    # ۱. محاسبه کندل‌های Heikin Ashi
    ha_df = pd.DataFrame(index=df_ohlc.index)
    ha_df['Close'] = (df_ohlc['Open'] + df_ohlc['High'] + df_ohlc['Low'] + df_ohlc['Close']) / 4

    ha_open = [(df_ohlc['Open'].iloc[0] + df_ohlc['Close'].iloc[0]) / 2]
    for i in range(1, len(df_ohlc)):
        ha_open.append((ha_open[i - 1] + ha_df['Close'].iloc[i - 1]) / 2)

    ha_df['Open'] = ha_open
    ha_df['High'] = df_ohlc[['High']].join(ha_df[['Open', 'Close']]).max(axis=1)
    ha_df['Low'] = df_ohlc[['Low']].join(ha_df[['Open', 'Close']]).min(axis=1)

    # ۲. استایل تاریک با اعداد و متون کاملاً روشن
    style = mpf.make_mpf_style(
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
        }
    )

    # ۳. خط افقی آبی/فیروزه‌ای با استایل dotted برای آلرت تازه (غیرفعال)
    hlines_config = dict(
        hlines=[target_price],
        colors=['#00d2d3'],  # رنگ فیروزه‌ای متمایز
        linestyle=':',       # خط نقطه چین برای تمایز از آلرت فعال شده
        linewidths=1.8
    )

    buf = io.BytesIO()
    title_text = f"NEW ALERT CREATED: {symbol} @ {target_price}"

    mpf.plot(
        ha_df,
        type='candle',
        style=style,
        title=dict(title=title_text, color='#00d2d3', size=12),
        hlines=hlines_config,
        savefig=dict(fname=buf, dpi=150, bbox_inches='tight'),
        volume=False
    )

    buf.seek(0)
    return buf