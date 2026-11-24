"""
Django Management Command: pass CSV data directly to replay_signals.py

Usage:
    python manage.py backtest_nds --db
    python manage.py backtest_nds --csv "data/XAUUSD_M1.csv" --symbol XAUUSD_l --lookback 50
    python manage.py backtest_nds --csv "C:/Users/Jarvis/Documents/download/xauusd-m1-bid-2024-11-22-2025-11-23.csv"
"""
from __future__ import annotations

import os
import sys
from django.core.management.base import BaseCommand, CommandError
from nds_bot.data import load_csv


class Command(BaseCommand):
    help = "اجرای replay_signals با داده‌های CSV یا مستقیم از دیتابیس (با --db)"

    def add_arguments(self, parser):
        parser.add_argument("--csv", required=False, help="مسیر فایل CSV کندل‌ها (هنگام استفاده از --db لازم نیست)")
        parser.add_argument("--db", action="store_true", help="خواندن کندل‌ها از دیتابیس به جای فایل CSV")
        parser.add_argument("--symbol", default="XAUUSD_l", help="نام نماد")

    def handle(self, *args, **options):
        project_root = os.getcwd()
        if project_root not in sys.path:
            sys.path.insert(0, project_root)

        from replay_signals import run_replay, _build_htf_from_ltf

        if options["db"]:
            print("🗄 حالت دیتابیس فعال است؛ فایل CSV خوانده نمی‌شود.")
            run_replay(ohlc_ltf=None, ohlc_htf=None, symbol=options["symbol"])
            return

        csv_path = options["csv"]
        if not csv_path:
            raise CommandError("❌ یا --db را بزنید یا مسیر فایل را با --csv بدهید.")
        if not os.path.exists(csv_path):
            raise CommandError(f"❌ فایل پیدا نشد: {csv_path}")

        ohlc_ltf = load_csv(csv_path)
        ohlc_htf = _build_htf_from_ltf(ohlc_ltf, rule="30min")

        run_replay(ohlc_ltf=ohlc_ltf, ohlc_htf=ohlc_htf, symbol=options["symbol"])