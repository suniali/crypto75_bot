import numpy as np
import pandas as pd


def compute_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """محاسبه اندیکاتور RSI با روش Wilder's smoothing"""
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta.clip(upper=0.0))

    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi.fillna(50.0)


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """محاسبه ATR برای مدیریت ریسک متناسب با نوسان نماد (مناسب برای XAUUSD)"""
    high = df['high_price'].astype(float)
    low = df['low_price'].astype(float)
    close = df['close_price'].astype(float)

    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()

    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window=period, min_periods=period).mean()


def find_pivot_highs(highs: np.ndarray, left: int = 3, right: int = 3) -> list[int]:
    """یافتن ایندکس سقف‌های نوسانی تاییدشده"""
    pivots = []
    n = len(highs)
    for i in range(left, n - right):
        window = highs[i - left : i + right + 1]
        if highs[i] == np.max(window) and np.sum(window == highs[i]) == 1:
            pivots.append(i)
    return pivots


def find_pivot_lows(lows: np.ndarray, left: int = 3, right: int = 3) -> list[int]:
    """یافتن ایندکس کف‌های نوسانی تاییدشده"""
    pivots = []
    n = len(lows)
    for i in range(left, n - right):
        window = lows[i - left : i + right + 1]
        if lows[i] == np.min(window) and np.sum(window == lows[i]) == 1:
            pivots.append(i)
    return pivots


def detect_rsi_divergence(
    closes: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    rsi: np.ndarray,
    pivot_highs: list[int],
    pivot_lows: list[int],
    current_idx: int,
    lookback_bars: int = 45,
) -> dict:
    """
    بررسی واگرایی صعودی و نزولی RSI تا کندل current_idx.
    خروجی:
      direction: 'BULLISH' | 'BEARISH' | None
      div_type: 'REGULAR' | 'HIDDEN' | None
      strength: float (0.0 .. 1.0)
    """
    # 1. واگرایی صعودی (برای سناریوی BUY)
    recent_lows = [idx for idx in pivot_lows if idx <= current_idx and (current_idx - idx) <= lookback_bars]
    if len(recent_lows) >= 2:
        p1, p2 = recent_lows[-2], recent_lows[-1]
        price_p1, price_p2 = lows[p1], lows[p2]
        rsi_p1, rsi_p2 = rsi[p1], rsi[p2]

        # واگرایی منظم صعودی: کف قیمت پایین‌تر، کف RSI بالاتر
        if price_p2 < price_p1 and rsi_p2 > rsi_p1:
            rsi_diff = float(rsi_p2 - rsi_p1)
            strength = min(1.0, max(0.35, rsi_diff / 12.0))
            return {
                'direction': 'BULLISH',
                'div_type': 'REGULAR',
                'strength': round(strength, 3),
                'pivot_prev': int(p1),
                'pivot_curr': int(p2),
            }

        # واگرایی مخفی صعودی: کف قیمت بالاتر، کف RSI پایین‌تر (ادامه روند صعودی)
        if price_p2 > price_p1 and rsi_p2 < rsi_p1 and closes[current_idx] > closes[p2]:
            rsi_diff = float(rsi_p1 - rsi_p2)
            strength = min(1.0, max(0.30, rsi_diff / 12.0))
            return {
                'direction': 'BULLISH',
                'div_type': 'HIDDEN',
                'strength': round(strength, 3),
                'pivot_prev': int(p1),
                'pivot_curr': int(p2),
            }

    # 2. واگرایی نزولی (برای سناریوی SELL)
    recent_highs = [idx for idx in pivot_highs if idx <= current_idx and (current_idx - idx) <= lookback_bars]
    if len(recent_highs) >= 2:
        h1, h2 = recent_highs[-2], recent_highs[-1]
        price_h1, price_h2 = highs[h1], highs[h2]
        rsi_h1, rsi_h2 = rsi[h1], rsi[h2]

        # واگرایی منظم نزولی: سقف قیمت بالاتر، سقف RSI پایین‌تر
        if price_h2 > price_h1 and rsi_h2 < rsi_h1:
            rsi_diff = float(rsi_h1 - rsi_h2)
            strength = min(1.0, max(0.35, rsi_diff / 12.0))
            return {
                'direction': 'BEARISH',
                'div_type': 'REGULAR',
                'strength': round(strength, 3),
                'pivot_prev': int(h1),
                'pivot_curr': int(h2),
            }

        # واگرایی مخفی نزولی: سقف قیمت پایین‌تر، سقف RSI بالاتر (ادامه روند نزولی)
        if price_h2 < price_h1 and rsi_h2 > rsi_h1 and closes[current_idx] < closes[h2]:
            rsi_diff = float(rsi_h2 - rsi_h1)
            strength = min(1.0, max(0.30, rsi_diff / 12.0))
            return {
                'direction': 'BEARISH',
                'div_type': 'HIDDEN',
                'strength': round(strength, 3),
                'pivot_prev': int(h1),
                'pivot_curr': int(h2),
            }

    return {'direction': None, 'div_type': None, 'strength': 0.0}


