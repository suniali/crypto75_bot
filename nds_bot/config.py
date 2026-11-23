from __future__ import annotations
from dataclasses import dataclass

TF_MINUTES = {"M1": 1, "M2": 2, "M3": 3, "M5": 5, "M10": 10, "M15": 15, "M30": 30,
              "H1": 60, "H4": 240, "D1": 1440}


@dataclass
class NDSConfig:
    # --- timeframes (the course: HTF gives direction + final target, LTF hunts the 123Flag)
    htf: str = "M30"
    ltf: str = "M1"
    # --- node (pivot) detection: a node = local extreme (sign change of the derivative).
    # The course gives no size, so strength = bars on each side is a parameter.
    pivot_htf: int = 3
    pivot_ltf: int = 3
    atr_period: int = 14
    tp_percent: float = 0.86
    # Symmetry tolerance (e.g., 7.5% deviation allowed)
    symmetry_tol: float = 0.075
    min_rr: float = 2.0 
    # Risk management
    risk_per_trade: float = 0.01   # 1% of account
    # Kalman filter initial parameters
    kalman_Q_init: float = 1e-4
    kalman_R_init: float = 1e-2
    # AI model
    model_path: str = "nds_model.pt"
    # --- flag / trigger
    min_flag_atr: float = 0.5      # ASSUMPTION (not in the PDFs): min depth of pullback Z, in ATR
    babi_max_bars: int = 5         # bars allowed between break of node 3 and close back inside
    signal_max_age: int = 0        # 0 = signal only on the BABI bar itself (backtest). Live: 1
    max_flag_bars: int = 200       # engineering limit: flag must form within N bars after node 3
    sl_buffer_atr: float = 0.1     # buffer behind the flag (ATR)
    tp1_mode: str = "both"         # "both" = both open nodes closed, "first" = nearest open node
    # --- optional filters (0 / False = off)
    max_sl_atr: float = 0.0
    max_reject_atr: float = 0.0
    require_low_volume: bool = False
    lvs_ratio: float = 1.0
    # --- trade plan used by the outcome simulator (course: 1/3 at TP1, rest at final target)
    tp1_fraction: float = 1.0 / 3.0
    move_be_after_tp1: bool = False
    max_hold_bars: int = 400
    same_bar_sl_first: bool = True  # SL and a target inside one bar: True = SL first (conservative)
    cost_price: float = 0.0        # spread + commission in price units, charged once per trade

    @property
    def htf_min(self) -> int:
        return TF_MINUTES[self.htf]

    @property
    def ltf_min(self) -> int:
        return TF_MINUTES[self.ltf]
