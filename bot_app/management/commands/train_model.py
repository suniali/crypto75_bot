"""
python manage.py train_model XAUUSD H1 --backfill 5000 --limit 5000 --min-move-usd 4.0 --atr-mult 1.2 --min-acc 0.52
"""
import pandas as pd
from django.core.management.base import BaseCommand

from bot_app.models import MarketCandle
from bot_app.services.market_data_service import prepare_and_update_candles
from bot_app.services.ml_engine import TradingAIEngine
from bot_app.services.ml_features import create_ml_labels, extract_features_for_ml
from bot_app.services.mt5_service import start_mt5_connection, stop_mt5_connection


class Command(BaseCommand):
    help = 'Train the ML setup filter model for one symbol/timeframe (offline).'

    def add_arguments(self, parser):
        parser.add_argument('symbol')
        parser.add_argument('timeframe')
        parser.add_argument('--limit', type=int, default=20000, help='حداکثر تعداد کندل برای آموزش')
        parser.add_argument('--backfill', type=int, default=0, help='تعداد کندل برای دریافت از MT5 قبل از آموزش')
        parser.add_argument('--min-move-usd', type=float, default=3.5, help='حداقل حرکت دلاری مورد انتظار روی طلا')
        parser.add_argument('--atr-mult', type=float, default=1.2, help='ضریب ATR برای آستانه حرکت')
        parser.add_argument('--min-acc', type=float, default=0.52, help='حداقل دقت تست برای ذخیره مدل')

    def handle(self, *args, **o):
        symbol, tf = o['symbol'], o['timeframe']

        if o['backfill']:
            if not start_mt5_connection():
                self.stderr.write('MT5 connection failed')
                return
            try:
                prepare_and_update_candles(symbol, tf, o['backfill'], o['backfill'])
            finally:
                stop_mt5_connection()

        # ترتیب صعودی زمانی (از قدیمی‌ترین به جدیدترین کندل)
        rows = list(
            MarketCandle.objects.filter(symbol=symbol, timeframe=tf)
            .order_by('-open_time')
            .values('open_price', 'high_price', 'low_price', 'close_price', 'volume', 'open_time')[: o['limit']]
        )[::-1]

        if not rows:
            self.stderr.write('No candles in DB.')
            return

        df = extract_features_for_ml(pd.DataFrame(rows))
        if df.empty:
            self.stderr.write('Feature extraction resulted in empty DataFrame.')
            return

        raw_labels = create_ml_labels(
            df,
            forward_periods=8,
            min_move_usd=o['min_move_usd'],
            atr_mult_target=o['atr_mult'],
        )
        df['target_label'] = pd.Series(raw_labels, index=df.index, dtype=int)

        self.stdout.write(f'Processed candles={len(df)}')
        result = TradingAIEngine(symbol, tf).train(df, min_accuracy=o['min_acc'])

        for k, v in result.items():
            self.stdout.write(f'  {k}: {v}')

        if result.get('saved'):
            self.stdout.write(self.style.SUCCESS('MODEL SAVED'))
        else:
            self.stdout.write(self.style.WARNING('MODEL NOT SAVED (no reliable edge found)'))