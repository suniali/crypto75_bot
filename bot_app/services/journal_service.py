import re
from decimal import Decimal, InvalidOperation
from typing import Dict, Any, Tuple
from asgiref.sync import sync_to_async
from django.utils import timezone

from bot_app.models import TradeJournal, TelegramUser, MarketType


def dict_to_formatted_text(data: dict) -> str:
    """
    دیکشنری استخراج‌شده از معامله را به فرمت متنی کلید-مقدار تبدیل می‌کند
    تا کاربر بتواند آن را در تلگرام کپی و ویرایش کند.
    """
    if not isinstance(data, dict):
        return str(data)

    lines = []
    # ترتیب و نام کلیدهایی که می‌خواهیم در متن نمایش داده شوند
    field_map = {
        'symbol': 'SYMBOL',
        'trade_type': 'TYPE',
        'volume': 'VOLUME',
        'entry_price': 'ENTRY',
        'exit_price': 'EXIT',
        'stop_loss': 'SL',
        'take_profit': 'TP',
        'profit': 'PROFIT',
        'commission': 'COMMISSION',
        'swap': 'SWAP',
        'notes': 'NOTE'
    }

    for key, label in field_map.items():
        val = data.get(key)
        # فقط فیلدهایی که مقدار دارند را به متن اضافه می‌کند
        if val is not None and str(val).strip() != '':
            lines.append(f"{label}: {val}")

    # اگر دیکشنری خالی بود یک نمونه پیش‌فرض برمی‌گرداند
    return "\n".join(lines) if lines else "SYMBOL: BTCUSDT\nTYPE: BUY\nENTRY: 65000"

# ==========================================
# 1. پارسر متنی (مستقل از تلگرام)
# ==========================================
def parse_trade_text(text: str) -> Dict[str, Any]:
    data = {}

    patterns = {
        'symbol': r'(?:SYMBOL|نماد|جفت\s*ارز)[\s:=]+([A-Za-z0-9/._-]+)',
        'trade_type': r'(?:TYPE|نوع|پوزیشن)[\s:=]+(BUY|SELL|خرید|فروش)',
        'entry_price': r'(?:ENTRY|ورود|قیمت\s*ورود)[\s:=]+([0-9.]+)',
        'exit_price': r'(?:EXIT|خروج|قیمت\s*خروج)[\s:=]+([0-9.]+)',
        'stop_loss': r'(?:SL|استاپ|حد\s*ضرر)[\s:=]+([0-9.]+)',
        'take_profit': r'(?:TP|تارگت|حد\s*سود)[\s:=]+([0-9.]+)',
        'volume': r'(?:VOLUME|LOT|حجم)[\s:=]+([0-9.]+)',
        'profit': r'(?:PROFIT|PNL|سود|زیان)[\s:=]+([-+]?[0-9.]+)',
        'commission': r'(?:COMMISSION|COMM|کمیسیون)[\s:=]+([-+]?[0-9.]+)',
        'swap': r'(?:SWAP|سواپ)[\s:=]+([-+]?[0-9.]+)',
        'result': r'(?:RESULT|نتیجه)[\s:=]+(WIN|LOSS|BE|PENDING|وین|لوس|وین‌شد|بسته‌شد)',
        'notes': r'(?:NOTE|NOTES|توضیحات|استراتژی)[\s:=]+(.+)',
        'entry_time': r'(?:ENTRY_TIME|زمان\s*ورود)[\s:=]+([\d{4}/\d{2}/\d{2}\s\d{2}:\d{2}|\d{2}:\d{2}|امروز|دیروز]+)',
        'exit_time': r'(?:EXIT_TIME|زمان\s*خروج)[\s:=]+([\d{4}/\d{2}/\d{2}\s\d{2}:\d{2}|\d{2}:\d{2}|امروز|دیروز]+)',
    }

    for key, pattern in patterns.items():
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            val = match.group(1).strip()

            # تبدیل‌های خاص استانداردساز
            if key == 'trade_type':
                val = 'BUY' if val.upper() in ['BUY', 'خرید'] else 'SELL'
            elif key == 'result':
                res_map = {'WIN': 'WIN', 'وین': 'WIN', 'LOSS': 'LOSS', 'لوس': 'LOSS', 'BE': 'BE'}
                val = res_map.get(val.upper(), 'PENDING')

            data[key] = val

    return data


