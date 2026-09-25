import io
import logging
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import mplfinance as mpf
import matplotlib.gridspec as gridspec

from datetime import datetime, date
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


def generate_pro_daily_dashboard(wins: int, losses: int, net_profit: float, win_rate: float,
                                 avg_win: float, avg_loss: float, profits_history: list) -> io.BytesIO:
    """رسم داشبورد ۳ قسمتی بدون ایموجی در متن‌های Matplotlib جهت جلوگیری از Warning و عدم رندر"""
    plt.style.use('dark_background')

    fig = plt.figure(figsize=(10, 7), dpi=180)
    fig.patch.set_facecolor('#0D1117')

    gs = gridspec.GridSpec(2, 2, height_ratios=[1.1, 1], width_ratios=[1, 1], hspace=0.35, wspace=0.25)

    ax_equity = fig.add_subplot(gs[0, :])
    ax_pie = fig.add_subplot(gs[1, 0])
    ax_bar = fig.add_subplot(gs[1, 1])

    for ax in [ax_equity, ax_pie, ax_bar]:
        ax.set_facecolor('#0D1117')

    # ------------ ۱. Equity Curve ------------
    cumulative_pnl = [0.0]
    for p in profits_history:
        cumulative_pnl.append(cumulative_pnl[-1] + p)

    x_points = list(range(len(cumulative_pnl)))
    line_color = '#23D160' if net_profit >= 0 else '#FF3860'

    ax_equity.plot(x_points, cumulative_pnl, color=line_color, linewidth=2.5, marker='o', markersize=5,
                   markerfacecolor='#FFFFFF')
    ax_equity.fill_between(x_points, cumulative_pnl, 0, color=line_color, alpha=0.15)
    ax_equity.axhline(0, color='#30363D', linestyle='--', linewidth=1)

    ax_equity.set_title("Equity Curve (Daily PnL Growth)", fontsize=11, pad=8, weight='bold', color='#F0F6FC')
    ax_equity.set_xlabel("Trades Count", fontsize=9, color='#8B949E')
    ax_equity.set_ylabel("Profit / Loss ($)", fontsize=9, color='#8B949E')
    ax_equity.tick_params(colors='#8B949E', labelsize=8)
    ax_equity.spines['top'].set_visible(False)
    ax_equity.spines['right'].set_visible(False)
    ax_equity.spines['left'].set_color('#30363D')
    ax_equity.spines['bottom'].set_color('#30363D')

    # ------------ ۲. Donut Chart ------------
    total_trades = wins + losses
    if total_trades == 0:
        sizes = [1]
        colors = ['#30363D']
        labels = ['No Trades']
    else:
        sizes = [wins, losses] if losses > 0 else [wins, 0.0001]
        colors = ['#23D160', '#FF3860']
        labels = [f'Win ({wins})', f'Loss ({losses})']

    wedges, texts, autotexts = ax_pie.pie(
        sizes,
        labels=labels,
        autopct='%1.0f%%' if total_trades > 0 else '',
        startangle=120,
        colors=colors,
        pctdistance=0.75,
        wedgeprops=dict(width=0.35, edgecolor='#0D1117', linewidth=2.5),
        textprops=dict(color='#C9D1D9', fontsize=9, weight='bold')
    )
    for at in autotexts:
        at.set_color('#FFFFFF')
        at.set_fontsize(10)

    p_color = '#23D160' if net_profit >= 0 else '#FF3860'
    p_sign = "+" if net_profit > 0 else ""
    ax_pie.text(0, 0.1, f"NET PnL\n{p_sign}${net_profit:,.2f}", ha='center', va='center',
                fontsize=11, weight='bold', color=p_color)
    ax_pie.text(0, -0.28, f"WinRate: {win_rate:.1f}%", ha='center', va='center',
                fontsize=8.5, weight='bold', color='#8B949E')
    ax_pie.set_title("Win / Loss Ratio", fontsize=10, pad=8, weight='bold', color='#F0F6FC')

    # ------------ ۳. Avg Win vs Avg Loss ------------
    categories = ['Avg Win', 'Avg Loss']
    values = [avg_win, avg_loss]
    bar_colors = ['#23D160', '#FF3860']

    bars = ax_bar.bar(categories, values, color=bar_colors, width=0.45, edgecolor='#0D1117', linewidth=1.5)

    for bar in bars:
        height = bar.get_height()
        ax_bar.annotate(f'${height:,.1f}',
                        xy=(bar.get_x() + bar.get_width() / 2, height),
                        xytext=(0, 3),
                        textcoords="offset points",
                        ha='center', va='bottom', fontsize=8.5, weight='bold', color='#C9D1D9')

    max_val = max(max(values) * 1.25, 10)
    ax_bar.set_ylim(0, max_val)
    ax_bar.spines['top'].set_visible(False)
    ax_bar.spines['right'].set_visible(False)
    ax_bar.spines['left'].set_color('#30363D')
    ax_bar.spines['bottom'].set_color('#30363D')
    ax_bar.tick_params(colors='#8B949E', labelsize=8.5)
    ax_bar.set_title("Average Win vs Loss", fontsize=10, pad=8, weight='bold', color='#F0F6FC')

    fig.suptitle(f"DAILY PERFORMANCE DASHBOARD - {datetime.now().strftime('%Y-%m-%d')}",
                 fontsize=11, weight='bold', color='#58A6FF', y=0.98)

    buf = io.BytesIO()
    plt.savefig(buf, format='png', bbox_inches='tight', facecolor=fig.get_facecolor(), dpi=180)
    buf.seek(0)
    plt.close(fig)
    return buf


def calculate_today_stats(closed_deals):
    today_date = date.today()
    today_deals = []

    for d in closed_deals:
        # دریافت سود معامله بدون ریسک KeyError
        profit = d.get("profit", 0.0) or 0.0

        # استخراج زمان معامله با انواع فرمت‌ها
        raw_time = d.get("close_time") or d.get("time") or d.get("date")
        deal_date = None

        if isinstance(raw_time, datetime):
            deal_date = raw_time.date()
        elif isinstance(raw_time, (int, float)):
            deal_date = datetime.fromtimestamp(raw_time).date()
        elif isinstance(raw_time, str):
            try:
                deal_date = datetime.strptime(raw_time.split()[0], "%Y.%m.%d").date()
            except ValueError:
                try:
                    deal_date = datetime.strptime(raw_time.split()[0], "%Y-%m-%d").date()
                except ValueError:
                    pass

        # اگر تاریخ یافت نشد، فرض می‌کنیم برای امروز است (برای جلوگیری از صفر شدن آمار)
        if deal_date == today_date or deal_date is None:
            today_deals.append({
                "profit": float(profit),
                "time": raw_time
            })

    total_trades = len(today_deals)
    if total_trades == 0:
        return 0, 0, 0, 0.0, 0.0, 0.0, 0.0, [0.0]

    wins = [d["profit"] for d in today_deals if d["profit"] > 0]
    losses = [d["profit"] for d in today_deals if d["profit"] < 0]
    profits_history = [d["profit"] for d in today_deals]

    win_count = len(wins)
    loss_count = len(losses)
    win_rate = (win_count / total_trades) * 100.0 if total_trades > 0 else 0.0
    net_profit = sum(profits_history)

    avg_win = (sum(wins) / win_count) if win_count > 0 else 0.0
    avg_loss = (abs(sum(losses)) / loss_count) if loss_count > 0 else 0.0

    return total_trades, win_count, loss_count, win_rate, net_profit, avg_win, avg_loss, profits_history