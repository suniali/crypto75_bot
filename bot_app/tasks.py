import logging
from django.db import close_old_connections

from bot_app.models import MarketCandle
from bot_app.services.execution_service import evaluate_market_and_execute
from bot_app.services.market_data_service import prepare_and_update_candles
from bot_app.services.telegram_service import send_telegram_trade_alert

logger = logging.getLogger(__name__)

def run_market_ai_analysis_task(symbol, time_frame, full_limit, short_limit, account_balance, risk):
    """تسک تحلیل بازار (واگرایی RSI + اردربلاک معتبر + فیلتر ML)"""
    close_old_connections()

    logger.info(f'🤖 [AI Task Triggered] Starting market analysis for {symbol} [{time_frame}] ...')

    success = prepare_and_update_candles(
        symbol=symbol,
        time_frame=time_frame,
        full_limit=full_limit,
        short_limit=short_limit,
    )
    if not success:
        logger.warning('⚠️ Skipping AI analysis due to candle update failure.')
        return

    try:
        candles_qs = MarketCandle.objects.filter(
            symbol=symbol, timeframe=time_frame
        ).order_by('-open_time')[:300]

        if candles_qs.count() < 100:
            logger.warning('⚠️ Not enough candles in database to run strategy! (Need at least 100)')
            return

        result = evaluate_market_and_execute(
            symbol=symbol,
            time_frame=time_frame,
            candles_qs=candles_qs,
            account_balance=account_balance,
            risk_percentage=risk,
        )
        logger.info(f'⚙️ Evaluation Result Status: {result.get("status")}')

        if result.get('status') == 'approved_signal':
            send_telegram_trade_alert(result)
            logger.info('✅ Signal approved and sent to Telegram!')
        else:
            logger.info(f'ℹ️ Signal not triggered. Reason: {result.get("reason")}')

    except Exception as e:
        logger.exception(f'❌ Error in run_market_ai_analysis_task: {e}')
    finally:
        close_old_connections()