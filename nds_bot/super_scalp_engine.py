from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any
import numpy as np
import pandas as pd


@dataclass
class SuperScalpConfig:
    """تنظیمات مستقل موتور Super-Scalping در NDS."""
    ltf: str = "M1"
    htf: str = "M30"
    htf_min: int = 30
    pivot_window: int = 2
    min_setup_bars: int = 5
    max_setup_bars: int = 80
    max_hold_bars: int = 45

    # پارامترهای نوسان (ATR و GARCH(1,1))
    atr_window: int = 14
    garch_omega: float = 1e-6
    garch_alpha: float = 0.10
    garch_beta: float = 0.85

    # پارامترهای هندسی و سکانسی چک‌لیست اسلاید 114
    symmetry_tol: float = 0.35
    node_symmetry_tol: float = 0.40
    fib_864_tol: float = 0.08
    max_flag_ratio: float = 0.45
    min_hook_sep_bars: int = 2
    min_rr: float = 0.5
    require_all_5_checks: bool = False
    min_checklist_score: int = 3

    # مدیریت ریسک ساده فعلی (بدون تغییر)
    account_balance: float = 10000.0
    risk_per_trade_pct: float = 0.5
    fixed_risk_usd: float | None = 50.0
    point_value_per_lot: float = 100.0
    min_lot: float = 0.01
    lot_step: float = 0.01
    max_lot: float = 20.0

    # حالت مشورتی AI
    ai_advisory_only: bool = True
    ai_min_prob: float = 0.50
    extra: dict[str, Any] = field(default_factory=dict)


def build_heikin_ashi(ohlc: pd.DataFrame) -> pd.DataFrame:
    """ساخت کندل‌های Heikin Ashi برای تشخیص نودها، سکانس و فلگ."""
    if ohlc is None or ohlc.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close"])

    o = ohlc["open"].astype(float).to_numpy()
    h = ohlc["high"].astype(float).to_numpy()
    l = ohlc["low"].astype(float).to_numpy()
    c = ohlc["close"].astype(float).to_numpy()

    ha_close = (o + h + l + c) / 4.0
    ha_open = np.empty_like(ha_close)
    ha_open[0] = (o[0] + c[0]) / 2.0

    for i in range(1, len(ha_close)):
        ha_open[i] = (ha_open[i - 1] + ha_close[i - 1]) / 2.0

    ha_high = np.maximum.reduce([h, ha_open, ha_close])
    ha_low = np.minimum.reduce([l, ha_open, ha_close])

    return pd.DataFrame(
        {"open": ha_open, "high": ha_high, "low": ha_low, "close": ha_close},
        index=ohlc.index,
    )


def compute_atr(ohlc: pd.DataFrame, window: int = 14) -> float:
    """محاسبه Average True Range (ATR) روی کندل‌های OHLC."""
    if ohlc is None or len(ohlc) < 2:
        return 0.0

    h = ohlc["high"].astype(float).to_numpy()
    l = ohlc["low"].astype(float).to_numpy()
    c = ohlc["close"].astype(float).to_numpy()
    prev_c = np.roll(c, 1)
    prev_c[0] = c[0]

    tr = np.maximum.reduce([h - l, np.abs(h - prev_c), np.abs(l - prev_c)])
    sub_tr = tr[-min(len(tr), max(1, window)) :]
    return float(np.mean(sub_tr))


def compute_garch_volatility(
    closes: np.ndarray,
    omega: float = 1e-6,
    alpha: float = 0.10,
    beta: float = 0.85,
) -> float:
    """
    محاسبه نوسان شرطی مدل GARCH(1,1):
    sigma_t^2 = omega + alpha * eps_{t-1}^2 + beta * sigma_{t-1}^2
    sigma_t = sqrt(sigma_t^2)
    """
    if closes is None or len(closes) < 5:
        return 0.0

    c = np.asarray(closes, dtype=float)
    valid = np.all(c > 0)
    if not valid:
        return 0.0

    returns = np.diff(np.log(c))
    if len(returns) < 3:
        return 0.0

    # پایداری شرطی ساده برای جلوگیری از واگرایی واریانس
    a = max(0.0, float(alpha))
    b = max(0.0, float(beta))
    w = max(1e-12, float(omega))
    if a + b >= 0.999:
        scale = 0.99 / (a + b)
        a *= scale
        b *= scale

    var_t = float(np.var(returns)) if float(np.var(returns)) > 1e-12 else 1e-6
    for eps in returns:
        var_t = w + a * (float(eps) ** 2) + b * var_t

    return math.sqrt(max(var_t, 0.0))


