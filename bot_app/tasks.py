import logging
from django.db import close_old_connections

from bot_app.services.execution_service import evaluate_market_and_execute,evaluate_super_scalp_and_execute

logger = logging.getLogger(__name__)


def run_super_scalp_analysis_task(
    symbol: str,
    time_frame: str = "M1",
    model_path: str | None = None,
    require_all_5_checks: bool = False,
    ai_advisory_only: bool = True,
    ai_min_prob: float = 0.55,
):
    """تسک اجرای موتور مستقل Super-Scalp (Heikin Ashi + چک‌لیست ۵گانه + AI)."""
    close_old_connections()
    logger.info(f"⚡ [SuperScalp Task Triggered] Starting analysis for {symbol} ({time_frame}) ...")
    try:
        result = evaluate_super_scalp_and_execute(
            symbol=symbol,
            time_frame=time_frame,
            model_path=model_path,
            require_all_5_checks=require_all_5_checks,
            ai_advisory_only=ai_advisory_only,
            ai_min_prob=ai_min_prob,
        )
        status = result.get("status")
        logger.info(f"⚙️ [SuperScalp] Status: {status}")
        if status == "approved_signal":
            logger.info(f"✅ [SuperScalp] Signal #{result.get('log_id')} processed.")
        else:
            logger.info(f"ℹ️ [SuperScalp] Reason: {result.get('reason')}")
    except Exception as e:
        logger.exception(f"❌ [SuperScalp] Error in task: {e}")
    finally:
        close_old_connections()

def run_market_ai_analysis_task(symbol, time_frame: str):
    """تسک تحلیل بازار (استراتژی NDS + موتور Super-Scalp)."""
    close_old_connections()
    logger.info(f"🤖 [AI Task Triggered] Starting market analysis for {symbol} ...")
    try:
        result = evaluate_market_and_execute(symbol=symbol, time_frame=time_frame)
        status = result.get("status")
        logger.info(f"⚙️ Status: {status}")
        if status == "approved_signal":
            logger.info(f"✅ Signal #{result.get('log_id')} queued for notification.")
        else:
            logger.info(f"ℹ️ Reason: {result.get('reason')}")

        # اجرای موتور مستقل Super-Scalp بعد از استراتژی قبلی
        # scalp_res = evaluate_super_scalp_and_execute(symbol=symbol, time_frame=time_frame,require_all_5_checks=False)
        # logger.info(f"⚡ [SuperScalp] Status: {scalp_res.get('status')} | {scalp_res.get('reason', '')}")
    except Exception as e:
        logger.exception(f"❌ Error in task: {e}")
    finally:
        close_old_connections()