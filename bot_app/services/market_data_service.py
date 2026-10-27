import logging
from datetime import datetime,timezone as dt_tz
from decimal import Decimal


from bot_app.services.mt5_service import get_rates_data

from bot_app.models import MarketCandle

logger = logging.getLogger(__name__)

def prepare_and_update_candles(
    symbol, time_frame, full_limit, short_limit
):
    existing_candles_count = MarketCandle.objects.filter(
        symbol=symbol, timeframe=time_frame
    ).count()

    limit = full_limit if existing_candles_count < 200 else short_limit

    rates_data, msg = get_rates_data(
        symbol=symbol, timeframe=time_frame, limit=limit
    )

    # بررسی صحت خروجی
    if rates_data is None or rates_data is False:
        logger.error(f'Error updating candles from MT5 -> {msg}')
        return False

    # استخراج زمان کندل‌هایی که از قبل در دیتابیس هستند (برای جلوگیری از تکرار با سرعت بالا)
    existing_times = set(
        MarketCandle.objects.filter(symbol=symbol, timeframe=time_frame).values_list(
            'open_time', flat=True
        )
    )

    candles_to_create = []
    rates_data = rates_data[:-1]
    
    for rate in rates_data:
        timestamp_val = int(rate['time'])
        open_time = datetime.fromtimestamp(timestamp_val, tz=dt_tz.utc)

        # اگر این کندل از قبل وجود نداشت، آن را به لیست درج گروهی اضافه می‌کنیم
        if open_time not in existing_times:
            candles_to_create.append(
                MarketCandle(
                    symbol=symbol,
                    timeframe=time_frame,
                    open_time=open_time,
                    open_price=Decimal(str(rate['open'])),
                    high_price=Decimal(str(rate['high'])),
                    low_price=Decimal(str(rate['low'])),
                    close_price=Decimal(str(rate['close'])),
                    volume=Decimal(str(rate['tick_volume'])),
                )
            )

    # ثبت یک‌جای تمام کندل‌های جدید با bulk_create (بدون قفل کردن دیتابیس)
    if candles_to_create:
        MarketCandle.objects.bulk_create(candles_to_create, ignore_conflicts=True)
        logger.info(
            f'✅ Successfully bulk-saved {len(candles_to_create)} new candles for'
            f' {symbol} [{time_frame}]!'
        )
    else:
        logger.info(f'ℹ️ No new candles to save for {symbol} [{time_frame}].')

    return True