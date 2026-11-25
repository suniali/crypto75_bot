import logging
from decimal import Decimal
from bot_app.models import TradeDecisionLog, MarketCandle,TelegramUser
from bot_app.services.analysis_service import get_user_progression_plan
from bot_app.services.mt5_service import get_broker_contract_size
from bot_app.services.market_data_service import prepare_and_update_candles
from bot_app.services.notification_service import enqueue_signal_for_notification
from nds_bot.super_scalp_engine import SuperScalpConfig
from .super_scalp_service import evaluate_super_scalp_strategy,load_scalp_ai_predictor
from .nds_strategy_service import NDS_BARS, evaluate_nds_strategy, sanitize, norm_tf, HTF_FOR

logger=logger = logging.getLogger(__name__)

def calculate_dynamic_lot_size(symbol, account_balance, risk_percentage, current_price, stop_loss):
    # (کدهای شما بدون تغییر)
    contract_size_float, point, digits = get_broker_contract_size(symbol)
    if not contract_size_float:
        contract_size_float = 100.0 if 'XAU' in symbol.upper() else 100000.0

    contract_size = Decimal(str(contract_size_float))
    risk_amount_usd = Decimal(str(account_balance)) * Decimal(str(risk_percentage / 100.0))
    price_risk_per_unit = abs(Decimal(str(current_price)) - Decimal(str(stop_loss)))

    if price_risk_per_unit == 0:
        return 0.0

    calculated_volume = risk_amount_usd / (price_risk_per_unit * contract_size)
    return max(0.01, round(float(calculated_volume), 2))

def fetch_recent_candles_rows(symbol: str, timeframe: str) -> list[dict]:
    """دریافت کندل‌های اخیر از دیتابیس به ترتیب زمانی صعودی (قدیمی به جدید) برای تحلیل."""
    qs = (
        MarketCandle.objects.filter(symbol=symbol, timeframe=timeframe)
        .order_by("-open_time")
        .values("open_price", "high_price", "low_price", "close_price", "volume", "open_time")[:NDS_BARS]
    )
    
    # مهم: معکوس می‌کنیم تا کندل آخر (جدیدترین کندل) در انتهای لیست باشد
    return list(qs)[::-1]

def evaluate_market_and_execute(symbol, time_frame:str = 'M1'):
    # 1. استانداردسازی تایم‌فریم و پیدا کردن HTF از کانفیگ NDS
    ltf = norm_tf(time_frame)
    htf = HTF_FOR.get(ltf)
    
    if not htf:
        return {'status': 'error', 'reason': f'تایم‌فریم {ltf} در استراتژی NDS پشتیبانی نمی‌شود.'}

    # 2. دریافت و آپدیت کندل‌های LTF و HTF از متاتریدر
    logger.info(f'🔄 Updating candles for {symbol} | LTF: {ltf} | HTF: {htf}')
    success = prepare_and_update_candles(
        symbol=symbol, 
        time_frame=ltf, # تابع خودش HTF را از HTF_FOR پیدا می‌کند
        full_limit=1500, 
        short_limit=50
    )
    if not success:
        return {'status': 'error', 'reason': 'خطا در دریافت داده‌ها از متاتریدر. لطفاً وضعیت اتصال را بررسی کنید.'}

    # 3. دریافت کندل‌ها از دیتابیس (حالا که مطمئنیم آپدیت هستند)
    rows = fetch_recent_candles_rows(symbol=symbol,timeframe=ltf)
    
    # 4. ارزیابی استراتژی
    res = evaluate_nds_strategy(rows, symbol, ltf)
    action = res['signal']
    
    if action == 'HOLD':
        return {'status': 'skipped', 'reason': res['reason']}


    # بررسی اینکه آیا این ستاپ قبلا ثبت شده است
    if TradeDecisionLog.objects.filter(symbol=symbol, features_snapshot__nds_key=res['nds_key']).exists():
        return {'status': 'skipped', 'reason': 'این ستاپ قبلاً ارزیابی شده است.'}

    user = TelegramUser.objects.filter(is_blocked=False).first()
    user_plan = get_user_progression_plan(user)
    account_balance = user.initial_balance
    current_risk = user_plan.get('current_risk')
    risk_percentage = (current_risk * 100) / account_balance
    # در سیستم جدید، AI فقط فیلتر تقارن کالمن را انجام می‌دهد. اگر به اینجا رسید یعنی تایید شده است.
    snapshot = sanitize({
        'strategy': 'NDS_KALMAN', 
        'nds_key': res['nds_key'], 
        'nds_explanation': res['nds_explanation'],
        'setup_type': res['setup_type'], 
        'risk_percentage': risk_percentage, 
        **res['nds_features'],
    })
    
    log = TradeDecisionLog.objects.create(
        symbol=symbol, 
        action=action,
        ai_confidence=1.0, # چون فیلتر تقارن رد شده بود، 1.0 است
        features_snapshot=snapshot
    )

    entry, sl, tp = res['entry_price'], res['stop_loss'], res['take_profit']
    if abs(entry - sl) == 0:
        return {'status': 'error', 'reason': 'فاصله حد ضرر صفر است.'}
        
    volume = calculate_dynamic_lot_size(symbol, account_balance, risk_percentage, entry, sl)
    risk_usd = Decimal(round(Decimal(str(account_balance)) * Decimal(str(risk_percentage / 100.0)), 2))
    
    snapshot.update({'entry_price': entry, 'stop_loss': sl, 'take_profit': tp,
                     'calculated_volume': volume, 'risk_amount_usd': risk_usd})
    log.features_snapshot = snapshot
    log.save(update_fields=['features_snapshot'])

    enqueue_signal_for_notification(log.id)

    return {'status': 'approved_signal', 'log_id': log.id, 'symbol': symbol, 'action': action,
            'setup_type': res['setup_type'], 'ai_confidence': 100.0,
            'entry_price': entry, 'stop_loss': sl, 'take_profit': tp,
            'take_profit_1': res['take_profit_1'], 'take_profit_2': res['take_profit_2'],
            'calculated_volume': volume, 'risk_amount_usd': risk_usd, 'features': snapshot}


