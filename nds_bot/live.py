import time
from .data import mt5_rates
from .structure import scan_latest, make_bars
from .kalman import KalmanFilter, QRPredictor
from .features import extract_features
import torch
import numpy as np

def run_live(symbols, cfg, model_path=None, poll_seconds=10):
    model = None
    if model_path:
        model = QRPredictor()
        model.load_state_dict(torch.load(model_path))
        model.eval()
    kf = KalmanFilter()
    errors = []
    issued = {s: set() for s in symbols}
    last_bar = {}
    while True:
        for sym in symbols:
            try:
                ltf = mt5_rates(sym, cfg.ltf, 1500)
                if last_bar.get(sym) == ltf.index[-1]: continue
                last_bar[sym] = ltf.index[-1]
                htf = mt5_rates(sym, cfg.htf, 800)
                s = scan_latest(ltf, htf, cfg, issued[sym])
                if s is None: continue
                issued[sym].add(s.key)
                # Kalman update
                obs = abs(s.p[2].price - s.p[0].price)
                if model:
                    feats = extract_features(s, make_bars(ltf, cfg), errors, 0.5)
                    with torch.no_grad():
                        Q_t, R_t = model(torch.tensor(feats).unsqueeze(0)).squeeze().numpy()
                else:
                    Q_t, R_t = cfg.kalman_Q_init, cfg.kalman_R_init
                L_hat, err = kf.update(obs, Q_t, R_t)
                errors.append(err)
                symmetry_error = abs(obs - L_hat) / (L_hat + 1e-9)
                if symmetry_error > cfg.symmetry_tol:
                    print(f"[{sym}] rejected: symmetry error {symmetry_error:.2%}")
                    continue
                print(f"🚀 SIGNAL: {sym} {'BUY' if s.direction==1 else 'SELL'} @ {s.entry}, SL={s.sl}, TP={s.tp}")
                # Here you can add order execution via MT5
            except Exception as e:
                print(f"[{sym}] error: {e}")
        time.sleep(poll_seconds)