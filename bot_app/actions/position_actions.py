import asyncio
import logging

from telegram import Update,InlineKeyboardButton,InlineKeyboardMarkup
from telegram.ext import ContextTypes,ConversationHandler
from telegram.error import BadRequest

from bot_app.utils.keyboard_helper import MAIN_KEYBOARD,MAIN_MENU_TEXT

from bot_app.services.mt5_service import (
    get_open_positions,
    get_market_watch_symbols,
    execute_trade,
    update_position_sltp,
    set_break_even,
    close_position,
    close_position_by_ticket,
    close_all_positions,
)

logger = logging.getLogger(__name__)

async def auto_refresh_positions_job(context: ContextTypes.DEFAULT_TYPE):
    """آپدیت خودکار پیام لیست پوزیشن‌ها هر چند ثانیه یک‌بار"""
    job = context.job
    chat_id = job.chat_id
    job_data = job.data or {}
    message_id = job_data.get("message_id")

    if not message_id:
        logger.warning("auto_refresh_positions_job: No message_id provided for chat_id %s. Removing job.", chat_id)
        job.schedule_removal()
        return

    try:
        try:
            # ۱. دریافت جدیدترین لیست پوزیشن‌ها از متاتریدر در Executor
            success, positions = await asyncio.to_thread(get_open_positions)
        except Exception as e:
            logger.error(f"Error fetching positions in background job: {e}")
            return

        # اگر پوزیشنی وجود نداشت یا خطا رخ داد
        if not success or not positions:
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text="📭 <b>در حال حاضر هیچ پوزیشن بازی وجود ندارد.</b>",
                    parse_mode="HTML",
                )
            except BadRequest as e:
                if "message is not modified" not in str(e).lower():
                    logger.warning(f"Failed to edit empty message: {e}")
                    job.schedule_removal()
            except Exception as e:
                logger.error(f"Unexpected error when clearing message: {e}")
                job.schedule_removal()

            job.schedule_removal()  # توقف تایمر
            return

        # ساخت متن جدید با فرمت HTML
        text = "🔄 <b>لیست پوزیشن‌های فعال (بروزرسانی زنده):</b>\n\n"
        total_profit = 0.0
        keyboard = []

        for pos in positions:
            profit = pos.get("profit", 0.0)
            total_profit += profit
            profit_icon = "🟢" if profit >= 0 else "🔴"

            symbol = pos.get('symbol', 'N/A')
            pos_type = pos.get('type', 'N/A')
            ticket = pos.get('ticket', '')
            volume = pos.get('volume', 0)
            price_open = pos.get('price_open', 0)
            price_current = pos.get('price_current', 0)

            text += (
                f"🔹 <b>تیکت:</b> <code>{ticket}</code> | <b>{symbol}</b> ({pos_type})\n"
                f"📊 <b>حجم:</b> <code>{volume}</code> | <b>ورود:</b> <code>{price_open}</code>\n"
                f"📈 <b>قیمت فعلی:</b> <code>{price_current:.5f}</code>\n"
                f"{profit_icon} <b>سود/ضرر:</b> <code>{profit:.2f}$</code>\n"
                f"➖➖➖➖➖➖➖➖➖➖\n"
            )

            keyboard.append([
                InlineKeyboardButton(
                    f"⚙️ مدیریت پوزیشن {ticket}",
                    callback_data=f"pos_detail_{ticket}"
                )
            ])

        text += f"\n💰 <b>مجموع سود/ضرر کل:</b> <code>{total_profit:.2f}$</code>"

        # دکمه‌های کنترلی
        keyboard.append([InlineKeyboardButton("💥 بستن همه پوزیشن‌ها", callback_data="close_all_positions")])

        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML",
            )
            logger.debug("Successfully updated positions message for chat_id %s", chat_id)
        except BadRequest as e:
            err_msg = str(e).lower()
            if "message is not modified" in err_msg:
                logger.debug("Message not modified for chat_id %s (prices unchanged).", chat_id)
            else:
                logger.warning("Stopping live_pos_job for chat_id %s due to BadRequest: %s", chat_id, e)
                job.schedule_removal()
        except Exception as e:
            logger.error("Error editing positions message for chat_id %s: %s", chat_id, e, exc_info=True)
            job.schedule_removal()
    except Exception as e:
        logger.critical(
            "Unhandled fatal exception in auto_refresh_positions_job for chat_id %s: %s",
            chat_id, e,
            exc_info=True
        )

