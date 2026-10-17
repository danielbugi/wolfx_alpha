"""The decision-time features and the dataset builder. PURE: it takes price frames and returns a frame; it opens no connection and reads no clock.

Everything in a FEATURE column uses bars up to and including the signal bar (checked by tests: the value computed on history truncated at the signal bar is identical). Everything in a LABEL column (see labels.py) needs bars after it.

Panel features (relative strength, regime, breadth, sector strength, group breakouts) come from a panel of ALL symbols in the supplied price frames, so they inherit that panel's survivorship: they are RECONSTRUCTED, never observed.
"""
from __future__ import annotations

import os
import sys
from typing import Dict, List, Mapping, Optional, Tuple

import numpy as np
import pandas as pd

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from ml_training.features import price_features as pf  # noqa: E402

from . import labels as L
from .config import Config
from .episodes import assign_episodes
from .splits import assign_segment

MIN_BAR_INDEX = 60


def clean_prices(px: pd.DataFrame) -> pd.DataFrame:
    px = px.copy()
    for c in ("open", "high", "low", "close", "volume"):
        px[c] = pd.to_numeric(px[c], errors="coerce")
    px["date"] = pd.to_datetime(px["date"])
    return px.dropna(subset=["close"]).drop_duplicates("date").sort_values("date").reset_index(drop=True)


