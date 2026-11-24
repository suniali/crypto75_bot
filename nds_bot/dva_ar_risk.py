from __future__ import annotations

from dataclasses import dataclass
import math
import numpy as np
import pandas as pd


@dataclass
class DVAARConfig:
    """پیکربندی مدل DVA-AR و مدیریت ریسک پویای NDS."""
    profile: str = "conservative"  # "conservative" | "moderate" | "aggressive"
    base_risk_pct: float = 0.005  # 0.50% ریسک پایه
    recovery_horizon_trades: int = 8  # افق بازیابی زیان (تعداد معامله)
    max_recovery_add_pct: float = 0.0025  # سقف ریسک اضافی برای بازیابی (0.25%)
    circuit_breaker_dd_pct: float = 0.06  # آستانه قطع‌کننده مدار (6.0% افت سرمایه)
    vol_ref_window: int = 20
    vol_baseline: float = 0.0008
    min_vol_dampener: float = 0.35
    max_vol_dampener: float = 1.25
    min_lot: float = 0.01
    lot_step: float = 0.01
    max_lot: float = 50.0
    advisory_reject_risk_scale: float = 0.5  # ضریب کاهش ریسک وقتی AI نظر مردود می‌دهد

    @classmethod
    def from_profile(cls, profile: str | None) -> "DVAARConfig":
        p = (profile or "conservative").lower().strip()
        if p in ("moderate", "balanced", "متوسط", "متعادل"):
            return cls(
                profile="moderate",
                base_risk_pct=0.01,
                recovery_horizon_trades=4,
                max_recovery_add_pct=0.0075,
                circuit_breaker_dd_pct=0.12,
            )
        if p in ("aggressive", "پرریسک", "تهاجمی"):
            return cls(
                profile="aggressive",
                base_risk_pct=0.02,
                recovery_horizon_trades=2,
                max_recovery_add_pct=0.015,
                circuit_breaker_dd_pct=0.20,
            )
        return cls(
            profile="conservative",
            base_risk_pct=0.005,
            recovery_horizon_trades=8,
            max_recovery_add_pct=0.0025,
            circuit_breaker_dd_pct=0.06,
        )


def compute_yang_zhang_volatility(ohlc: pd.DataFrame | None, window: int = 20) -> float:
    """محاسبه نوسان یانگ-ژانگ (Yang-Zhang) با لحاظ گپ باز شدن و محدوده درون‌کندلی."""
    if ohlc is None or len(ohlc) < max(5, window):
        return 0.0

    sub = ohlc.iloc[-window:].copy()
    o = sub["open"].astype(float).to_numpy()
    h = sub["high"].astype(float).to_numpy()
    l = sub["low"].astype(float).to_numpy()
    c = sub["close"].astype(float).to_numpy()

    if np.any(o <= 0) or np.any(h <= 0) or np.any(l <= 0) or np.any(c <= 0):
        return 0.0

    prev_c = np.roll(c, 1)
    prev_c[0] = o[0]

    log_ho = np.log(np.maximum(h / o, 1e-9))
    log_lo = np.log(np.maximum(l / o, 1e-9))
    log_co = np.log(np.maximum(c / o, 1e-9))
    log_oc = np.log(np.maximum(o / np.maximum(prev_c, 1e-9), 1e-9))
    log_cc = np.log(np.maximum(c / np.maximum(prev_c, 1e-9), 1e-9))

    n = len(sub)
    k = 0.34 / (1.34 + (n + 1.0) / max(n - 1.0, 1.0))

    rs_var = float(np.mean(log_ho * (log_ho - log_co) + log_lo * (log_lo - log_co)))
    open_var = float(np.var(log_oc, ddof=1)) if n > 1 else 0.0
    close_var = float(np.var(log_cc, ddof=1)) if n > 1 else 0.0

    yz_var = max(0.0, open_var + k * close_var + (1.0 - k) * max(0.0, rs_var))
    return math.sqrt(yz_var)


