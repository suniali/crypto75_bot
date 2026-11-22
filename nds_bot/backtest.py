import numpy as np
import pandas as pd
from .config import NDSConfig
from .structure import make_bars, raw_pivots, PivotTracker, group_by_confirm, htf_states, detect_setup, resample_ohlc
from .kalman import KalmanFilter, QRPredictor
from .features import extract_features
import torch

def run_backtest(ltf: pd.DataFrame, cfg: NDSConfig, htf: pd.DataFrame = None, model: QRPredictor = None):
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
    issued = set()
    rows = []
    kf = KalmanFilter()
    errors = []
    # Precompute Hurst for each bar (simplified)
    hurst_vals = np.full(len(B.c), 0.5)
    for i in range(100, len(B.c)):
        window = B.c[i-100:i]
        # simple hurst
        lags = range(2, 20)
        tau = [np.std(window[lag:] - window[:-lag]) for lag in lags]
        poly = np.polyfit(np.log(lags), np.log(tau), 1)
        hurst_vals[i] = poly[0]
    
    for c in range(len(B.c)):
        for p in by_conf.get(c, []): tr.add(p)
        hb = hmap[c]
        if hb < 30 or c < 100: continue
        info = states[hb]
        if info.bias == 0: continue
        s = detect_setup(tr.p, B, c, info, cfg, issued)
        if s is None: continue
        issued.add(s.key)
        # Kalman filter update using leg length observation (e.g., length of previous leg)
        # Observation: absolute distance from p1 to p3 (flag length)
        obs = abs(s.p[2].price - s.p[0].price)
        # Predict Q, R using AI
        if model is not None:
            feats = extract_features(s, B, errors, hurst_vals[c])
            with torch.no_grad():
                Q_t, R_t = model(torch.tensor(feats).unsqueeze(0)).squeeze().numpy()
        else:
            Q_t, R_t = cfg.kalman_Q_init, cfg.kalman_R_init
        L_hat, err = kf.update(obs, Q_t, R_t)
        errors.append(err)
        # Symmetry check: if |obs - L_hat| / L_hat > tol, skip trade
        symmetry_error = abs(obs - L_hat) / (L_hat + 1e-9)
        if symmetry_error > cfg.symmetry_tol:
            continue  # reject trade due to poor symmetry
        # Simulate trade
        res = simulate_trade(s, B, cfg)
        row = {"time": s.time, "direction": s.direction, "entry": s.entry, "sl": s.sl, "tp": s.tp, **res}
        rows.append(row)
    return pd.DataFrame(rows)

def simulate_trade(s, B, cfg):
    d = s.direction
    risk = abs(s.entry - s.sl)
    tp = s.tp
    sl = s.sl
    end = min(len(B.h), s.c + 1 + cfg.max_hold_bars)
    for b in range(s.c+1, end):
        hi, lo = B.h[b], B.l[b]
        # Check SL first (conservative)
        if d == 1:  # BUY
            if lo <= sl:
                return {"r": -1.0, "outcome": "sl", "bars_held": b - s.c}
            if hi >= tp:
                return {"r": (tp - s.entry)/risk, "outcome": "tp", "bars_held": b - s.c}
        else:  # SELL
            if hi >= sl:
                return {"r": -1.0, "outcome": "sl", "bars_held": b - s.c}
            if lo <= tp:
                return {"r": (s.entry - tp)/risk, "outcome": "tp", "bars_held": b - s.c}
    # Timeout
    last_close = B.c[end-1]
    r = d * (last_close - s.entry) / risk
    return {"r": float(r), "outcome": "timeout", "bars_held": end - s.c - 1}