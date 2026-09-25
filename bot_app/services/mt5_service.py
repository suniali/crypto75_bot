import threading
import logging
from logging.handlers import RotatingFileHandler
import MetaTrader5 as mt5
from datetime import datetime, timedelta, timezone

# ------------------------------------------------------------------
# Logging Configuration
# ------------------------------------------------------------------
logger = logging.getLogger("mt5_service")
logger.setLevel(logging.INFO)

formatter = logging.Formatter(
    fmt="%(asctime)s | %(levelname)-7s | %(funcName)s:%(lineno)d - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

if not logger.handlers:
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    file_handler = RotatingFileHandler(
        "mt5_service.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8"
    )
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
# Connection Management (مرکز مدیریت اتصال)
# ------------------------------------------------------------------
_mt5_lock = threading.Lock()
_mt5_connected = False


def start_mt5_connection() -> bool:
    """فراخوانی تک‌باره هنگام بالا آمدن ربات/برنامه"""
    global _mt5_connected
    with _mt5_lock:
        if not mt5.initialize():
            logger.critical("Failed to initialize MT5 on startup. Error: %s", mt5.last_error())
            _mt5_connected = False
            return False
        _mt5_connected = True
        logger.info("MT5 connection established successfully at startup.")
        return True


def stop_mt5_connection():
    """فراخوانی هنگام shutdown برنامه"""
    global _mt5_connected
    with _mt5_lock:
        mt5.shutdown()
        _mt5_connected = False
        logger.info("MT5 connection closed (application shutdown).")


def _ensure_connected() -> bool:
    """بررسی و بازیابی خودکار اتصال متاتریدر"""
    global _mt5_connected
    if mt5.terminal_info() is not None:
        return True

    logger.warning("MT5 connection lost. Attempting to reconnect...")
    if mt5.initialize():
        _mt5_connected = True
        logger.info("MT5 reconnected successfully.")
        return True

    logger.error("MT5 reconnect attempt failed. Error: %s", mt5.last_error())
    _mt5_connected = False
    return False


class mt5_session:
    def __enter__(self):
        _mt5_lock.acquire()
        return _ensure_connected()

    def __exit__(self, exc_type, exc_val, exc_tb):
        _mt5_lock.release()
        return False


# ------------------------------------------------------------------
# Helper Functions
# ------------------------------------------------------------------

def _get_filling_mode(symbol_info) -> int:
    """تشخیص هوشمند Filling Mode بر اساس قوانین بروکر"""
    filling_flags = symbol_info.filling_mode
    if filling_flags & mt5.ORDER_FILLING_FOK:
        return mt5.ORDER_FILLING_FOK
    elif filling_flags & mt5.ORDER_FILLING_IOC:
        return mt5.ORDER_FILLING_IOC
    return mt5.ORDER_FILLING_RETURN


def get_forex_price(symbol: str) -> float | None:
    """دریافت قیمت لحظه‌ای Bid از متاتریدر ۵"""
    with mt5_session() as ok:
        if not ok:
            logger.error("Failed to get forex price for %s, error code: %s", symbol, mt5.last_error())
            return None

        symbol_info = mt5.symbol_info(symbol)
        if symbol_info is None:
            logger.warning("Symbol %s not found in MetaTrader 5", symbol)
            return None

        if not symbol_info.select:
            if not mt5.symbol_select(symbol, True):
                logger.error("Failed to select symbol %s in Market Watch", symbol)
                return None

        tick = mt5.symbol_info_tick(symbol)
        if tick is None or tick.bid == 0:
            logger.warning("Failed to get tick for %s, error code: %s", symbol, mt5.last_error())
            return None

        return float(tick.bid)


def get_rates_data(symbol: str, timeframe: str):
    """دریافت کندل‌های اخیر نماد برای ترسیم چارت یا تحلیل تکنیکال"""
    logger.info("Fetching Data rates for Symbol: %s, Timeframe: %s", symbol, timeframe)

    with mt5_session() as ok:
        if not ok:
            logger.error("Failed to fetch data rates for %s, error code: %s", symbol, mt5.last_error())
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
            logger.exception("Exception during get_rates_data (%s): %s", symbol, e)
            return False, f"🚨 **خطا در دریافت داده‌ها:**\n`{e}`"


# ------------------------------------------------------------------
# Trade Operations
# ------------------------------------------------------------------

def execute_trade(
    symbol: str,
    action: str,
    lot: float = 0.01,
    entry_price="MARKET",
    sl_price: float = 0.0,
    tp_price: float = 0.0,
    telegram_id: int | None = None,
    username: str | None = None
):
    """اجرا یا ثبت سفارش معاملاتی (Market / Pending) همراه با ثبت مشخصات کاربر تلگرام"""
    logger.info("Initiating trade - User: %s (%s), Symbol: %s, Action: %s, Lot: %s, Price: %s, SL: %s, TP: %s",
                telegram_id, username, symbol, action, lot, entry_price, sl_price, tp_price)

    with mt5_session() as ok:
        if not ok:
            return False, ERROR_CANNOT_CONNECT_TO_METATRADER, None

        try:
            if not mt5.symbol_select(symbol, True):
                return False, f"⚠️ **نماد `{symbol}` در لیست Market Watch فعال نیست.**", None

            tick = mt5.symbol_info_tick(symbol)
            symbol_info = mt5.symbol_info(symbol)

            if tick is None or symbol_info is None:
                return False, f"⚠️ **خطا در دریافت قیمت لحظه‌ای نماد `{symbol}`!**", None

            is_buy = action.upper() == "BUY"

            if entry_price == "MARKET":
                trade_action_type = mt5.TRADE_ACTION_DEAL
                trade_type = mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL
                execution_price = tick.ask if is_buy else tick.bid
            else:
                trade_action_type = mt5.TRADE_ACTION_PENDING
                execution_price = float(entry_price)
                current_market_price = tick.ask if is_buy else tick.bid

                if is_buy:
                    trade_type = mt5.ORDER_TYPE_BUY_LIMIT if execution_price < current_market_price else mt5.ORDER_TYPE_BUY_STOP
                else:
                    trade_type = mt5.ORDER_TYPE_SELL_LIMIT if execution_price > current_market_price else mt5.ORDER_TYPE_SELL_STOP

            rr_ratio = None
            if sl_price > 0 and tp_price > 0:
                risk = abs(execution_price - sl_price)
                reward = abs(tp_price - execution_price)
                if risk > 0:
                    rr_ratio = reward / risk

            # ساخت کامنت متاتریدر با شناسه کاربر (محدودیت متاتریدر: حداکثر ۳۱ کاراکتر)
            trade_comment = f"TG:{telegram_id}" if telegram_id else "TG Bot Trade"

            request = {
                "action": trade_action_type,
                "symbol": symbol,
                "volume": float(lot),
                "type": trade_type,
                "price": round(execution_price, symbol_info.digits),
                "sl": round(sl_price, symbol_info.digits) if sl_price > 0 else 0.0,
                "tp": round(tp_price, symbol_info.digits) if tp_price > 0 else 0.0,
                "deviation": 20,
                "magic": telegram_id if telegram_id else 100000,  # استفاده از user_id به عنوان شناسه اختصاصی معامله
                "comment": trade_comment,
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_FOK,
            }

            result = mt5.order_send(request)

            if result is None:
                return False, "🚨 **خطای غیرمنتظره:** هیچ پاسخی از سرور متاتریدر دریافت نشد.", rr_ratio

            if result.retcode != mt5.TRADE_RETCODE_DONE:
                logger.error("Trade failed for %s (User %s). Retcode: %s, Comment: %s", symbol, telegram_id, result.retcode, result.comment)
                return False, f"🚨 **خطا در ثبت معامله در بروکر!**\n\n❌ **علت:** `{result.comment}`\n🔢 **کد خطا:** `{result.retcode}`", rr_ratio

            action_icon = "🟢" if is_buy else "🔴"
            rr_text = f"`1:{rr_ratio:.2f}`" if rr_ratio else "`نامشخص`"

            return True, (
                f"🎯 **سفارش با موفقیت ثبت شد**\n\n"
                f"📌 **نماد:** `{symbol}`\n"
                f"📊 **نوع:** {action_icon} `{action.upper()}`\n"
                f"📦 **حجم:** `{lot}`\n"
                f"💵 **قیمت اجرا:** `{round(execution_price, symbol_info.digits)}`\n"
                f"🛑 **حد زیان (SL):** `{sl_price if sl_price > 0 else 'تعیین نشده'}`\n"
                f"🎯 **حد سود (TP):** `{tp_price if tp_price > 0 else 'تعیین نشده'}`\n"
                f"⚖️ **نسبت R/R:** {rr_text}"
            ), rr_ratio

        except Exception as e:
            logger.exception("Unexpected error during trade execution for user %s: %s", telegram_id, e)
            return False, f"🚨 **خطایی در فرآیند اجرای معامله رخ داد:**\n`{e}`", None


def close_all_positions():
    """بستن یکجای تمام پوزیشن‌های باز"""
    logger.info("Request received to close ALL open positions.")
    with mt5_session() as ok:
        if not ok:
            return ERROR_CANNOT_CONNECT_TO_METATRADER

        positions = mt5.positions_get()
        if not positions:
            return "📭 <b>هیچ پوزیشن بازی جهت بستن یافت نشد.</b>"

        total_positions = len(positions)
        closed_count = 0
        total_closed_profit = 0.0
        details = []

        for pos in positions:
            pos_profit = getattr(pos, 'profit', 0.0)
            action = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
            tick = mt5.symbol_info_tick(pos.symbol)
            symbol_info = mt5.symbol_info(pos.symbol)

            if not tick or not symbol_info:
                details.append(f"❌ <code>{pos.ticket}</code> ({pos.symbol}): عدم دریافت قیمت لحظه‌ای")
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
                "type_filling":mt5.ORDER_FILLING_FOK,
            }
            res = mt5.order_send(req)
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                closed_count += 1
                total_closed_profit += pos_profit
                p_icon = "🟢" if pos_profit >= 0 else "🔴"
                details.append(f"✅ <code>{pos.ticket}</code> ({pos.symbol}) | {p_icon} <code>{pos_profit:.2f}$</code>")
            else:
                comment = res.comment if res else "پاسخی دریافت نشد"
                details.append(f"❌ <code>{pos.ticket}</code> ({pos.symbol}): {comment}")

        status_icon = "🎉" if closed_count == total_positions else ("⚠️" if closed_count > 0 else "❌")
        profit_summary_icon = "🟢" if total_closed_profit >= 0 else "🔴"

        output = (
            f"{status_icon} <b>نتیجه عملیات بستن تمامی پوزیشن‌ها:</b>\n"
            f"───────────────────\n"
            f"📊 <b>موفقیت:</b> <code>{closed_count}</code> از <code>{total_positions}</code> پوزیشن\n"
            f"{profit_summary_icon} <b>مجموع سود/زیان بسته‌شده:</b> <code>${total_closed_profit:,.2f}</code>\n"
            f"───────────────────\n"
            f"📝 <b>جزئیات پوزیشن‌ها:</b>\n"
        )
        output += "\n".join(details)
        return output


