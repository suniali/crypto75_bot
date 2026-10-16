import os
import json
import logging
import math
import shutil

from pathlib import Path
from decimal import Decimal
from collections import defaultdict
from datetime import timedelta,datetime
from django.utils import timezone
from django.shortcuts import render
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from .models import TradeJournal
from .utils.month_calender_data import get_monthly_calendar_data,calculate_streaks


logger = logging.getLogger(__name__)

def journal_dashboard(request):
    """
    Dashboard view for trade journal analytics, filtering, and pagination.
    """
    # ۱. دریافت بازه زمانی از URL (اصلاح بازه‌های روزانه و هفتگی)
    period = request.GET.get('period', 'weekly').lower()
    now = timezone.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    if period == 'daily':
        # از ساعت ۰۰:۰۰ امروز تا الان
        start_date = today_start
    elif period == 'weekly':
        # شروع هفته از روز شنبه (محاسبه دقیق فاصله تا شنبه گذشته)
        days_since_saturday = (now.weekday() - 5) % 7
        start_date = today_start - timedelta(days=days_since_saturday)
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
        days_since_saturday = (now.weekday() - 5) % 7
        start_date = today_start - timedelta(days=days_since_saturday)

    trades_qs = TradeJournal.active_objects.all()
    if start_date:
        trades_qs = trades_qs.filter(entry_time__gte=start_date)

    # مرتب‌سازی صعودی برای محاسبه دقیق PnL تجمعی و نمودارها
    trades_ascending = trades_qs.order_by('entry_time', 'created_at')

    processed_trades = []
    symbol_pnl = defaultdict(Decimal)
    day_pnl = {i: Decimal('0.00') for i in range(7)}

    chart_labels = ['Start']
    cumulative_pnl_data = [0.0]
    drawdown_data = [0.0]

    planned_rr_list = [0.0]
    realized_rr_list = [0.0]

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

        # R:R واقعی (محاسبه برای برآیند مثبت یا WIN)
        realized_val = 0.0
        realized_rr = "-"
        if trade.result == 'WIN' and entry and sl and exit_p:
            risk = abs(entry - sl)
            actual_reward = (exit_p - entry) if trade.trade_type == 'BUY' else (entry - exit_p)
            if risk > 0:
                realized_val = round(float(actual_reward / risk), 2)
                realized_rr = f"1:{realized_val:g}"

        # سود خالص
        pnl = Decimal(str(trade.net_profit)) if trade.net_profit is not None else Decimal('0.00')

        # کمیسیون و سواپ
        comm = Decimal(str(trade.commission)) if trade.commission is not None else Decimal('0.00')
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

        # مقادیر فرمت‌شده برای تمپلیت
        trade.fmt_comm = float(comm)
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

    # میانگین سود و زیان
    avg_win = float(round(gross_wins / wins, 2)) if wins > 0 else 0.0
    avg_loss = float(round(gross_losses / losses, 2)) if losses > 0 else 0.0
    print(f'avrage win : {avg_win}, avg loss: {avg_loss}')

    # نسبت Payoff (میانگین سود به میانگین زیان)
    payoff_ratio = round(avg_win / avg_loss, 2) if avg_loss > 0 else (avg_win if avg_win > 0 else 0.0)

    # امید ریاضی (Expectancy)
    win_prob = wins / total_trades if total_trades > 0 else 0.0
    loss_prob = losses / total_trades if total_trades > 0 else 0.0
    expectancy = round((win_prob * avg_win) - (loss_prob * avg_loss), 2) if total_trades > 0 else 0.0

    # محاسبه Sharpe Ratio بر اساس سود/زیان هر معامله
    trade_pnls = [float(t.net_profit) for t in processed_trades if t.net_profit is not None]
    if len(trade_pnls) > 1:
        mean_pnl = sum(trade_pnls) / len(trade_pnls)
        variance = sum((x - mean_pnl) ** 2 for x in trade_pnls) / len(trade_pnls)
        std_dev = math.sqrt(variance)
        sharpe_ratio = round(mean_pnl / std_dev, 2) if std_dev > 0 else 0.0
    else:
        sharpe_ratio = 0.0

    # محاسبه دقیق Profit Factor
    if gross_losses > 0:
        profit_factor = round(float(gross_wins / gross_losses), 2)
    elif gross_wins > 0:
        profit_factor = round(float(gross_wins), 2)
    else:
        profit_factor = 0.0

    # محاسبه متوالی‌ها (Winstreaks)
    streaks_data = calculate_streaks(processed_trades)

    # تعیین هوشمند نرخ پایداری سیستم بر اساس عملکرد کل
    if profit_factor >= 2.0 and win_rate >= 50:
        stability_label = "عالی و باثبات"
        stability_color = "text-emerald-400"
    elif profit_factor >= 1.2 and win_rate >= 40:
        stability_label = "بسیار خوب"
        stability_color = "text-emerald-400"
    elif profit_factor >= 1.0:
        stability_label = "نرمال / لب‌مرز"
        stability_color = "text-amber-400"
    else:
        stability_label = "نیاز به بازنگری ریسک"
        stability_color = "text-rose-400"

    # محاسبه امتیازات Radar (بر اساس ۵ معیار تخصصی)
    entry_score = min(100, int(win_rate * 1.5))
    exit_score = min(100, int(payoff_ratio * 35))
    risk_score = max(10, 100 - int(abs(float(max_drawdown)) / 50))
    stability_score = min(100, int((sharpe_ratio + 1) * 30))
    tempo_score = min(100, int(total_trades * 3))
    radar_score_overall = int((entry_score + exit_score + risk_score + stability_score + tempo_score) / 5)

    radar_data = [entry_score, risk_score, exit_score, stability_score, tempo_score]

    # بالانس اولیه پیش‌فرض جهت درصدها
    initial_balance = Decimal('100.00')
    gain_percent = round((total_net_profit / float(initial_balance)) * 100, 2)

    # بازدهی بازه‌ها
    period_daily_return = round(total_net_profit / 30 / float(initial_balance) * 100, 2) if total_trades > 0 else 0.0
    period_weekly_return = round(total_net_profit / 4 / float(initial_balance) * 100, 2) if total_trades > 0 else 0.0
    period_monthly_return = gain_percent
    period_annual_return = round(gain_percent * 12, 2)

    days_names = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
    day_pnl_values = [float(round(day_pnl[i], 2)) for i in range(7)]

    symbols_list = list(symbol_pnl.keys())
    symbols_pnl_values = [float(round(symbol_pnl[s], 2)) for s in symbols_list]

    # Pagination (نمایش معکوس برای اینکه جدیدترین معاملات در صفحه اول جدول قرار گیرند)
    paginator = Paginator(list(reversed(processed_trades)), 10)
    page_number = request.GET.get('page', 1)

    try:
        trades = paginator.page(page_number)
    except PageNotAnInteger:
        trades = paginator.page(1)
    except EmptyPage:
        trades = paginator.page(paginator.num_pages)

    context = {
        'trades': trades,
        'current_period': period,
        'total_trades': total_trades,
        'win_rate': win_rate,
        'wins': wins,
        'losses': losses,
        'be_count': be_count,
        'total_net_profit': total_net_profit,
        'profit_factor': profit_factor,
        'avg_win': avg_win,
        'avg_loss': avg_loss,
        'payoff_ratio': payoff_ratio,
        'expectancy': expectancy,
        'sharpe_ratio': sharpe_ratio,
        'max_drawdown': float(round(max_drawdown, 2)),
        'total_commission': float(round(total_commission, 2)),
        'total_swap': float(round(total_swap, 2)),
        'streaks': streaks_data,
        'stability_label': stability_label,
        'stability_color': stability_color,
        'radar_score_overall': radar_score_overall,
        'radar_json': json.dumps(radar_data),
        'gain_percent': gain_percent,
        'period_returns': {
            'daily': period_daily_return,
            'weekly': period_weekly_return,
            'monthly': period_monthly_return,
            'annualized': period_annual_return,
        },
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

    # دریافت سال و ماه از پارامترهای URL یا استفاده از ماه جاری
    req_year = int(request.GET.get('year', timezone.now().year))
    req_month = int(request.GET.get('month', timezone.now().month))
    
    cal_data = get_monthly_calendar_data(req_year, req_month)
    context.update(cal_data)

    return render(request, 'dashboard.html', context)


@require_POST
def update_trade_note_api(request):
    """
    API endpoint to update trade notes and psychological reviews.
    """
    import json
    try:
        data = json.loads(request.body)
        ticket = data.get('ticket')
        new_note = data.get('note', '').strip()


        if not ticket:
            logger.warning("⚠️️ Update note request rejected: Missing trade ticket.")
            return JsonResponse({'success': False, 'error': 'Missing ticket parameter'}, status=400)

        # Find the trade record in the journal database
        trade = TradeJournal.objects.filter(position_id=ticket).first()
        if not trade:
            logger.warning(f"⚠️ Trade record #{ticket} not found in the journal.")
            return JsonResponse({'success': False, 'error': 'Trade not found'}, status=404)

        # Update the note field
        trade.notes = new_note
        trade.save(update_fields=['notes'])

        logger.info(f"✅ Note successfully updated for trade #{ticket}.")
        return JsonResponse({'success': True, 'message': 'Note updated successfully'})

    except Exception as e:
        logger.error(f"❌ Error processing trade note update: {e}", exc_info=True)
        return JsonResponse({'success': False, 'error': str(e)}, status=500)

@require_POST
def py_backup_view(request):
    BASE_DIR = Path(__file__).resolve().parent.parent # با توجه به ساختار پروژه شما
    DB_FILE = BASE_DIR / "db.sqlite3"
    BACKUP_DIR = BASE_DIR / "backups"

    if not DB_FILE.exists():
        return JsonResponse({"status": "error", "message": "فایل دیتابیس پیدا نشد!"}, status=404)

    try:
        BACKUP_DIR.mkdir(exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        backup_filename = f"db_backup_{timestamp}.sqlite3"
        backup_path = BACKUP_DIR / backup_filename

        shutil.copy2(DB_FILE, backup_path)

        # نگهداری فقط ۱۰ بک‌آپ آخر برای جلوگیری از پر شدن حافظه
        backups = sorted(BACKUP_DIR.glob("db_backup_*.sqlite3"), key=os.path.getmtime)
        if len(backups) > 10:
            for old_backup in backups[:-10]:
                old_backup.unlink()

        return JsonResponse({"status": "success", "message": f"بک‌آپ با موفقیت ذخیره شد: {backup_filename}"})
    except Exception as e:
        return JsonResponse({"status": "error", "message": str(e)}, status=500)


def list_backups_view(request):
    """لیست تمام فایل‌های بک‌آپ موجود را برمی‌گرداند"""
    BASE_DIR = Path(__file__).resolve().parent.parent
    BACKUP_DIR = BASE_DIR / "backups"

    if not BACKUP_DIR.exists():
        return JsonResponse({"status": "success", "backups": []})

    # پیدا کردن و مرتب‌سازی فایل‌ها بر اساس تاریخ (جدیدترین اول)
    backups = sorted(BACKUP_DIR.glob("db_backup_*.sqlite3"), key=os.path.getmtime, reverse=True)
    backup_list = [b.name for b in backups]

    return JsonResponse({"status": "success", "backups": backup_list})


@require_POST
def restore_backup_view(request):
    """فایل بک‌آپ انتخابی را جایگزین دیتابیس اصلی می‌کند"""
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "متد غیرمجاز"}, status=400)

    import json
    try:
        data = json.loads(request.body)
        filename = data.get("filename")
        if not filename:
            return JsonResponse({"status": "error", "message": "نام فایل مشخص نشده است"}, status=400)

        BASE_DIR = Path(__file__).resolve().parent.parent
        DB_FILE = BASE_DIR / "db.sqlite3"
        BACKUP_DIR = BASE_DIR / "backups"
        target_backup = BACKUP_DIR / filename

        if not target_backup.exists():
            return JsonResponse({"status": "error", "message": "فایل بک‌آپ مورد نظر پیدا نشد"}, status=404)

        # قبل از جایگزینی، یک ایمنی موقت از وضعیت فعلی می‌گیریم
        emergency_backup = BACKUP_DIR / "emergency_before_restore.sqlite3"
        if DB_FILE.exists():
            shutil.copy2(DB_FILE, emergency_backup)

        # جایگزینی فایل بک‌آپ با دیتابیس اصلی
        shutil.copy2(target_backup, DB_FILE)

        return JsonResponse({"status": "success", "message": f"دیتابیس با موفقیت از روی فایل {filename} بازیابی شد!"})
    except Exception as e:
        return JsonResponse({"status": "error", "message": str(e)}, status=500)