def find_valid_order_blocks(
    df: pd.DataFrame,
    pivot_highs: list[int],
    pivot_lows: list[int],
    atr: np.ndarray,
    max_age_bars: int = 80,
) -> list[dict]:
    """
    تشخیص اردربلاک‌های معتبر صعودی و نزولی همراه با شکست ساختار (BoS)
    و بررسی عدم شکست کامل اردربلاک تا کندل جاری.
    """
    opens = df['open_price'].astype(float).values
    highs = df['high_price'].astype(float).values
    lows = df['low_price'].astype(float).values
    closes = df['close_price'].astype(float).values

    n = len(df)
    last_idx = n - 1
    order_blocks = []

    start_search = max(5, last_idx - max_age_bars)

    # جستجو برای Bullish Order Block (آخرین کندل نزولی پیش از شکست سقف قبلی با مومنتوم)
    for i in range(start_search, last_idx - 1):
        is_bearish_candle = closes[i] < opens[i]
        if not is_bearish_candle:
            continue

        # بررسی حرکت صعودی پس از کندل i
        impulse_high = np.max(highs[i + 1 : min(n, i + 8)])
        prev_swing_highs = [h for h in pivot_highs if h < i and (i - h) <= 40]
        broke_structure = False
        if prev_swing_highs:
            ref_high = highs[prev_swing_highs[-1]]
            broke_structure = impulse_high > ref_high

        atr_i = atr[i] if not np.isnan(atr[i]) and atr[i] > 0 else (highs[i] - lows[i])
        impulse_size = impulse_high - lows[i]
        strong_impulse = impulse_size >= (1.2 * atr_i)

        if broke_structure and strong_impulse:
            ob_low = float(lows[i])
            ob_high = float(highs[i])

            # اگر قیمت بعد از تشکیل OB از کف اردربلاک پایین‌تر رفته باشد، OB بی‌اعتبار است
            invalidated = np.any(lows[i + 1 : last_idx + 1] < ob_low)
            if not invalidated:
                order_blocks.append({
                    'type': 'BULLISH',
                    'index': int(i),
                    'low': ob_low,
                    'high': ob_high,
                    'mid': (ob_low + ob_high) / 2.0,
                    'age': int(last_idx - i),
                })

    # جستجو برای Bearish Order Block (آخرین کندل صعودی پیش از شکست کف قبلی با مومنتوم)
    for i in range(start_search, last_idx - 1):
        is_bullish_candle = closes[i] > opens[i]
        if not is_bullish_candle:
            continue

        impulse_low = np.min(lows[i + 1 : min(n, i + 8)])
        prev_swing_lows = [l for l in pivot_lows if l < i and (i - l) <= 40]
        broke_structure = False
        if prev_swing_lows:
            ref_low = lows[prev_swing_lows[-1]]
            broke_structure = impulse_low < ref_low

        atr_i = atr[i] if not np.isnan(atr[i]) and atr[i] > 0 else (highs[i] - lows[i])
        impulse_size = highs[i] - impulse_low
        strong_impulse = impulse_size >= (1.2 * atr_i)

        if broke_structure and strong_impulse:
            ob_low = float(lows[i])
            ob_high = float(highs[i])

            # اگر قیمت بعد از تشکیل OB از سقف اردربلاک بالاتر رفته باشد، OB بی‌اعتبار است
            invalidated = np.any(highs[i + 1 : last_idx + 1] > ob_high)
            if not invalidated:
                order_blocks.append({
                    'type': 'BEARISH',
                    'index': int(i),
                    'low': ob_low,
                    'high': ob_high,
                    'mid': (ob_low + ob_high) / 2.0,
                    'age': int(last_idx - i),
                })

    return order_blocks


