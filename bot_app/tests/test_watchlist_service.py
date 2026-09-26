import pytest
from bot_app.models import TelegramUser, Watchlist, MarketType
from bot_app.services.watchlist_service import (
    get_all_watchlist,
    save_watchlist_item,
    delete_from_watchlist,
)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
class TestWatchlistService:

    @pytest.fixture(autouse=True)
    async def setup_data(self):
        """ایجاد کاربر موقت برای تست‌ها"""
        self.user = await TelegramUser.objects.acreate(
            chat_id=123456789, username="watchlist_user"
        )

    async def test_get_all_watchlist(self):
        """تست دریافت تمام آیتم‌های واچ‌لیست"""
        await Watchlist.objects.acreate(
            user=self.user, symbol="BTCUSDT", time_frame="1h", market_type=MarketType.CRYPTO
        )
        await Watchlist.objects.acreate(
            user=self.user, symbol="EURUSD", time_frame="1d", market_type=MarketType.FOREX
        )

        items = await get_all_watchlist()
        assert len(items) == 2
        symbols = [item.symbol for item in items]
        assert "BTCUSDT" in symbols
        assert "EURUSD" in symbols

    async def test_save_watchlist_item_success(self):
        """تست ثبت آیتم جدید در واچ‌لیست (با حروف بزرگ)"""
        created = await save_watchlist_item(
            user=self.user,
            symbol="ethusdt",  # حروف کوچک
            timeframe="4h",
            market_type=MarketType.CRYPTO
        )

        assert created is True

        # بررسی وجود در دیتابیس با حروف بزرگ
        item = await Watchlist.objects.aget(user=self.user, symbol="ETHUSDT")
        assert item.time_frame == "4h"
        assert item.market_type == MarketType.CRYPTO

    async def test_save_watchlist_item_duplicate(self):
        """تست عدم ثبت مجدد آیتم تکراری در واچ‌لیست"""
        # ثبت بار اول
        await save_watchlist_item(
            user=self.user, symbol="XAUUSD", timeframe="1h", market_type=MarketType.FOREX
        )

        # تلاش برای ثبت مجدد همان آیتم
        created = await save_watchlist_item(
            user=self.user, symbol="xauusd", timeframe="1h", market_type=MarketType.FOREX
        )

        assert created is False
        assert await Watchlist.objects.filter(user=self.user, symbol="XAUUSD").acount() == 1

    async def test_delete_from_watchlist_success(self):
        """تست حذف موفق یک آیتم از واچ‌لیست"""
        await Watchlist.objects.acreate(
            user=self.user, symbol="GBPUSD", time_frame="15m", market_type=MarketType.FOREX
        )

        deleted = await delete_from_watchlist(
            symbol="gbpusd", timeframe="15m", market_type=MarketType.FOREX
        )

        assert deleted is True
        assert await Watchlist.objects.filter(symbol="GBPUSD").acount() == 0

    async def test_delete_from_watchlist_not_found(self):
        """تست عدم موفقیت حذف برای آیتم ناموجود"""
        deleted = await delete_from_watchlist(
            symbol="NONEXISTENT", timeframe="1h", market_type=MarketType.CRYPTO
        )

        assert deleted is False