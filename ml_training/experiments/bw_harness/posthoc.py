"""POST-HOC SENSITIVITY. NOT PRE-REGISTERED. NO VERDICT IS DERIVED FROM ANYTHING IN THIS MODULE.

The first full run of the pre-registered design showed that, as specified, several estimators are degenerate rather than informative:
  * matching on the 60-day-return tercile makes H1 (relative-strength leadership) unmatchable, because relative strength over 60 bars is almost the same quantity as the 60-day return (30 and 34 matched sets);
  * the group criterion of H5 ("at least 3 other same-sector breakouts in the previous 10 sessions") is true for essentially every signal at this universe size, so H5 has no controls;
  * H2 is a property of the DATE (every signal of a day shares it), so a within-date estimate does not exist for it.
This module reports, side by side and clearly labelled, one generic alternative matching (ATR decile + dollar-volume tercile + +-5 sessions, WITHOUT the 60-day-return tercile) and the sector-rank-only version of H5,
so the reader can see what the data say when the design is not degenerate. These are new analyses made after seeing the pre-registered results: they carry multiplicity (two designs), cannot upgrade or change any verdict,
and are hypothesis-generating at most. A pre-registration v2 would be needed to make any of them confirmatory.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Dict

import numpy as np
import pandas as pd

from . import hypotheses as H
from . import inference as I
from .config import Config
from .splits import TEST, VALIDATION

LABEL = "POST-HOC SENSITIVITY: NOT PRE-REGISTERED; no verdict is derived from it; hypothesis-generating at most; two matching designs mean multiplicity."


def h5r_t(d, t, c): return H._f(d, "sector_rs60_rank") <= c.sector_top_n
def h5r_c(d, t, c): return np.isfinite(H._f(d, "sector_rs60_rank")) & ~h5r_t(d, t, c)


H5_RANK_ONLY = H.Spec("H5_rank_only", "POST-HOC: sector 60-bar relative-strength rank <= 3 of 11 vs > 3 (the group-breakout criterion removed because it is saturated)", h5r_t, h5r_c, observed_sector_only=True)


def run(df: pd.DataFrame, thr: Dict[str, Any], cfg: Config) -> Dict[str, Any]:
    cfg2 = replace(cfg, match_on_ret60=False)
    specs = H.LONG_SPECS + [H5_RANK_ONLY]
    out: Dict[str, Any] = {"label": LABEL, "pre_registered": False, "config_hash": cfg2.hash(), "deviation_from_pre_registration": "matching without the 60-day-return tercile; H5 reported also without its saturated group criterion",
                           "long": {}}
    for si, spec in enumerate(specs):
        res: Dict[str, Any] = {}
        for seg in (VALIDATION, TEST):
            pool = H._pool(df, 1, (seg,), cfg2, observed_only=spec.observed_sector_only)
            res[seg] = H.evaluate(pool, spec, thr["long"], cfg2, seed=cfg.seed + 20000 + 10 * si + (0 if seg == VALIDATION else 1))
        if spec.observed_sector_only:
            for seg in (VALIDATION, TEST):
                pool = H._pool(df, 1, (seg,), cfg2, observed_only=False)
                pool = pool[pool["sector_provenance"] == "RECONSTRUCTED"].reset_index(drop=True)
                res[f"{seg}_reconstructed_replication"] = H.evaluate(pool, spec, thr["long"], cfg2, seed=cfg.seed + 25000 + 10 * si + (0 if seg == VALIDATION else 1), realised=False)
        out["long"][spec.id] = res
    ids = [s.id for s in specs]
    for seg in (VALIDATION, TEST):
        p = {i: H._primary_p(out["long"][i][seg]) for i in ids}
        h = I.holm(p, cfg.alpha)
        for i in ids:
            out["long"][i][seg]["holm_reference_only"] = h[i]
    return out