def compute_dva_ar_position(
    *,
    equity: float,
    peak_equity: float,
    entry_price: float,
    stop_price: float,
    reversal_price: float | None = None,
    target_price: float | None = None,
    ohlc_ltf: pd.DataFrame | None = None,
    point_value_per_lot: float = 100.0,
    cfg: DVAARConfig | None = None,
    ai_approved: bool | None = None,
) -> dict:
    """محاسبه حجم پویا، نوسان یانگ-ژانگ، بازیابی مستهلک‌شونده زیان و قطع‌کننده مدار."""
    cfg = cfg or DVAARConfig()
    equity = max(float(equity), 0.0)
    peak_equity = max(float(peak_equity), equity, 1e-9)

    dd_pct = max(0.0, (peak_equity - equity) / peak_equity)
    cb_limit = max(float(cfg.circuit_breaker_dd_pct), 1e-6)

    # ۱. قطع‌کننده مدار (Circuit Breaker)
    if dd_pct >= cb_limit:
        return {
            "allowed": False,
            "reason": f"قطع‌کننده مدار فعال شد: افت سرمایه ({dd_pct:.2%}) به سقف مجاز ({cb_limit:.2%}) رسید.",
            "volume": 0.0,
            "risk_amount_usd": 0.0,
            "risk_pct_effective": 0.0,
            "drawdown_pct": round(dd_pct, 6),
            "yz_vol": 0.0,
            "vol_dampener": 0.0,
            "recovery_dampener": 0.0,
            "nds_proximity_factor": 1.0,
        }

    # ۲. فیلتر نوسان یانگ-ژانگ
    yz_vol = compute_yang_zhang_volatility(ohlc_ltf, window=cfg.vol_ref_window)
    baseline = max(float(cfg.vol_baseline), 1e-9)
    if yz_vol > 0:
        vol_ratio = yz_vol / baseline
        vol_dampener = max(
            cfg.min_vol_dampener,
            min(cfg.max_vol_dampener, 1.0 / max(vol_ratio, 0.5)),
        )
    else:
        vol_dampener = 1.0

    # ۳. بازیابی مستهلک‌شونده زیان (ADR) با ترمز سهموی نزدیک آستانه خطر
    loss_amount = max(0.0, peak_equity - equity)
    dd_ratio = min(1.0, dd_pct / cb_limit)
    parabolic_brake = max(0.0, 1.0 - (dd_ratio ** 2))
    recovery_dampener = parabolic_brake * (1.0 - 0.84 * (dd_ratio ** 1.5))

    horizon = max(1, int(cfg.recovery_horizon_trades))
    raw_recovery_usd = (loss_amount / horizon) * recovery_dampener
    max_recovery_usd = equity * float(cfg.max_recovery_add_pct)
    recovery_add_usd = max(0.0, min(raw_recovery_usd, max_recovery_usd))

    # ۴. تعدیل نزدیکی به هدف در NDS (نقطه‌میانی / 86.4%)
    nds_factor = 1.0
    if reversal_price is not None and target_price is not None:
        denom = float(target_price) - float(reversal_price)
        if abs(denom) > 1e-9:
            gain_pct = ((float(entry_price) - float(reversal_price)) / denom) * 100.0
            if gain_pct > 50.0:
                delta = 0.3 * ((gain_pct - 50.0) / 10.0)
                delta = max(0.0, min(0.85, delta))
                nds_factor = 1.0 - delta

    # ۵. تعدیل مشورتی AI
    ai_factor = 1.0
    if ai_approved is False:
        ai_factor = max(0.1, min(1.0, float(cfg.advisory_reject_risk_scale)))

    # ۶. محاسبه حجم نهایی گسسته
    base_risk_usd = equity * float(cfg.base_risk_pct)
    total_risk_usd = (base_risk_usd + recovery_add_usd) * vol_dampener * nds_factor * ai_factor

    stop_dist = abs(float(entry_price) - float(stop_price))
    if stop_dist <= 1e-9 or point_value_per_lot <= 1e-9 or total_risk_usd <= 0:
        return {
            "allowed": False,
            "reason": "فاصله حد ضرر یا پارامترهای محاسبه حجم نامعتبر است.",
            "volume": 0.0,
            "risk_amount_usd": 0.0,
            "risk_pct_effective": 0.0,
            "drawdown_pct": round(dd_pct, 6),
            "yz_vol": round(yz_vol, 8),
            "vol_dampener": round(vol_dampener, 4),
            "recovery_dampener": round(recovery_dampener, 4),
            "nds_proximity_factor": round(nds_factor, 4),
        }

    raw_volume = total_risk_usd / (stop_dist * float(point_value_per_lot))
    step = max(float(cfg.lot_step), 1e-6)
    stepped_volume = math.floor(raw_volume / step) * step

    if stepped_volume < cfg.min_lot:
        return {
            "allowed": False,
            "reason": f"حجم محاسبه‌شده ({stepped_volume:.2f}) کمتر از حداقل مجاز بروکر ({cfg.min_lot:.2f}) است.",
            "volume": 0.0,
            "risk_amount_usd": 0.0,
            "risk_pct_effective": 0.0,
            "drawdown_pct": round(dd_pct, 6),
            "yz_vol": round(yz_vol, 8),
            "vol_dampener": round(vol_dampener, 4),
            "recovery_dampener": round(recovery_dampener, 4),
            "nds_proximity_factor": round(nds_factor, 4),
        }

    final_volume = min(stepped_volume, float(cfg.max_lot))
    actual_risk_usd = final_volume * stop_dist * float(point_value_per_lot)
    eff_risk_pct = (actual_risk_usd / equity) if equity > 0 else 0.0

    return {
        "allowed": True,
        "reason": "تایید شد",
        "volume": round(final_volume, 4),
        "risk_amount_usd": round(actual_risk_usd, 2),
        "risk_pct_effective": round(eff_risk_pct, 6),
        "drawdown_pct": round(dd_pct, 6),
        "yz_vol": round(yz_vol, 8),
        "vol_dampener": round(vol_dampener, 4),
        "recovery_dampener": round(recovery_dampener, 4),
        "nds_proximity_factor": round(nds_factor, 4),
    }