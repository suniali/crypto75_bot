import os
import logging
from typing import Optional
from asgiref.sync import sync_to_async
from django.conf import settings
from google import genai

from bot_app.models import TelegramUser, TradeJournal

logger = logging.getLogger("journal_service")

# پیکربندی اولیه SDK گوگل جمینای برای OCR و آنالیز تصویر
GEMINI_API_KEY = getattr(settings, "GEMINI_API_KEY", os.environ.get("GEMINI_API_KEY", ""))
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)


# ------------------------------------------------------------------
# TradeJournal Database Services
# ------------------------------------------------------------------

@sync_to_async(thread_sensitive=True)
def create_journal_entry(
        user: TelegramUser,
        symbol: str,
        trade_type: str,
        entry_price: float,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        image_path: Optional[str] = None
) -> TradeJournal:
    """ثبت یک معامله جدید در ژورنال معاملاتی دیتابیس"""
    journal = TradeJournal.objects.create(
        user=user,
        symbol=symbol.upper(),
        trade_type=trade_type.upper(),
        entry_price=entry_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        result='PENDING',
        image_path=image_path
    )
    logger.info("Trade journal created successfully for user %s on %s", user.chat_id, journal.symbol)
    return journal


@sync_to_async(thread_sensitive=True)
def close_journal_entry(
        journal_id: int,
        exit_price: float,
        result: str
) -> Optional[TradeJournal]:
    """بستن معامله در ژورنال و ثبت قیمت خروج و نتیجه (WIN / LOSS / PENDING)"""
    try:
        journal = TradeJournal.objects.get(id=journal_id)
        journal.exit_price = exit_price
        journal.result = result.upper()
        journal.save(update_fields=['exit_price', 'result'])
        logger.info("Closed trade journal ID #%s with result: %s", journal_id, result)
        return journal
    except TradeJournal.DoesNotExist:
        logger.error("Trade journal ID #%s not found for closing.", journal_id)
        return None


@sync_to_async(thread_sensitive=True)
def get_user_pending_trades(user: TelegramUser) -> list[TradeJournal]:
    """دریافت لیست معاملات باز (PENDING) کاربر"""
    return list(TradeJournal.objects.filter(user=user, result='PENDING').order_by('-created_at'))


@sync_to_async(thread_sensitive=True)
def get_user_trade_history(user: TelegramUser, limit: int = 20) -> list[TradeJournal]:
    """دریافت هیستوری معاملات ثبت‌شده کاربر"""
    return list(TradeJournal.objects.filter(user=user).order_by('-created_at')[:limit])

