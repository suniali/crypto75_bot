import MetaTrader5 as mt5

ERROR_CANNOT_CONNECT_TO_METATRADER="❌ اتصال به MetaTrader5 برقرار نشد."

def init_mt5():
    if not mt5.initialize():
        print(ERROR_CANNOT_CONNECT_TO_METATRADER)
        return False

    print("✅ اتصال به متاتریدر موفق بود.")
    return True


def get_forex_price(symbol):
    if not init_mt5():
        return None
    tick = mt5.symbol_info_tick(symbol)
    mt5.shutdown()
    return tick.bid if tick else None


def execute_trade(symbol, action, lot=0.01, sl_pips=0, tp_pips=0):
    if not init_mt5():
        return False, ERROR_CANNOT_CONNECT_TO_METATRADER

    try:
        # ۱. فعال‌سازی نماد
        if not mt5.symbol_select(symbol, True):
            mt5.shutdown()
            return False, f"خطا: نماد '{symbol}' در Market Watch پیدا نشد."

        # ۲. دریافت اطلاعات تیک و مشخصات نماد (برای محاسبه point)
        tick = mt5.symbol_info_tick(symbol)
        symbol_info = mt5.symbol_info(symbol)

        if tick is None or symbol_info is None:
            mt5.shutdown()
            return False, f"خطا: اطلاعات نماد '{symbol}' دریافت نشد."

        # اندازه هر پیپ یا پوینت نماد (مثلاً 0.00001 یا 0.001)
        point = symbol_info.point

        is_buy = action.upper() == 'BUY'
        trade_type = mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL
        price = tick.ask if is_buy else tick.bid

        # ۳. محاسبه قیمت SL و TP
        sl = 0.0
        tp = 0.0

        if is_buy:
            if sl_pips > 0:
                sl = price - (sl_pips * point * 10)  # ضرب در 10 برای تبدیل پیپ به پوینت
            if tp_pips > 0:
                tp = price + (tp_pips * point * 10)
        else: # SELL
            if sl_pips > 0:
                sl = price + (sl_pips * point * 10)
            if tp_pips > 0:
                tp = price - (tp_pips * point * 10)

        # ۴. تنظیم ساختار درخواست معامله
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": float(lot),
            "type": trade_type,
            "price": price,
            "sl": round(sl, symbol_info.digits),  # گرد کردن قیمت بر اساس اعشار نماد
            "tp": round(tp, symbol_info.digits),
            "deviation": 20,
            "comment": "Sent from Telegram Bot",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_FOK,
        }

        result = mt5.order_send(request)
        mt5.shutdown()

        if result is None:
            return False, "خطا: هیچ پاسخی از متاتریدر دریافت نشد."

        if result.retcode != mt5.TRADE_RETCODE_DONE:
            return False, f"خطا در ثبت معامله: {result.comment} (کد: {result.retcode})"

        return True, f"✅ معامله {action} روی {symbol} با حجم {lot}\n🎯 حد سود: {tp_pips} پیپ\n🛑 حد ضرر: {sl_pips} پیپ ثبت شد."

    except Exception as e:
        mt5.shutdown()
        return False, f"خطا در اجرای معامله: {e}"

def close_all_positions():
    if not init_mt5():
        return ERROR_CANNOT_CONNECT_TO_METATRADER

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
            print(ERROR_CANNOT_CONNECT_TO_METATRADER)
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