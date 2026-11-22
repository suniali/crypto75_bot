"""NDS strategy for the Django forex bot - Integrated with Kalman Filter & PyTorch AI."""
from __future__ import annotations

import math
import os
import torch
import numpy as np
import pandas as pd

from nds_bot import NDSConfig
from nds_bot.structure import make_bars, scan_latest
from nds_bot.features import extract_features
from nds_bot.kalman import KalmanFilter, QRPredictor
from nds_bot.explain import explain
from bot_app.models import MarketCandle

NDS_BARS = 1500
MIN_BARS = 600

# LTF -> HTF (طبق دستور شما: ورود در 1 دقیقه و روند در 4 ساعته/30 دقیقه)
HTF_FOR = {"M1": "M30", "M5": "M30", "M15": "H1", "M30": "H4", "H1": "H4"} 

# مسیر مدل آموزش دیده
MODEL_PATH = os.getenv("NDS_MODEL_PATH", "nds_model.pt")

_ISSUED: dict = {}         
_KALMAN_STATE: dict = {}   # برای ذخیره وضعیت کالمن به ازای هر نماد
_MODEL = None              # مدل پایتورچ به صورت سینگلتون لود می‌شود

def load_ai_model():
    global _MODEL
    if _MODEL is None and os.path.exists(MODEL_PATH):
        # تعداد ویژگی‌های ورودی باید با features.py هم‌خوانی داشته باشد (10 عدد)
        _MODEL = QRPredictor(input_dim=10)
        _MODEL.load_state_dict(torch.load(MODEL_PATH, map_location=torch.device('cpu')))
        _MODEL.eval()
    return _MODEL

def norm_tf(tf: str) -> str:
    s = str(tf).strip()
    if len(s) > 1 and s[0].upper() in "MHD" and s[1:].isdigit():
        return s[0].upper() + s[1:]
    return s.upper()

def rows_to_ohlc(rows: list) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    t = pd.to_datetime(df["open_time"], utc=True).dt.tz_localize(None)
    out = pd.DataFrame({"open": df["open_price"].astype(float), "high": df["high_price"].astype(float),
                        "low": df["low_price"].astype(float), "close": df["close_price"].astype(float),
                        "volume": df["volume"].astype(float)})
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

def evaluate_nds_strategy(rows: list, symbol: str, time_frame: str = 'M1') -> dict:
    ltf = norm_tf(time_frame)
    if ltf not in HTF_FOR:
        return _hold(f"تایم‌فریم {time_frame} برای NDS تعریف نشده است.")
    if len(rows) < MIN_BARS:
        return _hold(f"تعداد کندل کافی نیست ({len(rows)} از {MIN_BARS}).")

    cfg = NDSConfig(ltf=ltf, htf=HTF_FOR[ltf])
    ohlc_ltf = rows_to_ohlc(rows)

     # --- تغییر مهم: خواندن کندل‌های HTF از دیتابیس ---
    htf_tf = cfg.htf
    htf_rows = list(
        MarketCandle.objects.filter(symbol=symbol, timeframe=htf_tf)
        .order_by('-open_time')
        .values('open_price', 'high_price', 'low_price', 'close_price', 'volume', 'open_time')[:800] # 800 کندل HTF کافی است
    )
    if len(htf_rows) < 50:
        return _hold(f"تعداد کندل کافی برای تایم‌فریم بالا ({htf_tf}) وجود ندارد.")
        
    ohlc_htf = rows_to_ohlc(htf_rows)

    # 1. دریافت ستاپ NDS
    issued = _ISSUED.setdefault((symbol, ltf), set())

    s = scan_latest(ohlc_ltf, ohlc_htf, cfg, issued)
    
    if s is None:
        return _hold("ستاپ NDS (۱۲۳ + فلگ + BABI) وجود ندارد.")
    
    issued.add(s.key)

    # 2. آماده‌سازی فیلتر کالمن
    if symbol not in _KALMAN_STATE:
        _KALMAN_STATE[symbol] = {"kf": KalmanFilter(), "errors": []}
    
    kf = _KALMAN_STATE[symbol]["kf"]
    errors = _KALMAN_STATE[symbol]["errors"]
    
    obs = abs(s.p[2].price - s.p[0].price) # مشاهده: طول فلگ
    
    # 3. پیش‌بینی Q و R توسط هوش مصنوعی
    model = load_ai_model()
    if model is not None:
        B = make_bars(ohlc_ltf, cfg)
        feats = extract_features(s, B, errors, 0.5)
        with torch.no_grad():
            Q_t, R_t = model(torch.tensor(feats).unsqueeze(0)).squeeze().numpy()
    else:
        Q_t, R_t = cfg.kalman_Q_init, cfg.kalman_R_init # مقادیر پیش‌فرض در صورت نبود مدل

    # 4. آپدیت کالمن
    L_hat, err = kf.update(obs, Q_t, R_t)
    errors.append(err)
    if len(errors) > 100: errors.pop(0) # جلوگیری از بزرگ شدن بی‌نهایت حافظه
    
    # 5. بررسی تقارن (اگر خطای تقارن زیاد بود، سیگنال رد می‌شود)
    symmetry_error = abs(obs - L_hat) / (L_hat + 1e-9)
    if symmetry_error > cfg.symmetry_tol:
        return _hold(f"رد شده: خطای تقارن ({symmetry_error:.1%}) بیش از حد مجاز است.")

    # 6. ساخت خروجی برای اجرا
    return {
        "signal": "SELL" if s.direction == -1 else "BUY",
        "setup_type": "NDS_123FLAG_BABI_KALMAN",
        "nds_key": f"{symbol}_{ltf}_{s.key}",
        "entry_price": round(s.entry, 5),
        "stop_loss": round(s.sl, 5),
        "take_profit": round(s.tp, 5),           # تارگت 86 درصد
        "take_profit_1": round(s.tp, 5),         # برای هماهنگی با دیتابیس شما
        "take_profit_2": round(s.tp, 5),
        "atr": round(s.atr, 5),
        "rsi": 50.0,                             # RSI دیگر استفاده نمی‌شود
        "ai_probability": 1.0,                   # در این نسخه AI احتمال نمی‌دهد، بلکه Q/R می‌دهد. برای عبور از فیلتر جنگو 1.0 می‌گذاریم
        "ai_threshold": None,
        "nds_features": sanitize({"symmetry_error": symmetry_error, "Q_t": Q_t, "R_t": R_t, "L_hat": L_hat}),
        "nds_explanation": explain(s, cfg, None, None, symbol, "fa"),
        "reason": "ستاپ NDS تایید شد (با فیلتر کالمن و AI).",
    }