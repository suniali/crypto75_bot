import asyncio
import logging
import os
import sys
import inspect
from pathlib import Path

import django
from asgiref.sync import sync_to_async
from decouple import config

# ------------------------------------------------------------------
# 1. Django Setup
# ------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

# ------------------------------------------------------------------
# 2. Logging Configuration
# ------------------------------------------------------------------
logger = logging.getLogger("telegram_bot")
logger.setLevel(logging.INFO)

formatter = logging.Formatter(
    fmt="%(asctime)s | %(levelname)-7s | %(funcName)s:%(lineno)d - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# Console Handler
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

# File Handler
file_handler = logging.FileHandler("telegram_bot.log", encoding="utf-8")
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

# ------------------------------------------------------------------
# 3. Imports & Configurations
# ------------------------------------------------------------------
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)
from telegram.error import NetworkError,TimedOut,BadRequest
from telegram.request import HTTPXRequest

from bot_app.analysis_service import calculate_rsi
from bot_app.models import UserAlert, Watchlist
from bot_app.mt5_service import (
    check_symbol_info,
    close_all_positions,
    close_position_by_ticket,
    execute_trade,
    get_open_positions,
    close_position,
    set_break_even,
    update_position_sltp,
    get_market_watch_symbols,
)

TOKEN = config("TELEGRAM_BOT_TOKEN")
ADMIN_CHAT_ID = config("ADMIN_CHAT_ID")

TIMEFRAME_TO_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}

ADD_WATCHLIST_SYMBOL, ADD_WATCHLIST_TIMEFRAME, ADD_WATCHLIST_MARKET = (
    "ADD_WATCHLIST_SYMBOL",
    "ADD_WATCHLIST_TIMEFRAME",
    "ADD_WATCHLIST_MARKET"
)
ACTIVE_WORKERS = {}  # برای مدیریت و متوقف کردن تسک‌های پس‌زمینه هنگام حذف


# ------------------------------------------------------------------
# 4. Database Async Helpers
# ------------------------------------------------------------------
@sync_to_async
def save_alert_to_db(chat_id: str, symbol: str, target_price: float, is_forex: bool):
    market_type = "FOREX" if is_forex else "CRYPTO"
    exists = UserAlert.objects.filter(
        symbol=symbol, target_price=target_price, is_active=True
    ).exists()
    if exists:
        logger.warning("Alert already exists for chat_id %s, symbol %s at target %s", chat_id, symbol, target_price)
        return None
    alert = UserAlert.objects.create(
        chat_id=chat_id, symbol=symbol, target_price=target_price, market_type=market_type
    )
    logger.info("Alert created successfully: ID #%s for %s at %s", alert.id, symbol, target_price)
    return alert


@sync_to_async
def get_all_watchlist():
    return list(Watchlist.objects.all())


@sync_to_async
def save_watchlist_item(symbol: str, timeframe: str, market_type: str):
    obj, created = Watchlist.objects.get_or_create(
        symbol=symbol, time_frame=timeframe, market_type=market_type
    )
    if created:
        logger.info("New watchlist item added: %s | %s | %s", symbol, timeframe, market_type)
    else:
        logger.info("Watchlist item already existed: %s | %s | %s", symbol, timeframe, market_type)
    return created


@sync_to_async
def delete_from_watchlist(symbol: str, timeframe: str, market_type: str):
    deleted_count, _ = Watchlist.objects.filter(
        symbol=symbol, time_frame=timeframe, market_type=market_type
    ).delete()
    if deleted_count > 0:
        logger.info("Deleted %s from watchlist (%s, %s)", symbol, timeframe, market_type)
    else:
        logger.warning("Failed to delete %s from watchlist or item not found", symbol)
    return deleted_count > 0


# ------------------------------------------------------------------
# 5. Start And Stop Handlers
# ------------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_name = update.effective_user.first_name
    chat_id = update.effective_chat.id
    logger.info("User %s (chat_id: %s) started the bot.", user_name, chat_id)

    welcome_text = (
        f"سلام **{user_name}** عزیز! 👋\n"
        f"به **ربات دستیار و پایشگر ترید** خوش آمدید.\n\n"
        " راهنمای جامع دستورات ربات به شرح زیر است:\n\n"
        "🔔 **تنظیم هشدارهای قیمت (Price Alerts)**\n"
        "├ 🔸 کریپتو: `/alert <نماد> <قیمت>`\n"
        "│   مثال: `/alert BTCUSDT 65000`\n"
        "├ 🔹 فارکس/فلزات: `/falert <نماد> <قیمت>`\n"
        "│   مثال: `/falert XAUUSD-ECN 2100`\n\n"
        "📈 **مدیریت معاملات (Trading & Positions)**\n"
        "├ 🟢 ثبت معامله: `/trade <BUY/SELL> <نماد> <حجم> <SL> <TP>`\n"
        "│   مثال: `/trade BUY EURUSD 0.1 300 600`\n"
        "├ 📊 پوزیشن‌های باز: `/positions`\n"
        "└ ❌ بستن سریع همه: `/closeAll`\n\n"
        "📋 **مدیریت واچ‌لیست (Watchlist & RSI Worker)**\n"
        "├ 👁 مشاهده واچ‌لیست: `/showWatchlist`\n"
        "├ ➕ افزودن نماد: `/addWatchlist`\n\n"
        "💡 _برای استفاده سریع‌تر می‌توانید از دکمه‌های زیر استفاده کنید._"
    )

    main_keyboard = ReplyKeyboardMarkup(
        [["ثبت هشدار قیمت 🔔"],["📋 واچ‌لیست", "✨ افزودن به واچ‌لیست"], ["📈 معامله جدید","📊 پوزیشن‌های باز"]],
        resize_keyboard=True
    )
    await update.message.reply_text(welcome_text, parse_mode="Markdown", reply_markup=main_keyboard)

# ------------------------------------------------------------------
# 5.َAlert Handlers
# ------------------------------------------------------------------
ADD_ALERT_MARKET, ADD_ALERT_SYMBOL, ADD_ALERT_PRICE = (
    "ADD_ALERT_MARKET",
    "ADD_ALERT_SYMBOL",
    "ADD_ALERT_PRICE"
)

async def start_alert_wizard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """شروع فرایند ثبت هشدار و نمایش دکمه انتخاب بازار"""
    keyboard = [
        [
            InlineKeyboardButton("🪙 ارز دیجیتال (Crypto)", callback_data="market_crypto"),
            InlineKeyboardButton("📊 فارکس (Forex)", callback_data="market_forex"),
        ],
        [InlineKeyboardButton("❌ انصراف", callback_data="cancel_alert")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "🔔 **به بخش ثبت هشدار قیمت خوش آمدید.**\n\nلطفاً نوع بازار را انتخاب کنید:",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )
    return ADD_ALERT_MARKET

async def add_alert_market_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """پردازش انتخاب بازار و درخواست نماد از کاربر"""
    query = update.callback_query
    await query.answer()

    if query.data == "cancel_alert":
        await query.edit_message_text("❌ **ثبت هشدار لغو شد.**", parse_mode="Markdown")
        return ConversationHandler.END

    is_forex = (query.data == "market_forex")
    context.user_data["is_forex"] = is_forex

    market_name = "فارکس" if is_forex else "ارز دیجیتال"
    example = "XAUUSD-ECN" if is_forex else "BTCUSDT"

    # دکمه لغو برای مراحل بعدی
    cancel_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("❌ انصراف", callback_data="cancel_alert")]])

    await query.edit_message_text(
        f"🌐 بازار انتخاب‌شده: **{market_name}**\n\n"
        f"✍️ لطفاً **نماد** مورد نظر را وارد کنید (مثال: `{example}`):",
        reply_markup=cancel_keyboard,
        parse_mode="Markdown"
    )
    return ADD_ALERT_SYMBOL


