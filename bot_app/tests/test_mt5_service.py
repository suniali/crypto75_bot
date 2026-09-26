import pytest
from unittest.mock import patch, MagicMock
from bot_app.services.mt5_service import (
    get_forex_price,
    execute_trade,
    close_position,
    set_break_even,
    ERROR_CANNOT_CONNECT_TO_METATRADER,
)


@pytest.fixture(autouse=True)
def mock_mt5_session():
    """Mock mt5_session context manager to return True for MT5 availability."""
    with patch("bot_app.services.mt5_service.mt5_session") as mock_session:
        mock_ctx = MagicMock()
        mock_ctx.__enter__.return_value = True
        mock_ctx.__exit__.return_value = False
        mock_session.return_value = mock_ctx
        yield mock_session


class TestMT5Service:

    @patch("bot_app.services.mt5_service.mt5")
    def test_get_forex_price_success(self, mock_mt5):
        """تست دریافت موفق قیمت یک نماد"""
        mock_symbol_info = MagicMock()
        mock_symbol_info.select = True
        mock_mt5.symbol_info.return_value = mock_symbol_info

        mock_tick = MagicMock()
        mock_tick.bid = 1.0850
        mock_mt5.symbol_info_tick.return_value = mock_tick

        price = get_forex_price("EURUSD")
        assert price == 1.0850
        mock_mt5.symbol_info_tick.assert_called_once_with("EURUSD")

    @patch("bot_app.services.mt5_service.mt5")
    def test_get_forex_price_symbol_not_found(self, mock_mt5):
        """تست عدم یافتن نماد در متاتریدر"""
        mock_mt5.symbol_info.return_value = None

        price = get_forex_price("INVALID_SYMBOL")
        assert price is None

    @patch("bot_app.services.mt5_service.mt5")
    def test_execute_trade_market_buy_success(self, mock_mt5):
        """تست اجرای موفق سفارش خرید مارکت (Market Buy)"""
        mock_mt5.symbol_select.return_value = True

        mock_tick = MagicMock()
        mock_tick.ask = 2000.00
        mock_tick.bid = 1999.50
        mock_mt5.symbol_info_tick.return_value = mock_tick

        mock_info = MagicMock()
        mock_info.digits = 2
        mock_mt5.symbol_info.return_value = mock_info

        mock_result = MagicMock()
        mock_result.retcode = mock_mt5.TRADE_RETCODE_DONE
        mock_mt5.order_send.return_value = mock_result

        success, msg, rr = execute_trade(
            symbol="XAUUSD",
            action="BUY",
            lot=0.1,
            entry_price="MARKET",
            sl_price=1990.00,
            tp_price=2020.00,
            telegram_id=987654321
        )

        assert success is True
        assert "سفارش با موفقیت ثبت شد" in msg
        assert rr == 2.0  # Risk: 10, Reward: 20 => R/R = 2.0
        mock_mt5.order_send.assert_called_once()

    @patch("bot_app.services.mt5_service.mt5")
    def test_execute_trade_broker_rejection(self, mock_mt5):
        """تست رد شدن معامله توسط بروکر"""
        mock_mt5.symbol_select.return_value = True
        mock_mt5.symbol_info_tick.return_value = MagicMock(ask=1.1000, bid=1.0998)
        mock_mt5.symbol_info.return_value = MagicMock(digits=5)

        mock_result = MagicMock()
        mock_result.retcode = 10013  # Invalid request
        mock_result.comment = "Invalid stops"
        mock_mt5.order_send.return_value = mock_result

        success, msg, rr = execute_trade(
            symbol="EURUSD", action="BUY", lot=0.01, entry_price="MARKET"
        )

        assert success is False
        assert "خطا در ثبت معامله در بروکر" in msg

    @patch("bot_app.services.mt5_service.mt5")
    def test_close_position_success(self, mock_mt5):
        """تست بستن کامل یک پوزیشن باز"""
        mock_pos = MagicMock()
        mock_pos.symbol = "EURUSD"
        mock_pos.type = mock_mt5.ORDER_TYPE_BUY
        mock_pos.volume = 0.5
        mock_mt5.positions_get.return_value = [mock_pos]

        mock_mt5.symbol_info_tick.return_value = MagicMock(bid=1.0860, ask=1.0862)
        mock_mt5.symbol_info.return_value = MagicMock()

        mock_result = MagicMock()
        mock_result.retcode = mock_mt5.TRADE_RETCODE_DONE
        mock_mt5.order_send.return_value = mock_result

        success, msg = close_position(ticket=123456)
        assert success is True
        assert "با موفقیت بسته شد" in msg

    @patch("bot_app.services.mt5_service.mt5")
    def test_set_break_even_already_set(self, mock_mt5):
        """تست عدم تغییر حد ضرر در صورتی که از قبل روی نقطه ورود باشد"""
        mock_pos = MagicMock()
        mock_pos.price_open = 1.0850
        mock_pos.sl = 1.0850  # SL is already open price
        mock_mt5.positions_get.return_value = [mock_pos]

        success, msg = set_break_even(ticket=654321)
        assert success is True
        assert "از قبل روی نقطه ورود" in msg
        mock_mt5.order_send.assert_not_called()