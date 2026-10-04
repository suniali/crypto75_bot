import re
import os
import logging
from datetime import datetime, timezone,timedelta
from django.utils import timezone as t
from django.core.files import File
from decimal import Decimal, InvalidOperation
from typing import Dict, Any, Tuple
from asgiref.sync import sync_to_async
from django.core.files.base import ContentFile

from bot_app.models import TradeJournal, TelegramUser, MarketType

logger = logging.getLogger(__name__)


def dict_to_formatted_text(data: dict) -> str:
    """
    دیکشنری معامله را به فرمت متنی تمام‌فیلد تبدیل می‌کند
    تا کاربر تمام کلیدها را ببیند و بتواند مقادیر خالی یا موجود را ویرایش کند.
    """
    if not isinstance(data, dict):
        data = {}

    lines = []

    # ساختار کامل فیلدها همراه با مقادیر پیش‌فرض
    # اگر مقداری در data وجود داشته باشد جایگزین می‌شود، در غیر این صورت مقدار پیش‌فرض قرار می‌گیرد
    fields = [
        ('symbol', 'SYMBOL', 'BTCUSDT'),
        ('trade_type', 'TYPE', 'BUY'),
        ('volume', 'VOLUME', '0.01'),
        ('entry_price', 'ENTRY', ''),
        ('exit_price', 'EXIT', ''),
        ('stop_loss', 'SL', ''),
        ('take_profit', 'TP', ''),
        ('profit', 'PROFIT', '0.00'),
        ('commission', 'COMMISSION', '0.00'),
        ('swap', 'SWAP', '0.00'),
        ('result', 'RESULT', 'PENDING'),
        ('entry_time', 'ENTRY_TIME', ''),
        ('exit_time', 'EXIT_TIME', ''),
        ('notes', 'NOTE', '')
    ]

    for key, label, default_val in fields:
        val = data.get(key)

        # اگر مقدار None یا رشته خالی بود، از default_val استفاده کن
        if val is None or str(val).strip() == '':
            display_val = default_val
        else:
            display_val = str(val).strip()

        lines.append(f"{label}: {display_val}")

    return "\n".join(lines)


# ==========================================
# 1. پارسر متنی اصلاح‌شده و مقاوم
# ==========================================
def format_user_time(time_str: str):
    if not time_str:
        return None

    now = t.now()  # خود این Aware است
    time_str = time_str.strip()

    # ۱. پردازش کلمات میانبر
    if time_str.lower() in ['امروز', 'today']:
        return now

    if time_str.lower() in ['دیروز', 'yesterday']:
        return now - timedelta(days=1)

    # ۲. اگر فقط ساعت زده بود (مثل 12:48)
    if re.match(r'^\d{1,2}:\d{2}(?::\d{2})?$', time_str):
        today_date = now.strftime('%Y-%m-%d')
        time_str = f"{today_date} {time_str}"

    # ۳. تبدیل رشته به شیء datetime و اضافه کردن Timezone برای جانگو
    formats = [
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y/%m/%d %H:%M:%S",
    ]

    for fmt in formats:
        try:
            dt = datetime.strptime(time_str, fmt)
            # 💥 کلید حل RuntimeWarning اینجاست:
            return t.make_aware(dt)
        except ValueError:
            continue

    return None