async def add_alert_symbol_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دریافت و اعتبارسنجی نماد واردشده"""
    symbol = update.message.text.strip().upper()
    is_forex = context.user_data.get("is_forex", False)
    cancel_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("❌ انصراف", callback_data="cancel_alert")]])

    # بررسی صحت نماد فارکس در متاتریدر
    if is_forex:
        status_msg = await update.message.reply_text("⏳ **در حال بررسی نماد در متاتریدر...**", parse_mode="Markdown")
        loop = asyncio.get_running_loop()
        res = await loop.run_in_executor(None, check_symbol_info, symbol)

        if res is not True:
            if isinstance(res, list):
                logger.warning("Forex symbol %s not found. Suggestions: %s", symbol, res[:5])
                suggestions = "\n".join([f"▫️ `{s}`" for s in res[:10]])
                await status_msg.edit_text(
                    f"⚠️ **نماد `{symbol}` یافت نشد!**\n\n💡 پیشنهادها:\n{suggestions}\n\nلطفاً نماد را مجدداً ارسال کنید:",
                    reply_markup=cancel_keyboard,
                    parse_mode="Markdown"
                )
            else:
                logger.error("Error connecting to MetaTrader 5 while checking symbol %s", symbol)
                await status_msg.edit_text("🚨 **خطا در اتصال به MetaTrader 5!** ثبت هشدار لغو شد.", parse_mode="Markdown")
                return ConversationHandler.END
            return ADD_ALERT_SYMBOL

        await status_msg.delete()

    context.user_data["symbol"] = symbol

    await update.message.reply_text(
        f"✅ نماد: `{symbol}`\n\n🎯 حالا **قیمت هدف** مد نظر خود را به عدد وارد کنید:",
        reply_markup=cancel_keyboard,
        parse_mode="Markdown"
    )
    return ADD_ALERT_PRICE


async def add_alert_price_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دریافت قیمت هدف و ذخیره نهایی هشدار"""
    chat_id = update.effective_chat.id
    symbol = context.user_data.get("symbol")
    is_forex = context.user_data.get("is_forex", False)

    try:
        target_price = float(update.message.text.strip())
    except ValueError:
        cancel_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("❌ انصراف", callback_data="cancel_alert")]])
        await update.message.reply_text(
            "❌ **قیمت هدف باید یک عدد معتبر باشد.**\nلطفاً قیمت را دوباره وارد کنید:",
            reply_markup=cancel_keyboard,
            parse_mode="Markdown"
        )
        return ADD_ALERT_PRICE

    # ذخیره در دیتابیس
    await save_alert_to_db(str(chat_id), symbol, target_price, is_forex)

    logger.info("Alert created successfully: %s at %s for chat_id %s", symbol, target_price, chat_id)
    await update.message.reply_text(
        f"🔔 **هشدار قیمت با موفقیت ثبت شد!**\n\n"
        f"📌 **نماد:** `{symbol}`\n"
        f"🎯 **قیمت هدف:** `{target_price}`\n"
        f"🌐 **بازار:** {'فارکس' if is_forex else 'ارز دیجیتال'}",
        parse_mode="Markdown"
    )

    # پاکسازی داده‌های موقت کاربر
    context.user_data.clear()
    return ConversationHandler.END

async def cancel_alert_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """لغو عملیات در صورت کلیک روی دکمه انصراف"""
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await query.edit_message_text("❌ **ثبت هشدار لغو شد.**", parse_mode="Markdown")
    return ConversationHandler.END

# ------------------------------------------------------------------
# 6. Position & Trade Handlers
# ------------------------------------------------------------------
async def auto_refresh_positions_job(context: ContextTypes.DEFAULT_TYPE):
    """آپدیت خودکار پیام لیست پوزیشن‌ها هر چند ثانیه یک‌بار"""
    job = context.job
    chat_id = job.chat_id
    job_data = job.data or {}
    message_id = job_data.get("message_id")

    if not message_id:
        job.schedule_removal()
        return

    # دریافت جدیدترین لیست پوزیشن‌ها از متاتریدر در Executor
    loop = asyncio.get_running_loop()
    try:
        success, positions = await loop.run_in_executor(None, get_open_positions)
    except Exception as e:
        logger.error(f"Error fetching positions in background job: {e}")
        return

    # اگر پوزیشنی وجود نداشت یا خطا رخ داد
    if not success or not positions:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text="📭 <b>در حال حاضر هیچ پوزیشن بازی وجود ندارد.</b>",
                parse_mode="HTML",
            )
        except BadRequest as e:
            if "message is not modified" not in str(e).lower():
                logger.warning(f"Failed to edit empty message: {e}")
                job.schedule_removal()
        except Exception as e:
            logger.error(f"Unexpected error when clearing message: {e}")
            job.schedule_removal()

        job.schedule_removal()  # توقف تایمر
        return

    # ساخت متن جدید با فرمت HTML
    text = "🔄 <b>لیست پوزیشن‌های فعال (بروزرسانی زنده):</b>\n\n"
    total_profit = 0.0
    keyboard = []

    for pos in positions:
        profit = pos.get("profit", 0.0)
        total_profit += profit
        profit_icon = "🟢" if profit >= 0 else "🔴"

        symbol = pos.get('symbol', 'N/A')
        pos_type = pos.get('type', 'N/A')
        ticket = pos.get('ticket', '')
        volume = pos.get('volume', 0)
        price_open = pos.get('price_open', 0)
        price_current = pos.get('price_current', 0)

        text += (
            f"🔹 <b>تیکت:</b> <code>{ticket}</code> | <b>{symbol}</b> ({pos_type})\n"
            f"📊 <b>حجم:</b> <code>{volume}</code> | <b>ورود:</b> <code>{price_open}</code>\n"
            f"📈 <b>قیمت فعلی:</b> <code>{price_current:.5f}</code>\n"
            f"{profit_icon} <b>سود/ضرر:</b> <code>{profit:.2f}$</code>\n"
            f"➖➖➖➖➖➖➖➖➖➖\n"
        )

        keyboard.append([
            InlineKeyboardButton(
                f"⚙️ مدیریت پوزیشن {ticket}",
                callback_data=f"pos_detail_{ticket}"
            )
        ])

    text += f"\n💰 <b>مجموع سود/ضرر کل:</b> <code>{total_profit:.2f}$</code>"

    # دکمه‌های کنترلی
    keyboard.append([InlineKeyboardButton("💥 بستن همه پوزیشن‌ها", callback_data="close_all_positions")])

    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML",
        )
    except BadRequest as e:
        err_msg = str(e).lower()
        if "message is not modified" in err_msg:
            pass
        else:
            # اگر پیام ویرایش نمی‌شود (تغییر ماهیت داده، پاک شده یا کاربر منو را عوض کرده)، تایمر متوقف شود
            logger.warning(f"Stopping live_pos_job for chat {chat_id} due to BadRequest: {e}")
            job.schedule_removal()
    except Exception as e:
        logger.error(f"Error updating positions job: {e}")
        job.schedule_removal()


