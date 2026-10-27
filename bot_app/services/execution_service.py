import logging

from decimal import Decimal
import pandas as pd


from bot_app.services.ml_features import extract_features_for_ml,SL_ATR_MULT, TP_ATR_MULT
from bot_app.services.ml_engine import TradingAIEngine 
from bot_app.services.mt5_service import get_broker_contract_size 

from bot_app.models import TradeDecisionLog

logger = logging.getLogger(__name__)

def calculate_dynamic_lot_size(
    symbol, account_balance, risk_percentage, current_price, stop_loss
):
    # ۱. دریافت پویا‌ی Contract Size از بروکر
    contract_size_float = get_broker_contract_size(symbol)
    if not contract_size_float:
        # مقدار پیش‌فرض امن در صورت خطا (مثلا استاندارد فارکس)
        contract_size_float = 100000.0

    contract_size = Decimal(str(contract_size_float))

    # ۲. محاسبه ریسک دلاری
    risk_amount_usd = Decimal(str(account_balance)) * Decimal(
        str(risk_percentage / 100.0)
    )

    # ۳. فاصله حد ضرر
    price_risk_per_unit = abs(
        Decimal(str(current_price)) - Decimal(str(stop_loss))
    )

    if price_risk_per_unit == 0:
        return 0.0

    # ۴. محاسبه حجم دقیق با احتساب سایز قرارداد واقعی بروکر
    calculated_volume = risk_amount_usd / (price_risk_per_unit * contract_size)

    # رند کردن به ۲ رقم اعشار و اعمال حداقل حجم مجاز (0.01)
    final_volume = max(0.01, round(float(calculated_volume), 2))

    return final_volume

def evaluate_market_and_execute(
    symbol,time_frame, candles_qs, account_balance, risk_percentage=1.0
):
    """موتور اجرایی هوشمند:

    ۱. دریافت کندل‌ها و تبدیل به دیتافریم
    ۲. استخراج ویژگی‌های پرایس اکشن و اندیکاتورها
    ۳. گرفتن تاییدیه هوش مصنوعی (XGBoost)
    ۴. محاسبه حد ضرر پویا با ATR و سود هدف (با R:R استاندارد 1:2)
    ۵. اعمال قانون دقیق ریسک ۲ درصدی برای تعیین حجم (Volume)
    ۶. ثبت تصمیم در لاگ دیتابیس جنگو
    """

    # ۱. تبدیل کوئری‌ست کندل‌ها به دیتافریم پانداس
    df = pd.DataFrame(
        list(
            candles_qs.values(
                'open_price',
                'high_price',
                'low_price',
                'close_price',
                'volume',
                'open_time',
            )
        )
    )

    if len(df) < 100:
        return {
            'status': 'skipped',
            'reason': 'داده‌های تاریخی برای تحلیل کافی نیست.',
        }

    # ۲. استخراج ویژگی‌ها (فاز ۲)
    processed_df = extract_features_for_ml(df)
    if processed_df.empty:
        return {
            'status': 'skipped',
            'reason': 'خطا در پردازش ویژگی‌های پرایس اکشن.',
        }

    latest_row = processed_df.iloc[-1].to_dict()

    # 2. بررسی مدل هوش مصنوعی (اگر مدل هنوز ساخته نشده باشد)
    ai_engine = TradingAIEngine(symbol=symbol,timeframe=time_frame)
    
    if ai_engine.model is None:
        return {'status': 'skipped', 'reason': 'مدل معتبری برای این نماد/تایم‌فریم نیست (train_model).'}
    
    action, confidence = ai_engine.predict_signal(latest_row)


    # بررسی اینکه آیا سیگنال صادر شده است یا خیر (اگر HOLD باشد یعنی رد شده است)
    is_approved = action in ['BUY', 'SELL']

    formatted_features={
            k: float(v) if isinstance(v, (int, float, Decimal)) else str(v)
            for k, v in latest_row.items()
        }

    # ۴. ثبت نتیجه در دیتابیس (مدل TradeDecisionLog)
    decision_log = TradeDecisionLog.objects.create(
        symbol=symbol,
        action=action,
        ai_confidence=confidence,
        features_snapshot=formatted_features,
    )

    # اگر هوش مصنوعی تایید نکرد، فرآیند متوقف می‌شود
    if not is_approved:
        return {
            'status': 'rejected',
            'confidence': confidence,
            'reason': 'ضریب اطمینان هوش مصنوعی پایین‌تر از آستانه مجاز (۶۵٪) است.',
        }

    # ۵. محاسبه حد ضرر پویا (Stop Loss) بر اساس ATR و سود هدف (Take Profit) با R:R 1:2
    current_price = float(latest_row['close_price'])
    atr_value = float(latest_row['atr'])

    # فاصله حد ضرر معادل ۱.۵ برابر ATR فعلی بازار است
    sl_distance = atr_value * SL_ATR_MULT

    if action == 'BUY':
        stop_loss = current_price - sl_distance
        take_profit = current_price + sl_distance * (TP_ATR_MULT / SL_ATR_MULT)
    else:  # SELL
        stop_loss = current_price + sl_distance
        take_profit = current_price - sl_distance * (TP_ATR_MULT / SL_ATR_MULT)

    # ۶. پیاده‌سازی قانون سخت‌گیرانه مدیریت ریسک (حداکثر ۲٪ ریسک سرمایه)
    # فرمول: (مبلغ ریسک دلاری) تقسیم بر (فاصله قیمت ورود تا حد ضرر)
    # ۱. محاسبه مبلغ ریسک بر اساس درصد حساب (مثلا 2 درصد از بالانس)
    risk_amount_usd = Decimal(str(account_balance)) * Decimal(
        str(risk_percentage / 100.0)
    )

    # ۲. فاصله قیمت ورود تا حد ضرر
    price_risk_per_unit = abs(
        Decimal(str(current_price)) - Decimal(str(stop_loss))
    )
    
    if price_risk_per_unit == 0:
        return {
            'status': 'error',
            'reason': 'محاسبه فاصله حد ضرر با خطا مواجه شد.',
        }

    calculated_volume=calculate_dynamic_lot_size(
        symbol=symbol,
        account_balance=account_balance,
        risk_percentage=risk_percentage,
        current_price=current_price,
        stop_loss=stop_loss
        )

    formatted_features = {
        **formatted_features,
        'entry_price':current_price,
        'stop_loss': round(stop_loss, 5),
        'take_profit': round(take_profit, 5),
        'calculated_volume': round(float(calculated_volume), 2),
        'risk_amount_usd': float(round(risk_amount_usd, 2)),
        'risk_percentage':risk_percentage,
    }

    decision_log.features_snapshot=formatted_features
    decision_log.save(
        update_fields=['features_snapshot']
    )

    return {
        'status': 'approved_signal',
        'log_id': decision_log.id,
        'symbol': symbol,
        'action': action,
        'ai_confidence': round(confidence * 100, 2),
        'entry_price': current_price,
        'stop_loss': round(stop_loss, 5),
        'take_profit': round(take_profit, 5),
        'calculated_volume': round(float(calculated_volume), 2),
        'risk_amount_usd': float(round(risk_amount_usd, 2)),
        'features': formatted_features
    }