def close_position(ticket: int, volume_to_close: float = None):
    """بستن کامل یا جزئی (Partial Close) یک پوزیشن"""
    with mt5_session() as ok:
        if not ok:
            return False, ERROR_CANNOT_CONNECT_TO_METATRADER

        positions = mt5.positions_get(ticket=ticket)
        if not positions:
            return False, f"❌ پوزیشن با تیکت `{ticket}` یافت نشد."

        pos = positions[0]
        symbol = pos.symbol
        symbol_info = mt5.symbol_info(symbol)
        total_volume = pos.volume

        close_vol = volume_to_close if volume_to_close else total_volume
        if close_vol > total_volume:
            return False, "❌ حجم درخواستی برای خروج بیشتر از حجم کل پوزیشن است."

        order_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
        tick_info = mt5.symbol_info_tick(symbol)

        if not tick_info or not symbol_info:
            return False, f"❌ امکان دریافت قیمت لحظه‌ای برای نماد `{symbol}` وجود ندارد."

        price = tick_info.bid if order_type == mt5.ORDER_TYPE_SELL else tick_info.ask

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": close_vol,
            "type": order_type,
            "position": ticket,
            "price": price,
            "deviation": 20,
            "comment": "Closed via Telegram Bot",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_FOK,
        }

        result = mt5.order_send(request)
        if result is None:
            return False, "🚨 **خطای غیرمنتظره:** هیچ پاسخی از سرور متاتریدر دریافت نشد."

        if result.retcode != mt5.TRADE_RETCODE_DONE:
            return False, f"❌ خطا در بستن پوزیشن: `{result.comment}`"

        return True, f"✅ مقدار `{close_vol}` لات از پوزیشن `{ticket}` با موفقیت بسته شد."


