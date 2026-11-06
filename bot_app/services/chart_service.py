import io
import json
import logging
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import mplfinance as mpf
import matplotlib.gridspec as gridspec
from matplotlib.patches import Patch
from datetime import datetime

from decouple import config


logger = logging.getLogger(__name__)
CURENCY_UNIT=config("CURENCY_UNIT", default="$")
BG, GRID, TXT, TITLE = '#0D1117', '#30363D', '#8B949E', '#F0F6FC'
GREEN, RED, BLUE, AMBER, CYAN = '#23D160', '#FF3860', '#58A6FF', '#FFB020', '#22D3EE'


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
    ax_equity.set_ylabel(f"Profit / Loss ({CURENCY_UNIT})", fontsize=9, color='#8B949E')
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
    ax_pie.text(0, 0.1, f"NET PnL\n{p_sign}{CURENCY_UNIT}{net_profit:,.2f}", ha='center', va='center',
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
        ax_bar.annotate(f'{CURENCY_UNIT}{height:,.1f}',
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

def _style_ax(ax):
    ax.set_facecolor(BG)
    for s in ('top', 'right'):
        ax.spines[s].set_visible(False)
    for s in ('left', 'bottom'):
        ax.spines[s].set_color(GRID)
    ax.tick_params(colors=TXT, labelsize=8)


def _no_data(ax, title):
    ax.set_facecolor(BG)
    ax.set_title(title, fontsize=10, pad=8, weight='bold', color=TITLE)
    ax.text(0.5, 0.5, 'No Data', transform=ax.transAxes, ha='center', va='center',
            color=TXT, fontsize=10)
    ax.set_xticks([])
    ax.set_yticks([])
    for s in ax.spines.values():
        s.set_color(GRID)


def _to_buf(fig) -> io.BytesIO:
    buf = io.BytesIO()
    fig.savefig(buf, format='png', bbox_inches='tight', facecolor=fig.get_facecolor(), dpi=180)
    buf.seek(0)
    plt.close(fig)
    return buf

def _json(m: dict, key: str) -> list:
    """مقدار JSON-string داخل دیکشنری متریک‌ها را به لیست پایتون تبدیل می‌کند."""
    try:
        return json.loads(m.get(key) or '[]')
    except (TypeError, ValueError):
        return []
    
# ───────────────────────── داشبورد ۲: ریسک و انضباط ─────────────────────────
def generate_risk_discipline_dashboard(m: dict) -> io.BytesIO:
    plt.style.use('dark_background')
    fig = plt.figure(figsize=(10, 7), dpi=180)
    fig.patch.set_facecolor(BG)
    gs = gridspec.GridSpec(2, 2, height_ratios=[1.05, 1], hspace=0.4, wspace=0.3)

    # ---- درادان ----
    ax = fig.add_subplot(gs[0, :])
    dd = [-abs(float(v)) for v in _json(m, 'drawdown_json')]
    if len(dd) < 2:
        _no_data(ax, "Drawdown Curve")
    else:
        _style_ax(ax)
        x = list(range(len(dd)))
        ax.fill_between(x, dd, 0, color=RED, alpha=0.25)
        ax.plot(x, dd, color=RED, linewidth=2.2, marker='o', markersize=4, markerfacecolor='#FFFFFF')
        ax.axhline(0, color=GRID, linestyle='--', linewidth=1)
        low = min(dd)
        if low < 0:
            i = int(np.argmin(dd))
            ax.annotate(f"Max DD {low:.2f}%", xy=(i, low), xytext=(0, -14),
                        textcoords='offset points', ha='center',
                        color=RED, fontsize=9, weight='bold')
            ax.set_ylim(low * 1.4, abs(low) * 0.1)
        ax.set_xlabel("Trades Count", fontsize=9, color=TXT)
        ax.set_ylabel("Drawdown (%)", fontsize=9, color=TXT)
        ax.set_title(
            f"Daily Drawdown  |  Max {m.get('max_drawdown_pct', 0)}%  "
            f"({CURENCY_UNIT}{m.get('max_drawdown', 0):,.2f})",
            fontsize=11, pad=8, weight='bold', color=TITLE)

    # ---- گیج نظم ذهنی ----
    ax = fig.add_subplot(gs[1, 0])
    ax.set_facecolor(BG)
    score = max(0, min(100, int(m.get('mental_discipline_score', 0))))
    if score >= 80:
        color, label = GREEN, "Elite"
    elif score >= 60:
        color, label = CYAN, "Stable"
    elif score >= 40:
        color, label = AMBER, "Needs Focus"
    else:
        color, label = RED, "Emotional / High Risk"
    ax.pie([score, 100 - score, 100], startangle=180, counterclock=False,
           colors=[color, '#21262D', 'none'],
           wedgeprops=dict(width=0.35, edgecolor=BG, linewidth=2))
    ax.set_xlim(-1.15, 1.15)
    ax.set_ylim(-0.2, 1.15)
    ax.text(0, 0.2, f"{score}", ha='center', va='center', fontsize=28, weight='bold', color=color)
    ax.text(0, -0.02, f"/ 100  -  {label}", ha='center', va='center', fontsize=9, color=TXT)
    ax.set_title("Mental Discipline", fontsize=10, pad=8, weight='bold', color=TITLE)

    # ---- رادار ----
    radar = _json(m, 'radar_json')   # [entry, risk, exit, stability, tempo]
    if len(radar) == 5:
        ax = fig.add_subplot(gs[1, 1], projection='polar')
        ax.set_facecolor(BG)
        labels = ['Entry', 'Risk', 'Exit', 'Stability', 'Tempo']
        angles = np.linspace(0, 2 * np.pi, 5, endpoint=False).tolist()
        vals = [float(v) for v in radar]
        angles += angles[:1]
        vals += vals[:1]
        ax.plot(angles, vals, color=CYAN, linewidth=2)
        ax.fill(angles, vals, color=CYAN, alpha=0.25)
        ax.set_xticks(angles[:-1])
        ax.set_xticklabels(labels, color='#C9D1D9', fontsize=8.5)
        ax.set_ylim(0, 100)
        ax.set_yticks([25, 50, 75, 100])
        ax.set_yticklabels(['25', '50', '75', '100'], color=TXT, fontsize=6)
        ax.grid(color=GRID)
        ax.spines['polar'].set_color(GRID)
        ax.set_title(f"WaveScore Radar - {m.get('radar_score_overall', 0)}/100",
                     fontsize=10, pad=14, weight='bold', color=TITLE)
    else:
        _no_data(fig.add_subplot(gs[1, 1]), "WaveScore Radar")

    fig.suptitle(f"RISK & DISCIPLINE - {datetime.now().strftime('%Y-%m-%d')}",
                 fontsize=11, weight='bold', color=BLUE, y=0.98)
    return _to_buf(fig)


# ───────────────────────── داشبورد ۳: تفکیک عملکرد ─────────────────────────
def generate_breakdown_dashboard(m: dict) -> io.BytesIO:
    plt.style.use('dark_background')
    fig = plt.figure(figsize=(10, 7.5), dpi=180)
    fig.patch.set_facecolor(BG)
    gs = gridspec.GridSpec(2, 2, height_ratios=[1, 1], hspace=0.45, wspace=0.3)

    # ---- سود بر اساس روز هفته ----
    ax = fig.add_subplot(gs[0, 0])
    day_names = [d[:3] for d in _json(m, 'days_names_json')]
    day_vals = [float(v) for v in _json(m, 'day_pnl_json')]
    if not day_vals or not any(day_vals):
        _no_data(ax, "PnL by Weekday")
    else:
        _style_ax(ax)
        bars = ax.bar(day_names, day_vals, color=[GREEN if v >= 0 else RED for v in day_vals],
                      width=0.55, edgecolor=BG)
        ax.axhline(0, color=GRID, linewidth=1)
        for b, v in zip(bars, day_vals):
            if v != 0:
                ax.annotate(f"{v:,.1f}", xy=(b.get_x() + b.get_width() / 2, v),
                            xytext=(0, 3 if v > 0 else -10), textcoords='offset points',
                            ha='center', fontsize=7.5, weight='bold', color='#C9D1D9')
        ax.set_title("PnL by Weekday", fontsize=10, pad=8, weight='bold', color=TITLE)

    # ---- سود بر اساس نماد ----
    ax = fig.add_subplot(gs[0, 1])
    syms = _json(m, 'symbols_json')
    sym_vals = [float(v) for v in _json(m, 'symbols_pnl_json')]
    if not syms:
        _no_data(ax, "PnL by Symbol")
    else:
        _style_ax(ax)
        pairs = sorted(zip(syms, sym_vals), key=lambda p: abs(p[1]), reverse=True)[:10]
        pairs.sort(key=lambda p: p[1])
        names = [p[0] for p in pairs]
        vals = [p[1] for p in pairs]
        bars = ax.barh(names, vals, color=[GREEN if v >= 0 else RED for v in vals],
                       height=0.55, edgecolor=BG)
        ax.axvline(0, color=GRID, linewidth=1)
        for b, v in zip(bars, vals):
            ax.annotate(f"{v:,.1f}", xy=(v, b.get_y() + b.get_height() / 2),
                        xytext=(4 if v >= 0 else -4, 0), textcoords='offset points',
                        ha='left' if v >= 0 else 'right', va='center',
                        fontsize=7.5, weight='bold', color='#C9D1D9')
        ax.set_title("PnL by Symbol", fontsize=10, pad=8, weight='bold', color=TITLE)

    # ---- R:R برنامه‌ای در برابر واقعی ----
    ax = fig.add_subplot(gs[1, :])
    planned = [float(v) for v in _json(m, 'planned_rr_json')[1:]][-25:]
    realized = [float(v) for v in _json(m, 'realized_rr_json')[1:]][-25:]
    if not planned:
        _no_data(ax, "Planned R:R vs Realized R:R")
    else:
        _style_ax(ax)
        x = np.arange(1, len(planned) + 1)
        w = 0.38
        ax.bar(x - w / 2, planned, w, color=BLUE, edgecolor=BG)
        ax.bar(x + w / 2, realized, w,
               color=[GREEN if v >= 0 else RED for v in realized], edgecolor=BG)
        ax.axhline(0, color=GRID, linewidth=1)
        ax.set_xticks(x)
        ax.set_xlabel("Trade #", fontsize=9, color=TXT)
        ax.set_ylabel("R multiple", fontsize=9, color=TXT)
        ax.legend(handles=[Patch(color=BLUE, label='Planned'),
                           Patch(color=GREEN, label='Realized (+)'),
                           Patch(color=RED, label='Realized (-)')],
                  fontsize=8, facecolor=BG, edgecolor=GRID, labelcolor='#C9D1D9', loc='upper right')
        ax.set_title("Planned R:R vs Realized R:R", fontsize=10, pad=8, weight='bold', color=TITLE)

    fig.suptitle(f"PERFORMANCE BREAKDOWN - {datetime.now().strftime('%Y-%m-%d')}",
                 fontsize=11, weight='bold', color=BLUE, y=0.98)
    return _to_buf(fig)


def generate_extra_dashboards(m: dict) -> list[tuple[str, io.BytesIO]]:
    """(caption, buffer) برای هر تصویر. اگر یکی خطا داد بقیه ساخته می‌شوند."""
    out = []
    for caption, fn in (
        ("🛡 <b>ریسک و انضباط</b> — درادان، نظم ذهنی و رادار", generate_risk_discipline_dashboard),
        ("📊 <b>تفکیک عملکرد</b> — روز هفته، نماد و R:R", generate_breakdown_dashboard),
    ):
        try:
            out.append((caption, fn(m)))
        except Exception:
            logger.exception("Chart generation failed: %s", fn.__name__)
    return out

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