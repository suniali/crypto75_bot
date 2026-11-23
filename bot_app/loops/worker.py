import asyncio
import logging

from apscheduler.schedulers.background import BackgroundScheduler
from bot_app.services.notification_service import process_pending_notifications
from bot_app.tasks import run_market_ai_analysis_task

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler()


def start_notification_retry_job():
    """جاب دائمی برای retry سیگنال‌های ناموفق (مستقل از تحلیل بازار)"""
    if not scheduler.running:
        scheduler.start()
    scheduler.add_job(
        process_pending_notifications,
        'interval',
        seconds=30,
        id='nds_notification_retry',
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    logger.info('✅ Notification retry job started (every 30s).')

def start_ai_analysis_job(symbol:str, time_frame:str):
  logger.info(f"⚡ Starting AI Analysis Engine For {symbol} ...")

  # بررسی اینکه شجولر قبلاً استارت نخورده باشد (برای جلوگیری از تداخل احتمالی)
  if not scheduler.running:
    try:
      scheduler.start()
      logger.info('✅ APScheduler started successfully.')
    except Exception as e:
      logger.error(f'Error starting scheduler: {e}')

  # تنظیم زمان‌بندی (مثلاً هر ۱۵ دقیقه)
  scheduler.add_job(
      run_market_ai_analysis_task,
      'interval',
      minutes=1,
      args=[symbol,time_frame],
      id=f'ai_trade_analysis_{symbol}',
      replace_existing=True,
      max_instances=1,
      coalesce=True
  )

  logger.info(f"✅ AI Analysis Scheduler started successfully (ُSymbol: {symbol}).")

def stop_ai_analysis_job(symbol: str):
  """تابع جداگانه برای متوقف کردن و حذف جاب هوش مصنوعی یک نماد و تایم‌فریم خاص"""
  job_id = f'ai_trade_analysis_{symbol}'

  try:
    # بررسی اینکه آیا این جاب در شجولر وجود دارد یا خیر
    job = scheduler.get_job(job_id)
    if job:
      scheduler.remove_job(job_id)
      logger.info(f'🛑 Successfully stopped and removed AI analysis job for {symbol}')
      return True
    else:
      logger.warning(
          f'⚠️ Job ID {job_id} not found in scheduler, nothing to stop.'
      )
      return False
  except Exception as e:
    logger.exception(f'❌ Error stopping scheduler job for {symbol}: {e}')
    return False

async def shutdown_ai_jobs():
  try:
    if scheduler.running:
      # پارامتر wait=False کمک میکنه سریع‌تر و بدون بلوکه شدن بسته بشه
      await asyncio.to_thread(scheduler.shutdown, wait=False)
      logger.info('🛑 APScheduler stopped successfully.')
  except Exception as e:
    logger.error(f'Error stopping scheduler: {e}')

def _health_heartbeat():
    from bot_app.models import TradeDecisionLog
    pending = TradeDecisionLog.objects.filter(notification_status='pending').count()
    failed = TradeDecisionLog.objects.filter(notification_status='failed').count()
    logger.info(f"💓 Heartbeat | Pending: {pending} | Failed: {failed}")

scheduler.add_job(_health_heartbeat, 'interval', minutes=5, id='health_heartbeat')