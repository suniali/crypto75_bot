import  os
import sys
import asyncio
import django
from pathlib import Path
from decouple import config

from asgiref.sync import sync_to_async

# prepare main project
BASE_DIR=Path(__file__).resolve().parent.parent

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0,str(BASE_DIR))

# prepare project for run from this file
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from telegram import Update,InlineKeyboardButton,InlineKeyboardMarkup,ReplyKeyboardMarkup
from telegram.ext import (
    ContextTypes, CommandHandler, ApplicationBuilder,
    MessageHandler,filters,CallbackQueryHandler,ConversationHandler,
)

from bot_app.models import UserAlert,Watchlist
from bot_app.mt5_service import *
from bot_app.analysis_service import *

TOKEN=config('TELEGRAM_BOT_TOKEN')
ADMIN_CHAT_ID = config('ADMIN_CHAT_ID')

TIMEFRAME_TO_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_name = update.effective_user.first_name

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
        "├ ➕ افزودن نماد: `/addWatchlist <نماد> <تایم‌فریم> <بازار>`\n"
        "│   مثال: `/addWatchlist BTCUSDT 15m CRYPTO`\n"
        "└ ➖ حذف نماد: `/removeWatchlist <نماد> <تایم‌فریم> <بازار>`\n"
        "    مثال: `/removeWatchlist BTCUSDT 15m CRYPTO`\n\n"

        "💡 _برای استفاده سریع‌تر می‌توانید از دکمه‌های زیر استفاده کنید._"
    )

    main_keyboard = [
        ["📋 واچ‌لیست", "➕ افزودن به واچ‌لیست"],
        ["📊 پوزیشن‌های باز", "❌ بستن همه پوزیشن‌ها"]
    ]

    replay_markup= ReplyKeyboardMarkup(main_keyboard,resize_keyboard=True)

    await update.message.reply_text(
        welcome_text,
        parse_mode="Markdown",
        reply_markup=replay_markup
    )

@sync_to_async
def save_alert_to_db(chat_id,symbol,target_price,is_forex):
    is_user_alert_exists=UserAlert.objects.filter(symbol=symbol,target_price=target_price,is_active=True).exists()

    if is_user_alert_exists:
        return None

    market_type='FOREX' if is_forex else 'CRYPTO'
    return UserAlert.objects.create(
            chat_id=chat_id,
            symbol=symbol,
            target_price=target_price,
            market_type=market_type
    )

async def set_alert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        # بررسی تعداد ورودی‌ها
        if len(context.args) < 2:
            usage_text = (
                "⚠️ **فرمت دستور هشدار قیمت ناقص است!**\n\n"
                "📌 **شکل صحیح دستور:**\n"
                "`/alert <نماد> <قیمت_هدف>`\n\n"
                "💡 **مثال:**\n"
                "`/alert BTCUSDT 65000`"
            )
            await update.message.reply_text(usage_text, parse_mode="Markdown")
            return

        chat_id = str(update.effective_chat.id)
        symbol = context.args[0].upper()
        target_price = float(context.args[1])

        # پیام در حال پردازش
        saving_msg = await update.message.reply_text(
            f"⏳ **در حال ثبت هشدار برای `{symbol}`...**",
            parse_mode="Markdown"
        )

        # ذخیره در دیتابیس (False به معنی کریپتو / غیر فارکس)
        await save_alert_to_db(chat_id, symbol, target_price, False)

        success_text = (
            "🔔 **هشدار قیمت با موفقیت ثبت شد**\n\n"
            f"📌 **نماد:** `{symbol}`\n"
            f"🎯 **قیمت هدف:** `{target_price:,.2f}`\n\n"
            "✅ به محض رسیدن قیمت به این حد، پیام هشدار برای شما ارسال خواهد شد."
        )
        await saving_msg.edit_text(success_text, parse_mode="Markdown")

    except (IndexError, ValueError):
        invalid_format_text = (
            "❌ **خطا در فرمت ورودی!**\n\n"
            "قیمت هدف باید یک عدد معتبر باشد.\n"
            "💡 **مثال درست:** `/alert BTCUSDT 65000`"
        )
        await update.message.reply_text(invalid_format_text, parse_mode="Markdown")


