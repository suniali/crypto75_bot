import asyncio
import logging
import os
import sys
import inspect
import httpx
from pathlib import Path

from PIL import Image
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
import warnings
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
from telegram.warnings import PTBUserWarning
warnings.filterwarnings("ignore", category=PTBUserWarning)

from bot_app.analysis_service import (
    calculate_rsi,
    get_ai_market_view,
    extract_trade_from_image,
    analyze_trades_with_gemini,
)
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
    get_trades_history,
    check_recent_closed_positions,
)
from bot_app.report_service import generate_pdf_report
from bot_app.checker import fetch_active_alerts,process_alert

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

MAIN_MENU_TEXT = "\u200f🏠 **به منوی اصلی بازگشتید.**\n\n💡 _از دکمه‌های زیر جهت دسترسی سریع استفاده کنید:_"
MAIN_KEYBOARD = ReplyKeyboardMarkup(
        [
            ["ثبت هشدار قیمت 🔔"],
            ["📋 واچ‌لیست", "✨ افزودن به واچ‌لیست"],
            ["📊 پوزیشن‌های باز","📈 معامله جدید"],
            ["✍️ ثبت دستی معامله", "📸 استخراج معامله از عکس"],
            ["📊 دریافت گزارش (PDF)"]
        ],
        resize_keyboard=True
    )


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

    # کاراکتر \u200f جهت مرتب‌سازی درست متون فارسی و انگلیسی در تلگرام
    welcome_text = (
        f"\u200fسلام **{user_name}** عزیز! 👋✨\n"
        f"به **دستیار هوشمند و پایشگر تخصصی ترید** خوش آمدید.\n\n"
        "📌 **ویژگی‌ها و قابلیت‌های اصلی ربات:**\n\n"
        "🔔 **هشدارهای قیمت (Price Alerts)**\n"
        "└ ⚡ تنظیم و پایش لحظه‌ای قیمت‌های فارکس و کریپتو\n\n"
        "📋 **مدیریت واچ‌لیست (Watchlist)**\n"
        "└ 👁️ پایش نمادهای منتخب و زیر نظر داشتن نوسانات بازار\n\n"
        "🤖 **سیگنال RSI & تحلیل AI**\n"
        "├ 📊 سنجش خودکار RSI و تشخیص واگرایی‌ها\n"
        "└ 🧠 تحلیل الگوهای کندلی و تایم پایین با هوش مصنوعی (Gemini)\n\n"
        "🎯 **پایش خودکار SL & TP**\n"
        "└ ⏱️ شناسایی لحظه‌ای خروج از معاملات (حد سود/ضرر) و ارسال هشدار سریع\n\n"
        "📝 **استخراج عکس & ثبت دستی**\n"
        "├ 📸 **استخراج از تصویر:** خواندن خودکار مشخصات معامله از روی عکس چارت\n"
        "└ ✍️ **ثبت دستی معامله:** قالب‌بندی و ثبت اطلاعات جهت ژورنال‌نویسی\n\n"
        "📊 **گزارش‌گیری تخصصی (PDF)**\n"
        "├ 📈 ترسیم آنلاین منحنی رشد حساب (Equity Curve) و Max Drawdown\n"
        "├ 📐 محاسبه شاخص‌های وال‌استریت (Sharpe Ratio ،Profit Factor و Expectancy)\n"
        "└ 🧠 ارزیابی جامع عملکرد و رفتارشناسی ترید توسط Gemini\n\n"
        "💡 *برای شروع، از دکمه‌های منوی زیر استفاده کنید:* 👇"
    )

    await update.message.reply_text(
        welcome_text,
        parse_mode="Markdown",
        reply_markup=MAIN_KEYBOARD
    )


async def on_shutdown(app):
    logger.info("🛑 Stopping background workers...")

    # لغو تمامی تسک‌های فعال
    for worker_key, task in ACTIVE_WORKERS.items():
        if not task.done():
            task.cancel()
            logger.info("Cancelling worker: %s", worker_key)

    # منتظر ماندن برای بسته شدن کامل تسک‌ها
    await asyncio.gather(*ACTIVE_WORKERS.values(), return_exceptions=True)
    ACTIVE_WORKERS.clear()

    logger.info("✅ All background workers stopped cleanly.")

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
    """پردازش انتخاب بازار و ساخت کیبورد شیشه‌ای واچ‌لیست برای فارکس"""
    query = update.callback_query
    await query.answer()

    if query.data == "cancel_alert":
        await query.edit_message_text("❌ **ثبت هشدار لغو شد.**", parse_mode="Markdown")
        return ConversationHandler.END

    is_forex = (query.data == "market_forex")
    context.user_data["is_forex"] = is_forex

    market_name = "فارکس" if is_forex else "ارز دیجیتال"
    example = "XAUUSD-ECN" if is_forex else "BTCUSDT"

    symbol_buttons = []

    # اگر فارکس باشد، نمادهای مارکت‌واچ متاتریدر را می‌گیریم
    if is_forex:
        loop = asyncio.get_running_loop()
        symbols = await loop.run_in_executor(None, get_market_watch_symbols)

        # ساخت دکمه‌های ۲ ستونه برای واچ‌لیست
        row = []
        for sym in symbols:
            row.append(InlineKeyboardButton(sym, callback_data=f"select_sym:{sym}"))
            if len(row) == 2:
                symbol_buttons.append(row)
                row = []
        if row:
            symbol_buttons.append(row)

    # افزودن دکمه انصراف در انتها
    symbol_buttons.append([InlineKeyboardButton("❌ انصراف", callback_data="cancel_alert")])
    reply_markup = InlineKeyboardMarkup(symbol_buttons)

    text = (
        f"🌐 بازار انتخاب‌شده: **{market_name}**\n\n"
        f"👇 می‌توانید **نماد** را از لیست زیر انتخاب کرده یا آن را **تایپ کنید** (مثال: `{example}`):"
    )

    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    return ADD_ALERT_SYMBOL


