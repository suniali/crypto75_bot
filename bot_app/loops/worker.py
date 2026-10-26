import asyncio
import logging

from apscheduler.schedulers.background import BackgroundScheduler

from bot_app.utils.time_frame_helper import TIMEFRAME_TO_SHEDULER

from bot_app.tasks import run_market_ai_analysis_task

logger = logging.getLogger(__name__)

FULL_LIMIT=300
SHORT_LIMIT=50
ACCOUNT_BALANCE=100.0
RISK=1.0

scheduler = BackgroundScheduler()
 

def start_ai_analysis_job(symbol:str,time_frame:str):
  logger.info(f"⚡ Starting AI Analysis Engine For {symbol} on {time_frame}...")

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
      minutes=TIMEFRAME_TO_SHEDULER.get(time_frame,5),
      args=[symbol,time_frame,FULL_LIMIT,SHORT_LIMIT,ACCOUNT_BALANCE,RISK],
      id=f'ai_trade_analysis_{symbol}_{time_frame}',
      replace_existing=True,
  )

  logger.info(f"✅ AI Analysis Scheduler started successfully (Interval: {time_frame}).")

def stop_ai_analysis_job(symbol: str, time_frame: str):
  """تابع جداگانه برای متوقف کردن و حذف جاب هوش مصنوعی یک نماد و تایم‌فریم خاص"""
  job_id = f'ai_trade_analysis_{symbol}_{time_frame}'

  try:
    # بررسی اینکه آیا این جاب در شجولر وجود دارد یا خیر
    job = scheduler.get_job(job_id)
    if job:
      scheduler.remove_job(job_id)
      logger.info(
          f'🛑 Successfully stopped and removed AI analysis job for {symbol}'
          f' [{time_frame}].'
      )
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