async def auto_refresh_single_position_job(context: ContextTypes.DEFAULT_TYPE):
    """آپدیت خودکار جزییات یک پوزیشن خاص هر چند ثانیه یک‌بار"""
    job = context.job
    chat_id = job.chat_id
    job_data = job.data or {}
    message_id = job_data.get("message_id")
    ticket = job_data.get("ticket")

    if not message_id or not ticket:
        logger.warning("auto_refresh_single_position_job: Missing message_id or ticket for chat_id %s. Removing job.",
                       chat_id)
        job.schedule_removal()
        return

    try:
        try:
            success, positions = await asyncio.to_thread(get_open_positions)
        except Exception as e:
            logger.error("Error executing get_open_positions in executor for ticket %s: %s", ticket, e, exc_info=True)
            return

        pos = next((p for p in positions if p["ticket"] == ticket), None) if success and positions else None

        # اگر پوزیشن بسته شده باشد، اطلاع بده و تایمر را متوقف کن
        if not pos:
            logger.info("Position ticket %s closed or not found for chat_id %s. Removing job.", ticket, chat_id)
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=f"❌ <b>پوزیشن <code>{ticket}</code> بسته شده است یا یافت نشد.</b>",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت به لیست", callback_data="refresh_positions_list")]]),
                    parse_mode="HTML",
                )
            except Exception as e:
                logger.warning("Failed to notify user about closed position ticket %s: %s", ticket, e)

            job.schedule_removal()
            return

        trade_type = "🟢 BUY" if pos["type"] == "BUY" else "🔴 SELL"
        profit_emoji = "🟢" if pos["profit"] >= 0 else "🔴"

        caption = (
            f"⚙️ <b>مدیریت پوزیشن <code>{pos['symbol']}</code></b> (🎫 <code>{pos['ticket']}</code>)\n\n"
            f"🔹 <b>نوع:</b> {trade_type} | 📦 <b>حجم:</b> <code>{pos['volume']}</code> لات\n"
            f"💵 <b>قیمت ورود:</b> <code>{pos['price_open']}</code>\n"
            f"📈 <b>قیمت لحظه‌ای:</b> <code>{pos['price_current']:.5f}</code>\n"
            f"🛑 <b>SL:</b> <code>{pos['sl']}</code> | 🎯 <b>TP:</b> <code>{pos['tp']}</code>\n"
            f"───────────────────\n"
            f"📊 <b>سود/زیان لحظه‌ای:</b> {profit_emoji} <b><code>${pos['profit']:,.2f}</code></b>"
        )

        keyboard = [
            [
                InlineKeyboardButton("🛡 فری‌ریسک (Break-Even)", callback_data=f"action_be_{ticket}"),
                InlineKeyboardButton("✂️ خروج 25%", callback_data=f"action_close25_{ticket}"),
                InlineKeyboardButton("✂️ خروج 50%", callback_data=f"action_close50_{ticket}"),
            ],
            [
                InlineKeyboardButton("⚙️ تغییر SL / TP", callback_data=f"action_editsltp_{ticket}"),
                InlineKeyboardButton("📉 خروج جزئی دلخواه", callback_data=f"action_partial_{ticket}"),
            ],
            [
                InlineKeyboardButton("❌ بستن کامل پوزیشن", callback_data=f"close_pos_{ticket}"),
            ],
            [
                InlineKeyboardButton("🔙 بازگشت به لیست", callback_data="refresh_positions_list"),
            ],
        ]

        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=caption,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML",
            )
            logger.debug("Successfully updated single position message for ticket %s", ticket)
        except BadRequest as e:
            err_msg = str(e).lower()
            if "message is not modified" in err_msg:
                logger.debug("Single position message not modified for ticket %s.", ticket)
            else:
                logger.info("Stopping live_single_pos job for ticket %s due to UI transition or BadRequest: %s", ticket, e)
                job.schedule_removal()
        except Exception as e:
            logger.error("Error updating single position message for ticket %s: %s", ticket, e, exc_info=True)
            job.schedule_removal()

    except Exception as e:
        logger.critical(
            "Unhandled fatal exception in auto_refresh_single_position_job for ticket %s: %s",
            ticket, e,
            exc_info=True
        )


