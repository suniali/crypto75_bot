import io
import pytest
import pandas as pd
import numpy as np
from unittest.mock import patch, MagicMock, AsyncMock

from bot_app.services.analysis_service import (
    find_pivots,
    detect_divergence,
    calculate_rsi,
    generate_divergence_chart,
    get_lower_tf_candles,
    generate_equity_chart,
    generate_report_charts,
    get_ai_market_view,
    extract_trade_from_image,
    analyze_trades_with_gemini,
)


# =====================================================================
# 1. Indicator & Technical Analysis Tests
# =====================================================================

class TestTechnicalAnalysis:

    def test_find_pivots(self):
        """تست شناسایی درست قله‌ها و دره‌ها در سری زمانی"""
        series = pd.Series([10, 20, 10, 5, 10, 30, 15, 2, 8])
        highs, lows = find_pivots(series, order=1)

        assert len(highs) > 0
        assert len(lows) > 0
        assert 1 in highs  # عدد ۲۰ در ایندکس ۱ قله است
        assert 3 in lows   # عدد ۵ در ایندکس ۳ دره است

    def test_detect_divergence_bullish(self):
        """تست تشخیص واگرایی مثبت (Bullish Divergence) با اردر ۲"""
        # ایجاد داده‌هایی که دره‌ها کاملاً اکسترمم محلی واضح (فاصله حداقل ۲ تایی) دارند:
        # Low 1 در ایندکس 2 (قیمت 10, RSI 20)
        # Low 2 در ایندکس 6 (قیمت 7 [پایین‌تر], RSI 28 [بالاتر])
        prices =     [50, 40, 10, 30, 40, 20,  7, 25, 30]
        rsi_values = [60, 50, 20, 40, 50, 35, 28, 45, 50]

        df = pd.DataFrame({'close': prices, 'rsi': rsi_values})
        div_status, div_type, p1, p2, df_recent = detect_divergence(df, lookback=25)

        assert div_type == "BULLISH"
        assert "واگرایی مثبت" in div_status
        assert p1 is not None and p2 is not None

    def test_detect_divergence_bearish(self):
        """تست تشخیص واگرایی منفی (Bearish Divergence) با اردر ۲"""
        # ایجاد داده‌هایی که قله‌ها کاملاً اکسترمم محلی واضح (فاصله حداقل ۲ تایی) دارند:
        # High 1 در ایندکس 2 (قیمت 150, RSI 75)
        # High 2 در ایندکس 6 (قیمت 170 [بالاتر], RSI 68 [پایین‌تر])
        prices =     [100, 120, 150, 130, 110, 140, 170, 130, 100]
        rsi_values = [ 40,  55,  75,  60,  45,  58,  68,  50,  40]

        df = pd.DataFrame({'close': prices, 'rsi': rsi_values})
        div_status, div_type, p1, p2, df_recent = detect_divergence(df, lookback=25)

        assert div_type == "BEARISH"
        assert "واگرایی منفی" in div_status
        assert p1 is not None and p2 is not None

    def test_detect_divergence_none(self):
        """تست حالت بدون واگرایی"""
        prices = list(range(100, 130))
        rsi_values = list(range(30, 60))

        df = pd.DataFrame({'close': prices, 'rsi': rsi_values})
        div_status, div_type, p1, p2, _ = detect_divergence(df, lookback=25)

        assert div_type is None
        assert div_status == "بدون واگرایی"


# =====================================================================
# 2. Async RSI & Candle Data Fetching Tests
# =====================================================================