async def add_alert_symbol_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دریافت نماد انتخاب‌شده (تایپ متنی یا دکمه شیشه‌ای) و اعتبارسنجی آن"""
    cancel_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("❌ انصراف", callback_data="cancel_alert")]])
    is_forex = context.user_data.get("is_forex", False)

    # ۱. استخراج نماد بر اساس نوع ورودی (کلیک روی دکمه یا تایپ متنی)
    if update.callback_query:
        query = update.callback_query
        await query.answer()

        if query.data == "cancel_alert":
            await query.edit_message_text("❌ **ثبت هشدار لغو شد.**", parse_mode="Markdown")
            return ConversationHandler.END

        if query.data.startswith("select_sym:"):
            symbol = query.data.split(":")[1]
        else:
            return ADD_ALERT_SYMBOL

    elif update.message and update.message.text:
        symbol = update.message.text.strip().upper()
    else:
        return ADD_ALERT_SYMBOL

    # ۲. اعتبارسنجی نماد در صورت انتخاب فارکس
    if is_forex:
        if update.callback_query:
            status_msg = await update.callback_query.edit_message_text("⏳ **در حال بررسی نماد در متاتریدر...**", parse_mode="Markdown")
        else:
            status_msg = await update.message.reply_text("⏳ **در حال بررسی نماد در متاتریدر...**", parse_mode="Markdown")

        loop = asyncio.get_running_loop()
        res = await loop.run_in_executor(None, check_symbol_info, symbol)

        if res is not True:
            if isinstance(res, list):
                logger.warning("Forex symbol %s not found. Suggestions: %s", symbol, res[:5])

                # تبدیل پیشنهادها به دکمه‌های کلیک‌پذیر
                suggestion_buttons = [
                    [InlineKeyboardButton(s, callback_data=f"select_sym:{s}")] for s in res[:6]
                ]
                suggestion_buttons.append([InlineKeyboardButton("❌ انصراف", callback_data="cancel_alert")])
                sug_keyboard = InlineKeyboardMarkup(suggestion_buttons)

                await status_msg.edit_text(
                    f"⚠️ **نماد `{symbol}` یافت نشد!**\n\n💡 پیشنهادها را انتخاب کنید یا نماد جدیدی بنویسید:",
                    reply_markup=sug_keyboard,
                    parse_mode="Markdown"
                )
            else:
                logger.error("Error connecting to MetaTrader 5 while checking symbol %s", symbol)
                await status_msg.edit_text("🚨 **خطا در اتصال به MetaTrader 5!** ثبت هشدار لغو شد.", parse_mode="Markdown")
                return ConversationHandler.END

            return ADD_ALERT_SYMBOL

        await status_msg.delete()

    # ۳. ذخیره نماد و رفتن به مرحله بعد
    context.user_data["symbol"] = symbol
    next_msg = f"✅ نماد انتخاب‌شده: `{symbol}`\n\n🎯 حالا **قیمت هدف** مد نظر خود را به عدد وارد کنید:"

    if update.callback_query:
        await update.callback_query.message.reply_text(next_msg, reply_markup=cancel_keyboard, parse_mode="Markdown")
    else:
        await update.message.reply_text(next_msg, reply_markup=cancel_keyboard, parse_mode="Markdown")

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
    await query.edit_message_text(
        "❌ **ثبت هشدار لغو شد.**",
        reply_markup=MAIN_KEYBOARD,
        parse_mode="Markdown",
    )
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
            InlineKeyboardButton("✂️ خروج 25%", callback_data=f"action_close25_{ticket}"),
            InlineKeyboardButton("✂️ خروج 50%", callback_data=f"action_close50_{ticket}"),
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

    # ------------------ ۳. خروج 25% حجم ------------------
    elif action == "close25":
        success, positions = await loop.run_in_executor(None, get_open_positions)
        pos = next((p for p in positions if p['ticket'] == ticket), None) if success and positions else None

        if not pos:
            await query.edit_message_text("❌ پوزیشن یافت نشد یا قبلاً بسته شده است.", parse_mode="Markdown")
            return

        sm_vol = round(pos['volume'] / 3, 2)
        if sm_vol < 0.01:
            await query.edit_message_text(
                "⚠️ **حجم پوزیشن برای خروج 25٪ بسیار کوچک است (کمتر از 0.01).**",
                parse_mode="Markdown"
            )
            return

        await query.edit_message_text(f"⏳ در حال بستن `{sm_vol}` لات از پوزیشن `{ticket}`...", parse_mode="Markdown")
        _, msg = await loop.run_in_executor(None, close_position, ticket, sm_vol)
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
NEW_TRADE_SYMBOL, NEW_TRADE_ACTION, NEW_TRADE_LOT,NEW_TRADE_PRICE, NEW_TRADE_SL, NEW_TRADE_TP = (
    "NEW_TRADE_SYMBOL",
    "NEW_TRADE_ACTION",
    "NEW_TRADE_LOT",
    "NEW_TRADE_PRICE",
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
    """مرحله ۴: ذخیره حجم و درخواست قیمت ورود (یا معامله مارکت)"""
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
        msg = "❌ <b>حجم وارد شده نامعتبر است. لطفاً یک عدد مثبت وارد کنید:</b>"
        if update.callback_query:
            await update.callback_query.message.reply_text(msg, parse_mode="HTML")
        else:
            await update.message.reply_text(msg, parse_mode="HTML")
        return NEW_TRADE_LOT

    context.user_data["trade_lot"] = lot

    # دکمه ورود با قیمت لحظه‌ای (Market Price)
    keyboard = [
        [InlineKeyboardButton("⚡ ورود با قیمت لحظه‌ای (Market)", callback_data="price_market")],
        [InlineKeyboardButton("❌ انصراف", callback_data="cancel_trade")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    text = (
        f"📌 <b>نماد:</b> <code>{context.user_data['trade_symbol']}</code> | "
        f"<b>جهت:</b> <code>{context.user_data['trade_action']}</code> | "
        f"<b>حجم:</b> <code>{lot}</code>\n\n"
        "<b>مرحله ۴ از ۶:</b> لطفاً <b>قیمت ورود مد نظر (Pending Order)</b> را تایپ کنید "
        "یا دکمه <b>ورود با قیمت لحظه‌ای</b> را بزنید:"
    )

    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode="HTML")
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="HTML")

    return NEW_TRADE_PRICE

async def new_trade_get_price_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مرحله ۵: ذخیره قیمت ورود و درخواست قیمت حد ضرر (SL)"""
    if update.callback_query:
        query = update.callback_query
        await query.answer()

        if query.data == "price_market":
            entry_price = "MARKET"
        else:
            return NEW_TRADE_PRICE
    else:
        price_str = update.message.text.strip()
        try:
            entry_price = float(price_str)
            if entry_price <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text(
                "❌ <b>قیمت وارد شده نامعتبر است. لطفاً یک عدد معتبر وارد کنید:</b>",
                parse_mode="HTML"
            )
            return NEW_TRADE_PRICE

    context.user_data["trade_price"] = entry_price
    price_display = "قیمت لحظه‌ای (Market)" if entry_price == "MARKET" else entry_price

    keyboard = [
        [InlineKeyboardButton("⏭ بدون حد ضرر (0)", callback_data="sl_0")],
        [InlineKeyboardButton("❌ انصراف", callback_data="cancel_trade")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    text = (
        f"📌 <b>نماد:</b> <code>{context.user_data['trade_symbol']}</code> | "
        f"<b>جهت:</b> <code>{context.user_data['trade_action']}</code> | "
        f"<b>حجم:</b> <code>{context.user_data['trade_lot']}</code> | "
        f"<b>ورود:</b> <code>{price_display}</code>\n\n"
        "<b>مرحله ۵ از ۶:</b> <b>قیمت دقیق حد ضرر (SL)</b> را وارد کنید (مثلاً <code>1.08500</code> یا <code>2650.50</code>):\n"
        "<i>(در صورت عدم نیاز عدد 0 را ارسال یا دکمه رد کردن را بزنید)</i>"
    )

    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode="HTML")
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="HTML")

    return NEW_TRADE_SL


