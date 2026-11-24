from __future__ import annotations

import glob
import os
import pandas as pd

from nds_bot.config import NDSConfig
from nds_bot.data import mt5_rates,load_csv
from nds_bot.structure import resample_ohlc
from nds_bot.backtest import run_backtest
from nds_bot.model import MIN_SAMPLES, save_bundle, train_and_evaluate

def load_histdata_folder(folder_path: str) -> pd.DataFrame:
    """خواندن، یکپارچه‌سازی و مرتب‌سازی تمام فایل‌های CSV/TXT چندین سال از یک پوشه."""
    files = sorted(
        glob.glob(os.path.join(folder_path, "*.csv"))
        + glob.glob(os.path.join(folder_path, "*.txt"))
    )
    if not files:
        raise FileNotFoundError(f"❌ هیچ فایل CSV یا TXT در پوشه {folder_path} پیدا نشد.")

    dfs = []
    for fpath in files:
        print(f"   📄 در حال خواندن: {os.path.basename(fpath)}")
        with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
            first_line = f.readline().strip()

        sep = ";" if ";" in first_line else ","
        has_header = any(ch.isalpha() for ch in first_line.split(sep)[0])

        if has_header:
            df = pd.read_csv(fpath, sep=sep)
            df.columns = [c.strip().lower() for c in df.columns]
            if "date" in df.columns and "time" in df.columns:
                dt = pd.to_datetime(df["date"].astype(str) + " " + df["time"].astype(str))
            else:
                time_col = df.columns[0]
                dt = pd.to_datetime(df[time_col])
            sub = pd.DataFrame({
                "open": df["open"].astype(float),
                "high": df["high"].astype(float),
                "low": df["low"].astype(float),
                "close": df["close"].astype(float),
                "volume": df["volume"].astype(float) if "volume" in df.columns else 1.0,
            })
            sub.index = pd.DatetimeIndex(dt, name="time")
        else:
            raw = pd.read_csv(fpath, sep=sep, header=None)
            # فرمت DAT_MT: تاریخ و ساعت در دو ستون اول جدا هستند (YYYY.MM.DD , HH:MM)
            if raw.shape[1] >= 7 and ":" in str(raw.iloc[0, 1]):
                dt_str = raw.iloc[:, 0].astype(str).str.strip() + " " + raw.iloc[:, 1].astype(str).str.strip()
                dt = pd.to_datetime(dt_str, format="%Y.%m.%d %H:%M", errors="coerce")
                if dt.isna().all():
                    dt = pd.to_datetime(dt_str, format="%Y.%m.%d %H:%M:%S", errors="coerce")
                sub = pd.DataFrame({
                    "open": raw.iloc[:, 2].astype(float),
                    "high": raw.iloc[:, 3].astype(float),
                    "low": raw.iloc[:, 4].astype(float),
                    "close": raw.iloc[:, 5].astype(float),
                    "volume": raw.iloc[:, 6].fillna(1.0).astype(float),
                })
            else:
                # فرمت DAT_ASCII: تاریخ و ساعت در یک ستون هستند
                dt_str = raw.iloc[:, 0].astype(str).str.strip()
                dt = pd.to_datetime(dt_str, format="%Y%m%d %H%M%S", errors="coerce")
                if dt.isna().all():
                    dt = pd.to_datetime(dt_str, format="%Y.%m.%d %H:%M", errors="coerce")
                if dt.isna().all():
                    dt = pd.to_datetime(dt_str, errors="coerce")
                sub = pd.DataFrame({
                    "open": raw.iloc[:, 1].astype(float),
                    "high": raw.iloc[:, 2].astype(float),
                    "low": raw.iloc[:, 3].astype(float),
                    "close": raw.iloc[:, 4].astype(float),
                    "volume": raw.iloc[:, 5].fillna(1.0).astype(float) if raw.shape[1] > 5 else 1.0,
                })
            sub.index = pd.DatetimeIndex(dt, name="time")

        sub = sub[~sub.index.isna()]
        dfs.append(sub)

    full_df = pd.concat(dfs).sort_index()
    full_df = full_df[~full_df.index.duplicated(keep="last")]
    full_df["volume"] = full_df["volume"].replace(0, 1.0)
    return full_df


