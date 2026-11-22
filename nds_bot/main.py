import argparse
from .config import NDSConfig
from .data import load_csv, mt5_rates
from .backtest import run_backtest
from .live import run_live
from .kalman import QRPredictor
import torch

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["backtest", "live"], required=True)
    parser.add_argument("--symbol", default="EURUSD")
    parser.add_argument("--csv", help="Path to CSV for backtest")
    args = parser.parse_args()
    
    cfg = NDSConfig()
    if args.mode == "backtest":
        if args.csv:
            ltf = load_csv(args.csv)
        else:
            ltf = mt5_rates(args.symbol, cfg.ltf, 50000)
        htf = mt5_rates(args.symbol, cfg.htf, 5000) if not args.csv else None
        model = QRPredictor()
        # model.load_state_dict(torch.load(cfg.model_path))  # optional
        df = run_backtest(ltf, cfg, htf, model)
        print(df.describe())
        print("Total R:", df["r"].sum())
    else:
        run_live([args.symbol], cfg)

if __name__ == "__main__":
    main()