async def auto_refresh_single_position_job(context: ContextTypes.DEFAULT_TYPE):
    """آپدیت خودکار جزییات یک پوزیشن خاص هر چند ثانیه یک‌بار"""
    job = context.job
    chat_id = job.chat_id
    job_data = job.data or {}
    message_id = job_data.get("message_id")
    ticket = job_data.get("ticket")

    if not message_id or not ticket:
        job.schedule_removal()
        return

    loop = asyncio.get_running_loop()
    try:
        success, positions = await loop.run_in_executor(None, get_open_positions)
    except Exception as e:
        logger.error(f"Error fetching single position in background job: {e}")
        return

    pos = next((p for p in positions if p["ticket"] == ticket), None) if success and positions else None

    # اگر پوزیشن بسته شده باشد، اطلاع بده و تایمر را متوقف کن
    if not pos:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=f"❌ <b>پوزیشن <code>{ticket}</code> بسته شده است یا یافت نشد.</b>",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت به لیست", callback_data="refresh_positions_list")]]),
                parse_mode="HTML",
            )
        except Exception:
            pass
        job.schedule_removal()
        return

    trade_type = "🟢 BUY" if pos["type"] == "BUY" else "🔴 SELL"
    profit_emoji = "🟢" if pos["profit"] >= 0 else "🔴"

    caption = (
        f"⚙️ <b>مدیریت پوزیشن <code>{pos['symbol']}</code></b> (🎫 <code>{pos['ticket']}</code>)\n\n"
        f"🔹 <b>نوع:</b> {trade_type} | 📦 <b>حجم:</b> <code>{pos['volume']}</code> لات\n"
        f"💵 <b>قیمت ورود:</b> <code>{pos['price_open']}</code>\n"
        f"📈 <b>قیمت لحظه‌ای:</b> <code>{pos['price_current']:.5f}</code>\n"
        f"🛑 <b>SL:</b> <code>{pos['sl']}</code> | 🎯 <b>TP:</b> <code>{pos['tp']}</code>\n"
        f"───────────────────\n"
        f"📊 <b>سود/زیان لحظه‌ای:</b> {profit_emoji} <b><code>${pos['profit']:,.2f}</code></b>"
    )

    keyboard = [
        [
            InlineKeyboardButton("🛡 فری‌ریسک (Break-Even)", callback_data=f"action_be_{ticket}"),
            InlineKeyboardButton("✂️ خروج ۵۰٪", callback_data=f"action_close50_{ticket}"),
        ],
        [
            InlineKeyboardButton("⚙️ تغییر SL / TP", callback_data=f"action_editsltp_{ticket}"),
            InlineKeyboardButton("📉 خروج جزئی دلخواه", callback_data=f"action_partial_{ticket}"),
        ],
        [
            InlineKeyboardButton("❌ بستن کامل پوزیشن", callback_data=f"close_pos_{ticket}"),
        ],
        [
            InlineKeyboardButton("🔙 بازگشت به لیست", callback_data="refresh_positions_list"),
        ],
    ]

    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=caption,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML",
        )
    except BadRequest as e:
        err_msg = str(e).lower()
        if "message is not modified" in err_msg:
            pass
        else:
            # اگر کاربر دکمه اکشنی زده (مثلا ویرایش SL/TP) و متن تغییر کرده، لایو تک‌پوزیشن فوراً کشته شود
            logger.info(f"Stopping live_single_pos job for ticket {ticket} due to UI transition.")
            job.schedule_removal()
    except Exception as e:
        logger.error(f"Error in auto_refresh_single_position_job: {e}")
        job.schedule_removal()


