import numpy as np
from .structure import Bars, Setup

def extract_features(s: Setup, B: Bars, prev_errors: list, hurst_val: float) -> np.ndarray:
    # Features for AI: past errors, volatility, volume, momentum, NDS geometry
    feats = [
        prev_errors[-1] if prev_errors else 0.0,
        prev_errors[-2] if len(prev_errors) > 1 else 0.0,
        s.atr / s.entry,
        B.v[s.j] / (B.v[max(0, s.j-20):s.j].mean() + 1e-9),
        hurst_val,
        (s.entry - B.c[s.j-1]) / s.atr,  # momentum
        abs(s.p[2].price - s.p[0].price) / s.atr,  # flag length in ATR
        abs(s.z - s.p[2].price) / s.atr,  # pullback depth
        s.nd,  # direction
        s.htf.bias
    ]
    return np.array(feats, dtype=np.float32)