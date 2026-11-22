import numpy as np
import pandas as pd

def atr(df: pd.DataFrame, period: int = 14) -> np.ndarray:
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean().to_numpy()

def hurst(series: np.ndarray) -> float:
    # Simplified Hurst exponent via R/S method (for AI features)
    if len(series) < 100:
        return 0.5
    lags = range(2, 20)
    tau = [np.std(series[lag:] - series[:-lag]) for lag in lags]
    poly = np.polyfit(np.log(lags), np.log(tau), 1)
    return poly[0]