def parse_trade_text(text: str) -> Dict[str, Any]:
    data = {}

    # الگوی عمومی اعداد اعشاری/منفی/مثبت
    num_pattern = r'[-+]?\d*(?:\.\d+)?'

    patterns = {
        'symbol': r'(?:SYMBOL|نماد|جفت\s*ارز)[\s:=]+([A-Za-z0-9/._-]+)',
        'trade_type': r'(?:TYPE|نوع|پوزیشن)[\s:=]+(BUY|SELL|خرید|فروش)',
        'entry_price': rf'(?:ENTRY|ورود|قیمت\s*ورود)[\s:=]+({num_pattern})',
        'exit_price': rf'(?:EXIT|خروج|قیمت\s*خروج)[\s:=]+({num_pattern})',
        'stop_loss': rf'(?:SL|استاپ|حد\s*ضرر)[\s:=]+({num_pattern})',
        'take_profit': rf'(?:TP|تارگت|حد\s*سود)[\s:=]+({num_pattern})',
        'volume': rf'(?:VOLUME|LOT|حجم)[\s:=]+({num_pattern})',
        'profit': rf'(?:PROFIT|PNL|سود|زیان)[\s:=]+({num_pattern})',
        'commission': rf'(?:COMMISSION|COMM|کمیسیون)[\s:=]+({num_pattern})',
        'swap': rf'(?:SWAP|سواپ)[\s:=]+({num_pattern})',
        # پشتیبانی از نیم‌فاصله (\u200c) و فاصله معمولی (\s*) در یر‌به‌یر و سایر کلمات
        'result': r'(?:RESULT|نتیجه)[\s:=]+(WIN|LOSS|BE|PENDING|وین|لوس|برد|باخت|یر[\s\u200c]*به[\s\u200c]*یر|بسته\s*شد)',
        'entry_time': r'(?:ENTRY_TIME|زمان\s*ورود)[\s:=]+(\d{4}[-/.]\d{1,2}[-/.]\d{1,2}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?|\d{1,2}:\d{2}|\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|امروز|دیروز)',
        'exit_time': r'(?:EXIT_TIME|زمان\s*خروج)[\s:=]+(\d{4}[-/.]\d{1,2}[-/.]\d{1,2}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?|\d{1,2}:\d{2}|\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|امروز|دیروز)',

        'notes': r'(?:NOTE|NOTES|توضیحات|استراتژی)[\s:=]+([^\n]+)',
    }

    for key, pattern in patterns.items():
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            val = match.group(1).strip()

            # استانداردسازی زمان‌های ورودی
            if key in ['entry_time', 'exit_time']:
                val = format_user_time(val)

            # تبدیل‌های خاص و استانداردسازی خروجی
            if key == 'trade_type':
                val = 'BUY' if val.upper() in ['BUY', 'خرید'] else 'SELL'

            elif key == 'result':
                res_map = {
                    'WIN': 'WIN', 'وین': 'WIN', 'برد': 'WIN',
                    'LOSS': 'LOSS', 'لوس': 'LOSS', 'باخت': 'LOSS',
                    'BE': 'BE', 'یربه‌یر': 'BE',
                    'PENDING': 'PENDING'
                }
                val = res_map.get(val.upper(), 'PENDING')

            data[key] = val

    return data


# ==========================================
# 2. سرویس ذخیره در دیتابیس (مستقل از تلگرام)
# ==========================================

