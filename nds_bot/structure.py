from dataclasses import dataclass, field
from typing import Optional
import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view
from .indicators import atr as atr_indicator

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
    rsi: np.ndarray

@dataclass
class HTFInfo:
    bias: int = 0
    nodes: tuple = ()
    opens: tuple = ()
    final_tp: float = 0.0
    htf_bar: int = -1

@dataclass
class Setup:
    direction: int
    nd: int
    c: int
    time: pd.Timestamp
    entry: float
    sl: float      # SL at N3
    tp: float      # TP at 86% of flag
    p: tuple       # (N1, N2, N3)
    o: tuple
    z: float
    f: float
    k: int
    j: int
    lvl864: float
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
    is_h = (ch > H[:, :s].max(axis=1)) & (ch >= H[:, s+1:].max(axis=1))
    is_l = (cl < L[:, :s].min(axis=1)) & (cl <= L[:, s+1:].min(axis=1))
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
        self.p = []
    def add(self, piv: Pivot):
        if self.p and self.p[-1].dir == piv.dir:
            last = self.p[-1]
            if (piv.dir == 1 and piv.price > last.price) or (piv.dir == -1 and piv.price < last.price):
                self.p[-1] = piv
            return
        self.p.append(piv)

def group_by_confirm(pivs: list[Pivot]) -> dict:
    d = {}
    for p in pivs:
        d.setdefault(p.confirm, []).append(p)
    return d

def analyze_htf(piv: list[Pivot], high, low, close, h: int) -> HTFInfo:
    info = HTFInfo(htf_bar=h)
    m = len(piv)
    if m < 5:
        return info
    iH = iL = -1
    for i in range(m-1, -1, -1):
        if piv[i].dir == 1 and iH < 0: iH = i
        if piv[i].dir == -1 and iL < 0: iL = i
        if iH >= 0 and iL >= 0: break
    last_close = close[h]
    s_idx = l_idx = -1
    if iH >= 4:
        p1, p2, p3 = piv[iH-4], piv[iH-2], piv[iH]
        v1, v2 = piv[iH-3], piv[iH-1]
        if (p1.price < p2.price < p3.price
            and high[p3.idx+1:h+1].max(initial=-np.inf) <= p3.price
            and last_close > min(v1.price, v2.price)):
            s_idx = iH
    if iL >= 4:
        l1, l2, l3 = piv[iL-4], piv[iL-2], piv[iL]
        h1, h2 = piv[iL-3], piv[iL-1]
        if (l1.price > l2.price > l3.price
            and low[l3.idx+1:h+1].min(initial=np.inf) >= l3.price
            and last_close < max(h1.price, h2.price)):
            l_idx = iL
    if s_idx >= 0 and (l_idx < 0 or s_idx > l_idx):
        i = s_idx
        info.bias = -1
        info.nodes = (piv[i-4], piv[i-2], piv[i])
        info.opens = (piv[i-3], piv[i-1])
        info.final_tp = min(info.opens[0].price, info.opens[1].price)
    elif l_idx >= 0:
        i = l_idx
        info.bias = 1
        info.nodes = (piv[i-4], piv[i-2], piv[i])
        info.opens = (piv[i-3], piv[i-1])
        info.final_tp = max(info.opens[0].price, info.opens[1].price)
    return info

def htf_states(H: Bars, cfg, only_last: bool = False):
    by_conf = group_by_confirm(raw_pivots(H.h, H.l, cfg.pivot_htf))
    tr = PivotTracker()
    n = len(H.h)
    if only_last:
        for plist in by_conf.values():
            for p in plist: tr.add(p)
        return analyze_htf(tr.p, H.h, H.l, H.c, n-1)
    states = []
    for k in range(n):
        for p in by_conf.get(k, []): tr.add(p)
        states.append(analyze_htf(tr.p, H.h, H.l, H.c, k))
    return states

def resample_ohlc(df: pd.DataFrame, minutes: int) -> pd.DataFrame:
    agg = {"open": "first", "high": "max", "low": "min", "close": "last"}
    if "volume" in df: agg["volume"] = "sum"
    return df.resample(f"{minutes}min", label="left", closed="left").agg(agg).dropna()

