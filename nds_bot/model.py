"""AI layer = meta-labeling: the NDS rules propose a trade, a classifier estimates P(win) from the
NDS-geometry features and the trade is only shown/sent if P(win) >= threshold.

Walk-forward only (chronological splits, embargo) so the reported numbers are out-of-sample."""
from __future__ import annotations

import warnings
from dataclasses import dataclass

import joblib
import numpy as np
import pandas as pd

from .backtest import stats
from .features import FEATURES

MIN_SAMPLES = 150


def make_estimator(kind: str = "auto"):
    if kind in ("auto", "xgb"):
        try:
            from xgboost import XGBClassifier
            return XGBClassifier(n_estimators=200, max_depth=3, learning_rate=0.05, subsample=0.8,
                                 colsample_bytree=0.8, min_child_weight=5, eval_metric="logloss",
                                 random_state=0)
        except ImportError:
            if kind == "xgb":
                raise
    from sklearn.ensemble import HistGradientBoostingClassifier
    return HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=200,
                                          l2_regularization=1.0, min_samples_leaf=15, random_state=0)


def _xy(df: pd.DataFrame):
    X = df[FEATURES].to_numpy(float)
    y = (df["r"].to_numpy(float) > 0).astype(int)
    return X, y


def _fit(df: pd.DataFrame, kind: str):
    X, y = _xy(df)
    if len(np.unique(y)) < 2:
        raise ValueError("training data contains only one class")
    est = make_estimator(kind)
    est.fit(X, y)
    return est


def walk_forward_proba(df: pd.DataFrame, kind: str = "auto", n_splits: int = 5, embargo: int = 5):
    """Out-of-fold probabilities with expanding-window training. Rows before the first test fold get NaN."""
    n = len(df)
    proba = np.full(n, np.nan)
    first = max(60, n // (n_splits + 1))
    edges = np.linspace(first, n, n_splits + 1).astype(int)
    for a, b in zip(edges[:-1], edges[1:]):
        tr = df.iloc[:max(0, a - embargo)]
        if len(tr) < 50 or tr["r"].gt(0).nunique() < 2:
            continue
        est = _fit(tr, kind)
        proba[a:b] = est.predict_proba(df.iloc[a:b][FEATURES].to_numpy(float))[:, 1]
    return proba


def choose_threshold(df: pd.DataFrame, proba: np.ndarray, min_n: int = 30):
    """Pick the probability cut-off from out-of-fold predictions. The label 'R>0' has a low base rate
    (big reward:risk), so the cut is chosen as a quantile (take the top 20%..70%), not a fixed 0.5."""
    m = ~np.isnan(proba)
    d, p = df[m], proba[m]
    r = d["r"].to_numpy(float)
    best_t, best_e = float(np.quantile(p, 0.5)), -np.inf
    for frac in (0.2, 0.3, 0.4, 0.5, 0.6, 0.7):
        t = float(np.quantile(p, 1 - frac))
        sel = p >= t
        if sel.sum() < min_n:
            continue
        e = r[sel].mean()
        if e > best_e:
            best_t, best_e = t, float(e)
    return best_t


@dataclass
class Report:
    n_setups: int
    base: dict
    oof_base: dict
    oof_taken: dict
    holdout_base: dict
    holdout_taken: dict
    threshold: float
    auc_oof: float
    auc_holdout: float
    ai_useful: bool


def train_and_evaluate(df: pd.DataFrame, kind: str = "auto", holdout_frac: float = 0.2):
    """1) walk-forward OOF on the first (1-holdout) part -> choose threshold
       2) fit on that part, evaluate once on the untouched last holdout part
       3) final model fit on everything. Returns (bundle, Report)."""
    from sklearn.metrics import roc_auc_score
    df = df.sort_values("time").reset_index(drop=True)
    if len(df) < MIN_SAMPLES:
        raise ValueError(f"only {len(df)} setups - need at least {MIN_SAMPLES} (use more history / symbols)")
    cut = int(len(df) * (1 - holdout_frac))
    dev, hold = df.iloc[:cut].reset_index(drop=True), df.iloc[cut:].reset_index(drop=True)

    proba = walk_forward_proba(dev, kind)
    thr = choose_threshold(dev, proba)
    m = ~np.isnan(proba)
    y_dev = (dev["r"] > 0).astype(int).to_numpy()
    auc_oof = roc_auc_score(y_dev[m], proba[m]) if len(np.unique(y_dev[m])) == 2 else float("nan")

    est_dev = _fit(dev, kind)
    ph = est_dev.predict_proba(hold[FEATURES].to_numpy(float))[:, 1]
    y_h = (hold["r"] > 0).astype(int).to_numpy()
    auc_h = roc_auc_score(y_h, ph) if len(np.unique(y_h)) == 2 else float("nan")

    h_base, h_take = stats(hold), stats(hold[ph >= thr])
    # The AI filter is only called "useful" if, on data it never saw, the trades it keeps have a clearly
    # better average R than all trades AND there are enough of them.
    useful = bool(h_take.get("trades", 0) >= 20 and h_take["avg_R_ci95"][0] > h_base["avg_R"])
    # useful = bool(h_take.get("trades", 0) >= 20
    #               and h_take["avg_R"] > 0
    #               and h_take["avg_R_ci95"][0] > h_base["avg_R"])
    rep = Report(len(df), stats(df), stats(dev[m]), stats(dev[m][proba[m] >= thr]),
                 h_base, h_take, thr, float(auc_oof), float(auc_h), useful)
    bundle = {"model": _fit(df, kind), "features": FEATURES, "threshold": thr, "useful": useful,
              "meta": {"n": len(df), "holdout_avgR_taken": rep.holdout_taken.get("avg_R"),
                       "auc_holdout": auc_h}}
    return bundle, rep


def save_bundle(bundle: dict, path: str) -> None:
    joblib.dump(bundle, path)


def load_bundle(path: str) -> dict:
    return joblib.load(path)


def predict_win_prob(bundle: dict, feats: dict) -> float:
    x = np.array([[feats[f] for f in bundle["features"]]], float)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return float(bundle["model"].predict_proba(x)[0, 1])
