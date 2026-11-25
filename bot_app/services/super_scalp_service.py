from __future__ import annotations

import os
from typing import Any
import joblib
import pandas as pd

from nds_bot.super_scalp_engine import (
    SuperScalpConfig,
    build_heikin_ashi,
    compute_m30_bias,
    compute_simple_risk_lot,
    detect_super_scalp_setup,
)


def rows_to_ohlc_df(rows: list[dict[str, Any]]) -> pd.DataFrame:
    """تبدیل لیست دیکشنری کندل‌ها به DataFrame استاندارد OHLC با ایندکس زمانی صعودی."""
    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close"])

    df = pd.DataFrame(
        {
            "open": [float(r.get("open_price") if r.get("open_price") is not None else r.get("open")) for r in rows],
            "high": [float(r.get("high_price") if r.get("high_price") is not None else r.get("high")) for r in rows],
            "low": [float(r.get("low_price") if r.get("low_price") is not None else r.get("low")) for r in rows],
            "close": [float(r.get("close_price") if r.get("close_price") is not None else r.get("close")) for r in rows],
        },
        index=pd.to_datetime([r.get("open_time") if r.get("open_time") is not None else r.get("time") for r in rows]),
    )
    df = df.sort_index()
    return df[~df.index.duplicated(keep="last")]


def extract_scalp_ai_features(setup: dict[str, Any]) -> dict[str, float]:
    """استخراج ویژگی‌های عددی ستاپ Super-Scalp برای آموزش و پیش‌بینی مدل AI."""
    chk = setup.get("checklist") or {}
    f_info = setup.get("flag_info") or {}
    nds = setup.get("nds_metrics") or {}

    return {
        "direction": float(setup.get("direction", 0)),
        "rr": float(setup.get("rr", 0.0)),
        "setup_bars": float(setup.get("setup_bars", 0)),
        "symmetry_error": float(setup.get("symmetry_error", 1.0)),
        "leg_symmetry_error": float(setup.get("leg_symmetry_error", 1.0)),
        "node_symmetry_error": float(setup.get("node_symmetry_error", 1.0)),
        "atr": float(setup.get("atr", 0.0)),
        "garch_vol": float(setup.get("garch_vol", 0.0)),
        "m30_bias": float(setup.get("m30_bias", 0)),
        "m30_aligned": 1.0 if setup.get("m30_aligned") else 0.0,
        "checklist_score": float(setup.get("checklist_score", 0)),
        "chk_flag": 1.0 if chk.get("flag") else 0.0,
        "chk_sequencing": 1.0 if chk.get("sequencing") else 0.0,
        "chk_fib_864": 1.0 if chk.get("fib_864") else 0.0,
        "chk_symmetry": 1.0 if chk.get("symmetry") else 0.0,
        "chk_hhrr": 1.0 if chk.get("hhrr") else 0.0,
        "flag_ratio": float(f_info.get("flag_ratio", 1.0)),
        "hook_bar_sep": float(f_info.get("hook_bar_sep", 0)),
        "gain_pct_g": float(nds.get("gain_pct_g", 0.0)),
    }


def load_scalp_ai_predictor(model_path: str = "models/super_scalp_ai.joblib"):
    """لود مدل آموزش‌دیده Super-Scalp برای پیش‌بینی احتمال برد (در صورت عدم وجود، None برمی‌گرداند)."""
    if not model_path or not os.path.exists(model_path):
        return None

    bundle = joblib.load(model_path)
    model = bundle.get("model")
    feature_cols = bundle.get("feature_cols") or []
    if model is None or not feature_cols:
        return None

    def _predict(setup: dict[str, Any]) -> float:
        feats = extract_scalp_ai_features(setup)
        X = pd.DataFrame([[feats.get(col, 0.0) for col in feature_cols]], columns=feature_cols)
        if hasattr(model, "predict_proba"):
            return float(model.predict_proba(X)[0][1])
        return float(model.predict(X)[0])

    return _predict


def evaluate_super_scalp_strategy(
    rows: list[dict[str, Any]],
    symbol: str = "XAUUSD_l",
    time_frame: str = "M1",
    cfg: SuperScalpConfig | None = None,
    ai_predict_fn: Any = None,
) -> dict[str, Any]:
    """اجرای موتور Super-Scalping روی کندل‌های اخیر و ساخت payload مناسب ربات/تلگرام/داشبورد."""
    cfg = cfg or SuperScalpConfig(ltf=time_frame)
    ohlc = rows_to_ohlc_df(rows)
    if len(ohlc) < cfg.min_setup_bars + 5:
        return {"has_signal": False, "reason": "تعداد کندل‌ها برای تشخیص ستاپ اسکالپ کافی نیست."}

    ha = build_heikin_ashi(ohlc)
    m30_bias = compute_m30_bias(ohlc, htf_min=cfg.htf_min)
    last_idx = len(ohlc) - 1

    setup = detect_super_scalp_setup(ha, ohlc, last_idx, int(m30_bias[-1]), cfg)
    if not setup:
        return {"has_signal": False, "reason": "ستاپ Super-Scalp معتبری در کندل فعلی شکل نگرفته است."}

    lot, risk_usd = compute_simple_risk_lot(setup["entry"], setup["sl"], cfg)
    if lot <= 0:
        return {"has_signal": False, "reason": "حجم محاسبه‌شده کمتر از حداقل مجاز است."}

    ai_prob = None
    ai_threshold = cfg.ai_min_prob
    ai_approved = None
    if callable(ai_predict_fn):
        ai_prob = float(ai_predict_fn(setup))
        ai_approved = bool(ai_prob >= ai_threshold)
        if not getattr(cfg, "ai_advisory_only", False) and not ai_approved:
            return {
                "has_signal": False,
                "reason": f"ستاپ توسط فیلتر AI رد شد (prob={ai_prob:.2f} < {ai_threshold:.2f}).",
            }

    risk_pct = round((risk_usd / max(cfg.account_balance, 1.0)) * 100.0, 2)
    explanation = (
        f"Super-Scalp ({setup['side']}) | امتیاز چک‌لیست: {setup['checklist_score']}/5 | "
        f"هم‌راستا با M30: {'بله' if setup['m30_aligned'] else 'خیر'} | "
        f"ATR: {setup['atr']} | GARCH: {setup['garch_vol']:.6f} | "
        f"G: {setup['nds_metrics']['gain_pct_g']}%"
    )

    features_snapshot = {
        **setup,
        "symbol": symbol,
        "timeframe": time_frame,
        "htf": cfg.htf,
        "entry_price": setup["entry"],
        "stop_loss": setup["sl"],
        "take_profit": setup["tp"],
        "tp_final": setup["tp"],
        "calculated_volume": lot,
        "risk_amount_usd": risk_usd,
        "risk_percentage": risk_pct,
        "ai_prob": ai_prob,
        "ai_threshold": ai_threshold,
        "ai_approved": ai_approved,
        "nds_explanation": explanation,
    }

    return {
        "has_signal": True,
        "symbol": symbol,
        "action": setup["side"],
        "side": setup["side"],
        "timeframe": time_frame,
        "entry_price": setup["entry"],
        "stop_loss": setup["sl"],
        "take_profit": setup["tp"],
        "calculated_volume": lot,
        "risk_amount_usd": risk_usd,
        "ai_confidence": float(ai_prob) if ai_prob is not None else 0.0,
        "ai_prob": ai_prob,
        "ai_approved": ai_approved,
        "features": features_snapshot,
    }