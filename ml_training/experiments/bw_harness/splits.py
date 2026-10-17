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

TRAIN, VALIDATION, TEST, EMBARGO, OUTSIDE, PURGED = "train", "validation", "test", "embargo", "outside", "purged"


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


def apply_purge(df: pd.DataFrame, cfg: Config) -> np.ndarray:
    """Segment labels after the purge: a train signal whose primary-horizon window ends on or after the validation start, and a validation signal whose window ends on or after the test start, become 'purged'.
    The embargo months make this rare, but a symbol with missing bars stretches 120 bars over more calendar days (found by the first full run: 20 train and 5 validation signals), so the rule is enforced on the
    actual window end date, not assumed from the signal date."""
    seg = df["segment"].to_numpy(dtype=object).copy()
    we = pd.to_datetime(df["window_end_date"]).to_numpy()
    v0, s0 = np.datetime64(cfg.validation_start), np.datetime64(cfg.test_start)
    has = ~pd.isna(we)
    seg[(seg == TRAIN) & has & (we >= v0)] = PURGED
    seg[(seg == VALIDATION) & has & (we >= s0)] = PURGED
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
            "purged_signals": int((df["segment"] == PURGED).sum()), "ok": bad_tr == 0 and bad_va == 0}
