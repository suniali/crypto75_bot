import re
import json
import logging
import hashlib
from pathlib import Path
from decouple import config

from datetime import datetime
from django.utils import timezone
from django.shortcuts import render
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse

from .models import TradeJournal,TradeDecisionLog
from .services.bakup_restore_service import backup_database,get_backups,restore_backup
from bot_app.services.analysis_service import calculate_trading_metrics, analyze_trades_with_gemini


logger = logging.getLogger(__name__)
CACHE_DIR = Path("ai_analysis_cache")
CACHE_DIR.mkdir(exist_ok=True)

CURENCY_UNIT=config("CURENCY_UNIT", default="$")
VERSION=config("VERSION", default="1.0.0")

def _pick_num(features: dict, keys: list[str], default=0):
    if not isinstance(features, dict):
        return default
    for k in keys:
        val = features.get(k)
        if val is not None and val != "":
            try:
                num = float(val)
                if num != 0:
                    return num
            except (TypeError, ValueError):
                return val
    return default


def _parse_nds_explanation_prices(text: str) -> dict[str, float]:
    """استخراج قیمت‌های ورود، حد ضرر، هدف ۱ و هدف نهایی از متن nds_explanation."""
    if not isinstance(text, str) or not text:
        return {}

    patterns = {
        "entry_price": r"ورود:\s*([0-9]+(?:\.[0-9]+)?)",
        "stop_loss": r"حد ضرر[^:]*:\s*([0-9]+(?:\.[0-9]+)?)",
        "tp1": r"هدف\s*۱[^:]*:\s*([0-9]+(?:\.[0-9]+)?)",
        "take_profit": r"هدف نهایی[^:]*:\s*([0-9]+(?:\.[0-9]+)?)",
    }

    parsed: dict[str, float] = {}
    for field, pat in patterns.items():
        match = re.search(pat, text)
        if match:
            try:
                parsed[field] = float(match.group(1))
            except ValueError:
                pass
    return parsed


def _serialize_signal(sig, include_notification: bool = False) -> dict:
    features = sig.features_snapshot or {}
    raw_strategy = str(features.get("strategy_type") or features.get("strategy") or "").upper()
    is_super_scalp = "SCALP" in raw_strategy
    strategy_type = "SUPER_SCALP" if is_super_scalp else "NDS"

    # استخراج تایم‌فریم از features یا nds_key (مثلاً XAUUSD_l_M1_...)
    timeframe = str(features.get("timeframe") or "")
    if not timeframe and isinstance(features.get("nds_key"), str):
        parts = features["nds_key"].split("_")
        if len(parts) >= 3:
            timeframe = parts[2]
    if not timeframe and is_super_scalp:
        timeframe = "M1"

    parsed_prices = _parse_nds_explanation_prices(features.get("nds_explanation", ""))

    entry_price = _pick_num(features, ["entry_price", "entry", "price"], parsed_prices.get("entry_price", 0))
    stop_loss = _pick_num(features, ["stop_loss", "sl"], parsed_prices.get("stop_loss", 0))
    tp1 = _pick_num(features, ["tp1"], parsed_prices.get("tp1", 0))
    take_profit = _pick_num(features, ["take_profit", "tp_final", "tp"], parsed_prices.get("take_profit", 0))
    calculated_volume = _pick_num(features, ["calculated_volume", "volume", "lot"], 0)
    risk_amount_usd = _pick_num(features, ["risk_amount_usd", "risk_usd"], 0)
    risk_percentage = _pick_num(features, ["risk_percentage"], 0)
    tp1_rr = _pick_num(features, ["tp1_rr"], 0)
    final_rr = _pick_num(features, ["final_rr"], 0)

    ai_prob = _pick_num(features, ["ai_prob", "ai_confidence"], None)
    if ai_prob is None and getattr(sig, "ai_confidence", None) is not None:
        ai_prob = float(sig.ai_confidence)

    ai_approved = features.get("ai_approved")
    if ai_approved is None and ai_prob is not None:
        threshold = _pick_num(features, ["ai_threshold"], 0.55)
        ai_approved = bool(ai_prob >= threshold)

    payload = {
        "id": sig.id,
        "action": sig.action,
        "symbol": sig.symbol,
        "strategy_type": strategy_type,
        "strategy_raw": raw_strategy,
        "is_super_scalp": is_super_scalp,
        "timeframe": timeframe,
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "tp1": tp1,
        "take_profit": take_profit,
        "calculated_volume": calculated_volume,
        "risk_amount_usd": risk_amount_usd,
        "risk_percentage": risk_percentage,
        "tp1_rr": tp1_rr,
        "final_rr": final_rr,
        "is_reentry_864": bool(float(features.get("is_reentry_864") or 0) == 1),
        "ai_prob": ai_prob,
        "ai_approved": ai_approved,
    }

    if include_notification:
        status_map = {
            "none": {"text": "ثبت شده", "color": "text-slate-400"},
            "pending": {"text": "در حال ارسال", "color": "text-amber-400"},
            "sent": {"text": "ارسال شد", "color": "text-emerald-400"},
            "failed": {"text": "خطا در ارسال", "color": "text-rose-400"},
        }
        status_info = status_map.get(sig.notification_status, status_map["none"])
        payload.update({
            "time": sig.timestamp.strftime("%Y-%m-%d %H:%M") if getattr(sig, "timestamp", None) else "-",
            "notification_status": sig.notification_status,
            "notification_text": status_info["text"],
            "notification_color": status_info["color"],
            "notification_attempts": sig.notification_attempts,
        })

    return payload

