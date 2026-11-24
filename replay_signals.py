import os
import json
import django
import numpy as np
import pandas as pd

import plotly.graph_objects as go
from plotly.subplots import make_subplots

# 1. تنظیمات محیط جنگو
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
if not django.apps.apps.ready:
    django.setup()

# 2. ایمپورت‌ها (بدون TradeDecisionLog و notification_service)
from bot_app.models import MarketCandle
from bot_app.services.nds_strategy_service import rows_to_ohlc, NDSConfig
from nds_bot.structure import make_bars, raw_pivots, PivotTracker, group_by_confirm, detect_setup, htf_states


def _evaluate_setup_outcome(B, start_idx: int, direction: int, entry: float, sl: float, tp: float) -> dict:
    risk = abs(entry - sl)
    if risk <= 0:
        return {"status": "INVALID", "r": 0.0, "exit_bar": start_idx, "exit_time": B.t[start_idx], "exit_price": entry}

    reward_dist = abs(tp - entry)
    planned_rr = reward_dist / risk if risk > 0 else 0.0

    for idx in range(start_idx + 1, len(B.c)):
        high_i = float(B.h[idx])
        low_i = float(B.l[idx])

        if direction == 1:  # BUY
            hit_sl = low_i <= sl
            hit_tp = high_i >= tp
            if hit_sl:
                return {"status": "LOSS", "r": -1.0, "exit_bar": idx, "exit_time": B.t[idx], "exit_price": sl}
            if hit_tp:
                return {"status": "WIN", "r": planned_rr, "exit_bar": idx, "exit_time": B.t[idx], "exit_price": tp}

        elif direction == -1:  # SELL
            hit_sl = high_i >= sl
            hit_tp = low_i <= tp
            if hit_sl:
                return {"status": "LOSS", "r": -1.0, "exit_bar": idx, "exit_time": B.t[idx], "exit_price": sl}
            if hit_tp:
                return {"status": "WIN", "r": planned_rr, "exit_bar": idx, "exit_time": B.t[idx], "exit_price": tp}

    last_close = float(B.c[-1])
    floating_r = ((last_close - entry) * direction) / risk
    return {"status": "OPEN", "r": float(floating_r), "exit_bar": len(B.c) - 1, "exit_time": B.t[-1], "exit_price": last_close}


def _max_consecutive(bool_array: np.ndarray) -> int:
    """بیشترین تعداد تکرار متوالی True در آرایه بولین."""
    max_run = 0
    current = 0
    for val in bool_array:
        if val:
            current += 1
            if current > max_run:
                max_run = current
        else:
            current = 0
    return max_run


def compute_backtest_metrics(results: list[dict]) -> dict:
    """محاسبه شاخص‌های دقیق بک‌تست از لیست ستاپ‌های بررسی‌شده."""
    closed = [r for r in results if r["status"] in ("WIN", "LOSS")]
    open_trades = [r for r in results if r["status"] == "OPEN"]

    total_setups = len(results)
    closed_count = len(closed)

    if closed_count == 0:
        return {
            "total_setups": total_setups,
            "closed_trades": 0,
            "open_trades": len(open_trades),
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "loss_rate": 0.0,
            "profit_factor": 0.0,
            "expectancy_r": 0.0,
            "avg_r": 0.0,
            "avg_win_r": 0.0,
            "avg_loss_r": 0.0,
            "total_r": 0.0,
            "max_dd_r": 0.0,
            "max_win_streak": 0,
            "max_loss_streak": 0,
            "payoff_ratio": 0.0,
            "buy_count": sum(1 for r in results if r["direction"] == 1),
            "sell_count": sum(1 for r in results if r["direction"] == -1),
        }

    r_arr = np.array([float(x["r"]) for x in closed], dtype=float)
    wins_mask = r_arr > 0
    losses_mask = r_arr < 0

    wins = int(wins_mask.sum())
    losses = int(losses_mask.sum())
    win_rate = float(wins / closed_count)
    loss_rate = float(losses / closed_count)

    gross_profit_r = float(r_arr[wins_mask].sum()) if wins > 0 else 0.0
    gross_loss_r = float(abs(r_arr[losses_mask].sum())) if losses > 0 else 0.0

    profit_factor = float(gross_profit_r / gross_loss_r) if gross_loss_r > 0 else float("inf")
    avg_win_r = float(r_arr[wins_mask].mean()) if wins > 0 else 0.0
    avg_loss_r = float(abs(r_arr[losses_mask].mean())) if losses > 0 else 0.0
    payoff_ratio = float(avg_win_r / avg_loss_r) if avg_loss_r > 0 else float("inf")

    total_r = float(r_arr.sum())
    avg_r = float(r_arr.mean())
    expectancy_r = float((win_rate * avg_win_r) - (loss_rate * avg_loss_r))

    equity_r = np.cumsum(r_arr)
    peak_r = np.maximum.accumulate(equity_r)
    drawdown_r = peak_r - equity_r
    max_dd_r = float(drawdown_r.max()) if len(drawdown_r) > 0 else 0.0

    return {
        "total_setups": total_setups,
        "closed_trades": closed_count,
        "open_trades": len(open_trades),
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "loss_rate": loss_rate,
        "profit_factor": profit_factor,
        "expectancy_r": expectancy_r,
        "avg_r": avg_r,
        "avg_win_r": avg_win_r,
        "avg_loss_r": avg_loss_r,
        "total_r": total_r,
        "max_dd_r": max_dd_r,
        "max_win_streak": _max_consecutive(wins_mask),
        "max_loss_streak": _max_consecutive(losses_mask),
        "payoff_ratio": payoff_ratio,
        "buy_count": sum(1 for r in results if r["direction"] == 1),
        "sell_count": sum(1 for r in results if r["direction"] == -1),
    }


