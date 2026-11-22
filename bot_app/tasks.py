import logging
from django.db import close_old_connections

from bot_app.models import MarketCandle
from bot_app.services.execution_service import evaluate_market_and_execute
from bot_app.services.market_data_service import prepare_and_update_candles
from bot_app.services.telegram_service import send_telegram_trade_alert

logger = logging.getLogger(__name__)

def run_market_ai_analysis_task(symbol):
    """تسک تحلیل بازار (استراتژی NDS + فیلتر کالمن + هوش مصنوعی)"""
    close_old_connections()

    logger.info(f'🤖 [AI Task Triggered] Starting market analysis for {symbol} ...')

    try:
        result = evaluate_market_and_execute(symbol=symbol,)
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