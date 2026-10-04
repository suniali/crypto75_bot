import os
import logging
from PIL import Image
from decimal import Decimal, InvalidOperation

from telegram import Update,InlineKeyboardButton,InlineKeyboardMarkup
from telegram.ext import ContextTypes,ConversationHandler

from bot_app.utils.keyboard_helper import MAIN_KEYBOARD,MAIN_MENU_TEXT

from bot_app.services.analysis_service import extract_trade_from_image
from bot_app.services.journal_service import (
    parse_trade_text,
    dict_to_formatted_text,
    create_trade_from_dict
)

logger = logging.getLogger(__name__)

WAITING_FOR_TRADE_IMAGE, CONFIRM_JOURNAL_DATA, EDITING_JOURNAL_DATA = range(100, 103)

# ==========================================
# ۱. گام اول: درخواست تصویر چارت
# ==========================================
async def start_extract_trade_wizard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """گام ۱: درخواست تصویر چارت همراه با دکمه شیشه‌ای انصراف"""
    cancel_inline_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ انصراف", callback_data="confirm_journal_no")]
    ])

    await update.message.reply_text(
        "📸 **لطفاً تصویر چارت یا پوزیشن معاملاتی خود را ارسال کنید:**\n\n"
        "اطلاعات معامله (قیمت‌ها، سواپ، کمیسیون و...) استخراج شده و پس از تأیید شما جهت ژورنال‌نویسی ثبت می‌شود.",
        parse_mode="Markdown",
        reply_markup=cancel_inline_keyboard
    )
    return WAITING_FOR_TRADE_IMAGE


# ==========================================
# ۲. گام دوم: پردازش تصویر و استخراج هوشمند
# ==========================================
async def process_trade_image_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """گام ۲: دریافت تصویر، فشرده‌سازی، OCR، پارس کامل و نمایش پیش‌نمایش مالی"""

    await update.message.chat.send_action(action="typing")
    status_msg = await update.message.reply_text("⏳ در حال دریافت و پردازش تصویر چارت...")

    temp_image_path = f"temp_trade_{update.effective_user.id}.jpg"

    try:
        # ۱. دانلود عکس ارسال‌شده
        photo = update.message.photo[-1]
        file = await photo.get_file()
        await file.download_to_drive(temp_image_path)

        # ۲. فشرده‌سازی و بهینه‌سازی تصویر جهت کاهش حجم ارسال به AI
        try:
            with Image.open(temp_image_path) as img:
                img.thumbnail((1024, 1024))
                img.save(temp_image_path, "JPEG", quality=85)
        except Exception as img_err:
            logger.warning("Image optimization warning: %s", img_err)

        await status_msg.edit_text("🤖 هوش مصنوعی در حال خواندن قیمت‌ها، سواپ، کمیسیون و نماد است...")

        # ۳. فراخوانی تابع استخراج متن از تصویر
        raw_extracted_text = await extract_trade_from_image(temp_image_path)

        # ۴. اعتبارسنجی اولیه پاسخ هوش مصنوعی
        if not raw_extracted_text or raw_extracted_text.startswith("⚠️") or "خطا" in raw_extracted_text:
            raise ValueError(raw_extracted_text or "پاسخ نامعتبر یا عدم تشخیص اطلاعات از هوش مصنوعی")

        # ۵. پارس هوشمند متن استخراج‌شده به دیکشنری کامل دیتابیس
        parsed_data = parse_trade_text(raw_extracted_text)

        if not parsed_data.get('symbol') or not parsed_data.get('entry_price'):
            raise ValueError("⚠️ نماد معاملاتی (SYMBOL) یا قیمت ورود (ENTRY) در تصویر تشخیص داده نشد.")

        # ذخیره ساختار پارس‌شده در context جهت استفاده در گام‌های بعدی (ویرایش/ثبت)
        context.user_data['temp_image_path'] = temp_image_path
        context.user_data['extracted_journal_data'] = parsed_data

        # ۶. محاسبه پیش‌نمایش سود/زیان خالص
        p = float(parsed_data.get('profit', 0))
        c = float(parsed_data.get('commission', 0))
        s = float(parsed_data.get('swap', 0))
        net_pnl = p + c + s

        # ساخت پیام پیش‌نمایش مالی جامع
        msg = (
            f"🔍 **اطلاعات استخراج‌شده از تصویر:**\n\n"
            f"📌 **نماد:** `{parsed_data.get('symbol')}` ({parsed_data.get('trade_type', 'BUY')})\n"
            f"📦 **حجم:** `{parsed_data.get('volume', '0.01')}` لات/واحد\n"
            f"🏁 **قیمت ورود:** `{parsed_data.get('entry_price')}`\n"
            f"🏁 **قیمت خروج:** `{parsed_data.get('exit_price', 'هنوز باز/تعیین نشده')}`\n"
            f"🛑 **SL:** `{parsed_data.get('stop_loss', '-')}` | 🎯 **TP:** `{parsed_data.get('take_profit', '-')}`\n\n"
            f"💵 **سود خام:** `${p:.2f}`\n"
            f"💸 **کمیسیون:** `${c:.2f}`\n"
            f"🔄 **سواپ:** `${s:.2f}`\n"
            f"📊 **سود خالص (Net PnL):** `${net_pnl:.2f}`\n\n"
            f"🏷️ **وضعیت:** `{parsed_data.get('result', 'PENDING')}`\n"
            f"📝 **توضیحات:** `{parsed_data.get('notes', '-')}`\n\n"
            f"آیا اطلاعات فوق مورد تأیید است؟"
        )

        confirm_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ تأیید و ثبت نهایی", callback_data="confirm_journal_yes")],
            [InlineKeyboardButton("✏️ ویرایش دستی", callback_data="edit_journal_manual")],
            [InlineKeyboardButton("❌ لغو", callback_data="confirm_journal_no")]
        ])

        await status_msg.delete()
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=confirm_keyboard)
        return CONFIRM_JOURNAL_DATA

    except Exception as e:
        logger.error("Error during trade extraction: %s", e)
        context.user_data.pop('extracted_journal_data', None)

        error_text = str(e) if str(e).startswith("⚠️") else "⚠️ خطا در پردازش و استخراج اطلاعات تصویر. لطفاً تصویر واضح‌تری ارسال کنید یا دستی وارد نمایید."

        try:
            await status_msg.delete()
        except Exception:
            pass

        await update.message.reply_text(
            f"{error_text}\n\nعملیات لغو شد.",
            reply_markup=MAIN_KEYBOARD
        )
        return ConversationHandler.END


