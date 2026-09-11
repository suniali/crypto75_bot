import asyncio
import logging
import os
import sys
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

ADD_SYMBOL, ADD_TIMEFRAME, ADD_MARKET = range(3)
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
        [["ثبت هشدار قیمت 🔔"],["📋 واچ‌لیست", "➕ افزودن به واچ‌لیست"], ["📊 پوزیشن‌های باز"]],
        resize_keyboard=True
    )
    await update.message.reply_text(welcome_text, parse_mode="Markdown", reply_markup=main_keyboard)

# ------------------------------------------------------------------
# 5.َAlert Handlers
# ------------------------------------------------------------------
# تعریف مراحل گفتگو
SELECT_MARKET, INPUT_SYMBOL, INPUT_PRICE = range(3)

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
    return SELECT_MARKET

async def market_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    return INPUT_SYMBOL


async def symbol_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
            return INPUT_SYMBOL

        await status_msg.delete()

    context.user_data["symbol"] = symbol

    await update.message.reply_text(
        f"✅ نماد: `{symbol}`\n\n🎯 حالا **قیمت هدف** مد نظر خود را به عدد وارد کنید:",
        reply_markup=cancel_keyboard,
        parse_mode="Markdown"
    )
    return INPUT_PRICE


async def price_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        return INPUT_PRICE

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
        except Exception as e:
            logger.error(f"Unexpected error when clearing message: {e}")

        job.schedule_removal()  # توقف تایمر
        return

    # ساخت متن جدید با فرمت HTML (ایمن‌تر از Markdown)
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
    keyboard.append([InlineKeyboardButton("⏹ توقف آپدیت زنده", callback_data="stop_live_update")])

    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML",
        )
    except BadRequest as e:
        # اگر پیام تغییری نکرده باشد خطای Message is not modified می‌دهد که مشکلی نیست
        if "message is not modified" in str(e).lower():
            pass
        elif "message to edit not found" in str(e).lower():
            # اگر کاربر پیام را پاک کرده باشد، تایمر متوقف می‌شود
            job.schedule_removal()
        else:
            logger.error(f"BadRequest on edit_message_text: {e}")
    except Exception as e:
        logger.error(f"Error updating positions job: {e}")

async def auto_refresh_single_position_job(context: ContextTypes.DEFAULT_TYPE):
    """آپدیت خودکار جزییات یک پوزیشن خاص هر چند ثانیه یک‌بار"""
    job = context.job
    chat_id = job.chat_id
    message_id = job.data.get("message_id")
    ticket = job.data.get("ticket")

    loop = asyncio.get_running_loop()
    success, positions = await loop.run_in_executor(None, get_open_positions)

    pos = next((p for p in positions if p["ticket"] == ticket), None) if success and positions else None

    # اگر پوزیشن بسته شده باشد، تایمر را متوقف کن
    if not pos:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=f"❌ **پوزیشن `{ticket}` بسته شده است یا یافت نشد.**",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت به لیست", callback_data="refresh_positions_list")]]),
                parse_mode="Markdown",
            )
        except Exception:
            pass
        job.schedule_removal()
        return

    trade_type = "🟢 BUY" if pos["type"] == "BUY" else "🔴 SELL"
    profit_emoji = "🟢" if pos["profit"] >= 0 else "🔴"

    caption = (
        f"⚙️ **مدیریت پوزیشن `{pos['symbol']}`** (🎫 `{pos['ticket']}`)\n\n"
        f"🔹 **نوع:** {trade_type} | 📦 **حجم:** `{pos['volume']}` لات\n"
        f"💵 **قیمت ورود:** `{pos['price_open']}`\n"
        f"📈 **قیمت لحظه‌ای:** `{pos['price_current']:.5f}`\n"
        f"🛑 **SL:** `{pos['sl']}` | 🎯 **TP:** `{pos['tp']}`\n"
        f"───────────────────\n"
        f"📊 **سود/زیان لحظه‌ای:** {profit_emoji} **`${pos['profit']:,.2f}`**"
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
            parse_mode="Markdown",
        )
    except Exception:
        pass

# مراحل ConversationHandler برای دریافت عددی SL/TP یا Partial Close
INPUT_NEW_SL_TP, INPUT_PARTIAL_LOT = range(2)


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
            f"📊 **حجم:** `{pos['volume']}` | **ورود:** `{pos['price_open']}`\n"
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
    keyboard.append([InlineKeyboardButton("⏹ توقف آپدیت زنده", callback_data="stop_live_update")])

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