async def show_positions_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش لیست پوزیشن‌های باز و فعال‌سازی آپدیت زنده (Live Update)"""
    chat_id = update.effective_chat.id
    query = update.callback_query

    if query:
        await query.answer()

    # ۱. متوقف کردن تمامی تایمرهای قبلی (لیست کلی و تک پوزیشن)
    await stop_all_live_jobs(chat_id,context)


    # ۲. دریافت پوزیشن‌ها از متاتریدر
    success, positions = await asyncio.to_thread(get_open_positions)

    if not success:
        error_msg = positions if isinstance(positions, str) else "❌ **خطا در دریافت پوزیشن‌ها**"
        if query:
            await query.edit_message_text(error_msg, parse_mode="Markdown")
        else:
            await update.message.reply_text(error_msg, parse_mode="Markdown")
        return

    # ۳. بررسی خالی بودن لیست پوزیشن‌ها
    if not positions:
        text = "📭 **در حال حاضر هیچ پوزیشن بازی وجود ندارد.**"
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔄 بروزرسانی مجدد", callback_data="refresh_positions_list")]])

        if query:
            await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
        else:
            await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")
        return

    # ۴. ساخت متن خروجی و محاسبه سود/ضرر کل
    text = "🔄 **لیست پوزیشن‌های فعال (بروزرسانی زنده):**\n\n"
    total_profit = 0.0

    keyboard = []
    for pos in positions:
        profit = pos["profit"]
        total_profit += profit
        profit_icon = "🟢" if profit >= 0 else "🔴"

        text += (
            f"🔹 **تیکت:** `{pos['ticket']}` | **{pos['symbol']}** ({pos['type']})\n"
            f"📊 **حجم:** `{pos['volume']}` | **ورود:** `{pos['price_open']:.5f}`\n"
            f"📈 **قیمت فعلی:** `{pos['price_current']:.5f}`\n"
            f"{profit_icon} **سود/ضرر:** `{profit}$`\n"
            f"➖➖➖➖➖➖➖➖➖➖\n"
        )

        # اضافه کردن دکمه مدیریت اختصاصی برای هر پوزیشن
        keyboard.append(
            [InlineKeyboardButton(f"⚙️ مدیریت پوزیشن {pos['ticket']}", callback_data=f"pos_detail_{pos['ticket']}")])

    text += f"\n💰 **مجموع سود/ضرر کل:** `{round(total_profit, 2)}$`"

    # اضافه کردن دکمه‌های کنترلی اصلی
    keyboard.append([InlineKeyboardButton("💥 بستن همه پوزیشن‌ها", callback_data="close_all_positions")])

    reply_markup = InlineKeyboardMarkup(keyboard)

    # ۵. ارسال یا ویرایش پیام
    if query:
        msg = await query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")
        message_id = msg.message_id
    else:
        msg = await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")
        message_id = msg.message_id

    # ۶. تنظیم و شروع تایمر آپدیت زنده (هر ۳ ثانیه یک‌بار)
    if context.job_queue:
        context.job_queue.run_repeating(
            auto_refresh_positions_job,
            interval=3,  # بازه زمانی بروزرسانی (برحسب ثانیه)
            first=3,
            chat_id=chat_id,
            data={"message_id": message_id},
            name=f"live_pos_{chat_id}",
        )


async def stop_all_live_jobs(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    """تابع کمکی برای حذف تمامی تایمرهای مربوط به یک چت"""
    if context.job_queue:
        for job_name in [f"live_pos_{chat_id}", f"live_single_pos_{chat_id}"]:
            for job in context.job_queue.get_jobs_by_name(job_name):
                job.schedule_removal()
                logger.info("Stopped job %s for chat %s", job_name, chat_id)


def build_positions_keyboard(data):
    """ساخت کیبورد لیست پوزیشن‌ها"""
    keyboard = []
    for pos in data:
        p_emoji = "🟢" if pos['profit'] >= 0 else "🔴"
        btn_text = f"{p_emoji} {pos['symbol']} | {pos['type']} {pos['volume']}L | ${pos['profit']:,.2f}"
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"pos_detail_{pos['ticket']}")])

    keyboard.append([InlineKeyboardButton("💥 بستن همه پوزیشن‌ها", callback_data="close_all_positions$")])
    keyboard.append([InlineKeyboardButton("🔄 بروزرسانی لیست", callback_data="refresh_positions_list")])
    return InlineKeyboardMarkup(keyboard)

async def position_detail_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    chat_id = update.effective_chat.id
    ticket = int(query.data.split("_")[2])

    # ۱. توقف حتمی تایمرهای قبلی
    await stop_all_live_jobs(chat_id, context)

    # ۲. دریافت پوزیشن از متاتریدر به صورت Non-blocking
    success, positions = await asyncio.to_thread( get_open_positions)

    pos = next((p for p in positions if p["ticket"] == ticket), None) if success and positions else None

    if not pos:
        await query.edit_message_text(
            "❌ **این پوزیشن یافت نشد یا قبلاً بسته شده است.**",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت به لیست", callback_data="refresh_positions_list")]]),
            parse_mode="Markdown",
        )
        return

    trade_type = "🟢 BUY" if pos["type"] == "BUY" else "🔴 SELL"
    profit_emoji = "🟢" if pos["profit"] >= 0 else "🔴"

    caption = (
        f"⚙️ **مدیریت پوزیشن `{pos['symbol']}`** (🎫 `{pos['ticket']}`)\n\n"
        f"🔹 **نوع:** {trade_type} | 📦 **حجم:** `{pos['volume']}` لات\n"
        f"💵 **قیمت ورود:** `{pos['price_open']:.5f}`\n"
        f"📈 **قیمت لحظه‌ای:** `{pos['price_current']:.5f}`\n"
        f"🛑 **SL:** `{pos['sl']}` | 🎯 **TP:** `{pos['tp']}`\n"
        f"───────────────────\n"
        f"📊 **سود/زیان لحظه‌ای:** {profit_emoji} **`${pos['profit']:,.2f}`**"
    )

    # دکمه بروزرسانی دستی حذف شد چون لایو اضافه گردیده است
    keyboard = [
        [
            InlineKeyboardButton("🛡 فری‌ریسک (Break-Even)", callback_data=f"action_be_{ticket}"),
            InlineKeyboardButton("✂️ خروج 25%", callback_data=f"action_close25_{ticket}"),
            InlineKeyboardButton("✂️ خروج 50%", callback_data=f"action_close50_{ticket}"),

        ],
        [
            InlineKeyboardButton("⚙️ تغییر SL / TP", callback_data=f"action_editsltp_{ticket}"),
            InlineKeyboardButton("📉 خروج جزئی دلخواه", callback_data=f"action_partial_{ticket}"),
        ],
        [
            InlineKeyboardButton("❌ بستن کامل پوزیشن", callback_data=f"close_pos_{ticket}"),
        ],
        [
            InlineKeyboardButton("🔙 بازگشت به لیست", callback_data="refresh_positions_list"),
        ],
    ]

    msg = await query.edit_message_text(caption, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    # ۳. راه اندازی آپدیت لایو اختصاصی برای این تک پوزیشن (هر ۳ ثانیه)
    if context.job_queue:
        context.job_queue.run_repeating(
            auto_refresh_single_position_job,
            interval=3,
            first=3,
            chat_id=chat_id,
            data={"message_id": msg.message_id, "ticket": ticket},
            name=f"live_single_pos_{chat_id}",
        )

async def cancel_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("عملیات لغو شد.")

    # پاکسازی داده‌های موقت اکشن از context
    context.user_data.pop("action_ticket", None)
    context.user_data.pop("action_type", None)
    context.user_data.pop("target_ticket", None)
    context.user_data.pop("exit_price", None)

    data_parts = query.data.split("_")
    ticket = int(data_parts[2]) if len(data_parts) == 3 else None

    # ویرایش پیام و بازگشت به جزئیات پوزیشن یا لیست اصلی
    if ticket:
        # فراخوانی مجدد نمایش جزئیات پوزیشن (یا هدایت کاربر به تابع position_detail_callback)
        await position_detail_callback(update, context)
    else:
        await query.edit_message_text(
            "❌ **عملیات لغو شد.**",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 بازگشت به لیست پوزیشن‌ها", callback_data="refresh_positions_list")]
            ]),
            parse_mode="Markdown"
        )

    # ⚠️ بسیار مهم: پایان دادن به وضعیت ConversationHandler
    return ConversationHandler.END

async def handle_position_actions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data_parts = query.data.split("_")
    chat_id = update.effective_chat.id

    # ۱. توقف تایمرها
    await stop_all_live_jobs(chat_id, context)

    # ۲. استخراج اکشن و تیکت
    if len(data_parts) >= 3 and data_parts[0] == "action":
        action = data_parts[1]
        ticket = int(data_parts[2])
    elif len(data_parts) == 3 and data_parts[0] == "close" and data_parts[1] == "pos":
        action = "close"
        ticket = int(data_parts[2])
    else:
        action = data_parts[1]
        ticket = int(data_parts[2])

    # ------------------ ۱. بستن کامل پوزیشن ------------------
    if action in ["close", "closepos"]:
        confirm_keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ بله، کاملاً مطمئنم", callback_data=f"action_confirmclose_{ticket}"),
                InlineKeyboardButton("❌ انصراف", callback_data=f"pos_detail_{ticket}")
            ]
        ])
        await query.edit_message_text(
            f"⚠️ **هشدار بستن پوزیشن `{ticket}`**\n\nآیا از بستن کامل این پوزیشن اطمینان دارید؟",
            reply_markup=confirm_keyboard,
            parse_mode="Markdown"
        )
        return ConversationHandler.END

    elif action == "confirmclose":
        await query.edit_message_text(f"⏳ در حال بستن کامل پوزیشن `{ticket}`...", parse_mode="Markdown")
        success, msg = await asyncio.to_thread(close_position, ticket)
        back_keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔙 بازگشت به لیست پوزیشن‌ها", callback_data="refresh_positions_list")]])
        await query.edit_message_text(f"{msg}", reply_markup=back_keyboard, parse_mode="Markdown")
        return ConversationHandler.END

    # ------------------ ۲. فری‌ریسک (Break-Even) ------------------
    elif action == "be":
        confirm_keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ بله، فری‌ریسک شود", callback_data=f"action_confirmbe_{ticket}"),
                InlineKeyboardButton("❌ انصراف", callback_data=f"pos_detail_{ticket}")
            ]
        ])
        await query.edit_message_text(
            f"🛡 **تأییدیه فری‌ریسک (Break-Even) پوزیشن `{ticket}`**\n\nآیا مطمئن هستید که می‌خواهید حد ضرر به نقطه ورود منتقل شود؟",
            reply_markup=confirm_keyboard,
            parse_mode="Markdown"
        )
        return ConversationHandler.END

    elif action == "confirmbe":
        await query.edit_message_text(f"⏳ در حال انتقال حد ضرر پوزیشن `{ticket}` به نقطه ورود...", parse_mode="Markdown")
        _, msg = await asyncio.to_thread(set_break_even, ticket)
        await query.edit_message_text(
            f"{msg}",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 بازگشت به لیست", callback_data="refresh_positions_list")]]),
            parse_mode="Markdown"
        )
        return ConversationHandler.END

    # ------------------ ۳. خروج ۲۵٪ حجم ------------------
    elif action == "close25":
        success, positions = await asyncio.to_thread(get_open_positions)
        pos = next((p for p in positions if p['ticket'] == ticket), None) if success and positions else None

        if not pos:
            await query.edit_message_text("❌ پوزیشن یافت نشد یا قبلاً بسته شده است.", parse_mode="Markdown")
            return ConversationHandler.END

        sm_vol = round(pos['volume'] / 3, 2)  # فرمول اصلی شما برگردانده شد
        if sm_vol < 0.01:
            await query.edit_message_text("⚠️ **حجم پوزیشن برای خروج ۲۵٪ بسیار کوچک است (کمتر از 0.01).**", parse_mode="Markdown")
            return ConversationHandler.END

        confirm_keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ بله، خروج ۲۵٪", callback_data=f"action_confirmclose25_{ticket}"),
                InlineKeyboardButton("❌ انصراف", callback_data=f"pos_detail_{ticket}")
            ]
        ])
        await query.edit_message_text(
            f"✂️ **تأییدیه خروج ۲۵٪ از پوزیشن `{ticket}`**\n\n"
            f"حجم کل: `{pos['volume']}` لات\nحجم خروج: `{sm_vol}` لات\n\nآیا از بستن این میزان حجم اطمینان دارید؟",
            reply_markup=confirm_keyboard,
            parse_mode="Markdown"
        )
        return ConversationHandler.END

    elif action == "confirmclose25":
        success, positions = await asyncio.to_thread(get_open_positions)
        pos = next((p for p in positions if p['ticket'] == ticket), None) if success and positions else None

        if not pos:
            await query.edit_message_text("❌ پوزیشن یافت نشد یا قبلاً بسته شده است.", parse_mode="Markdown")
            return ConversationHandler.END

        sm_vol = round(pos['volume'] / 3, 2)  # فرمول اصلی شما برگردانده شد
        await query.edit_message_text(f"⏳ در حال بستن `{sm_vol}` لات از پوزیشن `{ticket}`...", parse_mode="Markdown")
        _, msg = await asyncio.to_thread(close_position, ticket, sm_vol)
        await query.edit_message_text(
            f"{msg}",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 بازگشت به لیست", callback_data="refresh_positions_list")]]),
            parse_mode="Markdown"
        )
        return ConversationHandler.END

    # ------------------ ۴. خروج ۵۰٪ حجم ------------------
    elif action == "close50":
        success, positions = await asyncio.to_thread(get_open_positions)
        pos = next((p for p in positions if p['ticket'] == ticket), None) if success and positions else None

        if not pos:
            await query.edit_message_text("❌ پوزیشن یافت نشد یا قبلاً بسته شده است.", parse_mode="Markdown")
            return ConversationHandler.END

        half_vol = round(pos['volume'] / 2, 2)
        if half_vol < 0.01:
            await query.edit_message_text("⚠️ **حجم پوزیشن برای خروج ۵۰٪ بسیار کوچک است.**", parse_mode="Markdown")
            return ConversationHandler.END

        confirm_keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ بله، خروج ۵۰٪", callback_data=f"action_confirmclose50_{ticket}"),
                InlineKeyboardButton("❌ انصراف", callback_data=f"pos_detail_{ticket}")
            ]
        ])
        await query.edit_message_text(
            f"✂️ **تأییدیه خروج ۵۰٪ از پوزیشن `{ticket}`**\n\n"
            f"حجم کل: `{pos['volume']}` لات\nحجم خروج: `{half_vol}` لات\n\nآیا از بستن این میزان حجم اطمینان دارید؟",
            reply_markup=confirm_keyboard,
            parse_mode="Markdown"
        )
        return ConversationHandler.END

    elif action == "confirmclose50":
        success, positions = await asyncio.to_thread(get_open_positions)
        pos = next((p for p in positions if p['ticket'] == ticket), None) if success and positions else None

        if not pos:
            await query.edit_message_text("❌ پوزیشن یافت نشد یا قبلاً بسته شده است.", parse_mode="Markdown")
            return ConversationHandler.END

        half_vol = round(pos['volume'] / 2, 2)
        await query.edit_message_text(f"⏳ در حال بستن `{half_vol}` لات از پوزیشن `{ticket}`...", parse_mode="Markdown")
        _, msg = await asyncio.to_thread(close_position, ticket, half_vol)
        await query.edit_message_text(
            f"{msg}",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 بازگشت به لیست", callback_data="refresh_positions_list")]]),
            parse_mode="Markdown"
        )
        return ConversationHandler.END

    # ------------------ ۵. تغییر SL / TP ------------------
    elif action == "editsltp":
        context.user_data["action_ticket"] = ticket
        cancel_btn = InlineKeyboardMarkup([[InlineKeyboardButton("❌ انصراف", callback_data=f"cancel_action_{ticket}")]])

        await query.edit_message_text(
            f"✏️ **ویرایش حد ضرر و حد سود پوزیشن `{ticket}`**\n\n"
            f"لطفاً **حد ضرر (SL)** و **حد سود (TP)** جدید را با یک فاصله وارد کنید:\n"
            f"💡 **فرمت:** `<SL> <TP>`\n"
            f"مثال: `2030.50 2060.00` (برای عدم تغییر هرکدام عدد 0 بگذارید)",
            reply_markup=cancel_btn,
            parse_mode="Markdown"
        )
        return INPUT_NEW_SL_TP

    # ------------------ ۶. خروج جزئی دلخواه ------------------
    elif action == "partial":
        context.user_data["action_ticket"] = ticket
        cancel_btn = InlineKeyboardMarkup([[InlineKeyboardButton("❌ انصراف", callback_data=f"cancel_action_{ticket}")]])

        await query.edit_message_text(
            f"✂️ **خروج جزئی از پوزیشن `{ticket}`**\n\nلطفاً **حجم مورد نظر جهت خروج** را به لات وارد کنید (مثال: `0.05`):",
            reply_markup=cancel_btn,
            parse_mode="Markdown"
        )
        return INPUT_PARTIAL_LOT

    return ConversationHandler.END


async def process_new_sltp_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دریافت مقادیر جدید SL و TP از کاربر"""
    ticket = context.user_data.get("action_ticket")

    # ۱. پاکسازی متن و تبدیل کاما به نقطه برای پشتیبانی از اعشار تمام کیبوردها
    raw_text = update.message.text.strip().replace(",", ".")
    text = raw_text.split()

    # ۲. بررسی تعداد آرگومان‌های ورودی
    if len(text) != 2:
        await update.message.reply_text(
            "❌ **فرمت ورودی نادرست است.**\n\n"
            "لطفاً دو عدد با یک فاصله وارد کنید (مثال: `2030.50 2060.00` یا `0 2060.00`):",
            parse_mode="Markdown"
        )
        return INPUT_NEW_SL_TP

    # ۳. تبدیل به عدد اعشاری با تبدیل امن
    try:
        new_sl = float(text[0])
        new_tp = float(text[1])
    except ValueError:
        await update.message.reply_text(
            "❌ **مقادیر وارد شده باید عدد باشند.** مجدداً وارد کنید (مثال: `2030.50 2060.00`):",
            parse_mode="Markdown"
        )
        return INPUT_NEW_SL_TP

    msg = await update.message.reply_text("⏳ در حال بروزرسانی حد ضرر و حد سود...", parse_mode="Markdown")

    # ۴. فراخوانی متاتریدر برای آپدیت SL/TP
    _, res_msg = await asyncio.to_thread(update_position_sltp, ticket, new_sl, new_tp)

    await msg.edit_text(
        res_msg,
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔙 بازگشت به لیست پوزیشن‌ها", callback_data="refresh_positions_list")]]
        ),
        parse_mode="Markdown"
    )

    # ۵. پاکسازی داده‌های موقت و اتمام گفتگو
    context.user_data.pop("action_ticket", None)
    return ConversationHandler.END