class TestAsyncDataFetching:

    @pytest.mark.asyncio
    @patch("httpx.AsyncClient.get")
    async def test_calculate_rsi_crypto_success(self, mock_get):
        """تست محاسبه RSI برای بازار کریپتو از API بایننس"""
        fake_klines = [
            [1600000000000 + i * 60000, "100", "105", "95", str(100 + (i % 5)), "1000", 0, 0, 0, 0, 0, 0]
            for i in range(50)
        ]

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = fake_klines
        mock_get.return_value = mock_resp

        rsi, status, div_status, chart_path = await calculate_rsi("BTCUSDT", "1m", market_type="CRYPTO")

        assert rsi is not None
        assert isinstance(rsi, float)
        assert status in ["🔴 OVERBOUGHT (اشباع خرید)", "🟢 OVERSOLD (اشباع فروش)", "⚪️ NORMAL (محدوده خنثی)"]

    @pytest.mark.asyncio
    @patch("httpx.AsyncClient.get", side_effect=Exception("Connection Timeout"))
    async def test_calculate_rsi_crypto_network_error(self, mock_get):
        """تست خطای شبکه هنگام دریافت RSI بایننس"""
        rsi, status, msg, chart_path = await calculate_rsi("BTCUSDT", "1m", market_type="CRYPTO")

        assert rsi is None
        assert "خطای اتصال به اینترنت" in msg

    @pytest.mark.asyncio
    @patch("bot_app.services.analysis_service.get_rates_data")
    async def test_calculate_rsi_forex_success(self, mock_get_rates):
        """تست محاسبه RSI برای بازار فارکس از MT5"""
        fake_rates = [
            {'time': 1600000000 + i * 60, 'open': 1.1, 'high': 1.12, 'low': 1.08, 'close': 1.1 + (i % 3) * 0.01}
            for i in range(40)
        ]
        mock_get_rates.return_value = (fake_rates, "OK")

        rsi, status, div_status, chart_path = await calculate_rsi("EURUSD", "M1", market_type="FOREX")

        assert rsi is not None
        assert status is not None

    @pytest.mark.asyncio
    @patch("httpx.AsyncClient.get")
    async def test_get_lower_tf_candles_crypto(self, mock_get):
        """تست دریافت کندل‌های تایم پایین کریپتو"""
        fake_klines = [
            [1600000000000, "100.0", "105.0", "98.0", "102.0", "10", 0, 0, 0, 0, 0, 0]
        ]
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = fake_klines
        mock_get.return_value = mock_resp

        result = await get_lower_tf_candles("BTCUSDT", lower_timeframe="1m", count=1, market_type="CRYPTO")

        assert "Open=100.0" in result
        assert "Close=102.0" in result


# =====================================================================
# 3. Chart Generation & Memory Safety Tests
# =====================================================================

class TestChartGeneration:

    def test_generate_divergence_chart(self, tmp_path):
        """تست ساخت چارت واگرایی و ذخیره روی دیسک"""
        df = pd.DataFrame({
            'close': [100.0 + i for i in range(35)],
            'rsi': [30.0 + i for i in range(35)],
            'index_orig': list(range(35))
        })

        with patch("os.makedirs"):
            with patch("matplotlib.figure.Figure.savefig") as mock_savefig:
                path = generate_divergence_chart(df, "EURUSD", "H1", p1_idx=5, p2_idx=25, div_type="BULLISH")
                assert path is not None
                assert "div_EURUSD_H1.png" in path

    def test_generate_equity_chart(self):
        """تست ساخت نمودار Equity Curve به صورت BytesIO"""
        trades = [
            {'time': 1, 'profit': 100.0},
            {'time': 2, 'profit': -30.0},
            {'time': 3, 'profit': 50.0}
        ]

        buf = generate_equity_chart(trades)

        assert buf is not None
        assert isinstance(buf, io.BytesIO)
        assert buf.getvalue().startswith(b'\x89PNG')  # تایید بایت‌های ابتدایی فایل PNG

    def test_generate_equity_chart_empty(self):
        """تست عدم ساخت نمودار در صورت خالی بودن لیست معاملات"""
        assert generate_equity_chart([]) is None

    def test_generate_report_charts(self):
        """تست تولید هر ۳ نمودار تحلیلی گزارش"""
        trades = [
            {'time': 1, 'symbol': 'EURUSD', 'profit': 50.0},
            {'time': 2, 'symbol': 'EURUSD', 'profit': -20.0},
            {'time': 3, 'symbol': 'GBPUSD', 'profit': 100.0}
        ]

        with patch("bot_app.services.analysis_service.calculate_symbol_breakdown") as mock_breakdown:
            mock_breakdown.return_value = [{'symbol': 'EURUSD', 'win_rate': 50.0}]

            charts = generate_report_charts(trades)

            assert isinstance(charts, dict)
            assert 'equity' in charts
            assert 'win_rate_symbol' in charts
            assert 'pnl_dist' in charts
            assert isinstance(charts['equity'], io.BytesIO)