async def new_trade_get_sl_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مرحله ۶: ذخیره قیمت SL و دریافت قیمت حد سود (TP)"""
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        sl_str = query.data.replace("sl_", "")
    else:
        sl_str = update.message.text.strip()

    try:
        sl = float(sl_str)
        if sl < 0:
            raise ValueError
    except ValueError:
        await update.effective_message.reply_text(
            "❌ <b>قیمت حد ضرر نامعتبر است. لطفاً یک عدد معتبر وارد کنید:</b>",
            parse_mode="HTML"
        )
        return NEW_TRADE_SL

    context.user_data["trade_sl"] = sl
    sl_display = sl if sl > 0 else "بدون SL"

    keyboard = [
        [InlineKeyboardButton("⏭ بدون حد سود (0)", callback_data="tp_0")],
        [InlineKeyboardButton("❌ انصراف", callback_data="cancel_trade")]
    ]

    text = (
        f"📌 <b>نماد:</b> <code>{context.user_data['trade_symbol']}</code> | "
        f"<b>حجم:</b> <code>{context.user_data['trade_lot']}</code>\n"
        f"🛑 <b>قیمت SL:</b> <code>{sl_display}</code>\n\n"
        "<b>مرحله ۶ از ۶:</b> <b>قیمت دقیق حد سود (TP)</b> را وارد کنید (مثلاً <code>1.09500</code> یا <code>2700.00</code>):"
    )

    if update.callback_query:
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    return NEW_TRADE_TP


async def execute_trade_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مرحله نهایی: جمع‌آوری تمامی اطلاعات و اجرای معامله"""
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        tp_str = query.data.replace("tp_", "")
    else:
        tp_str = update.message.text.strip()

    try:
        tp = float(tp_str)
        if tp < 0:
            raise ValueError
    except ValueError:
        await update.effective_message.reply_text(
            "❌ <b>قیمت حد سود نامعتبر است. لطفاً یک عدد معتبر وارد کنید:</b>",
            parse_mode="HTML"
        )
        return NEW_TRADE_TP

    symbol = context.user_data["trade_symbol"]
    action = context.user_data["trade_action"]
    lot = context.user_data["trade_lot"]
    entry_price = context.user_data["trade_price"]
    sl = context.user_data["trade_sl"]

    if update.callback_query:
        msg = await query.edit_message_text("⏳ <b>در حال ارسال سفارش به متاتریدر...</b>", parse_mode="HTML")
    else:
        msg = await update.message.reply_text("⏳ <b>در حال ارسال سفارش به متاتریدر...</b>", parse_mode="HTML")

    loop = asyncio.get_running_loop()
    success, result_msg, rr_ratio = await loop.run_in_executor(
        None, execute_trade, symbol, action, lot, entry_price, sl, tp
    )

    if success:
        title = "🎯 <b>معامله با موفقیت ثبت شد</b>"
        status_icon = "✅"
    else:
        title = "🚨 <b>خطا در اجرای معامله!</b>"
        status_icon = "❌"

    price_disp = "قیمت لحظه‌ای (Market)" if entry_price == "MARKET" else entry_price
    sl_disp = sl if sl > 0 else "تعیین نشده"
    tp_disp = tp if tp > 0 else "تعیین نشده"
    rr_disp = f"1:{rr_ratio:.2f}" if rr_ratio else "نامشخص"

    summary_text = (
        f"{title}\n"
        f"───────────────────\n"
        f"📌 <b>نماد:</b> <code>{symbol}</code>\n"
        f"📊 <b>جهت:</b> <code>{action}</code> | 📦 <b>حجم:</b> <code>{lot}</code>\n"
        f"💵 <b>ورود:</b> <code>{price_disp}</code>\n"
        f"🛑 <b>SL:</b> <code>{sl_disp}</code> | 🎯 <b>TP:</b> <code>{tp_disp}</code>\n"
        f"⚖️ <b>نسبت R/R:</b> <code>{rr_disp}</code>\n"
        f"───────────────────\n"
        f"{status_icon} <b>نتیجه:</b> {result_msg}"
    )

    await msg.edit_text(summary_text, parse_mode="HTML")
    context.user_data.clear()
    return ConversationHandler.END


