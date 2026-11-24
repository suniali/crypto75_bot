import os
import django
import numpy as np
import pandas as pd

# 1. تنظیمات محیط جنگو
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
if not django.apps.apps.ready:
    django.setup()

# 2. ایمپورت‌ها (بدون TradeDecisionLog و notification_service)
from bot_app.models import MarketCandle
from bot_app.services.nds_strategy_service import rows_to_ohlc, NDSConfig
from nds_bot.structure import make_bars, raw_pivots, PivotTracker, group_by_confirm, detect_setup, htf_states


def _evaluate_setup_outcome(B, start_idx: int, direction: int, entry: float, sl: float, tp: float) -> dict:
    """
    بررسی سرنوشت ستاپ روی کندل‌های بعدی تا برخورد به SL یا TP.
    direction: 1 برای BUY و -1 برای SELL
    """
    risk = abs(entry - sl)
    if risk <= 0:
        return {"status": "INVALID", "r": 0.0, "exit_bar": start_idx}

    reward_dist = abs(tp - entry)
    planned_rr = reward_dist / risk if risk > 0 else 0.0

    for idx in range(start_idx + 1, len(B.c)):
        high_i = float(B.h[idx])
        low_i = float(B.l[idx])

        if direction == 1:  # BUY
            hit_sl = low_i <= sl
            hit_tp = high_i >= tp
            # اگر در یک کندل هر دو بخورند، محافظه‌کارانه اول SL را لحاظ می‌کنیم
            if hit_sl and hit_tp:
                return {"status": "LOSS", "r": -1.0, "exit_bar": idx, "planned_rr": planned_rr}
            if hit_sl:
                return {"status": "LOSS", "r": -1.0, "exit_bar": idx, "planned_rr": planned_rr}
            if hit_tp:
                return {"status": "WIN", "r": planned_rr, "exit_bar": idx, "planned_rr": planned_rr}

        elif direction == -1:  # SELL
            hit_sl = high_i >= sl
            hit_tp = low_i <= tp
            if hit_sl and hit_tp:
                return {"status": "LOSS", "r": -1.0, "exit_bar": idx, "planned_rr": planned_rr}
            if hit_sl:
                return {"status": "LOSS", "r": -1.0, "exit_bar": idx, "planned_rr": planned_rr}
            if hit_tp:
                return {"status": "WIN", "r": planned_rr, "exit_bar": idx, "planned_rr": planned_rr}

    # اگر تا پایان داده نه SL بخورد نه TP، بر اساس قیمت بسته شدن آخرین کندل مقدار شناور R را حساب می‌کنیم
    last_close = float(B.c[-1])
    floating_r = ((last_close - entry) * direction) / risk
    return {"status": "OPEN", "r": float(floating_r), "exit_bar": len(B.c) - 1, "planned_rr": planned_rr}


def _max_consecutive(bool_array: np.ndarray) -> int:
    """بیشترین تعداد تکرار متوالی True در آرایه بولین."""
    max_run = 0
    current = 0
    for val in bool_array:
        if val:
            current += 1
            if current > max_run:
                max_run = current
        else:
            current = 0
    return max_run


