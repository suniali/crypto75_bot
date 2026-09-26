import pytest
import MetaTrader5 as mt5
from bot_app.services.mt5_service import (
    start_mt5_connection,
    stop_mt5_connection,
    get_forex_price,
    get_rates_data,
    get_open_positions,
    get_market_watch_symbols,
    execute_trade,
    close_position,
)


@pytest.fixture(scope="module", autouse=True)
def setup_teardown_real_mt5():
    """مدیریت اتصال و قطع اتصال واقعی به متاتریدر"""
    connected = start_mt5_connection()
    if not connected:
        pytest.skip(
            "⚠️ امکان اتصال به MetaTrader 5 وجود ندارد. تست‌های یکپارچگی رد شدند.",
            allow_module_level=True,
        )
    yield
    stop_mt5_connection()


@pytest.fixture(scope="module")
def active_symbol():
    """یافتن خودکار نماد کریپتو (مانند BTCUSD) برای جلوگیری از تعطیلی بازار در پایان هفته"""
    symbols = get_market_watch_symbols()
    if not symbols:
        pytest.skip("هیچ نمادی در Market Watch متاتریدر یافت نشد.")

    # اولویت با نماد بیت‌کوین (BTCUSD, BTCUSD-ECN, BTCUSDm و ...)
    crypto_symbol = next((s for s in symbols if "BTC" in s.upper()), None)

    if crypto_symbol:
        target_symbol = crypto_symbol
    else:
        target_symbol = symbols[0]

    mt5.symbol_select(target_symbol, True)
    return target_symbol


class TestMT5ServiceIntegration:

    def test_real_get_market_watch_symbols(self):
        """تست دریافت لیست نمادهای فعال در Market Watch"""
        symbols = get_market_watch_symbols()
        assert isinstance(symbols, list)
        assert len(symbols) > 0, "لیست نمادهای Market Watch خالی است."
        print(f"\n[Real MT5] نمادهای فعال در بروکر شما: {symbols[:5]}")

    def test_real_get_forex_price(self, active_symbol):
        """تست دریافت قیمت واقعی برای نماد فعال"""
        price = get_forex_price(active_symbol)
        assert price is not None, f"امکان دریافت قیمت برای نماد {active_symbol} وجود ندارد."
        assert price > 0, "قیمت باید یک عدد مثبت باشد."
        print(f"\n[Real MT5] قیمت لحظه‌ای {active_symbol}: {price}")

    def test_real_get_rates_data(self, active_symbol):
        """تست دریافت داده‌های کندلی (100 کندل اخیر تایم‌فریم 15 دقیقه‌ای)"""
        rates, msg = get_rates_data(active_symbol, "15m")
        assert rates is not None and rates is not False, f"خطا در دریافت کندل‌ها: {msg}"
        assert len(rates) == 100, f"تعداد کندل‌های دریافتی {len(rates)} است."
        print(f"\n[Real MT5] نماد: {active_symbol} | آخرین قیمت بسته‌شدن کندل: {rates[-1]['close']}")

    def test_real_get_open_positions(self):
        """تست دریافت لیست پوزیشن‌های باز واقعی"""
        ok, positions = get_open_positions()
        assert ok is True
        assert isinstance(positions, list)
        print(f"\n[Real MT5] تعداد پوزیشن‌های باز فعلی: {len(positions)}")

    def test_real_execute_and_close_trade_lifecycle(self, active_symbol):
        """تست چرخه کامل معامله دمو در متاتریدر روی نماد فعال (مانند BTCUSD)"""
        current_price = get_forex_price(active_symbol)
        assert current_price is not None, "امکان دریافت قیمت جهت ثبت معامله وجود ندارد."

        info = mt5.symbol_info(active_symbol)
        point = info.point if info else 0.0001

        # ۳۰۰ پوینت حد ضرر و حد سود
        sl_price = round(current_price - (300 * point), info.digits)
        tp_price = round(current_price + (300 * point), info.digits)

        success, msg, rr = execute_trade(
            symbol=active_symbol,
            action="BUY",
            lot=0.01,
            entry_price="MARKET",
            sl_price=sl_price,
            tp_price=tp_price,
            telegram_id=99999999,
        )

        # در صورت بروز خطای تعطیلی بازار
        if not success and ("10018" in msg or "market closed" in msg.lower()):
            pytest.skip(f"بازار نماد {active_symbol} در حال حاضر تعطیل است.")

        assert success is True, f"ثبت معامله با شکست مواجه شد: {msg}"

        ok, positions = get_open_positions()
        assert ok is True

        test_position = next((p for p in positions if p["symbol"] == active_symbol and p["volume"] == 0.01), None)
        assert test_position is not None, "پوزیشن ثبت شده در لیست پوزیشن‌ها یافت نشد."

        close_success, close_msg = close_position(ticket=test_position["ticket"])
        assert close_success is True, f"بستن پوزیشن با خطا مواجه شد: {close_msg}"
        print(f"\n[Real MT5] معامله تست روی {active_symbol} با تیکت {test_position['ticket']} باز و بسته شد.")