def print_backtest_report(metrics: dict):
    """چاپ منظم و کامل شاخص‌های بک‌تست."""
    pf_str = "∞" if np.isinf(metrics["profit_factor"]) else f"{metrics['profit_factor']:.2f}"
    pr_str = "∞" if np.isinf(metrics["payoff_ratio"]) else f"{metrics['payoff_ratio']:.2f}"

    print("\n" + "=" * 60)
    print("📊 گزارش کامل ستاپ‌ها و شاخص‌های بک‌تست (بدون ثبت در DB)")
    print("=" * 60)
    print(f"  کل ستاپ‌های پیداشده:     {metrics['total_setups']} (BUY: {metrics['buy_count']} | SELL: {metrics['sell_count']})")
    print(f"  معاملات بسته شده (TP/SL): {metrics['closed_trades']} | باز مانده: {metrics['open_trades']}")
    print(f"  تعداد برد / باخت:         {metrics['wins']} / {metrics['losses']}")
    print(f"  نرخ برد (Win Rate):       {metrics['win_rate'] * 100:.2f}%")
    print(f"  نرخ باخت (Loss Rate):      {metrics['loss_rate'] * 100:.2f}%")
    print(f"  فاکتور سود (PF):          {pf_str}")
    print(f"  نسبت برد به باخت (Payoff): {pr_str}")
    print(f"  میانگین R هر معامله:      {metrics['avg_r']:.3f} R")
    print(f"  میانگین برد / باخت:       +{metrics['avg_win_r']:.2f} R / -{metrics['avg_loss_r']:.2f} R")
    print(f"  انتظار ریاضی (Expectancy): {metrics['expectancy_r']:.3f} R")
    print(f"  مجموع سود/زیان (Total R):  {metrics['total_r']:+.2f} R")
    print(f"  حداکثر افت سرمایه (Max DD): {metrics['max_dd_r']:.2f} R")
    print(f"  بیشترین برد متوالی:       {metrics['max_win_streak']}")
    print(f"  بیشترین باخت متوالی:      {metrics['max_loss_streak']}")
    print("=" * 60)


def _build_htf_from_ltf(ohlc_ltf: pd.DataFrame, rule: str = "30min") -> pd.DataFrame:
    """ساخت کندل‌های تایم‌فریم بالا (M30) از روی دیتافریم LTF."""
    df = ohlc_ltf.sort_index()
    return df.resample(rule).agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna(subset=["open", "high", "low", "close"])

import os
import json
import math
import pandas as pd