async def cancel_trade_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """انصراف از ساخت معامله و بازگشت به منوی اصلی"""
    context.user_data.clear()

    query = update.callback_query
    if query:
        await query.answer()
        # ۱. ویرایش پیام شیشه‌ای و حذف دکمه‌های آن
        await query.edit_message_text("❌ <b>فرآیند ساخت معامله لغو شد.</b>", parse_mode="HTML")
        # ۲. ارسال پیام جدید برای فعال کردن مجدد کیبورد اصلی
        await update.effective_chat.send_message(
        MAIN_MENU_TEXT,
            reply_markup=MAIN_KEYBOARD
        )
    else:
        # اگر انصراف متنی یا کامند /stop بود
        await update.message.reply_text(
            "❌ <b>فرآیند ساخت معامله لغو شد.</b>",
            reply_markup=MAIN_KEYBOARD,
            parse_mode="HTML"
        )

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
    await query.edit_message_text(message,reply_markup=MAIN_KEYBOARD, parse_mode="Markdown")


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

    context.user_data.clear()

    query = update.callback_query
    if query:
        await query.answer()
        # ۱. ویرایش پیام شیشه‌ای (حذف دکمه‌های شیشه‌ای قبلی)
        await query.edit_message_text("❌ **عملیات افزودن به واچ‌لیست لغو شد.**", parse_mode="Markdown")

        # ۲. ارسال پیام جدید برای بازگرداندن کیبورد اصلی
        await update.effective_chat.send_message(
            MAIN_MENU_TEXT,
            parse_mode="Markdown",
            reply_markup=MAIN_KEYBOARD
        )
    else:
        # پشتیبانی از حالتی که لغو از طریق دستور متنی انجام شود
        await update.message.reply_text(
          MAIN_MENU_TEXT,
            parse_mode="Markdown",
            reply_markup=MAIN_KEYBOARD
        )

    return ConversationHandler.END

# ------------------------------------------------------------------
# 8. Extract Trade From Image
# ------------------------------------------------------------------

WAITING_FOR_TRADE_IMAGE, CONFIRM_JOURNAL_DATA, EDITING_JOURNAL_DATA = range(100, 103)

async def start_extract_trade_wizard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """گام ۱: درخواست تصویر چارت همراه با دکمه شیشه‌ای انصراف"""
    cancel_inline_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ انصراف", callback_data="cancel_trade_extraction")]
    ])

    await update.message.reply_text(
        "📸 **لطفاً تصویر چارت یا پوزیشن معاملاتی خود را ارسال کنید:**\n\n"
        "اطلاعات معامله استخراج شده و پس از تأیید شما جهت ژورنال‌نویسی نمایش داده می‌شود.",
        parse_mode="Markdown",
        reply_markup=cancel_inline_keyboard
    )
    return WAITING_FOR_TRADE_IMAGE


