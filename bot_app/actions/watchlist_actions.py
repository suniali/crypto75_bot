import asyncio
import logging

from telegram import Update,InlineKeyboardButton,InlineKeyboardMarkup
from telegram.ext import ContextTypes,ConversationHandler
from telegram.error import BadRequest

from bot_app.workers import ACTIVE_WORKERS
from bot_app.utils.keyboard_helper import MAIN_KEYBOARD,MAIN_MENU_TEXT

from bot_app.services.watchlist_service import (
    get_all_watchlist,
    save_watchlist_item,
    delete_from_watchlist
)
from bot_app.services.mt5_service import get_market_watch_symbols
from bot_app.services.user_service import get_or_create_user

from bot_app.loops.worker_loop import worker_loop

logger = logging.getLogger(__name__)

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
