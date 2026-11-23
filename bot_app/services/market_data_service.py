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
    """
    دریافت و آپدیت کندل‌ها برای هر دو تایم‌فریم LTF و HTF.
    مقدار limit به صورت داینامیک بر اساس فاصله زمانی محاسبه می‌شود
    تا در صورت خاموشی طولانی ربات، داده‌ای گم نشود.
    """
    from datetime import datetime, timezone as dt_tz
    from django.utils import timezone
    
    ltf = time_frame.strip().lower() if time_frame.islower() else time_frame
    if ltf not in HTF_FOR:
        logger.error(f'تایم‌فریم {ltf} در نقشه NDS تعریف نشده است.')
        return False
    
    htf = HTF_FOR.get(ltf)
    
    def calculate_needed_limit(tf, default_min=50, max_limit=5000):
        """محاسبه تعداد کندل موردنیاز بر اساس فاصله زمانی از آخرین کندل دیتابیس."""
        last = MarketCandle.objects.filter(symbol=symbol, timeframe=tf).order_by('-open_time').first()
        if not last:
            return full_limit  # اگر دیتابیس خالی است، از full_limit استفاده کن
        
        # فاصله زمانی بین الان و آخرین کندل (به دقیقه)
        tf_minutes = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240}[tf]
        now = timezone.now()
        gap_minutes = (now - last.open_time).total_seconds() / 60
        needed = int(gap_minutes / tf_minutes) + default_min
        
        # حداقل 50، حداکثر max_limit
        return min(max(needed, default_min), max_limit)
    
    # 1. آپدیت LTF با limit داینامیک
    ltf_limit = calculate_needed_limit(ltf, default_min=short_limit)
    logger.info(f"📊 LTF ({ltf}): درخواست {ltf_limit} کندل (به دلیل فاصله زمانی)")
    if not _fetch_and_save(symbol, ltf, ltf_limit):
        return False
    
    # 2. آپدیت HTF با limit داینامیک
    htf_limit = calculate_needed_limit(htf, default_min=50)
    logger.info(f"📊 HTF ({htf}): درخواست {htf_limit} کندل")
    if not _fetch_and_save(symbol, htf, htf_limit):
        return False
    
    return True