"""Matched controls.

For every treated signal (an independent-episode first signal) up to `controls_per_treated` controls are drawn from OTHER independent-episode first signals of the SAME direction that are
  * within +-`match_window_sessions` trading sessions of the treated signal's date,
  * in the same ATR% decile, 20-day dollar-volume tercile and 60-day-return tercile (edges frozen on the TRAIN segment, within direction),
  * in the same sector when BOTH have an observed (dated) sector; reconstructed-sector rows only match reconstructed-sector rows (sector is then not used as a match key),
  * from a different symbol, and not already used as a control in this run (no reuse).
Treated signals are processed in a seeded random order and each takes its nearest-in-date available controls. Matching looks only at decision-time covariates and dates, never at an outcome, so permuting outcomes
afterwards (see inference) is valid with the matched sets held fixed."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .config import Config
from .splits import TRAIN


def freeze_bins(df: pd.DataFrame, cfg: Config) -> Dict[str, Dict[str, List[float]]]:
    """Quantile edges per direction from the TRAIN segment only (eligible, independent-episode first signals)."""
    edges: Dict[str, Dict[str, List[float]]] = {}
    for d in (1, -1):
        g = df[(df["direction"] == d) & (df["segment"] == TRAIN) & df["eligible"] & df["is_episode_first"]]
        e: Dict[str, List[float]] = {}
        for col, key, nb in (("atr_pct", "atr", cfg.atr_pct_bins), ("dollar_vol_20", "dv", cfg.dollar_vol_bins), ("ret_60d", "ret60", cfg.ret60_bins)):
            x = g[col].replace([np.inf, -np.inf], np.nan).dropna()
            e[key] = [float(v) for v in np.quantile(x, np.linspace(0, 1, nb + 1)[1:-1])] if len(x) else []
        edges["long" if d == 1 else "short"] = e
    return edges


def add_strata(df: pd.DataFrame, edges: Dict[str, Dict[str, List[float]]]) -> pd.DataFrame:
    out = df.copy()
    out["atr_decile"] = -1
    out["dv_tercile"] = -1
    out["ret60_tercile"] = -1
    for d, name in ((1, "long"), (-1, "short")):
        m = (out["direction"] == d).to_numpy()
        e = edges[name]
        for col, key, dest in (("atr_pct", "atr", "atr_decile"), ("dollar_vol_20", "dv", "dv_tercile"), ("ret_60d", "ret60", "ret60_tercile")):
            v = out.loc[m, col].to_numpy(dtype=float)
            code = np.searchsorted(np.asarray(e[key]), v, side="right").astype(int)
            code[~np.isfinite(v)] = -1
            out.loc[m, dest] = code
    return out


@dataclass
class MatchResult:
    treated_pos: np.ndarray            # positions (into the pool frame) of treated signals that found >= 1 control
    control_pos: np.ndarray            # (n_treated_matched, k) positions, -1 where absent
    n_treated: int
    n_unmatched: int

    @property
    def n_sets(self) -> int:
        return int(len(self.treated_pos))

    @property
    def n_controls(self) -> int:
        return int((self.control_pos >= 0).sum())


def _key(r_atr, r_dv, r_ret, sector, observed) -> Tuple:
    return (int(r_atr), int(r_dv), int(r_ret), sector if observed else "-")


def match(pool: pd.DataFrame, treated: np.ndarray, control: np.ndarray, cfg: Config, rng: np.random.Generator) -> MatchResult:
    """`pool` is a frame with columns date_index, symbol, atr_decile, dv_tercile, ret60_tercile, sector, sector_provenance; `treated` / `control` are boolean masks aligned with it."""
    t_ix = np.flatnonzero(treated)
    c_ix = np.flatnonzero(control & ~treated)
    date_i = pool["date_index"].to_numpy()
    sym = pool["symbol"].to_numpy()
    atr, dv, ret = pool["atr_decile"].to_numpy(), pool["dv_tercile"].to_numpy(), pool["ret60_tercile"].to_numpy()
    if not cfg.match_on_ret60:
        ret = np.where(ret < 0, -1, 0)                      # the 60-day-return tercile is not a match key (a missing value still disqualifies the row)
    sector = pool["sector"].to_numpy(dtype=object)
    obs = (pool["sector_provenance"].to_numpy() == "OBSERVED_PIT")
    buckets: Dict[Tuple, List[int]] = {}
    for i in c_ix:
        if atr[i] < 0 or dv[i] < 0 or ret[i] < 0:
            continue
        buckets.setdefault(_key(atr[i], dv[i], ret[i], sector[i], obs[i]), []).append(i)
    idx_arr: Dict[Tuple, Tuple[np.ndarray, np.ndarray]] = {}
    for k, v in buckets.items():
        v = np.asarray(v)
        o = np.argsort(date_i[v], kind="stable")
        idx_arr[k] = (date_i[v][o], v[o])
    used = np.zeros(len(pool), dtype=bool)
    order = rng.permutation(t_ix)
    kmax = cfg.controls_per_treated
    out_t: List[int] = []
    out_c: List[List[int]] = []
    for i in order:
        if atr[i] < 0 or dv[i] < 0 or ret[i] < 0:
            continue
        b = idx_arr.get(_key(atr[i], dv[i], ret[i], sector[i], obs[i]))
        if b is None:
            continue
        dates, ids = b
        lo = np.searchsorted(dates, date_i[i] - cfg.match_window_sessions, side="left")
        hi = np.searchsorted(dates, date_i[i] + cfg.match_window_sessions, side="right")
        cand = ids[lo:hi]
        if len(cand) == 0:
            continue
        cand = cand[(~used[cand]) & (sym[cand] != sym[i])]
        if len(cand) == 0:
            continue
        dist = np.abs(date_i[cand] - date_i[i]) + rng.random(len(cand)) * 0.5          # nearest in date, ties broken at random
        pick = cand[np.argsort(dist, kind="stable")[:kmax]]
        used[pick] = True
        out_t.append(int(i))
        out_c.append(list(pick) + [-1] * (kmax - len(pick)))
    tp = np.asarray(out_t, dtype=int)
    cp = np.asarray(out_c, dtype=int).reshape(len(out_t), kmax)
    o = np.argsort(tp, kind="stable")                         # canonical order, independent of the random processing order
    return MatchResult(tp[o], cp[o], int(len(t_ix)), int(len(t_ix) - len(tp)))


def balance(pool: pd.DataFrame, m: MatchResult, cols: Tuple[str, ...] = ("atr_pct", "log_dollar_vol_20", "ret_60d", "ret_20d", "dist_sma200_pct")) -> Dict[str, Dict[str, float]]:
    """Standardised mean difference (treated minus matched controls, in pooled-sd units) before matching (against ALL control-pool members is not available here) and after."""
    out: Dict[str, Dict[str, float]] = {}
    if m.n_sets == 0:
        return out
    cflat = m.control_pos[m.control_pos >= 0]
    for c in cols:
        t = pool[c].to_numpy(dtype=float)[m.treated_pos]
        k = pool[c].to_numpy(dtype=float)[cflat]
        t, k = t[np.isfinite(t)], k[np.isfinite(k)]
        if len(t) < 2 or len(k) < 2:
            continue
        sd = np.sqrt((t.var(ddof=1) + k.var(ddof=1)) / 2)
        out[c] = {"treated_mean": round(float(t.mean()), 4), "control_mean": round(float(k.mean()), 4), "smd": round(float((t.mean() - k.mean()) / sd), 4) if sd > 0 else 0.0}
    return out
