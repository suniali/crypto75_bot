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

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler, ApplicationBuilder

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
    user_name=update.effective_user.first_name
    await update.message.reply_text(
        f"سلام {user_name} عزیز! به ربات دستیار ترید خوش اومدی.\n\n"
        f"برای ثبت هشدار کریپتو قیمت از فرمت زیر استفاده کن:\n"
        f"/alert btcusdt 65000\n"
        f"برای ثبت هشدار فارکس قیمت از فرمت زیر استفاده کن:\n"
        f"/falert xauusd-ecn 2100 \n"
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
        if len(context.args) < 2:
            await update.message.reply_text(
                "❌ ورودی ناپیوسته یا ناقص است!\nمثال درست:\n/alert btcusdt 65000"
            )
            return
        chat_id = str(update.effective_chat.id)
        symbol = context.args[0].upper()
        target_price = float(context.args[1])

        await save_alert_to_db(chat_id,symbol,target_price,False)

        await update.message.reply_text(f"✅ هشدار برای {symbol} روی قیمت {target_price} با موفقیت در دیتابیس ثبت شد.")
    except (IndexError, ValueError  ):
        await update.message.reply_text("❌ فرمت اشتباهه! مثال درست:\n/alert btcusdt 65000")

async def set_falert(update:Update,context:ContextTypes.DEFAULT_TYPE):
    try:
        if len(context.args) < 2:
            await update.message.reply_text(
                "❌ ورودی ناپیوسته یا ناقص است!\nمثال درست:\n/falert xauusd-ecn 2100"
            )
            return

        chat_id = str(update.effective_chat.id)
        symbol = context.args[0].upper()
        target_price = float(context.args[1])

        # Check if symbol is exists
        loop= asyncio.get_running_loop()
        res=await loop.run_in_executor(None,check_symbol_info,symbol)
        if res is True:
            await save_alert_to_db(chat_id, symbol, target_price, True)
            await update.message.reply_text(
                f"✅ هشدار برای {symbol} روی قیمت {target_price} با موفقیت در دیتابیس ثبت شد.")
        elif isinstance(res,list):
            sample_text = "\n".join(res)
            await update.message.reply_text(
                f"⚠️ نماد {symbol} در مارکت واچ یافت نشد یا فعال نشد.\n"
                f"نمونه نمادهای موجود در بروکر: \n{sample_text}"
            )
        else:
            # اگر خطایی در اتصال به MT5 پیش آمد (مقدار False برگشت)
            await update.message.reply_text("❌ خطا در اتصال به MetaTrader 5. لطفا مطمئن شوید برنامه MT5 باز است.")


    except (IndexError, ValueError):
        await update.message.reply_text("❌ فرمت اشتباهه! مثال درست:\n/falert xauusd-eur 2100")

async def close_all_command(update:Update,context:ContextTypes.DEFAULT_TYPE):
    msg = close_all_positions()
    await update.message.reply_text(msg)

async def trade_command(update:Update,context:ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 5:
        await update.message.reply_text(
            "❌ ورودی ناپیوسته یا ناقص است!\nمثال درست:\n/trade buy/sell symbol lot sl tp"
        )
        return

    action, symbol, lot ,sl ,tp= context.args[0].upper(), context.args[1].upper(), float(context.args[2]), int(context.args[3]), int(context.args[4])
    success, msg = execute_trade(symbol, action, lot, sl, tp)
    await update.message.reply_text(msg)

async def positions_command(update, context):
    success, msg = get_open_positions()
    await update.message.reply_text(msg, parse_mode="Markdown")

@sync_to_async()
def get_watchlist():
    return list(Watchlist.objects.all())

async def show_watchlist_command(update, context):
    watchlist = await get_watchlist()
    if not watchlist:
        await update.message.reply_text("واچ لیستی یافت نشد!")
        return

    msg='\n'.join([f"{watch.symbol} | {watch.time_frame}" for watch in watchlist])

    await update.message.reply_text(msg, parse_mode="Markdown")

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

async def add_to_watchlist_command(update, context):
    if len(context.args) < 3:
        await update.message.reply_text(
            "❌ ورودی ناپیوسته یا ناقص است!\nمثال درست:\n/addWatchlist btcusdt 15m crypto"
        )
        return

    symbol=context.args[0].upper()
    timeframe=context.args[1].lower()
    market_type=context.args[2].upper()

    await save_watchlist(symbol,timeframe,market_type)

    asyncio.create_task(
        worker_loop(
            symbol=symbol,
            timeframe=timeframe,
            market_type=market_type,
            chat_id=update.effective_chat.id,
            bot=context.bot
        )
    )

    await update.message.reply_text(
        f"✅ ارز {symbol} با موفقیت در دیتابیس ثبت شد.")

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

async def remove_from_watchlist_command(update, context):
    if len(context.args) < 3:
        await update.message.reply_text(
            "❌ ورودی ناپیوسته یا ناقص است!\nمثال درست:\n/removeWatchlist btcusdt 15m crypto"
        )
        return

    symbol = context.args[0].upper()
    timeframe = context.args[1].lower()
    market_type = context.args[2].upper()

    watchlist=await remove_from_watchlist(symbol,timeframe,market_type)
    if not watchlist:
        await update.message.reply_text(
            "❌ ارز در واچ لیست موجود نمیباشد!"
        )
        return

    await update.message.reply_text(
        f"✅ ارز {symbol} با موفقیت از دیتابیس حذف شد.")


async def worker_loop(symbol, timeframe, market_type, chat_id, bot):
    interval = TIMEFRAME_TO_SECONDS.get(timeframe, 1800)

    # پیام زیبای شروع کار لاپ برای این نماد
    print(f"🚀 [STARTED] پایش RSI برای نماد {symbol} | تایم‌فریم: {timeframe} | بازار: {market_type}")

    while True:
        try:
            print(f"🔍 [CHECKING] در حال تحلیل RSI برای {symbol} ({timeframe})...")

            rsi, status = await calculate_rsi(symbol, timeframe, market_type)

            if rsi is not None and status != "NORMAL":
                # متن زیباسازی‌شده برای پیام تلگرام
                message_text = (
                    f"🚨 **هشدار سیگنال RSI**\n\n"
                    f"📌 **نماد:** `{symbol}`\n"
                    f"⏳ **تایم‌فریم:** `{timeframe}`\n"
                    f"🏷 **بازار:** `{market_type}`\n"
                    f"📊 **مقدار RSI:** `{rsi:.2f}`\n"
                    f"⚡️ **وضعیت:** `{status}`\n\n"
                    f"⏰ _زمان ثبت: چند لحظه پیش_"
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
    app.add_handler(CommandHandler("addWatchlist", add_to_watchlist_command))
    app.add_handler(CommandHandler("removeWatchlist", remove_from_watchlist_command))

    print("✅ [READY] پیکربندی دستورات تکمیل شد.")
    print("🤖 [RUNNING] ربات روشن شد و آماده دریافت پیام است...")
    print("=" * 55 + "\n")

    app.run_polling()