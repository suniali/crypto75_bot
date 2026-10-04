import asyncio
import logging
from datetime import datetime

from bot_app.services.mt5_service import (
    check_recent_closed_positions,
    get_open_positions,
)
from bot_app.services.journal_service import (
    sync_open_trade_to_db,
    get_sl_tp_from_db,
    finalize_closed_trade_in_db
)
from bot_app.services.chart_service import (
    create_journal_heikin_ashi_chart,
    generate_pro_daily_dashboard,
    calculate_today_stats,
)
from bot_app.services.api_service import fetch_recent_klines
from bot_app.utils.text_helpers import fmt_price

logger = logging.getLogger(__name__)

async def sl_tp_monitor_loop(chat_id: int, bot):
    logger.info("🚀 [STARTED] Global SL/TP Monitor Task")

    notified_deals = set()

    # ۱. مقداردهی اولیه برای جلوگیری از ارسال مجدد پیام‌های گذشته
    try:
        now = datetime.now()
        start_of_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        hours_today = max(1, int((now - start_of_today).total_seconds() / 3600) + 1)

        initial_deals = check_recent_closed_positions(hours_today)
        if initial_deals and isinstance(initial_deals, list):
            for deal in initial_deals:
                d_id = deal.get("deal_id") or deal.get("position_id")
                if d_id:
                    notified_deals.add(str(d_id))

        logger.info(f"🔰 SL/TP Monitor ready. Ignored {len(notified_deals)} past deals.")
    except Exception as err:
        logger.error(f"Error in initial fetch: {err}")

    while True:
        try:
            # ۲. سینک پوزیشن‌های باز در دیتابیس
            try:
                is_ok, open_positions = get_open_positions()
                if is_ok and isinstance(open_positions, list):
                    for pos in open_positions:
                        pos_dict = pos._asdict() if hasattr(pos, '_asdict') else pos
                        await sync_open_trade_to_db(chat_id=chat_id, pos_data=pos_dict)
            except Exception as e:
                logger.error(f"Error reading open positions: {e}")

            # ۳. بررسی معاملات بسته‌شده جدید
            now = datetime.now()
            start_of_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
            hours_today = max(1, int((now - start_of_today).total_seconds() / 3600) + 1)

            closed_deals = check_recent_closed_positions(hours_today)

            if closed_deals and isinstance(closed_deals, list):
                new_deals_notified_count = 0

                for deal in closed_deals:
                    deal_id = str(deal.get("deal_id") or "")
                    position_id = deal.get("position_id")

                    if not deal_id or deal_id in notified_deals:
                        continue

                    # استخراج مستقیم از دیکشنری خروجی check_recent_closed_positions
                    symbol = deal.get("symbol", "UNKNOWN")
                    volume = deal.get("volume", 0.0)
                    entry_price = deal.get("entry_price", 0.0)
                    exit_price = deal.get("exit_price", 0.0)
                    exit_type = deal.get("exit_type", "بسته‌شدن پوزیشن")
                    gross_profit = deal.get("gross_profit", 0.0)
                    commission = deal.get("commission", 0.0)
                    swap = deal.get("swap", 0.0)
                    net_profit = deal.get("profit", 0.0)

                    # بازیابی مقادیر SL و TP ثبت‌شده زمان ورود از دیتابیس
                    saved_sl, saved_tp = await get_sl_tp_from_db(position_id)
                    sl = saved_sl or deal.get("sl", 0.0)
                    tp = saved_tp or deal.get("tp", 0.0)

                    # ساخت دیکشنری کامل جهت ذخیره در ژورنال
                    final_deal_data = {
                        "deal_id": deal_id,
                        "position_id": position_id,
                        "symbol": symbol,
                        "volume": volume,
                        "entry_price": entry_price,
                        "exit_price": exit_price,
                        "exit_type": exit_type,
                        "sl": sl,
                        "tp": tp,
                        "gross_profit": gross_profit,
                        "commission": commission,
                        "swap": swap,
                        "profit": net_profit,
                    }

                    # تولید چارت ژورنال
                    journal_chart_buf = None
                    try:
                        df_ohlc = await fetch_recent_klines(
                            client=None, symbol=symbol, interval="1m", limit=400, is_forex=True
                        )
                        if df_ohlc is not None and not df_ohlc.empty:
                            journal_chart_buf = create_journal_heikin_ashi_chart(
                                df_ohlc=df_ohlc, symbol=symbol, entry_price=entry_price,
                                sl_price=sl, tp_price=tp
                            )
                    except Exception as chart_err:
                        logger.error(f"Chart error for {symbol}: {chart_err}")

                    # ذخیره نهایی در دیتابیس ژورنال
                    await finalize_closed_trade_in_db(chat_id=chat_id, deal=final_deal_data, chart_buf=journal_chart_buf)

                    # ارسال هشدار به تلگرام
                    profit_icon = "🟢" if net_profit >= 0 else "🔴"
                    alert_msg = (
                        f"🔔 <b>هشدار بسته‌شدن پوزیشن (ثبت در ژورنال)</b>\n\n"
                        f"🎫 <b>تیکت:</b> <code>{position_id}</code>\n"
                        f"📌 <b>نماد:</b> <b>{symbol}</b>\n"
                        f"📌 <b>علت خروج:</b> {exit_type}\n"
                        f"📊 <b>حجم:</b> <code>{volume}</code> لات\n"
                        f"🏁 <b>قیمت ورود:</b> <code>{entry_price}</code> | <b>خروج:</b> <code>{exit_price}</code>\n"
                        f"🛑 <b>SL:</b> <code>{fmt_price(sl) or '-'}:</code> | 🎯 <b>TP:</b> <code>{fmt_price(tp) or '-'}</code>\n"
                        f"───────────────────\n"
                        f"💵 <b>سود ناخالص:</b> <code>${gross_profit:,.2f}</code>\n"
                        f"💸 <b>کمیسیون:</b> <code>${commission:,.2f}</code> | 🌙 <b>سوآپ:</b> <code>${swap:,.2f}</code>\n"
                        f"───────────────────\n"
                        f"{profit_icon} <b>سود/زیان خالص:</b> <b><code>${net_profit:,.2f}</code></b>"
                    )

                    if journal_chart_buf:
                        journal_chart_buf.seek(0)
                        await bot.send_photo(chat_id=chat_id, photo=journal_chart_buf, caption=alert_msg, parse_mode="HTML")
                        journal_chart_buf.close()
                    else:
                        await bot.send_message(chat_id=chat_id, text=alert_msg, parse_mode="HTML")

                    notified_deals.add(deal_id)
                    new_deals_notified_count += 1

                # ارسال گزارش روزانه در صورت وجود معامله جدید
                if new_deals_notified_count > 0:
                    stats = calculate_today_stats(closed_deals)
                    if stats:
                        total_trades, wins, losses, win_rate, day_net_profit, avg_win, avg_loss, profits_history = stats
                        net_icon = "🚀" if day_net_profit >= 0 else "🔻"
                        summary_msg = (
                            f"📊 <b>گزارش عملکرد کل امروز ({datetime.now().strftime('%Y-%m-%d')})</b>\n\n"
                            f"🔢 <b>مجموع معاملات امروز:</b> <code>{total_trades}</code>\n"
                            f"✅ <b>تعداد برد:</b> <code>{wins}</code> | ❌ <b>تعداد باخت:</b> <code>{losses}</code>\n"
                            f"🎯 <b>وین‌ریت (Win Rate):</b> <code>%{win_rate:.1f}</code>\n"
                            f"📈 <b>میانگین سود:</b> <code>${avg_win:,.2f}</code> | 📉 <b>میانگین زیان:</b> <code>${avg_loss:,.2f}</code>\n"
                            f"───────────────────\n"
                            f"{net_icon} <b>سود/زیان خالص کل امروز:</b> <b><code>${day_net_profit:,.2f}</code></b>"
                        )
                        chart_buf = generate_pro_daily_dashboard(wins, losses, day_net_profit, win_rate, avg_win, avg_loss, profits_history)
                        if chart_buf:
                            await bot.send_photo(chat_id=chat_id, photo=chart_buf, caption=summary_msg, parse_mode="HTML")
                            if hasattr(chart_buf, 'close'):
                                chart_buf.close()

        except Exception as e:
            logger.error(f"Error in SL/TP monitor loop: {e}", exc_info=True)

        await asyncio.sleep(3)