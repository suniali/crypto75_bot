"""Candle loading: CSV files or MetaTrader 5 (Windows, `pip install MetaTrader5`)."""
from __future__ import annotations

import pandas as pd


def load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, sep=None, engine="python")
    df.columns = [str(c).strip().lower().strip("<>") for c in df.columns]
    if "time" in df.columns and "date" in df.columns:          # MT5 export: DATE + TIME
        t = pd.to_datetime(df["date"].astype(str) + " " + df["time"].astype(str))
    else:
        col = next(c for c in ("time", "datetime", "date", "timestamp") if c in df.columns)
        t = pd.to_datetime(df[col])
    if "volume" not in df.columns:
        df["volume"] = df["tickvol"] if "tickvol" in df.columns else df.get("tick_volume", 1.0)
    out = pd.DataFrame({"open": df["open"], "high": df["high"], "low": df["low"],
                        "close": df["close"], "volume": df["volume"]}).astype(float)
    out.index = pd.DatetimeIndex(t, name="time")
    return out[~out.index.duplicated()].sort_index()


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
