"""Periodic retraining of the NDS AI model (walk-forward, validated, hot-reloaded by the live service).

Why periodic and not "online" (per tick / per trade)?
  NDS setups are rare (a few per week per symbol). Updating a model after every trade would train on a
  handful of noisy samples. Instead: retrain on the full history on a schedule, validate on data the model
  never saw, and write the model file atomically. nds_strategy_service.get_bundle() notices the new file
  (mtime) and starts using it on the next candle - no restart needed.
"""
from __future__ import annotations

import logging
import os
import time

import pandas as pd

from nds_bot.backtest import run_backtest
from nds_bot.config import NDSConfig
from nds_bot.model import MIN_SAMPLES, save_bundle, train_and_evaluate

try:                                    # inside the Django package
    from . import nds_strategy_service as svc
except ImportError:                     # standalone / tests
    import nds_strategy_service as svc

logger = logging.getLogger(__name__)
TRAIN_BARS = 80000                     # ~1 year of M5


def rates_to_ohlc(rates) -> pd.DataFrame:
    """MT5 rates (numpy structured array) -> OHLC DataFrame. The last row (forming candle) is dropped."""
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df = df.set_index("time").rename(columns={"tick_volume": "volume"})
    return df[["open", "high", "low", "close", "volume"]].astype(float).iloc[:-1]


def train_from_ohlc(symbol: str, time_frame: str, ohlc: pd.DataFrame, model_dir: str | None = None) -> dict:
    ltf = svc.norm_tf(time_frame)
    if ltf not in svc.HTF_FOR:
        return {"status": "skipped", "reason": f"unsupported timeframe {time_frame}"}
    cfg = NDSConfig(ltf=ltf, htf=svc.HTF_FOR[ltf])        # must match the live config (see evaluate_nds_strategy)
    trades = run_backtest(ohlc, cfg, symbol=symbol)
    if len(trades) < MIN_SAMPLES:
        return {"status": "not_enough_setups", "setups": len(trades), "needed": MIN_SAMPLES,
                "reason": "AI stays off; the bot runs on the NDS rules only."}

    bundle, rep = train_and_evaluate(trades)
    bundle["meta"].update({"trained_at": pd.Timestamp.now("UTC").isoformat(), "bars": len(ohlc),
                           "symbol": symbol, "timeframe": ltf, "last_candle": str(ohlc.index[-1])})
    path = os.path.join(model_dir or svc.MODEL_DIR, f"nds_model_{symbol}_{ltf}.joblib")
    tmp = path + ".tmp"
    save_bundle(bundle, tmp)
    os.replace(tmp, path)                                  # atomic: the live bot never reads a half file
    return {
        "status": "trained", "path": path, "setups": rep.n_setups, "ai_useful": rep.ai_useful,
        "threshold": rep.threshold, "auc_holdout": rep.auc_holdout,
        "holdout_trades": rep.holdout_base.get("trades"), "holdout_avg_R": rep.holdout_base.get("avg_R"),
        "taken_trades": rep.holdout_taken.get("trades"), "taken_avg_R": rep.holdout_taken.get("avg_R"),
        "all_avg_R": rep.base.get("avg_R"), "all_ci95": rep.base.get("avg_R_ci95"),
    }


def format_summary(symbol: str, time_frame: str, r: dict) -> str:
    head = f"🧠 آموزش مجدد NDS | {symbol} {time_frame}\n"
    if r["status"] != "trained":
        return head + f"وضعیت: {r['status']} | {r.get('reason', '')} (ستاپ‌ها: {r.get('setups', '-')})"
    f = lambda x: "-" if x is None or x != x else f"{x:.2f}"
    verdict = ("✅ AI روی داده‌ی دیده‌نشده بهتر بود؛ فیلتر فعال است." if r["ai_useful"]
               else "⚪ AI ثابت نشد؛ فقط قوانین NDS (P(win) صرفاً اطلاعاتی).")
    return (head + f"ستاپ‌ها: {r['setups']} | AUC (holdout): {f(r['auc_holdout'])}\n"
            f"holdout: همه {r['holdout_trades']} معامله avgR={f(r['holdout_avg_R'])} | "
            f"انتخاب‌شده {r['taken_trades']} معامله avgR={f(r['taken_avg_R'])}\n"
            f"کل تاریخچه: avgR={f(r['all_avg_R'])} بازه‌ی ۹۵٪: ({f(r['all_ci95'][0])}, {f(r['all_ci95'][1])})\n" + verdict)


def fetch_history(symbol: str, time_frame: str, bars: int = TRAIN_BARS,
                  _mt5=None, _session=None, _tf_map=None, _sleep=time.sleep):
    """Robust history download for training. Returns (rates, message).

    Differences from get_rates_data(): selects the symbol in Market Watch, uses a strict timeframe lookup
    (get_rates_data silently falls back to M30), retries while the terminal is still downloading history,
    falls back to smaller bar counts, and logs mt5.last_error(). The global MT5 lock is released between
    attempts so the live bot is not blocked."""
    if _mt5 is None:
        import MetaTrader5 as _mt5
        from .mt5_service import mt5_session as _session
        from bot_app.utils.time_frame_helper import TIMEFRAME_TO_MT5_TIMEFRAME as _tf_map
    if time_frame not in _tf_map:
        return None, f"timeframe {time_frame!r} is not in TIMEFRAME_TO_MT5_TIMEFRAME ({list(_tf_map)})"
    tf = _tf_map[time_frame]

    last_err = None
    for n in sorted({bars, 50000, 20000, 10000, 5000}, reverse=True):
        if n > bars:
            continue
        for attempt in range(3):
            with _session() as ok:
                if not ok:
                    return None, "no MT5 connection"
                if not _mt5.symbol_select(symbol, True):
                    return None, f"symbol_select({symbol}) failed: {_mt5.last_error()} (name not in THIS terminal?)"
                rates = _mt5.copy_rates_from_pos(symbol, tf, 0, n)
                last_err = _mt5.last_error()
            got = 0 if rates is None else len(rates)
            logger.info(f"NDS fetch {symbol} {time_frame}: asked {n}, got {got}, last_error={last_err}")
            if got >= 2000:
                return rates, f"{got} candles"
            _sleep(2)                                      # terminal may still be syncing history
    return None, f"not enough history (need >= 2000 candles); last_error={last_err}"


def retrain_nds_model(symbol: str, time_frame: str, bars: int = TRAIN_BARS, notify: bool = True) -> dict:
    """Scheduler / management-command entry point."""
    rates, msg = fetch_history(symbol, time_frame, bars)
    if rates is None:
        logger.error(f"NDS retrain: no data for {symbol} {time_frame}: {msg}")
        return {"status": "no_data", "reason": msg}
    result = train_from_ohlc(symbol, time_frame, rates_to_ohlc(rates))
    text = format_summary(symbol, time_frame, result)
    logger.info(text)
    if notify:
        try:
            from .telegram_service import send_telegram_text   # <-- real module path of your telegram functions
            send_telegram_text(text)
        except ImportError:
            logger.warning("send_telegram_text import path is wrong - fix it in nds_trainer.retrain_nds_model")
    return result