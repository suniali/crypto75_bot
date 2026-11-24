from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view
from .indicators import atr as atr_indicator, dfa_hurst, estimate_tail_alpha


@dataclass(frozen=True)
class Pivot:
    idx: int
    price: float
    dir: int      # +1 high, -1 low
    confirm: int


@dataclass
class Bars:
    t: np.ndarray
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    v: np.ndarray
    atr: np.ndarray


@dataclass
class HTFInfo:
    bias: int = 0
    nodes: tuple = ()
    opens: tuple = ()
    final_tp: float = 0.0
    htf_bar: int = -1
    hurst: float = 0.5
    tail_alpha: float = 1.5


@dataclass
class Setup:
    direction: int          # +1 BUY, -1 SELL
    nd: int                 # +1 if flag peaks (SELL setup), -1 if flag valleys (BUY setup)
    c: int                  # Detection bar index
    time: pd.Timestamp
    entry: float
    sl: float               # Behind spike F + buffer (Course Step 3 & 7)
    tp1: float              # Sequence Target 1: Closure of Open Nodes 1 & 2 (Course Step 4 & 9)
    tp_final: float         # HTF Node 3 Final Target (Course Step 12)
    p: tuple                # (N1, N2, N3)
    o: tuple                # (Open1, Open2)
    z: float                # Pullback Z before spike F
    f: float                # Spike F beyond N3
    k: int                  # Bar index where price spiked beyond N3
    j: int                  # Bar index where price closed back inside N3 (BABI)
    lvl864: float           # 86.4% retracement reference of prior impulse leg
    is_reentry_864: bool    # True if this 123Flag formed near the 86.4% retracement level (Step 5-6)
    leg_measure: float      # Displacement length |N3 - N2| used for Kalman symmetry tracking
    atr: float
    key: str
    htf: HTFInfo
    extras: dict = field(default_factory=dict)


def raw_pivots(high: np.ndarray, low: np.ndarray, strength: int) -> list[Pivot]:
    n, s = len(high), strength
    if n < 2 * s + 1:
        return []
    w = 2 * s + 1
    H, L = sliding_window_view(high, w), sliding_window_view(low, w)
    ch, cl = H[:, s], L[:, s]
    is_h = (ch > H[:, :s].max(axis=1)) & (ch >= H[:, s + 1:].max(axis=1))
    is_l = (cl < L[:, :s].min(axis=1)) & (cl <= L[:, s + 1:].min(axis=1))
    out = []
    for k in np.nonzero(is_h ^ is_l)[0]:
        i = int(k) + s
        if is_h[k]:
            out.append(Pivot(i, float(high[i]), 1, i + s))
        else:
            out.append(Pivot(i, float(low[i]), -1, i + s))
    return out


class PivotTracker:
    def __init__(self):
        self.p: list[Pivot] = []

    def add(self, piv: Pivot):
        if self.p and self.p[-1].dir == piv.dir:
            last = self.p[-1]
            if (piv.dir == 1 and piv.price > last.price) or (piv.dir == -1 and piv.price < last.price):
                self.p[-1] = piv
            return
        self.p.append(piv)


def group_by_confirm(pivs: list[Pivot]) -> dict[int, list[Pivot]]:
    d: dict[int, list[Pivot]] = {}
    for p in pivs:
        d.setdefault(p.confirm, []).append(p)
    return d


