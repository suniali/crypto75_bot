import os
import django

# 1. تنظیمات محیط جنگو
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

# 2. ایمپورت‌ها
from bot_app.models import MarketCandle, TradeDecisionLog, TelegramUser
from bot_app.services.nds_strategy_service import rows_to_ohlc, NDSConfig, sanitize
from bot_app.services.execution_service import calculate_dynamic_lot_size
from bot_app.services.notification_service import enqueue_signal_for_notification
from nds_bot.structure import make_bars, raw_pivots, PivotTracker, group_by_confirm, detect_setup, htf_states

print("⏳ در حال خواندن آخرین داده‌ها از دیتابیس...")

# 3. دریافت آخرین کندل‌ها (بدون محدودیت زمانی)
rows_ltf = list(MarketCandle.objects.filter(symbol="XAUUSD_l", timeframe="M1").order_by('-open_time').values('open_price', 'high_price', 'low_price', 'close_price', 'volume', 'open_time')[:1500])
rows_htf = list(MarketCandle.objects.filter(symbol="XAUUSD_l", timeframe="M30").order_by('-open_time').values('open_price', 'high_price', 'low_price', 'close_price', 'volume', 'open_time')[:500])

if not rows_ltf or not rows_htf:
    print("❌ داده‌ای در دیتابیس یافت نشد.")
    exit()

ohlc_ltf = rows_to_ohlc(rows_ltf)
ohlc_htf = rows_to_ohlc(rows_htf)
print(f"📊 آخرین کندل M1 در دیتابیس: {ohlc_ltf.index[-1]}")
print(f"📊 آخرین کندل M30 در دیتابیس: {ohlc_htf.index[-1]}")

cfg = NDSConfig(ltf="M1", htf="M30")
B = make_bars(ohlc_ltf, cfg)
H = make_bars(ohlc_htf, cfg)

# 4. شبیه‌سازی حالت لایو برای پیدا کردن ستاپ در 50 کندل آخر
info = htf_states(H, cfg, only_last=True)
print(f"🌐 جهت تایم‌فریم بالا (HTF Bias): {info.bias}")

if info.bias == 0:
    print("❌ روند تایم‌فریم بالا (M30) در حال حاضر مشخص نیست. (bias=0)")
else:
    by_conf = group_by_confirm(raw_pivots(B.h, B.l, cfg.pivot_ltf))
    tr = PivotTracker()
    
    found = False
    # به جای فقط آخرین کندل، 50 کندل آخر را می‌گردیم
    for c in range(len(B.c) - 50, len(B.c)):
        for p in by_conf.get(c, []): tr.add(p)
        
        # تلورانس 5 کندلی
        test_cfg = NDSConfig(ltf="M1", htf="M30", signal_max_age=5)
        s = detect_setup(tr.p, B, c, info, test_cfg, set())
        
        if s:
            print(f"✅ ستاپ پیدا شد! زمان کندل: {B.t[c]}")
            print(f"جهت: {'SELL' if s.direction == -1 else 'BUY'} | ورود: {s.entry} | SL: {s.sl} | TP: {s.tp}")
            
            # ثبت در دیتابیس
            nds_key = f"XAUUSD_l_M1_{s.key}"
            if TradeDecisionLog.objects.filter(symbol="XAUUSD_l", features_snapshot__nds_key=nds_key).exists():
                print("⚠️ این سیگنال از قبل ثبت شده است.")
            else:
                user = TelegramUser.objects.filter(is_blocked=False).first()
                if not user:
                    print("❌ کاربر تلگرام یافت نشد.")
                else:
                    account_balance = user.initial_balance or 100.0
                    risk_pct = 1.0
                    volume = calculate_dynamic_lot_size("XAUUSD_l", account_balance, risk_pct, s.entry, s.sl)
                    
                    snapshot = sanitize({
                        'strategy': 'NDS_KALMAN', 'nds_key': nds_key,
                        'nds_explanation': f"ستاپ دستی بازسازی شده در {B.t[c]}",
                        'setup_type': "NDS_123FLAG_BABI_KALMAN", 'risk_percentage': risk_pct, 
                        'account_balance': account_balance, 'entry_price': s.entry, 
                        'stop_loss': s.sl, 'take_profit': s.tp,
                        'calculated_volume': volume, 'risk_amount_usd': account_balance * (risk_pct/100)
                    })
                    
                    log = TradeDecisionLog.objects.create(
                        symbol="XAUUSD_l", action="SELL" if s.direction == -1 else "BUY",
                        ai_confidence=1.0, features_snapshot=snapshot
                    )
                    enqueue_signal_for_notification(log.id)
                    print(f"🎉 سیگنال به دیتابیس اضافه شد (Log ID: {log.id}). تا 30 ثانیه دیگر به تلگرام و داشبورد ارسال می‌شود.")
                    found = True
                    break # توقف بعد از پیدا کردن اولین سیگنال
        if found:
            break
        
    if not found:
        print("❌ در 50 کندل آخر، هیچ ستاپی یافت نشد.")