async def show_positions_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش لیست پوزیشن‌های باز و فعال‌سازی آپدیت زنده (Live Update)"""
    chat_id = update.effective_chat.id
    query = update.callback_query

    if query:
        await query.answer()

    # ۱. متوقف کردن تمامی تایمرهای قبلی (لیست کلی و تک پوزیشن)
    if context.job_queue:
        for job_name in [f"live_pos_{chat_id}", f"live_single_pos_{chat_id}"]:
            for job in context.job_queue.get_jobs_by_name(job_name):
                job.schedule_removal()
                logger.info("Stopped job %s for chat %s", job_name, chat_id)


    # ۲. دریافت پوزیشن‌ها از متاتریدر
    loop = asyncio.get_running_loop()
    success, positions = await loop.run_in_executor(None, get_open_positions)

    if not success:
        error_msg = positions if isinstance(positions, str) else "❌ **خطا در دریافت پوزیشن‌ها**"
        if query:
            await query.edit_message_text(error_msg, parse_mode="Markdown")
        else:
            await update.message.reply_text(error_msg, parse_mode="Markdown")
        return

    # ۳. بررسی خالی بودن لیست پوزیشن‌ها
    if not positions:
        text = "📭 **در حال حاضر هیچ پوزیشن بازی وجود ندارد.**"
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔄 بروزرسانی مجدد", callback_data="refresh_positions_list")]])

        if query:
            await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
        else:
            await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")
        return

    # ۴. ساخت متن خروجی و محاسبه سود/ضرر کل
    text = "🔄 **لیست پوزیشن‌های فعال (بروزرسانی زنده):**\n\n"
    total_profit = 0.0

    keyboard = []
    for pos in positions:
        profit = pos["profit"]
        total_profit += profit
        profit_icon = "🟢" if profit >= 0 else "🔴"

        text += (
            f"🔹 **تیکت:** `{pos['ticket']}` | **{pos['symbol']}** ({pos['type']})\n"
            f"📊 **حجم:** `{pos['volume']}` | **ورود:** `{pos['price_open']:.5f}`\n"
            f"📈 **قیمت فعلی:** `{pos['price_current']:.5f}`\n"
            f"{profit_icon} **سود/ضرر:** `{profit}$`\n"
            f"➖➖➖➖➖➖➖➖➖➖\n"
        )

        # اضافه کردن دکمه مدیریت اختصاصی برای هر پوزیشن
        keyboard.append(
            [InlineKeyboardButton(f"⚙️ مدیریت پوزیشن {pos['ticket']}", callback_data=f"pos_detail_{pos['ticket']}")])

    text += f"\n💰 **مجموع سود/ضرر کل:** `{round(total_profit, 2)}$`"

    # اضافه کردن دکمه‌های کنترلی اصلی
    keyboard.append([InlineKeyboardButton("💥 بستن همه پوزیشن‌ها", callback_data="close_all_positions")])

    reply_markup = InlineKeyboardMarkup(keyboard)

    # ۵. ارسال یا ویرایش پیام
    if query:
        msg = await query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")
        message_id = msg.message_id
    else:
        msg = await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")
        message_id = msg.message_id

    # ۶. تنظیم و شروع تایمر آپدیت زنده (هر ۳ ثانیه یک‌بار)
    context.job_queue.run_repeating(
        auto_refresh_positions_job,
        interval=3,  # بازه زمانی بروزرسانی (برحسب ثانیه)
        first=3,
        chat_id=chat_id,
        data={"message_id": message_id},
        name=f"live_pos_{chat_id}",
    )


async def stop_all_live_jobs(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    """تابع کمکی برای حذف تمامی تایمرهای مربوط به یک چت"""
    if context.job_queue:
        for job_name in [f"live_pos_{chat_id}", f"live_single_pos_{chat_id}"]:
            for job in context.job_queue.get_jobs_by_name(job_name):
                job.schedule_removal()
                logger.info("Stopped job %s for chat %s", job_name, chat_id)


def build_positions_keyboard(data):
    """ساخت کیبورد لیست پوزیشن‌ها"""
    keyboard = []
    for pos in data:
        p_emoji = "🟢" if pos['profit'] >= 0 else "🔴"
        btn_text = f"{p_emoji} {pos['symbol']} | {pos['type']} {pos['volume']}L | ${pos['profit']:,.2f}"
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"pos_detail_{pos['ticket']}")])

    keyboard.append([InlineKeyboardButton("💥 بستن همه پوزیشن‌ها", callback_data="close_all_positions$")])
    keyboard.append([InlineKeyboardButton("🔄 بروزرسانی لیست", callback_data="refresh_positions_list")])
    return InlineKeyboardMarkup(keyboard)

async def position_detail_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    chat_id = update.effective_chat.id
    ticket = int(query.data.split("_")[2])

    # ۱. توقف حتمی تایمرهای قبلی
    await stop_all_live_jobs(chat_id, context)

    # ۲. دریافت پوزیشن از متاتریدر به صورت Non-blocking
    loop = asyncio.get_running_loop()
    success, positions = await loop.run_in_executor(None, get_open_positions)

    pos = next((p for p in positions if p["ticket"] == ticket), None) if success and positions else None

    if not pos:
        await query.edit_message_text(
            "❌ **این پوزیشن یافت نشد یا قبلاً بسته شده است.**",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت به لیست", callback_data="refresh_positions_list")]]),
            parse_mode="Markdown",
        )
        return

    trade_type = "🟢 BUY" if pos["type"] == "BUY" else "🔴 SELL"
    profit_emoji = "🟢" if pos["profit"] >= 0 else "🔴"

    caption = (
        f"⚙️ **مدیریت پوزیشن `{pos['symbol']}`** (🎫 `{pos['ticket']}`)\n\n"
        f"🔹 **نوع:** {trade_type} | 📦 **حجم:** `{pos['volume']}` لات\n"
        f"💵 **قیمت ورود:** `{pos['price_open']:.5f}`\n"
        f"📈 **قیمت لحظه‌ای:** `{pos['price_current']:.5f}`\n"
        f"🛑 **SL:** `{pos['sl']}` | 🎯 **TP:** `{pos['tp']}`\n"
        f"───────────────────\n"
        f"📊 **سود/زیان لحظه‌ای:** {profit_emoji} **`${pos['profit']:,.2f}`**"
    )

    # دکمه بروزرسانی دستی حذف شد چون لایو اضافه گردیده است
    keyboard = [
        [
            InlineKeyboardButton("🛡 فری‌ریسک (Break-Even)", callback_data=f"action_be_{ticket}"),
            InlineKeyboardButton("✂️ خروج ۵۰٪", callback_data=f"action_close50_{ticket}"),
        ],
        [
            InlineKeyboardButton("⚙️ تغییر SL / TP", callback_data=f"action_editsltp_{ticket}"),
            InlineKeyboardButton("📉 خروج جزئی دلخواه", callback_data=f"action_partial_{ticket}"),
        ],
        [
            InlineKeyboardButton("❌ بستن کامل پوزیشن", callback_data=f"close_pos_{ticket}"),
        ],
        [
            InlineKeyboardButton("🔙 بازگشت به لیست", callback_data="refresh_positions_list"),
        ],
    ]

    msg = await query.edit_message_text(caption, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    # ۳. راه اندازی آپدیت لایو اختصاصی برای این تک پوزیشن (هر ۳ ثانیه)
    if context.job_queue:
        context.job_queue.run_repeating(
            auto_refresh_single_position_job,
            interval=3,
            first=3,
            chat_id=chat_id,
            data={"message_id": msg.message_id, "ticket": ticket},
            name=f"live_single_pos_{chat_id}",
        )


async def handle_position_actions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data_parts = query.data.split("_")

    # پشتیبانی از فرمت‌های مختلف: action_close_123 یا close_pos_123 یا action_confirmclose_123
    if len(data_parts) == 3 and data_parts[0] == "action":
        action = data_parts[1]
        ticket = int(data_parts[2])
    elif len(data_parts) == 3 and data_parts[0] == "close" and data_parts[1] == "pos":
        action = "close"
        ticket = int(data_parts[2])
    else:
        action = data_parts[1]
        ticket = int(data_parts[2])

    chat_id = update.effective_chat.id

    # ۱. توقف حتمی و آنی تمام تایمرهای آپدیت زنده
    await stop_all_live_jobs(chat_id, context)

    loop = asyncio.get_running_loop()

    # ------------------ ۱-الف. درخواست بستن (نمایش پیام تأییدیه) ------------------
    if action in ["close", "closepos"]:
        confirm_keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ بله، کاملاً مطمئنم", callback_data=f"action_confirmclose_{ticket}"),
                InlineKeyboardButton("❌ انصراف", callback_data=f"pos_detail_{ticket}")
            ]
        ])
        await query.edit_message_text(
            f"⚠️ **هشدار بستن پوزیشن `{ticket}`**\n\n"
            f"آیا از بستن کامل این پوزیشن اطمینان دارید؟",
            reply_markup=confirm_keyboard,
            parse_mode="Markdown"
        )

    # ------------------ ۱-ب. اجرای واقعی بستن پس از تأیید ------------------
    elif action == "confirmclose":
        await query.edit_message_text(
            f"⏳ در حال بستن کامل پوزیشن `{ticket}`...",
            parse_mode="Markdown"
        )

        # فراخوانی تابع بستن کامل پوزیشن در MT5 (به صورت Async/Executor)
        success, msg = await loop.run_in_executor(None, close_position, ticket)

        back_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 بازگشت به لیست پوزیشن‌ها", callback_data="refresh_positions_list")]
        ])

        await query.edit_message_text(
            f"{msg}",
            reply_markup=back_keyboard,
            parse_mode="Markdown"
        )

    # ------------------ ۲. فری‌ریسک (Break-Even) ------------------
    elif action == "be":
        await query.edit_message_text(
            f"⏳ در حال انتقال حد ضرر پوزیشن `{ticket}` به نقطه ورود...",
            parse_mode="Markdown"
        )
        _, msg = await loop.run_in_executor(None, set_break_even, ticket)
        await query.edit_message_text(
            f"{msg}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 بازگشت به لیست", callback_data="refresh_positions_list")]
            ]),
            parse_mode="Markdown"
        )

    # ------------------ ۳. خروج ۵۰٪ حجم ------------------
    elif action == "close50":
        success, positions = await loop.run_in_executor(None, get_open_positions)
        pos = next((p for p in positions if p['ticket'] == ticket), None) if success and positions else None

        if not pos:
            await query.edit_message_text("❌ پوزیشن یافت نشد یا قبلاً بسته شده است.", parse_mode="Markdown")
            return

        half_vol = round(pos['volume'] / 2, 2)
        if half_vol < 0.01:
            await query.edit_message_text("⚠️ **حجم پوزیشن برای خروج ۵۰٪ بسیار کوچک است (کمتر از 0.01).**",
                                          parse_mode="Markdown")
            return

        await query.edit_message_text(f"⏳ در حال بستن `{half_vol}` لات از پوزیشن `{ticket}`...", parse_mode="Markdown")
        _, msg = await loop.run_in_executor(None, close_position, ticket, half_vol)
        await query.edit_message_text(
            f"{msg}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 بازگشت به لیست", callback_data="refresh_positions_list")]
            ]),
            parse_mode="Markdown"
        )

    # ------------------ ۴. ورود به مرحله دریافت SL و TP جدید ------------------
    elif action == "editsltp":
        context.user_data["action_ticket"] = ticket
        context.user_data["action_type"] = "sltp"
        cancel_btn = InlineKeyboardMarkup([[InlineKeyboardButton("❌ انصراف", callback_data=f"pos_detail_{ticket}")]])
        await query.edit_message_text(
            f"✏️ **ویرایش حد ضرر و حد سود پوزیشن `{ticket}`**\n\n"
            f"لطفاً **حد ضرر (SL)** و **حد سود (TP)** جدید را با یک فاصله وارد کنید:\n"
            f"💡 **فرمت:** `<SL> <TP>`\n"
            f"مثال: `2030.50 2060.00` (برای عدم تغییر هرکدام عدد 0 بگذارید)",
            reply_markup=cancel_btn,
            parse_mode="Markdown"
        )
        return INPUT_NEW_SL_TP

    # ------------------ ۵. ورود به مرحله خروج جزئی دلخواه ------------------
    elif action == "partial":
        context.user_data["action_ticket"] = ticket
        cancel_btn = InlineKeyboardMarkup([[InlineKeyboardButton("❌ انصراف", callback_data=f"pos_detail_{ticket}")]])
        await query.edit_message_text(
            f"✂️ **خروج جزئی از پوزیشن `{ticket}`**\n\n"
            f"لطفاً **حجم مورد نظر جهت خروج** را به لات وارد کنید (مثال: `0.05`):",
            reply_markup=cancel_btn,
            parse_mode="Markdown"
        )
        return INPUT_PARTIAL_LOT


async def process_new_sltp_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دریافت مقادیر جدید SL و TP از کاربر"""
    ticket = context.user_data.get("action_ticket")
    text = update.message.text.strip().split()

    if len(text) < 2:
        await update.message.reply_text(
            "❌ **فرمت ورودی نادرست است.** لطفاً دو عدد با فاصله وارد کنید (مثال: `2030 2060`):")
        return INPUT_NEW_SL_TP

    try:
        new_sl = float(text[0])
        new_tp = float(text[1])
    except ValueError:
        await update.message.reply_text("❌ **مقادیر وارد شده باید عدد باشند.** مجدداً وارد کنید:")
        return INPUT_NEW_SL_TP

    loop = asyncio.get_running_loop()
    msg = await update.message.reply_text("⏳ در حال بروزرسانی حد ضرر و حد سود...", parse_mode="Markdown")

    # اعتمادسازی و فراخوانی متاتریدر برای آپدیت SL/TP
    _, res_msg = await loop.run_in_executor(None, update_position_sltp, ticket, new_sl, new_tp)

    await msg.edit_text(
        res_msg,
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔙 بازگشت به لیست پوزیشن‌ها", callback_data="refresh_positions_list")]]),
        parse_mode="Markdown"
    )
    return ConversationHandler.END