# مراحل ConversationHandler برای دریافت عددی SL/TP یا Partial Close
INPUT_NEW_SL_TP, INPUT_PARTIAL_LOT = ("INPUT_NEW_SL_TP","INPUT_PARTIAL_LOT")
async def process_partial_close_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دریافت حجم خروج جزئی دلخواه"""
    ticket = context.user_data.get("action_ticket")

    try:
        vol = float(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ **حجم وارد شده باید یک عدد معتبر باشد.** (مثال: `0.02`):")
        return INPUT_PARTIAL_LOT

    msg = await update.message.reply_text(f"⏳ در حال بستن `{vol}` لات از پوزیشن `{ticket}`...", parse_mode="Markdown")

    _, res_msg = await asyncio.to_thread(close_position, ticket, vol)

    await msg.edit_text(
        res_msg,
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔙 بازگشت به لیست پوزیشن‌ها", callback_data="refresh_positions_list")]]),
        parse_mode="Markdown"
    )
    return ConversationHandler.END

# تعریف مراحل Conversation New Trade
NEW_TRADE_SYMBOL, NEW_TRADE_ACTION, NEW_TRADE_LOT,NEW_TRADE_PRICE, NEW_TRADE_SL, NEW_TRADE_TP = (
    "NEW_TRADE_SYMBOL",
    "NEW_TRADE_ACTION",
    "NEW_TRADE_LOT",
    "NEW_TRADE_PRICE",
    "NEW_TRADE_SL",
    "NEW_TRADE_TP"
)

async def start_trade_wizard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مرحله ۱: دریافت پویای نمادها از Market Watch و ساخت دکمه‌ها"""
    context.user_data.clear()

    # ارسال فیدبک سریع در صورت کلیک روی دکمه شیشه‌ای
    if update.callback_query:
        await update.callback_query.answer()

    # دریافت نمادهای واچ‌لیست از متاتریدر در Executor (غیربلاک‌کننده)
    symbols = await asyncio.to_thread(get_market_watch_symbols)

    # چیدمان پویا: ایجاد دکمه‌های ۲ تایی در هر سطر
    keyboard = []
    row = []
    for sym in symbols:
        row.append(InlineKeyboardButton(sym, callback_data=f"sym_{sym}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    # افزودن دکمه انصراف در انتهای کیبورد
    keyboard.append([InlineKeyboardButton("❌ انصراف", callback_data="cancel_trade")])

    reply_markup = InlineKeyboardMarkup(keyboard)

    msg_text = (
        "📊 <b>ایجاد معامله جدید (مرحله ۱ از ۵)</b>\n\n"
        "لطفاً نماد مورد نظر را از <b>واچ‌لیست متاتریدر</b> انتخاب کنید یا نام آن را تایپ نمایید:"
    )

    if update.callback_query:
        await update.callback_query.edit_message_text(msg_text, reply_markup=reply_markup, parse_mode="HTML")
    elif update.message:
        await update.message.reply_text(msg_text, reply_markup=reply_markup, parse_mode="HTML")

    return NEW_TRADE_SYMBOL


async def new_trade_get_symbol_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مرحله ۲: دریافت نماد و انتخاب جهت معامله (BUY/SELL)"""

    # ۱. برقراری ایمنی کامل برای CallbackQuery و Message
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        symbol = query.data.replace("sym_", "").strip().upper()
    elif update.message and update.message.text:
        symbol = update.message.text.strip().upper()
    else:
        # اگر ورودی غیرمتنی فرستاده شد
        return NEW_TRADE_SYMBOL

    # ۲. ذخیره نماد انتخاب‌شده
    context.user_data["trade_symbol"] = symbol

    # ۳. دکمه‌های انتخاب جهت معامله (BUY / SELL)
    keyboard = [
        [
            InlineKeyboardButton("🟢 BUY (خرید)", callback_data="act_BUY"),
            InlineKeyboardButton("🔴 SELL (فروش)", callback_data="act_SELL")
        ],
        [InlineKeyboardButton("❌ انصراف", callback_data="cancel_trade")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    text = (
        f"📌 <b>نماد انتخاب شده:</b> <code>{symbol}</code>\n\n"
        "<b>مرحله ۲ از ۵:</b> جهت معامله را انتخاب کنید:"
    )

    # ۴. ارسال یا ادیت پیام متناسب با نوع ورودی
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode="HTML")
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="HTML")

    return NEW_TRADE_ACTION


async def new_trade_get_action_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مرحله ۳: ذخیره جهت و دریافت حجم (Lot)"""
    query = update.callback_query
    await query.answer()

    action = query.data.replace("act_", "")
    context.user_data["trade_action"] = action

    # دکمه‌های میانبر برای حجم‌های رایج
    keyboard = [
        [InlineKeyboardButton("0.01", callback_data="lot_0.01"), InlineKeyboardButton("0.05", callback_data="lot_0.05"),
         InlineKeyboardButton("0.10", callback_data="lot_0.10")],
        [InlineKeyboardButton("0.50", callback_data="lot_0.50"),
         InlineKeyboardButton("1.00", callback_data="lot_1.00")],
        [InlineKeyboardButton("❌ انصراف", callback_data="cancel_trade")]
    ]

    text = (
        f"📌 <b>نماد:</b> <code>{context.user_data['trade_symbol']}</code> | <b>جهت:</b> <code>{action}</code>\n\n"
        "<b>مرحله ۳ از ۵:</b> حجم معامله (Lot) را وارد کنید یا از دکمه‌ها انتخاب کنید:"
    )

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    return NEW_TRADE_LOT


async def new_trade_get_lot_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مرحله ۴: ذخیره حجم و درخواست قیمت ورود (یا معامله مارکت)"""
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        lot_str = query.data.replace("lot_", "")
    else:
        lot_str = update.message.text.strip()

    try:
        lot = float(lot_str)
        if lot <= 0:
            raise ValueError
    except ValueError:
        msg = "❌ <b>حجم وارد شده نامعتبر است. لطفاً یک عدد مثبت وارد کنید:</b>"
        if update.callback_query:
            await update.callback_query.message.reply_text(msg, parse_mode="HTML")
        else:
            await update.message.reply_text(msg, parse_mode="HTML")
        return NEW_TRADE_LOT

    context.user_data["trade_lot"] = lot

    # دکمه ورود با قیمت لحظه‌ای (Market Price)
    keyboard = [
        [InlineKeyboardButton("⚡ ورود با قیمت لحظه‌ای (Market)", callback_data="price_market")],
        [InlineKeyboardButton("❌ انصراف", callback_data="cancel_trade")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    text = (
        f"📌 <b>نماد:</b> <code>{context.user_data['trade_symbol']}</code> | "
        f"<b>جهت:</b> <code>{context.user_data['trade_action']}</code> | "
        f"<b>حجم:</b> <code>{lot}</code>\n\n"
        "<b>مرحله ۴ از ۶:</b> لطفاً <b>قیمت ورود مد نظر (Pending Order)</b> را تایپ کنید "
        "یا دکمه <b>ورود با قیمت لحظه‌ای</b> را بزنید:"
    )

    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode="HTML")
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="HTML")

    return NEW_TRADE_PRICE

async def new_trade_get_price_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مرحله ۵: ذخیره قیمت ورود و درخواست قیمت حد ضرر (SL)"""
    if update.callback_query:
        query = update.callback_query
        await query.answer()

        if query.data == "price_market":
            entry_price = "MARKET"
        else:
            return NEW_TRADE_PRICE
    else:
        price_str = update.message.text.strip()
        try:
            entry_price = float(price_str)
            if entry_price <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text(
                "❌ <b>قیمت وارد شده نامعتبر است. لطفاً یک عدد معتبر وارد کنید:</b>",
                parse_mode="HTML"
            )
            return NEW_TRADE_PRICE

    context.user_data["trade_price"] = entry_price
    price_display = "قیمت لحظه‌ای (Market)" if entry_price == "MARKET" else entry_price

    keyboard = [
        [InlineKeyboardButton("⏭ بدون حد ضرر (0)", callback_data="sl_0")],
        [InlineKeyboardButton("❌ انصراف", callback_data="cancel_trade")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    text = (
        f"📌 <b>نماد:</b> <code>{context.user_data['trade_symbol']}</code> | "
        f"<b>جهت:</b> <code>{context.user_data['trade_action']}</code> | "
        f"<b>حجم:</b> <code>{context.user_data['trade_lot']}</code> | "
        f"<b>ورود:</b> <code>{price_display}</code>\n\n"
        "<b>مرحله ۵ از ۶:</b> <b>قیمت دقیق حد ضرر (SL)</b> را وارد کنید (مثلاً <code>1.08500</code> یا <code>2650.50</code>):\n"
        "<i>(در صورت عدم نیاز عدد 0 را ارسال یا دکمه رد کردن را بزنید)</i>"
    )

    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode="HTML")
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="HTML")

    return NEW_TRADE_SL


async def new_trade_get_sl_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مرحله ۶: ذخیره قیمت SL و دریافت قیمت حد سود (TP)"""
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        sl_str = query.data.replace("sl_", "")
    else:
        sl_str = update.message.text.strip()

    try:
        sl = float(sl_str)
        if sl < 0:
            raise ValueError
    except ValueError:
        await update.effective_message.reply_text(
            "❌ <b>قیمت حد ضرر نامعتبر است. لطفاً یک عدد معتبر وارد کنید:</b>",
            parse_mode="HTML"
        )
        return NEW_TRADE_SL

    context.user_data["trade_sl"] = sl
    sl_display = sl if sl > 0 else "بدون SL"

    keyboard = [
        [InlineKeyboardButton("⏭ بدون حد سود (0)", callback_data="tp_0")],
        [InlineKeyboardButton("❌ انصراف", callback_data="cancel_trade")]
    ]

    text = (
        f"📌 <b>نماد:</b> <code>{context.user_data['trade_symbol']}</code> | "
        f"<b>حجم:</b> <code>{context.user_data['trade_lot']}</code>\n"
        f"🛑 <b>قیمت SL:</b> <code>{sl_display}</code>\n\n"
        "<b>مرحله ۶ از ۶:</b> <b>قیمت دقیق حد سود (TP)</b> را وارد کنید (مثلاً <code>1.09500</code> یا <code>2700.00</code>):"
    )

    if update.callback_query:
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    return NEW_TRADE_TP


async def execute_trade_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مرحله نهایی: جمع‌آوری تمامی اطلاعات و اجرای معامله"""
    # ۱. دریافت اطلاعات کاربر تلگرام
    user = update.effective_user
    telegram_id = user.id
    username = user.username or user.first_name

    if update.callback_query:
        query = update.callback_query
        await query.answer()
        tp_str = query.data.replace("tp_", "")
    else:
        tp_str = update.message.text.strip()

    try:
        tp = float(tp_str)
        if tp < 0:
            raise ValueError
    except ValueError:
        await update.effective_message.reply_text(
            "❌ <b>قیمت حد سود نامعتبر است. لطفاً یک عدد معتبر وارد کنید:</b>",
            parse_mode="HTML"
        )
        return NEW_TRADE_TP

    symbol = context.user_data["trade_symbol"]
    action = context.user_data["trade_action"]
    lot = context.user_data["trade_lot"]
    entry_price = context.user_data["trade_price"]
    sl = context.user_data["trade_sl"]

    if update.callback_query:
        msg = await query.edit_message_text("⏳ <b>در حال ارسال سفارش به متاتریدر...</b>", parse_mode="HTML")
    else:
        msg = await update.message.reply_text("⏳ <b>در حال ارسال سفارش به متاتریدر...</b>", parse_mode="HTML")



    success, result_msg, rr_ratio = await asyncio.to_thread(
        execute_trade, symbol, action, lot,
        entry_price, sl, tp, telegram_id,username
    )

    if success:
        title = "🎯 <b>معامله با موفقیت ثبت شد</b>"
        status_icon = "✅"
    else:
        title = "🚨 <b>خطا در اجرای معامله!</b>"
        status_icon = "❌"

    price_disp = "قیمت لحظه‌ای (Market)" if entry_price == "MARKET" else entry_price
    sl_disp = sl if sl > 0 else "تعیین نشده"
    tp_disp = tp if tp > 0 else "تعیین نشده"
    rr_disp = f"1:{rr_ratio:.2f}" if rr_ratio else "نامشخص"

    summary_text = (
        f"{title}\n"
        f"───────────────────\n"
        f"👤 <b>کاربر:</b> <code>{username}</code> (<code>{telegram_id}</code>)\n"
        f"📌 <b>نماد:</b> <code>{symbol}</code>\n"
        f"📊 <b>جهت:</b> <code>{action}</code> | 📦 <b>حجم:</b> <code>{lot}</code>\n"
        f"💵 <b>ورود:</b> <code>{price_disp}</code>\n"
        f"🛑 <b>SL:</b> <code>{sl_disp}</code> | 🎯 <b>TP:</b> <code>{tp_disp}</code>\n"
        f"⚖️ <b>نسبت R/R:</b> <code>{rr_disp}</code>\n"
        f"───────────────────\n"
        f"{status_icon} <b>نتیجه:</b> {result_msg}"
    )

    await msg.edit_text(summary_text, parse_mode="HTML")
    context.user_data.clear()
    return ConversationHandler.END

async def cancel_trade_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """انصراف از ساخت معامله و بازگشت به منوی اصلی"""
    context.user_data.clear()

    query = update.callback_query
    if query:
        await query.answer()
        # ۱. ویرایش پیام شیشه‌ای و حذف دکمه‌های آن
        await query.edit_message_text("❌ <b>فرآیند ساخت معامله لغو شد.</b>", parse_mode="HTML")
        # ۲. ارسال پیام جدید برای فعال کردن مجدد کیبورد اصلی
        await update.effective_chat.send_message(
        MAIN_MENU_TEXT,
            reply_markup=MAIN_KEYBOARD
        )
    else:
        # اگر انصراف متنی یا کامند /stop بود
        await update.message.reply_text(
            "❌ <b>فرآیند ساخت معامله لغو شد.</b>",
            reply_markup=MAIN_KEYBOARD,
            parse_mode="HTML"
        )

    return ConversationHandler.END


async def close_position_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    ticket = int(query.data.split("_")[2])
    logger.info("Request to close position ticket #%s from chat_id %s", ticket, update.effective_chat.id)
    await query.edit_message_text(f"⏳ در حال بستن پوزیشن `{ticket}`...", parse_mode="Markdown")

    _, message = await asyncio.to_thread(close_position_by_ticket, ticket)
    logger.info("Close position #%s result: %s", ticket, message)
    await query.edit_message_text(message,reply_markup=MAIN_KEYBOARD, parse_mode="Markdown")


async def close_all_positions_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش پیام تاییدیه قبل از اجرای دستور بستن همه پوزیشن‌ها"""
    chat_id = update.effective_chat.id
    query = update.callback_query
    if query:
        await query.answer()

    # ۱. متوقف کردن تمامی تایمرهای قبلی (لیست کلی و تک پوزیشن)
    await stop_all_live_jobs(chat_id, context)

    # ایجاد کیبورد تاییدیه
    keyboard = [
        [
            InlineKeyboardButton("✅ بله، همه را ببند", callback_data="confirm_close_all"),
            InlineKeyboardButton("❌ انصراف", callback_data="refresh_positions_list"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    text = (
        "⚠️ **هشدار: آیا اطمینان دارید؟**\n\n"
        "با تایید این گزینه، **تمام پوزیشن‌های فعال** شما در متاتریدر ۵ فوراً و با قیمت بازار بسته خواهند شد."
    )

    if query:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")


async def confirm_close_all_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """اجرای واقعی بستن تمامی پوزیشن‌ها پس از تایید کاربر"""
    query = update.callback_query
    await query.answer("در حال بستن همه پوزیشن‌ها...")

    chat_id = update.effective_chat.id

    logger.info("Confirmed close ALL positions triggered by chat_id %s", chat_id)
    await query.edit_message_text("⏳ <b>در حال بستن تمامی پوزیشن‌ها...</b>", parse_mode="HTML")

    # ۲. اجرای غیربلاک‌کننده بستن همه پوزیشن‌ها
    res_msg = await asyncio.to_thread(close_all_positions)
    logger.info("Close ALL positions result: %s", res_msg)

    # نمایش نتیجه و دکمه بازگشت به لیست
    back_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 بازگشت به لیست پوزیشن‌ها", callback_data="refresh_positions_list")]
    ])
    await query.edit_message_text(f"{res_msg}", reply_markup=back_keyboard, parse_mode="HTML")