def close_position_by_ticket(ticket: int):
    return close_position(ticket, volume_to_close=None)


def set_break_even(ticket: int):
    """انتقال حد ضرر (SL) به نقطه ورود پوزیشن (Break-Even)"""
    with mt5_session() as ok:
        if not ok:
            return False, ERROR_CANNOT_CONNECT_TO_METATRADER

        positions = mt5.positions_get(ticket=ticket)
        if not positions:
            return False, f"❌ پوزیشن با تیکت `{ticket}` یافت نشد."

        pos = positions[0]
        entry_price = pos.price_open

        if pos.sl == entry_price:
            return True, "ℹ️ حد ضرر از قبل روی نقطه ورود (Break-Even) تنظیم شده است."

        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": ticket,
            "symbol": pos.symbol,
            "sl": entry_price,
            "tp": pos.tp,
        }

        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            comment = result.comment if result else "پاسخی دریافت نشد"
            return False, f"❌ خطا در فری‌ریسک کردن: `{comment}`"

        return True, f"🛡 **پوزیشن فری‌ریسک شد!**\nحد ضرر جدید: `{entry_price}`"


def update_position_sltp(ticket: int, sl: float, tp: float):
    """بروزرسانی SL و TP یک پوزیشن باز"""
    with mt5_session() as ok:
        if not ok:
            return False, ERROR_CANNOT_CONNECT_TO_METATRADER

        positions = mt5.positions_get(ticket=ticket)
        if not positions:
            return False, f"❌ پوزیشن با تیکت `{ticket}` در متاتریدر یافت نشد."

        pos = positions[0]
        final_sl = sl if sl != 0 else pos.sl
        final_tp = tp if tp != 0 else pos.tp

        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": ticket,
            "symbol": pos.symbol,
            "sl": final_sl,
            "tp": final_tp
        }

        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            comment = result.comment if result else "پاسخی دریافت نشد"
            return False, f"❌ خطا در ویرایش SL/TP: `{comment}`"

        return True, f"✅ **حد ضرر و حد سود با موفقیت بروزرسانی شد.**\n\n🛑 **SL:** `{final_sl}`\n🎯 **TP:** `{final_tp}`"


