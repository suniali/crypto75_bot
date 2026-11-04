import json
import html
import asyncio
import logging
from decouple import config
from datetime import datetime

from bot_app.services.analysis_service import calculate_trading_metrics
from bot_app.services.mt5_service import (
    check_recent_closed_positions,
    get_open_positions,
)
from bot_app.services.journal_service import (
    sync_open_trade_to_db,
    get_sl_tp_from_db,
    finalize_closed_trade_in_db
)
from bot_app.services.chart_service import create_journal_heikin_ashi_chart,generate_pro_daily_dashboard

from bot_app.services.api_service import fetch_recent_klines
from bot_app.services.bakup_restore_service import backup_database

from bot_app.utils.text_helpers import fmt_price

from bot_app.models import TelegramUser

logger = logging.getLogger(__name__)
CURENCY_UNIT=config("CURENCY_UNIT", default="$")

def _money(v: float) -> str:
    """مبلغ علامت‌دار: +$12.50 / -$3.00"""
    v = float(v or 0)
    sign = '+' if v > 0 else '-' if v < 0 else ''
    return f"{sign}{CURENCY_UNIT}{abs(v):,.2f}"


def _bar(pct: float, length: int = 10) -> str:
    """نوار پیشرفت متنی"""
    pct = max(0.0, min(100.0, float(pct or 0)))
    filled = int(round(pct / 100 * length))
    return "▰" * filled + "▱" * (length - filled)


def _hearts(earned: int, target: int) -> str:
    try:
        earned, target = int(earned), int(target)
    except (TypeError, ValueError):
        return ""
    if target <= 0 or target > 12:
        return f"{earned}/{target}"
    return "❤️" * earned + "🤍" * max(0, target - earned) + f"  <code>{earned}/{target}</code>"


def _json(m: dict, key: str) -> list:
    try:
        return json.loads(m.get(key) or '[]')
    except (TypeError, ValueError):
        return []


