"""
python manage.py train_model EURUSD H1 --backfill 20000 --cost-pips 1.3
python manage.py train_model EURUSD H1 --limit 20000          (فقط آموزش، از دیتای موجود در DB)

آموزش آفلاین جدا از ربات زنده. اگر مدل از گیت کیفیت عبور نکند ذخیره نمی‌شود.
"""
import pandas as pd
from django.core.management.base import BaseCommand

from bot_app.models import MarketCandle
from bot_app.services.market_data_service import prepare_and_update_candles
from bot_app.services.ml_engine import TradingAIEngine
from bot_app.services.ml_features import create_ml_labels, extract_features_for_ml
from bot_app.services.mt5_service import start_mt5_connection, stop_mt5_connection

class Command(BaseCommand):
    help = 'Train the meta-label model for one symbol/timeframe (offline).'

    def add_arguments(self, parser):
        parser.add_argument('symbol')
        parser.add_argument('timeframe')
        parser.add_argument('--limit', type=int, default=20000, help='حداکثر تعداد کندل برای آموزش')
        parser.add_argument('--backfill', type=int, default=0, help='قبل از آموزش این تعداد کندل از MT5 بگیر')
        parser.add_argument('--pip', type=float, default=0.0001, help='اندازه‌ی پیپ (JPY: 0.01)')
        parser.add_argument('--cost-pips', type=float, default=1.3, help='اسپرد + slippage بر حسب پیپ')

    def handle(self, *args, **o):
        symbol, tf = o['symbol'], o['timeframe']

        if o['backfill']:
            if not start_mt5_connection():
                self.stderr.write('MT5 connection failed'); return
            try:
                prepare_and_update_candles(symbol, tf, o['backfill'], o['backfill'])
            finally:
                stop_mt5_connection()

        rows = list(
            MarketCandle.objects.filter(symbol=symbol, timeframe=tf)
            .order_by('-open_time')
            .values('open_price', 'high_price', 'low_price', 'close_price', 'volume', 'open_time')[:o['limit']]
        )[::-1]
        if not rows:
            self.stderr.write('No candles in DB.'); return

        df = extract_features_for_ml(pd.DataFrame(rows))
        if df.empty:
          self.stderr.write('Feature extraction resulted in empty DataFrame.')
          return
        # 👈 محاسبه آستانه پیپ بر اساس آرگومان‌های ورودی دستور
        threshold_value = o['cost_pips'] * o['pip']

        # دریافت لیست برچسب‌ها از تابع شما
        raw_labels = create_ml_labels(df, pips_threshold=threshold_value)

        # تبدیل لیست خروجی به Pandas Series با ایندکس دقیقِ دیتافریم برای جلوگیری از خطای join
        labels_series = pd.Series(
            raw_labels, index=df.index, name='target_label'
        )
        # چسباندن لیبل‌ها به دیتافریم
        df = df.join(labels_series)

        self.stdout.write(f'candles={len(df)}')

       # آموزش مدل
        result = TradingAIEngine(symbol, tf).train(df)

        # 👈 بررسی اینکه آیا خروجی دیکشنری است یا یک مقدار عددی ساده (مثل float)
        if isinstance(result, dict):
          for k, v in result.items():
            self.stdout.write(f'  {k}: {v}')
          is_saved = result.get('saved', False)
        else:
          # اگر متد فقط یک مقدار عددی (مثلا دقت مدل) برگردانده باشد
          self.stdout.write(f'  Model Score / Metric: {result}')
          is_saved = True  # یا منطق دلخواه برای بررسی ذخیره شدن

        # چاپ وضعیت نهایی ذخیره‌سازی مدل
        if is_saved:
          self.stdout.write(self.style.SUCCESS('MODEL SAVED'))
        else:
          self.style.WARNING('MODEL NOT SAVED (no reliable edge found)')