import  os
import sys

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

TOKEN=config('TELEGRAM_BOT_TOKEN')

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_name=update.effective_user.first_name
    await update.message.reply_text(
        f"سلام {user_name} عزیز! به ربات دستیار ترید خوش اومدی.\n\n"
        f"برای ثبت هشدار کریپتو قیمت از فرمت زیر استفاده کن:\n"
        f"/alert BTCUSDT 65000\n"
        f"برای ثبت هشدار فارکس قیمت از فرمت زیر استفاده کن:\n"
        f"/falert XAUUSD 2100 \n"
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
        chat_id = str(update.effective_chat.id)
        symbol = context.args[0].upper()
        target_price = float(context.args[1])

        await save_alert_to_db(chat_id,symbol,target_price,False)

        await update.message.reply_text(f"✅ هشدار برای {symbol} روی قیمت {target_price} با موفقیت در دیتابیس ثبت شد.")
    except (IndexError, ValueError  ):
        await update.message.reply_text("❌ فرمت اشتباهه! مثال درست:\n/alert BTCUSDT 65000")

async def set_falert(update:Update,context:ContextTypes.DEFAULT_TYPE):
    try:
        chat_id = str(update.effective_chat.id)
        symbol = context.args[0].upper()
        target_price = float(context.args[1])

        await save_alert_to_db(chat_id, symbol, target_price, True)

        await update.message.reply_text(f"✅ هشدار برای {symbol} روی قیمت {target_price} با موفقیت در دیتابیس ثبت شد.")
    except (IndexError, ValueError):
        await update.message.reply_text("❌ فرمت اشتباهه! مثال درست:\n/falert XAUUSD 2100")

if __name__ == '__main__':
    app=ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("alert", set_alert))
    app.add_handler(CommandHandler('falert',set_falert))

    print("ربات روشن شد و آماده دریافت پیام است...")
    app.run_polling()