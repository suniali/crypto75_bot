import pytest
from bot_app.models import TelegramUser, TradeJournal
from bot_app.services.journal_service import (
    create_journal_entry,
    close_journal_entry,
    get_user_pending_trades,
    get_user_trade_history,
)


@pytest.fixture
def sample_user(db):  # <--- پارامتر db اضافه شد و @pytest.mark.django_db حذف شد
    """ایجاد کاربر نمونه در دیتابیس تست"""
    return TelegramUser.objects.create(
        chat_id=123456789,
        username="testuser",
        first_name="Test"
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
class TestJournalService:

    # ------------------------------------------------------------------
    # 1. Tests for create_journal_entry
    # ------------------------------------------------------------------
    async def test_create_journal_entry_success(self, sample_user):
        """تست ثبت موفق یک ژورنال معاملاتی جدید"""
        journal = await create_journal_entry(
            user=sample_user,
            symbol="eurusd",
            trade_type="buy",
            entry_price=1.0850,
            stop_loss=1.0800,
            take_profit=1.0950,
            image_path="/path/to/chart.png"
        )

        assert journal.id is not None
        assert journal.user == sample_user
        assert journal.symbol == "EURUSD"
        assert journal.trade_type == "BUY"
        assert journal.entry_price == 1.0850
        assert journal.stop_loss == 1.0800
        assert journal.take_profit == 1.0950
        assert journal.result == "PENDING"
        assert journal.image_path == "/path/to/chart.png"

    async def test_create_journal_entry_minimal_params(self, sample_user):
        """تست ثبت ژورنال با حداقل پارامترهای اجباری (بدون TP/SL و تصویر)"""
        journal = await create_journal_entry(
            user=sample_user,
            symbol="btcusdt",
            trade_type="sell",
            entry_price=65000.0
        )

        assert journal.symbol == "BTCUSDT"
        assert journal.trade_type == "SELL"
        assert journal.stop_loss is None
        assert journal.take_profit is None
        assert journal.image_path is None

    # ------------------------------------------------------------------
    # 2. Tests for close_journal_entry
    # ------------------------------------------------------------------
    async def test_close_journal_entry_success(self, sample_user):
        """تست بستن موفق معامله و بروزرسانی قیمت خروج و نتیجه"""
        journal = await create_journal_entry(
            user=sample_user,
            symbol="XAUUSD",
            trade_type="BUY",
            entry_price=2300.0
        )

        closed_journal = await close_journal_entry(
            journal_id=journal.id,
            exit_price=2320.0,
            result="win"
        )

        assert closed_journal is not None
        assert closed_journal.id == journal.id
        assert closed_journal.exit_price == 2320.0
        assert closed_journal.result == "WIN"

    async def test_close_journal_entry_not_found(self):
        """تست سعی در بستن ژورنالی که وجود ندارد (شناسه نامعتبر)"""
        closed_journal = await close_journal_entry(
            journal_id=999999,
            exit_price=100.0,
            result="WIN"
        )

        assert closed_journal is None

    # ------------------------------------------------------------------
    # 3. Tests for get_user_pending_trades
    # ------------------------------------------------------------------
    async def test_get_user_pending_trades(self, sample_user):
        """تست دریافت فقط معاملات باز (PENDING) کاربر"""
        pending_trade = await create_journal_entry(
            user=sample_user, symbol="EURUSD", trade_type="BUY", entry_price=1.0800
        )
        closed_trade = await create_journal_entry(
            user=sample_user, symbol="GBPUSD", trade_type="SELL", entry_price=1.2500
        )
        await close_journal_entry(closed_trade.id, exit_price=1.2400, result="WIN")

        pending_trades = await get_user_pending_trades(sample_user)

        assert len(pending_trades) == 1
        assert pending_trades[0].id == pending_trade.id
        assert pending_trades[0].symbol == "EURUSD"

    # ------------------------------------------------------------------
    # 4. Tests for get_user_trade_history
    # ------------------------------------------------------------------
    async def test_get_user_trade_history_and_limit(self, sample_user):
        """تست دریافت تاریخچه معاملات کاربر با رعایت سقف تعداد (Limit)"""
        for i in range(3):
            await create_journal_entry(
                user=sample_user,
                symbol=f"SYM{i}",
                trade_type="BUY",
                entry_price=100.0 + i
            )

        history_all = await get_user_trade_history(sample_user, limit=5)
        assert len(history_all) == 3

        history_limited = await get_user_trade_history(sample_user, limit=2)
        assert len(history_limited) == 2

        assert history_limited[0].symbol == "SYM2"
        assert history_limited[1].symbol == "SYM1"