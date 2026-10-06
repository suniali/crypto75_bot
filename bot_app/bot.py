import os
import sys
import asyncio
import logging
import django
import warnings
import colorama

from pathlib import Path
from decouple import config

colorama.init(autoreset=True)

# ------------------------------------------------------------------
# 1. Django Setup
# ------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# 3. Imports & Configurations
# ------------------------------------------------------------------

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
    ChatMemberHandler,
    CommandHandler
)
from telegram.error import NetworkError,TimedOut
from telegram.request import HTTPXRequest
from telegram.warnings import PTBUserWarning
warnings.filterwarnings("ignore", category=PTBUserWarning)

from bot_app.utils.keyboard_helper import MAIN_KEYBOARD
from bot_app.workers import ACTIVE_WORKERS

from bot_app.services.mt5_service import start_mt5_connection,stop_mt5_connection
from bot_app.services.user_service import set_users_blocked_status,get_or_create_user
from bot_app.services.watchlist_service import get_all_watchlist

from bot_app.loops.alert_loop import check_alerts_loop
from bot_app.loops.sl_tp_monitor_loop import sl_tp_monitor_loop
from bot_app.loops.worker_loop import worker_loop

from bot_app.actions.watchlist_actions import (
    ADD_WATCHLIST_MARKET,ADD_WATCHLIST_SYMBOL,ADD_WATCHLIST_TIMEFRAME,
    show_watchlist_command,
    delete_watchlist_handler,
    confirm_delete_watchlist_handler,
    start_add_watchlist,
    add_watchlist_get_market_step,
    add_watchlist_get_symbol_step,
    add_watchlist_get_timeframe_step,
    cancel_watch_list_callback,
)
from bot_app.actions.alert_actions import (
    ADD_ALERT_MARKET,ADD_ALERT_SYMBOL,ADD_ALERT_PRICE,
    show_active_alerts_command,
    delete_alert_callback,
    confirm_delete_alert_callback,
    start_alert_wizard,
    add_alert_market_selected,
    add_alert_symbol_received,
    add_alert_price_received,
    cancel_alert_callback
)
from bot_app.actions.position_actions import (
    INPUT_NEW_SL_TP,INPUT_PARTIAL_LOT,
    NEW_TRADE_SYMBOL,NEW_TRADE_ACTION,NEW_TRADE_LOT,NEW_TRADE_PRICE,NEW_TRADE_SL,NEW_TRADE_TP,
    show_positions_handler,
    handle_position_actions,
    process_new_sltp_input,
    process_partial_close_input,
    close_all_positions_handler,
    confirm_close_all_handler,
    position_detail_callback,
    cancel_action,
    start_trade_wizard,
    new_trade_get_symbol_step,
    new_trade_get_action_step,
    new_trade_get_lot_step,
    new_trade_get_price_step,
    new_trade_get_sl_step,
    execute_trade_step,
    cancel_trade_handler,
)
from bot_app.actions.extract_journal_from_image_action import (
    WAITING_FOR_TRADE_IMAGE,EDITING_JOURNAL_DATA,CONFIRM_JOURNAL_DATA,
    start_extract_trade_wizard,
    start_manual_edit_handler,
    save_manual_edit_handler,
    process_trade_image_handler,
    confirm_journal_data_handler,
    cancel_extract_image_callback,
)
from bot_app.actions.manuel_journal_action import (
    WAITING_FOR_MANUAL_TRADE_INPUT,
    start_manual_trade_wizard,
    process_manual_trade_input,
)
from bot_app.actions.reporting_action import (
    SELECT_REPORT_PERIOD,
    start_report_wizard,
    process_report_generation,
    cancel_report_callback
)

TOKEN = config("TELEGRAM_BOT_TOKEN")
ADMIN_CHAT_ID = config("ADMIN_CHAT_ID")