# ------------------------------------------------------------------
# Market Data & Analysis Queries
# ------------------------------------------------------------------

def get_market_watch_symbols():
    """دریافت لیست نمادهای موجود در مارکت واچ"""
    with mt5_session() as ok:
        if not ok:
            return []

        symbols = mt5.symbols_get()
        if not symbols:
            return []

        watch_symbols = [s.name for s in symbols if s.visible]
        return watch_symbols if watch_symbols else [s.name for s in symbols[:8]]


def check_symbol_info(symbol: str):
    """بررسی و فعال‌سازی نماد در مارکت واچ"""
    with mt5_session() as ok:
        if not ok:
            return False
        try:
            if not mt5.symbol_select(symbol, True):
                symbols = mt5.symbols_get()
                return [s.name for s in symbols[:3]] if symbols else False
            return True
        except Exception:
            return False


def get_open_positions():
    """دریافت لیست تمام پوزیشن‌های باز جهت ارائه گزارش یا ساخت دکمه‌های شیشه‌ای"""
    with mt5_session() as ok:
        if not ok:
            return False, ERROR_CANNOT_CONNECT_TO_METATRADER

        try:
            positions = mt5.positions_get()
            if positions is None:
                return False, "ERROR_FETCH"

            positions_list = [
                {
                    "ticket": pos.ticket,
                    "symbol": pos.symbol,
                    "type": "BUY" if pos.type == mt5.ORDER_TYPE_BUY else "SELL",
                    "volume": pos.volume,
                    "price_open": pos.price_open,
                    "price_current": pos.price_current,
                    "sl": pos.sl,
                    "tp": pos.tp,
                    "profit": pos.profit,
                }
                for pos in positions
            ]
            return True, positions_list
        except Exception as e:
            return False, str(e)