async def process_trade_image_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """گام ۲: دریافت تصویر، استخراج داده‌ها با هندلینگ کامل خطا"""


    await update.message.chat.send_action(action="typing")
    status_msg = await update.message.reply_text("⏳ در حال دریافت و پردازش تصویر چارت...")

    temp_image_path = f"temp_trade_{update.effective_user.id}.jpg"

    try:
        # ۱. دانلود عکس
        photo = update.message.photo[-1]
        file = await photo.get_file()
        await file.download_to_drive(temp_image_path)

        # ۲. فشرده‌سازی تصویر
        try:
            with Image.open(temp_image_path) as img:
                img.thumbnail((1024, 1024))
                img.save(temp_image_path, "JPEG", quality=85)
        except Exception as img_err:
            logger.warning("Image optimization warning: %s", img_err)

        await status_msg.edit_text("🤖 هوش مصنوعی در حال خواندن قیمت‌ها و نماد است...")

        # ۳. فراخوانی هوش مصنوعی
        loop = asyncio.get_running_loop()
        extracted_text = await loop.run_in_executor(None, extract_trade_from_image, temp_image_path)

        # ۴. بررسی پیام‌های خطای خروجی از تابع استخراج
        if not extracted_text or extracted_text.startswith("⚠️") or "خطا" in extracted_text:
            raise ValueError(extracted_text or "پاسخ نامعتبر از هوش مصنوعی")

        # اگر تا اینجا خطایی نبود، پاکسازی فایل موقت و ادامه فرآیند
        if os.path.exists(temp_image_path):
            os.remove(temp_image_path)

        context.user_data['extracted_journal_data'] = extracted_text

        # ساخت دکمه‌های شیشه‌ای تأیید فقط در صورت موفقیت کامل
        confirm_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ تأیید و نمایش نهایی", callback_data="confirm_journal_yes")],
            [InlineKeyboardButton("✏️ ویرایش دستی", callback_data="edit_journal_manual")],
            [InlineKeyboardButton("❌ لغو", callback_data="confirm_journal_no")]
        ])

        msg = (
            f"🔍 **اطلاعات استخراج‌شده از تصویر:**\n\n"
            f"```text\n{extracted_text}\n```\n"
            f"آیا اطلاعات بالا مورد تأیید است؟"
        )

        await status_msg.delete()
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=confirm_keyboard)
        return CONFIRM_JOURNAL_DATA

    except Exception as e:
        logger.error("Error during trade extraction: %s", e)

        # پاکسازی فایل موقت در صورت وجود
        if os.path.exists(temp_image_path):
            os.remove(temp_image_path)

        context.user_data.pop('extracted_journal_data', None)

        # اعلام خطا به کاربر و بازگشت مستقیم به منوی اصلی بدون نشان دادن دکمه‌های شیشه‌ای
        error_text = str(e) if str(e).startswith(
            "⚠️") else "⚠️ خطا در پردازش و استخراج اطلاعات تصویر. لطفاً مجدداً تلاش کنید."

        await status_msg.delete()
        await update.message.reply_text(
            f"{error_text}\n\nعملیات لغو شد.",
            reply_markup=MAIN_KEYBOARD
        )

        # خاتمه دادن به گفتگو و لغو حالت Conversation
        return ConversationHandler.END


async def start_manual_edit_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """گام ۳-ب: درخواست ارسال متن اصلاح‌شده از کاربر"""
    query = update.callback_query
    await query.answer()

    current_data = context.user_data.get('extracted_journal_data', '')

    await query.edit_message_text(
        "✏️ **حالت ویرایش دستی:**\n\n"
        "لطفاً متن زیر را کپی کرده، تغییرات لازم (قیمت، نماد و...) را روی آن اعمال کنید و سپس **یک پیام جدید** حاوی متن اصلاح‌شده بفرستید:"
    )

    cancel_inline_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ انصراف", callback_data="confirm_journal_no")]
    ])

    # ارسال متن قبلی در یک پیام جداگانه برای کپی آسان‌تر توسط کاربر
    await query.message.reply_text(
        f"```text\n{current_data}\n```",
        parse_mode="Markdown",
        reply_markup=cancel_inline_keyboard
    )
    return EDITING_JOURNAL_DATA


async def save_manual_edit_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """گام ۳-ج: دریافت متن اصلاح‌شده کاربر و نمایش دکمه‌های تأیید مجدد"""
    edited_text = update.message.text.strip()
    context.user_data['extracted_journal_data'] = edited_text

    confirm_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ تأیید و نمایش نهایی", callback_data="confirm_journal_yes")],
        [InlineKeyboardButton("✏️ ویرایش مجدد", callback_data="edit_journal_manual")],
        [InlineKeyboardButton("❌ لغو", callback_data="confirm_journal_no")]
    ])

    msg = (
        f"✍️ **اطلاعات ویرایش‌شده توسط شما:**\n\n"
        f"```text\n{edited_text}\n```\n"
        f"آیا اطلاعات جدید مورد تأیید است؟"
    )

    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=confirm_keyboard)
    return CONFIRM_JOURNAL_DATA


async def confirm_journal_data_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """گام نهایی: نمایش متن نهایی تأییدشده جهت ژورنال‌نویسی"""
    query = update.callback_query
    await query.answer()

    data = context.user_data.get('extracted_journal_data', 'اطلاعاتی یافت نشد.')

    await query.edit_message_text("✅ *داده‌ها تأیید شدند.*", parse_mode="Markdown")

    final_msg = (
        f"📝 **دیتا آماده جهت ژورنال‌نویسی:**\n\n"
        f"```text\n{data}\n```\n\n"
        f"📌 *می‌توانید متن بالا را کپی کرده و در ژورنال شخصی خود استفاده کنید.*"
    )

    await query.message.reply_text(final_msg, parse_mode="Markdown", reply_markup=MAIN_KEYBOARD)
    context.user_data.pop('extracted_journal_data', None)
    return ConversationHandler.END


