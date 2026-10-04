import os
import asyncio
import logging

from bot_app.utils.time_frame_helper import TIMEFRAME_TO_SECONDS
from bot_app.utils.keyboard_helper import MAIN_KEYBOARD
from bot_app.services.analysis_service import (
    calculate_rsi,get_ai_market_view,
)

logger = logging.getLogger(__name__)

async def worker_loop(symbol: str, timeframe: str, market_type: str, chat_id: int, bot):
    interval = TIMEFRAME_TO_SECONDS.get(timeframe, 1800)
    logger.info("🚀 [STARTED] RSI Monitor task: %s | %s | %s", symbol, timeframe, market_type)

    last_signal_state = None  # جلوگیری از ارسال سیگنال تکراری پشت سر هم

    try:
        while True:
            chart_path = None
            try:
                rsi, status, divergence, chart_path = await calculate_rsi(symbol, timeframe, market_type)
                logger.debug("RSI checked for %s (%s): RSI=%s, Status=%s", symbol, timeframe, rsi, status)

                # ۱. بررسی وجود سیگنال
                if rsi is not None and ("NORMAL" not in status or divergence != "بدون واگرایی"):

                    # ۲. جلوگیری از ارسال پیام‌های کاملاً تکراری در کندل‌های متوالی
                    current_state = f"{status}_{divergence}"
                    if current_state != last_signal_state:
                        logger.info("Signal detected for %s (%s)! RSI: %s | Status: %s | Divergence: %s",
                                    symbol, timeframe, rsi, status, divergence)

                        # دریافت غیربلاک‌کننده تحلیل Gemini
                        ai_analysis = await get_ai_market_view(symbol, rsi, status, divergence, market_type)

                        msg = (
                            f"🚨 <b>هشدار سیگنال RSI</b>\n\n"
                            f"📌 <b>نماد:</b> <code>{symbol}</code> | ⏳ <code>{timeframe}</code>\n"
                            f"📊 <b>RSI:</b> <code>{rsi:.2f}</code> | ⚡️ <b>وضعیت:</b> <code>{status}</code>\n"
                            f"🔍 <b>واگرایی:</b> {divergence}\n\n"
                            f"🤖 <b>دیدگاه هوش مصنوعی (Gemini):</b>\n"
                            f"<i>{ai_analysis}</i>"
                        )

                        # ارسال به تلگرام
                        if chart_path and os.path.exists(chart_path):
                            with open(chart_path, "rb") as photo:
                                await bot.send_photo(
                                    chat_id=chat_id,
                                    photo=photo,
                                    caption=msg,
                                    reply_markup=MAIN_KEYBOARD,
                                    parse_mode="HTML"
                                )
                        else:
                            await bot.send_message(
                                chat_id=chat_id,
                                text=msg,
                                reply_markup=MAIN_KEYBOARD,
                                parse_mode="HTML"
                            )

                        last_signal_state = current_state
                else:
                    # ریست کردن حالت قبلی اگر بازار به وضعیت نرمال برگشت
                    last_signal_state = None

            except Exception as e:
                logger.exception("Error during RSI calculation worker for %s: %s", symbol, e)

            finally:
                # 📌 تضمین حذف عکس حتی در صورت بروز خطا در ارسال تلگرام
                if chart_path and os.path.exists(chart_path):
                    try:
                        os.remove(chart_path)
                    except Exception as cleanup_err:
                        logger.error("Failed to delete temp chart %s: %s", chart_path, cleanup_err)

            await asyncio.sleep(interval)

    except asyncio.CancelledError:
        logger.info("🛑 [STOPPED] RSI Monitor task cancelled for %s (%s)", symbol, timeframe)