# مراحل ConversationHandler برای دریافت عددی SL/TP یا Partial Close
INPUT_NEW_SL_TP, INPUT_PARTIAL_LOT = ("INPUT_NEW_SL_TP","INPUT_PARTIAL_LOT")
async def process_partial_close_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دریافت حجم خروج جزئی دلخواه"""
    ticket = context.user_data.get("action_ticket")

    try:
        vol = float(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ **حجم وارد شده باید یک عدد معتبر باشد.** (مثال: `0.02`):")
        return INPUT_PARTIAL_LOT

    loop = asyncio.get_running_loop()
    msg = await update.message.reply_text(f"⏳ در حال بستن `{vol}` لات از پوزیشن `{ticket}`...", parse_mode="Markdown")

    _, res_msg = await loop.run_in_executor(None, close_position, ticket, vol)

    await msg.edit_text(
        res_msg,
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔙 بازگشت به لیست پوزیشن‌ها", callback_data="refresh_positions_list")]]),
        parse_mode="Markdown"
    )
    return ConversationHandler.END


# تعریف مراحل Conversation New Trade
NEW_TRADE_SYMBOL, NEW_TRADE_ACTION, NEW_TRADE_LOT, NEW_TRADE_SL, NEW_TRADE_TP = (
    "NEW_TRADE_SYMBOL",
    "NEW_TRADE_ACTION",
    "NEW_TRADE_LOT",
    "NEW_TRADE_SL",
    "NEW_TRADE_TP"
)

async def start_trade_wizard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مرحله ۱: دریافت پویای نمادها از Market Watch و ساخت دکمه‌ها"""
    context.user_data.clear()

    # ارسال فیدبک سریع در صورت کلیک روی دکمه شیشه‌ای
    if update.callback_query:
        await update.callback_query.answer()

    # دریافت نمادهای واچ‌لیست از متاتریدر در Executor (غیربلاک‌کننده)
    loop = asyncio.get_running_loop()
    symbols = await loop.run_in_executor(None, get_market_watch_symbols)

    # چیدمان پویا: ایجاد دکمه‌های ۲ تایی در هر سطر
    keyboard = []
    row = []
    for sym in symbols:
        row.append(InlineKeyboardButton(sym, callback_data=f"sym_{sym}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    # افزودن دکمه انصراف در انتهای کیبورد
    keyboard.append([InlineKeyboardButton("❌ انصراف", callback_data="cancel_trade")])

    reply_markup = InlineKeyboardMarkup(keyboard)

    msg_text = (
        "📊 <b>ایجاد معامله جدید (مرحله ۱ از ۵)</b>\n\n"
        "لطفاً نماد مورد نظر را از <b>واچ‌لیست متاتریدر</b> انتخاب کنید یا نام آن را تایپ نمایید:"
    )

    if update.callback_query:
        await update.callback_query.edit_message_text(msg_text, reply_markup=reply_markup, parse_mode="HTML")
    elif update.message:
        await update.message.reply_text(msg_text, reply_markup=reply_markup, parse_mode="HTML")

    return NEW_TRADE_SYMBOL


async def new_trade_get_symbol_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مرحله ۲: دریافت نماد و انتخاب جهت معامله (BUY/SELL)"""

    # ۱. برقراری ایمنی کامل برای CallbackQuery و Message
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        symbol = query.data.replace("sym_", "").strip().upper()
    elif update.message and update.message.text:
        symbol = update.message.text.strip().upper()
    else:
        # اگر ورودی غیرمتنی فرستاده شد
        return NEW_TRADE_SYMBOL

    # ۲. ذخیره نماد انتخاب‌شده
    context.user_data["trade_symbol"] = symbol

    # ۳. دکمه‌های انتخاب جهت معامله (BUY / SELL)
    keyboard = [
        [
            InlineKeyboardButton("🟢 BUY (خرید)", callback_data="act_BUY"),
            InlineKeyboardButton("🔴 SELL (فروش)", callback_data="act_SELL")
        ],
        [InlineKeyboardButton("❌ انصراف", callback_data="cancel_trade")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    text = (
        f"📌 <b>نماد انتخاب شده:</b> <code>{symbol}</code>\n\n"
        "<b>مرحله ۲ از ۵:</b> جهت معامله را انتخاب کنید:"
    )

    # ۴. ارسال یا ادیت پیام متناسب با نوع ورودی
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode="HTML")
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="HTML")

    return NEW_TRADE_ACTION


async def new_trade_get_action_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مرحله ۳: ذخیره جهت و دریافت حجم (Lot)"""
    query = update.callback_query
    await query.answer()

    action = query.data.replace("act_", "")
    context.user_data["trade_action"] = action

    # دکمه‌های میانبر برای حجم‌های رایج
    keyboard = [
        [InlineKeyboardButton("0.01", callback_data="lot_0.01"), InlineKeyboardButton("0.05", callback_data="lot_0.05"),
         InlineKeyboardButton("0.10", callback_data="lot_0.10")],
        [InlineKeyboardButton("0.50", callback_data="lot_0.50"),
         InlineKeyboardButton("1.00", callback_data="lot_1.00")],
        [InlineKeyboardButton("❌ انصراف", callback_data="cancel_trade")]
    ]

    text = (
        f"📌 <b>نماد:</b> <code>{context.user_data['trade_symbol']}</code> | <b>جهت:</b> <code>{action}</code>\n\n"
        "<b>مرحله ۳ از ۵:</b> حجم معامله (Lot) را وارد کنید یا از دکمه‌ها انتخاب کنید:"
    )

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    return NEW_TRADE_LOT


async def new_trade_get_lot_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مرحله ۴: ذخیره حجم و دریافت میزان حد ضرر (SL) به پیپ"""
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        lot_str = query.data.replace("lot_", "")
    else:
        lot_str = update.message.text.strip()

    try:
        lot = float(lot_str)
        if lot <= 0:
            raise ValueError
    except ValueError:
        await update.effective_message.reply_text("❌ <b>حجم وارد شده نامعتبر است. لطفاً یک عدد مثبت وارد کنید:</b>",
                                                  parse_mode="HTML")
        return NEW_TRADE_LOT

    context.user_data["trade_lot"] = lot

    keyboard = [[InlineKeyboardButton("⏭ بدون حد ضرر (0)", callback_data="sl_0")],
                [InlineKeyboardButton("❌ انصراف", callback_data="cancel_trade")]]

    text = (
        f"📌 <b>نماد:</b> <code>{context.user_data['trade_symbol']}</code> | <b>جهت:</b> <code>{context.user_data['trade_action']}</code> | <b>حجم:</b> <code>{lot}</code>\n\n"
        "<b>مرحله ۴ از ۵:</b> حد ضرر (SL) را به <b>پیپ/پوینت</b> وارد کنید (مثلاً <code>300</code>):\n"
        "<i>(در صورت عدم نیاز عدد 0 را ارسال یا دکمه رد کردن را بزنید)</i>"
    )

    if update.callback_query:
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    return NEW_TRADE_SL


async def new_trade_get_sl_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مرحله ۵: ذخیره SL و دریافت حد سود (TP)"""
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        sl_str = query.data.replace("sl_", "")
    else:
        sl_str = update.message.text.strip()

    try:
        sl = int(sl_str)
        if sl < 0:
            raise ValueError
    except ValueError:
        await update.effective_message.reply_text("❌ <b>حد ضرر باید یک عدد صحیح (پیپ/پوینت) باشد:</b>",
                                                  parse_mode="HTML")
        return NEW_TRADE_SL

    context.user_data["trade_sl"] = sl

    keyboard = [[InlineKeyboardButton("⏭ بدون حد سود (0)", callback_data="tp_0")],
                [InlineKeyboardButton("❌ انصراف", callback_data="cancel_trade")]]

    text = (
        f"📌 <b>نماد:</b> <code>{context.user_data['trade_symbol']}</code> | <b>حجم:</b> <code>{context.user_data['trade_lot']}</code>\n"
        f"🛑 <b>SL:</b> <code>{sl}</code> پیپ\n\n"
        "<b>مرحله ۵ از ۵:</b> حد سود (TP) را به <b>پیپ/پوینت</b> وارد کنید (مثلاً <code>600</code>):"
    )

    if update.callback_query:
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    return NEW_TRADE_TP


async def execute_trade_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مرحله نهایی: جمع‌آوری اطلاعات و اجرای معامله"""
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        tp_str = query.data.replace("tp_", "")
    else:
        tp_str = update.message.text.strip()

    try:
        tp = int(tp_str)
        if tp < 0:
            raise ValueError
    except ValueError:
        await update.effective_message.reply_text("❌ <b>حد سود باید یک عدد صحیح (پیپ/پوینت) باشد:</b>",
                                                  parse_mode="HTML")
        return NEW_TRADE_TP

    symbol = context.user_data["trade_symbol"]
    action = context.user_data["trade_action"]
    lot = context.user_data["trade_lot"]
    sl = context.user_data["trade_sl"]

    # ارسال پیام در حال انجام
    if update.callback_query:
        msg = await query.edit_message_text("⏳ <b>در حال ارسال سفارش به متاتریدر...</b>", parse_mode="HTML")
    else:
        msg = await update.message.reply_text("⏳ <b>در حال ارسال سفارش به متاتریدر...</b>", parse_mode="HTML")

    # اجرای غیربلاک‌کننده معامله در Executor
    loop = asyncio.get_running_loop()
    success, result_msg = await loop.run_in_executor(None, execute_trade, symbol, action, lot, sl, tp)

    if success:
        title = "🎯 <b>معامله با موفقیت ثبت شد</b>"
        status_icon = "✅"
    else:
        title = "🚨 <b>خطا در اجرای معامله!</b>"
        status_icon = "❌"

    summary_text = (
        f"{title}\n"
        f"───────────────────\n"
        f"📌 <b>نماد:</b> <code>{symbol}</code>\n"
        f"📊 <b>جهت:</b> <code>{action}</code> | 📦 <b>حجم:</b> <code>{lot}</code>\n"
        f"🛑 <b>SL:</b> <code>{sl}</code> | 🎯 <b>TP:</b> <code>{tp}</code>\n"
        f"───────────────────\n"
        f"{status_icon} <b>نتیجه:</b> {result_msg}"
    )

    await msg.edit_text(summary_text, parse_mode="HTML")
    context.user_data.clear()
    return ConversationHandler.END


async def cancel_trade_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """انصراف از ساخت معامله"""
    query = update.callback_query
    if query:
        await query.answer()
        await query.edit_message_text("❌ <b>فرآیند ساخت معامله لغو شد.</b>", parse_mode="HTML")
    else:
        await update.message.reply_text("❌ <b>فرآیند ساخت معامله لغو شد.</b>", parse_mode="HTML")

    context.user_data.clear()
    return ConversationHandler.END


async def close_position_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    ticket = int(query.data.split("_")[2])
    logger.info("Request to close position ticket #%s from chat_id %s", ticket, update.effective_chat.id)
    await query.edit_message_text(f"⏳ در حال بستن پوزیشن `{ticket}`...", parse_mode="Markdown")

    loop = asyncio.get_running_loop()
    _, message = await loop.run_in_executor(None, close_position_by_ticket, ticket)
    logger.info("Close position #%s result: %s", ticket, message)
    await query.edit_message_text(message, parse_mode="Markdown")


async def close_all_positions_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش پیام تاییدیه قبل از اجرای دستور بستن همه پوزیشن‌ها"""
    chat_id = update.effective_chat.id
    query = update.callback_query
    if query:
        await query.answer()

    # ۱. متوقف کردن تمامی تایمرهای قبلی (لیست کلی و تک پوزیشن)
    await stop_all_live_jobs(chat_id, context)

    # ایجاد کیبورد تاییدیه
    keyboard = [
        [
            InlineKeyboardButton("✅ بله، همه را ببند", callback_data="confirm_close_all"),
            InlineKeyboardButton("❌ انصراف", callback_data="refresh_positions_list"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    text = (
        "⚠️ **هشدار: آیا اطمینان دارید؟**\n\n"
        "با تایید این گزینه، **تمام پوزیشن‌های فعال** شما در متاتریدر ۵ فوراً و با قیمت بازار بسته خواهند شد."
    )

    if query:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")


async def confirm_close_all_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """اجرای واقعی بستن تمامی پوزیشن‌ها پس از تایید کاربر"""
    query = update.callback_query
    await query.answer("در حال بستن همه پوزیشن‌ها...")

    chat_id = update.effective_chat.id

    logger.info("Confirmed close ALL positions triggered by chat_id %s", chat_id)
    await query.edit_message_text("⏳ <b>در حال بستن تمامی پوزیشن‌ها...</b>", parse_mode="HTML")

    loop = asyncio.get_running_loop()
    # ۲. اجرای غیربلاک‌کننده بستن همه پوزیشن‌ها
    res_msg = await loop.run_in_executor(None, close_all_positions)
    logger.info("Close ALL positions result: %s", res_msg)

    # نمایش نتیجه و دکمه بازگشت به لیست
    back_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 بازگشت به لیست پوزیشن‌ها", callback_data="refresh_positions_list")]
    ])
    await query.edit_message_text(f"{res_msg}", reply_markup=back_keyboard, parse_mode="HTML")

# ------------------------------------------------------------------
# 7. Watchlist & Conversation Handlers
# ------------------------------------------------------------------
async def show_watchlist_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    # ۱. پاسخ سریع به تلگرام برای برداشتن لودینگ دکمه
    if query:
        await query.answer()

    loop = asyncio.get_running_loop()
    # ۲. اجرای غیربلاک‌کننده فراخوانی دیتابیس
    watchlist = await loop.run_in_executor(None, get_all_watchlist) if not inspect.iscoroutinefunction(
        get_all_watchlist) else await get_all_watchlist()

    if not watchlist:
        text = "📭 واچ‌لیست شما خالی است!"
        if query:
            await query.edit_message_text(text)
        else:
            await update.message.reply_text(text)
        return

    msg = "📊 **لیست نمادهای تحت نظر:**\n\n"
    buttons = []
    row = []

    for watch in watchlist:
        msg += f"• `{watch.symbol}` ({watch.time_frame}) - {watch.market_type}\n"
        row.append(
            InlineKeyboardButton(
                f"❌ {watch.symbol}({watch.time_frame})",
                callback_data=f"del_watchlist_{watch.symbol}_{watch.time_frame}_{watch.market_type}"
            )
        )
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    msg += "\n*جهت حذف هر نماد روی دکمه مربوط به آن کلیک کنید:*"
    reply_markup = InlineKeyboardMarkup(buttons)

    # ۳. مدیریت یکپارچه پاسخ جهت جلوگیری از خطای update.message
    if query:
        await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=reply_markup)
    else:
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=reply_markup)

async def delete_watchlist_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()

    _, _, symbol, timeframe, market_type = query.data.split('_')

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ بله، مطمئنم", callback_data=f"confirm_del_watchlist_{symbol}_{timeframe}_{market_type}"),
            InlineKeyboardButton("❌ انصراف", callback_data="showWatchlist"),
        ]
    ])

    text = (
        "⚠️ **هشدار: آیا اطمینان دارید؟**\n\n"
        f"با تایید این گزینه نماد `{symbol}` ({timeframe}) از واچ‌لیست حذف خواهد شد!"
    )

    await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")


