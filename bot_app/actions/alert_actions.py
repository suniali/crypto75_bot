import asyncio
import logging
import httpx

from telegram import Update,InlineKeyboardButton,InlineKeyboardMarkup
from telegram.ext import ContextTypes,ConversationHandler
from telegram.error import BadRequest

from bot_app.utils.keyboard_helper import MAIN_KEYBOARD

from bot_app.services.alert_service import (
    fetch_active_alerts,
    deactivate_alert_by_id,
    create_user_alert
)
from bot_app.services.mt5_service import get_market_watch_symbols,check_symbol_info
from bot_app.services.user_service import get_or_create_user
from bot_app.services.chart_service import create_pending_alert_chart
from bot_app.services.api_service import fetch_recent_klines

from bot_app.models import MarketType

logger = logging.getLogger(__name__)

async def show_active_alerts_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

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

