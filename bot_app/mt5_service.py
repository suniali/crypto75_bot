import logging
from typing import reveal_type
import MetaTrader5 as mt5

# ------------------------------------------------------------------
# Logging Configuration
# ------------------------------------------------------------------
logger = logging.getLogger("mt5_service")
logger.setLevel(logging.INFO)

formatter = logging.Formatter(
    fmt="%(asctime)s | %(levelname)-7s | %(funcName)s:%(lineno)d - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# 1. Console Handler
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

# 2. File Handler
file_handler = logging.FileHandler("mt5_service.log", encoding="utf-8")
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

# ------------------------------------------------------------------
# Constants & Configurations
# ------------------------------------------------------------------
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


# ------------------------------------------------------------------
# Helper Functions
# ------------------------------------------------------------------
def init_mt5() -> bool:
    if not mt5.initialize():
        logger.error("Failed to initialize MT5 connection. Retcode/Error: %s", mt5.last_error())
        return False
    logger.debug("MT5 initialized successfully.")
    return True


def get_forex_price(symbol: str):
    logger.info("Fetching price for symbol: %s", symbol)
    if not init_mt5():
        return None
    try:
        tick = mt5.symbol_info_tick(symbol)
        if not tick:
            logger.warning("Tick info returned None for symbol: %s", symbol)
            return None
        return tick.bid
    finally:
        mt5.shutdown()


# ------------------------------------------------------------------
# Trade Operations
# ------------------------------------------------------------------
def execute_trade(symbol: str, action: str, lot: float = 0.01, sl_pips: int = 0, tp_pips: int = 0):
    logger.info("Initiating trade request - Symbol: %s, Action: %s, Lot: %s, SL_pips: %s, TP_pips: %s",
                symbol, action, lot, sl_pips, tp_pips)

    if not init_mt5():
        return False, ERROR_CANNOT_CONNECT_TO_METATRADER

    try:
        # ۱. فعال‌سازی نماد
        if not mt5.symbol_select(symbol, True):
            logger.warning("Symbol %s not found in Market Watch.", symbol)
            mt5.shutdown()
            return False, (
                f"⚠️ **نماد یافت نشد!**\n\n"
                f"نماد `{symbol}` در لیست Market Watch فعال یا موجود نیست."
            )

        # ۲. دریافت اطلاعات تیک و مشخصات نماد
        tick = mt5.symbol_info_tick(symbol)
        symbol_info = mt5.symbol_info(symbol)

        if tick is None or symbol_info is None:
            logger.error("Failed to fetch tick or symbol_info for %s", symbol)
            mt5.shutdown()
            return False, f"⚠️ **خطا در دریافت قیمت لحظه‌ای نماد `{symbol}`!**"

        point = symbol_info.point
        is_buy = action.upper() == "BUY"
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

        logger.debug("Sending order_send request: %s", request)
        result = mt5.order_send(request)
        mt5.shutdown()

        if result is None:
            logger.error("order_send returned None for symbol %s", symbol)
            return False, "🚨 **خطای غیرمنتظره:** هیچ پاسخی از سرور متاتریدر دریافت نشد."

        if result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error("Trade failed for %s. Retcode: %s, Comment: %s", symbol, result.retcode, result.comment)
            return False, (
                f"🚨 **خطا در ثبت معامله در بروکر!**\n\n"
                f"❌ **علت:** `{result.comment}`\n"
                f"🔢 **کد خطا:** `{result.retcode}`"
            )

        action_icon = "🟢" if is_buy else "🔴"
        logger.info("Trade successfully executed for %s (Ticket: %s, Price: %s)", symbol, result.order, result.price)
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
        logger.exception("Unexpected error during trade execution for %s: %s", symbol, e)
        mt5.shutdown()
        return False, f"🚨 **خطایی در فرآیند اجرای معامله رخ داد:**\n`{e}`"


def close_all_positions():
    logger.info("Request received to close ALL open positions.")
    if not init_mt5():
        return ERROR_CANNOT_CONNECT_TO_METATRADER

    positions = mt5.positions_get()
    if not positions:
        logger.info("No open positions found to close.")
        mt5.shutdown()
        return "📭 **هیچ پوزیشن بازی برای بستن پیدا نشد.**"

    closed_count = 0
    for pos in positions:
        action = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
        tick = mt5.symbol_info_tick(pos.symbol)

        if not tick:
            logger.warning("Could not fetch tick to close position #%s (%s)", pos.ticket, pos.symbol)
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
            "type_filling": mt5.ORDER_FILLING_FOK,
        }
        res = mt5.order_send(req)
        if res and res.retcode == mt5.TRADE_RETCODE_DONE:
            closed_count += 1
            logger.info("Closed position #%s for %s", pos.ticket, pos.symbol)
        else:
            comment = res.comment if res else "No response"
            logger.error("Failed to close position #%s (%s). Reason: %s", pos.ticket, pos.symbol, comment)

    mt5.shutdown()
    logger.info("Completed close_all_positions. Closed %s out of %s positions.", closed_count, len(positions))
    return f"⚡️ **عملیات بستن پوزیشن‌ها به پایان رسید.**\n\n✅ **تعداد پوزیشن‌های بسته شده:** `{closed_count}`"