@sync_to_async
def create_trade_from_dict(user_id: int, trade_data: Dict[str, Any],image_path: str = None) -> Tuple[TradeJournal, str]:
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
        now = t.now()
        entry_time = trade_data.get('entry_time') or now
        exit_time = trade_data.get('exit_time') or (now if exit_price else None)

        # تشخیص نوع مارکت
        market_type = MarketType.FOREX if any(
            c in symbol for c in ['EUR', 'USD', 'GBP', 'JPY', 'XAU', 'AUD', 'CAD']) else MarketType.CRYPTO

        # ایجاد رکورد کامل ژورنال
        trade = TradeJournal.objects.create(
            user=user,
            symbol=symbol,
            position_id=None,
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
        # ۲. اگر تصویر وجود داشت، آن را روی فیلد image ذخیره می‌کنیم
        if image_path and os.path.exists(image_path):
            filename = os.path.basename(image_path)
            with open(image_path, 'rb') as f:
                trade.image.save(filename, File(f), save=False)

        trade.save()
        return trade, "✅ معامله کامل با موفقیت در ژورنال ثبت شد."

    except (InvalidOperation, ValueError):
        return None, "❌ فرمت اعداد وارد شده (قیمت/استاپ/سواپ/کمیسیون) صحیح نیست."
    except TelegramUser.DoesNotExist:
        return None, "❌ کاربر یافت نشد."
    except Exception as e:
        return None, f"❌ خطای سیستم: {str(e)}"

@sync_to_async
def sync_open_trade_to_db(chat_id: int, pos_data: dict):
    """ثبت یا آپدیت پوزیشن باز در دیتابیس ژورنال"""
    try:
        user_obj, _ = TelegramUser.objects.get_or_create(chat_id=chat_id)

        position_id = pos_data.get("ticket") or pos_data.get("position_id")
        if not position_id:
            return

        open_timestamp = pos_data.get("time", 0)
        if open_timestamp > 0:
            entry_time = datetime.fromtimestamp(open_timestamp, tz=timezone.utc)
        else:
            entry_time = datetime.now(timezone.utc)

        symbol = pos_data.get("symbol", "")
        trade_type = str(pos_data.get("type", "BUY")).upper()

        entry_price = pos_data.get("price_open", 0.0) or pos_data.get("entry_price", 0.0)
        sl_price = pos_data.get("sl", 0.0)
        tp_price = pos_data.get("tp", 0.0)
        volume = pos_data.get("volume", 0.0)

        TradeJournal.objects.get_or_create(
            position_id=position_id,
            defaults={
                'user': user_obj,
                'symbol': symbol,
                'market_type': MarketType.FOREX,
                'trade_type': trade_type,
                'volume': volume,
                'entry_price': entry_price,
                'stop_loss': sl_price,
                'take_profit': tp_price,
                'entry_time': entry_time,
                'is_active': True,
            }
        )
    except Exception as e:
        logger.error(f"❌ خطا در ثبت پوزیشن باز {pos_data.get('ticket')}: {e}", exc_info=True)

@sync_to_async
def get_sl_tp_from_db(position_id):
    trade = TradeJournal.objects.filter(position_id=position_id).first()
    if trade:
        return trade.stop_loss, trade.take_profit
    return None, None

@sync_to_async
def finalize_closed_trade_in_db(chat_id: int, deal: dict, chart_buf=None):
    """ثبت نهایی معامله بسته‌شده، ذخیره سود خالص و چارت در ژورنال"""
    try:
        user_obj, _ = TelegramUser.objects.get_or_create(chat_id=chat_id)

        position_id = deal.get("position_id") or deal.get("ticket") or deal.get("deal_id")
        if not position_id:
            return

        # سود خالص از قبل در check_recent_closed_positions درست محاسبه شده است
        net_profit = deal.get("profit", 0.0)
        gross_profit=deal.get("gross_profit",0.0)
        commission = deal.get("commission", 0.0)
        swap = deal.get("swap", 0.0)

        result = "WIN" if net_profit > 0 else "LOSS" if net_profit < 0 else "BREAKEVEN"

        exit_timestamp = deal.get("time", 0)
        if exit_timestamp > 0:
            exit_time = datetime.fromtimestamp(exit_timestamp, tz=timezone.utc)
        else:
            exit_time = datetime.now(timezone.utc)

        trade_obj = TradeJournal.objects.filter(position_id=position_id).first()

        if not trade_obj:
            trade_obj, created = TradeJournal.objects.get_or_create(
                position_id=position_id,
                user=user_obj,
                symbol=deal.get("symbol", ""),
                market_type=MarketType.FOREX,
                volume=deal.get("volume", 0.0),
                entry_price=deal.get("entry_price", 0.0),
            )

        # به روزرسانی فیلدهای مربوط به خروج
        trade_obj.exit_price = deal.get("exit_price", 0.0)
        trade_obj.exit_time = exit_time
        trade_obj.profit = gross_profit
        trade_obj.commission = commission
        trade_obj.swap = swap
        trade_obj.result = result

        if chart_buf:
            chart_buf.seek(0)
            filename = f"chart_{position_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            trade_obj.image.save(filename, ContentFile(chart_buf.read()), save=False)

        trade_obj.save()
        logger.info(f"✅ معامله {position_id} با موفقیت در ژورنال ثبت نهایی شد.")

    except Exception as e:
        logger.error(f"❌ خطا در ثبت نهایی معامله بسته‌شده {deal}: {e}", exc_info=True)