def _resolve_super_scalp_model_path(
    model_path: str | None,
    require_all_5_checks: bool,
) -> str:
    """انتخاب مسیر مدل AI بر اساس حالت هر ۵ شرط یا حالت استاندارد."""
    if model_path:
        return model_path
    if require_all_5_checks:
        return "models/super_scalp_ai_all5.joblib"
    return "models/super_scalp_ai.joblib"


def evaluate_super_scalp_and_execute(
    symbol: str,
    time_frame: str = "M1",
    model_path: str | None = None,
    require_all_5_checks: bool = False,
    ai_advisory_only: bool = True,
    ai_min_prob: float = 0.55,
) -> dict:
    """اجرای موتور Super-Scalp روی کندل‌های آپدیت‌شده، ثبت لاگ و صف کردن برای ارسال تلگرام."""
    ltf = norm_tf(time_frame)
    htf = HTF_FOR.get(ltf)
    if not htf:
        return {"status": "error", "reason": f"تایم‌فریم {ltf} در استراتژی پشتیبانی نمی‌شود."}

    resolved_model_path = _resolve_super_scalp_model_path(
        model_path=model_path,
        require_all_5_checks=require_all_5_checks,
    )

    logger.info(f"🔄 [SuperScalp] Updating candles for {symbol} | LTF: {ltf} | HTF: {htf}")
    success = prepare_and_update_candles(
        symbol=symbol,
        time_frame=ltf,
        full_limit=1500,
        short_limit=50,
    )
    if not success:
        return {"status": "error", "reason": "خطا در دریافت داده‌ها از متاتریدر."}

    recent_m1_rows = fetch_recent_candles_rows(symbol=symbol, timeframe=ltf)
    if not recent_m1_rows:
        return {"status": "skipped", "reason": "کندلی در دیتابیس یافت نشد."}

    cfg = SuperScalpConfig(
        ltf=ltf,
        htf=htf,
        require_all_5_checks=require_all_5_checks,
        ai_advisory_only=ai_advisory_only,
        ai_min_prob=ai_min_prob,
    )
    ai_predict_fn = load_scalp_ai_predictor(resolved_model_path)

    res = evaluate_super_scalp_strategy(
        rows=recent_m1_rows,
        symbol=symbol,
        time_frame=ltf,
        cfg=cfg,
        ai_predict_fn=ai_predict_fn,
    )
    if not res.get("has_signal"):
        return {"status": "skipped", "reason": res.get("reason", "ستاپ Super-Scalp تشکیل نشد.")}

    features = res.get("features") or {}
    setup_key = features.get("setup_key")
    if setup_key and TradeDecisionLog.objects.filter(
        symbol=symbol,
        features_snapshot__setup_key=setup_key,
    ).exists():
        return {"status": "skipped", "reason": "این ستاپ Super-Scalp قبلاً ثبت شده است."}

    entry = float(res["entry_price"])
    sl = float(res["stop_loss"])
    tp = float(res["take_profit"])
    if abs(entry - sl) <= 1e-9:
        return {"status": "error", "reason": "فاصله حد ضرر صفر است."}

    volume = float(res.get("calculated_volume") or 0.01)
    risk_usd = Decimal(str(res.get("risk_amount_usd") or 0.0)).quantize(Decimal("0.01"))
    action = str(res["action"]).upper()

    snapshot = sanitize({
        **features,
        "strategy_type": "SUPER_SCALP",
        "strategy": "SUPER_SCALP_HA",
        "model_path": resolved_model_path,
        "require_all_5_checks": require_all_5_checks,
        "ai_advisory_only": ai_advisory_only,
        "timeframe": ltf,
        "htf": htf,
        "entry_price": entry,
        "stop_loss": sl,
        "take_profit": tp,
        "calculated_volume": volume,
        "risk_amount_usd": float(risk_usd),
    })

    log = TradeDecisionLog.objects.create(
        symbol=symbol,
        action=action,
        ai_confidence=float(res.get("ai_confidence") or 0.0),
        notification_status="pending",
        features_snapshot=snapshot,
    )

    enqueue_signal_for_notification(log.id)

    return {
        "status": "approved_signal",
        "log_id": log.id,
        "symbol": symbol,
        "action": action,
        "entry_price": entry,
        "stop_loss": sl,
        "take_profit": tp,
        "calculated_volume": volume,
        "risk_amount_usd": risk_usd,
        "features": snapshot,
    }