# =====================================================================
# 4. Gemini Async AI Functions Tests
# =====================================================================

class TestGeminiAI:

    @pytest.mark.asyncio
    @patch("bot_app.services.analysis_service.genai.Client")
    @patch("bot_app.services.analysis_service.get_lower_tf_candles", new_callable=AsyncMock)
    async def test_get_ai_market_view_success(self, mock_candles, mock_genai_client):
        """تست دریافت موفق نظر تحلیل‌گر هوش مصنوعی"""
        mock_candles.return_value = "کندل 1: Open=1.1000, High=1.1020, Low=1.0990, Close=1.1015"

        mock_response = MagicMock()
        mock_response.text = "بازار در محدوده اشباع خرید قرار دارد.\nپیشنهاد می‌شود منتظر تایید شکست بمانید."

        mock_client_instance = MagicMock()
        mock_client_instance.aio.models.generate_content = AsyncMock(return_value=mock_response)
        mock_genai_client.return_value = mock_client_instance

        with patch("bot_app.services.analysis_service.API_KEY", "test_key"):
            result = await get_ai_market_view("EURUSD", 75.0, "🔴 OVERBOUGHT", "بدون واگرایی")

            assert "بازار در محدوده اشباع خرید قرار دارد." in result
            assert result.startswith("\u200f")  # کاراکتر RTL تلگرام

    @pytest.mark.asyncio
    @patch("bot_app.services.analysis_service.genai.Client")
    @patch("PIL.Image.open")
    async def test_extract_trade_from_image_success(self, mock_img_open, mock_genai_client):
        """تست استخراج موفق داده‌های پوزیشن از عکس چارت"""
        mock_response = MagicMock()
        mock_response.text = "SYMBOL: EURUSD\nTYPE: BUY\nENTRY: 1.0850\nSL: 1.0820\nTP: 1.0910"

        mock_client_instance = MagicMock()
        mock_client_instance.aio.models.generate_content = AsyncMock(return_value=mock_response)
        mock_genai_client.return_value = mock_client_instance

        with patch("bot_app.services.analysis_service.API_KEY", "test_key"):
            result = await extract_trade_from_image("fake_image_path.png")

            assert "SYMBOL: EURUSD" in result
            assert "TYPE: BUY" in result

    @pytest.mark.asyncio
    @patch("bot_app.services.analysis_service.genai.Client")
    async def test_analyze_trades_with_gemini_success(self, mock_genai_client):
        """تست تحلیل جامع رفتارشناسی معاملات"""
        trades = [{'time': 1, 'symbol': 'EURUSD', 'profit': 100.0}]

        mock_response = MagicMock()
        mock_response.text = "استراتژی شما پایداری خوبی نشان می‌دهد."

        mock_client_instance = MagicMock()
        mock_client_instance.aio.models.generate_content = AsyncMock(return_value=mock_response)
        mock_genai_client.return_value = mock_client_instance

        with patch("bot_app.services.analysis_service.API_KEY", "test_key"), \
             patch("bot_app.services.analysis_service.calculate_trading_metrics") as mock_metrics, \
             patch("bot_app.services.analysis_service.calculate_symbol_breakdown") as mock_breakdown:

            mock_metrics.return_value = {
                'profit_factor': 1.5,
                'max_drawdown': 50.0,
                'payoff_ratio': 2.0,
                'avg_win': 100.0,
                'avg_loss': 50.0
            }
            mock_breakdown.return_value = [{'symbol': 'EURUSD', 'win_rate': 100.0}]

            result = await analyze_trades_with_gemini(trades, "هفتگی")

            assert "استراتژی شما پایداری خوبی نشان می‌دهد." in result