from __future__ import annotations
import math
import os
import numpy as np
import pandas as pd

from nds_bot.config import NDSConfig
from nds_bot.structure import make_bars, scan_latest
from nds_bot.features import extract_features_dict
from nds_bot.kalman import KalmanFilter
from nds_bot.indicators import dfa_hurst
from nds_bot.explain import explain
from nds_bot.model import load_bundle, predict_win_prob
from bot_app.models import MarketCandle, TradeDecisionLog

NDS_BARS = 1500
MIN_BARS = 600

# Strategic HTF = M30, Tactical Entry LTF = M1
HTF_FOR = {"M1": "M30", "M5": "M30", "M15": "H1", "M30": "H4", "H1": "H4"}

MODEL_DIR = os.getenv("NDS_MODEL_DIR", "models")

_ISSUED: dict = {}
_KALMAN_STATE: dict = {}
_BUNDLES: dict = {}


def get_bundle(symbol: str, ltf: str) -> dict | None:
    """Hot-reload trained joblib bundle if file exists or was updated by nds_trainer."""
    path = os.path.join(MODEL_DIR, f"nds_model_{symbol}_{ltf}.joblib")
    if not os.path.exists(path):
        return None
    mtime = os.path.getmtime(path)
    cached = _BUNDLES.get((symbol, ltf))
    if cached and cached["mtime"] == mtime:
        return cached["bundle"]
    bundle = load_bundle(path)
    _BUNDLES[(symbol, ltf)] = {"mtime": mtime, "bundle": bundle}
    return bundle


def norm_tf(tf: str) -> str:
    s = str(tf).strip()
    if len(s) > 1 and s[0].upper() in "MHD" and s[1:].isdigit():
        return s[0].upper() + s[1:]
    return s.upper()


def rows_to_ohlc(rows: list) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    t = pd.to_datetime(df["open_time"], utc=True).dt.tz_localize(None)
    out = pd.DataFrame({
        "open": df["open_price"].astype(float),
        "high": df["high_price"].astype(float),
        "low": df["low_price"].astype(float),
        "close": df["close_price"].astype(float),
        "volume": df["volume"].astype(float),
    })
    out.index = pd.DatetimeIndex(t, name="time")
    return out[~out.index.duplicated()].sort_index()