def close_position_by_ticket(ticket: int) -> tuple[bool, str]:
    logger.info("Attempting to close position ticket: #%s", ticket)
    if not init_mt5():
        return False, "خطا در اتصال به متاتریدر"

    try:
        positions = mt5.positions_get(ticket=ticket)
        if not positions:
            logger.warning("Position #%s not found.", ticket)
            return False, f"پوزیشنی با تیکت {ticket} یافت نشد یا قبلاً بسته شده است."

        position = positions[0]
        symbol = position.symbol
        volume = position.volume
        pos_type = position.type

        tick = mt5.symbol_info_tick(symbol)
        if not tick:
            logger.error("Failed to fetch tick info for %s while closing ticket #%s", symbol, ticket)
            return False, f"امکان دریافت قیمت لحظه‌ای برای {symbol} وجود ندارد."

        if pos_type == mt5.ORDER_TYPE_BUY:
            order_type = mt5.ORDER_TYPE_SELL
            price = tick.bid
        elif pos_type == mt5.ORDER_TYPE_SELL:
            order_type = mt5.ORDER_TYPE_BUY
            price = tick.ask
        else:
            logger.error("Unknown position type %s for ticket #%s", pos_type, ticket)
            return False, "نوع پوزیشن معتبر نیست."

        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "position": ticket,
            "symbol": symbol,
            "volume": volume,
            "type": order_type,
            "price": price,
            "deviation": 20,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_FOK,
        }

        result = mt5.order_send(req)

        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            comment = result.comment if result else "No response"
            retcode = result.retcode if result else "None"
            logger.error("Failed to close ticket #%s. Retcode: %s, Comment: %s", ticket, retcode, comment)
            return False, f"خطا در بستن پوزیشن: {comment} (کد: {retcode})"

        logger.info("Successfully closed ticket #%s at price %s", ticket, result.price)
        return True, f"✅ پوزیشن `{ticket}` با موفقیت در قیمت `{result.price}` بسته شد."

    except Exception as e:
        logger.exception("Unexpected exception while closing ticket #%s: %s", ticket, e)
        return False, f"خطای پیش‌بینی نشده: {e}"
    finally:
        mt5.shutdown()


# ------------------------------------------------------------------
# Market Data & Analysis Queries
# ------------------------------------------------------------------
def check_symbol_info(symbol: str):
    logger.info("Checking symbol info for: %s", symbol)
    try:
        if not init_mt5():
            logger.error("Connection failed during check_symbol_info for %s", symbol)
            return False

        selected = mt5.symbol_select(symbol, True)
        if not selected:
            logger.warning("Symbol %s is not available or selected. Searching for alternatives...", symbol)
            symbols = mt5.symbols_get()
            if symbols:
                sample_symbols = [s.name for s in symbols[:3]]
                return sample_symbols
            return False

        return True

    except Exception as e:
        logger.exception("Exception in check_symbol_info for %s: %s", symbol, e)
        return False
    finally:
        mt5.shutdown()


def get_open_positions():
    logger.info("Fetching open positions list...")
    if not init_mt5():
        return False, "ERROR_CONNECT"

    try:
        positions = mt5.positions_get()
        if positions is None:
            logger.error("positions_get() returned None.")
            return False, "ERROR_FETCH"

        positions_list = []
        for pos in positions:
            positions_list.append({
                "ticket": pos.ticket,
                "symbol": pos.symbol,
                "type": "BUY" if pos.type == mt5.ORDER_TYPE_BUY else "SELL",
                "volume": pos.volume,
                "price_open": pos.price_open,
                "price_current": pos.price_current,
                "sl": pos.sl,
                "tp": pos.tp,
                "profit": pos.profit,
            })

        logger.info("Retrieved %s open positions.", len(positions_list))
        return True, positions_list

    except Exception as e:
        logger.exception("Exception during get_open_positions: %s", e)
        return False, str(e)
    finally:
        mt5.shutdown()


def get_data_for_rsi(symbol: str, timeframe: str):
    logger.info("Fetching RSI rates for Symbol: %s, Timeframe: %s", symbol, timeframe)
    if not init_mt5():
        return False, ERROR_CANNOT_CONNECT_TO_METATRADER

    try:
        interval = TIMEFRAME_TO_MT5_TIMEFRAME.get(timeframe, mt5.TIMEFRAME_M30)
        rates = mt5.copy_rates_from_pos(symbol, interval, 0, 100)

        if rates is None or len(rates) == 0:
            logger.warning("No candle data returned for %s (%s)", symbol, timeframe)
            return None, f"⚠️ **هیچ کندلی برای نماد `{symbol}` دریافت نشد!**"

        logger.info("Successfully fetched %s candles for %s", len(rates), symbol)
        return rates, "✅ **داده‌های کندل‌ها با موفقیت دریافت شد.**"
    except Exception as e:
        logger.exception("Exception during get_data_for_rsi (%s): %s", symbol, e)
        return False, f"🚨 **خطا در دریافت داده‌های RSI:**\n`{e}`"
    finally:
        mt5.shutdown()