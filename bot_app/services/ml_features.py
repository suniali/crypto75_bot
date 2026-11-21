import numpy as np
import pandas as pd

from bot_app.services.order_block_rsi_strategy import (
    compute_atr,
    compute_rsi,
    detect_rsi_divergence,
    find_pivot_highs,
    find_pivot_lows,
    find_valid_order_blocks,
)

SL_ATR_MULT = 1.5
TP_ATR_MULT = 2.5


def extract_features_for_ml(df: pd.DataFrame) -> pd.DataFrame:
    """
    تبدیل دیتافریم کندل‌ها (مرتب‌شده از قدیم به جدید) به ویژگی‌های عددی برای مدل ML.
    """
    data = df.reset_index(drop=True).copy()
    for col in ['open_price', 'high_price', 'low_price', 'close_price', 'volume']:
        data[col] = data[col].astype(float)

    data['rsi'] = compute_rsi(data['close_price'], period=14)
    data['atr'] = compute_atr(data, period=14)

    data['price_change'] = data['close_price'].pct_change() * 100.0
    data['body_size'] = (data['close_price'] - data['open_price']).abs()
    data['candle_range'] = data['high_price'] - data['low_price']

    data['sma_50'] = data['close_price'].rolling(window=50).mean()
    data['sma_200'] = data['close_price'].rolling(window=200).mean()
    data['trend_direction'] = np.where(data['sma_50'] > data['sma_200'], 1.0, -1.0)

    # ویژگی‌های واگرایی RSI و اردربلاک برای هر کندل (بدون نگاه به آینده)
    highs = data['high_price'].values
    lows = data['low_price'].values
    closes = data['close_price'].values
    rsi_vals = data['rsi'].values
    atr_vals = data['atr'].values

    pivot_highs = find_pivot_highs(highs, left=3, right=3)
    pivot_lows = find_pivot_lows(lows, left=3, right=3)

    div_signal_col = np.zeros(len(data), dtype=float)     # 1 = Bullish Div, -1 = Bearish Div, 0 = None
    div_strength_col = np.zeros(len(data), dtype=float)
    ob_proximity_col = np.zeros(len(data), dtype=float)   # 1 = نزدیک Bullish OB, -1 = نزدیک Bearish OB, 0 = دور

    for i in range(50, len(data)):
        div = detect_rsi_divergence(
            closes=closes[: i + 1],
            highs=highs[: i + 1],
            lows=lows[: i + 1],
            rsi=rsi_vals[: i + 1],
            pivot_highs=[p for p in pivot_highs if p <= i],
            pivot_lows=[p for p in pivot_lows if p <= i],
            current_idx=i,
            lookback_bars=40,
        )
        if div['direction'] == 'BULLISH':
            div_signal_col[i] = 1.0
            div_strength_col[i] = float(div['strength'])
        elif div['direction'] == 'BEARISH':
            div_signal_col[i] = -1.0
            div_strength_col[i] = float(div['strength'])

        # بررسی فاصله تا نزدیک‌ترین OB در 40 کندل اخیر
        obs = find_valid_order_blocks(
            df=data.iloc[: i + 1],
            pivot_highs=[p for p in pivot_highs if p <= i],
            pivot_lows=[p for p in pivot_lows if p <= i],
            atr=atr_vals[: i + 1],
            max_age_bars=40,
        )
        cur_atr = atr_vals[i] if not np.isnan(atr_vals[i]) and atr_vals[i] > 0 else 1.0
        for ob in reversed(obs):
            if ob['type'] == 'BULLISH' and abs(closes[i] - ob['mid']) <= (1.0 * cur_atr):
                ob_proximity_col[i] = 1.0
                break
            if ob['type'] == 'BEARISH' and abs(closes[i] - ob['mid']) <= (1.0 * cur_atr):
                ob_proximity_col[i] = -1.0
                break

    data['div_signal'] = div_signal_col
    data['div_strength'] = div_strength_col
    data['ob_proximity'] = ob_proximity_col

    data.dropna(inplace=True)
    data.reset_index(drop=True, inplace=True)
    return data


def create_ml_labels(
    df: pd.DataFrame,
    forward_periods: int = 8,
    min_move_usd: float = 3.0,
    atr_mult_target: float = 1.2,
) -> list[int]:
    """
    برچسب‌گذاری باینری (1 = حرکت سودده فراتر از هزینه/نوسان، 0 = غیر آن)
    متناسب با حرکت واقعی انس طلا (مبتنی بر ماکزیمم بین آستانه دلاری و ضریب ATR).
    """
    closes = df['close_price'].astype(float).values
    highs = df['high_price'].astype(float).values
    lows = df['low_price'].astype(float).values
    atr_vals = df['atr'].astype(float).values if 'atr' in df.columns else np.full(len(df), min_move_usd)

    labels = []
    n = len(closes)

    for i in range(n):
        if i + forward_periods >= n:
            labels.append(0)
            continue

        target_move = max(float(min_move_usd), float(atr_vals[i]) * atr_mult_target)
        future_high = float(np.max(highs[i + 1 : i + 1 + forward_periods]))
        future_low = float(np.min(lows[i + 1 : i + 1 + forward_periods]))
        cur_close = float(closes[i])

        up_move = future_high - cur_close
        down_move = cur_close - future_low

        if up_move >= target_move or down_move >= target_move:
            labels.append(1)
        else:
            labels.append(0)

    return labels