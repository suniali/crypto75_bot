import os
import asyncio

from asgiref.sync import sync_to_async

from telegram import Update,InlineKeyboardButton,InlineKeyboardMarkup
from telegram.ext import ContextTypes,ConversationHandler

from bot_app.utils.keyboard_helper import MAIN_KEYBOARD

from bot_app.services.analysis_service import analyze_trades_with_gemini,calculate_trading_metrics
from bot_app.services.report_service import generate_pdf_report

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
        "rep_1": (1, "daily"),
        "rep_7": (7, "weekly"),
        "rep_30": (30, "monthly"),
        "rep_180": (180, "6months")
    }

    days, period_name = days_map.get(query.data, (7, "weekly"))

    await query.edit_message_text(
        f"⏳ **در حال استخراج معاملات و تحلیل هوش مصنوعی برای بازه {period_name}...**\nلطفاً چند لحظه شکیبا باشید.")


   # استفاده از sync_to_async روی تابع واسطه همگام
    metrics = await sync_to_async(calculate_trading_metrics, thread_sensitive=True)(period_name)

    # بقیه مراحل بدون مشکل و ارور اجرا می‌شوند:
    ai_analysis = await asyncio.to_thread(analyze_trades_with_gemini, metrics, period_name)

    # ۳. تولید فایل PDF
    pdf_filename = f"Trade_Report_{update.effective_user.id}_{days}d.pdf"
    await asyncio.to_thread(generate_pdf_report, pdf_filename, period_name, metrics, ai_analysis)

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