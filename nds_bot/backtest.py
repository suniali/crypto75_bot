from __future__ import annotations
import numpy as np
import pandas as pd
import torch

from .config import NDSConfig
from .structure import (
    make_bars, raw_pivots, PivotTracker, group_by_confirm,
    htf_states, detect_setup, resample_ohlc, Setup, Bars
)
from .kalman import KalmanFilter, QRPredictor
from .features import extract_features, extract_features_dict
from .indicators import dfa_hurst


def run_backtest(
    ltf: pd.DataFrame,
    cfg: NDSConfig,
    htf: pd.DataFrame | None = None,
    model: QRPredictor | None = None,
    symbol: str = "",
) -> pd.DataFrame:
    if htf is None:
        htf = resample_ohlc(ltf, cfg.htf_min)

    B = make_bars(ltf, cfg)
    H = make_bars(htf, cfg)
    states = htf_states(H, cfg)

    htf_close = (htf.index + pd.Timedelta(minutes=cfg.htf_min)).to_numpy()
    ltf_close = (ltf.index + pd.Timedelta(minutes=cfg.ltf_min)).to_numpy()
    hmap = np.searchsorted(htf_close, ltf_close, side="right") - 1

    by_conf = group_by_confirm(raw_pivots(B.h, B.l, cfg.pivot_ltf))
    tr = PivotTracker()
    issued: set[str] = set()
    rows: list[dict] = []

    kf = KalmanFilter(Q=cfg.kalman_Q_init, R=cfg.kalman_R_init)
    errors: list[float] = []

    # Use strict backtest bar matching (0) so each setup is evaluated cleanly on its BABI bar
    bt_cfg = NDSConfig(**{**cfg.__dict__, "signal_max_age": 0})

    for c in range(len(B.c)):
        for p in by_conf.get(c, []):
            tr.add(p)

        hb = hmap[c]
        if hb < 30 or c < 120:
            continue

        info = states[hb]
        if info.bias == 0:
            continue

        s = detect_setup(tr.p, B, c, info, bt_cfg, issued)
        if s is None:
            continue
        issued.add(s.key)

        # Kalman Filter update on single displacement leg |N3 - N2|
        obs = s.leg_measure
        ltf_h = dfa_hurst(B.c[max(0, c - cfg.hurst_window):c + 1])

        if model is not None:
            feats = extract_features(s, B, errors, ltf_h, info.hurst, info.tail_alpha)
            with torch.no_grad():
                Q_t, R_t = model(torch.tensor(feats).unsqueeze(0)).squeeze().numpy()
        else:
            Q_t, R_t = cfg.kalman_Q_init, cfg.kalman_R_init

        L_hat, err = kf.update(obs, float(Q_t), float(R_t))
        errors.append(float(err))
        if len(errors) > 100:
            errors.pop(0)

        symmetry_error = abs(obs - L_hat) / (L_hat + 1e-9)
        if symmetry_error > cfg.symmetry_tol:
            continue

        res = simulate_trade(s, B, cfg)
        feat_dict = extract_features_dict(s, B, errors[:-1], ltf_h, info.hurst, info.tail_alpha)

        rows.append({
            "symbol": symbol,
            "time": s.time,
            "direction": s.direction,
            "is_reentry_864": s.is_reentry_864,
            "entry": s.entry,
            "sl": s.sl,
            "tp1": s.tp1,
            "tp_final": s.tp_final,
            "lvl864": s.lvl864,
            "symmetry_error": symmetry_error,
            **feat_dict,
            **res,
        })

    return pd.DataFrame(rows)