def analyze_htf(piv: list[Pivot], high: np.ndarray, low: np.ndarray, close: np.ndarray, h: int, cfg) -> HTFInfo:
    info = HTFInfo(htf_bar=h)
    m = len(piv)
    if m < 5:
        return info

    # Fractal regime diagnostics on HTF window
    win_start = max(0, h + 1 - cfg.hurst_window)
    htf_slice = close[win_start:h + 1]
    info.hurst = dfa_hurst(htf_slice)
    info.tail_alpha = estimate_tail_alpha(htf_slice)

    iH = iL = -1
    for i in range(m - 1, -1, -1):
        if piv[i].dir == 1 and iH < 0:
            iH = i
        if piv[i].dir == -1 and iL < 0:
            iL = i
        if iH >= 0 and iL >= 0:
            break

    last_close = close[h]
    s_idx = l_idx = -1

    # HTF Bearish Sequence: 3 rising peaks N1 < N2 < N3, open valleys V1, V2 not yet closed
    if iH >= 4:
        p1, p2, p3 = piv[iH - 4], piv[iH - 2], piv[iH]
        v1, v2 = piv[iH - 3], piv[iH - 1]
        if (
            p1.price < p2.price < p3.price
            and high[p3.idx + 1:h + 1].max(initial=-np.inf) <= p3.price
            and last_close > min(v1.price, v2.price)
        ):
            s_idx = iH

    # HTF Bullish Sequence: 3 falling valleys L1 > L2 > L3, open peaks H1, H2 not yet closed
    if iL >= 4:
        l1, l2, l3 = piv[iL - 4], piv[iL - 2], piv[iL]
        h1, h2 = piv[iL - 3], piv[iL - 1]
        if (
            l1.price > l2.price > l3.price
            and low[l3.idx + 1:h + 1].min(initial=np.inf) >= l3.price
            and last_close < max(h1.price, h2.price)
        ):
            l_idx = iL

    if s_idx >= 0 and (l_idx < 0 or s_idx > l_idx):
        i = s_idx
        info.bias = -1
        info.nodes = (piv[i - 4], piv[i - 2], piv[i])
        info.opens = (piv[i - 3], piv[i - 1])
        info.final_tp = float(min(info.opens[0].price, info.opens[1].price))
    elif l_idx >= 0:
        i = l_idx
        info.bias = 1
        info.nodes = (piv[i - 4], piv[i - 2], piv[i])
        info.opens = (piv[i - 3], piv[i - 1])
        info.final_tp = float(max(info.opens[0].price, info.opens[1].price))
    return info


def htf_states(H: Bars, cfg, only_last: bool = False):
    by_conf = group_by_confirm(raw_pivots(H.h, H.l, cfg.pivot_htf))
    tr = PivotTracker()
    n = len(H.h)
    if only_last:
        for plist in by_conf.values():
            for p in plist:
                tr.add(p)
        return analyze_htf(tr.p, H.h, H.l, H.c, n - 1, cfg)
    states = []
    for k in range(n):
        for p in by_conf.get(k, []):
            tr.add(p)
        states.append(analyze_htf(tr.p, H.h, H.l, H.c, k, cfg))
    return states


def resample_ohlc(df: pd.DataFrame, minutes: int) -> pd.DataFrame:
    agg = {"open": "first", "high": "max", "low": "min", "close": "last"}
    if "volume" in df:
        agg["volume"] = "sum"
    return df.resample(f"{minutes}min", label="left", closed="left").agg(agg).dropna()


