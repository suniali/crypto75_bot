import logging
from django.db import close_old_connections

from bot_app.services.execution_service import evaluate_market_and_execute

logger = logging.getLogger(__name__)

def run_market_ai_analysis_task(symbol, time_frame:str):
    """تسک تحلیل بازار (استراتژی NDS + فیلتر کالمن + هوش مصنوعی)"""
    close_old_connections()

    logger.info(f'🤖 [AI Task Triggered] Starting market analysis for {symbol} ...')

    try:
        result = evaluate_market_and_execute(symbol=symbol, time_frame=time_frame)
        status = result.get("status")
        logger.info(f'⚙️ Status: {status}')
        if status == 'approved_signal':
            logger.info(f'✅ Signal #{result.get("log_id")} queued for notification.')
        else:
            logger.info(f'ℹ️ Reason: {result.get("reason")}')
    except Exception as e:
        logger.exception(f'❌ Error in task: {e}')
    finally:
        close_old_connections()