
from decimal import Decimal
import pandas as pd

from bot_app.models import TradeDecisionLog
from bot_app.services.ml_engine import TradingAIEngine
from bot_app.services.ml_features import extract_features_for_ml
from bot_app.services.order_block_rsi_strategy import evaluate_ob_rsi_strategy
from bot_app.services.mt5_service import get_broker_contract_size


def calculate_dynamic_lot_size(
    symbol, account_balance, risk_percentage, current_price, stop_loss
):
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


def evaluate_market_and_execute(
    symbol, time_frame, candles_qs, account_balance, risk_percentage=1.0, min_confidence=0.60
):
    # ۱. تبدیل کندل‌ها به DataFrame با ترتیب صعودی زمانی (قدیمی -> جدید)
    rows = list(
        candles_qs.values(
            'open_price',
            'high_price',
            'low_price',
            'close_price',
            'volume',
            'open_time',
        )
    )
    rows.sort(key=lambda r: r['open_time'])
    df = pd.DataFrame(rows)

    if len(df) < 100:
        return {
            'status': 'skipped',
            'reason': 'داده‌های تاریخی برای تحلیل کافی نیست.',
        }

    # ۲. ارزیابی استراتژی اردربلاک معتبر + واگرایی RSI (صعودی و نزولی)
    strategy_res = evaluate_ob_rsi_strategy(df, sl_atr_mult=1.5, rr_ratio=2.0)
    action = strategy_res.get('signal', 'HOLD')

    # ۳. استخراج ویژگی‌ها و امتیازدهی مدل ML (در صورت وجود مدل آموزش‌دیده)
    processed_df = extract_features_for_ml(df)
    latest_row = processed_df.iloc[-1].to_dict() if not processed_df.empty else {}

    ai_engine = TradingAIEngine(symbol=symbol, timeframe=time_frame)
    ml_prob = ai_engine.score_setup(latest_row) if ai_engine.model is not None else 0.50

    base_score = float(strategy_res.get('setup_score', 0.0))
    if action in ['BUY', 'SELL']:
        final_confidence = round(((base_score * 0.65) + (ml_prob * 0.35)), 4) if ai_engine.model is not None else base_score
    else:
        final_confidence = 0.0

    is_approved = (action in ['BUY', 'SELL']) and (final_confidence >= min_confidence)

    snapshot = {
        **{k: (float(v) if isinstance(v, (int, float, Decimal)) else str(v)) for k, v in latest_row.items()},
        'setup_type': strategy_res.get('setup_type'),
        'setup_score': base_score,
        'ml_probability': round(float(ml_prob), 4),
        'divergence': strategy_res.get('divergence'),
        'ob_low': strategy_res.get('ob_low'),
        'ob_high': strategy_res.get('ob_high'),
    }

    decision_log = TradeDecisionLog.objects.create(
        symbol=symbol,
        action=action if is_approved else 'HOLD',
        ai_confidence=final_confidence,
        features_snapshot=snapshot,
    )

    if not is_approved:
        reason = strategy_res.get('reason') if action == 'HOLD' else f'امتیاز نهایی ({final_confidence:.2f}) کمتر از حد مجاز ({min_confidence:.2f}) است.'
        return {
            'status': 'rejected',
            'confidence': final_confidence,
            'reason': reason,
        }

    current_price = float(strategy_res['entry_price'])
    stop_loss = float(strategy_res['stop_loss'])
    take_profit = float(strategy_res['take_profit'])

    price_risk_per_unit = abs(Decimal(str(current_price)) - Decimal(str(stop_loss)))
    if price_risk_per_unit == 0:
        return {
            'status': 'error',
            'reason': 'محاسبه فاصله حد ضرر با خطا مواجه شد.',
        }

    calculated_volume = calculate_dynamic_lot_size(
        symbol=symbol,
        account_balance=account_balance,
        risk_percentage=risk_percentage,
        current_price=current_price,
        stop_loss=stop_loss,
    )
    risk_amount_usd = float(round(Decimal(str(account_balance)) * Decimal(str(risk_percentage / 100.0)), 2))

    snapshot.update({
        'entry_price': current_price,
        'stop_loss': stop_loss,
        'take_profit': take_profit,
        'calculated_volume': calculated_volume,
        'risk_amount_usd': risk_amount_usd,
        'risk_percentage': risk_percentage,
    })
    decision_log.features_snapshot = snapshot
    decision_log.save(update_fields=['features_snapshot'])

    return {
        'status': 'approved_signal',
        'log_id': decision_log.id,
        'symbol': symbol,
        'action': action,
        'setup_type': strategy_res.get('setup_type'),
        'ai_confidence': round(final_confidence * 100, 2),
        'entry_price': current_price,
        'stop_loss': stop_loss,
        'take_profit': take_profit,
        'calculated_volume': calculated_volume,
        'risk_amount_usd': risk_amount_usd,
        'features': snapshot,
    }