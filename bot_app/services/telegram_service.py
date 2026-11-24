import asyncio
import logging
import html

from decouple import config

from telegram import Bot, Update
from telegram.ext import ContextTypes
from telegram.request import HTTPXRequest

from bot_app.utils.keyboard_helper import MAIN_KEYBOARD
from bot_app.models import TelegramUser

logger = logging.getLogger(__name__)

# کاهش تایم‌اوت‌ها برای اینکه در صورت قطعی شبکه، سریع شکست بخورد و برود سراغ Retry
request_config = HTTPXRequest(connection_pool_size=8, read_timeout=10.0, connect_timeout=10.0)
bot = Bot(token=config('TELEGRAM_BOT_TOKEN'), request=request_config)


# ==========================================
# بخش ۱: توابع امنیتی (برای ربات تلگرام)
# ==========================================
async def security_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await TelegramUser.objects.filter(is_blocked=False).afirst()
    if not user or update.effective_user.id != user.chat_id:
        user_id = update.effective_user.id if update.effective_user else 'Unknown'
        logger.warning(f'⚠️ Unauthorized access attempt by user ID: {user_id}')
        if update.message:
            await update.message.reply_text('⛔ Access Denied.')
        return False
    return True


# ==========================================
# بخش ۲: ساخت متن پیام
# ==========================================
def _fmt_price(val, default: str = "-") -> str:
    if val is None or val == "":
        return default
    try:
        num = float(val)
        return f"{num:.2f}".rstrip("0").rstrip(".") if "." in f"{num:.2f}" else f"{num:.2f}"
    except (TypeError, ValueError):
        return html.escape(str(val))


