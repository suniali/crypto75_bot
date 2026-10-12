import asyncio
import logging

from decouple import config

from bot_app.services.watchlist_service import get_all_watchlist
from bot_app.services.mt5_service import start_mt5_connection,stop_mt5_connection

from bot_app.workers import ACTIVE_WORKERS

from bot_app.loops.alert_loop import check_alerts_loop
from bot_app.loops.sl_tp_monitor_loop import sl_tp_monitor_loop
from bot_app.loops.worker_loop import worker_loop

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
            # استفاده از چت آیدی همان کاربر به جای ادمین
            chat_id = item.user.chat_id
            worker_key = f"{chat_id}_{item.symbol}_{item.time_frame}_{item.market_type}"

            if worker_key not in ACTIVE_WORKERS:
                task = asyncio.create_task(
                    worker_loop(item.symbol, item.time_frame, item.market_type, chat_id, app.bot)
                )
                ACTIVE_WORKERS[worker_key] = task

        logger.info("Successfully started background monitors and %d RSI worker tasks.", len(watchlist))
    except Exception as e:
        logger.exception("Error during background tasks startup: %s", e)


async def on_shutdown(app):
    logger.info("🛑 Stopping background workers...")

    for worker_key, task in ACTIVE_WORKERS.items():
        if not task.done():
            task.cancel()
            logger.info("Cancelling worker: %s", worker_key)

    await asyncio.gather(*ACTIVE_WORKERS.values(), return_exceptions=True)
    ACTIVE_WORKERS.clear()
    logger.info("✅ All background workers stopped cleanly.")

    # قطع اتصال متاتریدر ۵
    await asyncio.to_thread(stop_mt5_connection)
    logger.info("🛑 MetaTrader 5 connection closed cleanly.")