async def confirm_delete_watchlist_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    # پاسخ لحظه‌ای به دکمه برای از بین بردن تأخیر ظاهری
    await query.answer("در حال پردازش...", show_alert=False)

    _, _, _, symbol, timeframe, market_type = query.data.split("_")
    logger.info("Deleting item from watchlist: %s (%s, %s)", symbol, timeframe, market_type)

    # متوقف ساختن تسک پایش پس‌زمینه
    worker_key = f"{symbol}_{timeframe}_{market_type}"
    if worker_key in ACTIVE_WORKERS:
        ACTIVE_WORKERS[worker_key].cancel()
        del ACTIVE_WORKERS[worker_key]
        logger.info("Cancelled background worker task for key: %s", worker_key)

    # اجرای غیربلاک‌کننده حذف از دیتابیس
    loop = asyncio.get_running_loop()
    if inspect.iscoroutinefunction(delete_from_watchlist):
        res_msg = await delete_from_watchlist(symbol, timeframe, market_type)
    else:
        res_msg = await loop.run_in_executor(None, delete_from_watchlist, symbol, timeframe, market_type)

    back_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت به منو", callback_data="showWatchlist")]])

    # فقط یک بار ویرایش پیام در انتهای کار (کاهش Requestهای API)
    if res_msg:
        await query.edit_message_text(
            f"🗑 نماد `{symbol}` از واچ‌لیست حذف و پایش آن متوقف شد.",
            parse_mode="Markdown",
            reply_markup=back_keyboard
        )
    else:
        await query.edit_message_text(
            f"❌ خطایی در حذف نماد `{symbol}` رخ داد.",
            parse_mode="Markdown",
            reply_markup=back_keyboard
        )


