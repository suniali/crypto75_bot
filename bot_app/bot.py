import asyncio
import logging
import os
import sys
import httpx
from pathlib import Path
from datetime import datetime

from PIL import Image
import django
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

from bot_app.services.analysis_service import (
    calculate_rsi,get_ai_market_view,
    extract_trade_from_image,analyze_trades_with_gemini
)

from bot_app.services.mt5_service import (
    start_mt5_connection,
    stop_mt5_connection,
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
from bot_app.services.report_service import generate_pdf_report
from bot_app.services.alert_service import (
    fetch_active_alerts,
    deactivate_alert_by_id,
    get_or_create_user,
    create_user_alert
)
from bot_app.services.watchlist_service import (
    get_all_watchlist,
    save_watchlist_item,
    delete_from_watchlist
)
from bot_app.price_checker import process_alert
from bot_app.services.chart_service import create_pending_alert_chart,generate_pro_daily_dashboard,calculate_today_stats
from bot_app.services.api_service import fetch_recent_klines

from bot_app.models import MarketType

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

ACTIVE_WORKERS = {}  # برای مدیریت و متوقف کردن تسک‌های پس‌زمینه هنگام حذف

MAIN_MENU_TEXT = "\u200f🏠 **به منوی اصلی بازگشتید.**\n\n💡 _از دکمه‌های زیر جهت دسترسی سریع استفاده کنید:_"
MAIN_KEYBOARD = ReplyKeyboardMarkup(
        [
            ["🔔 هشدارهای فعال","ثبت هشدار قیمت 🔔"],
            ["📋 واچ‌لیست", "✨ افزودن به واچ‌لیست"],
            ["📊 پوزیشن‌های باز","📈 معامله جدید"],
            ["✍️ ثبت دستی معامله", "📸 استخراج معامله از عکس"],
            ["📊 دریافت گزارش (PDF)"]
        ],
        resize_keyboard=True
    )


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

# ------------------------------------------------------------------
# #. Show And Manage Alerts
# ------------------------------------------------------------------
# تابع اصلی برای نمایش لیست هشدارهای فعال کاربر
async def show_active_alerts_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = str(update.effective_chat.id)

    if query:
        try:
            await query.answer()
        except Exception as e:
            logger.warning("Error answering callback query: %s", e)

    try:

        alerts = await fetch_active_alerts()

        # ۲. اگر حسابی هشداری نداشت
        if not alerts:
            text = "🔕 **شما هیچ هشدار فعال قیمت ندارید!**"
            if query:
                await query.edit_message_text(text, parse_mode="Markdown")
            else:
                await update.message.reply_text(text, parse_mode="Markdown")
            return

        # ۳. ساخت متن و دکمه‌های حذف
        msg = "🔔 **لیست هشدارهای قیمت فعال شما:**\n\n"
        buttons = []

        for idx, alert in enumerate(alerts, 1):
            market_label = "فارکس" if alert.is_forex else "کریپتو"
            # تمیزسازی نماد جهت جلوگیری از خطای مارک‌داون
            sym = str(alert.symbol).replace("_", "\\_")

            msg += f"{idx}. `{sym}` ({market_label}) 🎯 قیمت: `{alert.target_price:.5f}`\n"

            # دکمه اختصاصی حذف بر اساس ID هشدار در دیتابیس
            buttons.append([
                InlineKeyboardButton(
                    f"🗑 حذف: {alert.symbol} روی {alert.target_price:.5f}",
                    callback_data=f"confirm_del_alert_{alert.id}"
                )
            ])

        msg += "\n*جهت لغو هر هشدار، روی دکمه مربوط به آن کلیک کنید:*"
        reply_markup = InlineKeyboardMarkup(buttons)

        if query:
            try:
                await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=reply_markup)
            except BadRequest as e:
                if "Message is not modified" not in str(e):
                    await query.message.reply_text(msg, parse_mode="Markdown", reply_markup=reply_markup)
        else:
            await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=reply_markup)

    except Exception as e:
        logger.exception("Error in show_active_alerts_command: %s", e)
        error_msg = "🚨 **خطا در دریافت لیست هشدارها!**"
        if query:
            await query.edit_message_text(error_msg, parse_mode="Markdown")
        elif update.message:
            await update.message.reply_text(error_msg, parse_mode="Markdown")

async def confirm_delete_alert_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مرحله ۱: درخواست تأیید از کاربر قبل از غیرفعالسازی"""
    query = update.callback_query
    await query.answer()

    alert_id = query.data.replace("confirm_del_alert_", "")

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ بله، غیرفعال شود", callback_data=f"do_del_alert_{alert_id}"),
            InlineKeyboardButton("❌ انصراف", callback_data="back_to_alerts_list"),
        ]
    ])

    await query.edit_message_text(
        "⚠️ **آیا از غیرفعال‌سازی این هشدار اطمینان دارید؟**",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

async def delete_alert_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مرحله ۲: اجرای نهایی غیرفعالسازی پس از تأیید"""
    query = update.callback_query
    await query.answer()

    alert_id = query.data.replace("do_del_alert_", "")

    # اجرای امن عملیات دیتابیس
    await deactivate_alert_by_id(alert_id)

    # بازگشت و نمایش مجدد لیست به روز شده
    await show_active_alerts_command(update, context)