async def cancel_extract_image_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """لغو فرآیند"""
    query = update.callback_query
    await query.answer()

    await query.edit_message_text("❌ عملیات استخراج لغو شد.")
    await query.message.reply_text(MAIN_MENU_TEXT,reply_markup=MAIN_KEYBOARD)
    context.user_data.pop('extracted_journal_data', None)
    return ConversationHandler.END

# ------------------------------------------------------------------
# 10. Manuel Extract Trade
# ------------------------------------------------------------------
(
    WAITING_FOR_TRADE_IMAGE,
    CONFIRM_JOURNAL_DATA,
    EDITING_JOURNAL_DATA,
    WAITING_FOR_MANUAL_TRADE_INPUT
) = range(100, 104)

async def start_manual_trade_wizard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """گام ۱: درخواست ورود اطلاعات معامله به‌صورت متنی"""
    cancel_inline_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ انصراف", callback_data="confirm_journal_no")]
    ])

    await update.message.reply_text(
        "✍️ **لطفاً اطلاعات معامله خود را وارد کنید:**\n\n"
        "می‌توانید اطلاعات را با فرمت دلخواه (مثلاً نماد، حد سود، حد ضرر و...) ارسال کنید:\n\n"
        "مثال:\n"
        "```text\n"
        "SYMBOL: BTCUSDT\n"
        "TYPE: BUY\n"
        "ENTRY: 65000\n"
        "SL: 64000\n"
        "TP: 68000\n"
        "```",
        parse_mode="Markdown",
        reply_markup=cancel_inline_keyboard
    )
    return WAITING_FOR_MANUAL_TRADE_INPUT


async def process_manual_trade_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """گام ۲: دریافت متن ورودی و نمایش دکمه‌های تأیید/ویرایش/لغو"""
    user_text = update.message.text.strip()

    if not user_text:
        await update.message.reply_text("⚠️ متن ارسالی خالی است. لطفاً اطلاعات معامله را ارسال کنید.")
        return WAITING_FOR_MANUAL_TRADE_INPUT

    context.user_data['extracted_journal_data'] = user_text

    confirm_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ تأیید و نمایش نهایی", callback_data="confirm_journal_yes")],
        [InlineKeyboardButton("✏️ ویرایش دستی", callback_data="edit_journal_manual")],
        [InlineKeyboardButton("❌ لغو", callback_data="confirm_journal_no")]
    ])

    msg = (
        f"🔍 **اطلاعات ثبت‌شده:**\n\n"
        f"```text\n{user_text}\n```\n"
        f"آیا اطلاعات بالا مورد تأیید است؟"
    )

    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=confirm_keyboard)
    return CONFIRM_JOURNAL_DATA

# ------------------------------------------------------------------
# 12. Reporting
# ------------------------------------------------------------------
SELECT_REPORT_PERIOD = range(104, 105)


async def start_report_wizard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """شروع فرآیند دریافت گزارش"""
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📅 روزانه (۲۴ ساعت)", callback_data="rep_1"),
         InlineKeyboardButton("📆 هفتگی (۷ روز)", callback_data="rep_7")],
        [InlineKeyboardButton("🗓 ماهانه (۳۰ روز)", callback_data="rep_30"),
         InlineKeyboardButton("📊 ۶ ماهه (۱۸۰ روز)", callback_data="rep_180")],
        [InlineKeyboardButton("❌ انصراف", callback_data="cancel_report")]
    ])

    msg_text = "📊 **انتخاب بازه زمانی گزارش عملکرد:**\n\nلطفاً بازه زمانی مورد نظر خود را برای دریافت فایل PDF و تحلیل هوش مصنوعی انتخاب کنید:"

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(msg_text, parse_mode="Markdown", reply_markup=keyboard)
    else:
        await update.message.reply_text(msg_text, parse_mode="Markdown", reply_markup=keyboard)

    return SELECT_REPORT_PERIOD