async def set_falert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        # بررسی تعداد ورودی‌ها
        if len(context.args) < 2:
            usage_text = (
                "⚠️ **فرمت دستور هشدار قیمت (فارکس/فلزات) ناقص است!**\n\n"
                "📌 **شکل صحیح دستور:**\n"
                "`/falert <نماد> <قیمت_هدف>`\n\n"
                "💡 **مثال:**\n"
                "`/falert XAUUSD-ECN 2100.50`"
            )
            await update.message.reply_text(usage_text, parse_mode="Markdown")
            return

        chat_id = str(update.effective_chat.id)
        symbol = context.args[0].upper()
        target_price = float(context.args[1])

        # پیام در حال بررسی برای تجربه کاربری بهتر
        checking_msg = await update.message.reply_text(
            f"🔍 **در حال بررسی نماد `{symbol}` در متاتریدر...**",
            parse_mode="Markdown"
        )

        # بررسی وجود نماد در متاتریدر به صورت غیرهمزمان (Non-blocking)
        loop = asyncio.get_running_loop()
        res = await loop.run_in_executor(None, check_symbol_info, symbol)

        if res is True:
            # ذخیره در دیتابیس
            await save_alert_to_db(chat_id, symbol, target_price, True)

            success_text = (
                "🔔 **هشدار قیمت با موفقیت ثبت شد**\n\n"
                f"📌 **نماد:** `{symbol}`\n"
                f"🎯 **قیمت هدف:** `{target_price:,.2f}`\n\n"
                "✅ به محض رسیدن قیمت به این حد، پیام هشدار برای شما ارسال خواهد شد."
            )
            await checking_msg.edit_text(success_text, parse_mode="Markdown")

        elif isinstance(res, list):
            # اگر نماد یافت نشد اما لیستی از پیشنهادها برگشت
            suggestions = "\n".join([f"▫️ `{s}`" for s in res[:10]])  # نمایش حداکثر ۱۰ پیشنهاد
            not_found_text = (
                f"⚠️ **نماد `{symbol}` یافت نشد!**\n\n"
                "این نماد در Market Watch متاتریدر موجود یا فعال نیست.\n\n"
                f"💡 **پیشنهاد نمادهای مشابه در بروکر:**\n{suggestions}"
            )
            await checking_msg.edit_text(not_found_text, parse_mode="Markdown")

        else:
            # اگر خطایی در اتصال به MT5 پیش آمد
            error_text = (
                "🚨 **خطا در اتصال به MetaTrader 5!**\n\n"
                "❌ امکان برقراری ارتباط با پلتفرم متاتریدر وجود ندارد.\n"
                "لطفاً مطمئن شوید نرم‌افزار MT5 باز و متصل به حساب است."
            )
            await checking_msg.edit_text(error_text, parse_mode="Markdown")

    except (IndexError, ValueError):
        invalid_format_text = (
            "❌ **خطا در فرمت ورودی!**\n\n"
            "قیمت هدف باید یک عدد معتبر باشد.\n"
            "💡 **مثال درست:** `/falert XAUUSD-ECN 2100`"
        )
        await update.message.reply_text(invalid_format_text, parse_mode="Markdown")

async def close_all_command(update:Update,context:ContextTypes.DEFAULT_TYPE):
    msg = close_all_positions()
    await update.message.reply_text(msg)

