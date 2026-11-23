"""
Django Management Command: backtest NDS strategy on a CSV file.

Usage:
    python manage.py backtest_nds --csv "C:/Users/Jarvis/Documents/download/xauusd-m1-bid-2024-11-22-2025-11-23.csv"
    python manage.py backtest_nds --csv "C:/Users/Jarvis/Documents/download/xauusd-m1-bid-2024-11-22-2025-11-23.csv" --with-ai
    python manage.py backtest_nds --csv data/XAUUSD_M1.csv --pivot 3 --sym 0.075 --cost 0.0002 --out results.csv
    python manage.py backtest_nds --help
"""
from __future__ import annotations

import os
import numpy as np
import pandas as pd
from django.core.management.base import BaseCommand, CommandError

from nds_bot.data import load_csv
from nds_bot.config import NDSConfig
from nds_bot.backtest import run_backtest


def compute_stats(df: pd.DataFrame, r_col: str = "r") -> dict:
    """محاسبه آمار بک‌تست از دیتافریم خروجی run_backtest."""
    if df is None or len(df) == 0 or r_col not in df.columns:
        return {"trades": 0}
    
    r = df[r_col].to_numpy(float)
    eq = np.cumsum(r)
    peak = np.maximum.accumulate(eq)
    gp = r[r > 0].sum()
    gl = -r[r < 0].sum()
    
    # Bootstrap 95% CI for mean R
    ci = (float("nan"), float("nan"))
    if len(r) >= 10:
        rng = np.random.default_rng(0)
        means = rng.choice(r, (1000, len(r))).mean(axis=1)
        lo, hi = np.percentile(means, [2.5, 97.5])
        ci = (float(lo), float(hi))
    
    return {
        "trades": int(len(r)),
        "wins": int((r > 0).sum()),
        "losses": int((r < 0).sum()),
        "win_rate": float((r > 0).mean()),
        "avg_R": float(r.mean()),
        "median_R": float(np.median(r)),
        "total_R": float(r.sum()),
        "profit_factor": float(gp / gl) if gl > 0 else float("inf"),
        "max_dd_R": float((peak - eq).max()),
        "tp1_rate": float(df["tp1_hit"].mean()) if "tp1_hit" in df.columns else float("nan"),
        "avg_R_ci95": ci,
    }


