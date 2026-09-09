import asyncio
import os
import sys
from pathlib import Path

import django
from decouple import config
from asgiref.sync import sync_to_async

# ------------------------------------------------------------------
# 1. Django Setup
# ------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

# ------------------------------------------------------------------
# 2. Imports & Configurations
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

from bot_app.analysis_service import calculate_rsi
from bot_app.models import UserAlert, Watchlist
from bot_app.mt5_service import (
    check_symbol_info,
    close_all_positions,
    close_position_by_ticket,
    execute_trade,
    get_open_positions,
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
# 3. Database Async Helpers
# ------------------------------------------------------------------
@sync_to_async
def save_alert_to_db(chat_id: str, symbol: str, target_price: float, is_forex: bool):
    market_type = "FOREX" if is_forex else "CRYPTO"
    exists = UserAlert.objects.filter(
        symbol=symbol, target_price=target_price, is_active=True
    ).exists()
    if exists:
        return None
    return UserAlert.objects.create(
        chat_id=chat_id, symbol=symbol, target_price=target_price, market_type=market_type
    )


@sync_to_async
def get_all_watchlist():
    return list(Watchlist.objects.all())


@sync_to_async
def save_watchlist_item(symbol: str, timeframe: str, market_type: str):
    obj, created = Watchlist.objects.get_or_create(
        symbol=symbol, time_frame=timeframe, market_type=market_type
    )
    return created


@sync_to_async
def delete_from_watchlist(symbol: str, timeframe: str, market_type: str):
    deleted_count, _ = Watchlist.objects.filter(
        symbol=symbol, time_frame=timeframe, market_type=market_type
    ).delete()
    return deleted_count > 0


# ------------------------------------------------------------------
# 4. Command Handlers
# ------------------------------------------------------------------
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
        "├ ➕ افزودن نماد: `/addWatchlist`\n\n"
        "💡 _برای استفاده سریع‌تر می‌توانید از دکمه‌های زیر استفاده کنید._"
    )

    main_keyboard = ReplyKeyboardMarkup(
        [["📋 واچ‌لیست", "➕ افزودن به واچ‌لیست"], ["📊 پوزیشن‌های باز"]],
        resize_keyboard=True
    )
    await update.message.reply_text(welcome_text, parse_mode="Markdown", reply_markup=main_keyboard)