def compute_nds_target_and_progress(
    *,
    side: str,
    entry_price: float,
    reversal_price: float,
    custom_target: float | None = None,
) -> dict[str, float]:
    """
    محاسبه هدف 86% NDS، سطح میانی (P_mid) و درصد پیشروی (G):
    G = ((P_entry - P_rev) / (P_target - P_rev)) * 100
    P_mid = (P_rev + P_target) / 2
    """
    if side == "SELL":
        target_86 = (
            float(custom_target)
            if custom_target is not None
            else float(entry_price) * (1.0 - 0.86)
        )
    else:
        target_86 = (
            float(custom_target)
            if custom_target is not None
            else float(entry_price) * (1.0 + 0.86)
        )

    p_mid = 0.5 * (float(reversal_price) + float(target_86))
    denom = float(target_86) - float(reversal_price)
    if abs(denom) > 1e-9:
        gain_pct = ((float(entry_price) - float(reversal_price)) / denom) * 100.0
    else:
        gain_pct = 0.0

    return {
        "target_86": round(float(target_86), 4),
        "p_mid": round(float(p_mid), 4),
        "gain_pct_g": round(float(gain_pct), 2),
    }


def compute_m30_bias(ohlc_ltf: pd.DataFrame, htf_min: int = 30) -> np.ndarray:
    """محاسبه جهت M30 به عنوان مزیت کیفی (غیرالزامی برای ورود اسکالپ)."""
    n = len(ohlc_ltf)
    if n == 0 or not isinstance(ohlc_ltf.index, pd.DatetimeIndex):
        return np.zeros(n, dtype=int)

    htf_df = (
        ohlc_ltf.resample(f"{htf_min}min", label="left", closed="left")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
        .dropna()
    )
    if len(htf_df) < 3:
        return np.zeros(n, dtype=int)

    ema_fast = htf_df["close"].ewm(span=8, adjust=False).mean()
    ema_slow = htf_df["close"].ewm(span=21, adjust=False).mean()
    htf_bias = np.where(ema_fast > ema_slow, 1, np.where(ema_fast < ema_slow, -1, 0))

    htf_close_ns = (htf_df.index + pd.Timedelta(minutes=htf_min)).to_numpy()
    ltf_close_ns = (ohlc_ltf.index + pd.Timedelta(minutes=1)).to_numpy()
    idx = np.clip(
        np.searchsorted(htf_close_ns, ltf_close_ns, side="right") - 1,
        0,
        len(htf_bias) - 1,
    )
    return htf_bias[idx]


def find_ha_pivots(ha: pd.DataFrame, window: int = 2) -> list[tuple[int, str, float]]:
    """استخراج پیوت‌های سقف (H) و کف (L) روی Heikin Ashi."""
    highs = ha["high"].to_numpy()
    lows = ha["low"].to_numpy()
    pivots: list[tuple[int, str, float]] = []

    for i in range(window, len(ha) - window):
        h_slice = highs[i - window : i + window + 1]
        l_slice = lows[i - window : i + window + 1]
        if highs[i] == np.max(h_slice) and np.sum(h_slice == highs[i]) == 1:
            pivots.append((i, "H", float(highs[i])))
        elif lows[i] == np.min(l_slice) and np.sum(l_slice == lows[i]) == 1:
            pivots.append((i, "L", float(lows[i])))

    return pivots


