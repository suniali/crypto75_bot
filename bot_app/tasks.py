import os
import sys
import django
from pathlib import Path

# ۱. تعیین مسیر دقیق پروژه
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

os.chdir(BASE_DIR)

# ۲. ست کردن متغیر محیطی تنظیمات جنگو (نام پوشه پروژه‌تان را جایگزین crypto_telegram_bot کنید اگر فرق دارد)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

# ۳. راه‌اندازی رسمی جنگو
django.setup()

import logging

from django.db import close_old_connections

from bot_app.services.execution_service import evaluate_market_and_execute
from bot_app.services.market_data_service import prepare_and_update_candles
from bot_app.services.telegram_service import send_telegram_trade_alert

from .models import MarketCandle

logger = logging.getLogger(__name__)

def run_market_ai_analysis_task(symbol,time_frame,full_limit,short_limit,account_balance,risk):
    """تسک تحلیل هوش مصنوعی (بدون نیاز به Celery، اجرا در BackgroundScheduler یا ترد)"""
    close_old_connections()

    logger.info(
        f'🤖 [AI Task Triggered] Starting market analysis for {symbol} ...'
    )

    # ۱. اول کندل‌ها را به‌روزرسانی می‌کنیم
    success = prepare_and_update_candles(symbol=symbol,time_frame=time_frame,full_limit=full_limit,short_limit=short_limit)
    if not success:
        logger.warning('⚠️ Skipping AI analysis due to candle update failure.')
        return
    

    try:
        # ۱. دریافت کندل‌ها از دیتابیس
        candles_qs = MarketCandle.objects.filter(symbol=symbol,timeframe=time_frame).order_by(
            '-open_time'
        )[:300]
        count = candles_qs.count()

        if count < 100:
            logger.warning(
                '⚠️ Not enough candles in database to run AI model! (Need at least'
                ' 100)'
            )
            return
                
        # ۲. اجرای موتور ارزیابی و تصمیم‌گیری هوش مصنوعی
        result = evaluate_market_and_execute(
            symbol=symbol, time_frame=time_frame,candles_qs=candles_qs, account_balance=account_balance, risk_percentage=risk
        )
        logger.info(f'⚙️ Evaluation Result Status: {result.get("status")}')

        # ۳. بررسی و ارسال سیگنال در صورت تایید
        if result.get('status') == 'approved_signal':
            send_telegram_trade_alert(result)
            logger.info('✅ AI Signal approved and sent to Telegram!')
        else:
            logger.info(f'ℹ️ Signal not triggered. Reason: {result.get("reason")}')

    except Exception as e:
        logger.exception(f'❌ Error in run_market_ai_analysis_task: {e}')