# Conversation steps
async def start_add_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("Starting addWatchlist conversation for chat_id %s", update.effective_chat.id)
    cancel_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("❌ انصراف", callback_data="cancel_watchlist")]])
    await update.message.reply_text(
        "📝 لطفاً نام نماد را وارد کنید (مثلاً `BTCUSDT`):",
        parse_mode="Markdown",
        reply_markup=cancel_keyboard,
    )
    return ADD_WATCHLIST_SYMBOL


async def add_watchlist_get_symbol_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    symbol = update.message.text.upper()
    context.user_data['symbol'] = symbol
    logger.info("AddWatchlist step 1 - Symbol entered: %s", symbol)

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("5m", callback_data="5m"), InlineKeyboardButton("15m", callback_data="15m")],
        [InlineKeyboardButton("30m", callback_data="30m"), InlineKeyboardButton("1h", callback_data="1h")],
        [InlineKeyboardButton("4h", callback_data="4h"), InlineKeyboardButton("1d", callback_data="1d")],
        [InlineKeyboardButton("❌ انصراف", callback_data="cancel_watchlist")],
    ])
    await update.message.reply_text(f"📌 نماد: `{symbol}`\n⏱ تایم‌فریم را انتخاب کنید:",
                                    parse_mode="Markdown", reply_markup=keyboard)
    return ADD_WATCHLIST_TIMEFRAME


async def add_watchlist_get_timeframe_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    timeframe = query.data
    context.user_data['timeframe'] = timeframe
    logger.info("AddWatchlist step 2 - Timeframe selected: %s", timeframe)

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 کریپتو", callback_data="CRYPTO"),
         InlineKeyboardButton("📈 فارکس", callback_data="FOREX")],
        [InlineKeyboardButton("❌ انصراف", callback_data="cancel_watchlist")],
    ])
    await query.edit_message_text("🏷 بازار را انتخاب کنید:", reply_markup=keyboard)
    return ADD_WATCHLIST_MARKET


async def add_watchlist_get_market_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    await query.edit_message_text("⏳ **در حال ثبت در واچ لیست...**", parse_mode="Markdown")

    symbol = context.user_data['symbol']
    timeframe = context.user_data['timeframe']
    market_type = query.data
    logger.info("AddWatchlist step 3 - Market selected: %s for symbol %s", market_type, symbol)

    await save_watchlist_item(symbol, timeframe, market_type)

    # شروع تسک جدید
    worker_key = f"{symbol}_{timeframe}_{market_type}"
    task = asyncio.create_task(worker_loop(symbol, timeframe, market_type, update.effective_chat.id, context.bot))
    ACTIVE_WORKERS[worker_key] = task
    logger.info("Created new worker task for key: %s", worker_key)

    await query.edit_message_text(f"✨ ` {timeframe} | {symbol}` به واچ‌لیست اضافه شد و پایش RSI فعال گردید. ", parse_mode="Markdown")
    return ConversationHandler.END

async def cancel_watch_list_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("AddWatchlist conversation cancelled by user %s", update.effective_chat.id)
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await query.edit_message_text("❌ **عملیات واچ لیست لغو شد.**",parse_mode="Markdown")
    return ConversationHandler.END


# ------------------------------------------------------------------
# 8. Background Worker Loop
# ------------------------------------------------------------------
async def worker_loop(symbol: str, timeframe: str, market_type: str, chat_id: int, bot):
    interval = TIMEFRAME_TO_SECONDS.get(timeframe, 1800)
    logger.info("🚀 [STARTED] RSI Monitor task: %s | %s | %s", symbol, timeframe, market_type)

    try:
        while True:
            try:
                rsi, status, divergence = await calculate_rsi(symbol, timeframe, market_type)
                logger.debug("RSI checked for %s (%s): RSI=%s, Status=%s", symbol, timeframe, rsi, status)

                if rsi is not None and ("NORMAL" not in status or divergence != "بدون واگرایی"):
                    logger.info("Signal detected for %s (%s)! RSI: %s | Status: %s | Divergence: %s",
                                symbol, timeframe, rsi, status, divergence)
                    msg = (
                        f"🚨 **هشدار سیگنال RSI**\n\n"
                        f"📌 **نماد:** `{symbol}` | ⏳ `{timeframe}`\n"
                        f"📊 **RSI:** `{rsi:.2f}` | ⚡️ **وضعیت:** `{status}`\n"
                        f"🔍 **واگرایی:** {divergence}"
                    )
                    await bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown")
            except Exception as e:
                logger.exception("Error during RSI calculation worker for %s: %s", symbol, e)

            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        logger.info("🛑 [STOPPED] RSI Monitor task cancelled for %s (%s)", symbol, timeframe)


