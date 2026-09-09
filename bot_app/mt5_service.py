from typing import reveal_type
import MetaTrader5 as mt5

ERROR_CANNOT_CONNECT_TO_METATRADER = (
    "🚨 **خطا در اتصال به MetaTrader 5!**\n\n"
    "❌ امکان برقراری ارتباط با نرم‌افزار متاتریدر وجود ندارد.\n"
    "لطفاً مطمئن شوید MT5 باز و به حساب متصل است."
)

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
        print("❌ [MT5] Failed to connect.")
        return False

    print("✅ [MT5] Connected successfully.")
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
            return False, (
                f"⚠️ **نماد یافت نشد!**\n\n"
                f"نماد `{symbol}` در لیست Market Watch فعال یا موجود نیست."
            )

        # ۲. دریافت اطلاعات تیک و مشخصات نماد
        tick = mt5.symbol_info_tick(symbol)
        symbol_info = mt5.symbol_info(symbol)

        if tick is None or symbol_info is None:
            mt5.shutdown()
            return False, f"⚠️ **خطا در دریافت قیمت لحظه‌ای نماد `{symbol}`!**"

        point = symbol_info.point
        is_buy = action.upper() == 'BUY'
        trade_type = mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL
        price = tick.ask if is_buy else tick.bid

        # ۳. محاسبه قیمت SL و TP
        sl = 0.0
        tp = 0.0

        if is_buy:
            if sl_pips > 0:
                sl = price - (sl_pips * point * 10)
            if tp_pips > 0:
                tp = price + (tp_pips * point * 10)
        else:  # SELL
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
            "sl": round(sl, symbol_info.digits),
            "tp": round(tp, symbol_info.digits),
            "deviation": 20,
            "comment": "Sent from Telegram Bot",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_FOK,
        }

        result = mt5.order_send(request)
        mt5.shutdown()

        if result is None:
            return False, "🚨 **خطای غیرمنتظره:** هیچ پاسخی از سرور متاتریدر دریافت نشد."

        if result.retcode != mt5.TRADE_RETCODE_DONE:
            return False, (
                f"🚨 **خطا در ثبت معامله در بروکر!**\n\n"
                f"❌ **علت:** `{result.comment}`\n"
                f"🔢 **کد خطا:** `{result.retcode}`"
            )

        action_icon = "🟢" if is_buy else "🔴"
        return True, (
            f"🎯 **معامله با موفقیت اجرا شد**\n\n"
            f"📌 **نماد:** `{symbol}`\n"
            f"📊 **نوع معامله:** {action_icon} `{action.upper()}`\n"
            f"📦 **حجم (Lot):** `{lot}`\n"
            f"💵 **قیمت ورود:** `{price}`\n"
            f"🛑 **حد زیان (SL):** `{sl_pips} pips` (`{round(sl, symbol_info.digits)}`)\n"
            f"🎯 **حد سود (TP):** `{tp_pips} pips` (`{round(tp, symbol_info.digits)}`)"
        )

    except Exception as e:
        mt5.shutdown()
        return False, f"🚨 **خطایی در فرآیند اجرای معامله رخ داد:**\n`{e}`"


def close_all_positions():
    if not init_mt5():
        return ERROR_CANNOT_CONNECT_TO_METATRADER

    positions = mt5.positions_get()
    if not positions:
        mt5.shutdown()
        return "📭 **هیچ پوزیشن بازی برای بستن پیدا نشد.**"

    closed_count = 0
    for pos in positions:
        action = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
        price = (
            mt5.symbol_info_tick(pos.symbol).bid
            if pos.type == mt5.ORDER_TYPE_BUY
            else mt5.symbol_info_tick(pos.symbol).ask
        )

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
        if res and res.retcode == mt5.TRADE_RETCODE_DONE:
            closed_count += 1

    mt5.shutdown()
    return f"⚡️ **عملیات بستن پوزیشن‌ها به پایان رسید.**\n\n✅ **تعداد پوزیشن‌های بسته شده:** `{closed_count}`"


def check_symbol_info(symbol):
    try:
        if not init_mt5():
            print(ERROR_CANNOT_CONNECT_TO_METATRADER)
            return False

        selected = mt5.symbol_select(symbol, True)
        if not selected:
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
        positions = mt5.positions_get()
        mt5.shutdown()

        if positions is None:
            return False, "❌ **خطا در دریافت لیست پوزیشن‌ها از سرور.**"

        if len(positions) == 0:
            return True, "📊 **هیچ پوزیشن بازی در حال حاضر وجود ندارد.**"

        msg = "📋 **لیست پوزیشن‌های فعال:**\n\n"
        total_profit = 0.0

        for pos in positions:
            trade_type = "🟢 BUY" if pos.type == mt5.ORDER_TYPE_BUY else "🔴 SELL"
            total_profit += pos.profit
            profit_emoji = "🟢" if pos.profit >= 0 else "🔴"

            sl_display = f"`{pos.sl}`" if pos.sl > 0 else "❌ _تنظیم نشده_"
            tp_display = f"`{pos.tp}`" if pos.tp > 0 else "❌ _تنظیم نشده_"

            msg += (
                f"🔹 **نماد:** `{pos.symbol}` | 🎫 `{pos.ticket}`\n"
                f"├ 📊 **نوع:** {trade_type} | 📦 **حجم:** `{pos.volume}`\n"
                f"├ 💵 **ورود:** `{pos.price_open}` ➔ **فعلی:** `{pos.price_current}`\n"
                f"├ 🛑 **SL:** {sl_display}\n"
                f"├ 🎯 **TP:** {tp_display}\n"
                f"└ 💵 **سود/زیان:** {profit_emoji} **`${pos.profit:,.2f}`**\n"
                f"───────────────\n"
            )

        total_emoji = "🟩" if total_profit >= 0 else "🟥"
        msg += f"\n{total_emoji} **مجموع برآیند معاملات:** **`${total_profit:,.2f}`**"

        return True, msg

    except Exception as e:
        mt5.shutdown()
        return False, f"🚨 **خطا در دریافت لیست پوزیشن‌ها:**\n`{e}`"


def get_data_for_rsi(symbol, timeframe):
    if not init_mt5():
        return False, ERROR_CANNOT_CONNECT_TO_METATRADER

    try:
        interval = TIMEFRAME_TO_MT5_TIMEFRAME.get(timeframe, mt5.TIMEFRAME_M30)
        rates = mt5.copy_rates_from_pos(symbol, interval, 0, 100)

        if rates is None or len(rates) == 0:
            return None, f"⚠️ **هیچ کندلی برای نماد `{symbol}` دریافت نشد!**"

        return rates, "✅ **داده‌های کندل‌ها با موفقیت دریافت شد.**"
    except Exception as e:
        return False, f"🚨 **خطا در دریافت داده‌های RSI:**\n`{e}`"
    finally:
        mt5.shutdown()