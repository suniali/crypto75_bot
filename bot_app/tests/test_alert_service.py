import pytest
from django.db import IntegrityError
from bot_app.models import TelegramUser, UserAlert, MarketType
from bot_app.services.alert_service import (
    fetch_active_alerts,
    deactivate_alert,
    deactivate_alert_by_id,
    create_user_alert,
    get_user_active_alerts,
)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
class TestAlertService:

    @pytest.fixture(autouse=True)
    async def setup_data(self):
        """ایجاد داده‌های پایه برای تست‌ها"""
        self.user1 = await TelegramUser.objects.acreate(
            chat_id=123456789, username="testuser1"
        )
        self.user2 = await TelegramUser.objects.acreate(
            chat_id=987654321, username="testuser2"
        )

    async def test_fetch_active_alerts(self):
        # ساخت آلرت فعال و غیرفعال
        await UserAlert.objects.acreate(
            user=self.user1, symbol="BTCUSDT", target_price=65000.0, is_active=True
        )
        await UserAlert.objects.acreate(
            user=self.user1, symbol="ETHUSDT", target_price=3500.0, is_active=False
        )

        active_alerts = await fetch_active_alerts()
        assert len(active_alerts) == 1
        assert active_alerts[0].symbol == "BTCUSDT"
        assert active_alerts[0].user.chat_id == 123456789

    async def test_deactivate_alert(self):
        alert = await UserAlert.objects.acreate(
            user=self.user1, symbol="XAUUSD", target_price=2400.0, is_active=True
        )

        await deactivate_alert(alert)

        # تازه سازی داده از دیتابیس
        await alert.arefresh_from_db()
        assert alert.is_active is False

    async def test_deactivate_alert_by_id_success(self):
        alert = await UserAlert.objects.acreate(
            user=self.user1, symbol="EURUSD", target_price=1.0850, is_active=True
        )

        result = await deactivate_alert_by_id(alert.id)
        assert result is True

        await alert.arefresh_from_db()
        assert alert.is_active is False

    async def test_deactivate_alert_by_id_failure(self):
        # تست با ID غیراعتلا
        result = await deactivate_alert_by_id(99999)
        assert result is False

    async def test_create_user_alert_success(self):
        alert, created = await create_user_alert(
            user=self.user1,
            symbol="btcusdt",  # تست حروف کوچک
            target_price=67000.0,
            market_type=MarketType.CRYPTO
        )

        assert created is True
        assert alert is not None
        assert alert.symbol == "BTCUSDT"  # باید بزرگ شده باشد
        assert alert.target_price == 67000.0

    async def test_create_user_alert_integrity_error(self):
        # ساخت اولین آلرت
        await create_user_alert(self.user1, "BTCUSDT", 60000.0)

        # اگر در مدل UniqueConstraint روی (user, symbol, target_price, is_active) وجود دارد:
        alert, created = await create_user_alert(self.user1, "BTCUSDT", 60000.0)

        # در صورت تکرار باید IntegrityError هندل شده و False برگردد
        assert created is False
        assert alert is None

    async def test_get_user_active_alerts(self):
        # آلرت‌های کاربر ۱
        await UserAlert.objects.acreate(user=self.user1, symbol="BTCUSDT", target_price=60000.0, is_active=True)
        await UserAlert.objects.acreate(user=self.user1, symbol="ETHUSDT", target_price=3000.0, is_active=True)
        # آلرت غیرفعال کاربر ۱
        await UserAlert.objects.acreate(user=self.user1, symbol="SOLUSDT", target_price=150.0, is_active=False)
        # آلرت کاربر ۲
        await UserAlert.objects.acreate(user=self.user2, symbol="XRPUSDT", target_price=0.50, is_active=True)

        user1_alerts = await get_user_active_alerts(self.user1)

        assert len(user1_alerts) == 2
        symbols = [a.symbol for a in user1_alerts]
        assert "BTCUSDT" in symbols
        assert "ETHUSDT" in symbols
        assert "SOLUSDT" not in symbols