def detect_setup(piv: list[Pivot], B: Bars, c: int, htf: HTFInfo, cfg, issued: set) -> Optional[Setup]:
    if htf.bias == 0 or len(piv) < 6:
        return None
    nd = 1 if htf.bias == -1 else -1
    atr = B.atr[c]
    if not np.isfinite(atr) or atr <= 0:
        return None
    ext = B.h if nd == 1 else B.l
    opp = B.l if nd == 1 else B.h
    tried = 0
    for i in range(len(piv)-1, 3, -1):
        if tried >= 4: break
        p3 = piv[i]
        if p3.dir != nd: continue
        tried += 1
        p1, p2 = piv[i-4], piv[i-2]
        o1, o2 = piv[i-3], piv[i-1]
        if not (nd * p1.price < nd * p2.price < nd * p3.price): continue
        i3 = p3.idx
        if c - i3 > 200: continue  # max_flag_bars assumption
        brk = np.nonzero(nd * ext[i3+1:c+1] > nd * p3.price)[0]
        if brk.size == 0: continue
        k = i3 + 1 + int(brk[0])
        if k == i3 + 1: continue
        back = np.nonzero(nd * B.c[k:min(c, k+5)+1] < nd * p3.price)[0]  # BABI within 5 bars
        if back.size == 0: continue
        j = k + int(back[0])
        if j > c or j < c: continue  # signal only on BABI bar
        # Pullback Z
        zs = nd * opp[i3+1:k]
        zi = i3 + 1 + int(np.argmin(zs))
        Z = float(opp[zi])
        if nd * (p3.price - Z) < 0.5 * atr: continue  # min flag depth assumption
        if not nd * Z > nd * (o1.price if nd*o1.price < nd*o2.price else o2.price): continue
        # Spike F
        fi = k + int(np.argmax(nd * ext[k:j+1]))
        F = float(ext[fi])
        # Key
        key = f"{nd}_{B.t[p3.idx]}"
        if key in issued: return None
        # Entry = close of BABI bar
        entry = float(B.c[j])
        # SL at N3 (with small buffer? user said at N3, so exactly p3.price)
        sl = p3.price
        # TP = 86% of flag (N3 to N1)
        flag_len = abs(p3.price - p1.price)
        tp = p3.price - nd * cfg.tp_percent * flag_len
        # Symmetry check: AB = BC? (Here we don't have AB/BC explicitly, but we can check L1 and L2)
        # In 123 flag, the legs are p1->p2 and p2->p3. For symmetry, |p2-p1| ≈ |p3-p2|?
        # Actually AB=BC is a different pattern. The 123 flag is three nodes. We'll skip symmetry filter for now.
        risk = abs(entry - sl)
        if risk <= 0: continue
        # Check if TP is in correct direction
        if nd * tp > nd * entry: continue  # TP should be below entry for SELL
        lvl864 = tp
        return Setup(direction=-nd, nd=nd, c=c, time=pd.Timestamp(B.t[c]), entry=entry, sl=float(sl),
                     tp=float(tp), p=(p1,p2,p3), o=(o1,o2), z=Z, f=F, k=k, j=j,
                     lvl864=lvl864, atr=float(atr), key=key, htf=htf)
    return None

def scan_latest(ltf: pd.DataFrame, htf: Optional[pd.DataFrame], cfg, issued: Optional[set] = None):
    issued = issued if issued is not None else set()
    if len(ltf) < 100: return None
    if htf is None:
        htf = resample_ohlc(ltf, cfg.htf_min)
    ltf_last_close = ltf.index[-1] + pd.Timedelta(minutes=cfg.ltf_min)
    htf = htf[htf.index + pd.Timedelta(minutes=cfg.htf_min) <= ltf_last_close]
    if len(htf) < 30: return None
    H = make_bars(htf, cfg)
    info = htf_states(H, cfg, only_last=True)
    if info.bias == 0: return None
    B = make_bars(ltf, cfg)
    tr = PivotTracker()
    for p in raw_pivots(B.h, B.l, cfg.pivot_ltf): tr.add(p)
    return detect_setup(tr.p, B, len(B.c)-1, info, cfg, issued)

def make_bars(df: pd.DataFrame, cfg) -> Bars:
    atr = atr_indicator(df, cfg.atr_period)
    rsi = np.full(len(df), 50.0)  # not used
    vol = df["volume"].to_numpy(float) if "volume" in df else np.ones(len(df))
    return Bars(df.index.to_numpy(), df["open"].to_numpy(float), df["high"].to_numpy(float),
                df["low"].to_numpy(float), df["close"].to_numpy(float), vol, atr, rsi)