# ------------------------------------------------------------------
# #.Create New Alert
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
        symbols = await asyncio.to_thread(get_market_watch_symbols)

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


        res = await asyncio.to_thread(check_symbol_info, symbol)

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
        last_msg_id=await update.callback_query.message.reply_text(next_msg, reply_markup=cancel_keyboard, parse_mode="Markdown")
    else:
        last_msg_id=await update.message.reply_text(next_msg, reply_markup=cancel_keyboard, parse_mode="Markdown")

    context.user_data["last_message_id"] = last_msg_id.message_id

    return ADD_ALERT_PRICE


async def add_alert_price_received(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    """دریافت قیمت هدف و ذخیره نهایی هشدار به همراه ارسال چارت"""
    chat_id = update.effective_chat.id
    symbol = str(context.user_data.get("symbol"))
    is_forex = context.user_data.get("is_forex", False)
    last_msg_id = context.user_data.get("last_message_id")

    # اطلاعات کاربر از آپدیت تلگرام
    user_info = update.effective_user
    username = user_info.username
    first_name = user_info.first_name

    try:
        target_price = float(update.message.text.strip())
    except ValueError:
        cancel_keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("❌ انصراف", callback_data="cancel_alert")]]
        )
        await update.message.reply_text(
            "❌ **قیمت هدف باید یک عدد معتبر باشد.**\nلطفاً قیمت را دوباره وارد کنید:",
            reply_markup=cancel_keyboard,
            parse_mode="Markdown",
        )
        return ADD_ALERT_PRICE

    # ۱. ارسال یک پیام موقت برای اعلام شروع پردازش به کاربر
    if last_msg_id:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=last_msg_id,
                text="⏳ **در حال ثبت هشدار و دریافت چارت...**\nلطفاً چند لحظه شکیبا باشید.",
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.warning("Could not edit last message: %s", e)

    # ۲. ثبت/بروزرسانی کاربر و ذخیره آلرت در دیتابیس
    user_obj = await get_or_create_user(
        chat_id=chat_id, username=username, first_name=first_name
    )
    market_type = MarketType.FOREX if is_forex else MarketType.CRYPTO

    alert, created = await create_user_alert(
        user=user_obj,
        symbol=symbol,
        target_price=target_price,
        market_type=market_type,
    )

    # اگر آلرت تکراری بود و ثبت نشد
    if not created:
        if last_msg_id:
            try:
                await context.bot.delete_message(
                    chat_id=chat_id, message_id=last_msg_id
                )
            except Exception:
                pass

        await update.message.reply_text(
            f"⚠️ **شما قبلاً یک هشدار فعال با همین قیمت (`{target_price}`) برای نماد `{symbol}` ثبت کرده‌اید.**",
            parse_mode="Markdown",
        )
        context.user_data.clear()
        return ConversationHandler.END

    logger.info(
        "Alert created successfully: %s at %s for chat_id %s",
        symbol,
        target_price,
        chat_id,
    )

    # ۳. ساخت متن کپشن پیام نهایی
    msg_text = (
        f"⏳ **هشدار جدید ثبت شد (در انتظار فعال‌سازی)**\n\n"
        f"📌 **نماد:** `{symbol}`\n"
        f"🎯 **قیمت هدف:** `{target_price}`\n"
        f"🌐 **بازار:** {'فارکس' if is_forex else 'ارز دیجیتال'}\n\n"
        f"🔹 *خط نقطه‌چین فیروزه‌ای روی چارت نشان‌دهنده تارگت جدید شماست.*"
    )

    # ۴. دریافت داده‌های کندل و تولید چارت
    chart_buf = None
    try:
        async with httpx.AsyncClient() as client:
            df_klines = await fetch_recent_klines(
                client=client,
                symbol=symbol,
                interval="30m",
                limit=50,
                is_forex=is_forex,
            )
            if df_klines is not None and not df_klines.empty:
                chart_buf = create_pending_alert_chart(
                    df_klines, target_price, symbol
                )
    except Exception as e:
        logger.exception(
            "Failed to generate chart for new alert %s: %s", symbol, e
        )

    # ۵. پاک کردن پیام موقت «در حال پردازش»
    if last_msg_id:
        try:
            await context.bot.delete_message(
                chat_id=chat_id, message_id=last_msg_id
            )
        except Exception:
            pass

    # ۶. ارسال پاسخ نهایی
    if chart_buf:
        await update.message.reply_photo(
            photo=chart_buf,
            caption=msg_text,
            reply_markup=MAIN_KEYBOARD,
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            msg_text,
            reply_markup=MAIN_KEYBOARD,
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
        logger.warning("auto_refresh_positions_job: No message_id provided for chat_id %s. Removing job.", chat_id)
        job.schedule_removal()
        return

    try:
        try:
            # ۱. دریافت جدیدترین لیست پوزیشن‌ها از متاتریدر در Executor
            success, positions = await asyncio.to_thread(get_open_positions)
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
            logger.debug("Successfully updated positions message for chat_id %s", chat_id)
        except BadRequest as e:
            err_msg = str(e).lower()
            if "message is not modified" in err_msg:
                logger.debug("Message not modified for chat_id %s (prices unchanged).", chat_id)
            else:
                logger.warning("Stopping live_pos_job for chat_id %s due to BadRequest: %s", chat_id, e)
                job.schedule_removal()
        except Exception as e:
            logger.error("Error editing positions message for chat_id %s: %s", chat_id, e, exc_info=True)
            job.schedule_removal()
    except Exception as e:
        logger.critical(
            "Unhandled fatal exception in auto_refresh_positions_job for chat_id %s: %s",
            chat_id, e,
            exc_info=True
        )

async def auto_refresh_single_position_job(context: ContextTypes.DEFAULT_TYPE):
    """آپدیت خودکار جزییات یک پوزیشن خاص هر چند ثانیه یک‌بار"""
    job = context.job
    chat_id = job.chat_id
    job_data = job.data or {}
    message_id = job_data.get("message_id")
    ticket = job_data.get("ticket")

    if not message_id or not ticket:
        logger.warning("auto_refresh_single_position_job: Missing message_id or ticket for chat_id %s. Removing job.",
                       chat_id)
        job.schedule_removal()
        return

    try:
        try:
            success, positions = await asyncio.to_thread(get_open_positions)
        except Exception as e:
            logger.error("Error executing get_open_positions in executor for ticket %s: %s", ticket, e, exc_info=True)
            return

        pos = next((p for p in positions if p["ticket"] == ticket), None) if success and positions else None

        # اگر پوزیشن بسته شده باشد، اطلاع بده و تایمر را متوقف کن
        if not pos:
            logger.info("Position ticket %s closed or not found for chat_id %s. Removing job.", ticket, chat_id)
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=f"❌ <b>پوزیشن <code>{ticket}</code> بسته شده است یا یافت نشد.</b>",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت به لیست", callback_data="refresh_positions_list")]]),
                    parse_mode="HTML",
                )
            except Exception as e:
                logger.warning("Failed to notify user about closed position ticket %s: %s", ticket, e)

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

        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=caption,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML",
            )
            logger.debug("Successfully updated single position message for ticket %s", ticket)
        except BadRequest as e:
            err_msg = str(e).lower()
            if "message is not modified" in err_msg:
                logger.debug("Single position message not modified for ticket %s.", ticket)
            else:
                logger.info("Stopping live_single_pos job for ticket %s due to UI transition or BadRequest: %s", ticket, e)
                job.schedule_removal()
        except Exception as e:
            logger.error("Error updating single position message for ticket %s: %s", ticket, e, exc_info=True)
            job.schedule_removal()

    except Exception as e:
        logger.critical(
            "Unhandled fatal exception in auto_refresh_single_position_job for ticket %s: %s",
            ticket, e,
            exc_info=True
        )