def detect_setup(piv: list[Pivot], B: Bars, c: int, htf: HTFInfo, cfg, issued: set) -> Optional[Setup]:
    if htf.bias == 0 or len(piv) < 6:
        return None

    # Optional Fractal Regime Guard (Avoid pure noise random walk on HTF)
    if cfg.use_fractal_filter:
        if cfg.hurst_random_low <= htf.hurst <= cfg.hurst_random_high:
            return None
        if htf.tail_alpha < cfg.min_tail_alpha:
            return None

    # In HTF Downtrend (bias = -1), LTF forms rising counter-trend 1-2-3 peaks (nd = +1) to SELL
    # In HTF Uptrend (bias = +1), LTF forms falling counter-trend 1-2-3 valleys (nd = -1) to BUY
    nd = 1 if htf.bias == -1 else -1
    atr_val = B.atr[c]
    if not np.isfinite(atr_val) or atr_val <= 0:
        return None

    ext = B.h if nd == 1 else B.l
    opp = B.l if nd == 1 else B.h
    tried = 0

    for i in range(len(piv) - 1, 3, -1):
        if tried >= 4:
            break
        p3 = piv[i]
        if p3.dir != nd:
            continue
        tried += 1

        p1, p2 = piv[i - 4], piv[i - 2]
        o1, o2 = piv[i - 3], piv[i - 1]

        # Sequence 1 -> 2 -> 3 monotonic condition
        if not (nd * p1.price < nd * p2.price < nd * p3.price):
            continue

        i3 = p3.idx
        if c - i3 > cfg.max_flag_bars:
            continue

        # 1) Spike F beyond Node 3
        brk = np.nonzero(nd * ext[i3 + 1:c + 1] > nd * p3.price)[0]
        if brk.size == 0:
            continue
        k = i3 + 1 + int(brk[0])
        if k == i3 + 1:
            continue

        # 2) Close back inside Node 3 (BABI) within babi_max_bars
        back = np.nonzero(nd * B.c[k:min(c, k + cfg.babi_max_bars) + 1] < nd * p3.price)[0]
        if back.size == 0:
            continue
        j = k + int(back[0])

        # Respect signal_max_age (0 for strict backtest bar, >=1 for live polling)
        if j > c or (c - j) > cfg.signal_max_age:
            continue

        # 3) Pullback Z between N3 and Spike F must have depth >= min_flag_atr AND must NOT close Open Nodes 1 & 2
        zs = nd * opp[i3 + 1:k]
        if zs.size == 0:
            continue
        zi = i3 + 1 + int(np.argmin(zs))
        Z = float(opp[zi])
        if nd * (p3.price - Z) < cfg.min_flag_atr * atr_val:
            continue

        # Ensure Open Nodes 1 & 2 are still open before entry
        furthest_open = float(o1.price if nd * o1.price < nd * o2.price else o2.price)
        nearest_open = float(o2.price if nd * o1.price < nd * o2.price else o1.price)
        if not (nd * Z > nd * furthest_open):
            continue

        # 4) Spike F extreme
        fi = k + int(np.argmax(nd * ext[k:j + 1]))
        F = float(ext[fi])

        key = f"{nd}_{B.t[p3.idx]}"
        if key in issued:
            return None

        entry = float(B.c[j])

        # Course Step 3 & 7: SL placed behind Spike F + small ATR buffer
        sl = float(F + nd * cfg.sl_buffer_atr * atr_val)
        risk = abs(entry - sl)
        if risk <= 0:
            continue

        # Course Step 4 & 9: TP1 = Closure of Open Nodes 1 & 2 (Sequence Priority, NOT waiting for 86%!)
        tp1 = furthest_open if cfg.tp1_mode == "both" else nearest_open
        if nd * tp1 >= nd * entry:
            continue

        # Course Step 12: Final Target = HTF Node 3 Open Target
        tp_final = float(htf.final_tp)
        if nd * tp_final >= nd * tp1:
            tp_final = tp1

        # Reference 86.4% level of the prior impulse leg (Step 5-6 secondary re-entry check)
        # If there was a prior impulse leg before p1 (i >= 5), measure 86.4% retracement of that leg
        lvl864 = float("nan")
        is_reentry_864 = False
        if i >= 5:
            prior_origin = piv[i - 5].price
            prior_impulse = abs(prior_origin - p1.price)
            if prior_impulse > 0:
                # Retracement from p1 back toward prior_origin by 86.4%
                lvl864 = float(p1.price + nd * cfg.cycle_retrace_pct * prior_impulse)
                if abs(F - lvl864) <= cfg.cycle_tolerance * prior_impulse:
                    is_reentry_864 = True

        # Single displacement wave length |N3 - N2| for Kalman symmetry tracking
        leg_measure = float(abs(p3.price - p2.price))

        return Setup(
            direction=-nd,
            nd=nd,
            c=c,
            time=pd.Timestamp(B.t[c]),
            entry=entry,
            sl=sl,
            tp1=tp1,
            tp_final=tp_final,
            p=(p1, p2, p3),
            o=(o1, o2),
            z=Z,
            f=F,
            k=k,
            j=j,
            lvl864=lvl864,
            is_reentry_864=is_reentry_864,
            leg_measure=leg_measure,
            atr=float(atr_val),
            key=key,
            htf=htf,
        )
    return None


def scan_latest(ltf: pd.DataFrame, htf: Optional[pd.DataFrame], cfg, issued: Optional[set] = None):
    issued = issued if issued is not None else set()
    if len(ltf) < 100:
        return None
    if htf is None:
        htf = resample_ohlc(ltf, cfg.htf_min)
    ltf_last_close = ltf.index[-1] + pd.Timedelta(minutes=cfg.ltf_min)
    htf = htf[htf.index + pd.Timedelta(minutes=cfg.htf_min) <= ltf_last_close]
    if len(htf) < 30:
        return None
    H = make_bars(htf, cfg)
    info = htf_states(H, cfg, only_last=True)
    if info.bias == 0:
        return None
    B = make_bars(ltf, cfg)
    tr = PivotTracker()
    for p in raw_pivots(B.h, B.l, cfg.pivot_ltf):
        tr.add(p)
    return detect_setup(tr.p, B, len(B.c) - 1, info, cfg, issued)


def make_bars(df: pd.DataFrame, cfg) -> Bars:
    atr_arr = atr_indicator(df, cfg.atr_period)
    vol = df["volume"].to_numpy(float) if "volume" in df else np.ones(len(df))
    return Bars(
        df.index.to_numpy(),
        df["open"].to_numpy(float),
        df["high"].to_numpy(float),
        df["low"].to_numpy(float),
        df["close"].to_numpy(float),
        vol,
        atr_arr,
    )