def detect_signals(prices: Mapping[str, pd.DataFrame], start: str = "2018-01-01") -> pd.DataFrame:
    """Re-detect the dataset's signal set from bars with the dataset builder's own rule (close crosses the previous 20-bar channel with the previous close inside; liquid; 60+ bars; no discontinuity within 253 bars
    before or 20 after). Used to reconcile `ml_breakout_dataset_v2`, which is rewritten daily."""
    rows = []
    for sym, px in prices.items():
        px = clean_prices(px)
        if len(px) < MIN_BAR_INDEX + pf.PLAN_HORIZON:
            continue
        disc = pf.find_discontinuities(px)
        bad = pf.contaminated_mask(len(px), disc["pos"])
        ind = pf.compute_indicators(px)
        d = pf.detect_breakouts(ind)
        liquid = (ind["dollar_vol_20"] >= pf.MIN_DOLLAR_VOLUME_20).to_numpy()
        keep = (d != 0) & ~bad & liquid & (np.arange(len(px)) >= MIN_BAR_INDEX) & (pd.to_datetime(ind["date"]) >= pd.Timestamp(start)).to_numpy()
        pos = np.flatnonzero(keep)
        rows.append(pd.DataFrame({"symbol": sym, "date": pd.to_datetime(ind["date"]).to_numpy()[pos], "direction": d[pos].astype(int)}))
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=["symbol", "date", "direction"])


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ the panel
class Panel:
    """Date x symbol matrices computed from ALL supplied symbols (survivors only in production)."""

    def __init__(self, prices: Mapping[str, pd.DataFrame], sector_snaps: Optional[pd.DataFrame], cfg: Config):
        self.cfg = cfg
        self.symbols = sorted(prices)
        frames = {s: clean_prices(prices[s]) for s in self.symbols}
        self.dates = pd.DatetimeIndex(np.unique(np.concatenate([f["date"].to_numpy() for f in frames.values()])))
        T, S = len(self.dates), len(self.symbols)
        close = np.full((T, S), np.nan)
        vol = np.full((T, S), np.nan)
        for j, s in enumerate(self.symbols):
            f = frames[s]
            p = self.dates.get_indexer(f["date"])
            close[p, j] = f["close"].to_numpy()
            vol[p, j] = f["volume"].to_numpy()
        self.close = pd.DataFrame(close, index=self.dates, columns=self.symbols)
        dv = (self.close * pd.DataFrame(vol, index=self.dates, columns=self.symbols)).rolling(20, min_periods=20).mean()
        self.liquid = dv >= cfg.min_dollar_volume_20
        r = self.close.pct_change(fill_method=None)
        r = r.where(r.abs() <= 0.5)                                  # a +-50% day is a data discontinuity, not index movement
        ew = r.where(self.liquid).mean(axis=1).fillna(0.0)
        self.index_level = (1.0 + ew).cumprod()
        ma = self.index_level.rolling(cfg.index_ma_bars, min_periods=cfg.index_ma_bars).mean()
        self.index_above_200 = (self.index_level > ma).where(ma.notna())
        above = (self.close > self.close.rolling(cfg.breadth_ma_bars, min_periods=cfg.breadth_ma_bars).mean())
        have = self.close.rolling(cfg.breadth_ma_bars, min_periods=cfg.breadth_ma_bars).mean().notna() & self.liquid
        self.breadth_50 = (above & have).sum(axis=1) / have.sum(axis=1).replace(0, np.nan)
        self.rs: Dict[int, pd.DataFrame] = {}
        self.rs_pct: Dict[int, pd.DataFrame] = {}
        for n in cfg.rs_windows:
            ret_n = self.close / self.close.shift(n) - 1.0
            idx_n = self.index_level / self.index_level.shift(n) - 1.0
            rs = ret_n.sub(idx_n, axis=0).where(self.liquid)
            self.rs[n] = rs
            self.rs_pct[n] = rs.rank(axis=1, pct=True)
        self._build_sectors(sector_snaps)

    def _build_sectors(self, snaps: Optional[pd.DataFrame]) -> None:
        T, S = len(self.dates), len(self.symbols)
        names: List[str] = []
        code_of: Dict[str, int] = {}
        self.first_observed_date: Optional[pd.Timestamp] = None
        code = np.full((T, S), -1, dtype=np.int16)
        obs = np.zeros((T, S), dtype=bool)
        if snaps is not None and len(snaps):
            sn = snaps.dropna(subset=["sector"])
            sn = sn[sn["sector"].astype(str) != ""]
            for s in sorted(sn["sector"].unique()):
                code_of[s] = len(names)
                names.append(s)
            self.first_observed_date = pd.Timestamp(sn["date"].min())
            piv = sn.drop_duplicates(["date", "symbol"], keep="last").pivot(index="date", columns="symbol", values="sector")
            piv.index = pd.to_datetime(piv.index)
            piv = piv.reindex(columns=self.symbols)
            latest = piv.ffill().iloc[-1]
            static = np.array([code_of.get(latest.get(s), -1) if isinstance(latest.get(s), str) else -1 for s in self.symbols], dtype=np.int16)
            code[:, :] = static[None, :]                                          # before any snapshot: today's sector applied backwards (RECONSTRUCTED)
            pf_ = piv.reindex(self.dates, method=None)
            pf_ = pf_.reindex(self.dates.union(piv.index)).ffill().reindex(self.dates)
            m = pf_.notna().to_numpy()
            obs_codes = np.full((T, S), -1, dtype=np.int16)
            for nm, c_ in code_of.items():
                obs_codes[(pf_ == nm).to_numpy()] = c_
            code = np.where(m, obs_codes, code)
            obs = m
        self.sector_names = names
        self.sector_code = code
        self.sector_observed = obs

    def sector_rank_table(self, date_positions: np.ndarray, window: int = 60) -> Dict[int, np.ndarray]:
        """For each requested date position: the rank (1 = strongest) of every sector by mean relative strength of its liquid members; NaN for a sector with fewer than 5 liquid members."""
        rs = self.rs[window].to_numpy()
        liq = self.liquid.to_numpy()
        K = max(1, len(self.sector_names))
        out: Dict[int, np.ndarray] = {}
        for t in np.unique(date_positions):
            codes = self.sector_code[t]
            v = rs[t]
            ok = np.isfinite(v) & liq[t] & (codes >= 0)
            sums = np.bincount(codes[ok], weights=v[ok], minlength=K)
            cnt = np.bincount(codes[ok], minlength=K)
            mean = np.where(cnt >= 5, sums / np.maximum(cnt, 1), np.nan)
            rank = np.full(K, np.nan)
            valid = np.isfinite(mean)
            if valid.any():
                order = (-mean[valid]).argsort(kind="stable").argsort(kind="stable") + 1
                rank[valid] = order
            out[int(t)] = rank
        return out


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ the dataset
def build_dataset(prices: Mapping[str, pd.DataFrame], signals: pd.DataFrame, sector_snaps: Optional[pd.DataFrame], cfg: Config, panel: Optional[Panel] = None) -> pd.DataFrame:
    """One row per supplied signal (symbol, date, direction). Deterministic: rows sorted by (symbol, date, direction)."""
    panel = panel or Panel(prices, sector_snaps, cfg)
    sig = signals.copy()
    sig["date"] = pd.to_datetime(sig["date"])
    sig = sig.sort_values(["symbol", "date", "direction"]).reset_index(drop=True)
    sym_col = {s: j for j, s in enumerate(panel.symbols)}
    parts: List[pd.DataFrame] = []
    first_year = {s: int(pd.to_datetime(prices[s]["date"]).min().year) for s in prices}
    for sym, g in sig.groupby("symbol", sort=True):
        if sym not in prices:
            continue
        px = clean_prices(prices[sym])
        n = len(px)
        ind = pf.compute_indicators(px)
        disc = pf.find_discontinuities(px)
        dates = pd.DatetimeIndex(ind["date"])
        pos = dates.get_indexer(pd.DatetimeIndex(g["date"]))
        keep = pos >= 0
        pos, dr = pos[keep], g["direction"].to_numpy()[keep].astype(int)
        if len(pos) == 0:
            continue
        feats = pf.build_breakout_features(ind, pos, dr.astype(float), None)
        feats = feats[[c for c in feats.columns if not c.startswith("sector_") and c != "is_bullish"]]
        bad_back = pf.contaminated_mask(n, disc["pos"], forward=0)                                   # lookback only
        disc_win = {H: pf.contaminated_mask(n, disc["pos"], forward=H) for H in cfg.horizons}
        lab = L.signal_labels(px["open"].to_numpy(float), px["high"].to_numpy(float), px["low"].to_numpy(float), px["close"].to_numpy(float), ind["atr"].to_numpy(), pos, dr, cfg, disc_win)
        row = pd.DataFrame({"symbol": sym, "signal_date": dates.to_numpy()[pos], "direction": dr, "bar_index": pos,
                            "dollar_vol_20": ind["dollar_vol_20"].to_numpy()[pos], "atr14": ind["atr"].to_numpy()[pos]})
        row = pd.concat([row, feats.reset_index(drop=True)], axis=1)
        row["history_ok"] = (pos >= cfg.min_history_bars) & ~bad_back[pos]
        for k, v in lab.items():
            row[k] = v
        nxt = np.minimum(pos + 1, n - 1)
        row["entry_date"] = np.where(pos + 1 < n, dates.to_numpy()[nxt], np.datetime64("NaT"))
        for H in cfg.horizons:
            endp = pos + H
            row[f"window_end_date_{H}"] = np.where(endp < n, dates.to_numpy()[np.minimum(endp, n - 1)], np.datetime64("NaT"))
        row["symbol_first_bar_year"] = first_year[sym]
        parts.append(row)
    df = pd.concat(parts, ignore_index=True)
    df["signal_date"] = pd.to_datetime(df["signal_date"])
    df["window_end_date"] = df[f"window_end_date_{cfg.primary_horizon}"]
    df = df.drop(columns=[f"window_end_date_{H}" for H in cfg.horizons])
    df["year"] = df["signal_date"].dt.year
    df["month"] = df["signal_date"].dt.to_period("M").astype(str)
    df["cohort"] = np.where(df["symbol_first_bar_year"] <= 2016, "listed_by_2016", "listed_later")
    t_idx = panel.dates.get_indexer(df["signal_date"])
    df["date_index"] = t_idx
    cols = np.array([sym_col[s] for s in df["symbol"]])
    ok = t_idx >= 0
    for n in cfg.rs_windows:
        df[f"rs{n}"] = np.where(ok, panel.rs[n].to_numpy()[np.where(ok, t_idx, 0), cols], np.nan)
        df[f"rs{n}_pct"] = np.where(ok, panel.rs_pct[n].to_numpy()[np.where(ok, t_idx, 0), cols], np.nan)
    df["index_above_200"] = np.where(ok, panel.index_above_200.to_numpy(dtype=float)[np.where(ok, t_idx, 0)], np.nan)
    df["breadth_50"] = np.where(ok, panel.breadth_50.to_numpy()[np.where(ok, t_idx, 0)], np.nan)
    code = np.where(ok, panel.sector_code[np.where(ok, t_idx, 0), cols], -1)
    df["sector"] = [panel.sector_names[c] if c >= 0 else None for c in code]
    df["sector_provenance"] = np.where(np.where(ok, panel.sector_observed[np.where(ok, t_idx, 0), cols], False), "OBSERVED_PIT", "RECONSTRUCTED")
    rank_tab = panel.sector_rank_table(t_idx[ok], 60)
    sr = np.full(len(df), np.nan)
    for i in np.flatnonzero(ok & (code >= 0)):
        r = rank_tab[int(t_idx[i])]
        sr[i] = r[code[i]] if code[i] < len(r) else np.nan
    df["sector_rs60_rank"] = sr
    df["group_breakouts_10"] = _group_counts(df, code, panel, cfg)
    df["episode_id"], df["is_episode_first"] = assign_episodes(df["symbol"].to_numpy(), df["direction"].to_numpy(), df["bar_index"].to_numpy(), cfg.episode_gap_bars)
    df["segment"] = assign_segment(df["signal_date"], cfg)
    df["eligible"] = df["history_ok"] & np.isfinite(df["atr14"]) & ok & (df["dollar_vol_20"] >= cfg.min_dollar_volume_20)
    df = df.sort_values(["symbol", "signal_date", "direction"]).reset_index(drop=True)
    return df


def _group_counts(df: pd.DataFrame, code: np.ndarray, panel: Panel, cfg: Config) -> np.ndarray:
    """Other same-sector same-direction breakout signals in the previous `group_lookback_sessions` sessions (strictly before the signal date)."""
    T = len(panel.dates)
    K = max(1, len(panel.sector_names))
    out = np.full(len(df), np.nan)
    t_idx = df["date_index"].to_numpy()
    dr = df["direction"].to_numpy()
    for d in (1, -1):
        cnt = np.zeros((K, T), dtype=np.int32)
        m = (dr == d) & (code >= 0) & (t_idx >= 0)
        np.add.at(cnt, (code[m], t_idx[m]), 1)
        cum = np.concatenate([np.zeros((K, 1), dtype=np.int64), np.cumsum(cnt, axis=1)], axis=1)         # cum[:, t] = sum of counts at dates < t
        w = cfg.group_lookback_sessions
        sel = np.flatnonzero(m)
        t = t_idx[sel]
        lo = np.maximum(t - w, 0)
        out[sel] = cum[code[sel], t] - cum[code[sel], lo]
    return out
