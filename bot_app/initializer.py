import asyncio
import logging

from decouple import config

from bot_app.services.watchlist_service import get_all_watchlist
from bot_app.services.mt5_service import start_mt5_connection,stop_mt5_connection

from bot_app.workers import ACTIVE_WORKERS

from bot_app.loops.alert_loop import check_alerts_loop
from bot_app.loops.sl_tp_monitor_loop import sl_tp_monitor_loop
from bot_app.loops.worker import start_ai_analysis_job,shutdown_ai_jobs

logger = logging.getLogger(__name__)
ADMIN_CHAT_ID = config("ADMIN_CHAT_ID")


async def on_startup(app):
    logger.info("⚡ Initializing MetaTrader 5 Connection...")
    mt5_initialized = await asyncio.to_thread(start_mt5_connection)
    if not mt5_initialized:
        logger.error("❌ Failed to initialize MetaTrader 5")
        return

    logger.info("✅ MetaTrader 5 initialized successfully.")
    try:
        logger.info("Starting bot background workers...")

        # ۱. استارت ورکر عمومی پایش استاپ‌لوس و تیک‌پرافیت برای ادمین
        if ADMIN_CHAT_ID:
            sl_tp_task = asyncio.create_task(
                sl_tp_monitor_loop(int(ADMIN_CHAT_ID), app.bot)
            )
            ACTIVE_WORKERS["global_sl_tp_monitor"] = sl_tp_task

        # ۲. استارت ورکر آلرت قیمت
        price_alert_task = asyncio.create_task(
            check_alerts_loop(app.bot)
        )
        ACTIVE_WORKERS["price_alert_monitor"] = price_alert_task

        # ۳. استارت ورکرهای RSI برای نمادهای واچ‌لیست کاربران
        watchlist = await get_all_watchlist()

        for item in watchlist:
            start_ai_analysis_job(item.symbol,item.time_frame)


    except Exception as e:
        logger.exception("Error during background tasks startup: %s", e)


async def on_shutdown(app_instance=None):
  logger.info('🛑 Stopping background workers and application...')

  # ۱. متوقف کردن تسک‌های قدیمی (اگر هنوز وجود دارند)
  for worker_key, task in ACTIVE_WORKERS.items():
    if not task.done():
      task.cancel()
      logger.info('Cancelling worker: %s', worker_key)

  if ACTIVE_WORKERS:
    await asyncio.gather(*ACTIVE_WORKERS.values(), return_exceptions=True)
    ACTIVE_WORKERS.clear()

  # ۲. متوقف کردن جاب‌ها و شجولر هوش مصنوعی
  await shutdown_ai_jobs()  # یا تابع پاکسازی شجولر شما

  logger.info('✅ All background workers stopped cleanly.')

  # ۳. قطع اتصال متاتریدر ۵
  await asyncio.to_thread(stop_mt5_connection)
  logger.info('🛑 MetaTrader 5 connection closed cleanly.')

  # ۴. خاموش کردن کامل ربات تلگرام (برای اینکه فرآیند پولینگ متوقف شود و ربات کاملاً خاموش شود)
  try:
    # اگر متغیر app تلگرام در دسترس است:
    global app  # یا از طریق شیء اپلیکیشنی که دارید
    if 'app' in globals() and app:
      await app.stop()
      await app.shutdown()
      logger.info('🛑 Telegram Bot application stopped cleanly.')
  except Exception as e:
    logger.error(f'Error stopping Telegram application: {e}')