def evaluate_flag_and_hhrr(
    ha_win: pd.DataFrame,
    highs_only: list[tuple[int, str, float]],
    lows_only: list[tuple[int, str, float]],
    hook_range: float,
    max_flag_ratio: float = 0.45,
    min_hook_sep_bars: int = 2,
) -> dict[str, Any]:
    """
    ارزیابی جزئیات Flag و Hook-Hook-Rally (H1 -> H2) روی پنجره Heikin Ashi.
    """
    flag_slice = ha_win.iloc[-min(5, len(ha_win)) :]
    flag_high = float(flag_slice["high"].max())
    flag_low = float(flag_slice["low"].min())
    flag_span = max(0.0, flag_high - flag_low)
    flag_ratio = (flag_span / hook_range) if hook_range > 1e-9 else 1.0

    flag_ok = (hook_range > 1e-9) and (flag_ratio <= max_flag_ratio)

    h1_piv = highs_only[-2] if len(highs_only) >= 2 else None
    h2_piv = highs_only[-1] if len(highs_only) >= 1 else None
    l1_piv = lows_only[-2] if len(lows_only) >= 2 else None
    l2_piv = lows_only[-1] if len(lows_only) >= 1 else None

    h_sep = (h2_piv[0] - h1_piv[0]) if (h1_piv and h2_piv) else 0
    l_sep = (l2_piv[0] - l1_piv[0]) if (l1_piv and l2_piv) else 0

    hhrr_ok = (
        len(highs_only) >= 2
        and len(lows_only) >= 2
        and max(h_sep, l_sep) >= min_hook_sep_bars
    )

    return {
        "flag_ok": bool(flag_ok),
        "flag_high": flag_high,
        "flag_low": flag_low,
        "flag_span": round(flag_span, 4),
        "flag_ratio": round(float(flag_ratio), 4),
        "hhrr_ok": bool(hhrr_ok),
        "h1_price": h1_piv[2] if h1_piv else None,
        "h2_price": h2_piv[2] if h2_piv else None,
        "l1_price": l1_piv[2] if l1_piv else None,
        "l2_price": l2_piv[2] if l2_piv else None,
        "hook_bar_sep": int(max(h_sep, l_sep)),
    }


def _compute_node_symmetry_error(
    pivots: list[tuple[int, str, float]],
    leg_a: float,
    leg_b: float,
) -> tuple[float, float]:
    """
    محاسبه خطای تقارن لگ‌ها و تقارن نودهای متوالی در ساختار 1-2-3.
    """
    leg_sym_err = abs(leg_a - leg_b) / max(leg_a, leg_b, 1e-9)

    if len(pivots) >= 4:
        d1 = abs(pivots[-1][2] - pivots[-2][2])
        d2 = abs(pivots[-2][2] - pivots[-3][2])
        d3 = abs(pivots[-3][2] - pivots[-4][2])
        node_sym_err = max(
            abs(d1 - d2) / max(d1, d2, 1e-9),
            abs(d2 - d3) / max(d2, d3, 1e-9),
        )
    else:
        node_sym_err = leg_sym_err

    return float(leg_sym_err), float(node_sym_err)


