import numpy as np
import pandas as pd


def calculate_rsi(series, period=14):
    """محاسبه اندیکاتور RSI"""
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()

    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


def calculate_atr(df, period=14):
    """محاسبه Average True Range (ATR) برای سنجش نوسان و تعیین حد ضرر"""
    high_low = df['high_price'] - df['low_price']
    high_close = np.abs(df['high_price'] - df['close_price'].shift())
    low_close = np.abs(df['low_price'] - df['close_price'].shift())

    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = np.max(ranges, axis=1)
    atr = true_range.rolling(window=period).mean()
    return atr


def extract_features_for_ml(df):
    """تابع اصلی برای تبدیل داده‌های خام کندل به ویژگی‌های قابل فهم برای مدل ML

    df شامل ستون‌های: open, high, low, close, volume است.
    """
    # کپی داده‌ها برای جلوگیری از هشدار SettingWithCopy
    data = df.copy()

    # ۱. محاسبه اندیکاتورها
    data['rsi'] = calculate_rsi(data['close_price'], period=14)
    data['atr'] = calculate_atr(data, period=14)

    # ۲. ویژگی‌های مومنتوم و تغییرات قیمت (Price Action Features)
    data['price_change'] = data['close_price'].pct_change() * 100
    data['body_size'] = (data['close_price'] - data['open_price']).abs()
    data['candle_range'] = data['high_price'] - data['low_price']

    # ۳. تشخیص ساده روند با میانگین متحرک (Moving Average Trend Filter)
    data['sma_50'] = data['close_price'].rolling(window=50).mean()
    data['sma_200'] = data['close_price'].rolling(window=200).mean()

    # فیلتر روند: 1 یعنی روند صعودی، -1 یعنی نزولی
    data['trend_direction'] = np.where(
        data['sma_50'] > data['sma_200'],
        1,
        -1,
    )

    # ۴. پاکسازی مقادیر خالی ناشی از رولینگ
    data.dropna(inplace=True)

    feature_cols = [
      'rsi',
      'atr',
      'price_change',
      'body_size',
      'candle_range',
      'trend_direction',
    ]
    for col in feature_cols:
        if col in data.columns:
            data[col] = data[col].astype(float)

    return data


def create_ml_labels(df, forward_periods=5, pips_threshold=0.0020):
    """برچسب‌گذاری داده‌ها (Labeling) به همراه تطابق کامل طول ایندکس"""
    labels = []
    closes = df['close_price'].values

    for i in range(len(closes) - forward_periods):
        current_close = closes[i]
        future_window = closes[i + 1 : i + 1 + forward_periods]
        max_future = np.max(future_window)
        min_future = np.min(future_window)

        if (max_future - current_close) >= pips_threshold and (
            current_close - min_future
        ) < pips_threshold:
            labels.append(1)  # WIN
        else:
            labels.append(0)  # LOSS

    remaining_len = len(df) - len(labels)
    if remaining_len > 0:
        labels.extend([0] * remaining_len)

    return labels