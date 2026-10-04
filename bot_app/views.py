from decimal import Decimal
from collections import defaultdict
from datetime import timedelta
import json
from django.utils import timezone
from django.shortcuts import render
from .models import TradeJournal


def journal_dashboard(request):
    # ۱. دریافت بازه زمانی از URL
    period = request.GET.get('period', 'weekly').lower()
    now = timezone.now()

    if period == 'daily':
        start_date = now - timedelta(days=1)
    elif period == 'monthly':
        start_date = now - timedelta(days=30)
    elif period == '3months':
        start_date = now - timedelta(days=90)
    elif period == '6months':
        start_date = now - timedelta(days=180)
    elif period == 'yearly':
        start_date = now - timedelta(days=365)
    elif period == 'all':
        start_date = None
    else:
        period = 'weekly'
        start_date = now - timedelta(days=7)

    trades_qs = TradeJournal.active_objects.all()
    if start_date:
        trades_qs = trades_qs.filter(entry_time__gte=start_date)

    # مرتب‌‌سازی صعودی
    trades_ascending = trades_qs.order_by('entry_time', 'created_at')

    processed_trades = []
    symbol_pnl = defaultdict(Decimal)
    day_pnl = {i: Decimal('0.00') for i in range(7)}

    planned_rr_list = []
    realized_rr_list = []

    chart_labels = []
    cumulative_pnl_data = []
    drawdown_data = []

    running_total = Decimal('0.00')
    peak_pnl = Decimal('0.00')
    max_drawdown = Decimal('0.00')

    gross_wins = Decimal('0.00')
    gross_losses = Decimal('0.00')
    total_commission = Decimal('0.00')
    total_swap = Decimal('0.00')

    # ۲. پردازش دقیق معاملات
    for trade in trades_ascending:
        entry = Decimal(str(trade.entry_price)) if trade.entry_price is not None else None
        exit_p = Decimal(str(trade.exit_price)) if trade.exit_price is not None else None
        sl = Decimal(str(trade.stop_loss)) if trade.stop_loss is not None else None
        tp = Decimal(str(trade.take_profit)) if trade.take_profit is not None else None

        # R:R برنامه‌ریزی شده
        planned_val = 0.0
        planned_rr = "-"
        if entry and sl and tp:
            risk = abs(entry - sl)
            reward = abs(tp - entry)
            if risk > 0:
                planned_val = round(float(reward / risk), 2)
                planned_rr = f"1:{planned_val:g}"

        # R:R واقعی (فقط برای WIN)
        realized_val = 0.0
        realized_rr = "-"
        if trade.result == 'WIN' and entry and sl and exit_p:
            risk = abs(entry - sl)
            actual_reward = (exit_p - entry) if trade.trade_type == 'BUY' else (entry - exit_p)
            if risk > 0:
                realized_val = round(float(actual_reward / risk), 2)
                realized_rr = f"1:{realized_val:g}"

        # دریافت سود خالص مستقیماً از Decimal (مطمئن شوید net_profit در مدل Decimal برمی‌گرداند)
        pnl = Decimal(str(trade.net_profit)) if trade.net_profit is not None else Decimal('0.00')

        # کمیسیون و سوآپ به صورت قدر مطلق برای جمع کل
        comm = abs(Decimal(str(trade.commission))) if trade.commission is not None else Decimal('0.00')
        swp = Decimal(str(trade.swap)) if trade.swap is not None else Decimal('0.00')

        total_commission += comm
        total_swap += swp

        # تفکیک سود و زیان ناخالص
        if pnl > 0:
            gross_wins += pnl
        elif pnl < 0:
            gross_losses += abs(pnl)

        trade_time = trade.entry_time or trade.created_at
        symbol_pnl[trade.symbol] += pnl
        day_pnl[trade_time.weekday()] += pnl

        planned_rr_list.append(planned_val)
        realized_rr_list.append(realized_val)

        # محاسبه PnL تجمعی و Drawdown
        running_total += pnl
        if running_total > peak_pnl:
            peak_pnl = running_total

        current_dd = running_total - peak_pnl
        if current_dd < max_drawdown:
            max_drawdown = current_dd

        date_str = trade_time.strftime('%Y-%m-%d %H:%M')
        chart_labels.append(date_str)
        cumulative_pnl_data.append(float(round(running_total, 2)))
        drawdown_data.append(float(round(current_dd, 2)))

        # مقداردهی فرمت‌شده کمیسیون و سود برای تمپلیت
        comm_val = abs(Decimal(str(trade.commission))) if trade.commission is not None else Decimal('0.00')
        trade.fmt_comm = float(comm_val)

        # مقادیر فرمت‌شده برای تمپلیت
        trade.fmt_entry = float(entry) if entry else None
        trade.fmt_exit = float(exit_p) if exit_p else None
        trade.fmt_sl = float(sl) if sl else None
        trade.fmt_tp = float(tp) if tp else None
        trade.calc_planned_rr = planned_rr
        trade.calc_realized_rr = realized_rr
        processed_trades.append(trade)

    # ۳. محاسبات نهایی آمار کل
    total_trades = len(processed_trades)
    wins = sum(1 for t in processed_trades if t.result == 'WIN')
    losses = sum(1 for t in processed_trades if t.result == 'LOSS')
    be_count = sum(1 for t in processed_trades if t.result == 'BE')

    win_rate = round((wins / total_trades * 100), 1) if total_trades > 0 else 0.0
    total_net_profit = float(round(running_total, 2))

    # محاسبه دقیق Profit Factor
    if gross_losses > 0:
        profit_factor = round(float(gross_wins / gross_losses), 2)
    elif gross_wins > 0:
        profit_factor = round(float(gross_wins), 2)
    else:
        profit_factor = 0.0

    days_names = ['دوشنبه', 'سه‌شنبه', 'چهارشنبه', 'پنج‌شنبه', 'جمعه', 'شنبه', 'یکشنبه']
    day_pnl_values = [float(round(day_pnl[i], 2)) for i in range(7)]

    symbols_list = list(symbol_pnl.keys())
    symbols_pnl_values = [float(round(symbol_pnl[s], 2)) for s in symbols_list]

    context = {
        'trades': list(reversed(processed_trades)),
        'current_period': period,
        'total_trades': total_trades,
        'win_rate': win_rate,
        'wins': wins,
        'losses': losses,
        'be_count': be_count,
        'total_net_profit': total_net_profit,
        'profit_factor': profit_factor,
        'max_drawdown': float(round(max_drawdown, 2)),
        'total_commission': float(round(total_commission, 2)),
        'total_swap': float(round(total_swap, 2)),
        'chart_labels_json': json.dumps(chart_labels),
        'chart_pnl_json': json.dumps(cumulative_pnl_data),
        'drawdown_json': json.dumps(drawdown_data),
        'win_loss_json': json.dumps([wins, losses, be_count]),
        'days_names_json': json.dumps(days_names),
        'day_pnl_json': json.dumps(day_pnl_values),
        'symbols_json': json.dumps(symbols_list),
        'symbols_pnl_json': json.dumps(symbols_pnl_values),
        'planned_rr_json': json.dumps(planned_rr_list),
        'realized_rr_json': json.dumps(realized_rr_list),
    }

    return render(request, 'dashboard.html', context)