class Command(BaseCommand):
    help = "بک‌تست استراتژی NDS روی یک فایل CSV (با قابلیت استفاده از مدل AI)."

    def add_arguments(self, parser):
        parser.add_argument("--csv", required=True, help="مسیر فایل CSV با داده‌های OHLCV")
        parser.add_argument("--symbol", default="XAUUSD_l", help="نام نماد (فقط برای لاگ)")
        parser.add_argument("--with-ai", action="store_true", help="استفاده از مدل آموزش‌دیده nds_model.pt")
        parser.add_argument("--model", default="nds_model.pt", help="مسیر فایل مدل")
        parser.add_argument("--pivot", type=int, default=3, help="قدرت نودها (پیش‌فرض 3)")
        parser.add_argument("--sym", type=float, default=0.075, help="تلورانس تقارن کالمن (پیش‌فرض 0.075)")
        parser.add_argument("--rr", type=float, default=2.0, help="حداقل ریسک به ریوارد (پیش‌فرض 2.0)")
        parser.add_argument("--cost", type=float, default=0.0, help="اسپرد+کمیسیون به واحد قیمت")
        parser.add_argument("--out", default="backtest_results.csv", help="مسیر خروجی CSV")

    def handle(self, *args, **options):
        csv_path = options["csv"]
        if not os.path.exists(csv_path):
            raise CommandError(f"❌ فایل CSV یافت نشد: {csv_path}")

        # 1. بارگذاری داده‌ها
        self.stdout.write(self.style.NOTICE(f"⏳ در حال بارگذاری {csv_path} ..."))
        try:
            ltf = load_csv(csv_path)
        except Exception as e:
            raise CommandError(f"❌ خطا در خواندن CSV: {e}")
        self.stdout.write(self.style.SUCCESS(
            f"✅ {len(ltf)} کندل بارگذاری شد ({ltf.index[0]} → {ltf.index[-1]})"
        ))

        # 2. بارگذاری مدل AI (اختیاری)
        model = None
        if options["with_ai"]:
            model_path = options["model"]
            if not os.path.exists(model_path):
                raise CommandError(f"❌ فایل مدل یافت نشد: {model_path}")
            try:
                import torch
                from nds_bot.kalman import QRPredictor
                self.stdout.write(self.style.NOTICE(f"🤖 در حال بارگذاری مدل AI از {model_path} ..."))
                model = QRPredictor(input_dim=10)
                model.load_state_dict(torch.load(model_path, map_location="cpu"))
                model.eval()
                self.stdout.write(self.style.SUCCESS("✅ مدل AI بارگذاری شد."))
            except Exception as e:
                raise CommandError(f"❌ خطا در بارگذاری مدل: {e}")

        # 3. ساخت تنظیمات
        cfg_kwargs = dict(
            ltf="M1",
            htf="M30",
            pivot_ltf=options["pivot"],
            cost_price=options["cost"],
        )
        # اگر فیلدها در NDSConfig وجود داشتند، اضافه کن
        try:
            cfg = NDSConfig(**cfg_kwargs, symmetry_tol=options["sym"])
        except TypeError:
            cfg = NDSConfig(**cfg_kwargs)
            self.stdout.write(self.style.WARNING("⚠️ فیلد symmetry_tol در NDSConfig یافت نشد."))
        
        if hasattr(cfg, "min_rr"):
            cfg.min_rr = options["rr"]

        # 4. اجرای بک‌تست
        self.stdout.write(self.style.NOTICE("🚀 در حال اجرای بک‌تست ..."))
        try:
            trades = run_backtest(ltf, cfg, htf=None, model=model)
        except TypeError as e:
            if "model" in str(e):
                self.stdout.write(self.style.WARNING(
                    "⚠️ run_backtest پارامتر model را نمی‌پذیرد — بدون AI اجرا می‌شود."
                ))
                trades = run_backtest(ltf, cfg, htf=None)
            else:
                raise CommandError(f"❌ خطا در بک‌تست: {e}")

        if trades is None or len(trades) == 0:
            self.stdout.write(self.style.WARNING("❌ هیچ معامله‌ای در این بازه پیدا نشد."))
            return

        # 5. محاسبه و چاپ آمار
        report = compute_stats(trades)
        mode = "با AI" if model else "فقط قوانین NDS"

        self.stdout.write("\n" + "=" * 55)
        self.stdout.write(self.style.MIGRATE_HEADING(f"📊 گزارش بک‌تست NDS ({mode})"))
        self.stdout.write("=" * 55)
        self.stdout.write(f"  تعداد معاملات:      {report['trades']}")
        self.stdout.write(f"  برد / باخت:         {report['wins']} / {report['losses']}")
        self.stdout.write(f"  نرخ برد:            {report['win_rate']:.2%}")
        self.stdout.write(f"  میانگین R:          {report['avg_R']:.3f}")
        self.stdout.write(f"  میانه R:            {report['median_R']:.3f}")
        self.stdout.write(f"  مجموع R:            {report['total_R']:.2f}")
        self.stdout.write(f"  فاکتور سود:         {report['profit_factor']:.2f}")
        self.stdout.write(f"  حداکثر افت (DD):    {report['max_dd_R']:.2f} R")
        if "tp1_hit" in trades.columns:
            self.stdout.write(f"  نرخ رسیدن به TP:    {report['tp1_rate']:.2%}")
        ci = report["avg_R_ci95"]
        if not pd.isna(ci[0]):
            self.stdout.write(f"  بازه اطمینان 95%:   ({ci[0]:.2f}, {ci[1]:.2f})")
        self.stdout.write("=" * 55)

        # 6. ذخیره نتایج
        trades.to_csv(options["out"], index=False)
        self.stdout.write(self.style.SUCCESS(f"💾 نتایج کامل در {options['out']} ذخیره شد."))