def sanitize(obj):
    if isinstance(obj, dict):
        return {k: sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [sanitize(v) for v in obj]
    try:
        f = float(obj)
    except (TypeError, ValueError):
        return obj if isinstance(obj, (str, bool)) or obj is None else str(obj)
    return None if not math.isfinite(f) else (int(f) if isinstance(obj, bool) else f)


def _hold(reason: str) -> dict:
    return {"signal": "HOLD", "setup_type": "NONE", "reason": reason}


def _bootstrap_symbol_state(symbol: str, ltf: str, cfg: NDSConfig):
    """Recover issued setup keys & Kalman filter state from DB after Django restart."""
    if symbol not in _KALMAN_STATE:
        kf = KalmanFilter(Q=cfg.kalman_Q_init, R=cfg.kalman_R_init)
        errors: list[float] = []
        recent_logs = (
            TradeDecisionLog.objects.filter(symbol=symbol)
            .order_by("-timestamp")[:30]
        )
        for lg in reversed(list(recent_logs)):
            snap = lg.features_snapshot or {}
            obs = snap.get("leg_measure")
            if obs is not None:
                _, err = kf.update(float(obs), cfg.kalman_Q_init, cfg.kalman_R_init)
                errors.append(float(err))
        _KALMAN_STATE[symbol] = {"kf": kf, "errors": errors}

    issued = _ISSUED.setdefault((symbol, ltf), set())
    if not issued:
        prefix = f"{symbol}_{ltf}_"
        db_keys = (
            TradeDecisionLog.objects.filter(symbol=symbol, features_snapshot__nds_key__startswith=prefix)
            .values_list("features_snapshot__nds_key", flat=True)[:100]
        )
        for full_k in db_keys:
            if full_k and full_k.startswith(prefix):
                issued.add(full_k[len(prefix):])
    return issued


def evaluate_nds_strategy(rows: list, symbol: str, time_frame: str = "M1") -> dict:
    ltf = norm_tf(time_frame)
    if ltf not in HTF_FOR:
        return _hold(f"تایم‌فریم {time_frame} برای NDS تعریف نشده است.")
    if len(rows) < MIN_BARS:
        return _hold(f"تعداد کندل کافی نیست ({len(rows)} از {MIN_BARS}).")

    cfg = NDSConfig(ltf=ltf, htf=HTF_FOR[ltf], signal_max_age=1)
    ohlc_ltf = rows_to_ohlc(rows)

    htf_tf = cfg.htf
    htf_rows = list(
        MarketCandle.objects.filter(symbol=symbol, timeframe=htf_tf)
        .order_by("-open_time")
        .values("open_price", "high_price", "low_price", "close_price", "volume", "open_time")[:800]
    )
    if len(htf_rows) < 50:
        return _hold(f"تعداد کندل کافی برای تایم‌فریم بالا ({htf_tf}) وجود ندارد.")

    ohlc_htf = rows_to_ohlc(htf_rows)
    issued = _bootstrap_symbol_state(symbol, ltf, cfg)

    s = scan_latest(ohlc_ltf, ohlc_htf, cfg, issued)
    if s is None:
        return _hold("ستاپ معتبر NDS (جهت M30 + الگوی 123Flag + BABI در M1) وجود ندارد.")

    issued.add(s.key)

    # 1. Kalman Filter Symmetry Validation on single displacement wave |N3 - N2|
    kf = _KALMAN_STATE[symbol]["kf"]
    errors = _KALMAN_STATE[symbol]["errors"]
    obs = s.leg_measure
    L_hat, err = kf.update(obs, cfg.kalman_Q_init, cfg.kalman_R_init)
    errors.append(float(err))
    if len(errors) > 100:
        errors.pop(0)

    symmetry_error = abs(obs - L_hat) / (L_hat + 1e-9)
    if symmetry_error > cfg.symmetry_tol:
        return _hold(f"رد شده: خطای تقارن موج ({symmetry_error:.1%}) بیش از حد مجاز ({cfg.symmetry_tol:.1%}) است.")

    # 2. Extract NDS + Fractal Features & Optional Meta-Labeling AI Filter
    B = make_bars(ohlc_ltf, cfg)
    ltf_h = dfa_hurst(B.c[max(0, s.c - cfg.hurst_window):s.c + 1])
    feat_dict = extract_features_dict(s, B, errors[:-1], ltf_h, s.htf.hurst, s.htf.tail_alpha)

    bundle = get_bundle(symbol, ltf)
    prob = None
    thr = None
    ai_approved = None
    if bundle is not None:
        prob = float(predict_win_prob(bundle, feat_dict))
        raw_thr = float(bundle.get("threshold", cfg.ai_min_prob))
        thr = max(raw_thr, cfg.ai_min_prob) if cfg.force_ai else raw_thr
        ai_approved = bool(prob >= thr)

        ai_active = True if cfg.force_ai else bool(bundle.get("useful"))
        if ai_active and not getattr(cfg, "ai_advisory_only", False) and not ai_approved:
            return _hold(f"رد شده توسط فیلتر AI: احتمال موفقیت ({prob:.0%}) کمتر از آستانه ({thr:.0%}) است.")

    feat_dict["ai_prob"] = prob
    feat_dict["ai_threshold"] = thr
    feat_dict["ai_approved"] = ai_approved

    return {
        "signal": "SELL" if s.direction == -1 else "BUY",
        "setup_type": "NDS_REENTRY_864" if s.is_reentry_864 else "NDS_123FLAG_BABI",
        "nds_key": f"{symbol}_{ltf}_{s.key}",
        "entry_price": round(s.entry, 5),
        "stop_loss": round(s.sl, 5),
        "take_profit": round(s.tp_final, 5),     # هدف نهایی نود ۳ تایم‌فریم M30
        "take_profit_1": round(s.tp1, 5),        # هدف اول (اولویت سکانس: بسته شدن نقاط ۱ و ۲ باز - خروج ۱/۳)
        "take_profit_2": round(s.tp_final, 5),   # هدف نهایی
        "atr": round(s.atr, 5),
        "rsi": 50.0,
        "ai_probability": round(prob, 4) if prob is not None else 1.0,
        "ai_threshold": round(thr, 4) if thr is not None else None,
        "nds_features": sanitize({
            **feat_dict,
            "leg_measure": obs,
            "symmetry_error": symmetry_error,
            "L_hat": L_hat,
            "lvl864": s.lvl864,
        }),
        "nds_explanation": explain(s, cfg, prob, thr, symbol, "fa"),
        "reason": "ستاپ NDS تایید شد (هم‌راستا با M30 + تقارن کالمن + رژیم فرکتالی).",
    }