# ------------------------------------------------------------------
# 5. Start And Stop Handlers
# ------------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_name = update.effective_user.first_name
    chat_id = update.effective_chat.id
    logger.info("User %s (chat_id: %s) started the bot.", user_name, chat_id)

    try:
        await get_or_create_user(chat_id,user_name)
        logger.info("✅ User %s marked as is_blocked=False in Database.", chat_id)
    except Exception as e:
        logger.error("Failed to update is_blocked in DB for user %s: %s", chat_id, e)

    # کاراکتر \u200f جهت مرتب‌سازی درست متون فارسی و انگلیسی در تلگرام
    welcome_text = (
        f"\u200fسلام {user_name} عزیز! 👋✨\n"
        f"به **دستیار هوشمند و پایشگر تخصصی ترید** خوش آمدید.\n\n"
        "🎯 **با من چه کارهایی می‌توانید انجام دهید؟**\n\n"
        "⚡ **مدیریت و اجرای لایو:**\n"
        "├ ثبت سفارشات، ریسک‌فری، خروج پله‌ای و ویرایش سریع SL/TP\n"
        "└ پایش ۵ ثانیه‌ای پوزیشن‌ها و ارسال هشدار خودکار خروج (SL/TP)\n\n"
        "🔔 **پایش بازار و سیگنال:**\n"
        "├ هشدارهای قیمتی لحظه‌ای (فارکس و کریپتو) و مدیریت واچ‌لیست\n"
        "└ تحلیل RSI، الگوهای کندلی و ساختار بازار با **Gemini AI**\n\n"
        "📝 **ژورنال‌نویسی و گزارش‌گیری:**\n"
        "├ خواندن خودکار مشخصات معامله از روی اسکرین‌شات چارت (Vision AI)\n"
        "└ صدور گزارش PDF وال‌استریت (Equity Curve، Sharpe Ratio و...)\n\n"
        "👇 *برای شروع یکی از گزینه‌های زیر را انتخاب کنید:*"
    )

    await update.message.reply_text(
        welcome_text,
        parse_mode="Markdown",
        reply_markup=MAIN_KEYBOARD
    )


async def handle_user_stop_or_block(user_id: int, context: ContextTypes.DEFAULT_TYPE = None):
    """
    تابع اصلی پاک‌سازی تسک‌ها، رم و بلاک کردن کاربر در دیتابیس
    """
    logger.info("🛑 User %s stopped/blocked the bot. Starting cleanup...", user_id)

    # ------------ ۱. متوقف کردن و حذف تسک‌های پس‌زمینه کاربر از حافظه ------------
    user_id_str = str(user_id)
    keys_to_remove = [key for key in ACTIVE_WORKERS.keys() if
                      key.startswith(f"{user_id_str}_") or key.endswith(f"_{user_id_str}")]

    for key in keys_to_remove:
        task = ACTIVE_WORKERS.pop(key, None)
        if task and not task.done():
            task.cancel()
            logger.info("Cancelled active background worker: %s", key)

    # ------------ ۲. پاک‌سازی حافظه Context و داده‌های موقت کاربر ------------
    if context:
        # پاک‌سازی user_data
        if hasattr(context, "user_data") and context.user_data:
            context.user_data.clear()

        # اگر اطلاعات مکالمه یا واچ‌لیستی در حافظه موقت رم دارید
        if hasattr(context, "chat_data") and context.chat_data:
            context.chat_data.clear()

    # ------------ ۳. به روزرسانی وضعیت کاربر در دیتابیس ------------
    try:
        await set_users_blocked_status(user_id,True)
        logger.info("✅ User %s marked as is_blocked=True in Database.", user_id)
    except Exception as e:
        logger.error("Failed to update is_blocked in DB for user %s: %s", user_id, e)

async def block_detection_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """شناسایی لحظه‌ای بلاک شدن ربات توسط کاربر"""
    chat_member = update.my_chat_member
    if not chat_member:
        return

    new_status = chat_member.new_chat_member.status

    # اگر وضعیت کاربر به kicked (بلاک کرده) تغییر کرد
    if new_status == "kicked":
        user_id = chat_member.from_user.id
        await handle_user_stop_or_block(user_id, context)