async def process_report_generation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """پردازش، فراخوانی Gemini و ارسال PDF"""
    query = update.callback_query
    await query.answer()

    days_map = {
        "rep_1": (1, "روزانه"),
        "rep_7": (7, "هفتگی"),
        "rep_30": (30, "ماهانه"),
        "rep_180": (180, "شش ماهه")
    }

    days, period_name = days_map.get(query.data, (7, "هفتگی"))

    await query.edit_message_text(
        f"⏳ **در حال استخراج معاملات و تحلیل هوش مصنوعی برای بازه {period_name}...**\nلطفاً چند لحظه شکیبا باشید.")

    loop = asyncio.get_running_loop()

    # ۱. استخراج دیتای MT5
    trades, error = await loop.run_in_executor(None, get_trades_history, days)

    if error or not trades:
        await query.edit_message_text(f"⚠️ {error or 'هیچ معامله‌ای یافت نشد.'}")
        await update.effective_chat.send_message("🏠 **به منوی اصلی بازگشتید.**", reply_markup=MAIN_KEYBOARD,
                                                 parse_mode="Markdown")
        return ConversationHandler.END

    # ۲. تحلیل Gemini
    ai_analysis = await loop.run_in_executor(None, analyze_trades_with_gemini, trades, period_name)

    # ۳. تولید فایل PDF
    pdf_filename = f"Trade_Report_{update.effective_user.id}_{days}d.pdf"
    await loop.run_in_executor(None, generate_pdf_report, pdf_filename, period_name, trades, ai_analysis)

    # ۴. ارسال فایل برای کاربر
    await query.edit_message_text("✅ **گزارش با موفقیت آماده شد. در حال ارسال فایل...**")

    with open(pdf_filename, 'rb') as doc_file:
        await update.effective_chat.send_document(
            document=doc_file,
            filename=f"Report_{period_name}.pdf",
            caption=f"📄 **گزارش عملکرد معامله‌گری ({period_name})**\n🤖 همراه با تحلیل هوشمند Gemini",
            parse_mode="Markdown"
        )

    # پاکسازی فایل موقت
    if os.path.exists(pdf_filename):
        os.remove(pdf_filename)

    await update.effective_chat.send_message(
        "\u200f🏠 **به منوی اصلی بازگشتید.**\n\n👇 _لطفاً یکی از گزینه‌های زیر را انتخاب کنید:_",
        reply_markup=MAIN_KEYBOARD,
        parse_mode="Markdown"
    )

    return ConversationHandler.END


async def cancel_report_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """لغو گزارش‌گیری"""
    query = update.callback_query
    if query:
        await query.answer()
        await query.edit_message_text("❌ **عملیات گزارش‌گیری لغو شد.**", parse_mode="Markdown")

    await update.effective_chat.send_message(
        "\u200f🏠 **به منوی اصلی بازگشتید.**\n\n👇 _لطفاً یکی از گزینه‌های زیر را انتخاب کنید:_",
        reply_markup=MAIN_KEYBOARD,
        parse_mode="Markdown"
    )
    return ConversationHandler.END
# ------------------------------------------------------------------
# 13. Background Worker Loop
# ------------------------------------------------------------------
async def check_alerts_loop(bot) -> None:
    logger.info("🚀 Price Monitoring Worker started...")

    async with httpx.AsyncClient() as client:
        while True:
            try:
                alerts = await fetch_active_alerts()
                if alerts:
                    # اجرا و دریافت خروجی تمام تسک‌ها هم‌زمان
                    tasks = [process_alert(client, alert) for alert in alerts]
                    results = await asyncio.gather(*tasks, return_exceptions=True)

                    # پیمایش روی نتایج و ارسال پیام در صورت وجود خروجی
                    for alert, result in zip(alerts, results):
                        if isinstance(result, str):  # یعنی متن پیام برگشته است
                            try:
                                await bot.send_message(
                                    chat_id=alert.chat_id,
                                    text=result,
                                    parse_mode="Markdown"
                                )
                                logger.info("Telegram notification sent to %s", alert.chat_id)
                            except Exception as e:
                                logger.exception("Failed to send msg to %s: %s", alert.chat_id, e)

            except Exception as e:
                logger.exception("Unexpected error in main alert loop: %s", e)

            await asyncio.sleep(2)

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


async def sl_tp_monitor_loop(chat_id: int, bot):
    logger.info("🚀 [STARTED] Global SL/TP Monitor Task")
    loop = asyncio.get_running_loop()

    # ۱. در لحظه روشن شدن ربات، معاملات ۱۲ ساعت گذشته را می خوانیم
    # و به عنوان قدیمی علامت می‌زنیم تا فقط معاملات "جدید" اطلاع‌رسانی شوند.
    initial_deals = await loop.run_in_executor(None, check_recent_closed_positions, 12)
    notified_deals = {deal["deal_id"] for deal in initial_deals}
    logger.info(f"🔰 SL/TP Monitor ready. Ignored {len(notified_deals)} past deals.")

    try:
        while True:
            try:
                # چک کردن معاملات با بازه مطمئن
                closed_deals = await loop.run_in_executor(None, check_recent_closed_positions, 12)

                for deal in closed_deals:
                    deal_id = deal["deal_id"]

                    # اگر معامله جدیدی رخ داده باشد که در notified_deals نیست:
                    if deal_id not in notified_deals:
                        profit = deal["profit"]
                        profit_icon = "🟢" if profit >= 0 else "🔴"

                        msg = (
                            f"🔔 <b>هشدار بسته‌شدن پوزیشن!</b>\n\n"
                            f"🎫 <b>تیکت پوزیشن:</b> <code>{deal['position_id']}</code>\n"
                            f"📌 <b>نماد:</b> <b>{deal['symbol']}</b>\n"
                            f"📌 <b>علت خروج:</b> {deal['exit_type']}\n"
                            f"📊 <b>حجم:</b> <code>{deal['volume']}</code> لات\n"
                            f"🏁 <b>قیمت خروج:</b> <code>{deal['exit_price']}</code>\n"
                            f"{profit_icon} <b>سود/زیان نهایی:</b> <code>${profit:,.2f}</code>"
                        )

                        await bot.send_message(chat_id=chat_id, text=msg, parse_mode="HTML")
                        logger.info(f"✅ Alert sent for NEW Deal {deal_id}")

                        # ثبت آی‌دی جدید
                        notified_deals.add(deal_id)

            except Exception as e:
                logger.error("Error in SL/TP monitor loop: %s", e)

            # چک کردن هر ۵ ثانیه
            await asyncio.sleep(5)

    except asyncio.CancelledError:
        logger.info("🛑 [STOPPED] Global SL/TP Monitor Task")

