from typing import reveal_type

import MetaTrader5 as mt5

ERROR_CANNOT_CONNECT_TO_METATRADER="❌ اتصال به MetaTrader5 برقرار نشد."

TIMEFRAME_TO_MT5_TIMEFRAME = {
    "1m": mt5.TIMEFRAME_M1,
    "5m": mt5.TIMEFRAME_M5,
    "15m": mt5.TIMEFRAME_M15,
    "30m": mt5.TIMEFRAME_M30,
    "1h": mt5.TIMEFRAME_H1,
    "4h": mt5.TIMEFRAME_H4,
    "1d": mt5.TIMEFRAME_D1,
}

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


def get_open_positions():
    if not init_mt5():
        return False, ERROR_CANNOT_CONNECT_TO_METATRADER

    try:
        # دریافت تمامی پوزیشن‌های باز
        positions = mt5.positions_get()
        mt5.shutdown()

        if positions is None:
            return False, "خطا در دریافت لیست پوزیشن‌ها."

        if len(positions) == 0:
            return True, "📊 هیچ پوزیشن بازی یافت نشد."

        msg = "📋 **لیست پوزیشن‌های باز:**\n\n"
        total_profit = 0.0

        for pos in positions:
            # تشخیص نوع معامله (BUY یا SELL)
            trade_type = "🟢 BUY" if pos.type == mt5.ORDER_TYPE_BUY else "🔴 SELL"

            # محاسبه سود/زیان کل
            total_profit += pos.profit

            # تعیین ایموجی سود یا زیان
            profit_emoji = "🟢" if pos.profit >= 0 else "🔴"

            msg += (
                f"🔹 **نماد:** `{pos.symbol}`\n"
                f"▫️ **نوع:** {trade_type}\n"
                f"▫️ **حجم:** {pos.volume}\n"
                f"▫️ **قیمت ورود:** {pos.price_open}\n"
                f"▫️ **قیمت فعلی:** {pos.price_current}\n"
                f"▫️ **حد ضرر (SL):** {pos.sl if pos.sl > 0 else 'تنظیم نشده'}\n"
                f"▫️ **حد سود (TP):** {pos.tp if pos.tp > 0 else 'تنظیم نشده'}\n"
                f"▫️ **سود/زیان:** {profit_emoji} `${pos.profit:.2f}`\n"
                f"▫️ **تیکت:** `{pos.ticket}`\n"
                f"───────────────\n"
            )

        total_emoji = "🟩" if total_profit >= 0 else "🟥"
        msg += f"\n{total_emoji} **مجموع سود/زیان کل:** `${total_profit:.2f}`"

        return True, msg

    except Exception as e:
        mt5.shutdown()
        return False, f"خطا در دریافت پوزیشن‌ها: {e}"

def get_data_for_rsi(symbol,timeframe):
    if not init_mt5():
        return False, ERROR_CANNOT_CONNECT_TO_METATRADER

    try:
        interval = TIMEFRAME_TO_MT5_TIMEFRAME.get(timeframe,mt5.TIMEFRAME_M30)
        rates=mt5.copy_rates_from_pos(symbol,interval,0,100)

        if rates is None or len(rates) == 0:
            return None, "دیتایی یافت نشد!"

        return rates, "دیتا با موفقیت دریافت شد."
    except Exception as e:
        return False, f" خطا در دریافت داده ها ! {e}"
    finally:
        mt5.shutdown()