def detect_super_scalp_setup(
    ha: pd.DataFrame,
    raw_ohlc: pd.DataFrame,
    bar_idx: int,
    m30_bias_val: int,
    cfg: SuperScalpConfig,
) -> dict[str, Any] | None:
    """
    تشخیص ستاپ Super-Scalping بر اساس چک‌لیست اسلاید 114 روی Heikin Ashi:
    1. Flag
    2. Sequencing (1-2-3 با پنجره کندلی در بر گیرنده ستاپ)
    3. %86.4 Hook
    4. Symmetry (Legs & Nodes)
    5. Hook-Hook-Rally (HHRR)
    """
    start_i = max(0, bar_idx - cfg.max_setup_bars)
    ha_win = ha.iloc[start_i : bar_idx + 1]
    raw_win = raw_ohlc.iloc[start_i : bar_idx + 1]
    if len(ha_win) < cfg.min_setup_bars:
        return None

    pivots = find_ha_pivots(ha_win, window=cfg.pivot_window)
    if len(pivots) < 4:
        return None

    last_piv = pivots[-1]
    highs_only = [p for p in pivots if p[1] == "H"]
    lows_only = [p for p in pivots if p[1] == "L"]
    if len(highs_only) < 2 or len(lows_only) < 2:
        return None

    entry_raw = float(raw_win["close"].iloc[-1])
    atr_val = compute_atr(raw_win, window=cfg.atr_window)
    garch_vol = compute_garch_volatility(
        raw_win["close"].to_numpy(),
        omega=cfg.garch_omega,
        alpha=cfg.garch_alpha,
        beta=cfg.garch_beta,
    )

    # حالت SHORT (نود 3 سقف و تارگت روی اولین نقاط 1,2 باز / کمر فلگ)
    if last_piv[1] == "H":
        n3_idx, _, n3_price = last_piv
        open_low = lows_only[-1]
        open_high = highs_only[-2]
        hook_base_low = lows_only[0][2]
        hook_top_high = highs_only[-1][2]
        hook_range = max(hook_top_high - hook_base_low, 1e-9)
        level_864 = hook_base_low + 0.864 * hook_range

        f_info = evaluate_flag_and_hhrr(
            ha_win,
            highs_only,
            lows_only,
            hook_range,
            max_flag_ratio=cfg.max_flag_ratio,
            min_hook_sep_bars=cfg.min_hook_sep_bars,
        )
        seq_start_local = min(open_high[0], open_low[0])
        setup_span_bars = int(n3_idx - seq_start_local + 1)
        seq_ok = cfg.min_setup_bars <= setup_span_bars <= cfg.max_setup_bars

        near_864 = abs(entry_raw - level_864) / max(entry_raw, 1e-9) <= cfg.fib_864_tol
        fib_864_ok = near_864 or (entry_raw >= hook_base_low + 0.75 * hook_range)

        leg_a = abs(open_high[2] - open_low[2])
        leg_b = abs(n3_price - open_low[2])
        leg_sym_err, node_sym_err = _compute_node_symmetry_error(pivots, leg_a, leg_b)
        sym_err = max(leg_sym_err, node_sym_err)
        sym_ok = (leg_sym_err <= cfg.symmetry_tol) and (node_sym_err <= cfg.node_symmetry_tol)

        checks = {
            "flag": f_info["flag_ok"],
            "sequencing": seq_ok,
            "fib_864": fib_864_ok,
            "symmetry": sym_ok,
            "hhrr": f_info["hhrr_ok"],
        }
        score = sum(1 for v in checks.values() if v)
        if cfg.require_all_5_checks and score < 5:
            return None
        if not (seq_ok and f_info["flag_ok"]) and score < cfg.min_checklist_score:
            return None

        sl_price = max(f_info["flag_high"], n3_price) + max(0.05 * f_info["flag_span"], 0.2)
        waist_price = 0.5 * (open_low[2] + min(open_high[2], n3_price))
        tp_price = min(waist_price, entry_raw - 0.35 * abs(sl_price - entry_raw))
        if tp_price >= entry_raw or sl_price <= entry_raw:
            return None

        rr = (entry_raw - tp_price) / max(entry_raw - sl_price, 1e-9)
        if rr < cfg.min_rr:
            return None

        nds_metrics = compute_nds_target_and_progress(
            side="SELL",
            entry_price=entry_raw,
            reversal_price=hook_base_low,
            custom_target=level_864,
        )

        return {
            "side": "SELL",
            "direction": -1,
            "entry": entry_raw,
            "sl": float(sl_price),
            "tp": float(tp_price),
            "rr": round(float(rr), 2),
            "level_864": float(level_864),
            "waist_price": float(waist_price),
            "setup_bars": setup_span_bars,
            "symmetry_error": round(float(sym_err), 4),
            "leg_symmetry_error": round(float(leg_sym_err), 4),
            "node_symmetry_error": round(float(node_sym_err), 4),
            "atr": round(float(atr_val), 4),
            "garch_vol": round(float(garch_vol), 8),
            "m30_bias": int(m30_bias_val),
            "m30_aligned": bool(m30_bias_val == -1),
            "checklist": checks,
            "checklist_score": score,
            "flag_info": f_info,
            "nds_metrics": nds_metrics,
            "setup_key": f"SELL_{start_i + seq_start_local}_{bar_idx}",
        }

    # حالت LONG (نود 3 کف و تارگت روی اولین نقاط 1,2 باز / کمر فلگ)
    if last_piv[1] == "L":
        n3_idx, _, n3_price = last_piv
        open_high = highs_only[-1]
        open_low = lows_only[-2]
        hook_top_high = highs_only[0][2]
        hook_base_low = lows_only[-1][2]
        hook_range = max(hook_top_high - hook_base_low, 1e-9)
        level_864 = hook_top_high - 0.864 * hook_range

        f_info = evaluate_flag_and_hhrr(
            ha_win,
            highs_only,
            lows_only,
            hook_range,
            max_flag_ratio=cfg.max_flag_ratio,
            min_hook_sep_bars=cfg.min_hook_sep_bars,
        )
        seq_start_local = min(open_low[0], open_high[0])
        setup_span_bars = int(n3_idx - seq_start_local + 1)
        seq_ok = cfg.min_setup_bars <= setup_span_bars <= cfg.max_setup_bars

        near_864 = abs(entry_raw - level_864) / max(entry_raw, 1e-9) <= cfg.fib_864_tol
        fib_864_ok = near_864 or (entry_raw <= hook_top_high - 0.75 * hook_range)

        leg_a = abs(open_high[2] - open_low[2])
        leg_b = abs(open_high[2] - n3_price)
        leg_sym_err, node_sym_err = _compute_node_symmetry_error(pivots, leg_a, leg_b)
        sym_err = max(leg_sym_err, node_sym_err)
        sym_ok = (leg_sym_err <= cfg.symmetry_tol) and (node_sym_err <= cfg.node_symmetry_tol)

        checks = {
            "flag": f_info["flag_ok"],
            "sequencing": seq_ok,
            "fib_864": fib_864_ok,
            "symmetry": sym_ok,
            "hhrr": f_info["hhrr_ok"],
        }
        score = sum(1 for v in checks.values() if v)
        if cfg.require_all_5_checks and score < 5:
            return None
        if not (seq_ok and f_info["flag_ok"]) and score < cfg.min_checklist_score:
            return None

        sl_price = min(f_info["flag_low"], n3_price) - max(0.05 * f_info["flag_span"], 0.2)
        waist_price = 0.5 * (open_high[2] + max(open_low[2], n3_price))
        tp_price = max(waist_price, entry_raw + 0.35 * abs(entry_raw - sl_price))
        if tp_price <= entry_raw or sl_price >= entry_raw:
            return None

        rr = (tp_price - entry_raw) / max(entry_raw - sl_price, 1e-9)
        if rr < cfg.min_rr:
            return None

        nds_metrics = compute_nds_target_and_progress(
            side="BUY",
            entry_price=entry_raw,
            reversal_price=hook_top_high,
            custom_target=level_864,
        )

        return {
            "side": "BUY",
            "direction": 1,
            "entry": entry_raw,
            "sl": float(sl_price),
            "tp": float(tp_price),
            "rr": round(float(rr), 2),
            "level_864": float(level_864),
            "waist_price": float(waist_price),
            "setup_bars": setup_span_bars,
            "symmetry_error": round(float(sym_err), 4),
            "leg_symmetry_error": round(float(leg_sym_err), 4),
            "node_symmetry_error": round(float(node_sym_err), 4),
            "atr": round(float(atr_val), 4),
            "garch_vol": round(float(garch_vol), 8),
            "m30_bias": int(m30_bias_val),
            "m30_aligned": bool(m30_bias_val == 1),
            "checklist": checks,
            "checklist_score": score,
            "flag_info": f_info,
            "nds_metrics": nds_metrics,
            "setup_key": f"BUY_{start_i + seq_start_local}_{bar_idx}",
        }

    return None


def compute_simple_risk_lot(entry: float, sl: float, cfg: SuperScalpConfig) -> tuple[float, float]:
    """محاسبه ساده حجم و مبلغ ریسک دلاری بر اساس فاصله SL واقعی."""
    sl_dist = abs(float(entry) - float(sl))
    if sl_dist <= 1e-9 or cfg.point_value_per_lot <= 1e-9:
        return 0.0, 0.0

    risk_usd = (
        float(cfg.fixed_risk_usd)
        if cfg.fixed_risk_usd is not None
        else float(cfg.account_balance) * (float(cfg.risk_per_trade_pct) / 100.0)
    )
    raw_lot = risk_usd / (sl_dist * float(cfg.point_value_per_lot))
    step = max(float(cfg.lot_step), 1e-6)
    lot = math.floor(raw_lot / step) * step
    lot = min(max(lot, cfg.min_lot), cfg.max_lot) if raw_lot >= cfg.min_lot else 0.0
    actual_risk_usd = lot * sl_dist * float(cfg.point_value_per_lot)
    return round(lot, 2), round(actual_risk_usd, 2)