# ------------------------------------------------------------------
# 9. Application Startup & Main Execution
# ------------------------------------------------------------------
async def on_startup(app):
    logger.info("Starting bot initialization and background workers...")
    watchlist = await get_all_watchlist()

    for item in watchlist:
        worker_key = f"{item.symbol}_{item.time_frame}_{item.market_type}"
        task = asyncio.create_task(
            worker_loop(item.symbol, item.time_frame, item.market_type, ADMIN_CHAT_ID, app.bot)
        )
        ACTIVE_WORKERS[worker_key] = task

    logger.info("Successfully started %d background worker tasks.", len(watchlist))


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    # اگر خطای شبکه بود، فقط لاگ هشدار بده و ربات را زنده نگه دار
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
        .build()
    )

    app.add_error_handler(error_handler)

    # ------------------ 2️⃣ ثبت دستورات اولیه (Commands) ------------------
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("showWatchlist", show_watchlist_command))

    # ------------------ 3️⃣ گفتگوها (Conversation Handlers) ------------------

    # واچ‌لیست
    add_watchlist_handler = ConversationHandler(
        entry_points=[
            CommandHandler("addWatchlist", start_add_watchlist),
            MessageHandler(filters.Regex("^➕ افزودن به واچ‌لیست$"), start_add_watchlist),
        ],
        states={
            ADD_WATCHLIST_SYMBOL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_watchlist_get_symbol_step),
                CallbackQueryHandler(cancel_watch_list_callback, pattern="^cancel_watchlist$")
            ],
            ADD_WATCHLIST_TIMEFRAME: [
                CallbackQueryHandler(add_watchlist_get_timeframe_step,pattern="^(5m|15m|30m|1h|4h|1d)$"),
                CallbackQueryHandler(cancel_watch_list_callback, pattern="^cancel_watchlist$")
            ],
            ADD_WATCHLIST_MARKET: [
                CallbackQueryHandler(add_watchlist_get_market_step),
                CallbackQueryHandler(cancel_watch_list_callback, pattern="^cancel_watchlist$")
            ],
        },
        fallbacks=[CallbackQueryHandler(cancel_watch_list_callback,pattern="^cancel_watchlist$")],
    )
    app.add_handler(add_watchlist_handler)

    # هشدار قیمت
    alert_handler = ConversationHandler(
        entry_points=[
            CommandHandler("setalert", start_alert_wizard),
            MessageHandler(filters.Regex("^ثبت هشدار قیمت 🔔$"), start_alert_wizard),
        ],
        states={
            ADD_ALERT_MARKET: [CallbackQueryHandler(add_alert_market_selected, pattern="^market_|cancel_alert$")],
            ADD_ALERT_SYMBOL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_alert_symbol_received),
                CallbackQueryHandler(cancel_alert_callback, pattern="^cancel_alert$"),
            ],
            ADD_ALERT_PRICE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_alert_price_received),
                CallbackQueryHandler(cancel_alert_callback, pattern="^cancel_alert$"),
            ],
        },
        fallbacks=[CallbackQueryHandler(cancel_alert_callback, pattern="^cancel_alert$")],
    )
    app.add_handler(alert_handler)

    # مدیریت پوزیشن‌ها (تغییر SL/TP و خروج جزئی)
    pos_management_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(handle_position_actions, pattern="^action_(editsltp|partial)_")
        ],
        states={
            INPUT_NEW_SL_TP: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_new_sltp_input)],
            INPUT_PARTIAL_LOT: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_partial_close_input)],
        },
        fallbacks=[
            CallbackQueryHandler(show_positions_handler, pattern="^refresh_positions_list$"),
            CallbackQueryHandler(position_detail_callback, pattern="^pos_detail_")
        ],
        per_chat=True,
        per_user=True
    )
    app.add_handler(pos_management_conv)

    # اضافه کردن ترید جدید
    trade_wizard_handler = ConversationHandler(
        entry_points=[
            CommandHandler("newtrade", start_trade_wizard),
            CommandHandler("trade", start_trade_wizard),
            CallbackQueryHandler(start_trade_wizard, pattern="^start_new_trade$"),
            MessageHandler(filters.Regex(r"^\s*📈 معامله جدید$"), start_trade_wizard),
        ],
        states={
            NEW_TRADE_SYMBOL: [
                CallbackQueryHandler(new_trade_get_symbol_step, pattern="^sym_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, new_trade_get_symbol_step),
            ],
            NEW_TRADE_ACTION: [
                CallbackQueryHandler(new_trade_get_action_step, pattern="^act_"),
            ],
            NEW_TRADE_LOT: [
                CallbackQueryHandler(new_trade_get_lot_step, pattern="^lot_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, new_trade_get_lot_step),
            ],
            NEW_TRADE_SL: [
                CallbackQueryHandler(new_trade_get_sl_step, pattern="^sl_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, new_trade_get_sl_step),
            ],
            NEW_TRADE_TP: [
                CallbackQueryHandler(execute_trade_step, pattern="^tp_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, execute_trade_step),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(cancel_trade_handler, pattern="^cancel_trade$"),
            CommandHandler("cancel", cancel_trade_handler),
        ],
    )
    app.add_handler(trade_wizard_handler)

    # ------------------ 4️⃣ کلیدهای میانبر کیبورد (Keyboard Handlers) ------------------
    app.add_handler(MessageHandler(filters.Regex("^📋 واچ‌لیست$"), show_watchlist_command))
    app.add_handler(MessageHandler(filters.Regex("^📊 پوزیشن‌های باز$"), show_positions_handler))

    # ------------------ 5️⃣ دکمه‌های شیشه‌ای (Callback Query Handlers) ------------------
    # حذف از واچ لیست
    app.add_handler(CallbackQueryHandler(show_watchlist_command, pattern="^showWatchlist$"))
    app.add_handler(CallbackQueryHandler(delete_watchlist_handler, pattern="^del_watchlist_"))
    app.add_handler(CallbackQueryHandler(confirm_delete_watchlist_handler, pattern="^confirm_del_watchlist_"))


    # مدیریت پوزیشن‌ها و آپدیت لایو
    app.add_handler(CommandHandler("positions", show_positions_handler))
    app.add_handler(CallbackQueryHandler(show_positions_handler, pattern="^refresh_positions_list$"))
    app.add_handler(CallbackQueryHandler(position_detail_callback, pattern="^pos_detail_"))

    # اکشن‌های مستقیم پوزیشن‌ها
    app.add_handler(CallbackQueryHandler(handle_position_actions, pattern="^action_(be|close50)_"))
    app.add_handler(CallbackQueryHandler(handle_position_actions, pattern="^(action_|close_pos_)"))
    app.add_handler(CallbackQueryHandler(close_all_positions_handler, pattern="^close_all_positions$"))
    app.add_handler(CallbackQueryHandler(confirm_close_all_handler, pattern="^confirm_close_all$"))

    # ------------------ 6️⃣ اجرا و مانیتورینگ ------------------
    logger.info("🤖 Bot is polling for updates...")
    try:
        app.run_polling(drop_pending_updates=True)
    except KeyboardInterrupt:
        logger.info("🛑 Telegram bot stopped manually.")