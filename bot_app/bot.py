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

from bot_app.models import UserAlert
from bot_app.mt5_service import check_symbol_info,close_all_positions

TOKEN=config('TELEGRAM_BOT_TOKEN')

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

if __name__ == '__main__':
    app=ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("alert", set_alert))
    app.add_handler(CommandHandler('falert',set_falert))
    app.add_handler(CommandHandler('closeAll', close_all_command))

    print("ربات روشن شد و آماده دریافت پیام است...")
    app.run_polling()