async def stop_live_update_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """توقف خودکار آپدیت زنده با کلیک کاربر روی دکمه توقف"""
    query = update.callback_query
    await query.answer("آپدیت زنده متوقف شد.")

    chat_id = update.effective_chat.id

    # پیدا کردن و حذف تایمر مربوط به این چت
    current_jobs = context.job_queue.get_jobs_by_name(f"live_pos_{chat_id}")
    for job in current_jobs:
        job.schedule_removal()

    # جایگزینی دکمه «توقف» با دکمه «بروزرسانی مجدد»
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔄 بروزرسانی و شروع مجدد لایو", callback_data="refresh_positions_list")],
            [InlineKeyboardButton("💥 بستن همه پوزیشن‌ها", callback_data="close_all_positions")],
        ]
    )

    current_text = query.message.text
    # حذف خط اولِ مربوط به "بروزرسانی زنده" و جایگزینی با متن ایستاتیک
    new_text = current_text.replace("🔄 لیست پوزیشن‌های فعال (بروزرسانی زنده):",
                                    "📋 **لیست پوزیشن‌های فعال (توقف‌یافته):**")

    try:
        await query.edit_message_text(text=new_text, reply_markup=keyboard, parse_mode="Markdown")
    except Exception:
        pass


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

    # ۱. متوقف کردن تمامی تایمرهای قبلی (لیست کلی و تک پوزیشن)
    if context.job_queue:
        for job_name in [f"live_pos_{chat_id}", f"live_single_pos_{chat_id}"]:
            for job in context.job_queue.get_jobs_by_name(job_name):
                job.schedule_removal()
                logger.info("Stopped job %s for chat %s", job_name, chat_id)

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
        f"💵 **قیمت ورود:** `{pos['price_open']}`\n"
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

    # پشتیبانی همزمان از اکشن‌های action_close_123 و close_pos_123
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

    # ۱. متوقف کردن تمامی تایمرهای قبلی (لیست کلی و تک پوزیشن)
    if context.job_queue:
        for job_name in [f"live_pos_{chat_id}", f"live_single_pos_{chat_id}"]:
            for job in context.job_queue.get_jobs_by_name(job_name):
                job.schedule_removal()
                logger.info("Stopped job %s for chat %s", job_name, chat_id)

    loop = asyncio.get_running_loop()

    # ------------------ ۱. بستن کامل پوزیشن ------------------
    if action in ["close", "closepos"]:
        await query.edit_message_text(
            f"⏳ در حال بستن کامل پوزیشن `{ticket}`...",
            parse_mode="Markdown"
        )

        # فراخوانی تابع بستن کامل پوزیشن در MT5
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
        # دریافت اطلاعات پوزیشن جهت محاسبه نصف حجم
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

async def trade_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if len(context.args) < 5:
        logger.warning("Invalid trade args from chat_id %s: %s", chat_id, context.args)
        await update.message.reply_text(
            "⚠️ **فرمت دستور ناقص است!**\n\n"
            "📌 **فرمت:** `/trade <BUY/SELL> <نماد> <حجم> <SL> <TP>`\n"
            "💡 **مثال:** `/trade BUY EURUSD 0.1 300 600`",
            parse_mode="Markdown"
        )
        return

    try:
        action, symbol = context.args[0].upper(), context.args[1].upper()
        lot, sl, tp = float(context.args[2]), int(context.args[3]), int(context.args[4])
        if action not in ["BUY", "SELL"]:
            raise ValueError
    except ValueError:
        logger.warning("Invalid numerical or action input for trade command from chat_id %s", chat_id)
        await update.message.reply_text("❌ **ورودی‌های عددی یا نوع معامله (BUY/SELL) نامعتبر است.**",
                                        parse_mode="Markdown")
        return

    logger.info("Executing trade request: %s %s (Lot: %s, SL: %s, TP: %s) by chat_id %s", action, symbol, lot, sl, tp, chat_id)
    msg = await update.message.reply_text("⏳ **در حال ارسال سفارش...**", parse_mode="Markdown")
    success, result_msg = execute_trade(symbol, action, lot, sl, tp)

    if success:
        logger.info("Trade executed successfully for %s: %s", symbol, result_msg)
        title = "🎯 **معامله با موفقیت ثبت شد**"
    else:
        logger.error("Failed to execute trade for %s: %s", symbol, result_msg)
        title = "🚨 **خطا در اجرای معامله!**"

    await msg.edit_text(f"{title}\n\n📌 **نماد:** `{symbol}`\n💬 `{result_msg}`", parse_mode="Markdown")


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
    if context.job_queue:
        for job_name in [f"live_pos_{chat_id}", f"live_single_pos_{chat_id}"]:
            for job in context.job_queue.get_jobs_by_name(job_name):
                job.schedule_removal()
                logger.info("Stopped job %s for chat %s", job_name, chat_id)

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
    await query.answer()

    logger.info("Confirmed close ALL positions triggered by chat_id %s", update.effective_chat.id)
    await query.edit_message_text("⏳ **در حال بستن تمامی پوزیشن‌ها...**", parse_mode="Markdown")

    loop = asyncio.get_running_loop()
    res_msg = await loop.run_in_executor(None, close_all_positions)
    logger.info("Close ALL positions result: %s", res_msg)

    # نمایش نتیجه و دکمه بازگشت به لیست
    back_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت به منو", callback_data="refresh_positions_list")]])
    await query.edit_message_text(f"{res_msg}", reply_markup=back_keyboard, parse_mode="Markdown")