@login_required
def api_check_new_signals(request):
    last_seen_id = request.GET.get("last_seen_id")

    if last_seen_id is None:
        latest = TradeDecisionLog.objects.filter(action__in=["BUY", "SELL"]).order_by("-id").first()
        latest_id = latest.id if latest else 0
        return JsonResponse({"status": "init", "last_id": latest_id})

    try:
        last_seen_id = int(last_seen_id)
    except (TypeError, ValueError):
        return JsonResponse({"status": "error", "message": "Invalid last_seen_id"}, status=400)

    new_signals = TradeDecisionLog.objects.filter(
        id__gt=last_seen_id,
        action__in=["BUY", "SELL"],
    ).order_by("id")

    signals_data = [_serialize_signal(sig, include_notification=False) for sig in new_signals]
    return JsonResponse({"status": "success", "new_signals": signals_data})


@login_required
def api_latest_signal(request):
    """آخرین سیگنال و وضعیت ارسال آن را برمی‌گرداند."""
    latest = TradeDecisionLog.objects.filter(
        action__in=["BUY", "SELL"],
    ).order_by("-id").first()

    if not latest:
        return JsonResponse({"status": "no_signal"})

    return JsonResponse({
        "status": "success",
        "signal": _serialize_signal(latest, include_notification=True),
    })

def generate_trades_hash(processed_trades) -> str:
    """تولید یک اثر انگشت (Hash) یکتا بر اساس شناسه، زمان و وضعیت تریدها"""
    hasher = hashlib.sha256()
    for t in processed_trades:
        # ساخت رشتهتاثیرگذار از ویژگی‌های کلیدی ترید
        unique_str = f"{getattr(t, 'id', '')}_{getattr(t, 'symbol', '')}_{getattr(t, 'net_profit', '')}_{getattr(t, 'close_time', '')}"
        hasher.update(unique_str.encode('utf-8'))
    return hasher.hexdigest()