# ------------------------------------------------------------------
# 14. Application Startup & Main Execution
# ------------------------------------------------------------------
async def on_startup(app):
    logger.info("Starting bot initialization and background workers...")

    # ۱. استارت ورکر عمومی پایش استاپ‌لوس و تیک‌پرافیت
    sl_tp_task = asyncio.create_task(
        sl_tp_monitor_loop(ADMIN_CHAT_ID, app.bot)
    )
    ACTIVE_WORKERS["global_sl_tp_monitor"] = sl_tp_task

    # ۲. استارت ورکر آلرت قیمت (Price Alerts)
    price_alert_task = asyncio.create_task(
        check_alerts_loop(app.bot)
    )
    ACTIVE_WORKERS["price_alert_monitor"] = price_alert_task

    # ۳. استارت ورکرهای RSI برای نمادهای واچ‌لیست
    watchlist = await get_all_watchlist()

    for item in watchlist:
        worker_key = f"{item.symbol}_{item.time_frame}_{item.market_type}"
        task = asyncio.create_task(
            worker_loop(item.symbol, item.time_frame, item.market_type, ADMIN_CHAT_ID, app.bot)
        )
        ACTIVE_WORKERS[worker_key] = task

    logger.info("Successfully started SL/TP monitor and %d RSI worker tasks.", len(watchlist))


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
        .post_shutdown(on_shutdown)
        .build()
    )

    app.add_error_handler(error_handler)

    # ------------------ 2️⃣ ثبت دستورات اولیه (Commands) ------------------
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("showWatchlist", show_watchlist_command))

    # ------------------ 3️⃣ گفتگوها (Conversation Handlers) ------------------

    # واچ‌لیست
    add_watchlist_handler = ConversationHandler(
        entry_points=[
            CommandHandler("addWatchlist", start_add_watchlist),
            MessageHandler(filters.Text(["✨ افزودن به واچ‌لیست"]), start_add_watchlist),
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
        fallbacks=[
            CallbackQueryHandler(cancel_watch_list_callback,pattern="^cancel_watchlist$"),
        ],
        per_message=False,  # اضافه شد جهت حذف هشدار
        per_chat=True,  # اضافه شد جهت مدیریت بر اساس چت
        per_user=True,  # اضافه شد جهت مدیریت بر اساس کاربر
    )
    app.add_handler(add_watchlist_handler)

    # هشدار قیمت
    alert_handler = ConversationHandler(
        entry_points=[
            CommandHandler("setalert", start_alert_wizard),
            MessageHandler(filters.Regex("^ثبت هشدار قیمت 🔔$"), start_alert_wizard),
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
        ],
        per_message=False,  # اضافه شد جهت حذف هشدار
        per_chat=True,  # اضافه شد جهت مدیریت بر اساس چت
        per_user=True,  # اضافه شد جهت مدیریت بر اساس کاربر
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
        per_message=False,  # اضافه شد جهت حذف هشدار
        per_chat=True,  # اضافه شد جهت مدیریت بر اساس چت
        per_user=True,  # اضافه شد جهت مدیریت بر اساس کاربر
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
            NEW_TRADE_PRICE: [  # 👈 اضافه شدن هندلرهای مرحله قیمت
                CallbackQueryHandler(new_trade_get_price_step, pattern="^(price_market|cancel_trade)"),
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
        ],
        per_message=False,  # اضافه شد جهت حذف هشدار
        per_chat=True,  # اضافه شد جهت مدیریت بر اساس چت
        per_user=True,  # اضافه شد جهت مدیریت بر اساس کاربر
    )
    app.add_handler(trade_wizard_handler)

    # استخراج اطلاعات ترید از عکس
    extract_image_handler = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex(r"^\s*📸 استخراج معامله از عکس$"), start_extract_trade_wizard),
            MessageHandler(filters.Regex(r"^\s*✍️ ثبت دستی معامله$"), start_manual_trade_wizard)
        ],
        states={
            WAITING_FOR_TRADE_IMAGE: [
                MessageHandler(filters.PHOTO, process_trade_image_handler),
                # پشتیبانی از دکمه شیشه‌ای انصراف در مرحله ارسال عکس
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
        ],
        per_chat=True,
        per_user=True,
        per_message=False
    )
    app.add_handler(extract_image_handler)

    #
    # ریپورت گیری
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
        ],
        per_message=False,
        per_chat=True,
        per_user=True
    )

    app.add_handler(report_handler)

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
    app.add_handler(CallbackQueryHandler(handle_position_actions, pattern="^action_(be|close25|close50)_"))
    app.add_handler(CallbackQueryHandler(handle_position_actions, pattern="^(action_|close_pos_)"))
    app.add_handler(CallbackQueryHandler(close_all_positions_handler, pattern="^close_all_positions$"))
    app.add_handler(CallbackQueryHandler(confirm_close_all_handler, pattern="^confirm_close_all$"))

    # ------------------ 6️⃣ اجرا و مانیتورینگ ------------------
    logger.info("🤖 Bot is polling for updates...")
    try:
        app.run_polling(drop_pending_updates=True)
    except KeyboardInterrupt:
        logger.info("🛑 Telegram bot stopped manually.")