def evaluate_ob_rsi_strategy(
    df: pd.DataFrame,
    sl_atr_mult: float = 1.5,
    rr_ratio: float = 2.0,
    zone_buffer_atr: float = 0.25,
) -> dict:
    """
    ارزیابی نهایی استراتژی اردربلاک معتبر + واگرایی RSI روی آخرین کندل بسته‌شده.
    دیتافریم ورودی باید به ترتیب صعودی زمان (قدیمی به جدید) باشد.
    """
    if len(df) < 60:
        return {'signal': 'HOLD', 'setup_score': 0.0, 'reason': 'تعداد کندل کافی نیست.'}

    work_df = df.reset_index(drop=True).copy()
    opens = work_df['open_price'].astype(float).values
    highs = work_df['high_price'].astype(float).values
    lows = work_df['low_price'].astype(float).values
    closes = work_df['close_price'].astype(float).values

    rsi_series = compute_rsi(work_df['close_price'].astype(float), period=14)
    atr_series = compute_atr(work_df, period=14)

    rsi = rsi_series.values
    atr = atr_series.values

    last_idx = len(work_df) - 1
    current_price = float(closes[last_idx])
    current_low = float(lows[last_idx])
    current_high = float(highs[last_idx])
    current_rsi = float(rsi[last_idx])
    current_atr = float(atr[last_idx]) if not np.isnan(atr[last_idx]) and atr[last_idx] > 0 else float(np.mean(highs[-14:] - lows[-14:]))

    pivot_highs = find_pivot_highs(highs, left=3, right=3)
    pivot_lows = find_pivot_lows(lows, left=3, right=3)

    div_info = detect_rsi_divergence(
        closes=closes,
        highs=highs,
        lows=lows,
        rsi=rsi,
        pivot_highs=pivot_highs,
        pivot_lows=pivot_lows,
        current_idx=last_idx,
        lookback_bars=45,
    )

    obs = find_valid_order_blocks(
        df=work_df,
        pivot_highs=pivot_highs,
        pivot_lows=pivot_lows,
        atr=atr,
        max_age_bars=80,
    )

    buffer = zone_buffer_atr * current_atr

    # بررسی ستاپ صعودی (BUY): Bullish OB + واگرایی صعودی RSI + کندل صعودی یا رد شدن از ناحیه
    if div_info['direction'] == 'BULLISH':
        bullish_obs = [ob for ob in obs if ob['type'] == 'BULLISH']
        for ob in reversed(bullish_obs):
            in_zone = (current_low <= ob['high'] + buffer) and (current_high >= ob['low'] - buffer)
            bullish_candle = closes[last_idx] >= opens[last_idx]

            if in_zone and bullish_candle:
                raw_sl = min(ob['low'] - (0.35 * current_atr), current_price - (sl_atr_mult * current_atr))
                # محدود کردن فاصله SL برای جلوگیری از حدضرر بیش‌ازحد بزرگ
                max_sl_dist = max(sl_atr_mult * current_atr, (current_price - ob['low']) + 0.35 * current_atr)
                sl_dist = min(max_sl_dist, abs(current_price - raw_sl))
                if sl_dist <= 0:
                    sl_dist = sl_atr_mult * current_atr

                stop_loss = current_price - sl_dist
                take_profit = current_price + (sl_dist * rr_ratio)

                setup_score = min(0.98, 0.55 + (0.25 * div_info['strength']) + (0.15 if div_info['div_type'] == 'REGULAR' else 0.08))
                return {
                    'signal': 'BUY',
                    'setup_type': f"BULLISH_OB_{div_info['div_type']}_DIV",
                    'setup_score': round(float(setup_score), 4),
                    'rsi': round(current_rsi, 2),
                    'atr': round(current_atr, 5),
                    'entry_price': round(current_price, 5),
                    'stop_loss': round(stop_loss, 5),
                    'take_profit': round(take_profit, 5),
                    'ob_low': ob['low'],
                    'ob_high': ob['high'],
                    'divergence': div_info,
                    'reason': 'واگرایی صعودی RSI هم‌زمان با واکنش قیمت به Bullish Order Block معتبر.',
                }

    # بررسی ستاپ نزولی (SELL): Bearish OB + واگرایی نزولی RSI + کندل نزولی یا رد شدن از ناحیه
    if div_info['direction'] == 'BEARISH':
        bearish_obs = [ob for ob in obs if ob['type'] == 'BEARISH']
        for ob in reversed(bearish_obs):
            in_zone = (current_high >= ob['low'] - buffer) and (current_low <= ob['high'] + buffer)
            bearish_candle = closes[last_idx] <= opens[last_idx]

            if in_zone and bearish_candle:
                raw_sl = max(ob['high'] + (0.35 * current_atr), current_price + (sl_atr_mult * current_atr))
                max_sl_dist = max(sl_atr_mult * current_atr, (ob['high'] - current_price) + 0.35 * current_atr)
                sl_dist = min(max_sl_dist, abs(raw_sl - current_price))
                if sl_dist <= 0:
                    sl_dist = sl_atr_mult * current_atr

                stop_loss = current_price + sl_dist
                take_profit = current_price - (sl_dist * rr_ratio)

                setup_score = min(0.98, 0.55 + (0.25 * div_info['strength']) + (0.15 if div_info['div_type'] == 'REGULAR' else 0.08))
                return {
                    'signal': 'SELL',
                    'setup_type': f"BEARISH_OB_{div_info['div_type']}_DIV",
                    'setup_score': round(float(setup_score), 4),
                    'rsi': round(current_rsi, 2),
                    'atr': round(current_atr, 5),
                    'entry_price': round(current_price, 5),
                    'stop_loss': round(stop_loss, 5),
                    'take_profit': round(take_profit, 5),
                    'ob_low': ob['low'],
                    'ob_high': ob['high'],
                    'divergence': div_info,
                    'reason': 'واگرایی نزولی RSI هم‌زمان با واکنش قیمت به Bearish Order Block معتبر.',
                }

    return {
        'signal': 'HOLD',
        'setup_type': 'NONE',
        'setup_score': 0.0,
        'rsi': round(current_rsi, 2),
        'atr': round(current_atr, 5),
        'divergence': div_info,
        'active_obs_count': len(obs),
        'reason': 'هم‌زمانی واگرایی RSI معتبر و ورود قیمت به اردربلاک معتبر مشاهده نشد.',
    }