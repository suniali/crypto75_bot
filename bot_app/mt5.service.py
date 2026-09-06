from decouple import config
from mt5linux import MetaTrader5

# تنظیمات اتصال به سرور mt5linux
MT5_HOST = config("MT5_HOST",default= "localhost")  # آدرس آی‌پی سیستم ویندوزی یا لینوکس
MT5_PORT = config("MT5_PORT",cast=int,default= 18812)   # پورت پیش‌فرض mt5linux

# ساخت شیء متاتریدر
mt5 = MetaTrader5(host=MT5_HOST, port=MT5_PORT)

def init_mt5():
    if not mt5.initialize():
        print("خطا در اتصال به متاتریدر 5")
        return False

    return True

def get_fx_price(symbol):
    if not init_mt5():
        return None
    tick = mt5.symbol_info_tick(symbol)
    mt5.shutdown()
    return tick.bid if tick else None

def open_position(symbol,action,lot=0.01):
    if not init_mt5():
        return False,"اتصال به متاتریدر برقرار نشد."

    order_type=mt5.ORDER_TYPE_BUY if action.lower()=="buy" else mt5.ORDER_TYPE_SELL
    tick = mt5.symbol_info_tick(symbol)
    if not tick:
        mt5.shutdown()
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

    result=mt5.order_send(request)
    mt5.shutdown()

    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        err_msg = result.comment if result else "پاسخی از سرور دریافت نشد."
        return False, f"خطا در ثبت معامله: {err_msg}"

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
        res=mt5.order_send(req)
        if res and res.retcode == mt5.TRADE_RETCODE_DONE:
            closed_count += 1

    mt5.shutdown()
    return f"✅ تعداد {closed_count} پوزیشن بسته شد."