async def trade_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # بررسی تعداد ورودی‌ها
    if len(context.args) < 5:
        usage_text = (
            "⚠️ **فرمت دستور معامله ناقص است!**\n\n"
            "📌 **شکل صحیح دستور:**\n"
            "`/trade <BUY/SELL> <نماد> <حجم> <حد_زیان> <حد_سود>`\n\n"
            "💡 **مثال:**\n"
            "`/trade BUY EURUSD 0.1 300 600`"
        )
        await update.message.reply_text(usage_text, parse_mode="Markdown")
        return

    # استخراج و اعتبارسنجی نوع داده‌ها
    try:
        action = context.args[0].upper()
        symbol = context.args[1].upper()
        lot = float(context.args[2])
        sl = int(context.args[3])
        tp = int(context.args[4])

        if action not in ["BUY", "SELL"]:
            await update.message.reply_text("❌ نوع معامله فقط باید `BUY` یا `SELL` باشد.", parse_mode="Markdown")
            return

    except ValueError:
        await update.message.reply_text(
            "❌ **خطا در مقادیر عددی!**\nلطفاً حجم، حد زیان (SL) و حد سود (TP) را به‌صورت عدد وارد کنید.",
            parse_mode="Markdown"
        )
        return

    # ارسال پیام در حال پردازش
    processing_msg = await update.message.reply_text("⏳ **در حال ارسال و اجرای سفارش در متاتریدر...**", parse_mode="Markdown")

    # اجرای معامله
    success, msg = execute_trade(symbol, action, lot, sl, tp)

    # قالب‌بندی خروجی بر اساس نتیجه معامله
    if success:
        result_text = (
            "🎯 **معامله با موفقیت ثبت شد**\n\n"
            f"📌 **نماد:** `{symbol}`\n"
            f"📊 **نوع سفارش:** `{action}`\n"
            f"پ **حجم (Lot):** `{lot}`\n"
            f"🛑 **حد زیان (SL):** `{sl} pips`\n"
            f"🎯 **حد سود (TP):** `{tp} pips`\n\n"
            f"💬 **پیام سیستم:** _{msg}_"
        )
    else:
        result_text = (
            "🚨 **خطا در اجرای معامله!**\n\n"
            f"📌 **نماد:** `{symbol}`\n"
            f"📊 **نوع سفارش:** `{action}`\n\n"
            f"❌ **علت خطا:** _{msg}_"
        )

    # ویرایش پیام قبلی با نتیجه نهایی
    await processing_msg.edit_text(result_text, parse_mode="Markdown")

async def positions_command(update, context):
    success, msg = get_open_positions()
    await update.message.reply_text(msg, parse_mode="Markdown")

@sync_to_async()
def get_watchlist():
    return list(Watchlist.objects.all())


async def show_watchlist_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    watchlist = await get_watchlist()
    if not watchlist:
        await update.message.reply_text("📭 واچ‌لیست شما خالی است!")
        return

    for watch in watchlist:
        # ساخت دکمه حذف اختصاصی برای هر نماد
        keyboard = [[
            InlineKeyboardButton(
                "❌ حذف از واچ‌لیست",
                callback_data=f"del_{watch.symbol}_{watch.time_frame}_{watch.market_type}"
            )
        ]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        msg = f"📌 **نماد:** `{watch.symbol}` | ⏳ `{watch.time_frame}` | 🏷 `{watch.market_type}`"
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=reply_markup)

# هندلر کلیک روی دکمه حذف
async def handle_delete_watchlist_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # تجزیه دیتای دکمه (del_BTCUSDT_15m_CRYPTO)
    _, symbol, timeframe, market_type = query.data.split("_")

    await remove_from_watchlist(symbol, timeframe, market_type)
    await query.edit_message_text(f"🗑 نماد `{symbol}` با موفقیت حذف شد.", parse_mode="Markdown")

@sync_to_async()
def save_watchlist(symbol,timeframe,market_type):
    is_watchlist_is_exists=Watchlist.objects.filter(
        symbol=symbol,
        time_frame=timeframe,
        market_type=market_type
    ).exists()

    if is_watchlist_is_exists:
        return None

    return Watchlist.objects.create(
        symbol=symbol,
        time_frame=timeframe,
        market_type=market_type
    )

# تعریف مراحل مکالمه (States)
ADD_SYMBOL, ADD_TIMEFRAME, ADD_MARKET = range(3)

# ------------------ ۱. شروع فرآیند افزودن به واچ‌لیست ------------------
async def start_add_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📝 **افزودن نماد به واچ‌لیست**\n\n"
        "لطفاً نام نماد مورد نظر را وارد کنید (مثلاً: `BTCUSDT` یا `EURUSD-ECN`):",
        parse_mode="Markdown"
    )
    return ADD_SYMBOL


