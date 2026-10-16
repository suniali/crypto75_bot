import calendar
from datetime import  date
from decimal import Decimal
from collections import defaultdict

from bot_app.models import TradeJournal

def get_monthly_calendar_data(year=None, month=None,initial_balance=Decimal('100.00')):
    today = date.today()
    if not year:
        year = today.year
    if not month:
        month = today.month

    # استخراج تمام تریدهای فعال مربوط به همین سال و ماهِ مشخص‌شده (مستقل از فیلترهای دیگر)
    month_trades_qs = TradeJournal.active_objects.filter(
        entry_time__year=year,
        entry_time__month=month
    )

    cal = calendar.Calendar(firstweekday=6) # شروع هفته از یکشنبه
    month_days = cal.monthdatescalendar(year, month)

    calendar_weeks = []
    month_total_pnl = Decimal('0.00')
    month_trades_count = 0
    month_wins_count = 0

    for week in month_days:
        week_days_data = []
        week_total_pnl = Decimal('0.00')

        for day_date in week:
            is_current_month = (day_date.month == month)
            
            # فیلتر تریدهای این روز خاص از میان تریدهای همین ماه
            day_trades = [t for t in month_trades_qs if t.entry_time and t.entry_time.date() == day_date] if is_current_month else []

            day_pnl = Decimal('0.00')
            day_count = len(day_trades)
            
            if is_current_month and day_count > 0:
                day_pnl = sum(Decimal(str(t.net_profit or 0)) for t in day_trades)
                
                month_total_pnl += day_pnl
                month_trades_count += day_count
                month_wins_count += sum(1 for t in day_trades if t.result == 'WIN')
                week_total_pnl += day_pnl

            # محاسبه درصد نسبت به بالانس پایه
            day_pnl_percent = round(float(day_pnl / initial_balance * 100), 2) if initial_balance > 0 else 0.0

            # تعیین وضعیت بصری روز
            if not is_current_month:
                status = 'other_month'
            elif day_count == 0:
                status = 'empty'
            elif day_pnl > 0:
                status = 'profit'
            elif day_pnl < 0:
                status = 'loss'
            else:
                status = 'be'

            week_days_data.append({
                'day_num': day_date.day,
                'date_str': day_date.strftime('%Y-%m-%d'),
                'pnl': float(round(day_pnl, 2)),
                'pnl_percent': day_pnl_percent,
                'count': day_count,
                'status': status,
                'is_current_month': is_current_month,
            })

        week_percent = round(float(week_total_pnl / initial_balance * 100), 2) if initial_balance > 0 else 0.0
        calendar_weeks.append({
            'days': week_days_data,
            'week_total_pnl': float(round(week_total_pnl, 2)),
            'week_percent': week_percent,
        })

    month_name = date(year, month, 1).strftime('%B %Y')
    month_percent = round(float(month_total_pnl / initial_balance * 100), 2) if initial_balance > 0 else 0.0

    return {
        'calendar_weeks': calendar_weeks,
        'month_name': month_name,
        'current_year': year,
        'current_month': month,
        'month_total_pnl': float(round(month_total_pnl, 2)),
        'month_percent': month_percent,
        'month_trades_count': month_trades_count,
        'month_wins_count': month_wins_count,
    }


def calculate_streaks(processed_trades):
    """محاسبه دقیق بردهای متوالی و باخت‌های متوالی (معاملات و روزها)"""
    max_win_streak_trades = 0
    max_loss_streak_trades = 0
    current_win_trades = 0
    current_loss_trades = 0

    # بررسی معاملات به ترتیب زمان ثبت
    for trade in processed_trades:
        res = str(trade.result).upper() if trade.result else ''
        if res == 'WIN':
            current_win_trades += 1
            current_loss_trades = 0
            if current_win_trades > max_win_streak_trades:
                max_win_streak_trades = current_win_trades
        elif res == 'LOSS':
            current_loss_trades += 1
            current_win_trades = 0
            if current_loss_trades > max_loss_streak_trades:
                max_loss_streak_trades = current_loss_trades
        elif res == 'BE':
            # پوزیشن‌های سربه‎سر زنجیره استریک را قطع نمی‌کنند اما برد/باخت هم محسوب نمی‌شوند
            pass
        else:
            current_win_trades = 0
            current_loss_trades = 0

    # محاسبه متوالی در سطح روزها بر اساس سود/زیان روزانه
    daily_pnls = defaultdict(Decimal)
    for trade in processed_trades:
        t_date = (trade.entry_time or trade.created_at).date()
        daily_pnls[t_date] += (trade.net_profit or Decimal('0.00'))

    sorted_dates = sorted(daily_pnls.keys())
    max_win_streak_days = 0
    max_loss_streak_days = 0
    current_win_days = 0
    current_loss_days = 0

    for d in sorted_dates:
        pnl = daily_pnls[d]
        if pnl > 0:
            current_win_days += 1
            current_loss_days = 0
            if current_win_days > max_win_streak_days:
                max_win_streak_days = current_win_days
        elif pnl < 0:
            current_loss_days += 1
            current_win_days = 0
            if current_loss_days > max_loss_streak_days:
                max_loss_streak_days = current_loss_days
        else:
            current_win_days = 0
            current_loss_days = 0

    return {
        'max_win_streak_trades': max_win_streak_trades,
        'max_loss_streak_trades': max_loss_streak_trades,
        'max_win_streak_days': max_win_streak_days,
        'max_loss_streak_days': max_loss_streak_days,
    }