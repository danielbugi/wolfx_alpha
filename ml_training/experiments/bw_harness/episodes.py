"""Independent episodes. Breakouts cluster: 67% of signals follow the previous signal of the same symbol and direction within 10 bars, and a 120-bar label window overlaps its neighbours almost completely, so a signal is not an independent observation.

Rule (fixed): per (symbol, direction), in bar order, a signal is KEPT when it is at least `gap` bars after the previous KEPT signal (or is the first); a kept signal starts a new episode. Every other signal belongs to the episode of the most recent kept
signal before it. Inference uses one signal per episode (the kept one: `is_episode_first`); the rest are descriptive. Two kept signals of one (symbol, direction) are always `gap` or more bars apart, so their 120-bar windows never overlap."""
from __future__ import annotations

from typing import Tuple

import numpy as np
import pandas as pd


def assign_episodes(symbol: np.ndarray, direction: np.ndarray, bar_index: np.ndarray, gap: int = 120) -> Tuple[np.ndarray, np.ndarray]:
    """(episode_id, is_episode_first) aligned with the inputs (any order). Episode ids are 0..K-1 in (symbol, direction, bar) order, so they are deterministic."""
    sym = np.asarray(symbol)
    dr = np.asarray(direction)
    bi = np.asarray(bar_index)
    order = np.lexsort((bi, dr, sym))
    ep = np.empty(len(sym), dtype=np.int64)
    first = np.zeros(len(sym), dtype=bool)
    cur = -1
    last_sym = last_dir = None
    last_kept = 0
    for pos in order:
        s, d, b = sym[pos], dr[pos], bi[pos]
        if s != last_sym or d != last_dir or b - last_kept < 0 or (b - last_kept) >= gap:
            cur += 1
            first[pos] = True
            last_kept = b
            last_sym, last_dir = s, d
        ep[pos] = cur
    return ep, first


def episode_summary(df: pd.DataFrame) -> dict:
    """Counts used in the data-quality report."""
    kept = df[df["is_episode_first"]]
    return {"signals": int(len(df)), "episodes": int(kept["episode_id"].nunique()), "signals_per_episode_mean": round(float(len(df) / max(1, kept["episode_id"].nunique())), 2),
            "share_signals_that_start_an_episode": round(float(df["is_episode_first"].mean()), 4)}