@login_required
def api_get_ai_analysis(request):
    period = request.GET.get('period', 'weekly')
    ai_response = None

    metrics = calculate_trading_metrics(period=period)

    # ۱. محاسبه هشِ وضعیت فعلی تریدها
    current_trades_hash = generate_trades_hash(metrics.get('trades',[]))
    cache_file = CACHE_DIR / f"analysis_{period}.json"

    # ۲. بررسی اینکه آیا فایل کش وجود دارد و آیا تریدها تغییر کرده‌اند یا خیر
    if cache_file.exists():
        try:
            with open(cache_file, 'r', encoding='utf-8') as f:
                cache_data = json.load(f)
                if cache_data.get('hash') == current_trades_hash:
                    # اگر تریدها عوض نشده بودند، تحلیل ذخیره‌شده را برمی‌گردانیم (بدون مصرف توکن جدید!)
                    ai_response = cache_data.get('analysis')
        except Exception as e:
            logger.error("Error reading AI cache file: %s", e)

    if not ai_response:
        # فراخوانی مستقیم و بدون خطای async
        ai_response = analyze_trades_with_gemini(metrics, period)
        if ai_response and ("`[SYSTEM_ALERT]:`" not in ai_response):
            # ۳. ذخیره تحلیل جدید همراه با هش تریدها در فایل JSON
                try:
                    cache_data = {
                        'hash': current_trades_hash,
                        'analysis': ai_response
                    }
                    with open(cache_file, 'w', encoding='utf-8') as f:
                        json.dump(cache_data, f, ensure_ascii=False, indent=4)
                except Exception as cache_err:
                    logger.error("Error saving AI cache file: %s", cache_err)

    if not ai_response:
        ai_response = 'تحلیلی برای این دوره یافت نشد یا خطا در پردازش رخ داد.'
    
    return JsonResponse({'status': 'success', 'analysis': ai_response})

@login_required
def journal_dashboard(request):
    """
    Dashboard view for trade journal analytics, filtering, and pagination.
    """
    # ۱. دریافت بازه زمانی از URL (اصلاح بازه‌های روزانه و هفتگی)
    period = request.GET.get('period', 'weekly').lower()
    # دریافت سال و ماه از پارامترهای URL یا استفاده از ماه جاری
    req_year = int(request.GET.get('year', timezone.now().year))
    req_month = int(request.GET.get('month', timezone.now().month))

    # ۱. فراخوانی کامل تابع محاسباتی یکپارچه
    metrics = calculate_trading_metrics(period=period, req_year=req_year, req_month=req_month)

    # استخراج نوت‌ها و تفکیک یادداشت‌های برنده و بازنده برای پنل هوش مصنوعی
    processed_trades = metrics.get('trades', [])
    winning_notes = [t.notes for t in processed_trades if t.result == 'WIN' and t.notes and t.notes.strip() != '']
    losing_notes = [t.notes for t in processed_trades if t.result == 'LOSS' and t.notes and t.notes.strip() != '']

    ai_analysis_data = {
        'winning_notes': winning_notes[::-1][:5],
        'losing_notes': losing_notes[::-1][:5],
    }

    # Pagination (نمایش معکوس برای اینکه جدیدترین معاملات در صفحه اول جدول قرار گیرند)
    paginator = Paginator(list(reversed(metrics['trades'])), 10)
    page_number = request.GET.get('page', 1)

    try:
        metrics['trades'] = paginator.page(page_number)
    except PageNotAnInteger:
        metrics['trades'] = paginator.page(1)
    except EmptyPage:
        metrics['trades'] = paginator.page(paginator.num_pages)

    context = {
        **metrics,
        'ai_analysis_data': ai_analysis_data,
        'unit':CURENCY_UNIT,
        'version': VERSION,}

    return render(request, 'dashboard.html', context)


@login_required
@require_POST
def update_trade_note_api(request):
    """
    API endpoint to update trade notes and psychological reviews.
    """
    if request.method != "POST":
            return JsonResponse({"status": "error", "message": "متد غیرمجاز"}, status=400)
    
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

@login_required
@require_POST
def py_backup_view(request):
    return backup_database()


@login_required
def list_backups_view(request):
   return get_backups()

@login_required
@require_POST
def restore_backup_view(request):
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "متد غیرمجاز"}, status=400)

    data = json.loads(request.body)
    filename = data.get("filename")
    if not filename:
        return JsonResponse({"status": "error", "message": "نام فایل مشخص نشده است"}, status=400)

    return restore_backup(filename)

@login_required
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
            'position_id': t.position_id,
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
            'unit':CURENCY_UNIT
        })
        
    return JsonResponse({
        "status": "success",
        "date": date_str,
        "trades": trades_data
    })