def train_from_folder(
    folder_path: str = "data",
    symbol: str = "XAUUSD_l",
    model_dir: str = "models",
    min_setups: int = 80,
):
    print(f"1. تجمیع فایل‌های چندساله از پوشه: {folder_path}...")
    ltf = load_histdata_folder(folder_path)
    print(f"   ✅ کل کندل‌های M1 یکپارچه‌شده: {len(ltf):,} ({ltf.index[0]} تا {ltf.index[-1]})")

    htf = resample_ohlc(ltf, 30)
    cfg = NDSConfig(ltf="M1", htf="M30")

    print("2. استخراج ستاپ‌های NDS (با اولویت سکانس و خروج در نقاط ۱ و ۲ باز)...")
    trades_df = run_backtest(ltf, cfg, htf=htf, model=None, symbol=symbol)
    n_setups = len(trades_df)
    print(f"   ✅ تعداد کل ستاپ‌های استخراج‌شده: {n_setups}")

    if n_setups < min_setups:
        print(f"❌ تعداد ستاپ‌ها ({n_setups}) کمتر از حد نصاب ({min_setups}) است.")
        return None

    print("3. ارزیابی Walk-Forward و آموزش مدل فیلتر...")
    bundle, rep = train_and_evaluate(trades_df)
    bundle["meta"].update({
        "trained_at": pd.Timestamp.now("UTC").isoformat(),
        "bars": len(ltf),
        "symbol": symbol,
        "timeframe": "M1",
        "htf": "M30",
        "train_setups": n_setups,
        "date_range": f"{ltf.index[0]} -> {ltf.index[-1]}",
    })

    os.makedirs(model_dir, exist_ok=True)
    out_path = os.path.join(model_dir, f"nds_model_{symbol}_M1.joblib")
    tmp_path = out_path + ".tmp"
    save_bundle(bundle, tmp_path)
    os.replace(tmp_path, out_path)

    print(f"\n🎉 مدل با موفقیت روی دیتای چندساله آموزش دید و ذخیره شد: {out_path}")
    print(f"   AI Useful: {rep.ai_useful} | Threshold: {rep.threshold:.2f} | Holdout AUC: {rep.auc_holdout:.3f}")
    return bundle

def train_ai_model(
    symbol: str = "XAUUSD_l",
    bars: int = 80000,
    model_dir: str = "models",
):
    print(f"1. دریافت داده از متاتریدر برای {symbol} (تعداد کندل M1: {bars})...")
    ltf = mt5_rates(symbol, "M1", bars)
    htf = resample_ohlc(ltf, 30)

    cfg = NDSConfig(ltf="M1", htf="M30")
    print("2. استخراج ستاپ‌ها با اولویت سکانس و خروج در نقاط ۱ و ۲ باز...")
    trades_df = run_backtest(ltf, cfg, htf=htf, model=None, symbol=symbol)
    print(f"   تعداد ستاپ‌های استخراج‌شده: {len(trades_df)}")

    if len(trades_df) < max(60, MIN_SAMPLES // 2):
        print("❌ تعداد ستاپ‌ها برای آموزش مدل کافی نیست. تعداد کندل بیشتری بفرستید.")
        return None

    print("3. ارزیابی Walk-Forward و آموزش مدل فیلتر...")
    bundle, rep = train_and_evaluate(trades_df)
    bundle["meta"].update({
        "trained_at": pd.Timestamp.now("UTC").isoformat(),
        "bars": len(ltf),
        "symbol": symbol,
        "timeframe": "M1",
        "htf": "M30",
        "last_candle": str(ltf.index[-1]),
    })

    os.makedirs(model_dir, exist_ok=True)
    out_path = os.path.join(model_dir, f"nds_model_{symbol}_M1.joblib")
    tmp_path = out_path + ".tmp"
    save_bundle(bundle, tmp_path)
    os.replace(tmp_path, out_path)

    print(f"✅ مدل با موفقیت ذخیره شد: {out_path}")
    print(f"   AI Useful: {rep.ai_useful} | Threshold: {rep.threshold:.2f} | Holdout AUC: {rep.auc_holdout:.3f}")
    return bundle


if __name__ == "__main__":
    # train_ai_model()
    train_from_folder(folder_path="data/", symbol="XAUUSD")