from __future__ import annotations
import numpy as np
import pandas as pd


def atr(df: pd.DataFrame, period: int = 14) -> np.ndarray:
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False).mean().to_numpy()


def dfa_hurst(prices: np.ndarray, min_box: int = 8, max_box: int = 32) -> float:
    """
    Detrended Fluctuation Analysis (DFA) on log-returns cumulative profile:
    Y(k) = sum(r_i - mean(r)), F(n) ~ n^H.
    Correctly distinguishes persistent trends (H > 0.55), mean-reversion (H < 0.45),
    and pure random walk noise (H ~ 0.50).
    """
    n_pts = len(prices)
    if n_pts < max_box * 2:
        return 0.5

    log_p = np.log(np.maximum(prices, 1e-12))
    returns = np.diff(log_p)
    profile = np.cumsum(returns - np.mean(returns))
    N = len(profile)

    boxes = np.unique(np.logspace(np.log10(min_box), np.log10(min(max_box, N // 3)), num=8).astype(int))
    boxes = boxes[boxes >= 4]
    if len(boxes) < 3:
        return 0.5

    flucts = []
    valid_boxes = []
    for box in boxes:
        n_segments = N // box
        if n_segments < 2:
            continue
        reshaped = profile[:n_segments * box].reshape(n_segments, box)
        x = np.arange(box, dtype=float)
        x_mean = x.mean()
        x_var = np.sum((x - x_mean) ** 2)
        y_mean = reshaped.mean(axis=1, keepdims=True)
        slopes = np.sum((x - x_mean) * (reshaped - y_mean), axis=1) / x_var
        intercepts = y_mean[:, 0] - slopes * x_mean
        trend = slopes[:, None] * x + intercepts[:, None]
        rms = np.sqrt(np.mean((reshaped - trend) ** 2))
        if rms > 1e-12:
            flucts.append(rms)
            valid_boxes.append(box)

    if len(valid_boxes) < 3:
        return 0.5

    H = float(np.polyfit(np.log(valid_boxes), np.log(flucts), 1)[0])
    return float(np.clip(H, 0.05, 0.95))


def estimate_tail_alpha(prices: np.ndarray) -> float:
    """
    Hill-type / fractional tail index estimator for LML fat-tail risk regime.
    Alpha ~ 2.0 (Gaussian-like), Alpha ~ 1.5 (S&P500), Alpha < 1.15 (Extreme Crypto/Crisis).
    """
    if len(prices) < 60:
        return 1.5
    rets = np.abs(np.diff(np.log(np.maximum(prices, 1e-12))))
    rets = rets[rets > 0]
    if len(rets) < 30:
        return 1.5
    rets_sorted = np.sort(rets)[::-1]
    k = max(5, len(rets_sorted) // 5)
    top_k = rets_sorted[:k]
    thresh = rets_sorted[k]
    if thresh <= 0:
        return 1.5
    denom = np.mean(np.log(top_k / thresh))
    if denom <= 1e-9:
        return 2.0
    alpha = 1.0 / denom
    return float(np.clip(alpha, 0.5, 2.5))