def build_full_report_sections(m: dict, title: str) -> list[str]:
    """هر بخش یک رشته جداست تا بعداً در پیام‌های ≤۴۰۰۰ کاراکتر بسته‌بندی شود."""
    sections = []
    total = m.get('total_trades', 0)
    net = m.get('total_net_profit', 0.0)
    net_icon = "🚀" if net > 0 else "🔻" if net < 0 else "➖"

    # ── هدر ──
    sections.append(
        f"📊 <b>{title}</b>\n"
        f"🗓 <code>{datetime.now().strftime('%Y-%m-%d %H:%M')}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━"
    )

    # ── خلاصه معاملات ──
    sections.append(
        f"🔢 <b>خلاصه معاملات</b>\n"
        f"• مجموع معاملات: <code>{total}</code>\n"
        f"• ✅ برد: <code>{m.get('wins', 0)}</code> | "
        f"❌ باخت: <code>{m.get('losses', 0)}</code> | "
        f"➖ سر‌به‌سر: <code>{m.get('be_count', 0)}</code>\n"
        f"• 🎯 وین‌ریت: <code>%{m.get('win_rate', 0):.1f}</code>  {_bar(m.get('win_rate', 0))}"
    )

    # ── مالی ──
    sections.append(
        f"💰 <b>وضعیت مالی</b>\n"
        f"• {net_icon} سود/زیان خالص: <b><code>{_money(net)}</code></b>\n"
        f"• بازده بازه: <code>{m.get('gain_percent', 0):+.2f}%</code>\n"
        f"• کمیسیون: <code>{_money(m.get('total_commission', 0))}</code>\n"
        f"• سوآپ: <code>{_money(m.get('total_swap', 0))}</code>\n"
        f"• 📈 میانگین برد: <code>{CURENCY_UNIT}{m.get('avg_win', 0):,.2f}</code>\n"
        f"• 📉 میانگین باخت: <code>{CURENCY_UNIT}{m.get('avg_loss', 0):,.2f}</code>"
    )

    # ── شاخص‌های کیفیت ──
    pf = m.get('profit_factor', 0)
    pf_icon = "🟢" if pf >= 1.2 else "🟡" if pf >= 1 else "🔴"
    exp = m.get('expectancy', 0)
    sections.append(
        f"⚖️ <b>شاخص‌های عملکرد</b>\n"
        f"• {pf_icon} Profit Factor: <code>{pf}</code>\n"
        f"• Payoff Ratio: <code>1:{m.get('payoff_ratio', 0)}</code>\n"
        f"• Expectancy (هر معامله): <code>{_money(exp)}</code>\n"
        f"• Sharpe (هر معامله): <code>{m.get('sharpe_ratio', 0)}</code>\n"
        f"• وضعیت پایداری: <b>{html.escape(str(m.get('stability_label', '-')))}</b>"
    )

    # ── ریسک ──
    sections.append(
        f"🛡 <b>ریسک و افت حساب</b>\n"
        f"• Max Drawdown: <code>{m.get('max_drawdown_pct', 0)}%</code> "
        f"(<code>{CURENCY_UNIT}{m.get('max_drawdown', 0):,.2f}</code>)\n"
        f"• سطح ریسک: <b>{html.escape(str(m.get('risk_level_label', '-')))}</b>\n"
        f"• انضباط ذهنی: <code>{m.get('mental_discipline_score', 0)}/100</code> "
        f"— {html.escape(str(m.get('discipline_label', '-')))}\n"
        f"  {_bar(m.get('mental_discipline_score', 0))}"
    )

    # ── استریک‌ها ──
    st = m.get('streaks') or {}
    sections.append(
        f"🔥 <b>زنجیره‌ها (Streaks)</b>\n"
        f"• معاملات متوالی: برد <code>{st.get('max_win_streak_trades', 0)}</code> | "
        f"باخت <code>{st.get('max_loss_streak_trades', 0)}</code>\n"
        f"• روزهای متوالی: برد <code>{st.get('max_win_streak_days', 0)}</code> | "
        f"باخت <code>{st.get('max_loss_streak_days', 0)}</code>"
    )

    # ── بازدهی بازه‌ها ──
    pr = m.get('period_returns') or {}
    sections.append(
        f"📆 <b>بازدهی تخمینی (بر اساس نرخ همین بازه)</b>\n"
        f"• روزانه: <code>{pr.get('daily', 0):+.2f}%</code>\n"
        f"• هفتگی: <code>{pr.get('weekly', 0):+.2f}%</code>\n"
        f"• ماهانه: <code>{pr.get('monthly', 0):+.2f}%</code>\n"
        f"• سالانه: <code>{pr.get('annualized', 0):+.2f}%</code>"
    )

    # ── رادار ──
    radar = _json(m, 'radar_json')   # [entry, risk, exit, stability, tempo]
    if len(radar) == 5:
        names = ["ورود", "ریسک", "خروج", "ثبات", "ریتم"]
        lines = "\n".join(
            f"• {n}: <code>{v:>3}</code> {_bar(v)}" for n, v in zip(names, radar)
        )
        sections.append(
            f"🕸 <b>رادار ارزیابی (WaveScore): {m.get('radar_score_overall', 0)}/100</b>\n{lines}"
        )

    # ── بهترین/بدترین روز و نماد ──
    day_names = _json(m, 'days_names_json')
    day_vals = _json(m, 'day_pnl_json')
    sym_names = _json(m, 'symbols_json')
    sym_vals = _json(m, 'symbols_pnl_json')

    extra = []
    if day_vals and any(day_vals):
        bi = max(range(len(day_vals)), key=lambda i: day_vals[i])
        wi = min(range(len(day_vals)), key=lambda i: day_vals[i])
        extra.append(f"• 📅 بهترین روز: <b>{day_names[bi]}</b> <code>{_money(day_vals[bi])}</code>")
        extra.append(f"• 📅 بدترین روز: <b>{day_names[wi]}</b> <code>{_money(day_vals[wi])}</code>")
    if sym_vals:
        bi = max(range(len(sym_vals)), key=lambda i: sym_vals[i])
        wi = min(range(len(sym_vals)), key=lambda i: sym_vals[i])
        extra.append(f"• 🏆 بهترین نماد: <b>{html.escape(sym_names[bi])}</b> <code>{_money(sym_vals[bi])}</code>")
        extra.append(f"• ⚠️ ضعیف‌ترین نماد: <b>{html.escape(sym_names[wi])}</b> <code>{_money(sym_vals[wi])}</code>")
        if len(sym_names) > 2:
            ranking = sorted(zip(sym_names, sym_vals), key=lambda x: x[1], reverse=True)
            extra.append("• رتبه‌بندی نمادها:")
            for n, v in ranking[:6]:
                extra.append(f"   └ {html.escape(n)}: <code>{_money(v)}</code>")
    if extra:
        sections.append("🔀 <b>تفکیک روز و نماد</b>\n" + "\n".join(extra))

    # ── R:R ──
    planned = [x for x in _json(m, 'planned_rr_json')[1:] if x]
    realized = [x for x in _json(m, 'realized_rr_json')[1:] if x]
    if planned or realized:
        avg_p = sum(planned) / len(planned) if planned else 0
        avg_r = sum(realized) / len(realized) if realized else 0
        sections.append(
            f"🎯 <b>Risk to Reward</b>\n"
            f"• میانگین R:R برنامه‌ریزی‌شده: <code>1:{avg_p:.2f}</code>\n"
            f"• میانگین R:R واقعی: <code>{avg_r:+.2f}R</code>"
        )

    # ── نقشه راه قلب‌ها (در صورت وجود) ──
    if m.get('current_level_num') is not None:
        in_loss = m.get('is_in_loss')
        if in_loss:
            heart_line = f"• ❤️ قلب لازم برای جبران: <code>{m.get('required_recovery_hearts', 0)}</code>"
        else:
            heart_line = f"• قلب‌های مرحله: {_hearts(m.get('earned_hearts_in_level', 0), m.get('target_hearts', 0))}"
        sections.append(
            f"🗺 <b>نقشه راه رشد اکانت</b>\n"
            f"• سطح: <code>{m.get('current_level_num')}</code> — "
            f"{html.escape(str(m.get('trend_status', '')))}\n"
            f"{heart_line}\n"
            f"• پیشرفت سطح: <code>{'-' if in_loss else ''}{m.get('completion_percentage', 0)}%</code> "
            f"{_bar(m.get('completion_percentage', 0))}\n"
            f"• ریسک معامله بعدی: <code>{m.get('unit', CURENCY_UNIT)}{m.get('current_risk', 0)}</code>\n"
            f"• کیفیت معامله‌گری: <code>{m.get('quality_score', 0)}/100</code>\n"
            f"• قلب ذخیره لازم: <code>{m.get('required_hearts', '-')}</code>"
        )

    return sections


