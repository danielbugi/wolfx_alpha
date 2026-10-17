"""Synthetic data for the harness tests (random-walk price panels and planted-effect datasets). Deterministic by seed. Test support only; nothing in a real run imports this module."""
from __future__ import annotations

from typing import Dict, Optional

import numpy as np
import pandas as pd


def make_prices(n_symbols: int = 40, start: str = "2017-03-01", end: str = "2026-08-31", seed: int = 1, trend_symbols: int = 0) -> Dict[str, pd.DataFrame]:
    """Random-walk OHLCV frames on a business-day calendar. `trend_symbols` of them get a persistent positive drift so that breakouts follow through."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start, end)
    out: Dict[str, pd.DataFrame] = {}
    for k in range(n_symbols):
        sigma = rng.uniform(0.012, 0.035)
        mu = 0.0007 if k < trend_symbols else rng.normal(0.0001, 0.0002)
        r = rng.normal(mu, sigma, len(dates))
        c = rng.uniform(15, 120) * np.exp(np.cumsum(r))
        gap = rng.normal(0, sigma * 0.3, len(dates))
        o = np.r_[c[0], c[:-1]] * np.exp(gap)
        hi = np.maximum(o, c) * (1 + np.abs(rng.normal(0, sigma * 0.4, len(dates))))
        lo = np.minimum(o, c) * (1 - np.abs(rng.normal(0, sigma * 0.4, len(dates))))
        vol = rng.lognormal(np.log(2_000_000), 0.4, len(dates)).astype(np.int64)
        out[f"S{k:03d}"] = pd.DataFrame({"date": dates, "open": o, "high": hi, "low": lo, "close": c, "volume": vol})
    return out


def make_sector_snaps(symbols, start: str = "2023-07-11", end: str = "2026-08-31", n_sectors: int = 4, seed: int = 2) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    names = [f"Sector{i}" for i in range(n_sectors)]
    sec = {s: names[rng.integers(0, n_sectors)] for s in symbols}
    dates = pd.bdate_range(start, end)
    return pd.DataFrame([(s, d, sec[s]) for s in symbols for d in dates], columns=["symbol", "date", "sector"])


def planted_dataset(n: int = 40000, seed: int = 3, effect: float = 1.0, date_confound: float = 0.0, treated_share: float = 0.25) -> pd.DataFrame:
    """A harness-shaped frame (only the columns the hypothesis machinery reads) with a binary feature `rs60_pct`/`rs120_pct` (treated = high) whose BW5 odds are multiplied by `effect`, optionally confounded by a
    market-day effect: `date_confound` adds a per-month shift that raises BOTH the treated share and the outcome rate on good months (a pure market-time effect with no stock-level effect when effect == 1)."""
    rng = np.random.default_rng(seed)
    months = pd.period_range("2018-01", "2025-12", freq="M")
    month = rng.integers(0, len(months), n)
    good = rng.normal(0, 1, len(months))
    dates_in_m = 20
    date_index = month * 21 + rng.integers(0, dates_in_m, n)
    p_treat = np.clip(treated_share + date_confound * 0.15 * good[month], 0.02, 0.9)
    treated = rng.random(n) < p_treat
    base = 0.26 * np.exp(date_confound * 0.5 * good[month])
    odds = base / (1 - base) * np.where(treated, effect, 1.0)
    p = odds / (1 + odds)
    y = (rng.random(n) < p).astype(float)
    atr_pct = rng.lognormal(np.log(3), 0.4, n)
    dv = rng.lognormal(np.log(2e7), 1.0, n)
    ret60 = rng.normal(8, 20, n)
    sym = rng.integers(0, 2500, n)
    sig_date = months[month].to_timestamp() + pd.to_timedelta(rng.integers(0, 27, n), unit="D")
    df = pd.DataFrame({
        "symbol": [f"X{s:04d}" for s in sym], "signal_date": sig_date, "direction": 1, "bar_index": date_index, "date_index": date_index, "month": months[month].astype(str), "year": months[month].year,
        "rs60_pct": np.where(treated, rng.uniform(0.85, 1.0, n), rng.uniform(0.0, 0.55, n)), "rs120_pct": np.where(treated, rng.uniform(0.85, 1.0, n), rng.uniform(0.0, 0.55, n)),
        "atr_pct": atr_pct, "dollar_vol_20": dv, "ret_60d": ret60, "ret_20d": rng.normal(3, 10, n), "dist_sma200_pct": rng.normal(10, 20, n), "log_dollar_vol_20": np.log10(dv),
        "index_above_200": 1.0, "breadth_50": rng.uniform(0, 1, n), "pct_from_52w_high": rng.uniform(-40, 0, n), "pct_from_52w_low": rng.uniform(5, 150, n), "chan_squeeze_pctile": rng.uniform(0, 100, n),
        "vol_ratio_50": rng.lognormal(0, 0.5, n), "sector_rs60_rank": rng.integers(1, 12, n).astype(float), "group_breakouts_10": rng.integers(0, 6, n).astype(float),
        "sector": "Tech", "sector_provenance": "OBSERVED_PIT",
        "ge5r_120": y, "ge10r_120": (y * (rng.random(n) < 0.2)).astype(float), "ret_label_120": (y * (rng.random(n) < 0.6)).astype(float), "vn_label_120": (y * (rng.random(n) < 0.7)).astype(float),
        "mfe_r_120": np.where(y > 0, rng.uniform(5, 12, n), rng.uniform(0, 4.9, n)), "status_120": "ok", "eligible": True, "is_episode_first": True,
    })
    seg = np.where(df["signal_date"] <= "2021-06-30", "train", np.where((df["signal_date"] >= "2022-01-01") & (df["signal_date"] <= "2023-06-30"), "validation", np.where(df["signal_date"] >= "2024-01-01", "test", "embargo")))
    df["segment"] = seg
    return df
