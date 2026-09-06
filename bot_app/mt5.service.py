import rpyc
from decouple import config

# تنظیمات اتصال
MT5_HOST = config("MT5_HOST", default="localhost")
MT5_PORT = config("MT5_PORT", cast=int, default=18812)
MT5_PATH = config("MT5_PATH", default="C:\\Program Files\\MetaTrader 5\\terminal64.exe")

conn = None
mt5 = None


def init_mt5():
    """ایجاد اتصال و initialize متاتریدر"""
    global conn, mt5
    try:
        # اصلاح تابع connect (افزودن دات بین classic و connect)
        conn = rpyc.classic.connect(MT5_HOST, MT5_PORT)
        mt5 = conn.modules['MetaTrader5']

        # پاس دادن مسیر برای جلوگیری از ارور IPC (-10003)
        if not mt5.initialize(path=MT5_PATH):
            print("خطا در initialize متاتریدر:", mt5.last_error())
            shutdown_mt5()  # بستن RPyC در صورت عدم موفقیت initialize
            return False

        return True
    except Exception as e:
        print(f"خطا در برقراری اتصال RPyC: {e}")
        shutdown_mt5()
        return False


def shutdown_mt5():
    """بستن ایمن منابع و اتصال RPyC"""
    global conn, mt5
    if mt5:
        try:
            mt5.shutdown()
        except Exception:
            pass
        mt5 = None

    if conn:
        try:
            conn.close()
        except Exception:
            pass
        conn = None


def get_fx_price(symbol):
    if not init_mt5():
        return None
    try:
        tick = mt5.symbol_info_tick(symbol)
        return tick.bid if tick else None
    finally:
        shutdown_mt5()


def open_position(symbol, action, lot=0.01):
    if not init_mt5():
        return False, "اتصال به متاتریدر برقرار نشد."

    try:
        order_type = mt5.ORDER_TYPE_BUY if action.lower() == "buy" else mt5.ORDER_TYPE_SELL
        tick = mt5.symbol_info_tick(symbol)
        if not tick:
            return False, f"اطلاعات قیمت برای {symbol} دریافت نشد."

        price = tick.ask if action.upper() == 'BUY' else tick.bid

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": float(lot),
            "type": order_type,
            "price": price,
            "magic": 10024,
            "deviation": 20,
            "comment": "Sent from Telegram Bot",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        result = mt5.order_send(request)

        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            err_msg = result.comment if result else "پاسخی از سرور دریافت نشد."
            return False, f"خطا در ثبت معامله: {err_msg}"

        return True, f"✅ معامله {action} روی {symbol} با حجم {lot} ثبت شد."
    finally:
        shutdown_mt5()


def close_all_positions():
    if not init_mt5():
        return "خطا در اتصال به متاتریدر"

    try:
        positions = mt5.positions_get()
        if not positions:
            return "پوزیشن باز یافت نشد."

        closed_count = 0
        for pos in positions:
            action = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
            tick = mt5.symbol_info_tick(pos.symbol)
            if not tick:
                continue

            price = tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask

            req = {
                "action": mt5.TRADE_ACTION_DEAL,
                "position": pos.ticket,
                "symbol": pos.symbol,
                "volume": pos.volume,
                "type": action,
                "price": price,
                "deviation": 20,
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            res = mt5.order_send(req)
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                closed_count += 1

        return f"✅ تعداد {closed_count} پوزیشن بسته شد."
    finally:
        shutdown_mt5()