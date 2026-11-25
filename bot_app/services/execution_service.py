import logging
from decimal import Decimal
from bot_app.models import TradeDecisionLog, MarketCandle, TelegramUser
from bot_app.services.analysis_service import get_user_progression_plan
from bot_app.services.mt5_service import get_broker_contract_size
from bot_app.services.market_data_service import prepare_and_update_candles
from bot_app.services.notification_service import enqueue_signal_for_notification
from nds_bot.super_scalp_engine import SuperScalpConfig
from nds_bot.dva_ar_risk import DVAARConfig, compute_dva_stop_loss
from .super_scalp_service import evaluate_super_scalp_strategy, load_scalp_ai_predictor,rows_to_ohlc_df
from .nds_strategy_service import NDS_BARS, evaluate_nds_strategy, sanitize, norm_tf,HTF_FOR

logger = logging.getLogger(__name__)

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

def calculate_dynamic_lot_size(symbol, account_balance, risk_percentage, current_price, stop_loss):
    contract_size_float, point, digits = get_broker_contract_size(symbol)
    if not contract_size_float:
        contract_size_float = 100.0 if "XAU" in symbol.upper() else 100000.0

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
    return list(qs)[::-1]


def evaluate_super_scalp_and_execute(
    symbol: str,
    time_frame: str = "M1",
    model_path: str | None = None,
    require_all_5_checks: bool = False,
    ai_advisory_only: bool = False,
    ai_min_prob: float = 0.55,
    dva_cfg: DVAARConfig | None = None,
) -> dict:
    """اجرای موتور Super-Scalp روی کندل‌های آپدیت‌شده، تعیین SL با DVA، ثبت لاگ و صف تلگرام."""
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

    dva_cfg = dva_cfg or DVAARConfig()
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

    action = str(res["action"]).upper()
    direction = 1 if action in ("BUY", "LONG") else -1
    entry = float(res["entry_price"])
    tp = float(res["take_profit"])

    ohlc_df = rows_to_ohlc_df(recent_m1_rows)
    win_slice = ohlc_df.iloc[-dva_cfg.vol_ref_window :] if not ohlc_df.empty else None

    sl_px, _ = compute_dva_stop_loss(
        direction=direction,
        entry_price=entry,
        atr_value=features.get("atr"),
        ohlc_ltf=win_slice,
        cfg=dva_cfg,
    )
    sl = float(sl_px)

    if abs(entry - sl) <= 1e-9:
        return {"status": "error", "reason": "فاصله حد ضرر صفر است."}

    volume = float(res.get("calculated_volume") or 0.01)
    risk_usd = Decimal(str(res.get("risk_amount_usd") or 0.0)).quantize(Decimal("0.01"))

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