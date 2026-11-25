# python manage.py train_super_scalp_ai --symbol XAUUSD_l --timeframe M1 --limit 30000
# python manage.py train_super_scalp_ai --symbol XAUUSD_l --timeframe M1 --limit 30000 --all-5
# python manage.py train_super_scalp_ai --csv data/xauusd_m1.csv --all-5

import os
import joblib
import numpy as np
import pandas as pd
from django.core.management.base import BaseCommand
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report, roc_auc_score

from bot_app.models import MarketCandle
from super_scalp_backtest import load_ohlc_from_csv, run_super_scalp_backtest
from nds_bot.super_scalp_engine import SuperScalpConfig
from nds_bot.dva_ar_risk import DVAARConfig
from bot_app.services.super_scalp_service import extract_scalp_ai_features


class Command(BaseCommand):
    help = "آموزش مدل AI اختصاصی برای موتور Super-Scalping از روی دیتابیس یا CSV"

    def add_arguments(self, parser):
        parser.add_argument("--symbol", type=str, default="XAUUSD_l")
        parser.add_argument("--timeframe", type=str, default="M1")
        parser.add_argument("--csv", type=str, default=None)
        parser.add_argument("--limit", type=int, default=30000)
        parser.add_argument(
            "--all-5",
            action="store_true",
            help="آموزش مدل فقط روی ستاپ‌هایی که هر ۵ شرط چک‌لیست را پاس کرده‌اند",
        )
        parser.add_argument("--out", type=str, default=None)

    def handle(self, *args, **options):
        all_5 = bool(options["all_5"])
        out_path = options["out"] or (
            "models/super_scalp_ai_all5.joblib" if all_5 else "models/super_scalp_ai.joblib"
        )

        if options["csv"]:
            ohlc = load_ohlc_from_csv(options["csv"])
        else:
            qs = (
                MarketCandle.objects.filter(
                    symbol=options["symbol"],
                    timeframe=options["timeframe"],
                )
                .order_by("-open_time")
                .values("open_time", "open_price", "high_price", "low_price", "close_price")[: options["limit"]]
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
                },
                index=pd.to_datetime([r["open_time"] for r in rows]),
            )

        cfg = SuperScalpConfig(
            ltf=options["timeframe"],
            require_all_5_checks=all_5,
            ai_advisory_only=True,
        )
        raw_dva_cfg = DVAARConfig(dva_enabled=False, dva_breakeven_r=None)

        bt = run_super_scalp_backtest(
            ohlc_ltf=ohlc,
            cfg=cfg,
            dva_cfg=raw_dva_cfg,
            ai_predict_fn=None,
        )
        trades = bt.get("trades") or []

        if len(trades) < 30:
            self.stderr.write(
                f"❌ تعداد ستاپ‌های استخراج‌شده ({len(trades)}) برای آموزش مدل کافی نیست "
                f"(پیشنهاد: --limit بزرگ‌تر یا فایل CSV طولانی‌تر بدهید)."
            )
            return

        valid_pairs = []
        for t in trades:
            r_val = t.get("r")
            if r_val is None or pd.isna(r_val):
                continue
            feats = extract_scalp_ai_features(t)
            if isinstance(feats, dict) and feats:
                valid_pairs.append((feats, 1 if float(r_val) > 0 else 0))

        if len(valid_pairs) < 30:
            self.stderr.write(f"❌ تعداد نمونه‌های معتبر ({len(valid_pairs)}) برای آموزش کافی نیست.")
            return

        X = pd.DataFrame([pair[0] for pair in valid_pairs]).apply(pd.to_numeric, errors="coerce").fillna(0.0)
        y = np.array([pair[1] for pair in valid_pairs], dtype=int)

        split = int(len(X) * 0.8)
        X_train, X_test = X.iloc[:split], X.iloc[split:]
        y_train, y_test = y[:split], y[split:]

        clf = RandomForestClassifier(
            n_estimators=200,
            max_depth=4 if all_5 else 5,
            min_samples_leaf=4 if all_5 else 5,
            random_state=42,
            class_weight="balanced",
        )
        clf.fit(X_train, y_train)

        if len(set(y_test)) > 1:
            preds = clf.predict(X_test)
            probs = clf.predict_proba(X_test)[:, 1]
            auc = roc_auc_score(y_test, probs)
            self.stdout.write("📊 گزارش ارزیابی مدل روی ۲۰٪ داده انتهایی:\n")
            self.stdout.write(classification_report(y_test, preds, digits=3))
            self.stdout.write(f"🎯 ROC-AUC روی بخش تست: {auc:.3f}\n")

        clf.fit(X, y)
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        joblib.dump(
            {
                "model": clf,
                "feature_cols": list(X.columns),
                "symbol": options["symbol"],
                "timeframe": options["timeframe"],
                "require_all_5_checks": all_5,
                "samples": len(X),
            },
            out_path,
        )
        self.stdout.write(
            f"✅ مدل Super-Scalp AI (all_5={all_5}) ذخیره شد: {out_path} (نمونه‌ها: {len(X)})"
        )