async def stop_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """پاسخ به دستور /stop کاربر"""
    user_id = update.effective_user.id

    # اجرا فرایند استاپ و پاکسازی
    await handle_user_stop_or_block(user_id, context)

    await update.message.reply_text(
        "🛑 **ربات برای شما متوقف شد و تمامی پایش‌های فعال شما لغو گردیدند.**\n\n"
        "هر زمان مایل بودید می‌توانید با فرستادن دستور /start مجدداً ربات را فعال کنید.",
        parse_mode="Markdown"
    )


# ------------------------------------------------------------------
# 14. Application Startup & Main Execution
# ------------------------------------------------------------------
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


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    if isinstance(context.error, (NetworkError, TimedOut)):
        logger.warning("Telegram network connection issue: %s", context.error)
    else:
        logger.error("Exception while handling an update:", exc_info=context.error)


if __name__ == "__main__":
    logger.info("Initializing Telegram Bot Application...")

    request = HTTPXRequest(
        connect_timeout=20.0,
        read_timeout=20.0,
        connection_pool_size=8
    )

    app = (
        ApplicationBuilder()
        .token(TOKEN)
        .request(request)
        .post_init(on_startup)
        .post_shutdown(on_shutdown)
        .build()
    )

    app.add_error_handler(error_handler)

    # ------------------ 1️⃣ دستورات اولیه و چت ------------------
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stop", stop_command_handler))
    app.add_handler(CommandHandler("showWatchlist", show_watchlist_command))
    app.add_handler(CommandHandler("positions", show_positions_handler))
    app.add_handler(ChatMemberHandler(block_detection_handler, ChatMemberHandler.MY_CHAT_MEMBER))

    # ------------------ 2️⃣ گفتگوها (Conversation Handlers) ------------------

    # واچ‌لیست
    add_watchlist_handler = ConversationHandler(
        entry_points=[
            CommandHandler("addWatchlist", start_add_watchlist),
            MessageHandler(filters.Regex("^✨ افزودن به واچ‌لیست$"), start_add_watchlist),
            CallbackQueryHandler(start_add_watchlist, pattern="^addWatchlist$"),
        ],
        states={
            ADD_WATCHLIST_MARKET: [
                CallbackQueryHandler(cancel_watch_list_callback, pattern="^cancel_watchlist$"),
                CallbackQueryHandler(add_watchlist_get_market_step, pattern="^(CRYPTO|FOREX)$"),
            ],
            ADD_WATCHLIST_SYMBOL: [
                CallbackQueryHandler(cancel_watch_list_callback, pattern="^cancel_watchlist$"),
                CallbackQueryHandler(add_watchlist_get_symbol_step, pattern="^sym_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_watchlist_get_symbol_step),
            ],
            ADD_WATCHLIST_TIMEFRAME: [
                CallbackQueryHandler(cancel_watch_list_callback, pattern="^cancel_watchlist$"),
                CallbackQueryHandler(add_watchlist_get_timeframe_step, pattern="^(5m|15m|30m|1h|4h|1d)$"),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(cancel_watch_list_callback, pattern="^cancel_watchlist$"),
        ]
    )
    app.add_handler(add_watchlist_handler)

    # هشدار قیمت
    alert_handler = ConversationHandler(
        entry_points=[
            CommandHandler("setalert", start_alert_wizard),
            MessageHandler(filters.Regex("^ثبت هشدار قیمت 🔔$"), start_alert_wizard),
            CallbackQueryHandler(start_alert_wizard, pattern="^add_alert$")
        ],
        states={
            ADD_ALERT_MARKET: [
                CallbackQueryHandler(add_alert_market_selected, pattern="^(market_crypto|market_forex|cancel_alert)$")
            ],
            ADD_ALERT_SYMBOL: [
                CallbackQueryHandler(add_alert_symbol_received, pattern="^(select_sym:|cancel_alert)"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_alert_symbol_received),
            ],
            ADD_ALERT_PRICE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_alert_price_received),
                CallbackQueryHandler(cancel_alert_callback, pattern="^cancel_alert$"),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(cancel_alert_callback, pattern="^cancel_alert$")
        ]
    )
    app.add_handler(alert_handler)

    # 💡 مدیریت جامع پوزیشن‌ها (تک‌مرحله‌ای + چندمرحله‌ای)
    pos_management_conv = ConversationHandler(
        entry_points=[
            # پشتیبانی کامل از تمام اکشن‌های اکشن‌دار و ساده
            CallbackQueryHandler(handle_position_actions, pattern=r"^(action_[a-zA-Z0-9]+_|close_pos_)")
        ],
        states={
            INPUT_NEW_SL_TP: [
                CallbackQueryHandler(cancel_action, pattern="^cancel_action_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, process_new_sltp_input)
            ],
            INPUT_PARTIAL_LOT: [
                CallbackQueryHandler(cancel_action, pattern="^cancel_action_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, process_partial_close_input)
            ],
        },
        fallbacks=[
            CallbackQueryHandler(cancel_action, pattern="^cancel_action_"),
            CommandHandler("cancel", cancel_action),
            CallbackQueryHandler(show_positions_handler, pattern="^refresh_positions_list$"),
            CallbackQueryHandler(position_detail_callback, pattern="^pos_detail_")
        ]
    )
    app.add_handler(pos_management_conv)

    # معامله جدید
    trade_wizard_handler = ConversationHandler(
        entry_points=[
            CommandHandler("newtrade", start_trade_wizard),
            CommandHandler("trade", start_trade_wizard),
            CallbackQueryHandler(start_trade_wizard, pattern="^start_new_trade$"),
            MessageHandler(filters.Regex(r"^\s*📈 معامله جدید$"), start_trade_wizard),
        ],
        states={
            NEW_TRADE_SYMBOL: [
                CallbackQueryHandler(cancel_trade_handler, pattern="^cancel_trade$"),
                CallbackQueryHandler(new_trade_get_symbol_step, pattern="^sym_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, new_trade_get_symbol_step),
            ],
            NEW_TRADE_ACTION: [
                CallbackQueryHandler(cancel_trade_handler, pattern="^cancel_trade$"),
                CallbackQueryHandler(new_trade_get_action_step, pattern="^act_"),
            ],
            NEW_TRADE_LOT: [
                CallbackQueryHandler(cancel_trade_handler, pattern="^cancel_trade$"),
                CallbackQueryHandler(new_trade_get_lot_step, pattern="^lot_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, new_trade_get_lot_step),
            ],
            NEW_TRADE_PRICE: [
                CallbackQueryHandler(cancel_trade_handler, pattern="^cancel_trade$"),
                CallbackQueryHandler(new_trade_get_price_step, pattern="^price_market$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, new_trade_get_price_step),
            ],
            NEW_TRADE_SL: [
                CallbackQueryHandler(cancel_trade_handler, pattern="^cancel_trade$"),
                CallbackQueryHandler(new_trade_get_sl_step, pattern="^sl_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, new_trade_get_sl_step),
            ],
            NEW_TRADE_TP: [
                CallbackQueryHandler(cancel_trade_handler, pattern="^cancel_trade$"),
                CallbackQueryHandler(execute_trade_step, pattern="^tp_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, execute_trade_step),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(cancel_trade_handler, pattern="^cancel_trade$"),
            MessageHandler(filters.Regex(r"^\s*(❌ انصراف|لغو)\s*$"), cancel_trade_handler),
        ]
    )
    app.add_handler(trade_wizard_handler)

    # استخراج عکس / ژورنال
    extract_image_handler = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex(r"^\s*📸 استخراج معامله از عکس$"), start_extract_trade_wizard),
            MessageHandler(filters.Regex(r"^\s*✍️ ثبت دستی معامله$"), start_manual_trade_wizard)
        ],
        states={
            WAITING_FOR_TRADE_IMAGE: [
                MessageHandler(filters.PHOTO, process_trade_image_handler),
                CallbackQueryHandler(cancel_extract_image_callback, pattern="^cancel_trade_extraction$")
            ],
            WAITING_FOR_MANUAL_TRADE_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, process_manual_trade_input),
                CallbackQueryHandler(cancel_extract_image_callback, pattern="^cancel_trade_extraction$")
            ],
            CONFIRM_JOURNAL_DATA: [
                CallbackQueryHandler(confirm_journal_data_handler, pattern="^confirm_journal_yes$"),
                CallbackQueryHandler(start_manual_edit_handler, pattern="^edit_journal_manual$"),
                CallbackQueryHandler(cancel_extract_image_callback, pattern="^confirm_journal_no$")
            ],
            EDITING_JOURNAL_DATA: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, save_manual_edit_handler)
            ]
        },
        fallbacks=[
            MessageHandler(filters.Regex(r"^\s*(❌ انصراف|لغو)\s*$"), cancel_extract_image_callback),
            CallbackQueryHandler(cancel_extract_image_callback,
                                 pattern="^(confirm_journal_no|cancel_trade_extraction)$")
        ]
    )
    app.add_handler(extract_image_handler)

    # گزارش‌گیری
    report_handler = ConversationHandler(
        entry_points=[
            CommandHandler("report", start_report_wizard),
            MessageHandler(filters.Regex(r"^\s*📊 دریافت گزارش \(PDF\)\s*$"), start_report_wizard)
        ],
        states={
            SELECT_REPORT_PERIOD: [
                CallbackQueryHandler(cancel_report_callback, pattern="^cancel_report$"),
                CallbackQueryHandler(process_report_generation, pattern="^rep_")
            ]
        },
        fallbacks=[
            CommandHandler("stop", cancel_report_callback),
            CallbackQueryHandler(cancel_report_callback, pattern="^cancel_report$"),
            MessageHandler(filters.Regex(r"^\s*(❌ انصراف|لغو|/stop)\s*$"), cancel_report_callback)
        ]
    )
    app.add_handler(report_handler)

    # ------------------ 3️⃣ کیبورد و دکمه‌های متنی اصلی ------------------
    app.add_handler(MessageHandler(filters.Regex("^📋 واچ‌لیست$"), show_watchlist_command))
    app.add_handler(MessageHandler(filters.Regex("^📊 پوزیشن‌های باز$"), show_positions_handler))
    app.add_handler(MessageHandler(filters.Regex("^🔔 هشدارهای فعال$"), show_active_alerts_command))

    # ------------------ 4️⃣ Callback Query Handlers عمومی (بدون تداخل) ------------------
    # هشدارهای قیمت
    app.add_handler(CallbackQueryHandler(confirm_delete_alert_callback, pattern="^confirm_del_alert_"))
    app.add_handler(CallbackQueryHandler(delete_alert_callback, pattern="^do_del_alert_"))
    app.add_handler(CallbackQueryHandler(show_active_alerts_command, pattern="^back_to_alerts_list$"))

    # واچ لیست
    app.add_handler(CallbackQueryHandler(show_watchlist_command, pattern="^showWatchlist$"))
    app.add_handler(CallbackQueryHandler(delete_watchlist_handler, pattern="^del_watchlist_"))
    app.add_handler(CallbackQueryHandler(confirm_delete_watchlist_handler, pattern="^confirm_del_watchlist_"))

    # نمایش لیست و جزئیات پوزیشن‌ها (اکشن‌ها در Conversation Handler بالا قرار گرفتند)
    app.add_handler(CallbackQueryHandler(show_positions_handler, pattern="^refresh_positions_list$"))
    app.add_handler(CallbackQueryHandler(position_detail_callback, pattern="^pos_detail_"))

    # بستن یکباره تمام پوزیشن‌ها
    app.add_handler(CallbackQueryHandler(close_all_positions_handler, pattern="^close_all_positions$"))
    app.add_handler(CallbackQueryHandler(confirm_close_all_handler, pattern="^confirm_close_all$"))
    # ------------------ 5️⃣ اجرا ------------------

    logger.info("🤖 Bot is polling for updates...")
    while True:
        try:
            app.run_polling(
                poll_interval=2.0,
                timeout=30,
            )
        except (NetworkError, httpx.RemoteProtocolError) as e:
            logger.warning(f"⚠️ Network connection lost: {e}. Reconnecting in 5 seconds...")
            time.sleep(5)

        except Exception as e:
            logger.error(f"❌ Unexpected critical error: {e}", exc_info=True)
            time.sleep(10)