def _normalize_ts_seconds(idx: pd.DatetimeIndex) -> list[int]:
    """تبدیل ایندکس زمانی به لیست ثانیه یونیکس بدون وابستگی به Timezone."""
    dt_idx = pd.DatetimeIndex(idx)
    if dt_idx.tz is not None:
        dt_idx = dt_idx.tz_convert("UTC").tz_localize(None)
    ns = dt_idx.as_unit("ns").asi8
    return (ns // 1_000_000_000).tolist()


def _df_to_candles(df_ohlc: pd.DataFrame) -> list[dict]:
    """تبدیل دیتافریم OHLC به لیست کندل‌های معتبر برای Lightweight Charts."""
    if df_ohlc is None or df_ohlc.empty:
        return []

    sub = df_ohlc[["open", "high", "low", "close"]].apply(pd.to_numeric, errors="coerce").dropna()
    sub = sub[~sub.index.isna()]
    sub = sub[~sub.index.duplicated(keep="last")].sort_index()
    if sub.empty:
        return []

    ts_list = _normalize_ts_seconds(sub.index)
    candles = []
    seen = set()

    for t, o, h, l, c in zip(ts_list, sub["open"], sub["high"], sub["low"], sub["close"]):
        t_int = int(t)
        if t_int in seen:
            continue
        vals = [float(o), float(h), float(l), float(c)]
        if any(math.isnan(v) or math.isinf(v) for v in vals):
            continue
        seen.add(t_int)
        candles.append({
            "time": t_int,
            "open": round(vals[0], 5),
            "high": round(vals[1], 5),
            "low": round(vals[2], 5),
            "close": round(vals[3], 5),
        })

    return candles


def generate_backtest_chart(
    ohlc_ltf: pd.DataFrame,
    setups_results: list[dict],
    ohlc_htf: pd.DataFrame | None = None,
    output_html: str = "backtest_chart.html",
    auto_open: bool = True,
):
    """
    چارت کندلی تعاملی شبیه TradingView با قابلیت سوییچ بین 1m و 30m:
    - جابه‌جایی سریع بین تایم‌فریم 1m و 30m از نوار ابزار بالای چارت
    - دکمه‌های Forward (سیگنال بعدی) و سیگنال قبلی + کلیدهای ArrowRight / ArrowLeft
    - قابلیت Expand / Zoom آزادانه با اسکرول موس، درگ محورها و دکمه‌های زوم
    """
    if not setups_results:
        print("⚠️ معامله‌ای برای رسم چارت وجود ندارد.")
        return

    df_1m = ohlc_ltf.copy()
    if not isinstance(df_1m.index, pd.DatetimeIndex):
        df_1m.index = pd.to_datetime(df_1m.index, errors="coerce")
    df_1m = df_1m[~df_1m.index.isna()].sort_index()

    candles_1m = _df_to_candles(df_1m)
    if not candles_1m:
        print("❌ کندل 1m معتبری برای رسم چارت پیدا نشد.")
        return

    if ohlc_htf is not None and len(ohlc_htf) > 0:
        df_30m = ohlc_htf.copy()
        if not isinstance(df_30m.index, pd.DatetimeIndex):
            df_30m.index = pd.to_datetime(df_30m.index, errors="coerce")
        df_30m = df_30m[~df_30m.index.isna()].sort_index()
    else:
        df_30m = (
            df_1m[["open", "high", "low", "close"]]
            .apply(pd.to_numeric, errors="coerce")
            .dropna()
            .resample("30min")
            .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
            .dropna()
        )

    candles_30m = _df_to_candles(df_30m)
    if not candles_30m:
        candles_30m = candles_1m

    min_1m, max_1m = candles_1m[0]["time"], candles_1m[-1]["time"]
    min_30m, max_30m = candles_30m[0]["time"], candles_30m[-1]["time"]

    markers_1m = []
    markers_30m = []
    trades_payload = []

    for idx, s in enumerate(setups_results):
        entry_ts = int(pd.Timestamp(s["time"]).tz_localize(None).timestamp())
        exit_ts = int(pd.Timestamp(s["exit_time"]).tz_localize(None).timestamp())
        entry_30m_ts = entry_ts - (entry_ts % 1800)
        exit_30m_ts = exit_ts - (exit_ts % 1800)

        is_buy = s["direction"] == 1
        is_win = s["status"] == "WIN"

        # مارکرهای 1m
        if min_1m <= entry_ts <= max_1m:
            markers_1m.append({
                "time": entry_ts,
                "position": "belowBar" if is_buy else "aboveBar",
                "color": "#00e676" if is_buy else "#ff1744",
                "shape": "arrowUp" if is_buy else "arrowDown",
                "text": f"#{idx + 1} {'BUY' if is_buy else 'SELL'}",
            })
        if s["status"] in ("WIN", "LOSS") and (min_1m <= exit_ts <= max_1m):
            markers_1m.append({
                "time": exit_ts,
                "position": "aboveBar" if is_buy else "belowBar",
                "color": "#00e676" if is_win else "#ff5252",
                "shape": "circle",
                "text": f"#{idx + 1} {s['status']} ({s['r']:+.2f}R)",
            })

        # مارکرهای 30m (گرد شده به ابتدای کندل 30 دقیقه‌ای)
        if min_30m <= entry_30m_ts <= max_30m:
            markers_30m.append({
                "time": entry_30m_ts,
                "position": "belowBar" if is_buy else "aboveBar",
                "color": "#00e676" if is_buy else "#ff1744",
                "shape": "arrowUp" if is_buy else "arrowDown",
                "text": f"#{idx + 1} {'BUY' if is_buy else 'SELL'}",
            })
        if s["status"] in ("WIN", "LOSS") and (min_30m <= exit_30m_ts <= max_30m):
            markers_30m.append({
                "time": exit_30m_ts,
                "position": "aboveBar" if is_buy else "belowBar",
                "color": "#00e676" if is_win else "#ff5252",
                "shape": "circle",
                "text": f"#{idx + 1} {s['status']} ({s['r']:+.2f}R)",
            })

        trades_payload.append({
            "id": idx + 1,
            "side": "BUY" if is_buy else "SELL",
            "status": s["status"],
            "r": round(float(s["r"]), 2),
            "entry_time": entry_ts,
            "exit_time": exit_ts,
            "entry_time_30m": entry_30m_ts,
            "exit_time_30m": exit_30m_ts,
            "entry_time_str": str(s["time"]),
            "exit_time_str": str(s["exit_time"]),
            "entry": round(float(s["entry"]), 5),
            "sl": round(float(s["sl"]), 5),
            "tp": round(float(s["tp"]), 5),
            "exit_price": round(float(s["exit_price"]), 5),
        })

    markers_1m.sort(key=lambda m: m["time"])
    markers_30m.sort(key=lambda m: m["time"])

    html_content = f"""<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="UTF-8" />
<title>NDS Backtest Chart (1m / 30m)</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: Tahoma, sans-serif; }}
  body {{ background: #131722; color: #d1d4dc; display: flex; flex-direction: column; height: 100vh; overflow: hidden; }}
  #toolbar {{
    background: #1e222d; border-bottom: 1px solid #2a2e39; padding: 8px 14px;
    display: flex; align-items: center; gap: 10px; flex-wrap: wrap; z-index: 10;
  }}
  button {{
    background: #2962ff; color: #fff; border: none; padding: 6px 12px;
    border-radius: 4px; cursor: pointer; font-size: 13px; font-weight: bold;
  }}
  button:hover {{ background: #1e53e5; }}
  button.secondary {{ background: #2a2e39; color: #d1d4dc; }}
  button.secondary:hover {{ background: #363a45; }}
  select {{
    background: #2a2e39; color: #fff; border: 1px solid #363a45;
    padding: 6px 10px; border-radius: 4px; font-size: 13px;
  }}
  #trade-select {{ min-width: 260px; }}
  #tf-select {{ min-width: 95px; font-weight: bold; }}
  #trade-info {{
    margin-right: auto; font-size: 13px; background: #131722;
    padding: 6px 12px; border-radius: 4px; border: 1px solid #2a2e39; direction: ltr;
  }}
  .win {{ color: #00e676; font-weight: bold; }}
  .loss {{ color: #ff5252; font-weight: bold; }}
  #chart-container {{ flex: 1; width: 100%; height: 100%; position: relative; }}
  #debug-err {{
    display: none; position: fixed; bottom: 10px; left: 10px; right: 10px;
    background: #4a151b; color: #ffb4b4; border: 1px solid #ff5252;
    padding: 10px; border-radius: 6px; font-size: 12px; direction: ltr; z-index: 99;
  }}
</style>
</head>
<body>

<div id="toolbar">
  <button class="secondary" onclick="prevTrade()">⏮ سیگنال قبلی</button>
  <button onclick="nextTrade()">سیگنال بعدی (Forward) ⏭</button>
  <select id="tf-select" onchange="switchTimeframe(this.value)" title="تغییر تایم‌فریم">
    <option value="1m" selected>1m</option>
    <option value="30m">30m</option>
  </select>
  <select id="trade-select" onchange="jumpToTrade(parseInt(this.value))"></select>
  <button class="secondary" onclick="expandView()">🔍 Expand / زوم بازتر</button>
  <button class="secondary" onclick="fitAll()">🌐 کل چارت</button>
  <div id="trade-info">در حال بارگذاری چارت...</div>
</div>

<div id="chart-container"></div>
<div id="debug-err"></div>

<script>
  const errBox = document.getElementById('debug-err');
  function showError(msg) {{
    errBox.style.display = 'block';
    errBox.innerText = msg;
  }}
  window.addEventListener('error', (e) => showError('JS Error: ' + e.message));

  const candlesByTf = {{
    "1m": {json.dumps(candles_1m)},
    "30m": {json.dumps(candles_30m)}
  }};
  const markersByTf = {{
    "1m": {json.dumps(markers_1m)},
    "30m": {json.dumps(markers_30m)}
  }};
  const trades = {json.dumps(trades_payload)};

  const cdnUrls = [
    "https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js",
    "https://cdn.jsdelivr.net/npm/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js"
  ];

  function loadScriptSequential(urls, idx = 0) {{
    if (idx >= urls.length) {{
      showError("بارگذاری کتابخانه Lightweight Charts از اینترنت ممکن نشد. اتصال اینترنت یا فایروال/پروکسی را بررسی کنید.");
      return;
    }}
    const s = document.createElement('script');
    s.src = urls[idx];
    s.onload = () => initChart();
    s.onerror = () => loadScriptSequential(urls, idx + 1);
    document.head.appendChild(s);
  }}

  let chart, candleSeries, activeLines = [], currentIdx = -1, currentTf = "1m";

  function applyTimeframeData(tf) {{
    currentTf = tf;
    candleSeries.setData(candlesByTf[tf] || []);
    if (typeof candleSeries.setMarkers === 'function') {{
      candleSeries.setMarkers(markersByTf[tf] || []);
    }}
  }}

  function initChart() {{
    if (typeof LightweightCharts === 'undefined') {{
      showError("LightweightCharts تعریف نشده است.");
      return;
    }}
    const container = document.getElementById('chart-container');
    chart = LightweightCharts.createChart(container, {{
      width: container.clientWidth || window.innerWidth,
      height: container.clientHeight || (window.innerHeight - 50),
      layout: {{ background: {{ color: '#131722' }}, textColor: '#d1d4dc' }},
      grid: {{ vertLines: {{ color: '#1f2943' }}, horzLines: {{ color: '#1f2943' }} }},
      crosshair: {{ mode: LightweightCharts.CrosshairMode.Normal }},
      rightPriceScale: {{ borderColor: '#2a2e39', autoScale: true }},
      timeScale: {{
        borderColor: '#2a2e39',
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 15,
        barSpacing: 8,
      }},
    }});

    candleSeries = typeof chart.addCandlestickSeries === 'function'
      ? chart.addCandlestickSeries({{
          upColor: '#26a69a',
          downColor: '#ef5350',
          borderVisible: false,
          wickUpColor: '#26a69a',
          wickDownColor: '#ef5350',
        }})
      : chart.addSeries(LightweightCharts.CandlestickSeries, {{
          upColor: '#26a69a',
          downColor: '#ef5350',
          wickUpColor: '#26a69a',
          wickDownColor: '#ef5350',
        }});

    applyTimeframeData(currentTf);

    const selectEl = document.getElementById('trade-select');
    trades.forEach((t, i) => {{
      const opt = document.createElement('option');
      opt.value = i;
      opt.text = `#${{t.id}} | ${{t.side}} | ${{t.status}} (${{t.r >= 0 ? '+' : ''}}${{t.r}}R) | ${{t.entry_time_str}}`;
      selectEl.appendChild(opt);
    }});

    if (trades.length > 0) jumpToTrade(0);
    else chart.timeScale().fitContent();
  }}

  function switchTimeframe(tf) {{
    if (!candleSeries || !candlesByTf[tf]) return;
    applyTimeframeData(tf);
    if (currentIdx >= 0) {{
      jumpToTrade(currentIdx);
    }} else {{
      chart.timeScale().fitContent();
    }}
  }}

  function clearPriceLines() {{
    activeLines.forEach(line => candleSeries.removePriceLine(line));
    activeLines = [];
  }}

  function jumpToTrade(idx) {{
    if (!candleSeries || idx < 0 || idx >= trades.length) return;
    currentIdx = idx;
    document.getElementById('trade-select').value = idx;
    const t = trades[idx];

    clearPriceLines();
    activeLines.push(candleSeries.createPriceLine({{
      price: t.entry, color: '#2962ff', lineWidth: 2, lineStyle: 0, axisLabelVisible: true, title: `ENTRY #${{t.id}}`
    }}));
    activeLines.push(candleSeries.createPriceLine({{
      price: t.sl, color: '#ff5252', lineWidth: 2, lineStyle: 2, axisLabelVisible: true, title: `SL #${{t.id}}`
    }}));
    activeLines.push(candleSeries.createPriceLine({{
      price: t.tp, color: '#00e676', lineWidth: 2, lineStyle: 2, axisLabelVisible: true, title: `TP #${{t.id}}`
    }}));

    const is30m = currentTf === "30m";
    const entryT = is30m ? t.entry_time_30m : t.entry_time;
    const exitT = is30m ? t.exit_time_30m : t.exit_time;
    const paddingBefore = is30m ? (25 * 1800) : (40 * 60);
    const paddingAfter = is30m ? (20 * 1800) : (30 * 60);
    const minSpan = is30m ? (30 * 1800) : (60 * 60);

    chart.timeScale().setVisibleRange({{
      from: entryT - paddingBefore,
      to: Math.max(exitT + paddingAfter, entryT + minSpan),
    }});

    const statusClass = t.status === 'WIN' ? 'win' : 'loss';
    document.getElementById('trade-info').innerHTML =
      `<b>[${{currentTf}}] #${{t.id}} ${{t.side}}</b> | <span class="${{statusClass}}">${{t.status}} (${{t.r >= 0 ? '+' : ''}}${{t.r}}R)</span> | ` +
      `Entry: <b>${{t.entry}}</b> | SL: <b>${{t.sl}}</b> | TP: <b>${{t.tp}}</b> | ` +
      `In: ${{t.entry_time_str}} → Out: ${{t.exit_time_str}}`;
  }}

  function nextTrade() {{
    if (!trades.length) return;
    jumpToTrade((currentIdx + 1) % trades.length);
  }}

  function prevTrade() {{
    if (!trades.length) return;
    jumpToTrade(currentIdx <= 0 ? trades.length - 1 : currentIdx - 1);
  }}

  function expandView() {{
    if (!chart) return;
    const range = chart.timeScale().getVisibleRange();
    if (!range) return;
    const span = range.to - range.from;
    chart.timeScale().setVisibleRange({{
      from: range.from - Math.floor(span * 0.5),
      to: range.to + Math.floor(span * 0.5),
    }});
  }}

  function fitAll() {{
    if (chart) chart.timeScale().fitContent();
  }}

  window.addEventListener('keydown', (e) => {{
    if (e.key === 'ArrowRight') nextTrade();
    if (e.key === 'ArrowLeft') prevTrade();
  }});

  window.addEventListener('resize', () => {{
    if (!chart) return;
    const container = document.getElementById('chart-container');
    chart.applyOptions({{
      width: container.clientWidth || window.innerWidth,
      height: container.clientHeight || (window.innerHeight - 50),
    }});
  }});

  loadScriptSequential(cdnUrls);
</script>
</body>
</html>
"""

    with open(output_html, "w", encoding="utf-8") as f:
        f.write(html_content)

    print(f"📈 چارت تعاملی (1m / 30m) ساخته شد: {os.path.abspath(output_html)}")

    if auto_open:
        import webbrowser
        webbrowser.open(f"file://{os.path.abspath(output_html)}")
        
def run_replay(
    ohlc_ltf: pd.DataFrame | None = None,
    ohlc_htf: pd.DataFrame | None = None,
    symbol: str = "XAUUSD_l",
    start_bar: int = 0,
):
    if ohlc_ltf is None or ohlc_htf is None:
        print("⏳ در حال خواندن آخرین داده‌ها از دیتابیس...")

        rows_ltf = list(
            MarketCandle.objects.filter(symbol=symbol, timeframe="M1")
            .order_by('-open_time')
            .values('open_price', 'high_price', 'low_price', 'close_price', 'volume', 'open_time')[:1500]
        )
        rows_htf = list(
            MarketCandle.objects.filter(symbol=symbol, timeframe="M30")
            .order_by('-open_time')
            .values('open_price', 'high_price', 'low_price', 'close_price', 'volume', 'open_time')[:500]
        )

        if not rows_ltf or not rows_htf:
            print("❌ داده‌ای در دیتابیس یافت نشد.")
            return []

        ohlc_ltf = rows_to_ohlc(rows_ltf)
        ohlc_htf = rows_to_ohlc(rows_htf)
        print(f"📊 آخرین کندل M1 در دیتابیس: {ohlc_ltf.index[-1]}")
        print(f"📊 آخرین کندل M30 در دیتابیس: {ohlc_htf.index[-1]}")
    else:
        print("📂 استفاده از داده‌های CSV برای بررسی تمام ستاپ‌ها...")
        ohlc_ltf = ohlc_ltf.sort_index()
        ohlc_htf = ohlc_htf.sort_index()
        print(f"📊 بازه کندل M1 در CSV: {ohlc_ltf.index[0]} → {ohlc_ltf.index[-1]} ({len(ohlc_ltf)} کندل)")
        print(f"📊 بازه کندل M30 در CSV: {ohlc_htf.index[0]} → {ohlc_htf.index[-1]} ({len(ohlc_htf)} کندل)")

    cfg = NDSConfig(ltf="M1", htf="M30")
    B = make_bars(ohlc_ltf, cfg)
    H = make_bars(ohlc_htf, cfg)

    # محاسبه وضعیت HTF در طول کل سری زمانی
    htf_info_list = htf_states(H, cfg, only_last=False)
    if not htf_info_list:
        print("❌ اطلاعات تایم‌فریم بالا (HTF) در دسترس نیست.")
        return []

    by_conf = group_by_confirm(raw_pivots(B.h, B.l, cfg.pivot_ltf))
    tr = PivotTracker()
    test_cfg = NDSConfig(ltf="M1", htf="M30", signal_max_age=5)

    seen_keys = set()
    setups_results = []

    # پیمایش کل کندل‌ها بدون خروج زودهنگام با break
    for c in range(max(0, start_bar), len(B.c)):
        for p in by_conf.get(c, []):
            tr.add(p)

        # نگاشت زمان کندل LTF به آخرین کندل بسته‌شده HTF
        bar_time = B.t[c]
        htf_idx = int(np.searchsorted(H.t, bar_time, side="right")) - 1
        if htf_idx < 0:
            continue
        htf_idx = min(htf_idx, len(htf_info_list) - 1)
        info = htf_info_list[htf_idx]

        if getattr(info, "bias", 0) == 0:
            continue

        s = detect_setup(tr.p, B, c, info, test_cfg, seen_keys)
        if s:
            seen_keys.add(s.key)
            outcome = _evaluate_setup_outcome(
                B=B,
                start_idx=c,
                direction=s.direction,
                entry=float(s.entry),
                sl=float(s.sl),
                tp=float(s.tp),
            )

            record = {
                "bar_index": c,
                "time": B.t[c],
                "direction": s.direction,
                "side": "SELL" if s.direction == -1 else "BUY",
                "entry": float(s.entry),
                "sl": float(s.sl),
                "tp": float(s.tp),
                "status": outcome["status"],
                "r": outcome["r"],
                "key": s.key,
                "exit_time": outcome["exit_time"],
                "exit_price": outcome["exit_price"],
            }
            setups_results.append(record)

            print(
                f"#{len(setups_results)} | {record['time']} | {record['side']} | "
                f"ورود: {record['entry']} | SL: {record['sl']} | TP: {record['tp']} | "
                f"نتیجه: {record['status']} ({record['r']:+.2f}R)"
            )

    if not setups_results:
        print("❌ هیچ ستاپی در بازه داده‌ها یافت نشد.")
        return []

    metrics = compute_backtest_metrics(setups_results)
    print_backtest_report(metrics)
    generate_backtest_chart(ohlc_ltf, setups_results, ohlc_htf=ohlc_htf, output_html="backtest_chart.html")
    return setups_results



if __name__ == "__main__":
    run_replay()