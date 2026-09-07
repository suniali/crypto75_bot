import MetaTrader5 as mt5


def init_mt5():
    if not mt5.initialize():
        print("خطا در اتصال به MetaTrader5")
        return False
    return True


def get_forex_price(symbol):
    if not init_mt5():
        return None
    tick = mt5.symbol_info_tick(symbol)
    mt5.shutdown()
    return tick.bid if tick else None


def execute_trade(symbol, action, lot=0.01):
    if not init_mt5():
        return False, "اتصال به متاتریدر برقرار نشد."

    trade_type = mt5.ORDER_TYPE_BUY if action.upper() == 'BUY' else mt5.ORDER_TYPE_SELL
    price = mt5.symbol_info_tick(symbol).ask if action.upper() == 'BUY' else mt5.symbol_info_tick(symbol).bid

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": lot,
        "type": trade_type,
        "price": price,
        "deviation": 20,
        "magic": 10024,
        "comment": "Sent from Telegram Bot",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)
    mt5.shutdown()
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        return False, f"خطا در ثبت معامله: {result.comment}"
    return True, f"✅ معامله {action} روی {symbol} با حجم {lot} ثبت شد."


def close_all_positions():
    if not init_mt5():
        return "خطا در اتصال به متاتریدر"

    positions = mt5.positions_get()
    if not positions:
        mt5.shutdown()
        return "پوزیشن باز یافت نشد."

    closed_count = 0
    for pos in positions:
        action = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
        price = mt5.symbol_info_tick(pos.symbol).bid if pos.type == mt5.ORDER_TYPE_BUY else mt5.symbol_info_tick(
            pos.symbol).ask

        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "position": pos.ticket,
            "symbol": pos.symbol,
            "volume": pos.volume,
            "type": action,
            "price": price,
            "deviation": 20,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_FOK,
        }
        res = mt5.order_send(req)
        if res.retcode == mt5.TRADE_RETCODE_DONE:
            closed_count += 1

    mt5.shutdown()
    return f"✅ تعداد {closed_count} پوزیشن بسته شد."


def check_symbol_info(symbol):
    try:
        # ۱. مقداردهی اولیه و اتصال به متاتریدر ۵
        if not init_mt5():
            print("❌ اتصال به MetaTrader5 برقرار نشد.")
            return False

        # ۲. فعال‌سازی نماد در Market Watch
        selected = mt5.symbol_select(symbol, True)
        if not selected:
            # لیست کردن چند نماد برای بررسی نام‌گذاری بروکر
            symbols = mt5.symbols_get()
            if symbols:
                sample_symbols = [s.name for s in symbols[:3]]
                return sample_symbols
            return False

        return True

    except Exception as e:
        print(f"❌ Exception during debug: {e}")
        return False
    finally:
        mt5.shutdown()