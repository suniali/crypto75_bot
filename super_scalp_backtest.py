from __future__ import annotations

import copy
import json
import math
import os
import webbrowser
from typing import Any

import numpy as np
import pandas as pd

from nds_bot.super_scalp_engine import (
    SuperScalpConfig,
    build_heikin_ashi,
    compute_m30_bias,
    compute_simple_risk_lot,
    detect_super_scalp_setup,
)
from nds_bot.dva_ar_risk import DVAARConfig, compute_dva_scalp_position,compute_dva_stop_loss


def load_ohlc_from_csv(csv_path: str) -> pd.DataFrame:
    """خواندن فایل CSV کندل‌ها و استانداردسازی ستون‌های OHLC و ایندکس زمانی."""
    df = pd.read_csv(csv_path)
    col_map = {c.lower().strip(): c for c in df.columns}

    time_col = next((col_map[k] for k in ("time", "open_time", "timestamp", "datetime", "date") if k in col_map), None)
    o_col = next((col_map[k] for k in ("open", "open_price") if k in col_map), None)
    h_col = next((col_map[k] for k in ("high", "high_price") if k in col_map), None)
    l_col = next((col_map[k] for k in ("low", "low_price") if k in col_map), None)
    c_col = next((col_map[k] for k in ("close", "close_price") if k in col_map), None)

    if not all([time_col, o_col, h_col, l_col, c_col]):
        raise ValueError("ستون‌های زمان و OHLC در فایل CSV یافت نشدند.")

    out = pd.DataFrame(
        {
            "open": df[o_col].astype(float),
            "high": df[h_col].astype(float),
            "low": df[l_col].astype(float),
            "close": df[c_col].astype(float),
        }
    )
    out.index = pd.to_datetime(df[time_col])
    out = out.sort_index()
    out = out[~out.index.duplicated(keep="last")]
    return out


def simulate_scalp_trade_with_be(
    df: pd.DataFrame,
    entry_idx: int,
    direction: int,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    max_bars: int = 60,
    breakeven_r: float | None = None,
) -> dict:
    """شبیه‌سازی معامله اسکالپ با امکان انتقال حد ضرر به نقطه ورود (Break-Even)."""
    risk_dist = abs(entry_price - stop_loss)
    if risk_dist <= 1e-9:
        return {
            "outcome": "sl",
            "r": -1.0,
            "bars_held": 1,
            "exit_idx": min(len(df) - 1, entry_idx + 1),
            "exit_price": stop_loss,
            "be_moved": False,
        }

    active_sl = float(stop_loss)
    be_moved = False
    be_trigger_dist = (float(breakeven_r) * risk_dist) if (breakeven_r is not None and float(breakeven_r) > 0) else None

    end_idx = min(len(df) - 1, entry_idx + max_bars)
    highs = df["high"].astype(float).to_numpy()
    lows = df["low"].astype(float).to_numpy()
    closes = df["close"].astype(float).to_numpy()

    for idx in range(entry_idx + 1, end_idx + 1):
        h = highs[idx]
        l = lows[idx]

        if direction == 1:  # BUY
            if l <= active_sl:
                if be_moved and abs(active_sl - entry_price) <= 1e-9:
                    return {
                        "outcome": "be",
                        "r": 0.0,
                        "bars_held": idx - entry_idx,
                        "exit_idx": idx,
                        "exit_price": entry_price,
                        "be_moved": True,
                    }
                return {
                    "outcome": "sl",
                    "r": -1.0,
                    "bars_held": idx - entry_idx,
                    "exit_idx": idx,
                    "exit_price": active_sl,
                    "be_moved": be_moved,
                }

            if h >= take_profit:
                tp_r = abs(take_profit - entry_price) / risk_dist
                return {
                    "outcome": "tp",
                    "r": round(tp_r, 4),
                    "bars_held": idx - entry_idx,
                    "exit_idx": idx,
                    "exit_price": float(take_profit),
                    "be_moved": be_moved,
                }

            if be_trigger_dist is not None and not be_moved:
                if (h - entry_price) >= be_trigger_dist:
                    active_sl = float(entry_price)
                    be_moved = True

        else:  # SELL
            if h >= active_sl:
                if be_moved and abs(active_sl - entry_price) <= 1e-9:
                    return {
                        "outcome": "be",
                        "r": 0.0,
                        "bars_held": idx - entry_idx,
                        "exit_idx": idx,
                        "exit_price": entry_price,
                        "be_moved": True,
                    }
                return {
                    "outcome": "sl",
                    "r": -1.0,
                    "bars_held": idx - entry_idx,
                    "exit_idx": idx,
                    "exit_price": active_sl,
                    "be_moved": be_moved,
                }

            if l <= take_profit:
                tp_r = abs(entry_price - take_profit) / risk_dist
                return {
                    "outcome": "tp",
                    "r": round(tp_r, 4),
                    "bars_held": idx - entry_idx,
                    "exit_idx": idx,
                    "exit_price": float(take_profit),
                    "be_moved": be_moved,
                }

            if be_trigger_dist is not None and not be_moved:
                if (entry_price - l) >= be_trigger_dist:
                    active_sl = float(entry_price)
                    be_moved = True

    last_close = float(closes[end_idx])
    raw_r = ((last_close - entry_price) / risk_dist) if direction == 1 else ((entry_price - last_close) / risk_dist)
    return {
        "outcome": "timeout",
        "r": round(raw_r, 4),
        "bars_held": max(1, end_idx - entry_idx),
        "exit_idx": end_idx,
        "exit_price": last_close,
        "be_moved": be_moved,
    }