def simulate_trade(s: Setup, B: Bars, cfg: NDSConfig) -> dict:
    """
    Simulates the NDS Day-Trading exit lifecycle:
    1) Sequence Priority Exit (TP1): When price closes Open Nodes 1 & 2 (forming new Node 3),
       exit `tp1_fraction` (1/3) immediately without waiting for 86%.
    2) Optionally move SL to Breakeven after TP1.
    3) Hold remaining 2/3 until HTF Node 3 Final Target (`tp_final`), SL, or timeout.
    """
    d = s.direction
    risk = abs(s.entry - s.sl)
    cost_r = cfg.cost_price / risk if risk > 0 else 0.0

    rem_weight = 1.0
    realized_r = 0.0
    tp1_hit = False
    active_sl = s.sl

    end = min(len(B.h), s.c + 1 + cfg.max_hold_bars)

    for b in range(s.c + 1, end):
        hi, lo = B.h[b], B.l[b]

        if d == 1:  # BUY
            sl_hit = lo <= active_sl
            tp1_reached = (not tp1_hit) and (hi >= s.tp1)
            tpf_reached = hi >= s.tp_final

            if sl_hit and (tp1_reached or tpf_reached) and cfg.same_bar_sl_first:
                realized_r += rem_weight * ((active_sl - s.entry) / risk)
                return {"r": float(realized_r - cost_r), "outcome": "sl_after_tp1" if tp1_hit else "sl",
                        "tp1_hit": tp1_hit, "bars_held": b - s.c}

            if tp1_reached:
                w1 = min(cfg.tp1_fraction, rem_weight)
                realized_r += w1 * ((s.tp1 - s.entry) / risk)
                rem_weight -= w1
                tp1_hit = True
                if cfg.move_be_after_tp1:
                    active_sl = max(active_sl, s.entry)

            if tpf_reached and rem_weight > 0:
                realized_r += rem_weight * ((s.tp_final - s.entry) / risk)
                return {"r": float(realized_r - cost_r), "outcome": "tp_final",
                        "tp1_hit": tp1_hit, "bars_held": b - s.c}

            if sl_hit and rem_weight > 0:
                realized_r += rem_weight * ((active_sl - s.entry) / risk)
                return {"r": float(realized_r - cost_r), "outcome": "sl_after_tp1" if tp1_hit else "sl",
                        "tp1_hit": tp1_hit, "bars_held": b - s.c}

        else:  # SELL
            sl_hit = hi >= active_sl
            tp1_reached = (not tp1_hit) and (lo <= s.tp1)
            tpf_reached = lo <= s.tp_final

            if sl_hit and (tp1_reached or tpf_reached) and cfg.same_bar_sl_first:
                realized_r += rem_weight * ((s.entry - active_sl) / risk)
                return {"r": float(realized_r - cost_r), "outcome": "sl_after_tp1" if tp1_hit else "sl",
                        "tp1_hit": tp1_hit, "bars_held": b - s.c}

            if tp1_reached:
                w1 = min(cfg.tp1_fraction, rem_weight)
                realized_r += w1 * ((s.entry - s.tp1) / risk)
                rem_weight -= w1
                tp1_hit = True
                if cfg.move_be_after_tp1:
                    active_sl = min(active_sl, s.entry)

            if tpf_reached and rem_weight > 0:
                realized_r += rem_weight * ((s.entry - s.tp_final) / risk)
                return {"r": float(realized_r - cost_r), "outcome": "tp_final",
                        "tp1_hit": tp1_hit, "bars_held": b - s.c}

            if sl_hit and rem_weight > 0:
                realized_r += rem_weight * ((s.entry - active_sl) / risk)
                return {"r": float(realized_r - cost_r), "outcome": "sl_after_tp1" if tp1_hit else "sl",
                        "tp1_hit": tp1_hit, "bars_held": b - s.c}

    last_close = B.c[end - 1]
    realized_r += rem_weight * (d * (last_close - s.entry) / risk)
    return {
        "r": float(realized_r - cost_r),
        "outcome": "timeout_after_tp1" if tp1_hit else "timeout",
        "tp1_hit": tp1_hit,
        "bars_held": end - s.c - 1,
    }


def stats(df: pd.DataFrame) -> dict:
    """Summary statistics required by model.py & nds_trainer.py."""
    if df is None or len(df) == 0:
        return {"trades": 0, "win_rate": 0.0, "avg_R": 0.0, "total_R": 0.0,
                "profit_factor": 0.0, "avg_R_ci95": (0.0, 0.0)}
    r = df["r"].to_numpy(float)
    n = len(r)
    wins = r[r > 0]
    losses = r[r < 0]
    avg_r = float(r.mean())
    se = float(r.std(ddof=1) / np.sqrt(n)) if n > 1 else 0.0
    pf = float(wins.sum() / abs(losses.sum())) if len(losses) > 0 and losses.sum() != 0 else float("inf")
    return {
        "trades": int(n),
        "win_rate": float((r > 0).mean()),
        "tp1_rate": float(df["tp1_hit"].mean()) if "tp1_hit" in df else 0.0,
        "avg_R": avg_r,
        "total_R": float(r.sum()),
        "profit_factor": pf,
        "avg_R_ci95": (avg_r - 1.96 * se, avg_r + 1.96 * se),
    }