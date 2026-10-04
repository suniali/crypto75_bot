from decimal import Decimal, InvalidOperation

from telegram import Update,InlineKeyboardButton,InlineKeyboardMarkup
from telegram.ext import ContextTypes

from bot_app.services.journal_service import parse_trade_text

(
    WAITING_FOR_TRADE_IMAGE,
    CONFIRM_JOURNAL_DATA,
    EDITING_JOURNAL_DATA,
    WAITING_FOR_MANUAL_TRADE_INPUT
) = range(100, 104)

async def start_manual_trade_wizard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """راهنمای کامل ثبت معامله متنی با فیلدهای مالی و زمان"""
    cancel_inline_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ انصراف", callback_data="confirm_journal_no")]
    ])

    sample_template = (
        "✍️ **اطلاعات معامله را وارد کنید:**\n\n"
        "می‌توانید فرمت کامل یا خلاصه‌شده زیر را ارسال کنید:\n\n"
        "```text\n"
        "SYMBOL: EURUSD\n"
        "TYPE: BUY\n"
        "VOLUME: 0.1\n"
        "ENTRY: 1.0850\n"
        "EXIT: 1.0910\n"
        "SL: 1.0820\n"
        "TP: 1.0920\n"
        "PROFIT: 60.00\n"
        "COMMISSION: -2.50\n"
        "SWAP: -0.0\n"
        "RESULT: WIN/LOSS/PENDING\n"
        "ENTRY_TIME: 2026-10-04 14:30\n"
        "EXIT_TIME: 2026-10-04 18:00\n"
        "NOTE: شکست خط ترند ۴ ساعته\n"
        "```"
    )

    await update.message.reply_text(sample_template, parse_mode="Markdown", reply_markup=cancel_inline_keyboard)
    return WAITING_FOR_MANUAL_TRADE_INPUT


async def process_manual_trade_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش تمامی جزییات مالی استخراج‌شده به کاربر قبل از ثبت نهایی"""
    user_text = update.message.text.strip() if update.message and update.message.text else ""
    if not user_text:
        await update.message.reply_text("⚠️ متن ارسالی خالی است. لطفاً جزئیات معامله را ارسال کنید.")
        return WAITING_FOR_MANUAL_TRADE_INPUT

    parsed_data = parse_trade_text(user_text)

    if not parsed_data or not parsed_data.get('symbol') or not parsed_data.get('entry_price'):
        await update.message.reply_text(
            "⚠️ **اطلاعات ناقص یا نامعتبر است!**\n"
            "حداقل باید `SYMBOL` و `ENTRY` مشخص باشند.\n\n"
            "💡 **نمونه فرمت ورودی صحیح:**\n"
            "`BTCUSDT BUY Entry: 64200 Exit: 65000 Profit: 150 SL: 63500 TP: 66000`",
            parse_mode="Markdown"
        )
        return WAITING_FOR_MANUAL_TRADE_INPUT

    # ذخیره داده‌های استخراج‌شده در context
    context.user_data['extracted_journal_data'] = parsed_data

    # محاسبه ایمن پیش‌نمایش سود خالص با Decimal
    try:
        p = Decimal(str(parsed_data.get('profit', '0') or '0'))
        c = Decimal(str(parsed_data.get('commission', '0') or '0'))
        s = Decimal(str(parsed_data.get('swap', '0') or '0'))
        net_pnl = p + c + s
    except (InvalidOperation, TypeError, ValueError):
        p, c, s, net_pnl = Decimal('0'), Decimal('0'), Decimal('0'), Decimal('0')

    # بررسی وضعیت وجود تصویر در نشست کاربر
    has_image = bool(context.user_data.get('temp_image_path'))
    image_status = "🖼️ **تصویر چارت:** ضمیمه شده است" if has_image else "📝 **نوع ثبت:** ثبت متنی/دستی"

    msg = (
        f"🔍 **پیش‌نمایش معامله استخراج‌شده:**\n\n"
        f"📌 **نماد:** `{parsed_data.get('symbol')}` ({parsed_data.get('trade_type', 'BUY')})\n"
        f"📦 **حجم:** `{parsed_data.get('volume', '0.01')}` لات/واحد\n"
        f"🏁 **قیمت ورود:** `{parsed_data.get('entry_price')}`\n"
        f"🏁 **قیمت خروج:** `{parsed_data.get('exit_price', 'هنوز باز/تعیین نشده')}`\n"
        f"🛑 **SL:** `{parsed_data.get('stop_loss', '-')}` | 🎯 **TP:** `{parsed_data.get('take_profit', '-')}`\n\n"
        f"💵 **سود خام:** `${p:,.2f}`\n"
        f"💸 **کمیسیون:** `${c:,.2f}`\n"
        f"🔄 **سواپ:** `${s:,.2f}`\n"
        f"📊 **سود خالص (Net PnL):** `${net_pnl:,.2f}`\n\n"
        f"🏷️ **وضعیت:** `{parsed_data.get('result', 'PENDING')}`\n"
        f"📝 **توضیحات:** `{parsed_data.get('notes', '-')}`\n"
        f"{image_status}\n\n"
        f"آیا اطلاعات فوق مورد تأیید است؟"
    )

    confirm_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ ثبت نهایی", callback_data="confirm_journal_yes")],
        [InlineKeyboardButton("✏️ اصلاح متن", callback_data="edit_journal_manual")],
        [InlineKeyboardButton("❌ انصراف", callback_data="confirm_journal_no")]
    ])

    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=confirm_keyboard)
    return CONFIRM_JOURNAL_DATA