import logging
from datetime import datetime, timezone as dt_tz
from decimal import Decimal
from bot_app.services.mt5_service import get_rates_data
from bot_app.models import MarketCandle

logger = logging.getLogger(__name__)

# تعریف نقشه تایم‌فریم‌ها (باید با nds_strategy_service هماهنگ باشد)
HTF_FOR = {"M1": "M30", "M5": "M30", "M15": "H1", "M30": "H4", "H1": "H4"}

def _fetch_and_save(symbol, tf, limit):
    """تابع کمکی برای دریافت و ذخیره یک تایم‌فریم خاص"""
    rates_data, msg = get_rates_data(symbol=symbol, timeframe=tf, limit=limit)
    if rates_data is None or rates_data is False:
        logger.error(f'Error updating {tf} candles from MT5 -> {msg}')
        return False

    existing_times = set(
        MarketCandle.objects.filter(symbol=symbol, timeframe=tf).values_list('open_time', flat=True)
    )

    candles_to_create = []
    rates_data = rates_data[:-1] # حذف کندل در حال شکل‌گیری
    
    for rate in rates_data:
        open_time = datetime.fromtimestamp(int(rate['time']), tz=dt_tz.utc)
        if open_time not in existing_times:
            candles_to_create.append(
                MarketCandle(
                    symbol=symbol, timeframe=tf, open_time=open_time,
                    open_price=Decimal(str(rate['open'])), high_price=Decimal(str(rate['high'])),
                    low_price=Decimal(str(rate['low'])), close_price=Decimal(str(rate['close'])),
                    volume=Decimal(str(rate['tick_volume'])),
                )
            )

    if candles_to_create:
        MarketCandle.objects.bulk_create(candles_to_create, ignore_conflicts=True)
        logger.info(f'✅ Saved {len(candles_to_create)} new candles for {symbol} [{tf}].')
    return True

def prepare_and_update_candles(symbol, time_frame, full_limit=1500, short_limit=50):
    """دریافت و آپدیت کندل‌ها برای هر دو تایم‌فریم LTF و HTF"""
    ltf = time_frame
    htf = HTF_FOR.get(ltf)
    
    if not htf:
        logger.error(f'تایم‌فریم {ltf} در نقشه NDS تعریف نشده است.')
        return False

    # 1. آپدیت کندل‌های LTF (مثلاً M1)
    ltf_count = MarketCandle.objects.filter(symbol=symbol, timeframe=ltf).count()
    ltf_limit = full_limit if ltf_count < 200 else short_limit
    if not _fetch_and_save(symbol, ltf, ltf_limit):
        return False

    # 2. آپدیت کندل‌های HTF (مثلاً M30)
    # برای HTF نیازی به 1500 کندل نیست، 500 کندل کافی است
    htf_count = MarketCandle.objects.filter(symbol=symbol, timeframe=htf).count()
    htf_limit = 500 if htf_count < 100 else 50 
    if not _fetch_and_save(symbol, htf, htf_limit):
        return False

    return True