async def show_positions_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش لیست پوزیشن‌های باز و فعال‌سازی آپدیت زنده (Live Update)"""
    chat_id = update.effective_chat.id
    query = update.callback_query

    if query:
        await query.answer()

    # ۱. متوقف کردن تمامی تایمرهای قبلی (لیست کلی و تک پوزیشن)
    await stop_all_live_jobs(chat_id,context)


    # ۲. دریافت پوزیشن‌ها از متاتریدر
    success, positions = await asyncio.to_thread(get_open_positions)

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
    if context.job_queue:
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
    success, positions = await asyncio.to_thread( get_open_positions)

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

async def cancel_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("عملیات لغو شد.")

    # پاکسازی داده‌های موقت اکشن از context
    context.user_data.pop("action_ticket", None)
    context.user_data.pop("action_type", None)

    data_parts = query.data.split("_")
    ticket = int(data_parts[2]) if len(data_parts) == 3 else None

    # ویرایش پیام و بازگشت به جزئیات پوزیشن یا لیست اصلی
    if ticket:
        # فراخوانی مجدد نمایش جزئیات پوزیشن (یا هدایت کاربر به تابع position_detail_callback)
        await position_detail_callback(update, context)
    else:
        await query.edit_message_text(
            "❌ **عملیات لغو شد.**",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 بازگشت به لیست پوزیشن‌ها", callback_data="refresh_positions_list")]
            ]),
            parse_mode="Markdown"
        )

    # ⚠️ بسیار مهم: پایان دادن به وضعیت ConversationHandler
    return ConversationHandler.END

async def handle_position_actions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data_parts = query.data.split("_")
    chat_id = update.effective_chat.id

    # ۱. توقف حتمی و آنی تمام تایمرهای آپدیت زنده
    await stop_all_live_jobs(chat_id, context)

    # پشتیبانی از فرمت‌های مختلف callback_data
    if len(data_parts) == 3 and data_parts[0] == "action":
        action = data_parts[1]
        ticket = int(data_parts[2])
    elif len(data_parts) == 3 and data_parts[0] == "close" and data_parts[1] == "pos":
        action = "close"
        ticket = int(data_parts[2])
    else:
        action = data_parts[1]
        ticket = int(data_parts[2])

    # ------------------ ۱. بستن کامل پوزیشن ------------------
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

    elif action == "confirmclose":
        await query.edit_message_text(f"⏳ در حال بستن کامل پوزیشن `{ticket}`...", parse_mode="Markdown")
        success, msg = await asyncio.to_thread(close_position, ticket)
        back_keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔙 بازگشت به لیست پوزیشن‌ها", callback_data="refresh_positions_list")]])
        await query.edit_message_text(f"{msg}", reply_markup=back_keyboard, parse_mode="Markdown")

    # ------------------ ۲. فری‌ریسک (Break-Even) ------------------
    # ۲-الف: درخواست تأیید فری‌ریسک
    elif action == "be":
        confirm_keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ بله، فری‌ریسک شود", callback_data=f"action_confirmbe_{ticket}"),
                InlineKeyboardButton("❌ انصراف", callback_data=f"pos_detail_{ticket}")
            ]
        ])
        await query.edit_message_text(
            f"🛡 **تأییدیه فری‌ریسک (Break-Even) پوزیشن `{ticket}`**\n\n"
            f"آیا مطمئن هستید که می‌خواهید حد ضرر به نقطه ورود منتقل شود؟",
            reply_markup=confirm_keyboard,
            parse_mode="Markdown"
        )

    # ۲-ب: اجرای واقعی فری‌ریسک پس از تأیید
    elif action == "confirmbe":
        await query.edit_message_text(f"⏳ در حال انتقال حد ضرر پوزیشن `{ticket}` به نقطه ورود...",
                                      parse_mode="Markdown")
        _, msg = await asyncio.to_thread(set_break_even, ticket)
        await query.edit_message_text(
            f"{msg}",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 بازگشت به لیست", callback_data="refresh_positions_list")]]),
            parse_mode="Markdown"
        )

    # ------------------ ۳. خروج ۲۵٪ حجم ------------------
    # ۳-الف: درخواست تأیید خروج ۲۵٪
    elif action == "close25":
        success, positions = await asyncio.to_thread(get_open_positions)
        pos = next((p for p in positions if p['ticket'] == ticket), None) if success and positions else None

        if not pos:
            await query.edit_message_text("❌ پوزیشن یافت نشد یا قبلاً بسته شده است.", parse_mode="Markdown")
            return

        sm_vol = round(pos['volume'] / 3, 2)
        if sm_vol < 0.01:
            await query.edit_message_text("⚠️ **حجم پوزیشن برای خروج 25٪ بسیار کوچک است (کمتر از 0.01).**",
                                          parse_mode="Markdown")
            return

        confirm_keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ بله، خروج ۲۵٪", callback_data=f"action_confirmclose25_{ticket}"),
                InlineKeyboardButton("❌ انصراف", callback_data=f"pos_detail_{ticket}")
            ]
        ])
        await query.edit_message_text(
            f"✂️ **تأییدیه خروج ۲۵٪ از پوزیشن `{ticket}`**\n\n"
            f"حجم کل: `{pos['volume']}` لات\n"
            f"حجم خروج: `{sm_vol}` لات\n\n"
            f"آیا از بستن این میزان حجم اطمینان دارید؟",
            reply_markup=confirm_keyboard,
            parse_mode="Markdown"
        )

    # ۳-ب: اجرای واقعی خروج ۲۵٪ پس از تأیید
    elif action == "confirmclose25":
        success, positions = await asyncio.to_thread(get_open_positions)
        pos = next((p for p in positions if p['ticket'] == ticket), None) if success and positions else None

        if not pos:
            await query.edit_message_text("❌ پوزیشن یافت نشد یا قبلاً بسته شده است.", parse_mode="Markdown")
            return

        sm_vol = round(pos['volume'] / 3, 2)
        await query.edit_message_text(f"⏳ در حال بستن `{sm_vol}` لات از پوزیشن `{ticket}`...", parse_mode="Markdown")
        _, msg = await asyncio.to_thread(close_position, ticket, sm_vol)
        await query.edit_message_text(
            f"{msg}",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 بازگشت به لیست", callback_data="refresh_positions_list")]]),
            parse_mode="Markdown"
        )

    # ------------------ ۴. خروج ۵۰٪ حجم ------------------
    # ۴-الف: درخواست تأیید خروج ۵۰٪
    elif action == "close50":
        success, positions = await asyncio.to_thread(get_open_positions)
        pos = next((p for p in positions if p['ticket'] == ticket), None) if success and positions else None

        if not pos:
            await query.edit_message_text("❌ پوزیشن یافت نشد یا قبلاً بسته شده است.", parse_mode="Markdown")
            return

        half_vol = round(pos['volume'] / 2, 2)
        if half_vol < 0.01:
            await query.edit_message_text("⚠️ **حجم پوزیشن برای خروج ۵۰٪ بسیار کوچک است (کمتر از 0.01).**",
                                          parse_mode="Markdown")
            return

        confirm_keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ بله، خروج ۵۰٪", callback_data=f"action_confirmclose50_{ticket}"),
                InlineKeyboardButton("❌ انصراف", callback_data=f"pos_detail_{ticket}")
            ]
        ])
        await query.edit_message_text(
            f"✂️ **تأییدیه خروج ۵۰٪ از پوزیشن `{ticket}`**\n\n"
            f"حجم کل: `{pos['volume']}` لات\n"
            f"حجم خروج: `{half_vol}` لات\n\n"
            f"آیا از بستن این میزان حجم اطمینان دارید؟",
            reply_markup=confirm_keyboard,
            parse_mode="Markdown"
        )

    # ۴-ب: اجرای واقعی خروج ۵۰٪ پس از تأیید
    elif action == "confirmclose50":
        success, positions = await asyncio.to_thread(get_open_positions)
        pos = next((p for p in positions if p['ticket'] == ticket), None) if success and positions else None

        if not pos:
            await query.edit_message_text("❌ پوزیشن یافت نشد یا قبلاً بسته شده است.", parse_mode="Markdown")
            return

        half_vol = round(pos['volume'] / 2, 2)
        await query.edit_message_text(f"⏳ در حال بستن `{half_vol}` لات از پوزیشن `{ticket}`...", parse_mode="Markdown")
        _, msg = await asyncio.to_thread(close_position, ticket, half_vol)
        await query.edit_message_text(
            f"{msg}",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 بازگشت به لیست", callback_data="refresh_positions_list")]]),
            parse_mode="Markdown"
        )

    # ------------------ ۵. ورود به مرحله دریافت SL و TP جدید ------------------
    elif action == "editsltp":
        context.user_data["action_ticket"] = ticket
        context.user_data["action_type"] = "sltp"

        # 💡 تغییر callback_data به cancel_action_ticket
        cancel_btn = InlineKeyboardMarkup([[InlineKeyboardButton("❌ انصراف", callback_data=f"cancel_action_{ticket}")]])

        await query.edit_message_text(
            f"✏️ **ویرایش حد ضرر و حد سود پوزیشن `{ticket}`**\n\n"
            f"لطفاً **حد ضرر (SL)** و **حد سود (TP)** جدید را با یک فاصله وارد کنید:\n"
            f"💡 **فرمت:** `<SL> <TP>`\n"
            f"مثال: `2030.50 2060.00` (برای عدم تغییر هرکدام عدد 0 بگذارید)",
            reply_markup=cancel_btn,
            parse_mode="Markdown"
        )
        return INPUT_NEW_SL_TP

    # ------------------ ۶. ورود به مرحله خروج جزئی دلخواه ------------------
    elif action == "partial":
        context.user_data["action_ticket"] = ticket

        # 💡 تغییر callback_data به cancel_action_ticket
        cancel_btn = InlineKeyboardMarkup([[InlineKeyboardButton("❌ انصراف", callback_data=f"cancel_action_{ticket}")]])

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


    msg = await update.message.reply_text("⏳ در حال بروزرسانی حد ضرر و حد سود...", parse_mode="Markdown")

    # اعتمادسازی و فراخوانی متاتریدر برای آپدیت SL/TP
    _, res_msg = await asyncio.to_thread(update_position_sltp, ticket, new_sl, new_tp)

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

    msg = await update.message.reply_text(f"⏳ در حال بستن `{vol}` لات از پوزیشن `{ticket}`...", parse_mode="Markdown")

    _, res_msg = await asyncio.to_thread(close_position, ticket, vol)

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
    symbols = await asyncio.to_thread(get_market_watch_symbols)

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
    # ۱. دریافت اطلاعات کاربر تلگرام
    user = update.effective_user
    telegram_id = user.id
    username = user.username or user.first_name

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



    success, result_msg, rr_ratio = await asyncio.to_thread(
        execute_trade, symbol, action, lot,
        entry_price, sl, tp, telegram_id,username
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
        f"👤 <b>کاربر:</b> <code>{username}</code> (<code>{telegram_id}</code>)\n"
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

    _, message = await asyncio.to_thread(close_position_by_ticket, ticket)
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

    # ۲. اجرای غیربلاک‌کننده بستن همه پوزیشن‌ها
    res_msg = await asyncio.to_thread(close_all_positions)
    logger.info("Close ALL positions result: %s", res_msg)

    # نمایش نتیجه و دکمه بازگشت به لیست
    back_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 بازگشت به لیست پوزیشن‌ها", callback_data="refresh_positions_list")]
    ])
    await query.edit_message_text(f"{res_msg}", reply_markup=back_keyboard, parse_mode="HTML")

# ------------------------------------------------------------------
# 7. Watchlist & Conversation Handlers
# -----------------------------------------------------------------
async def show_watchlist_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    # ۱. پاسخ سریع جهت رفع لودینگ دکمه شیشه‌ای
    if query:
        try:
            await query.answer()
        except Exception as e:
            logger.warning("Could not answer callback query: %s", e)

    try:
        watchlist = await get_all_watchlist()

        # ۲. بررسی خالی بودن واچ‌لیست (اصلاح‌شده)
        if not watchlist:
            text = "📭 **واچ‌لیست شما خالی است!**"

            # دکمه شیشه‌ای بازگشت/افزودن برای حالت Callback
            empty_inline_keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ افزودن نماد جدید", callback_data="addWatchlist")],
                # یا Callback مربوط به منوی اصلی شما
            ])

            if query:
                try:
                    # ویرایش پیام با کیبورد Inline (جلوگیری از ارسال ReplyKeyboardMarkup به edit_message_text)
                    await query.edit_message_text(text, reply_markup=empty_inline_keyboard, parse_mode="Markdown")
                except BadRequest as e:
                    if "Message is not modified" not in str(e):
                        await query.message.reply_text(text, reply_markup=MAIN_KEYBOARD, parse_mode="Markdown")
            else:
                await update.message.reply_text(text, reply_markup=empty_inline_keyboard, parse_mode="Markdown")
            return

        # ۳. ساخت متن و دکمه‌ها
        msg = "📊 **لیست نمادهای تحت نظر:**\n\n"
        buttons = []
        row = []

        for watch in watchlist:
            sym = str(watch.symbol).replace("_", "\\_")
            tf = str(watch.time_frame)
            m_type = str(watch.market_type)

            msg += f"• `{sym}` ({tf}) - {m_type}\n"

            row.append(
                InlineKeyboardButton(
                    f"❌ {watch.symbol} ({tf})",
                    callback_data=f"del_watchlist_{watch.symbol}_{tf}_{m_type}"
                )
            )
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)


        msg += "\n*جهت حذف هر نماد، روی دکمه مربوط به آن کلیک کنید:*"
        reply_markup = InlineKeyboardMarkup(buttons)

        # ۴. ارسال یا ویرایش پیام
        if query:
            try:
                await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=reply_markup)
            except BadRequest as e:
                if "Message is not modified" in str(e):
                    pass
                else:
                    await query.message.reply_text(msg, parse_mode="Markdown", reply_markup=reply_markup)
        else:
            await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=reply_markup)

    except Exception as e:
        logger.exception("Error in show_watchlist_command: %s", e)
        error_msg = "🚨 **خطا در دریافت اطلاعات واچ‌لیست!**"


        if query:
            try:
                await query.edit_message_text(error_msg, reply_markup=MAIN_KEYBOARD, parse_mode="Markdown")
            except Exception:
                await query.message.reply_text(error_msg, reply_markup=MAIN_KEYBOARD, parse_mode="Markdown")
        elif update.message:
            await update.message.reply_text(error_msg, reply_markup=MAIN_KEYBOARD, parse_mode="Markdown")

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

    res_msg = await delete_from_watchlist(symbol,timeframe, market_type)

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


# ------------------------------------------------------------------
# #. Watchlist Conversation steps
# ------------------------------------------------------------------
ADD_WATCHLIST_MARKET, ADD_WATCHLIST_SYMBOL, ADD_WATCHLIST_TIMEFRAME = (
    "ADD_WATCHLIST_MARKET",
    "ADD_WATCHLIST_SYMBOL",
    "ADD_WATCHLIST_TIMEFRAME"
)


# مرحله ۱: شروع و درخواست انتخاب مارکت
async def start_add_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("Starting addWatchlist conversation for chat_id %s", update.effective_chat.id)
    context.user_data.clear()

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 کریپتو", callback_data="CRYPTO"),
         InlineKeyboardButton("📈 فارکس", callback_data="FOREX")],
        [InlineKeyboardButton("❌ انصراف", callback_data="cancel_watchlist")],
    ])

    msg_text = "🏷 **مرحله ۱ از ۳:** لطفاً نوع بازار را انتخاب کنید:"

    if update.message:
        await update.message.reply_text(msg_text, parse_mode="Markdown", reply_markup=keyboard)
    elif update.callback_query:
        await update.callback_query.edit_message_text(msg_text, parse_mode="Markdown", reply_markup=keyboard)

    return ADD_WATCHLIST_MARKET


# مرحله ۲: ذخیره مارکت و درخواست نماد (با پشتیبانی از واچ‌لیست MT5)
async def add_watchlist_get_market_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    market_type = query.data
    context.user_data['market_type'] = market_type
    logger.info("AddWatchlist step 1 - Market selected: %s", market_type)

    keyboard_buttons = []
    msg_text = f"🏷 **بازار:** `{market_type}`\n\n"

    # اگر فارکس بود، نمادها را از متاتریدر می‌گیریم
    if market_type == "FOREX":
        msg_text += "📝 **مرحله ۲ از ۳:** نماد مورد نظر را از لیست زیر انتخاب کرده یا نام آن را دقیق تایپ کنید:"
        try:
            # فراخوانی تابع دریافت واچ‌لیست متاتریدر (با فرض اینکه این تابع را از قبل دارید)
            symbols = await asyncio.to_thread(get_market_watch_symbols)

            # چیدمان ۲ تایی دکمه‌ها
            row = []
            for sym in symbols:
                row.append(InlineKeyboardButton(sym, callback_data=f"sym_{sym}"))
                if len(row) == 2:
                    keyboard_buttons.append(row)
                    row = []
            if row:
                keyboard_buttons.append(row)
        except Exception as e:
            logger.error("Failed to load MT5 symbols: %s", e)
            msg_text += "\n*(خطا در دریافت لیست متاتریدر. لطفاً نام نماد را تایپ کنید)*"
    else:
        # برای کریپتو فقط پیام تایپ دستی می‌دهیم
        msg_text += "📝 **مرحله ۲ از ۳:** لطفاً نام نماد را وارد کنید (مثلاً `BTCUSDT`):"

    keyboard_buttons.append([InlineKeyboardButton("❌ انصراف", callback_data="cancel_watchlist")])
    keyboard = InlineKeyboardMarkup(keyboard_buttons)

    await query.edit_message_text(msg_text, parse_mode="Markdown", reply_markup=keyboard)
    return ADD_WATCHLIST_SYMBOL


# مرحله ۳: ذخیره نماد و درخواست تایم‌فریم
async def add_watchlist_get_symbol_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # بررسی اینکه ورودی از کلیک دکمه بوده یا تایپ دستی
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        symbol = query.data.replace("sym_", "").upper()
        message_func = query.edit_message_text
    else:
        symbol = update.message.text.strip().upper()
        message_func = update.message.reply_text

    context.user_data['symbol'] = symbol
    market_type = context.user_data.get('market_type', 'UNKNOWN')
    logger.info("AddWatchlist step 2 - Symbol entered: %s", symbol)

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("5m", callback_data="5m"), InlineKeyboardButton("15m", callback_data="15m")],
        [InlineKeyboardButton("30m", callback_data="30m"), InlineKeyboardButton("1h", callback_data="1h")],
        [InlineKeyboardButton("4h", callback_data="4h"), InlineKeyboardButton("1d", callback_data="1d")],
        [InlineKeyboardButton("❌ انصراف", callback_data="cancel_watchlist")],
    ])

    msg_text = (
        f"🏷 **بازار:** `{market_type}`\n"
        f"📌 **نماد:** `{symbol}`\n\n"
        "⏱ **مرحله ۳ از ۳:** تایم‌فریم را انتخاب کنید:"
    )

    await message_func(msg_text, parse_mode="Markdown", reply_markup=keyboard)
    return ADD_WATCHLIST_TIMEFRAME


# مرحله ۴: ذخیره تایم‌فریم و ثبت نهایی در واچ‌لیست
async def add_watchlist_get_timeframe_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    await query.edit_message_text("⏳ **در حال ثبت در واچ لیست...**", parse_mode="Markdown")

    timeframe = query.data
    symbol = context.user_data['symbol']
    market_type = context.user_data['market_type']

    # دریافت اطلاعات کاربر تلگرام
    chat_id = update.effective_chat.id
    user_info = update.effective_user

    # ۱. دریافت یا ثبت کاربر در دیتابیس
    user_obj = await get_or_create_user(
        chat_id=chat_id,
        username=user_info.username,
        first_name=user_info.first_name
    )

    logger.info("AddWatchlist step 3 - Finished. User: %s %s %s %s", chat_id, market_type, symbol, timeframe)

    # ۲. ذخیره در دیتابیس
    created = await save_watchlist_item(
        user=user_obj,
        symbol=symbol,
        timeframe=timeframe,
        market_type=market_type
    )

    if not created:
        await query.edit_message_text(
            f"⚠️ نماد `{symbol}` ({market_type}) در تایم‌فریم `{timeframe}` قبلاً در واچ‌لیست شما ثبت شده است.",
            parse_mode="Markdown"
        )
        context.user_data.clear()
        return ConversationHandler.END

    # ۳. شروع تسک جدید (در صورت ثبت موفق)
    worker_key = f"{chat_id}_{symbol}_{timeframe}_{market_type}"  # بهتره chat_id هم کلید ورکر اضافه بشه تا مجزا باشه
    task = asyncio.create_task(worker_loop(symbol, timeframe, market_type, chat_id, context.bot))
    ACTIVE_WORKERS[worker_key] = task
    logger.info("Created new worker task for key: %s", worker_key)

    await query.edit_message_text(
        f"✨ نماد `{symbol}` ({market_type}) در تایم‌فریم `{timeframe}` به واچ‌لیست شما اضافه و پایش آن فعال گردید.",
        parse_mode="Markdown"
    )

    context.user_data.clear()
    return ConversationHandler.END


# هندلر لغو عملیات
async def cancel_watch_list_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("AddWatchlist conversation cancelled by user %s", update.effective_chat.id)
    context.user_data.clear()

    query = update.callback_query
    if query:
        await query.answer()
        await query.edit_message_text("❌ **عملیات افزودن به واچ‌لیست لغو شد.**", parse_mode="Markdown")
        await update.effective_chat.send_message(
            MAIN_MENU_TEXT,
            parse_mode="Markdown",
            reply_markup=MAIN_KEYBOARD
        )
    else:
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

        extracted_text = await  extract_trade_from_image(temp_image_path)

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


    # ۱. استخراج دیتای MT5
    trades, error = await asyncio.to_thread(get_trades_history, days)

    if error or not trades:
        await query.edit_message_text(f"⚠️ {error or 'هیچ معامله‌ای یافت نشد.'}")
        await update.effective_chat.send_message("🏠 **به منوی اصلی بازگشتید.**", reply_markup=MAIN_KEYBOARD,
                                                 parse_mode="Markdown")
        return ConversationHandler.END

    # ۲. تحلیل Gemini
    ai_analysis = await analyze_trades_with_gemini(trades, period_name)

    # ۳. تولید فایل PDF
    pdf_filename = f"Trade_Report_{update.effective_user.id}_{days}d.pdf"
    await asyncio.to_thread(generate_pdf_report, pdf_filename, period_name, trades, ai_analysis)

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

    initial_deals = await asyncio.to_thread(check_recent_closed_positions, 24)
    notified_deals = {deal["deal_id"] for deal in initial_deals}
    logger.info(f"🔰 SL/TP Monitor ready. Ignored {len(notified_deals)} past deals.")

    try:
        while True:
            try:
                # ۱. دریافت معاملات ۲۴ ساعت گذشته
                closed_deals = await asyncio.to_thread(check_recent_closed_positions, 24)

                new_deals_notified_count = 0

                # ۲. حلقه اول: ارسال پیام هشدار مجزا برای تک‌تک معاملات جدید
                for deal in closed_deals:
                    deal_id = deal["deal_id"]

                    if deal_id not in notified_deals:
                        profit = deal.get("profit", 0.0) or 0.0
                        profit_icon = "🟢" if profit >= 0 else "🔴"

                        # ساخت پیام اختصاصی معامله
                        alert_msg = (
                            f"🔔 <b>هشدار بسته‌شدن پوزیشن!</b>\n\n"
                            f"🎫 <b>تیکت:</b> <code>{deal['position_id']}</code>\n"
                            f"📌 <b>نماد:</b> <b>{deal['symbol']}</b>\n"
                            f"📌 <b>علت خروج:</b> {deal['exit_type']}\n"
                            f"📊 <b>حجم:</b> <code>{deal['volume']}</code> لات\n"
                            f"🏁 <b>قیمت خروج:</b> <code>{deal['exit_price']}</code>\n"
                            f"{profit_icon} <b>سود/زیان معامله:</b> <code>${profit:,.2f}</code>"
                        )

                        # ارسال پیام هشدار خروج معامله
                        await bot.send_message(
                            chat_id=chat_id,
                            text=alert_msg,
                            parse_mode="HTML"
                        )
                        logger.info(f"✅ Alert sent for NEW Deal {deal_id}")

                        # علامت‌گذاری معامله
                        notified_deals.add(deal_id)
                        new_deals_notified_count += 1

                # ۳. اگر حداقل یک معامله جدید در این پارت فرستاده شد، حالا پیام جداگانه داشبورد روزانه ارسال می‌شود
                if new_deals_notified_count > 0:
                    # محاسبه آمار معاملات امروز
                    total_trades, wins, losses, win_rate, net_profit, avg_win, avg_loss, profits_history = calculate_today_stats(closed_deals)
                    net_icon = "🚀" if net_profit >= 0 else "🔻"

                    # متن پیام گزارش روزانه
                    summary_msg = (
                        f"📊 <b>گزارش عملکرد کل امروز ({datetime.now().strftime('%Y-%m-%d')})</b>\n\n"
                        f"🔢 <b>مجموع معاملات امروز:</b> <code>{total_trades}</code>\n"
                        f"✅ <b>تعداد برد:</b> <code>{wins}</code> | ❌ <b>تعداد باخت:</b> <code>{losses}</code>\n"
                        f"🎯 <b>وین‌ریت (Win Rate):</b> <code>%{win_rate:.1f}</code>\n"
                        f"📈 <b>میانگین سود:</b> <code>${avg_win:,.2f}</code> | 📉 <b>میانگین زیان:</b> <code>${avg_loss:,.2f}</code>\n"
                        f"───────────────────\n"
                        f"{net_icon} <b>سود/زیان کل امروز:</b> <b><code>${net_profit:,.2f}</code></b>"
                    )

                    # تولید داشبورد گرافیکی
                    chart_buf = await asyncio.to_thread(
                        generate_pro_daily_dashboard, wins, losses, net_profit, win_rate, avg_win, avg_loss, profits_history
                    )

                    # ارسال داشبورد در یک پیام جداگانه (تصویر + کاپشن)
                    await bot.send_photo(
                        chat_id=chat_id,
                        photo=chart_buf,
                        caption=summary_msg,
                        parse_mode="HTML"
                    )
                    logger.info("📊 Daily Dashboard sent as a separate message.")

            except Exception as e:
                logger.error("Error in SL/TP monitor loop: %s", e, exc_info=True)

            # چک کردن هر ۵ ثانیه
            await asyncio.sleep(5)

    except asyncio.CancelledError:
        logger.info("🛑 [STOPPED] Global SL/TP Monitor Task")

# ------------------------------------------------------------------
# 14. Application Startup & Main Execution
# ------------------------------------------------------------------
async def on_startup(app):
    logger.info("⚡ Initializing MetaTrader 5 Connection...")
    # متصل کردن متاتریدر ۵ ابتدای اجرای برنامه
    mt5_initialized = await asyncio.to_thread(start_mt5_connection)
    if not mt5_initialized:
        logger.error("❌ Failed to initialize MetaTrader 5")
        return

    logger.info("✅ MetaTrader 5 initialized successfully.")
    try:
        logger.info("Starting bot background workers...")
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
    except KeyboardInterrupt:
        logger.info("درخواست توقف ربات از طرف کاربر دریافت شد.")

async def on_shutdown(app):
    logger.info("🛑 Stopping background workers...")

    # لغو تمامی تسک‌های فعال
    for worker_key, task in ACTIVE_WORKERS.items():
        if not task.done():
            task.cancel()
            logger.info("Cancelling worker: %s", worker_key)

    await asyncio.gather(*ACTIVE_WORKERS.values(), return_exceptions=True)
    ACTIVE_WORKERS.clear()
    logger.info("✅ All background workers stopped cleanly.")

    # قطع اتصال متاتریدر ۵ هنگام خاتمه ربات
    await asyncio.to_thread(stop_mt5_connection)
    logger.info("🛑 MetaTrader 5 connection closed cleanly.")

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
            CallbackQueryHandler(start_add_watchlist, pattern="^addWatchlist$"),
        ],
        states={
            ADD_WATCHLIST_MARKET: [
                CallbackQueryHandler(cancel_watch_list_callback, pattern="^cancel_watchlist$"),
                CallbackQueryHandler(add_watchlist_get_market_step, pattern="^(CRYPTO|FOREX)$"),
            ],
            ADD_WATCHLIST_SYMBOL: [
                CallbackQueryHandler(cancel_watch_list_callback, pattern="^cancel_watchlist$"),
                CallbackQueryHandler(add_watchlist_get_symbol_step, pattern="^sym_"),  # برای کلیک روی دکمه‌های متاتریدر
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_watchlist_get_symbol_step),  # برای تایپ دستی
            ],
            ADD_WATCHLIST_TIMEFRAME: [
                CallbackQueryHandler(cancel_watch_list_callback, pattern="^cancel_watchlist$"),
                CallbackQueryHandler(add_watchlist_get_timeframe_step, pattern="^(5m|15m|30m|1h|4h|1d)$"),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(cancel_watch_list_callback, pattern="^cancel_watchlist$"),
        ],
        per_message=False,
        per_chat=True,
        per_user=True,
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
    app.add_handler(MessageHandler(filters.Text(["🔔 هشدارهای فعال"]), show_active_alerts_command))

    # ------------------ 5️⃣ دکمه‌های شیشه‌ای (Callback Query Handlers) ------------------
    # حذف آلرت
    # ۱. درخواست تأیید قبل از حذف
    app.add_handler(CallbackQueryHandler(confirm_delete_alert_callback, pattern="^confirm_del_alert_"))
    # ۲. انجام نهایی حذف پس از کلیک روی "بله"
    app.add_handler(CallbackQueryHandler(delete_alert_callback, pattern="^do_del_alert_"))
    # ۳. دکمه بازگشت به لیست هشدارها در صورت انصراف
    app.add_handler(CallbackQueryHandler(show_active_alerts_command, pattern="^back_to_alerts_list$"))

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