# ==========================================
# 2. سرویس ذخیره در دیتابیس (مستقل از تلگرام)
# ==========================================
@sync_to_async
def create_trade_from_dict(user_id: int, trade_data: Dict[str, Any]) -> Tuple[TradeJournal, str]:
    """
    اعتبارسنجی کامل داده‌ها و محاسبه سود خالص و ثبت در دیتابیس TradeJournal
    قابل استفاده مشترک در Telegram Bot / Django View / REST API
    """
    try:
        # به جای get، از get_or_create استفاده کنید
        user, created = TelegramUser.objects.get_or_create(
            chat_id=user_id,
            defaults={'username': trade_data.get('username', '')}
        )

        symbol = trade_data.get('symbol', '').upper()
        if not symbol:
            return None, "❌ نماد معاملاتی (SYMBOL) مشخص نشده است."

        trade_type = trade_data.get('trade_type', 'BUY').upper()

        # قیمت‌ها و فیلدهای اصلی
        entry_price = Decimal(trade_data.get('entry_price', '0'))
        if entry_price <= 0:
            return None, "❌ قیمت ورود (ENTRY) الزامی و باید بزرگتر از 0 باشد."

        exit_price = Decimal(trade_data['exit_price']) if trade_data.get('exit_price') else None
        stop_loss = Decimal(trade_data['stop_loss']) if trade_data.get('stop_loss') else None
        take_profit = Decimal(trade_data['take_profit']) if trade_data.get('take_profit') else None
        volume = Decimal(trade_data.get('volume', '0.01'))

        # هزینه و سود (Swap, Commission, Profit)
        profit = Decimal(trade_data.get('profit', '0.00'))
        commission = Decimal(trade_data.get('commission', '0.00'))
        swap = Decimal(trade_data.get('swap', '0.00'))

        # تعیین خودکار نتیجه (اگر خروج ثبت شده ولی نتیجه درج نشده)
        result = trade_data.get('result', 'PENDING')
        if result == 'PENDING' and exit_price:
            net_pnl = profit + commission + swap
            if net_pnl > 0:
                result = 'WIN'
            elif net_pnl < 0:
                result = 'LOSS'
            else:
                result = 'BE'

        # مدیریت زمان‌بندی ورود و خروج
        now = timezone.now()
        entry_time = now  # در صورت عدم ارسال، زمان جاری ست می‌شود
        exit_time = now if exit_price else None

        # تشخیص نوع مارکت
        market_type = MarketType.FOREX if any(
            c in symbol for c in ['EUR', 'USD', 'GBP', 'JPY', 'XAU', 'AUD', 'CAD']) else MarketType.CRYPTO

        # ایجاد رکورد کامل ژورنال
        trade = TradeJournal.objects.create(
            user=user,
            symbol=symbol,
            trade_type=trade_type,
            market_type=market_type,
            volume=volume,
            entry_price=entry_price,
            exit_price=exit_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            profit=profit,
            commission=commission,
            swap=swap,
            result=result,
            notes=trade_data.get('notes', 'ثبت متنی/دستی'),
            entry_time=entry_time,
            exit_time=exit_time,
            is_active=True
        )
        return trade, "✅ معامله کامل با موفقیت در ژورنال ثبت شد."

    except (InvalidOperation, ValueError):
        return None, "❌ فرمت اعداد وارد شده (قیمت/استاپ/سواپ/کمیسیون) صحیح نیست."
    except TelegramUser.DoesNotExist:
        return None, "❌ کاربر یافت نشد."
    except Exception as e:
        return None, f"❌ خطای سیستم: {str(e)}"