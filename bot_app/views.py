import json
import logging
import math


from decimal import Decimal,ROUND_CEILING,ROUND_HALF_UP
from collections import defaultdict
from datetime import timedelta,datetime
from django.utils import timezone
from django.shortcuts import render
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.views.decorators.http import require_POST
from django.http import JsonResponse

from .models import TradeJournal,TelegramUser
from .services.bakup_restore_service import backup_database,get_backups,restore_backup
from .utils.month_calender_data import get_monthly_calendar_data,calculate_streaks


logger = logging.getLogger(__name__)

MIN_RESERVE_HEARTS = 4   # حداقل قلب ذخیره در شروع مرحله

def risk_from_reserve(reserve, base_risk,max_reserve_hearts):
    """
    ریسک مرحله از روی ذخیره‌ی سود در شروع مرحله:
    کوچک‌ترین مضرب ریسک پایه که قلب ذخیره از 10 بیشتر نشه،
    و اگر کمتر از ۴ قلب موند، یک پله پایین‌تر.
    """
    if reserve <= 0:
        return base_risk
    k = int(reserve / (base_risk * Decimal(str(max_reserve_hearts + 1)))) + 1
    if k > 1 and int(reserve / (base_risk * k)) < MIN_RESERVE_HEARTS:
        k -= 1
    return base_risk * k

