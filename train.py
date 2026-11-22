import torch
import torch.optim as optim
import numpy as np
import pandas as pd
from nds_bot.data import mt5_rates
from nds_bot.structure import make_bars, raw_pivots, PivotTracker, group_by_confirm, htf_states, detect_setup
from nds_bot.kalman import QRPredictor, KalmanFilter
from nds_bot.features import extract_features
from nds_bot.config import NDSConfig

def train_ai_model(symbol="XAUUSD_l", bars=80000):
    print("1. دریافت داده از متاتریدر...")
    ltf = mt5_rates(symbol, "M1", bars) # داده‌های ۱ دقیقه
    htf = mt5_rates(symbol, "H4", 500)  # داده‌های ۴ ساعته
    
    cfg = NDSConfig(ltf="M1", htf="H4")
    B = make_bars(ltf, cfg)
    H = make_bars(htf, cfg)
    states = htf_states(H, cfg)
    
    # هم‌راستایی زمانی
    htf_close = (htf.index + pd.Timedelta(minutes=cfg.htf_min)).to_numpy()
    ltf_close = (ltf.index + pd.Timedelta(minutes=cfg.ltf_min)).to_numpy()
    hmap = np.searchsorted(htf_close, ltf_close, side="right") - 1
    
    by_conf = group_by_confirm(raw_pivots(B.h, B.l, cfg.pivot_ltf))
    tr = PivotTracker()
    issued = set()
    
    X_data, Y_data = [], []
    kf = KalmanFilter()
    errors = []
    
    print("2. استخراج ستاپ‌ها و ساخت دیتاست...")
    for c in range(100, len(B.c)):
        for p in by_conf.get(c, []): tr.add(p)
        hb = hmap[c]
        if hb < 30: continue
        info = states[hb]
        if info.bias == 0: continue
        s = detect_setup(tr.p, B, c, info, cfg, issued)
        if s is None: continue
        issued.add(s.key)
        
        # مشاهده واقعی (طول فلگ)
        obs = abs(s.p[2].price - s.p[0].price)
        
        # ویژگی‌های ورودی برای AI
        feats = extract_features(s, B, errors, 0.5) # 0.5 hurst placeholder
        X_data.append(feats)
        
        # هدف (Target): نویز واقعی بازار در این لحظه. 
        # برای سادگی، نوسان (ATR) را به عنوان نویز مشاهده (R) و تغییرات ATR را به عنوان نویز فرآیند (Q) در نظر می‌گیریم.
        # در دنیای واقعی، این مقادیر باید بهینه‌سازی شوند.
        Y_data.append([np.log(s.atr), np.log(s.atr * 0.1)]) 
        
        # شبیه‌سازی آپدیت کالمن برای دریافت خطا برای گام بعدی
        _, err = kf.update(obs, np.exp(Y_data[-1][0]), np.exp(Y_data[-1][1]))
        errors.append(err)

    print(f"3. تعداد ستاپ‌های یافت‌شده برای آموزش: {len(X_data)}")
    if len(X_data) < 100:
        print("داده کافی برای آموزش وجود ندارد!")
        return

    X_tensor = torch.tensor(np.array(X_data), dtype=torch.float32)
    Y_tensor = torch.tensor(np.array(Y_data), dtype=torch.float32)
    
    # 4. تعریف مدل و بهینه‌ساز
    model = QRPredictor(input_dim=X_tensor.shape[1])
    criterion = torch.nn.MSELoss()
    optimizer = optim.Adam(model.parameters(), lr=0.001)
    
    # 5. حلقه آموزش
    print("4. شروع آموزش شبکه عصبی...")
    epochs = 100
    for epoch in range(epochs):
        optimizer.zero_grad()
        outputs = model(X_tensor)
        # خروجی مدل لگاریتم واریانس‌هاست (برای مثبت ماندن) -> پس هدف هم log گرفته شده
        log_outputs = torch.log(outputs + 1e-8) 
        loss = criterion(log_outputs, Y_tensor)
        loss.backward()
        optimizer.step()
        
        if (epoch+1) % 10 == 0:
            print(f"Epoch {epoch+1}/{epochs}, Loss: {loss.item():.6f}")

    # 6. ذخیره مدل
    torch.save(model.state_dict(), "nds_model.pt")
    print("✅ مدل با موفقیت آموزش دید و در فایل nds_model.pt ذخیره شد.")

if __name__ == "__main__":
    train_ai_model()