async def handle_alert_creation(update: Update, context: ContextTypes.DEFAULT_TYPE, is_forex: bool):
    """تابع کمکی مشترک برای /alert و /falert"""
    cmd = "/falert" if is_forex else "/alert"
    example = "`/falert XAUUSD-ECN 2100`" if is_forex else "`/alert BTCUSDT 65000`"

    if len(context.args) < 2:
        await update.message.reply_text(
            f"⚠️ **فرمت دستور ناقص است!**\n\n📌 **فرمت:** `{cmd} <نماد> <قیمت_هدف>`\n💡 **مثال:** {example}",
            parse_mode="Markdown"
        )
        return

    try:
        symbol = context.args[0].upper()
        target_price = float(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ **قیمت هدف باید یک عدد معتبر باشد.**", parse_mode="Markdown")
        return

    status_msg = await update.message.reply_text(f"⏳ **در حال بررسی و ثبت هشدار `{symbol}`...**", parse_mode="Markdown")

    if is_forex:
        loop = asyncio.get_running_loop()
        res = await loop.run_in_executor(None, check_symbol_info, symbol)
        if res is not True:
            if isinstance(res, list):
                suggestions = "\n".join([f"▫️ `{s}`" for s in res[:10]])
                await status_msg.edit_text(f"⚠️ **نماد `{symbol}` یافت نشد!**\n\n💡 پیشنهادها:\n{suggestions}",
                                           parse_mode="Markdown")
            else:
                await status_msg.edit_text("🚨 **خطا در اتصال به MetaTrader 5!**", parse_mode="Markdown")
            return

    await save_alert_to_db(str(update.effective_chat.id), symbol, target_price, is_forex)
    await status_msg.edit_text(
        f"🔔 **هشدار قیمت ثبت شد**\n\n📌 **نماد:** `{symbol}`\n🎯 **هدف:** `{target_price:,.2f}`",
        parse_mode="Markdown"
    )


async def set_alert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await handle_alert_creation(update, context, is_forex=False)


async def set_falert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await handle_alert_creation(update, context, is_forex=True)


# ------------------------------------------------------------------
# 5. Position & Trade Handlers
# ------------------------------------------------------------------
async def trade_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 5:
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
        await update.message.reply_text("❌ **ورودی‌های عددی یا نوع معامله (BUY/SELL) نامعتبر است.**",
                                        parse_mode="Markdown")
        return

    msg = await update.message.reply_text("⏳ **در حال ارسال سفارش...**", parse_mode="Markdown")
    success, result_msg = execute_trade(symbol, action, lot, sl, tp)

    title = "🎯 **معامله با موفقیت ثبت شد**" if success else "🚨 **خطا در اجرای معامله!**"
    await msg.edit_text(f"{title}\n\n📌 **نماد:** `{symbol}`\n💬 `{result_msg}`", parse_mode="Markdown")


async def show_positions_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    success, data = get_open_positions()
    if not success:
        await update.message.reply_text("❌ خطا در دریافت اطلاعات از متاتریدر.")
        return
    if not data:
        await update.message.reply_text("📊 **هیچ پوزیشن بازی وجود ندارد.**", parse_mode="Markdown")
        return

    text_lines = ["📋 **لیست پوزیشن‌های فعال:**\n"]
    keyboard = []
    total_profit = 0.0

    for pos in data:
        trade_type = "🟢 BUY" if pos["type"] == "BUY" else "🔴 SELL"
        total_profit += pos["profit"]
        profit_emoji = "🟢" if pos["profit"] >= 0 else "🔴"

        text_lines.append(
            f"🔹 **{pos['symbol']}** | 🎫 `{pos['ticket']}`\n"
            f"├ 📊 {trade_type} | 📦 `{pos['volume']}` | 💵 ورود: `{pos['price_open']}`\n"
            f"└ 💵 سود/زیان: {profit_emoji} **`${pos['profit']:,.2f}`**\n"
            "───────────────"
        )
        keyboard.append([InlineKeyboardButton(f"❌ بستن {pos['symbol']} ({pos['ticket']})",
                                              callback_data=f"close_pos_{pos['ticket']}")])

    keyboard.append([InlineKeyboardButton("💥 بستن همه پوزیشن‌ها", callback_data="close_all_positions")])
    total_emoji = "🟩" if total_profit >= 0 else "🟥"
    text_lines.append(f"\n{total_emoji} **مجموع برآیند:** **`${total_profit:,.2f}`**")

    await update.message.reply_text("\n".join(text_lines), parse_mode="Markdown",
                                    reply_markup=InlineKeyboardMarkup(keyboard))


async def close_position_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    ticket = int(query.data.split("_")[2])
    await query.edit_message_text(f"⏳ در حال بستن پوزیشن `{ticket}`...", parse_mode="Markdown")

    loop = asyncio.get_running_loop()
    _, message = await loop.run_in_executor(None, close_position_by_ticket, ticket)
    await query.edit_message_text(message, parse_mode="Markdown")


async def close_all_positions_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        msg_target = query
    else:
        msg_target = await update.message.reply_text("⏳ در حال بستن تمامی پوزیشن‌ها...", parse_mode="Markdown")

    loop = asyncio.get_running_loop()
    res_msg = await loop.run_in_executor(None, close_all_positions)

    if update.callback_query:
        await msg_target.edit_message_text(res_msg, parse_mode="Markdown")
    else:
        await msg_target.edit_text(res_msg, parse_mode="Markdown")


# ------------------------------------------------------------------
# 6. Watchlist & Conversation Handlers
# ------------------------------------------------------------------
async def show_watchlist_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

    # متوقف ساختن تسک پایش مربوطه
    worker_key = f"{symbol}_{timeframe}_{market_type}"
    if worker_key in ACTIVE_WORKERS:
        ACTIVE_WORKERS[worker_key].cancel()
        del ACTIVE_WORKERS[worker_key]

    await delete_from_watchlist(symbol, timeframe, market_type)
    await query.edit_message_text(f"🗑 نماد `{symbol}` از واچ‌لیست حذف و پایش آن متوقف شد.", parse_mode="Markdown")


# Conversation steps
async def start_add_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📝 لطفاً نام نماد را وارد کنید (مثلاً `BTCUSDT`):", parse_mode="Markdown")
    return ADD_SYMBOL


async def get_symbol_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['symbol'] = update.message.text.upper()
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("5m", callback_data="5m"), InlineKeyboardButton("15m", callback_data="15m")],
        [InlineKeyboardButton("30m", callback_data="30m"), InlineKeyboardButton("1h", callback_data="1h")],
        [InlineKeyboardButton("4h", callback_data="4h"), InlineKeyboardButton("1d", callback_data="1d")]
    ])
    await update.message.reply_text(f"📌 نماد: `{context.user_data['symbol']}`\n⏱ تایم‌فریم را انتخاب کنید:",
                                    parse_mode="Markdown", reply_markup=keyboard)
    return ADD_TIMEFRAME