def get_user_progression_plan(user):
    all_trades = TradeJournal.active_objects.exclude(result='PENDING')

    # ۱. محاسبه وین‌ریت معامله‌گر برای تعیین پویا ی MAX_RESERVE_HEARTS
    total_trades = len(all_trades)
    if total_trades > 0:
        winning_trades = sum(1 for t in all_trades if Decimal(str(t.net_profit)) > 0)
        win_rate = (Decimal(str(winning_trades)) / Decimal(str(total_trades))) * Decimal('100')
    else:
        win_rate = Decimal('50.0') # مقدار پیش‌فرض برای حساب‌های تازه بدون معامله
        
    # ۲. تعیین پویا ی MAX_RESERVE_HEARTS بر اساس وین‌ریت (تریدر قوی‌تر = عدد کمتر، حداقل ۵)
    if win_rate >= Decimal('70.0'):
        max_reserve_hearts = 5    # تریدر بسیار قوی (حداقل مجاز)
    elif win_rate >= Decimal('60.0'):
        max_reserve_hearts = 6
    elif win_rate >= Decimal('50.0'):
        max_reserve_hearts = 8
    elif win_rate >= Decimal('40.0'):
        max_reserve_hearts = 10
    else:
        max_reserve_hearts = 12   # تریدر ضعیف‌تر (نیاز به احتیاط و ذخیره بیشتر)
    
    # تبدیل امن بالانس اولیه و ریسک پایه به Decimal
    base_risk = user.initial_balance / Decimal("100") if getattr(user, 'initial_balance', None) else Decimal("1.0")
    
    # جمع زدن سود کل معاملات
    total_net_profit = sum((Decimal(str(t.net_profit)) for t in all_trades), Decimal('0.0'))
    
    levels = []
    current_cumulative_profit = Decimal('0.0')
    reserve = Decimal('0.0')

    # ساخت جدول پویای مراحل (تا ۱۰۰ مرحله)
    for lvl in range(1, 101):
        risk_per_trade = risk_from_reserve(reserve, base_risk, max_reserve_hearts)
        reserve_hearts = int(reserve / risk_per_trade) if risk_per_trade > 0 else 0

        target_hearts = 4
        stage_profit = risk_per_trade * Decimal(str(target_hearts))
        current_cumulative_profit += stage_profit
        reserve += stage_profit

        has_payout = (lvl % 4 == 0)
        payout_amount = reserve * Decimal('0.10') if has_payout else Decimal('0.0')
        reserve -= payout_amount

        levels.append({
            'level_num': lvl,
            'risk_per_trade': risk_per_trade,
            'target_hearts': target_hearts,
            'reserve_hearts': reserve_hearts,
            'stage_profit': stage_profit,
            'cumulative_profit': current_cumulative_profit,
            'has_payout': has_payout,
            'payout_amount': payout_amount
        })

    earned_hearts_in_level = 0
    required_recovery_hearts = 0
    is_in_loss = False
    trend_status = ""
    trend_color = "text-slate-400"

    # 🛑 سناریوی اول: حساب در مجموع در ضرر است (منفی)
    if total_net_profit < 0:
        is_in_loss = True
        active_level_idx = 0
        current_level = levels[0]
        next_4_levels = levels[0:4]
        
        first_stage_target = levels[0]['cumulative_profit']
        calculated_drawdown = (abs(total_net_profit) / first_stage_target) * Decimal('100')
        completion_percentage = min(100, max(1, int(calculated_drawdown)))
        
        # قلب‌های لازم برای جبران بر اساس ریسک سطح فعلی
        profit_per_heart = current_level['risk_per_trade']
        if profit_per_heart > 0:
            div_result = abs(total_net_profit) / profit_per_heart
            required_recovery_hearts = int(div_result.to_integral_value(rounding=ROUND_CEILING))
        else:
            required_recovery_hearts = 1
            
        trend_status = f"حساب در ضرر (-{abs(total_net_profit):.1f}$): نیازمند جبران ⚠️"
        trend_color = "text-rose-400"
        
    # 📈 سناریوی دوم: حساب در سود است (پیدا کردن سطح فعال بر اساس سود واقعی)
    else:
        is_in_loss = False
        active_level_idx = 0
        for i, lvl in enumerate(levels):
            if total_net_profit < lvl['cumulative_profit']:
                active_level_idx = i
                break
        else:
            active_level_idx = len(levels) - 1
            
        current_level = levels[active_level_idx]
        start_show_idx = max(0, active_level_idx)
        next_4_levels = levels[start_show_idx : start_show_idx + 4]
        
        prev_cumulative = levels[active_level_idx - 1]['cumulative_profit'] if active_level_idx > 0 else Decimal('0.0')
        progress_in_level = total_net_profit - prev_cumulative
        level_span = current_level['stage_profit']
        
        if level_span > 0:
            pct_calc = (progress_in_level / level_span) * Decimal('100')
            completion_percentage = min(100, max(0, int(pct_calc.quantize(Decimal('1'), rounding=ROUND_HALF_UP))))
        else:
            completion_percentage = 0
            
        profit_per_heart = current_level['risk_per_trade']
        if profit_per_heart > 0:
            earned_hearts_in_level = int(progress_in_level / profit_per_heart)
        else:
            earned_hearts_in_level = 0
            
        # تشخیص وضعیت روند برای نمایش پیام‌های جذاب
        if completion_percentage >= 75:
            trend_status = "نزدیک به پایان مرحله (عالی) 🚀"
            trend_color = "text-emerald-400"
        elif completion_percentage > 0:
            trend_status = "در حال پیشروی در مرحله 📈"
            trend_color = "text-sky-400"
        else:
            trend_status = "در آستانه شروع مرحله جدید 📊"
            trend_color = "text-amber-400"

    return {
        'current_level_num': current_level['level_num'],
        'current_risk': current_level['risk_per_trade'],
        'completion_percentage': completion_percentage,
        'is_in_loss': is_in_loss,
        'earned_hearts_in_level': earned_hearts_in_level,
        'required_recovery_hearts': required_recovery_hearts,
        'target_hearts': current_level['target_hearts'],
        'next_4_levels': next_4_levels,
        'trend_status': trend_status,
        'trend_color': trend_color,
    }

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
    account = TelegramUser.objects.filter(is_blocked=False).first()
    if account:
        initial_balance = account.initial_balance
    else:
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

    # محاسبه پویا و دقیق ارزیابی سطح ریسک حساب بر اساس Drawdown
    abs_dd = abs(float(max_drawdown))
    if abs_dd <= 5.0:
        risk_level_label = "پایین (ایمن)"
        risk_level_color = "text-emerald-400"
    elif abs_dd <= 15.0:
        risk_level_label = "متوسط (کنترل‌شده)"
        risk_level_color = "text-sky-400"
    elif abs_dd <= 25.0:
        risk_level_label = "بالا (هشدار)"
        risk_level_color = "text-amber-400"
    else:
        risk_level_label = "بسیار بالا (خطرناک)"
        risk_level_color = "text-rose-400"

    abs_gain_percent = abs(gain_percent)

    # تبدیل درصد به زاویه (۳۶۰ درجه) برای CSS conic-gradient
    # محدود کردن حداکثر زاویه به ۳۶۰ درجه
    gauge_angle = min(360, int((abs_gain_percent / 100) * 360)) if abs_gain_percent > 0 else 0
    # تبدیل درصد وین‌ریت به زاویه برای حلقه دایره‌ای (۳۶۰ درجه)
    win_rate_angle = int((win_rate / 100) * 360) if win_rate > 0 else 0
    # تبدیل Profit Factor به زاویه (فرض: حداکثر مطلوب ۳.۰ معادل دایره کامل ۳۶۰ درجه)
    pf_angle = min(360, int((float(profit_factor) / 3.0) * 360)) if profit_factor > 0 else 0
    # تبدیل حداکثر افت حساب به زاویه (فرض: حداکثر مقیاس بحرانی ۵۰٪ افت معادل ۳۶۰ درجه)
    # اگر خواستید حساس‌تر یا بازتر باشد می‌توانید عدد مخرج (50.0) را تغییر دهید
    dd_angle = min(360, int((abs(float(max_drawdown)) / 50.0) * 360)) if max_drawdown else 0

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
        'risk_level_label': risk_level_label,
        'risk_level_color': risk_level_color,
        'gain_percent': gain_percent,
        'abs_gain_percent': abs_gain_percent,
        'gauge_angle': gauge_angle,
        'win_rate_angle': win_rate_angle,
        'pf_angle': pf_angle,
        'dd_angle': dd_angle,
    }

    # دریافت سال و ماه از پارامترهای URL یا استفاده از ماه جاری
    req_year = int(request.GET.get('year', timezone.now().year))
    req_month = int(request.GET.get('month', timezone.now().month))
    
    cal_data = get_monthly_calendar_data(req_year, req_month,initial_balance)
    context.update(cal_data)

    # دریافت پلن پیشرفت کاربر
    user_progression_plan = get_user_progression_plan(account) if account else {}
    context.update(user_progression_plan)

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
    return backup_database()


def list_backups_view(request):
   return get_backups()

@require_POST
def restore_backup_view(request):
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "متد غیرمجاز"}, status=400)

    data = json.loads(request.body)
    filename = data.get("filename")
    if not filename:
        return JsonResponse({"status": "error", "message": "نام فایل مشخص نشده است"}, status=400)

    return restore_backup(filename)


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