def compute_backtest_metrics(results: list[dict]) -> dict:
    """محاسبه شاخص‌های دقیق بک‌تست از لیست ستاپ‌های بررسی‌شده."""
    closed = [r for r in results if r["status"] in ("WIN", "LOSS")]
    open_trades = [r for r in results if r["status"] == "OPEN"]

    total_setups = len(results)
    closed_count = len(closed)

    if closed_count == 0:
        return {
            "total_setups": total_setups,
            "closed_trades": 0,
            "open_trades": len(open_trades),
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "loss_rate": 0.0,
            "profit_factor": 0.0,
            "expectancy_r": 0.0,
            "avg_r": 0.0,
            "avg_win_r": 0.0,
            "avg_loss_r": 0.0,
            "total_r": 0.0,
            "max_dd_r": 0.0,
            "max_win_streak": 0,
            "max_loss_streak": 0,
            "payoff_ratio": 0.0,
            "buy_count": sum(1 for r in results if r["direction"] == 1),
            "sell_count": sum(1 for r in results if r["direction"] == -1),
        }

    r_arr = np.array([float(x["r"]) for x in closed], dtype=float)
    wins_mask = r_arr > 0
    losses_mask = r_arr < 0

    wins = int(wins_mask.sum())
    losses = int(losses_mask.sum())
    win_rate = float(wins / closed_count)
    loss_rate = float(losses / closed_count)

    gross_profit_r = float(r_arr[wins_mask].sum()) if wins > 0 else 0.0
    gross_loss_r = float(abs(r_arr[losses_mask].sum())) if losses > 0 else 0.0

    profit_factor = float(gross_profit_r / gross_loss_r) if gross_loss_r > 0 else float("inf")
    avg_win_r = float(r_arr[wins_mask].mean()) if wins > 0 else 0.0
    avg_loss_r = float(abs(r_arr[losses_mask].mean())) if losses > 0 else 0.0
    payoff_ratio = float(avg_win_r / avg_loss_r) if avg_loss_r > 0 else float("inf")

    total_r = float(r_arr.sum())
    avg_r = float(r_arr.mean())
    expectancy_r = float((win_rate * avg_win_r) - (loss_rate * avg_loss_r))

    equity_r = np.cumsum(r_arr)
    peak_r = np.maximum.accumulate(equity_r)
    drawdown_r = peak_r - equity_r
    max_dd_r = float(drawdown_r.max()) if len(drawdown_r) > 0 else 0.0

    return {
        "total_setups": total_setups,
        "closed_trades": closed_count,
        "open_trades": len(open_trades),
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "loss_rate": loss_rate,
        "profit_factor": profit_factor,
        "expectancy_r": expectancy_r,
        "avg_r": avg_r,
        "avg_win_r": avg_win_r,
        "avg_loss_r": avg_loss_r,
        "total_r": total_r,
        "max_dd_r": max_dd_r,
        "max_win_streak": _max_consecutive(wins_mask),
        "max_loss_streak": _max_consecutive(losses_mask),
        "payoff_ratio": payoff_ratio,
        "buy_count": sum(1 for r in results if r["direction"] == 1),
        "sell_count": sum(1 for r in results if r["direction"] == -1),
    }


def print_backtest_report(metrics: dict):
    """چاپ منظم و کامل شاخص‌های بک‌تست."""
    pf_str = "∞" if np.isinf(metrics["profit_factor"]) else f"{metrics['profit_factor']:.2f}"
    pr_str = "∞" if np.isinf(metrics["payoff_ratio"]) else f"{metrics['payoff_ratio']:.2f}"

    print("\n" + "=" * 60)
    print("📊 گزارش کامل ستاپ‌ها و شاخص‌های بک‌تست (بدون ثبت در DB)")
    print("=" * 60)
    print(f"  کل ستاپ‌های پیداشده:     {metrics['total_setups']} (BUY: {metrics['buy_count']} | SELL: {metrics['sell_count']})")
    print(f"  معاملات بسته شده (TP/SL): {metrics['closed_trades']} | باز مانده: {metrics['open_trades']}")
    print(f"  تعداد برد / باخت:         {metrics['wins']} / {metrics['losses']}")
    print(f"  نرخ برد (Win Rate):       {metrics['win_rate'] * 100:.2f}%")
    print(f"  نرخ باخت (Loss Rate):      {metrics['loss_rate'] * 100:.2f}%")
    print(f"  فاکتور سود (PF):          {pf_str}")
    print(f"  نسبت برد به باخت (Payoff): {pr_str}")
    print(f"  میانگین R هر معامله:      {metrics['avg_r']:.3f} R")
    print(f"  میانگین برد / باخت:       +{metrics['avg_win_r']:.2f} R / -{metrics['avg_loss_r']:.2f} R")
    print(f"  انتظار ریاضی (Expectancy): {metrics['expectancy_r']:.3f} R")
    print(f"  مجموع سود/زیان (Total R):  {metrics['total_r']:+.2f} R")
    print(f"  حداکثر افت سرمایه (Max DD): {metrics['max_dd_r']:.2f} R")
    print(f"  بیشترین برد متوالی:       {metrics['max_win_streak']}")
    print(f"  بیشترین باخت متوالی:      {metrics['max_loss_streak']}")
    print("=" * 60)


def _build_htf_from_ltf(ohlc_ltf: pd.DataFrame, rule: str = "30min") -> pd.DataFrame:
    """ساخت کندل‌های تایم‌فریم بالا (M30) از روی دیتافریم LTF."""
    df = ohlc_ltf.sort_index()
    return df.resample(rule).agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna(subset=["open", "high", "low", "close"])