def get_trades_history(days: int):
    """دریافت هیستوری معاملات بسته شده"""
    with mt5_session() as ok:
        if not ok:
            return None, ERROR_CANNOT_CONNECT_TO_METATRADER

        from_date = datetime.now(timezone.utc) - timedelta(days=days)
        to_date = datetime.now(timezone.utc)

        history = mt5.history_deals_get(from_date, to_date)
        if not history:
            return [], "هیچ معامله‌ای در این بازه پیدا نشد."

        trades = [
            {
                "ticket": deal.ticket,
                "symbol": deal.symbol,
                "type": "BUY" if deal.type == mt5.DEAL_TYPE_BUY else "SELL",
                "volume": deal.volume,
                "profit": deal.profit + deal.swap + deal.commission,
                "time": datetime.fromtimestamp(deal.time, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')
            }
            for deal in history
            if deal.entry == mt5.DEAL_ENTRY_OUT and deal.type in (mt5.DEAL_TYPE_BUY, mt5.DEAL_TYPE_SELL)
        ]
        return trades, None


def check_recent_closed_positions(hours_back=12):
    """دریافت پوزیشن‌های بسته‌شده جهت اطلاع‌رسانی اتوماتیک TP/SL در ربات"""
    with mt5_session() as ok:
        if not ok:
            return []

        now_utc = datetime.now(timezone.utc)
        from_date = now_utc - timedelta(hours=hours_back)
        to_date = now_utc + timedelta(hours=12)

        deals = mt5.history_deals_get(from_date, to_date)
        if not deals:
            return []

        closed_alerts = []
        for deal in deals:
            if deal.entry in (1, 2, mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_INOUT):
                comment = str(deal.comment).lower()
                reason = deal.reason

                exit_type = None
                if reason == mt5.DEAL_REASON_TP or reason == 5 or "tp" in comment:
                    exit_type = "🎯 TP (حد سود)"
                elif reason == mt5.DEAL_REASON_SL or reason == 4 or "sl" in comment:
                    exit_type = "🛑 SL (حد ضرر)"
                elif reason == mt5.DEAL_REASON_SO or reason == 6:
                    exit_type = "💥 Stop Out (کال مارجین)"
                elif reason == 3 or reason == mt5.DEAL_REASON_CLIENT:
                    exit_type = "✋ بسته‌شدن دستی / کلوز پوزیشن"

                if exit_type:
                    closed_alerts.append({
                        "deal_id": deal.ticket,
                        "position_id": deal.position_id,
                        "symbol": deal.symbol,
                        "profit": deal.profit + deal.swap + deal.commission,
                        "volume": deal.volume,
                        "exit_price": deal.price,
                        "exit_type": exit_type
                    })

        return closed_alerts