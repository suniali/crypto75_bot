# اجرای استاندارد روی ۱۰,۰۰۰ کندل آخر M1 طلا در دیتابیس
# python manage.py backtest_super_scalp --symbol XAUUSD_l --timeframe M1 --limit 10000

# اجرا با الزام سخت‌گیرانه هر ۵ شرط چک‌لیست اسلاید 114
# python manage.py backtest_super_scalp --symbol XAUUSD_l --timeframe M1 --limit 10000 --all-5

# بک‌تست مجدد با مدل AI مشورتی
# python manage.py backtest_super_scalp --symbol XAUUSD_l --timeframe M1 --limit 15000 --model models/super_scalp_ai.joblib

# نمایش تمام تریدها در چارت
# python manage.py backtest_super_scalp --symbol XAUUSD_l --timeframe M1 --limit 10000 --model models/super_scalp_ai.joblib --chart

import os
import pandas as pd
from django.core.management.base import BaseCommand

from bot_app.models import MarketCandle
from super_scalp_backtest import load_ohlc_from_csv, run_super_scalp_backtest
from bot_app.services.super_scalp_service import load_scalp_ai_predictor
from nds_bot.super_scalp_engine import SuperScalpConfig
from nds_bot.dva_ar_risk import DVAARConfig


class Command(BaseCommand):
    help = "اجرای بک‌تست موتور Super-Scalping (Heikin Ashi + چک‌لیست ۵گانه + AI + DVA + چارت تعاملی)"

    def add_arguments(self, parser):
        parser.add_argument("--symbol", type=str, default="XAUUSD_l")
        parser.add_argument("--timeframe", type=str, default="M1")
        parser.add_argument("--csv", type=str, default=None)
        parser.add_argument("--limit", type=int, default=10000)
        parser.add_argument("--risk-usd", type=float, default=50.0)
        parser.add_argument("--max-hold", type=int, default=45)
        parser.add_argument("--all-5", action="store_true", help="الزام پاس شدن هم‌زمان هر ۵ شرط چک‌لیست")
        parser.add_argument("--model", type=str, default="models/super_scalp_ai.joblib")
        parser.add_argument("--strict-ai", action="store_true", help="خروج AI از حالت مشورتی و مسدود کردن سیگنال‌های ردشده")
        parser.add_argument("--ai-min-prob", type=float, default=0.55)
        parser.add_argument("--chart", action="store_true", help="ساخت و باز کردن چارت تعاملی HTML")
        parser.add_argument("--chart-out", type=str, default="scalp_backtest_chart.html")
        parser.add_argument("--no-open", action="store_true", help="فایل چارت ساخته شود ولی مرورگر باز نشود")
        parser.add_argument("--no-dva", action="store_true", help="غیرفعال کردن DVA و استفاده از درصد ریسک ثابت")
        parser.add_argument("--dva-equity", type=float, default=100.0, help="سرمایه مبنا برای محاسبه DVA در بک‌تست")
        parser.add_argument(
            "--be-r",
            type=float,
            default=0.8,
            help="آستانه R برای انتقال حد ضرر به سر‌به‌سر (مثلاً 0.8 یا 0 برای غیرفعال)",
        )
        parser.add_argument("--require-m30", action="store_true", help="فقط ستاپ‌های هم‌راستا با جهت M30 اجرا شوند")

    def handle(self, *args, **options):
        tf = options["timeframe"].upper()

        if options["csv"]:
            self.stdout.write(f"📄 خواندن کندل‌ها از فایل CSV: {options['csv']}...")
            ohlc = load_ohlc_from_csv(options["csv"])
        else:
            self.stdout.write(f"🗄️ خواندن کندل‌ها از دیتابیس ({options['symbol']} | {tf})...")
            qs = (
                MarketCandle.objects.filter(symbol=options["symbol"], timeframe=tf)
                .order_by("-open_time")
                .values("open_time", "open_price", "high_price", "low_price", "close_price", "volume")[: options["limit"]]
            )
            rows = list(qs)[::-1]
            if not rows:
                self.stderr.write("❌ داده‌ای در دیتابیس یافت نشد.")
                return
            ohlc = pd.DataFrame(
                {
                    "open": [float(r["open_price"]) for r in rows],
                    "high": [float(r["high_price"]) for r in rows],
                    "low": [float(r["low_price"]) for r in rows],
                    "close": [float(r["close_price"]) for r in rows],
                    "volume": [float(r.get("volume") or 1.0) for r in rows],
                },
                index=pd.to_datetime([r["open_time"] for r in rows]),
            )

        model_path = options["model"]
        ai_predict_fn = None
        if model_path and os.path.exists(model_path):
            ai_predict_fn = load_scalp_ai_predictor(model_path)
            mode_label = "فیلتر فعال" if options["strict_ai"] else "مشورتی"
            self.stdout.write(f"🤖 مدل AI ({mode_label}) لود شد: {model_path}")
        else:
            self.stdout.write("⚠️ مدل AI یافت نشد؛ بک‌تست بدون امتیاز AI اجرا می‌شود.")

        be_r_val = None if float(options["be_r"]) <= 0 else float(options["be_r"])
        dva_cfg = DVAARConfig(
            dva_enabled=not bool(options["no_dva"]),
            dva_equity=float(options["dva_equity"]),
            dva_breakeven_r=be_r_val,
        )

        cfg = SuperScalpConfig(
            ltf=tf,
            fixed_risk_usd=float(options["risk_usd"]),
            max_hold_bars=int(options["max_hold"]),
            require_all_5_checks=bool(options["all_5"]),
            ai_advisory_only=not bool(options["strict_ai"]),
            ai_min_prob=float(options["ai_min_prob"]),
        )
        cfg.require_m30_alignment = bool(options["require_m30"])

        bt = run_super_scalp_backtest(
            ohlc_ltf=ohlc,
            cfg=cfg,
            dva_cfg=dva_cfg,
            ai_predict_fn=ai_predict_fn,
            plot_chart=bool(options["chart"]),
            chart_html=options["chart_out"],
            auto_open_chart=not bool(options["no_open"]),
        )
        m = bt.get("summary") or {}

        total_trades = int(m.get("total_trades", 0))
        wins = int(m.get("wins", 0))
        losses = int(m.get("losses", 0))
        be_trades_count = int(m.get("breakeven_trades", m.get("be_exit_count", 0)))
        win_rate_pct = float(m.get("win_rate_pct", 0.0))
        real_win_rate_pct = float(m.get("real_win_rate_pct", win_rate_pct))
        total_r = float(m.get("total_r", 0.0))
        total_pnl_usd = float(m.get("total_pnl_usd", m.get("total_usd", 0.0)))
        max_dd_r = float(m.get("max_dd_r", 0.0))

        m30_aligned_trades = int(m.get("m30_aligned_trades", m.get("m30_count", 0)))
        m30_aligned_win_rate_pct = float(m.get("m30_aligned_win_rate_pct", m.get("m30_win_rate", 0.0)))

        ai_approved_trades = int(m.get("ai_approved_trades", m.get("ai_count", 0)))
        ai_approved_win_rate_pct = float(m.get("ai_approved_win_rate_pct", m.get("ai_win_rate", 0.0)))

        filtered_by_ai = m.get("filtered_by_ai", bt.get("filtered_by_ai"))
        be_display = "غیرفعال (OFF)" if be_r_val is None else f"{be_r_val:.2f} R"
        dva_status = "فعال" if dva_cfg.dva_enabled else "غیرفعال"

        self.stdout.write("\n" + "=" * 62)
        title_mode = "AI فیلتر فعال" if options.get("strict_ai") else "AI مشورتی"
        self.stdout.write(f"📊 گزارش بک‌تست Super-Scalping (Heikin Ashi + {title_mode})")
        self.stdout.write("=" * 62)
        self.stdout.write(f"مدیریت ریسک DVA:         {dva_status} (سرمایه مبنا: {dva_cfg.dva_equity:.2f}$)")
        self.stdout.write(f"آستانه سر‌به‌سر (BE):      {be_display} | فعال‌شده: {m.get('be_moved_count', 0)} | بسته روی BE: {m.get('be_exit_count', 0)}")
        self.stdout.write(f"کل معاملات:              {total_trades}")
        self.stdout.write(f"برد / باخت / سر‌به‌سر:     {wins} / {losses} / {be_trades_count}")
        self.stdout.write(f"نرخ برد کل (از کل):      {win_rate_pct:.2f}%")
        self.stdout.write(f"نرخ برد واقعی (بدون BE):  {real_win_rate_pct:.2f}%")
        self.stdout.write(f"مجموع R:                {total_r:+.2f} R")
        self.stdout.write(f"بیشترین افت (Max DD):    {max_dd_r:.2f} R")
        self.stdout.write(f"سود/زیان دلاری:          {total_pnl_usd:+.2f}$")
        self.stdout.write(f"هم‌راستا با M30:         {m30_aligned_trades} معامله (Win Rate: {m30_aligned_win_rate_pct:.2f}%)")
        if ai_predict_fn is not None:
            self.stdout.write(f"تاییدشده توسط AI:        {ai_approved_trades} معامله (Win Rate: {ai_approved_win_rate_pct:.2f}%)")
            if options.get("strict_ai") and filtered_by_ai is not None:
                self.stdout.write(f"ردشده توسط فیلتر AI:     {int(filtered_by_ai)} ستاپ")
        self.stdout.write("=" * 62)