def run_replay(
    ohlc_ltf: pd.DataFrame | None = None,
    ohlc_htf: pd.DataFrame | None = None,
    symbol: str = "XAUUSD_l",
    start_bar: int = 0,
):
    if ohlc_ltf is None or ohlc_htf is None:
        print("⏳ در حال خواندن آخرین داده‌ها از دیتابیس...")

        rows_ltf = list(
            MarketCandle.objects.filter(symbol=symbol, timeframe="M1")
            .order_by('-open_time')
            .values('open_price', 'high_price', 'low_price', 'close_price', 'volume', 'open_time')[:1500]
        )
        rows_htf = list(
            MarketCandle.objects.filter(symbol=symbol, timeframe="M30")
            .order_by('-open_time')
            .values('open_price', 'high_price', 'low_price', 'close_price', 'volume', 'open_time')[:500]
        )

        if not rows_ltf or not rows_htf:
            print("❌ داده‌ای در دیتابیس یافت نشد.")
            return []

        ohlc_ltf = rows_to_ohlc(rows_ltf)
        ohlc_htf = rows_to_ohlc(rows_htf)
        print(f"📊 آخرین کندل M1 در دیتابیس: {ohlc_ltf.index[-1]}")
        print(f"📊 آخرین کندل M30 در دیتابیس: {ohlc_htf.index[-1]}")
    else:
        print("📂 استفاده از داده‌های CSV برای بررسی تمام ستاپ‌ها...")
        ohlc_ltf = ohlc_ltf.sort_index()
        ohlc_htf = ohlc_htf.sort_index()
        print(f"📊 بازه کندل M1 در CSV: {ohlc_ltf.index[0]} → {ohlc_ltf.index[-1]} ({len(ohlc_ltf)} کندل)")
        print(f"📊 بازه کندل M30 در CSV: {ohlc_htf.index[0]} → {ohlc_htf.index[-1]} ({len(ohlc_htf)} کندل)")

    cfg = NDSConfig(ltf="M1", htf="M30")
    B = make_bars(ohlc_ltf, cfg)
    H = make_bars(ohlc_htf, cfg)

    # محاسبه وضعیت HTF در طول کل سری زمانی
    htf_info_list = htf_states(H, cfg, only_last=False)
    if not htf_info_list:
        print("❌ اطلاعات تایم‌فریم بالا (HTF) در دسترس نیست.")
        return []

    by_conf = group_by_confirm(raw_pivots(B.h, B.l, cfg.pivot_ltf))
    tr = PivotTracker()
    test_cfg = NDSConfig(ltf="M1", htf="M30", signal_max_age=5)

    seen_keys = set()
    setups_results = []

    # پیمایش کل کندل‌ها بدون خروج زودهنگام با break
    for c in range(max(0, start_bar), len(B.c)):
        for p in by_conf.get(c, []):
            tr.add(p)

        # نگاشت زمان کندل LTF به آخرین کندل بسته‌شده HTF
        bar_time = B.t[c]
        htf_idx = int(np.searchsorted(H.t, bar_time, side="right")) - 1
        if htf_idx < 0:
            continue
        htf_idx = min(htf_idx, len(htf_info_list) - 1)
        info = htf_info_list[htf_idx]

        if getattr(info, "bias", 0) == 0:
            continue

        s = detect_setup(tr.p, B, c, info, test_cfg, seen_keys)
        if s:
            seen_keys.add(s.key)
            outcome = _evaluate_setup_outcome(
                B=B,
                start_idx=c,
                direction=s.direction,
                entry=float(s.entry),
                sl=float(s.sl),
                tp=float(s.tp),
            )

            record = {
                "bar_index": c,
                "time": B.t[c],
                "direction": s.direction,
                "side": "SELL" if s.direction == -1 else "BUY",
                "entry": float(s.entry),
                "sl": float(s.sl),
                "tp": float(s.tp),
                "status": outcome["status"],
                "r": outcome["r"],
                "key": s.key,
            }
            setups_results.append(record)

            print(
                f"#{len(setups_results)} | {record['time']} | {record['side']} | "
                f"ورود: {record['entry']} | SL: {record['sl']} | TP: {record['tp']} | "
                f"نتیجه: {record['status']} ({record['r']:+.2f}R)"
            )

    if not setups_results:
        print("❌ هیچ ستاپی در بازه داده‌ها یافت نشد.")
        return []

    metrics = compute_backtest_metrics(setups_results)
    print_backtest_report(metrics)
    return setups_results


if __name__ == "__main__":
    run_replay()