def pack_sections(sections: list[str], limit: int = 4000) -> list[str]:
    """بخش‌ها را در پیام‌هایی تا سقف limit کاراکتر بسته‌بندی می‌کند (بدون شکستن وسط یک بخش)."""
    messages, current = [], ""
    for sec in sections:
        candidate = f"{current}\n\n{sec}" if current else sec
        if len(candidate) > limit and current:
            messages.append(current)
            current = sec
        else:
            current = candidate
    if current:
        messages.append(current)
    return messages

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
                            client=None, symbol=symbol, interval="1m", limit=1000, is_forex=True
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
                        f"💵 <b>سود ناخالص:</b> <code>{CURENCY_UNIT}{gross_profit:,.2f}</code>\n"
                        f"💸 <b>کمیسیون:</b> <code>{CURENCY_UNIT}{commission:,.2f}</code> | 🌙 <b>سوآپ:</b> <code>{CURENCY_UNIT}{swap:,.2f}</code>\n"
                        f"───────────────────\n"
                        f"{profit_icon} <b>سود/زیان خالص:</b> <b><code>{CURENCY_UNIT}{net_profit:,.2f}</code></b>"
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
                    backup_database()  # ایجاد بک‌آپ قبل از ارسال  گزارش

                    user_obj = await TelegramUser.objects.filter(chat_id=chat_id).afirst()
                    metrics = await asyncio.to_thread(calculate_trading_metrics,period='daily', user=user_obj)

                    if metrics and metrics.get('total_trades', 0) > 0:
                        # profits_history برای نمودار: اختلاف‌های منحنی تجمعی
                        cum = json.loads(metrics['chart_pnl_json'])          # [0, ...]
                        profits_history = [round(cum[i] - cum[i - 1], 2) for i in range(1, len(cum))]

                        chart_buf = generate_pro_daily_dashboard(
                            metrics['wins'], metrics['losses'], metrics['total_net_profit'],
                            metrics['win_rate'], metrics['avg_win'], metrics['avg_loss'],
                            profits_history,
                        )

                        net = metrics['total_net_profit']
                        net_icon = "🚀" if net >= 0 else "🔻"
                        short_caption = (
                            f"📊 <b>گزارش عملکرد امروز</b> ({datetime.now().strftime('%Y-%m-%d')})\n"
                            f"{net_icon} سود/زیان خالص: <b><code>{_money(net)}</code></b>\n"
                            f"🎯 وین‌ریت: <code>%{metrics['win_rate']:.1f}</code> | "
                            f"🔢 معاملات: <code>{metrics['total_trades']}</code>"
                        )

                        if chart_buf:
                            await bot.send_photo(chat_id=chat_id, photo=chart_buf,
                                                caption=short_caption, parse_mode="HTML")
                            if hasattr(chart_buf, 'close'):
                                chart_buf.close()

                        sections = build_full_report_sections(metrics, "گزارش کامل عملکرد امروز")
                        for part in pack_sections(sections):
                            await bot.send_message(chat_id=chat_id, text=part, parse_mode="HTML")
                    

        except Exception as e:
            logger.error(f"Error in SL/TP monitor loop: {e}", exc_info=True)

        await asyncio.sleep(3)