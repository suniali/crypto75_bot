import io
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
import pandas as pd

from bot_app.price_checker import check_target_reached, process_alert


# ------------------------------------------------------------------
# ۱. تست‌های تابع pure و ساده check_target_reached
# ------------------------------------------------------------------
class TestCheckTargetReached:

    def test_alert_type_above(self):
        """تست شرط ABOVE (قیمت بالا رفته یا مساوی تارگت شده)"""
        assert check_target_reached(105.0, 100.0, "ABOVE") is True
        assert check_target_reached(100.0, 100.0, "ABOVE") is True
        assert check_target_reached(95.0, 100.0, "ABOVE") is False

    def test_alert_type_below(self):
        """تست شرط BELOW (قیمت پایین آمده یا مساوی تارگت شده)"""
        assert check_target_reached(95.0, 100.0, "BELOW") is True
        assert check_target_reached(100.0, 100.0, "BELOW") is True
        assert check_target_reached(105.0, 100.0, "BELOW") is False

    def test_alert_type_fallback_exact_match(self):
        """تست حالت دیفالت ( برابری دقیق)"""
        assert check_target_reached(100.0, 100.0, "EXACT") is True
        assert check_target_reached(105.0, 100.0, "EXACT") is False


# ------------------------------------------------------------------
# ۲. فیکسچرهای مربوط به alert نمونه
# ------------------------------------------------------------------
@pytest.fixture
def crypto_alert():
    alert = MagicMock()
    alert.id = 1
    alert.symbol = "BTCUSDT"
    alert.target_price = 50000.0
    alert.alert_type = "ABOVE"
    alert.is_forex = False
    return alert


@pytest.fixture
def forex_alert():
    alert = MagicMock()
    alert.id = 2
    alert.symbol = "EURUSD"
    alert.target_price = 1.08500
    alert.alert_type = "BELOW"
    alert.is_forex = True
    return alert


@pytest.fixture
def sample_klines_df():
    """دیتافریم نمونه برای کندل‌ها"""
    data = {
        'open': [100.0, 101.0],
        'high': [102.0, 103.0],
        'low': [99.0, 100.5],
        'close': [101.0, 102.5],
        'volume': [10, 15]
    }
    return pd.DataFrame(data)


# ------------------------------------------------------------------
# ۳. تست‌های async تابع process_alert
# ------------------------------------------------------------------
@pytest.mark.asyncio
class TestProcessAlert:

    @patch("bot_app.price_checker.get_crypto_price", new_callable=AsyncMock)
    @patch("bot_app.price_checker.deactivate_alert", new_callable=AsyncMock)
    @patch("bot_app.price_checker.fetch_recent_klines", new_callable=AsyncMock)
    @patch("bot_app.price_checker.create_heikin_ashi_chart")
    async def test_process_crypto_alert_target_reached_success(
            self, mock_create_chart, mock_fetch_klines, mock_deactivate, mock_get_crypto_price,
            crypto_alert, sample_klines_df
    ):
        """تست فعال‌سازی موفق آلرت کریپتو با چارت هیکن آشی"""
        client_mock = AsyncMock()
        mock_get_crypto_price.return_value = 51000.0  # قیمت بالاتر از ۵۰۰۰۰
        mock_fetch_klines.return_value = sample_klines_df

        mock_chart_buf = io.BytesIO(b"fake_chart_bytes")
        mock_create_chart.return_value = mock_chart_buf

        result = await process_alert(client_mock, crypto_alert)

        # ۱. بررسی صدا زدن غیرفعال‌سازی در دیتابیس
        mock_deactivate.assert_called_once_with(crypto_alert)

        # ۲. بررسی صدا زدن ساخت چارت
        mock_create_chart.assert_called_once_with(sample_klines_df, 50000.0, "BTCUSDT")

        # ۳. بررسی خروجی پیام و چارت
        assert result is not None
        msg, chart_buf = result
        assert "BTCUSDT" in msg
        assert "50000.0" in msg
        assert chart_buf == mock_chart_buf

    @patch("bot_app.price_checker.get_forex_price")
    @patch("bot_app.price_checker.deactivate_alert", new_callable=AsyncMock)
    @patch("bot_app.price_checker.fetch_recent_klines", new_callable=AsyncMock)
    async def test_process_forex_alert_target_reached_success(
            self, mock_fetch_klines, mock_deactivate, mock_get_forex_price,
            forex_alert
    ):
        """تست فعال‌سازی آلرت فارکس با استفاده از get_forex_price بر روی نخ مجزا"""
        client_mock = AsyncMock()
        mock_get_forex_price.return_value = 1.08400  # پایین‌تر از ۱.۰۸۵۰۰ (BELOW)
        mock_fetch_klines.return_value = None  # فرضا کلاین دریافت نشد

        result = await process_alert(client_mock, forex_alert)

        mock_deactivate.assert_called_once_with(forex_alert)
        assert result is not None
        msg, chart_buf = result
        assert "EURUSD" in msg
        assert chart_buf is None  # چارت تولید نشده است

    @patch("bot_app.price_checker.get_crypto_price", new_callable=AsyncMock)
    @patch("bot_app.price_checker.deactivate_alert", new_callable=AsyncMock)
    async def test_process_alert_target_not_reached(
            self, mock_deactivate, mock_get_crypto_price, crypto_alert
    ):
        """تست نرسیدن قیمت به تارگت (عدم غیرفعال‌سازی و برگشت None)"""
        client_mock = AsyncMock()
        mock_get_crypto_price.return_value = 49000.0  # هنوز نرسیده به ۵۰۰۰۰

        result = await process_alert(client_mock, crypto_alert)

        mock_deactivate.assert_not_called()
        assert result is None

    @patch("bot_app.price_checker.get_crypto_price", new_callable=AsyncMock)
    async def test_process_alert_price_fetch_failed(
            self, mock_get_crypto_price, crypto_alert
    ):
        """تست عدم موفقیت در دریافت قیمت (بازگشت None از ای‌پی‌آی)"""
        client_mock = AsyncMock()
        mock_get_crypto_price.return_value = None

        result = await process_alert(client_mock, crypto_alert)

        assert result is None

    @patch("bot_app.price_checker.get_crypto_price", new_callable=AsyncMock)
    async def test_process_alert_exception_handling(
            self, mock_get_crypto_price, crypto_alert
    ):
        """تست مدیریت Exceptionهای غیرمنتظره و عدم کرش برنامه"""
        client_mock = AsyncMock()
        mock_get_crypto_price.side_effect = Exception("Binance Connection Timeout")

        result = await process_alert(client_mock, crypto_alert)

        assert result is None