# ==========================================
# 1. گام ویرایش دستی (نمایش فرمت متنی قابل کپی)
# ==========================================
async def start_manual_edit_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """گام ۳-ب: درخواست ارسال متن اصلاح‌شده از کاربر"""
    query = update.callback_query
    await query.answer()

    extracted_data = context.user_data.get('extracted_journal_data', {})

    # اگر داده از قبل دیکشنری بود، آن را به متن متناسب برای ویرایش تبدیل می‌کنیم
    if isinstance(extracted_data, dict):
        editable_text = dict_to_formatted_text(extracted_data)
    else:
        editable_text = str(extracted_data)

    await query.edit_message_text(
        "✏️ **حالت ویرایش دستی:**\n\n"
        "لطفاً متن زیر را کپی کرده، تغییرات لازم (قیمت، سواپ، کمیسیون، نماد و...) را روی آن اعمال کرده و سپس **یک پیام جدید** ارسال کنید:"
    )

    cancel_inline_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ انصراف", callback_data="confirm_journal_no")]
    ])

    # ارسال متن ساختاریافته قبلی در یک پیام مجزا جهت کپی‌برداری راحت
    await query.message.reply_text(
        f"```text\n{editable_text}\n```",
        parse_mode="Markdown",
        reply_markup=cancel_inline_keyboard
    )
    return EDITING_JOURNAL_DATA