# ------------------------------------------------------------------
# 7. Watchlist & Conversation Handlers
# ------------------------------------------------------------------
async def show_watchlist_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("Fetching watchlist for chat_id %s", update.effective_chat.id)
    watchlist = await get_all_watchlist()
    if not watchlist:
        await update.message.reply_text("📭 واچ‌لیست شما خالی است!")
        return

    for watch in watchlist:
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ حذف", callback_data=f"del_{watch.symbol}_{watch.time_frame}_{watch.market_type}")
        ]])
        msg = f"📌 **نماد:** `{watch.symbol}` | ⏳ `{watch.time_frame}` | 🏷 `{watch.market_type}`"
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=keyboard)


async def handle_delete_watchlist_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    _, symbol, timeframe, market_type = query.data.split("_")
    logger.info("Deleting item from watchlist: %s (%s, %s)", symbol, timeframe, market_type)

    # متوقف ساختن تسک پایش مربوطه
    worker_key = f"{symbol}_{timeframe}_{market_type}"
    if worker_key in ACTIVE_WORKERS:
        ACTIVE_WORKERS[worker_key].cancel()
        del ACTIVE_WORKERS[worker_key]
        logger.info("Cancelled background worker task for key: %s", worker_key)

    await delete_from_watchlist(symbol, timeframe, market_type)
    await query.edit_message_text(f"🗑 نماد `{symbol}` از واچ‌لیست حذف و پایش آن متوقف شد.", parse_mode="Markdown")


# Conversation steps
async def start_add_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("Starting addWatchlist conversation for chat_id %s", update.effective_chat.id)
    cancel_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("❌ انصراف", callback_data="cancel_watchlist")]])
    await update.message.reply_text(
        "📝 لطفاً نام نماد را وارد کنید (مثلاً `BTCUSDT`):",
        parse_mode="Markdown",
        reply_markup=cancel_keyboard,
    )
    return ADD_SYMBOL


async def get_symbol_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    return ADD_TIMEFRAME


async def get_timeframe_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    return ADD_MARKET


async def get_market_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

    # 1️⃣ اضافه شدن job_queue() جهت فعال‌سازی آپدیت زنده
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
    app.add_handler(CommandHandler("trade", trade_command))
    app.add_handler(CommandHandler("showWatchlist", show_watchlist_command))

    # ------------------ 3️⃣ گفتگوها (Conversation Handlers) ------------------

    # واچ‌لیست
    add_watchlist_handler = ConversationHandler(
        entry_points=[
            CommandHandler("addWatchlist", start_add_watchlist),
            MessageHandler(filters.Regex("^➕ افزودن به واچ‌لیست$"), start_add_watchlist),
        ],
        states={
            ADD_SYMBOL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_symbol_step),
                CallbackQueryHandler(cancel_watch_list_callback, pattern="^cancel_watchlist$")
            ],
            ADD_TIMEFRAME: [
                CallbackQueryHandler(get_timeframe_step,pattern="^(5m|15m|30m|1h|4h|1d)$"),
                CallbackQueryHandler(cancel_watch_list_callback, pattern="^cancel_watchlist$")
            ],
            ADD_MARKET: [
                CallbackQueryHandler(get_market_step),
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
            SELECT_MARKET: [CallbackQueryHandler(market_selected, pattern="^market_|cancel_alert$")],
            INPUT_SYMBOL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, symbol_received),
                CallbackQueryHandler(cancel_alert_callback, pattern="^cancel_alert$"),
            ],
            INPUT_PRICE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, price_received),
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

    # ------------------ 4️⃣ کلیدهای میانبر کیبورد (Keyboard Handlers) ------------------
    app.add_handler(MessageHandler(filters.Regex("^📋 واچ‌لیست$"), show_watchlist_command))
    app.add_handler(MessageHandler(filters.Regex("^📊 پوزیشن‌های باز$"), show_positions_handler))

    # ------------------ 5️⃣ دکمه‌های شیشه‌ای (Callback Query Handlers) ------------------
    # حذف از واچ لیست
    app.add_handler(CallbackQueryHandler(handle_delete_watchlist_callback, pattern="^del_"))

    # مدیریت پوزیشن‌ها و آپدیت لایو
    app.add_handler(CommandHandler("positions", show_positions_handler))
    app.add_handler(CallbackQueryHandler(show_positions_handler, pattern="^refresh_positions_list$"))
    app.add_handler(CallbackQueryHandler(stop_live_update_handler, pattern="^stop_live_update$"))
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