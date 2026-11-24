from __future__ import annotations
from dataclasses import dataclass

TF_MINUTES = {
    "M1": 1, "M2": 2, "M3": 3, "M5": 5, "M10": 10, "M15": 15, "M30": 30,
    "H1": 60, "H4": 240, "D1": 1440
}


@dataclass
class NDSConfig:
    # --- Timeframes (M30 = Strategic Direction & Final Target, M1 = Tactical 123Flag Entry)
    htf: str = "M30"
    ltf: str = "M1"

    # --- Node (Pivot) detection strength (bars on each side)
    pivot_htf: int = 3
    pivot_ltf: int = 3
    atr_period: int = 14

    # --- NDS Cycle & Retracement Constants
    cycle_retrace_pct: float = 0.864   # 86.4% reference cycle retracement level
    cycle_tolerance: float = 0.08      # +/- 8% zone around 86.4% for secondary re-entry flags
    symmetry_tol: float = 0.25         # Max allowed relative deviation between measured leg & Kalman L*

    # --- Flag / Trigger (BABI = Break And Back Inside)
    min_flag_atr: float = 0.5          # Min pullback Z depth in ATR
    babi_max_bars: int = 5             # Max bars allowed between spike F beyond N3 and close back inside N3
    signal_max_age: int = 1            # 0 for strict backtest bar, 1-2 for live polling tolerance
    max_flag_bars: int = 200           # Max bars after N3 for flag completion
    sl_buffer_atr: float = 0.15        # Buffer behind spike F (NOT N3!) so wick doesn't stop out immediately

    # --- Sequence Exit Rules (Priority: Closing Open Nodes 1 & 2 -> Node 3)
    tp1_mode: str = "both"             # "both" = furthest open node (1 & 2 closed), "first" = nearest open node
    tp1_fraction: float = 1.0 / 3.0    # Course rule: close 1/3 at Open Nodes 1 & 2 closure
    move_be_after_tp1: bool = True     # Protect remaining position after sequence closes Open Nodes 1 & 2
    max_hold_bars: int = 600           # Max LTF bars to hold before timeout

    # --- Fractal (DFA Hurst) & LML Risk Filters
    use_fractal_filter: bool = True
    hurst_window: int = 120            # Rolling window for DFA Hurst calculation
    hurst_random_low: float = 0.45     # Avoid pure random walk zone [0.45, 0.55] on HTF
    hurst_random_high: float = 0.55
    min_tail_alpha: float = 1.15       # LML tail index threshold (below = extreme crisis regime)

    # --- Kalman Filter & AI Meta-Labeling / QR Predictor
    kalman_Q_init: float = 1e-4
    kalman_R_init: float = 1e-2
    model_dir: str = "models"
    ai_min_samples: int = 120
    force_ai: bool = True              # روشن نگه داشتن اجباری فیلتر AI حتی وقتی ai_useful=False باشد
    ai_advisory_only: bool = True       # True = فقط نظر می‌دهد و سیگنال را رد نمی‌کند
    ai_min_prob: float = 0.45          # کف آستانه احتمال برد هنگام فعال بودن اجباری فیلتر AI
    
    # --- Backtest Execution Realism
    risk_per_trade: float = 0.01       # 1% base risk per trade
    same_bar_sl_first: bool = True     # Conservative intra-bar conflict resolution
    cost_price: float = 0.0            # Spread + commission in price units

    @property
    def htf_min(self) -> int:
        return TF_MINUTES[self.htf]

    @property
    def ltf_min(self) -> int:
        return TF_MINUTES[self.ltf]