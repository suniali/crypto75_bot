import calendar
from datetime import datetime, date, timedelta
from decimal import Decimal

from bot_app.models import TradeJournal

def get_monthly_calendar_data(year=None, month=None):
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
                'count': day_count,
                'status': status,
                'is_current_month': is_current_month,
            })

        calendar_weeks.append({
            'days': week_days_data,
            'week_total_pnl': float(round(week_total_pnl, 2))
        })

    month_name = date(year, month, 1).strftime('%B %Y')

    return {
        'calendar_weeks': calendar_weeks,
        'month_name': month_name,
        'current_year': year,
        'current_month': month,
        'month_total_pnl': float(round(month_total_pnl, 2)),
        'month_trades_count': month_trades_count,
        'month_wins_count': month_wins_count,
    }