def day_trades_api_view(request):
    date_str = request.GET.get('date')  # فرمت: YYYY-MM-DD
    if not date_str:
        return JsonResponse({"status": "error", "message": "تاریخ مشخص نشده است"}, status=400)
    
    try:
        target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return JsonResponse({"status": "error", "message": "فرمت تاریخ نامعتبر است"}, status=400)
        
    trades = TradeJournal.active_objects.filter(entry_time__date=target_date).order_by('entry_time')
    
    trades_data = []
    for t in trades:
        net_pnl = float(t.net_profit) if t.net_profit is not None else 0.0
        
        # فرمت‌دهی قیمت‌ها تا 5 رقم اعشار (اگر مقدار داشته باشند)
        def fmt_price(val):
            return f"{val:.5f}" if val is not None else '-'

        trades_data.append({
            'id': t.id,
            'symbol': t.symbol,
            'trade_type': t.trade_type,  # BUY یا SELL
            'result': t.result,          # WIN, LOSS, BE, PENDING
            'volume': str(t.volume) if t.volume else '-',
            'entry_price': fmt_price(t.entry_price),
            'exit_price': fmt_price(t.exit_price),
            'stop_loss': fmt_price(t.stop_loss),
            'take_profit': fmt_price(t.take_profit),
            'commission': float(t.commission) if t.commission else 0.0,
            'swap': float(t.swap) if t.swap else 0.0,
            'net_profit': net_pnl,
            'notes': t.notes if t.notes else '',
            'image_url': t.image.url if t.image else None,
            'entry_time': t.entry_time.strftime('%H:%M:%S') if t.entry_time else '-',
            'exit_time': t.exit_time.strftime('%H:%M:%S') if t.exit_time else '-',
        })
        
    return JsonResponse({
        "status": "success",
        "date": date_str,
        "trades": trades_data
    })