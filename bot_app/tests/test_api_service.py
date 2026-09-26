import pytest
import httpx
import pandas as pd
from unittest.mock import patch, AsyncMock
from bot_app.services.api_service import (
    fetch_with_retry,
    get_crypto_price,
    fetch_recent_klines,
)


@pytest.mark.asyncio
class TestApiService:

    # ------------------------------------------------------------------
    # 1. Tests for fetch_with_retry
    # ------------------------------------------------------------------
    async def test_fetch_with_retry_success_first_try(self, httpx_mock):
        """تست دریافت موفق اطلاعات در تلاش اول"""
        httpx_mock.add_response(url="https://api.test.com", status_code=200, json={"status": "ok"})

        async with httpx.AsyncClient() as client:
            response = await fetch_with_retry(client, "https://api.test.com", retries=2)
            assert response is not None
            assert response.status_code == 200
            assert response.json() == {"status": "ok"}

    async def test_fetch_with_retry_success_after_retry(self, httpx_mock):
        """تست موفقیت پس از یک بار خطا (Retry)"""
        httpx_mock.add_response(url="https://api.test.com", status_code=500)
        httpx_mock.add_response(url="https://api.test.com", status_code=200, json={"status": "ok"})

        async with httpx.AsyncClient() as client:
            response = await fetch_with_retry(client, "https://api.test.com", retries=2)
            assert response is not None
            assert response.status_code == 200

    async def test_fetch_with_retry_all_failures(self, httpx_mock):
        """تست شکست در تمام تلاش‌ها"""
        httpx_mock.add_response(url="https://api.test.com", status_code=500)
        httpx_mock.add_response(url="https://api.test.com", status_code=500)
        httpx_mock.add_response(url="https://api.test.com", status_code=500)

        async with httpx.AsyncClient() as client:
            response = await fetch_with_retry(client, "https://api.test.com", retries=2)
            assert response is None

    # ------------------------------------------------------------------
    # 2. Tests for get_crypto_price
    # ------------------------------------------------------------------
    async def test_get_crypto_price_success(self, httpx_mock):
        """تست دریافت قیمت لحظه‌ای کریپتو"""
        httpx_mock.add_response(
            url="https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT",
            status_code=200,
            json={"symbol": "BTCUSDT", "price": "65432.10"}
        )

        async with httpx.AsyncClient() as client:
            price = await get_crypto_price(client, "btcusdt")
            assert price == 65432.10
            assert isinstance(price, float)

    async def test_get_crypto_price_failure(self, httpx_mock):
        """تست شکست در دریافت قیمت کریپتو"""
        httpx_mock.add_response(
            url="https://api.binance.com/api/v3/ticker/price?symbol=INVALID",
            status_code=400,
            is_reusable=True
        )

        async with httpx.AsyncClient() as client:
            price = await get_crypto_price(client, "invalid")
            assert price is None

    # ------------------------------------------------------------------
    # 3. Tests for fetch_recent_klines (Crypto & Forex)
    # ------------------------------------------------------------------
    async def test_fetch_recent_klines_crypto_success(self, httpx_mock):
        """تست دریافت و تبدیل کندل‌های بایننس به DataFrame"""
        # دیتای ساختگی Klines بایننس (مهر استاندارد بایننس)
        mock_klines = [
            [1609459200000, "29000.0", "29500.0", "28800.0", "29300.0", "100", 1609462799999, "2930000", 10, "50",
             "1465000", "0"],
            [1609462800000, "29300.0", "29800.0", "29200.0", "29600.0", "150", 1609466399999, "4440000", 15, "80",
             "2368000", "0"]
        ]
        httpx_mock.add_response(
            url="https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1h&limit=50",
            status_code=200,
            json=mock_klines
        )

        async with httpx.AsyncClient() as client:
            df = await fetch_recent_klines(client, "BTCUSDT", interval="1h", limit=50, is_forex=False)

            assert df is not None
            assert isinstance(df, pd.DataFrame)
            assert list(df.columns) == ['Open', 'High', 'Low', 'Close']
            assert len(df) == 2
            assert df.iloc[0]['Close'] == 29300.0

    @patch("bot_app.services.api_service.get_rates_data")
    async def test_fetch_recent_klines_forex_success(self, mock_get_rates):
        """تست دریافت نرخ‌های متاتریدر ۵ (Forex) با ماک کردن get_rates_data"""
        # دیتای ساختگی متاتریدر ۵
        mock_rates = [
            {'time': 1609459200, 'open': 1.2100, 'high': 1.2150, 'low': 1.2080, 'close': 1.2140, 'tick_volume': 500},
            {'time': 1609462800, 'open': 1.2140, 'high': 1.2180, 'low': 1.2130, 'close': 1.2170, 'tick_volume': 600}
        ]
        mock_get_rates.return_value = (mock_rates, "OK")

        async with httpx.AsyncClient() as client:
            df = await fetch_recent_klines(client, "EURUSD", interval="1h", limit=1, is_forex=True)

            assert df is not None
            assert isinstance(df, pd.DataFrame)
            assert list(df.columns) == ['Open', 'High', 'Low', 'Close']
            assert len(df) == 1  # به خاطر limit=1
            assert df.iloc[0]['Close'] == 1.2170
            mock_get_rates.assert_called_once_with("EURUSD", "1h")

    @patch("bot_app.services.api_service.get_rates_data")
    async def test_fetch_recent_klines_forex_failure(self, mock_get_rates):
        """تست مدیریت خطای دریافت داده از MT5"""
        mock_get_rates.return_value = (False, "Symbol not found")

        async with httpx.AsyncClient() as client:
            df = await fetch_recent_klines(client, "INVALID", is_forex=True)
            assert df is None