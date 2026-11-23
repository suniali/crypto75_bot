"""Candle loading: CSV files or MetaTrader 5 (Windows, `pip install MetaTrader5`)."""
from __future__ import annotations

import pandas as pd


def load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, sep=None, engine="python")
    # نرمال‌سازی نام ستون‌ها
    df.columns = [str(c).strip().lower().strip("<>") for c in df.columns]
    
    print(f"🔎 ستون‌های شناسایی‌شده: {list(df.columns)}")
    
    # 1. حالت متاتریدر: ستون‌های جداگانه date و time
    if "time" in df.columns and "date" in df.columns:
        t = pd.to_datetime(df["date"].astype(str) + " " + df["time"].astype(str))
    else:
        # 2. حالت‌های رایج دیگر: time/datetime/date/timestamp/gmt time
        candidates = ("time", "datetime", "date", "timestamp", "gmt time", "gmt_time", "local time", "local_time")
        col = next((c for c in candidates if c in df.columns), None)
        if col is None:
            raise ValueError(f"❌ هیچ ستون زمانی شناخته‌شده‌ای یافت نشد. ستون‌های موجود: {list(df.columns)}")
        
        raw = df[col]
        
        # 3. اگر ستون عددی است: احتمالاً Unix Timestamp است
        if pd.api.types.is_numeric_dtype(raw):
            # مقادیر بزرگتر از 1e17 را نانوثانیه، 1e14 را میکروثانیه، 1e11 را میلی‌ثانیه، و کمتر را ثانیه در نظر بگیر
            sample = float(raw.iloc[0])
            if sample > 1e17:
                unit = 'ns'
            elif sample > 1e14:
                unit = 'us'
            elif sample > 1e11:
                unit = 'ms'
            else:
                unit = 's'
            print(f"⏱ ستون زمانی عددی تشخیص داده شد. واحد: {unit} (مقدار نمونه: {sample})")
            t = pd.to_datetime(raw, unit=unit)
        else:
            # 4. اگر رشته است: پارس معمولی
            t = pd.to_datetime(raw, errors='coerce')
    
    # حذف ردیف‌های با تاریخ نامعتبر
    valid_mask = t.notna()
    if not valid_mask.all():
        print(f"⚠️ {len(t) - valid_mask.sum()} ردیف با تاریخ نامعتبر حذف شد.")
        df = df[valid_mask]
        t = t[valid_mask]
    
    # ستون حجم
    if "volume" not in df.columns:
        df["volume"] = df["tickvol"] if "tickvol" in df.columns else df.get("tick_volume", 1.0)
    
    out = pd.DataFrame({
        "open": df["open"].astype(float),
        "high": df["high"].astype(float),
        "low": df["low"].astype(float),
        "close": df["close"].astype(float),
        "volume": df["volume"].astype(float),
    })
    out.index = pd.DatetimeIndex(t, name="time")
    out = out[~out.index.duplicated()].sort_index()
    
    print(f"📅 محدوده زمانی نهایی: {out.index[0]} → {out.index[-1]}")
    print(f"📊 تعداد نهایی کندل: {len(out)}")
    return out

def _mt5():
    try:
        import MetaTrader5 as mt5
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("pip install MetaTrader5 (Windows only)") from e
    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
    return mt5


def mt5_rates(symbol: str, tf: str, count: int = 1500, drop_forming: bool = True) -> pd.DataFrame:
    mt5 = _mt5()
    code = getattr(mt5, f"TIMEFRAME_{tf}")
    rates = mt5.copy_rates_from_pos(symbol, code, 0, count)
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"no data for {symbol} {tf}: {mt5.last_error()}")
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df = df.set_index("time").rename(columns={"tick_volume": "volume"})[["open", "high", "low", "close", "volume"]]
    return df.iloc[:-1] if drop_forming else df           # last row is the still-forming bar


def make_synthetic(n: int = 40000, seed: int = 0, minutes: int = 5, start: str = "2023-01-02",
                   substeps: int = 120) -> pd.DataFrame:
    """Driftless random walk with volatility clustering. Candles are built from a fine intrabar path
    (so wicks are real path excursions). It is pure noise: any 'edge' found on it is a bug."""
    import numpy as np
    rng = np.random.default_rng(seed)
    vol = np.empty(n)
    v = 1.0
    for i in range(n):
        v = 0.98 * v + 0.02 * 1.0 + 0.05 * abs(rng.normal())
        vol[i] = v
    steps = rng.normal(0, 1, (n, substeps)) * (vol[:, None] * 0.0002 / np.sqrt(substeps))
    path = np.cumsum(steps, axis=1)                                  # log-return inside each bar
    end = np.cumsum(path[:, -1])                                      # log close of each bar
    start_lvl = np.r_[0.0, end[:-1]]
    full = start_lvl[:, None] + path
    base = 1.1
    open_ = base * np.exp(start_lvl)
    close = base * np.exp(end)
    high = base * np.exp(np.maximum(full.max(axis=1), start_lvl))
    low = base * np.exp(np.minimum(full.min(axis=1), start_lvl))
    idx = pd.date_range(start, periods=n, freq=f"{minutes}min", name="time")
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close,
                         "volume": rng.integers(50, 500, n).astype(float)}, index=idx)
