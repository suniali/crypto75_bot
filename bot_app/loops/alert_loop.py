import asyncio
import logging
import httpx

from bot_app.services.alert_service import fetch_active_alerts
from bot_app.price_checker import process_alert

logger = logging.getLogger(__name__)

async def check_alerts_loop(bot) -> None:
    logger.info("🚀 Price Monitoring Worker started...")

    async with httpx.AsyncClient() as client:
        while True:
            try:
                # ۱. دریافت تمام آلرت‌های فعال (حتماً باید user رو select_related کنه)
                alerts = await fetch_active_alerts()

                if alerts:
                    # اجرا و دریافت خروجی تمام تسک‌ها هم‌زمان
                    tasks = [process_alert(client, alert) for alert in alerts]
                    results = await asyncio.gather(*tasks, return_exceptions=True)

                    # پیمایش روی نتایج و ارسال پیام/چارت در صورت وجود خروجی
                    for alert, result in zip(alerts, results):

                        # اگر در اجرای process_alert خطایی رخ داده بود
                        if isinstance(result, Exception):
                            logger.error("Error processing alert ID #%s: %s", alert.id, result)
                            continue

                        # اگر آلرت تاچ نشده و نتیجه None است
                        if not result:
                            continue

                        # اگر خروجی به صورت (msg, chart_buf) برگشته بود
                        if isinstance(result, tuple) and len(result) == 2:
                            msg, chart_buf = result

                            # دریافت chat_id از طریق رابطه کاربر
                            chat_id = alert.user.chat_id

                            if msg:
                                try:
                                    if chart_buf:
                                        await bot.send_photo(
                                            chat_id=chat_id,
                                            photo=chart_buf,
                                            caption=msg,
                                            parse_mode="Markdown"
                                        )
                                        logger.info("Photo notification sent to %s for symbol %s", chat_id,
                                                    alert.symbol)
                                    else:
                                        await bot.send_message(
                                            chat_id=chat_id,
                                            text=msg,
                                            parse_mode="Markdown"
                                        )
                                        logger.info("Text notification sent to %s for symbol %s", chat_id, alert.symbol)

                                except Exception as e:
                                    logger.exception("Failed to send notification to %s: %s", chat_id, e)

            except Exception as e:
                logger.exception("Unexpected error in main alert loop: %s", e)

            await asyncio.sleep(2)