def build_nds_message(signal_data: dict) -> str:
    """متن HTML کامل سیگنال NDS برای تلگرام به همراه نظر مشورتی AI و جزئیات ریسک."""
    f = signal_data.get("features") or {}
    explanation = str(f.get("nds_explanation") or "").strip()[:2000]

    # ۱. نماد، جهت معامله و تایم‌فریم
    symbol = html.escape(str(signal_data.get("symbol") or f.get("symbol") or "N/A"))
    side_raw = str(signal_data.get("action") or signal_data.get("side") or f.get("side") or "").upper()
    if side_raw in ("BUY", "LONG", "1", "+1"):
        side_label = "🟢 خرید (BUY)"
    elif side_raw in ("SELL", "SHORT", "-1"):
        side_label = "🔴 فروش (SELL)"
    else:
        side_label = html.escape(side_raw or "N/A")

    timeframe = html.escape(str(signal_data.get("timeframe") or f.get("timeframe") or "M1"))
    htf = html.escape(str(f.get("htf") or "M30"))

    # ۲. نوع سکانس
    is_reentry = bool(signal_data.get("is_reentry_864") or f.get("is_reentry_864"))
    seq_badge = "ورود دوم (۸۶.۴٪)" if is_reentry else "سکانس اصلی (123Flag + BABI)"

    # ۳. سطوح ورود، حد ضرر و اهداف
    entry = _fmt_price(signal_data.get("entry_price") or signal_data.get("entry") or f.get("entry_price") or f.get("entry"))
    sl = _fmt_price(signal_data.get("stop_loss") or signal_data.get("sl") or f.get("stop_loss") or f.get("sl"))
    tp1 = _fmt_price(signal_data.get("tp1") or f.get("tp1"))
    tp_final = _fmt_price(
        signal_data.get("take_profit")
        or signal_data.get("tp_final")
        or signal_data.get("tp")
        or f.get("take_profit")
        or f.get("tp_final")
        or f.get("tp")
    )

    # ۴. نظر مشورتی AI (قبول یا مردود بدون مسدود کردن ارسال)
    ai_prob = signal_data.get("ai_prob") if signal_data.get("ai_prob") is not None else f.get("ai_prob")
    ai_thr = signal_data.get("ai_threshold") if signal_data.get("ai_threshold") is not None else f.get("ai_threshold")
    ai_approved = signal_data.get("ai_approved") if signal_data.get("ai_approved") is not None else f.get("ai_approved")

    try:
        prob_val = float(ai_prob)
        if ai_approved is None and ai_thr is not None:
            ai_approved = prob_val >= float(ai_thr)

        if ai_approved is True:
            verdict_text = "✅ قبول از نظر AI"
        elif ai_approved is False:
            verdict_text = "⚠️ مردود از نظر AI (حالت مشورتی)"
        else:
            verdict_text = "ℹ️ ثبت‌شده"

        thr_part = f" | حد آستانه: {float(ai_thr):.0%}" if ai_thr is not None else ""
        ai_line = f"🤖 <b>نظر AI:</b> {verdict_text} (<code>احتمال: {prob_val:.1%}{thr_part}</code>)"
    except (TypeError, ValueError):
        ai_line = "🤖 <b>نظر AI:</b> <code>در دسترس نیست</code>"

    # ۵. خطای تقارن کالمن (در صورت وجود)
    sym_err = signal_data.get("symmetry_error") if signal_data.get("symmetry_error") is not None else f.get("symmetry_error")
    try:
        sym_line = f"📐 <b>خطای تقارن کالمن:</b> <code>{float(sym_err):.2%}</code>"
    except (TypeError, ValueError):
        sym_line = ""

    # ۶. حجم پیشنهادی و مدیریت ریسک
    risk_pct = f.get("risk_percentage", 1.0)
    volume = _fmt_price(signal_data.get("calculated_volume") or f.get("calculated_volume"), default="0")
    risk_usd = _fmt_price(signal_data.get("risk_amount_usd") or f.get("risk_amount_usd"), default="0")
    log_id = html.escape(str(signal_data.get("log_id") or signal_data.get("id") or "N/A"))

    lines = [
        f"📡 <b>سیگنال NDS — {symbol}</b> | {side_label}",
        f"⏱ <b>تایم‌فریم:</b> <code>{timeframe}</code> (HTF: <code>{htf}</code>) | <b>سکانس:</b> {html.escape(seq_badge)}",
        "",
        "🎯 <b>سطوح معاملاتی:</b>",
        f"• ورود (Entry): <code>{entry}</code>",
        f"• حد ضرر (SL): <code>{sl}</code>",
        f"• هدف اول — خروج ۱/۳ در نقاط ۱و۲ باز: <code>{tp1}</code>",
        f"• هدف نهایی (TP Final): <code>{tp_final}</code>",
        "",
        "🧠 <b>ارزیابی فیلترها:</b>",
        ai_line,
    ]

    if sym_line:
        lines.append(sym_line)

    if explanation:
        lines.extend([
            "",
            "📝 <b>تحلیل و دلیل ستاپ:</b>",
            html.escape(explanation),
        ])

    lines.extend([
        "",
        f"📊 <b>حجم پیشنهادی (ریسک {html.escape(str(risk_pct))}%):</b> <code>{volume}</code> لوت",
        f"💵 <b>مبلغ ریسک:</b> <code>{risk_usd}</code>$",
        f"🔖 <i>شناسه لاگ: #{log_id}</i>",
    ])

    return "\n".join(lines)

# ==========================================
# بخش ۳: موتور ارسال با قابلیت Retry (Outbox)
# ==========================================
async def _try_send_async(message: str, reply_markup=None) -> tuple:
    """برمی‌گرداند: (موفقیت، message_id، متن خطا)"""
    try:
        # نکته: در نسخه‌های جدید python-telegram-bot استفاده از async with bot ضروری نیست
        # مگر اینکه ربات را در حال خاموش کردن باشد. اما برای امنیت نگه می‌داریم.
        user = await TelegramUser.objects.filter(is_blocked=False).afirst()
        if not user:
            return False, None, "No active user found"
            
        msg = await bot.send_message(
            chat_id=user.chat_id,
            text=message,
            parse_mode='HTML',
            reply_markup=reply_markup,
        )
        return True, msg.message_id, None
    except Exception as e:
        return False, None, str(e)


def try_send_signal(signal_data: dict) -> tuple:
    """ارسال سیگنال (توسط notification_service.py صدا زده می‌شود)"""
    try:
        message = build_nds_message(signal_data)
        return asyncio.run(_try_send_async(message, reply_markup=MAIN_KEYBOARD))
    except Exception as e:
        return False, None, f"asyncio wrapper error: {e}"


def try_send_text(text: str) -> tuple:
    """ارسال پیام متنی ساده (مثلاً گزارش آموزش مدل توسط nds_trainer.py)"""
    try:
        return asyncio.run(_try_send_async(html.escape(text)))
    except Exception as e:
        return False, None, f"asyncio wrapper error: {e}"