"""
Django Management Command: pass CSV data directly to replay_signals.py

Usage:
    python manage.py backtest_nds --db --symbol XAUUSD_l  #بک‌تست از روی کندل‌های موجود در دیتابیس 
    python manage.py backtest_nds --db --symbol XAUUSD_l --lookback 5000   #محدود کردن به N کندل آخر LTF (برای تست سریع‌تر):
    python manage.py backtest_nds --db --symbol XAUUSD_l --no-open   #اجرا بدون باز شدن خودکار چارت در مرورگر (مناسب برای اجراهای پرتکرار یا محیط بدون GUI)
    python manage.py backtest_nds --csv "data/XAUUSD_M1.csv" --symbol XAUUSD_l  
    "C:/Users/Jarvis/Documents/download/xauusd-m1-bid-2024-11-22-2025-11-23.csv"
"""
from __future__ import annotations

import os
import sys
from django.core.management.base import BaseCommand, CommandError
from nds_bot.data import load_csv


class Command(BaseCommand):
    help = "اجرای replay_signals با داده‌های CSV یا مستقیم از دیتابیس (با --db)"

    def add_arguments(self, parser):
        parser.add_argument("--csv", required=False, help="مسیر فایل CSV کندل‌ها")
        parser.add_argument("--db", action="store_true", help="خواندن کندل‌ها از دیتابیس به جای CSV")
        parser.add_argument("--symbol", default="XAUUSD_l", help="نام نماد")
        parser.add_argument("--lookback", type=int, default=None, help="محدود کردن به N کندل آخر LTF")
        parser.add_argument("--no-open", action="store_true", help="عدم باز کردن خودکار چارت HTML در مرورگر")

    def handle(self, *args, **options):
        project_root = os.getcwd()
        if project_root not in sys.path:
            sys.path.insert(0, project_root)

        from replay_signals import run_replay, _build_htf_from_ltf

        auto_open = not options["no_open"]
        lookback = options["lookback"]

        if options["db"]:
            print("🗄 حالت دیتابیس فعال است؛ فایل CSV خوانده نمی‌شود.")
            run_replay(
                ohlc_ltf=None,
                ohlc_htf=None,
                symbol=options["symbol"],
                lookback_bars=lookback,
                open_chart=auto_open,
            )
            return

        csv_path = options["csv"]
        if not csv_path:
            raise CommandError("❌ یا --db را بزنید یا مسیر فایل را با --csv بدهید.")
        if not os.path.exists(csv_path):
            raise CommandError(f"❌ فایل پیدا نشد: {csv_path}")

        ohlc_ltf = load_csv(csv_path)
        ohlc_htf = _build_htf_from_ltf(ohlc_ltf, rule="30min")

        run_replay(
            ohlc_ltf=ohlc_ltf,
            ohlc_htf=ohlc_htf,
            symbol=options["symbol"],
            lookback_bars=lookback,
            open_chart=auto_open,
        )