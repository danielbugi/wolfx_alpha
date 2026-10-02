"""Deterministic synthetic inputs shared by the Market Intelligence tests (no database, no network)."""
import os
import sys
from datetime import date

import numpy as np
import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))
sys.path.insert(0, ROOT)

from market_intelligence import relative_strength as RS  # noqa: E402
from market_intelligence import risk_regime as RR  # noqa: E402

T = date(2026, 9, 30)
N_DAYS = 80
SECTORS = ("Technology", "Energy", "Healthcare")


def panel(n_stocks=1200, seed=1):
    rng = np.random.default_rng(seed)
    g = rng.normal(0.001, 0.004, n_stocks)
    idx = pd.bdate_range(end=pd.Timestamp(T), periods=N_DAYS)
    syms = [f"S{i:04d}" for i in range(n_stocks)]
    close = pd.DataFrame({s: 100.0 * (1.0 + gi) ** np.arange(N_DAYS) for s, gi in zip(syms, g)}, index=idx)
    sector_of = {s: SECTORS[i % len(SECTORS)] for i, s in enumerate(syms)}
    spx = pd.Series(4000 * (1 + 0.0005) ** np.arange(N_DAYS), index=idx)
    return close, sector_of, {"^GSPC": spx}


def relative_strength(provenance="observed", n_stocks=1200):
    close, sector_of, idx = panel(n_stocks)
    return RS.compute(T, close, sector_of, idx, {}, sector_provenance=provenance)


def regime(scores=None):
    scores = {k: 0.5 for k in RR.WEIGHTS} if scores is None else scores
    return RR.from_scores(T, scores)