# ==========================================
# 2. دریافت و پارس مجدد متن ویرایش‌شده
# ==========================================
async def save_manual_edit_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """گام ۳-ج: دریافت متن اصلاح‌شده کاربر، پارس مجدد و نمایش پیش‌نمایش تایید"""
    edited_text = update.message.text.strip() if update.message and update.message.text else ""

    if not edited_text:
        await update.message.reply_text("⚠️ متن ارسالی خالی است. لطفاً متن اصلاح‌شده را ارسال کنید.")
        return EDITING_JOURNAL_DATA

    # پارس مجدد متن اصلاح‌شده
    parsed_data = parse_trade_text(edited_text)

    if not parsed_data or not parsed_data.get('symbol') or not parsed_data.get('entry_price'):
        await update.message.reply_text(
            "⚠️ **فرمت متنی تشخیص داده نشد!**\n"
            "حداقل باید `SYMBOL` و `ENTRY` وارد شده باشند.\n\n"
            "مثال:\n"
            "`BTCUSDT BUY Entry: 64200 Exit: 65000 Profit: 150`\n\n"
            "لطفاً مجدداً متن اصلاح‌شده را بفرستید.",
            parse_mode="Markdown"
        )
        return EDITING_JOURNAL_DATA

    # به‌روزرسانی داده‌های پارس‌شده در context (بدون دستکاری temp_image_path)
    context.user_data['extracted_journal_data'] = parsed_data

    # محاسبه ایمن پیش‌نمایش سود خالص با Decimal (برای جلوگیری از خطای float)
    try:
        p = Decimal(str(parsed_data.get('profit', '0') or '0'))
        c = Decimal(str(parsed_data.get('commission', '0') or '0'))
        s = Decimal(str(parsed_data.get('swap', '0') or '0'))
        net_pnl = p + c + s
    except (InvalidOperation, TypeError, ValueError):
        p, c, s, net_pnl = Decimal('0'), Decimal('0'), Decimal('0'), Decimal('0')

    # بررسی اینکه آیا عکسی همراه این نشست بوده یا خیر
    has_image = bool(context.user_data.get('temp_image_path'))
    image_status = "🖼️ **تصویر چارت:** ضمیمه شده است" if has_image else "📝 **نوع ثبت:** ثبت متنی/دستی"

    msg = (
        f"✍️ **اطلاعات ویرایش‌شده توسط شما:**\n\n"
        f"📌 **نماد:** `{parsed_data.get('symbol')}` ({parsed_data.get('trade_type', 'BUY')})\n"
        f"📦 **حجم:** `{parsed_data.get('volume', '0.01')}` لات/واحد\n"
        f"🏁 **ورود:** `{parsed_data.get('entry_price')}` | **خروج:** `{parsed_data.get('exit_price', '-')}`\n"
        f"🛑 **SL:** `{parsed_data.get('stop_loss', '-')}` | 🎯 **TP:** `{parsed_data.get('take_profit', '-')}`\n\n"
        f"💵 **سود خام:** `${p:,.2f}` | 💸 **کمیسیون:** `${c:,.2f}` | 🔄 **سواپ:** `${s:,.2f}`\n"
        f"📊 **سود خالص:** `${net_pnl:,.2f}`\n"
        f"{image_status}\n\n"
        f"آیا اطلاعات جدید مورد تأیید است؟"
    )

    confirm_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ تأیید و ثبت نهایی", callback_data="confirm_journal_yes")],
        [InlineKeyboardButton("✏️ ویرایش مجدد", callback_data="edit_journal_manual")],
        [InlineKeyboardButton("❌ لغو", callback_data="confirm_journal_no")]
    ])

    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=confirm_keyboard)
    return CONFIRM_JOURNAL_DATA


# ==========================================
# 3. گام نهایی: ذخیره واقعی در دیتابیس
# ==========================================
async def confirm_journal_data_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """گام نهایی: فراخوانی لایه سرویس و ثبت معامله در دیتابیس"""
    query = update.callback_query
    await query.answer()

    trade_data = context.user_data.get('extracted_journal_data')
    image_path = context.user_data.get('temp_image_path')
    chat_id = query.message.chat_id

    if not trade_data or not isinstance(trade_data, dict):
        await query.edit_message_text("❌ اطلاعات معامله یافت نشد یا منقضی شده است.")
        return ConversationHandler.END

    try:
        # ۱. فراخوانی تابع سرویس دیتابیس
        trade_obj, result_msg = await create_trade_from_dict(chat_id, trade_data,image_path)

        if trade_obj:
            await query.edit_message_text("✅ *داده‌ها با موفقیت در دیتابیس ذخیره شدند.*", parse_mode="Markdown")

            final_msg = (
                f"🎉 **معامله جدید در ژورنال ثبت شد:**\n\n"
                f"🆔 **شناسه معامله:** `#{trade_obj.id}`\n"
                f"📌 **نماد:** `{trade_obj.symbol}` ({trade_obj.trade_type})\n"
                f"📊 **سود/زیان خالص:** `${trade_obj.net_profit:,.2f}`\n"
                f"🏷️ **وضعیت:** `{trade_obj.result}`\n\n"
                f"📌 {result_msg}"
            )

            await query.message.reply_text(final_msg, parse_mode="Markdown", reply_markup=MAIN_KEYBOARD)
            context.user_data.pop('extracted_journal_data', None)
            return ConversationHandler.END
        else:
            # در صورت بروز خطای اعتبارسنجی/دیتابیس
            await query.edit_message_text(
                f"❌ **خطا در ثبت معامله:**\n\n{result_msg}\n\nلطفاً مجدداً تلاش کنید.",
                parse_mode="Markdown"
            )
            return CONFIRM_JOURNAL_DATA

    finally:
        # تضمین پاک‌سازی فایل موقت تحت هر شرایطی
        if image_path and os.path.exists(image_path):
            try:
                os.remove(image_path)
            except Exception as clean_err:
                logger.warning(f"Failed to remove temp image file {image_path}: {clean_err}")

# ==========================================
# 4. لغو عملیات
# ==========================================
async def cancel_extract_image_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """لغو فرآیند و پاکسازی داده‌های موقت"""
    query = update.callback_query
    await query.answer()

    # پاکسازی فایل موقت تصویر در صورت انصراف
    image_path = context.user_data.pop('temp_image_path', None)
    if image_path and os.path.exists(image_path):
        os.remove(image_path)

    await query.edit_message_text("❌ عملیات ثبت معامله لغو شد.")
    await query.message.reply_text(MAIN_MENU_TEXT, reply_markup=MAIN_KEYBOARD)
    context.user_data.pop('extracted_journal_data', None)
    return ConversationHandler.END