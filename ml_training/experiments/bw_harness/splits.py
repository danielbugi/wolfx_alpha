"""Chronological segments by SIGNAL date, with embargo and purge.

  train       signals 2018-01-01 .. 2021-06-30
  embargo     2021-07-01 .. 2021-12-31   (a 120-bar label window started here reaches into validation: dropped)
  validation  2022-01-01 .. 2023-06-30
  embargo     2023-07-01 .. 2023-12-31   (same reason, towards test)
  test        2024-01-01 .. the latest signal whose 120-bar window is complete

`purge_check` proves the property instead of assuming it: for every labelled signal the date of the last bar of its primary-horizon window (`window_end_date`) is before the start of the next segment."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import Config

TRAIN, VALIDATION, TEST, EMBARGO, OUTSIDE = "train", "validation", "test", "embargo", "outside"


def assign_segment(dates, cfg: Config) -> np.ndarray:
    d = pd.to_datetime(pd.Series(dates)).to_numpy()
    t0, t1, v0, v1, s0 = (np.datetime64(x) for x in (cfg.train_start, cfg.train_end, cfg.validation_start, cfg.validation_end, cfg.test_start))
    seg = np.full(len(d), OUTSIDE, dtype=object)
    seg[(d >= t0) & (d <= t1)] = TRAIN
    seg[(d > t1) & (d < v0)] = EMBARGO
    seg[(d >= v0) & (d <= v1)] = VALIDATION
    seg[(d > v1) & (d < s0)] = EMBARGO
    seg[d >= s0] = TEST
    return seg


def purge_check(df: pd.DataFrame, cfg: Config) -> dict:
    """Every labelled train signal's window ends before validation starts; every labelled validation signal's window ends before test starts."""
    lab = df[df[f"status_{cfg.primary_horizon}"] == "ok"]
    we = pd.to_datetime(lab["window_end_date"])
    tr = lab[lab["segment"] == TRAIN]
    va = lab[lab["segment"] == VALIDATION]
    bad_tr = int((we[tr.index] >= pd.Timestamp(cfg.validation_start)).sum())
    bad_va = int((we[va.index] >= pd.Timestamp(cfg.test_start)).sum())
    return {"labelled_train": int(len(tr)), "labelled_validation": int(len(va)), "train_windows_reaching_validation": bad_tr, "validation_windows_reaching_test": bad_va,
            "ok": bad_tr == 0 and bad_va == 0}