def _scalp_ts_seconds(idx: pd.DatetimeIndex) -> list[int]:
    dt_idx = pd.DatetimeIndex(idx)
    if dt_idx.tz is not None:
        dt_idx = dt_idx.tz_convert("UTC").tz_localize(None)
    ns = dt_idx.as_unit("ns").asi8
    return (ns // 1_000_000_000).tolist()


def _scalp_df_to_candles(df_ohlc: pd.DataFrame) -> list[dict]:
    if df_ohlc is None or df_ohlc.empty:
        return []

    sub = df_ohlc[["open", "high", "low", "close"]].apply(pd.to_numeric, errors="coerce").dropna()
    sub = sub[~sub.index.isna()]
    sub = sub[~sub.index.duplicated(keep="last")].sort_index()
    if sub.empty:
        return []

    ts_list = _scalp_ts_seconds(sub.index)
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


def _to_unix_sec(ts_val) -> int:
    ts = pd.Timestamp(ts_val)
    if ts.tzinfo is not None:
        ts = ts.tz_convert("UTC").tz_localize(None)
    return int(ts.timestamp())


def generate_scalp_backtest_chart(
    ohlc_ltf: pd.DataFrame,
    trades_results: list[dict],
    ohlc_htf: pd.DataFrame | None = None,
    output_html: str = "scalp_backtest_chart.html",
    auto_open: bool = True,
    breakeven_r: float | None = None,
):
    """ساخت چارت تعاملی HTML برای نتایج بک‌تست Super-Scalping (1m / 30m)."""
    if not trades_results:
        print("⚠️ معامله‌ای برای رسم چارت Super-Scalp وجود ندارد.")
        return

    df_1m = ohlc_ltf.copy()
    if not isinstance(df_1m.index, pd.DatetimeIndex):
        df_1m.index = pd.to_datetime(df_1m.index, errors="coerce")
    df_1m = df_1m[~df_1m.index.isna()].sort_index()

    candles_1m = _scalp_df_to_candles(df_1m)
    if not candles_1m:
        print("❌ کندل 1m معتبری برای رسم چارت اسکالپ پیدا نشد.")
        return

    if ohlc_htf is not None and len(ohlc_htf) > 0:
        df_30m = ohlc_htf.copy()
        if not isinstance(df_30m.index, pd.DatetimeIndex):
            df_30m.index = pd.to_datetime(df_30m.index, errors="coerce")
        df_30m = df_30m[~df_30m.index.isna()].sort_index()
    else:
        df_30m = (
            df_1m.resample("30min")
            .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
            .dropna()
        )

    candles_30m = _scalp_df_to_candles(df_30m)
    if not candles_30m:
        candles_30m = candles_1m

    min_1m, max_1m = candles_1m[0]["time"], candles_1m[-1]["time"]
    min_30m, max_30m = candles_30m[0]["time"], candles_30m[-1]["time"]

    markers_1m = []
    markers_30m = []
    trades_payload = []

    for idx, t in enumerate(trades_results):
        raw_entry_time = t.get("entry_time") or t.get("time")
        raw_exit_time = t.get("exit_time") or raw_entry_time
        if raw_entry_time is None:
            continue

        entry_ts = _to_unix_sec(raw_entry_time)
        exit_ts = _to_unix_sec(raw_exit_time)
        entry_30m_ts = entry_ts - (entry_ts % 1800)
        exit_30m_ts = exit_ts - (exit_ts % 1800)

        side_str = str(t.get("side") or "").upper()
        direction = t.get("direction")
        is_buy = (side_str == "BUY") or (direction in (1, "1", "BUY", "LONG"))

        r_val = float(t.get("r", 0.0) or 0.0)
        is_win = r_val > 0

        entry_px = float(t.get("entry_price") if t.get("entry_price") is not None else t.get("entry", 0.0))
        sl_px = float(t.get("stop_loss") if t.get("stop_loss") is not None else t.get("sl", 0.0))
        tp_px = float(
            t.get("take_profit")
            if t.get("take_profit") is not None
            else (t.get("tp") if t.get("tp") is not None else (t.get("tp_final") or entry_px))
        )
        exit_px = float(t.get("exit_price") if t.get("exit_price") is not None else tp_px)
        status_str = str(t.get("status") or t.get("outcome") or ("WIN" if is_win else "LOSS")).upper()

        ai_prob_raw = t.get("ai_prob")
        ai_pct = (
            f"{float(ai_prob_raw) * 100:.0f}%"
            if ai_prob_raw is not None and float(ai_prob_raw) <= 1
            else (f"{float(ai_prob_raw):.0f}%" if ai_prob_raw is not None else "-")
        )
        chk_score = t.get("checklist_score", "-")
        dva_risk_pct = t.get("dva_risk_pct", "-")

        if min_1m <= entry_ts <= max_1m:
            markers_1m.append({
                "time": entry_ts,
                "position": "belowBar" if is_buy else "aboveBar",
                "color": "#ffb300" if is_buy else "#ff5252",
                "shape": "arrowUp" if is_buy else "arrowDown",
                "text": f"SCALP #{idx + 1} {'BUY' if is_buy else 'SELL'}",
            })
        if min_1m <= exit_ts <= max_1m:
            markers_1m.append({
                "time": exit_ts,
                "position": "aboveBar" if is_buy else "belowBar",
                "color": "#00e676" if is_win else "#ff5252",
                "shape": "circle",
                "text": f"#{idx + 1} {status_str} ({r_val:+.2f}R)",
            })

        if min_30m <= entry_30m_ts <= max_30m:
            markers_30m.append({
                "time": entry_30m_ts,
                "position": "belowBar" if is_buy else "aboveBar",
                "color": "#ffb300" if is_buy else "#ff5252",
                "shape": "arrowUp" if is_buy else "arrowDown",
                "text": f"SCALP #{idx + 1} {'BUY' if is_buy else 'SELL'}",
            })
        if min_30m <= exit_30m_ts <= max_30m:
            markers_30m.append({
                "time": exit_30m_ts,
                "position": "aboveBar" if is_buy else "belowBar",
                "color": "#00e676" if is_win else "#ff5252",
                "shape": "circle",
                "text": f"#{idx + 1} {status_str} ({r_val:+.2f}R)",
            })

        trades_payload.append({
            "id": idx + 1,
            "side": "BUY" if is_buy else "SELL",
            "status": status_str,
            "r": round(r_val, 2),
            "ai_pct": ai_pct,
            "checklist_score": chk_score,
            "dva_risk_pct": dva_risk_pct,
            "entry_time": entry_ts,
            "exit_time": exit_ts,
            "entry_time_30m": entry_30m_ts,
            "exit_time_30m": exit_30m_ts,
            "entry_time_str": str(raw_entry_time),
            "exit_time_str": str(raw_exit_time),
            "entry": round(entry_px, 5),
            "sl": round(sl_px, 5),
            "tp": round(tp_px, 5),
            "exit_price": round(exit_px, 5),
        })

    markers_1m.sort(key=lambda m: m["time"])
    markers_30m.sort(key=lambda m: m["time"])
    be_label = "OFF" if (breakeven_r is None or float(breakeven_r) <= 0) else f"{float(breakeven_r):.2f}R"

    html_content = f"""<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="UTF-8" />
<title>Super-Scalp Backtest Chart (1m / 30m)</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: Tahoma, sans-serif; }}
  body {{ background: #131722; color: #d1d4dc; display: flex; flex-direction: column; height: 100vh; overflow: hidden; }}
  #toolbar {{
    background: #1e222d; border-bottom: 1px solid #2a2e39; padding: 8px 14px;
    display: flex; align-items: center; gap: 10px; flex-wrap: wrap; z-index: 10;
  }}
  .badge-scalp {{
    background: rgba(255, 179, 0, 0.16); color: #ffd54f; border: 1px solid rgba(255, 179, 0, 0.4);
    padding: 4px 8px; border-radius: 4px; font-size: 12px; font-weight: bold;
  }}
  .badge-be {{
    background: rgba(56, 189, 248, 0.14); color: #7dd3fc; border: 1px solid rgba(56, 189, 248, 0.35);
    padding: 4px 8px; border-radius: 4px; font-size: 12px; font-family: monospace;
  }}
  button {{
    background: #f59e0b; color: #111827; border: none; padding: 6px 12px;
    border-radius: 4px; cursor: pointer; font-size: 13px; font-weight: bold;
  }}
  button:hover {{ background: #fbbf24; }}
  button.secondary {{ background: #2a2e39; color: #d1d4dc; }}
  button.secondary:hover {{ background: #363a45; }}
  select {{
    background: #2a2e39; color: #fff; border: 1px solid #363a45;
    padding: 6px 10px; border-radius: 4px; font-size: 13px;
  }}
  #trade-select {{ min-width: 290px; }}
  #tf-select {{ min-width: 95px; font-weight: bold; }}
  #trade-info {{
    margin-right: auto; font-size: 13px; background: #131722;
    padding: 6px 12px; border-radius: 4px; border: 1px solid #2a2e39; direction: ltr;
  }}
  .win {{ color: #00e676; font-weight: bold; }}
  .loss {{ color: #ff5252; font-weight: bold; }}
  #chart-container {{ flex: 1; width: 100%; height: 100%; position: relative; }}
</style>
</head>
<body>

<div id="toolbar">
  <span class="badge-scalp">⚡ SUPER-SCALP</span>
  <span class="badge-be">BE Threshold: {be_label}</span>
  <button class="secondary" onclick="prevTrade()">⏮ سیگنال قبلی</button>
  <button onclick="nextTrade()">سیگنال بعدی (Forward) ⏭</button>
  <select id="tf-select" onchange="switchTimeframe(this.value)">
    <option value="1m" selected>1m</option>
    <option value="30m">30m</option>
  </select>
  <select id="trade-select" onchange="jumpToTrade(parseInt(this.value))"></select>
  <button class="secondary" onclick="expandView()">🔍 Expand</button>
  <button class="secondary" onclick="fitAll()">🌐 کل چارت</button>
  <div id="trade-info">در حال بارگذاری چارت اسکالپ...</div>
</div>

<div id="chart-container"></div>

<script>
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
    if (idx >= urls.length) return;
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
    const container = document.getElementById('chart-container');
    chart = LightweightCharts.createChart(container, {{
      width: container.clientWidth || window.innerWidth,
      height: container.clientHeight || (window.innerHeight - 50),
      layout: {{ background: {{ color: '#131722' }}, textColor: '#d1d4dc' }},
      grid: {{ vertLines: {{ color: '#1f2943' }}, horzLines: {{ color: '#1f2943' }} }},
      crosshair: {{ mode: LightweightCharts.CrosshairMode.Normal }},
      rightPriceScale: {{ borderColor: '#2a2e39', autoScale: true }},
      timeScale: {{ borderColor: '#2a2e39', timeVisible: true, secondsVisible: false, rightOffset: 15, barSpacing: 8 }},
    }});

    candleSeries = typeof chart.addCandlestickSeries === 'function'
      ? chart.addCandlestickSeries({{ upColor: '#26a69a', downColor: '#ef5350', borderVisible: false, wickUpColor: '#26a69a', wickDownColor: '#ef5350' }})
      : chart.addSeries(LightweightCharts.CandlestickSeries, {{ upColor: '#26a69a', downColor: '#ef5352', borderVisible: false, wickUpColor: '#26a69a', wickDownColor: '#ef5352' }});

    applyTimeframeData(currentTf);

    const selectEl = document.getElementById('trade-select');
    trades.forEach((t, i) => {{
      const opt = document.createElement('option');
      opt.value = i;
      opt.text = `#${{t.id}} | ${{t.side}} | ${{t.status}} (${{t.r >= 0 ? '+' : ''}}${{t.r}}R) | AI:${{t.ai_pct}} | ${{t.entry_time_str}}`;
      selectEl.appendChild(opt);
    }});

    if (trades.length > 0) jumpToTrade(0);
    else chart.timeScale().fitContent();
  }}

  function switchTimeframe(tf) {{
    if (!candleSeries || !candlesByTf[tf]) return;
    applyTimeframeData(tf);
    if (currentIdx >= 0) jumpToTrade(currentIdx);
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
    activeLines.push(candleSeries.createPriceLine({{ price: t.entry, color: '#ffb300', lineWidth: 2, lineStyle: 0, axisLabelVisible: true, title: `SCALP ENTRY #${{t.id}}` }}));
    activeLines.push(candleSeries.createPriceLine({{ price: t.sl, color: '#ff5252', lineWidth: 2, lineStyle: 2, axisLabelVisible: true, title: `SL #${{t.id}}` }}));
    activeLines.push(candleSeries.createPriceLine({{ price: t.tp, color: '#00e676', lineWidth: 2, lineStyle: 1, axisLabelVisible: true, title: `TP #${{t.id}}` }}));

    const is30m = currentTf === "30m";
    const entryT = is30m ? t.entry_time_30m : t.entry_time;
    const exitT = is30m ? t.exit_time_30m : t.exit_time;
    const paddingBefore = is30m ? (25 * 1800) : (35 * 60);
    const paddingAfter = is30m ? (20 * 1800) : (25 * 60);
    const minSpan = is30m ? (30 * 1800) : (50 * 60);

    chart.timeScale().setVisibleRange({{
      from: entryT - paddingBefore,
      to: Math.max(exitT + paddingAfter, entryT + minSpan),
    }});

    const statusClass = t.r >= 0 ? 'win' : 'loss';
    document.getElementById('trade-info').innerHTML =
      `<b>[SCALP ${{currentTf}}] #${{t.id}} ${{t.side}}</b> | <span class="${{statusClass}}">${{t.status}} (${{t.r >= 0 ? '+' : ''}}${{t.r}}R)</span> | ` +
      `Entry: <b>${{t.entry}}</b> | SL: <b>${{t.sl}}</b> | TP: <b>${{t.tp}}</b> | Risk%: <b>${{t.dva_risk_pct}}</b> | AI: <b>${{t.ai_pct}}</b>`;
  }}

  function nextTrade() {{ if (trades.length) jumpToTrade((currentIdx + 1) % trades.length); }}
  function prevTrade() {{ if (trades.length) jumpToTrade(currentIdx <= 0 ? trades.length - 1 : currentIdx - 1); }}
  function expandView() {{
    if (!chart) return;
    const r = chart.timeScale().getVisibleRange();
    if (!r) return;
    const span = r.to - r.from;
    chart.timeScale().setVisibleRange({{ from: r.from - Math.floor(span * 0.5), to: r.to + Math.floor(span * 0.5) }});
  }}
  function fitAll() {{ if (chart) chart.timeScale().fitContent(); }}

  window.addEventListener('keydown', (e) => {{
    if (e.key === 'ArrowRight') nextTrade();
    if (e.key === 'ArrowLeft') prevTrade();
  }});
  window.addEventListener('resize', () => {{
    if (!chart) return;
    const c = document.getElementById('chart-container');
    chart.applyOptions({{ width: c.clientWidth || window.innerWidth, height: c.clientHeight || (window.innerHeight - 50) }});
  }});

  loadScriptSequential(cdnUrls);
</script>
</body>
</html>
"""
    with open(output_html, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"📈 چارت تعاملی Super-Scalp (1m / 30m) ساخته شد: {os.path.abspath(output_html)}")
    if auto_open:
        webbrowser.open(f"file://{os.path.abspath(output_html)}")


def compare_breakeven_thresholds(
    ohlc: pd.DataFrame,
    base_cfg: SuperScalpConfig,
    dva_cfg: DVAARConfig | None = None,
    ai_predict_fn=None,
    thresholds: list[float | None] | None = None,
) -> pd.DataFrame:
    """مقایسه چند مقدار مختلف dva_breakeven_r روی یک دیتای ثابت."""
    if thresholds is None:
        thresholds = [None, 0.6, 0.8, 1.0, 1.2]

    base_dva = copy.deepcopy(dva_cfg) if dva_cfg is not None else DVAARConfig()
    rows = []
    for be_r in thresholds:
        dva_copy = copy.deepcopy(base_dva)
        dva_copy.dva_breakeven_r = be_r

        bt = run_super_scalp_backtest(
            ohlc,
            cfg=base_cfg,
            dva_cfg=dva_copy,
            ai_predict_fn=ai_predict_fn,
            plot_chart=False,
        )
        m = bt.get("summary") or {}
        trades = bt.get("trades") or []

        be_count = sum(1 for t in trades if str(t.get("outcome", "")).lower() in ("be", "breakeven", "sl_after_be"))
        sl_count = sum(1 for t in trades if str(t.get("outcome", "")).lower() == "sl")
        tp_count = sum(1 for t in trades if str(t.get("outcome", "")).lower() in ("tp", "tp_final"))

        max_dd_r = float(m.get("max_dd_r", 0.0))
        if max_dd_r == 0.0 and trades:
            r_arr = np.array([float(t.get("r", 0.0)) for t in trades], dtype=float)
            eq = np.cumsum(r_arr)
            max_dd_r = float(np.max(np.maximum.accumulate(eq) - eq))

        total_r = float(m.get("total_r", 0.0))
        return_dd_ratio = round(total_r / max_dd_r, 2) if max_dd_r > 1e-9 else 0.0

        rows.append({
            "breakeven_r": "OFF" if (be_r is None or float(be_r) <= 0) else f"{float(be_r):.2f}R",
            "trades": int(m.get("total_trades", len(trades))),
            "win_rate_%": float(m.get("win_rate_pct", 0.0)),
            "ai_wr_%": float(m.get("ai_approved_win_rate_pct", 0.0)),
            "tp": tp_count,
            "be": be_count,
            "sl": sl_count,
            "total_r": total_r,
            "pnl_usd": float(m.get("total_pnl_usd", 0.0)),
            "max_dd_r": round(max_dd_r, 2),
            "r_per_dd": return_dd_ratio,
        })

    df_cmp = pd.DataFrame(rows)
    print("\n" + "=" * 78)
    print("📊 مقایسه آستانه‌های مختلف dva_breakeven_r در بک‌تست Super-Scalping")
    print("=" * 78)
    print(df_cmp.to_string(index=False))
    print("=" * 78)
    return df_cmp


def run_super_scalp_backtest(
    ohlc_ltf: pd.DataFrame,
    cfg: SuperScalpConfig | None = None,
    dva_cfg: DVAARConfig | None = None,
    ai_predict_fn: Any = None,
    plot_chart: bool = False,
    chart_html: str = "scalp_backtest_chart.html",
    auto_open_chart: bool = True,
) -> dict[str, Any]:
    """اجرای بک‌تست مستقل روی OHLC خام با Heikin Ashi، تعیین SL/حجم با DVA، سر‌به‌سر و فیلترها."""
    cfg = cfg or SuperScalpConfig()
    dva_cfg = dva_cfg or DVAARConfig()
    if ohlc_ltf is None or len(ohlc_ltf) < cfg.max_setup_bars + 10:
        return {"trades": [], "summary": {}, "filtered_by_ai": 0}

    ha = build_heikin_ashi(ohlc_ltf)
    m30_bias = compute_m30_bias(ohlc_ltf, htf_min=cfg.htf_min)

    seen_keys: set[str] = set()
    trades: list[dict[str, Any]] = []
    cooldown_until = -1
    filtered_by_ai = 0

    equity = max(float(getattr(dva_cfg, "dva_equity", getattr(cfg, "account_balance", 1000.0)) or 1000.0), 1.0)
    peak_equity = equity
    max_dd_r = 0.0
    cum_r = 0.0
    peak_r = 0.0

    for c in range(cfg.max_setup_bars, len(ohlc_ltf) - 1):
        if c <= cooldown_until:
            continue

        setup = detect_super_scalp_setup(ha, ohlc_ltf, c, int(m30_bias[c]), cfg)
        if not setup or setup["setup_key"] in seen_keys:
            continue

        seen_keys.add(setup["setup_key"])

        # فیلتر هم‌راستایی با M30 در صورت فعال بودن
        if getattr(cfg, "require_m30_alignment", False) and not bool(setup.get("m30_aligned", False)):
            continue

        ai_prob = None
        ai_approved = None
        if callable(ai_predict_fn):
            ai_prob = float(ai_predict_fn(setup))
            ai_approved = bool(ai_prob >= cfg.ai_min_prob)
            if not getattr(cfg, "ai_advisory_only", True) and not ai_approved:
                filtered_by_ai += 1
                continue

        # ۱. تعیین حد ضرر (SL) توسط DVA
        entry_px = float(setup["entry"])
        direction = int(setup["direction"])
        point_val = 100.0 if entry_px >= 50.0 else 10.0
        win_slice = ohlc_ltf.iloc[max(0, c - dva_cfg.vol_ref_window + 1) : c + 1]

        sl_px, sl_dist = compute_dva_stop_loss(
            direction=direction,
            entry_price=entry_px,
            atr_value=setup.get("atr"),
            ohlc_ltf=win_slice,
            cfg=dva_cfg,
        )
        sl_px = float(sl_px)
        sl_dist = float(sl_dist)
        if sl_dist <= 1e-9 or abs(entry_px - sl_px) <= 1e-9:
            continue

        # ۲. تنظیم تارگت (TP) متناسب با SL جدیدِ DVA و حداقل RR
        min_rr_val = max(1.0, float(getattr(cfg, "min_rr", 1.2) or 1.2))
        raw_tp = float(setup["tp"])
        if direction == 1:  # BUY
            min_tp = entry_px + (min_rr_val * sl_dist)
            tp_px = max(raw_tp, min_tp)
        else:  # SELL
            min_tp = entry_px - (min_rr_val * sl_dist)
            tp_px = min(raw_tp, min_tp)

        setup["sl"] = sl_px
        setup["tp"] = round(tp_px, 5)
        setup["rr"] = round(abs(tp_px - entry_px) / sl_dist, 2)

        # ۳. محاسبه حجم و ریسک دلاری
        if dva_cfg.dva_enabled:
            dva_res = compute_dva_scalp_position(
                equity=equity,
                entry_price=entry_px,
                stop_price=sl_px,
                atr_value=setup.get("atr"),
                ohlc_ltf=win_slice,
                point_value_per_lot=point_val,
                cfg=dva_cfg,
                ai_prob=ai_prob,
                ai_approved=ai_approved,
            )
            if dva_res.get("allowed"):
                lot = float(dva_res["volume"])
                risk_usd = float(dva_res["risk_amount_usd"])
                dva_risk_pct = float(dva_res["dva_risk_pct"])
                vol_factor = float(dva_res["vol_factor"])
            else:
                lot, risk_usd = compute_simple_risk_lot(entry_px, sl_px, cfg)
                if lot <= 0:
                    lot = float(getattr(dva_cfg, "min_lot", 0.01))
                    risk_usd = lot * sl_dist * point_val
                dva_risk_pct = round((risk_usd / max(equity, 1.0)) * 100.0, 4)
                vol_factor = float(dva_res.get("vol_factor", 1.0))
        else:
            lot, risk_usd = compute_simple_risk_lot(entry_px, sl_px, cfg)
            if lot <= 0:
                continue
            dva_risk_pct = round((risk_usd / max(equity, 1.0)) * 100.0, 4)
            vol_factor = 1.0

        # ۴. شبیه‌سازی معامله با امکان انتقال حد ضرر به سر‌به‌سر (Break-Even)
        be_threshold = (
            float(dva_cfg.dva_breakeven_r)
            if (dva_cfg.dva_breakeven_r is not None and float(dva_cfg.dva_breakeven_r) > 0)
            else None
        )
        sim = simulate_scalp_trade_with_be(
            df=ohlc_ltf,
            entry_idx=c,
            direction=direction,
            entry_price=entry_px,
            stop_loss=sl_px,
            take_profit=tp_px,
            max_bars=cfg.max_hold_bars,
            breakeven_r=be_threshold,
        )

        r_val = float(sim["r"])
        pnl_usd = round(r_val * risk_usd, 2)
        equity = max(1.0, equity + pnl_usd)
        peak_equity = max(peak_equity, equity)

        cum_r += r_val
        peak_r = max(peak_r, cum_r)
        max_dd_r = max(max_dd_r, peak_r - cum_r)

        exit_idx = int(sim.get("exit_idx", min(len(ohlc_ltf) - 1, c + int(sim["bars_held"]))))
        cooldown_until = c + max(1, int(sim["bars_held"]))

        trades.append(
            {
                **setup,
                "time": ohlc_ltf.index[c],
                "exit_time": ohlc_ltf.index[exit_idx],
                "lot": lot,
                "risk_usd": round(risk_usd, 2),
                "dva_risk_pct": dva_risk_pct,
                "vol_factor": vol_factor,
                "breakeven_r": be_threshold,
                "be_moved": bool(sim.get("be_moved", False)),
                "outcome": sim["outcome"],
                "r": r_val,
                "pnl_usd": pnl_usd,
                "bars_held": sim["bars_held"],
                "exit_price": sim["exit_price"],
                "ai_prob": ai_prob,
                "ai_approved": ai_approved,
            }
        )

    total = len(trades)
    wins = [t for t in trades if float(t["r"]) > 0]
    be_trades = [t for t in trades if float(t["r"]) == 0]
    losses = [t for t in trades if float(t["r"]) < 0]

    m30_aligned = [t for t in trades if t.get("m30_aligned")]
    m30_wins = [t for t in m30_aligned if float(t["r"]) > 0]

    ai_ok_trades = [t for t in trades if t.get("ai_approved") is True]
    ai_ok_wins = [t for t in ai_ok_trades if float(t["r"]) > 0]
    be_moved_count = sum(1 for t in trades if t.get("be_moved"))
    be_exit_count = len(be_trades)

    decisive_total = len(wins) + len(losses)
    real_win_rate_pct = round((len(wins) / decisive_total) * 100.0, 2) if decisive_total else 0.0

    summary = {
        "total_trades": total,
        "wins": len(wins),
        "losses": len(losses),
        "breakeven_trades": len(be_trades),
        "win_rate_pct": round((len(wins) / total) * 100.0, 2) if total else 0.0,
        "real_win_rate_pct": real_win_rate_pct,
        "total_r": round(sum(float(t["r"]) for t in trades), 2),
        "total_pnl_usd": round(sum(float(t["pnl_usd"]) for t in trades), 2),
        "max_dd_r": round(max_dd_r, 2),
        "m30_aligned_trades": len(m30_aligned),
        "m30_aligned_win_rate_pct": round((len(m30_wins) / len(m30_aligned)) * 100.0, 2) if m30_aligned else 0.0,
        "ai_approved_trades": len(ai_ok_trades),
        "ai_approved_win_rate_pct": round((len(ai_ok_wins) / len(ai_ok_trades)) * 100.0, 2) if ai_ok_trades else 0.0,
        "ai_advisory_only": getattr(cfg, "ai_advisory_only", True),
        "filtered_by_ai": filtered_by_ai,
        "dva_enabled": bool(dva_cfg.dva_enabled),
        "dva_breakeven_r": dva_cfg.dva_breakeven_r,
        "be_moved_count": be_moved_count,
        "be_exit_count": be_exit_count,
        "final_equity": round(equity, 2),
    }

    if plot_chart and trades:
        generate_scalp_backtest_chart(
            ohlc_ltf=ohlc_ltf,
            trades_results=trades,
            output_html=chart_html,
            auto_open=auto_open_chart,
            breakeven_r=dva_cfg.dva_breakeven_r,
        )

    return {"trades": trades, "summary": summary, "filtered_by_ai": filtered_by_ai}