async def get_timeframe_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data['timeframe'] = query.data
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("🌐 کریپتو", callback_data="CRYPTO"),
        InlineKeyboardButton("📈 فارکس", callback_data="FOREX")
    ]])
    await query.edit_message_text("🏷 بازار را انتخاب کنید:", reply_markup=keyboard)
    return ADD_MARKET


async def get_market_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    symbol = context.user_data['symbol']
    timeframe = context.user_data['timeframe']
    market_type = query.data

    await save_watchlist_item(symbol, timeframe, market_type)

    # شروع تسک جدید
    worker_key = f"{symbol}_{timeframe}_{market_type}"
    task = asyncio.create_task(worker_loop(symbol, timeframe, market_type, update.effective_chat.id, context.bot))
    ACTIVE_WORKERS[worker_key] = task

    await query.edit_message_text(f"✨ `{symbol}` به واچ‌لیست اضافه شد و پایش RSI فعال گردید.", parse_mode="Markdown")
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ عملیات لغو شد.")
    return ConversationHandler.END


# ------------------------------------------------------------------
# 7. Background Worker Loop
# ------------------------------------------------------------------
async def worker_loop(symbol: str, timeframe: str, market_type: str, chat_id: int, bot):
    interval = TIMEFRAME_TO_SECONDS.get(timeframe, 1800)
    print(f"🚀 [STARTED] پایش RSI: {symbol} | {timeframe} | {market_type}")

    try:
        while True:
            try:
                rsi, status, divergence = await calculate_rsi(symbol, timeframe, market_type)
                if rsi is not None and ("NORMAL" not in status or divergence != "بدون واگرایی"):
                    msg = (
                        f"🚨 **هشدار سیگنال RSI**\n\n"
                        f"📌 **نماد:** `{symbol}` | ⏳ `{timeframe}`\n"
                        f"📊 **RSI:** `{rsi:.2f}` | ⚡️ **وضعیت:** `{status}`\n"
                        f"🔍 **واگرایی:** {divergence}"
                    )
                    await bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown")
            except Exception as e:
                print(f"❌ [ERROR] پایش روی {symbol}: {e}")

            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        print(f"🛑 [STOPPED] پایش {symbol} متوقف شد.")


# ------------------------------------------------------------------
# 8. Application Startup & Main Execution
# ------------------------------------------------------------------
async def on_startup(app):
    print("\n" + "=" * 50 + "\n🚀 [STARTUP] راه‌اندازی ربات...")
    watchlist = await get_all_watchlist()

    for item in watchlist:
        worker_key = f"{item.symbol}_{item.time_frame}_{item.market_type}"
        task = asyncio.create_task(
            worker_loop(item.symbol, item.time_frame, item.market_type, ADMIN_CHAT_ID, app.bot)
        )
        ACTIVE_WORKERS[worker_key] = task

    print(f"✅ [SYSTEM] {len(watchlist)} تسک در پس‌زمینه فعال شدند.\n" + "=" * 50)


if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).post_init(on_startup).build()

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("alert", set_alert))
    app.add_handler(CommandHandler("falert", set_falert))
    app.add_handler(CommandHandler("trade", trade_command))
    app.add_handler(CommandHandler("positions", show_positions_handler))
    app.add_handler(CommandHandler("closeAll", close_all_positions_handler))
    app.add_handler(CommandHandler("showWatchlist", show_watchlist_command))

    # Callbacks
    app.add_handler(CallbackQueryHandler(close_position_callback, pattern="^close_pos_"))
    app.add_handler(CallbackQueryHandler(close_all_positions_handler, pattern="^close_all_positions"))
    app.add_handler(CallbackQueryHandler(handle_delete_watchlist_callback, pattern="^del_"))

    # Conversation
    add_watchlist_handler = ConversationHandler(
        entry_points=[
            CommandHandler("addWatchlist", start_add_watchlist),
            MessageHandler(filters.Regex("^➕ افزودن به واچ‌لیست$"), start_add_watchlist),
        ],
        states={
            ADD_SYMBOL: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_symbol_step)],
            ADD_TIMEFRAME: [CallbackQueryHandler(get_timeframe_step)],
            ADD_MARKET: [CallbackQueryHandler(get_market_step)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(add_watchlist_handler)

    # Keyboard Handlers
    app.add_handler(MessageHandler(filters.Regex("^📋 واچ‌لیست$"), show_watchlist_command))
    app.add_handler(MessageHandler(filters.Regex("^📊 پوزیشن‌های باز$"), show_positions_handler))

    print("🤖 [RUNNING] ربات روشن شد...")
    app.run_polling()