# ------------------ ۲. دریافت نماد و انتخاب تایم‌فریم با دکمه ------------------
async def get_symbol_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['symbol'] = update.message.text.upper()

    # ساخت دکمه‌های شیشه‌ای برای تایم‌فریم
    keyboard = [
        [InlineKeyboardButton("5m", callback_data="5m"), InlineKeyboardButton("15m", callback_data="15m")],
        [InlineKeyboardButton("30m", callback_data="30m"), InlineKeyboardButton("1h", callback_data="1h")],
        [InlineKeyboardButton("4h", callback_data="4h"), InlineKeyboardButton("1d", callback_data="1d")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        f"📌 نماد: `{context.user_data['symbol']}`\n\n"
        "⏱ لطفاً **تایم‌فریم** مورد نظر را انتخاب کنید:",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )
    return ADD_TIMEFRAME


# ------------------ ۳. دریافت تایم‌فریم و انتخاب نوع بازار با دکمه ------------------
async def get_timeframe_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data['timeframe'] = query.data

    # ساخت دکمه‌های انتخاب نوع بازار
    keyboard = [
        [
            InlineKeyboardButton("🌐 کریپتو (Crypto)", callback_data="CRYPTO"),
            InlineKeyboardButton("📈 فارکس (Forex)", callback_data="FOREX")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        f"📌 نماد: `{context.user_data['symbol']}`\n"
        f"⏳ تایم‌فریم: `{context.user_data['timeframe']}`\n\n"
        "🏷 لطفاً **نوع بازار** را انتخاب کنید:",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )
    return ADD_MARKET


# ------------------ ۴. ثبت نهایی در دیتابیس و اجرا ------------------
async def get_market_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    symbol = context.user_data['symbol']
    timeframe = context.user_data['timeframe']
    market_type = query.data

    # ذخیره در دیتابیس
    await save_watchlist(symbol, timeframe, market_type)

    # اجرای تسک در پس‌زمینه
    asyncio.create_task(
        worker_loop(
            symbol=symbol,
            timeframe=timeframe,
            market_type=market_type,
            chat_id=update.effective_chat.id,
            bot=context.bot
        )
    )

    await query.edit_message_text(
        "✨ **آیتم با موفقیت به واچ‌لیست اضافه شد!**\n\n"
        f"📌 **نماد:** `{symbol}`\n"
        f"⏳ **تایم‌فریم:** `{timeframe}`\n"
        f"🏷 **بازار:** `{market_type}`\n\n"
        "✅ پایش لحظه‌ای RSI برای این نماد فعال شد.",
        parse_mode="Markdown"
    )
    return ConversationHandler.END

# ------------------ انصراف از فرآیند ------------------
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ عملیات لغو شد.")
    return ConversationHandler.END

@sync_to_async()
def remove_from_watchlist(symbol,timeframe,market_type):
    watchlist = Watchlist.objects.filter(
        symbol=symbol,
        time_frame=timeframe,
        market_type=market_type
    )

    if not watchlist.exists():
        return None

    return watchlist.delete()


async def worker_loop(symbol, timeframe, market_type, chat_id, bot):
    interval = TIMEFRAME_TO_SECONDS.get(timeframe, 1800)

    # پیام زیبای شروع کار لاپ برای این نماد
    print(f"🚀 [STARTED] پایش RSI برای نماد {symbol} | تایم‌فریم: {timeframe} | بازار: {market_type}")

    while True:
        try:
            print(f"🔍 [CHECKING] در حال تحلیل RSI برای {symbol} ({timeframe})...")

            rsi, status,divergence = await calculate_rsi(symbol, timeframe, market_type)

            if rsi is not None and (("NORMAL" not in status) or divergence != "بدون واگرایی"):
                # متن زیباسازی‌شده برای پیام تلگرام
                message_text = (
                    f"🚨 **هشدار سیگنال RSI**\n\n"
                    f"📌 **نماد:** `{symbol}`\n"
                    f"⏳ **تایم‌فریم:** `{timeframe}`\n"
                    f"🏷 **بازار:** `{market_type}`\n"
                    f"📊 **مقدار RSI:** `{rsi:.2f}`\n"
                    f"⚡️ **وضعیت:** `{status}`\n"
                    f"🔍 **واگرایی:** {divergence}\n\n"
                )

                await bot.send_message(
                    chat_id=chat_id,
                    text=message_text,
                    parse_mode="Markdown"
                )
                print(f"✅ [ALERT SENT] سیگنال {symbol} با موفقیت ارسال شد.")
            else:
                print(f"💤 [NORMAL] RSI برای {symbol} در محدوده عادی قرار دارد ({rsi}).")

        except Exception as e:
            print(f"❌ [ERROR] خطای پایش روی نماد {symbol}: {e}")

        await asyncio.sleep(interval)


# ۲. تابعی که پس از ساخت Application اجرا می‌شود
async def on_startup(app):
    print("\n" + "=" * 50)
    print("🚀 [STARTUP] ربات پایش بازار در حال راه‌اندازی است...")

    watchlist = await get_watchlist()
    total_items = len(watchlist)

    if not watchlist:
        print("⚠️ [WARNING] هیچ نمادی در واچ‌لیست یافت نشد!")
        print("=" * 50 + "\n")
        return

    print(f"📦 [WATCHLIST] تعداد {total_items} نماد با موفقیت بارگذاری شد.")
    print("🔄 [TASKS] در حال ساخت و اجرای فرآیندهای پس‌زمینه (Tasks)...")

    for item in watchlist:
        # ساخت task مجزا برای هر آیتم واچ‌لیست
        asyncio.create_task(
            worker_loop(
                symbol=item.symbol,
                timeframe=item.time_frame,
                market_type=item.market_type,
                chat_id=ADMIN_CHAT_ID,
                bot=app.bot
            )
        )
        print(f"   ├─ 📈 تسک فعال شد: {item.symbol:<10} | تایم‌فریم: {item.time_frame:<5} | بازار: {item.market_type}")

    print(f"✅ [SYSTEM] تمامی {total_items} تسک با موفقیت در پس‌زمینه شروع به کار کردند.")
    print("=" * 50 + "\n")

if __name__ == '__main__':
    print("\n" + "=" * 55)
    print("⚙️ [INIT] در حال پیکربندی و ساخت هسته ربات...")

    app = ApplicationBuilder().token(TOKEN).post_init(on_startup).build()

    # ------------------ دستورات عمومی (General) ------------------
    app.add_handler(CommandHandler("start", start))

    # ------------------ هشدارها (Alerts) ------------------
    app.add_handler(CommandHandler("alert", set_alert))
    app.add_handler(CommandHandler("falert", set_falert))

    # ------------------ مدیریت پوزیشن‌ها (Position Management) ------------------
    app.add_handler(CommandHandler("trade", trade_command))
    app.add_handler(CommandHandler("positions", positions_command))
    app.add_handler(CommandHandler("closeAll", close_all_command))

    # ------------------ واچ‌لیست (Watchlist) ------------------
    app.add_handler(CommandHandler("showWatchlist", show_watchlist_command))
    # ثبت کلیک روی دکمه حذف (تشخیص با Pattern الگوی دکمه)
    app.add_handler(CallbackQueryHandler(handle_delete_watchlist_callback, pattern="^del_"))
    # تعریف هندلر مرحله‌ای برای افزودن واچ‌لیست
    add_watchlist_handler = ConversationHandler(
        entry_points=[
            CommandHandler('addWatchlist', start_add_watchlist),
            MessageHandler(filters.Regex("^➕ افزودن به واچ‌لیست$"), start_add_watchlist)
        ],
        states={
            ADD_SYMBOL: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_symbol_step)],
            ADD_TIMEFRAME: [CallbackQueryHandler(get_timeframe_step)],
            ADD_MARKET: [CallbackQueryHandler(get_market_step)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    app.add_handler(add_watchlist_handler)
    # ------------------ هاندرهای دکمه‌های متنی (Keyboard Handlers) ------------------
    app.add_handler(MessageHandler(filters.Regex("^📋 واچ‌لیست$"), show_watchlist_command))
    app.add_handler(MessageHandler(filters.Regex("^📊 پوزیشن‌های باز$"), positions_command))
    app.add_handler(MessageHandler(filters.Regex("^❌ بستن همه پوزیشن‌ها$"), close_all_command))

    print("✅ [READY] پیکربندی دستورات و دکمه‌ها تکمیل شد.")
    print("🤖 [RUNNING] ربات روشن شد و آماده دریافت پیام است...")
    print("=" * 55 + "\n")

    app.run_polling()