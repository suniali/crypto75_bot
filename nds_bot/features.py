from __future__ import annotations
import numpy as np
from .structure import Bars, Setup

FEATURES = [
    "prev_err_1",
    "prev_err_2",
    "atr_pct",
    "rel_volume",
    "ltf_hurst",
    "htf_hurst",
    "tail_alpha",
    "momentum_atr",
    "leg_len_atr",
    "leg_symmetry_ratio",
    "pullback_z_atr",
    "spike_f_atr",
    "tp1_rr",
    "final_rr",
    "is_reentry_864",
    "direction",
]


def extract_features_dict(
    s: Setup,
    B: Bars,
    prev_errors: list[float],
    ltf_hurst: float,
    htf_hurst: float,
    tail_alpha: float,
) -> dict[str, float]:
    risk = max(abs(s.entry - s.sl), 1e-9)
    atr_val = max(s.atr, 1e-9)
    vol_mean = float(B.v[max(0, s.j - 20):s.j].mean()) if s.j > 0 else float(B.v[s.j])

    # Leg 1: N1 -> N2, Leg 2: N2 -> N3 (NDS vector displacement symmetry)
    leg1 = abs(s.p[1].price - s.p[0].price)
    leg2 = abs(s.p[2].price - s.p[1].price)
    leg_sym_ratio = leg2 / max(leg1, 1e-9)

    return {
        "prev_err_1": float(prev_errors[-1]) if len(prev_errors) >= 1 else 0.0,
        "prev_err_2": float(prev_errors[-2]) if len(prev_errors) >= 2 else 0.0,
        "atr_pct": float(atr_val / max(s.entry, 1e-9)),
        "rel_volume": float(B.v[s.j] / max(vol_mean, 1e-9)),
        "ltf_hurst": float(ltf_hurst),
        "htf_hurst": float(htf_hurst),
        "tail_alpha": float(tail_alpha),
        "momentum_atr": float((s.entry - B.c[max(0, s.j - 1)]) / atr_val),
        "leg_len_atr": float(leg2 / atr_val),
        "leg_symmetry_ratio": float(leg_sym_ratio),
        "pullback_z_atr": float(abs(s.z - s.p[2].price) / atr_val),
        "spike_f_atr": float(abs(s.f - s.p[2].price) / atr_val),
        "tp1_rr": float(abs(s.tp1 - s.entry) / risk),
        "final_rr": float(abs(s.tp_final - s.entry) / risk),
        "is_reentry_864": 1.0 if s.is_reentry_864 else 0.0,
        "direction": float(s.direction),
    }


def extract_features(
    s: Setup,
    B: Bars,
    prev_errors: list[float],
    ltf_hurst: float,
    htf_hurst: float = 0.5,
    tail_alpha: float = 1.5,
) -> np.ndarray:
    d = extract_features_dict(s, B, prev_errors, ltf_hurst, htf_hurst, tail_alpha)
    return np.array([d[k] for k in FEATURES], dtype=np.float32)