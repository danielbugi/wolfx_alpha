"""Survivorship-bias sensitivity.

The price table contains only companies that were still trading in 2025 or later (no company with a last bar before 2025; `companies` is empty), so the historical population is today's list projected back. The
missing companies cannot be recovered from inside the data, so the question asked here is a different one: HOW MUCH missing-company mass, and how unevenly spread between the treated and the control group, would it take
to remove an observed effect?

Model (explicit, deliberately simple). A fraction phi_g of group g's TRUE population is missing from the data; the missing signals have a Big Winner rate pm (longs: delisted names rarely reach +5R, pm in {0, 5%};
shorts: delisted names often collapse, pm in {0, 50%}). The true group rate is p'_g = (1 - phi_g) p_g + phi_g pm, and RR' = p'_t / p'_c.
  * equal phi in both groups with pm = 0 leaves RR unchanged (both rates scale by the same factor): only UNEVEN missing mass can change a ratio;
  * the tipping point reported per effect is the treated-group missing share phi_t (controls complete) that brings RR' to 1.0 and to the support threshold, i.e. what a hidden pool of failed treated-type names would have to look like.
Nothing here corrects a result; it brackets it."""
from __future__ import annotations

from typing import Any, Dict, Optional

from .config import Config


def adjusted_rr(p_t: float, p_c: float, phi_t: float, phi_c: float, pm: float) -> Optional[float]:
    pt = (1 - phi_t) * p_t + phi_t * pm
    pc = (1 - phi_c) * p_c + phi_c * pm
    return pt / pc if pc > 0 else None


def tipping_phi_treated(p_t: float, p_c: float, target: float, pm: float) -> Optional[float]:
    """Treated-group missing share needed for RR' = target (controls complete). None when no value in [0, 1] does it; 0.0 when the observed ratio is already at or below the target."""
    if p_c <= 0:
        return None
    if p_t / p_c <= target:
        return 0.0
    if p_t <= pm:
        return None
    phi = (p_t - target * p_c) / (p_t - pm)
    return float(phi) if 0.0 <= phi <= 1.0 else None


def sensitivity(p_t: float, p_c: float, cfg: Config, side: str) -> Dict[str, Any]:
    pms = cfg.surv_missing_winner_rate_long if side == "long" else cfg.surv_missing_winner_rate_short
    grid = []
    for pm in pms:
        for phi_t in cfg.surv_phi_treated:
            for delta in cfg.surv_delta:
                rr = adjusted_rr(p_t, p_c, phi_t, phi_t + delta, pm)
                grid.append({"missing_winner_rate": pm, "phi_treated": phi_t, "phi_control_extra": delta, "rr": None if rr is None else round(rr, 3)})
    tipping = {f"pm={pm}": {"phi_treated_to_reach_rr_1.0": tipping_phi_treated(p_t, p_c, 1.0, pm), f"phi_treated_to_reach_rr_{cfg.min_rr}": tipping_phi_treated(p_t, p_c, cfg.min_rr, pm)} for pm in pms}
    return {"observed_rr": round(p_t / p_c, 4) if p_c > 0 else None, "grid": grid, "tipping": tipping,
            "equal_missing_share_changes_rr": False, "note": "with pm = 0 and the same missing share in both groups the ratio is unchanged; only uneven missing mass matters"}


def analyse(results: Dict[str, Any], cfg: Config) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for side in ("long", "short"):
        for hid, res in results[side].items():
            for seg in ("validation", "test"):
                lab = ((res.get(seg) or {}).get("labels") or {}).get("BW5@120 (primary, R-based)")
                if not lab or lab.get("treated_rate") is None or not lab.get("control_rate"):
                    continue
                out[f"{side}/{hid}/{seg}"] = sensitivity(lab["treated_rate"], lab["control_rate"], cfg, side)
    return out


def empirical_diagnostics(df) -> Dict[str, Any]:
    """What the data itself can say about survivorship: how the population grows (listings only), the rates by listing cohort, and the signals of the symbols that already stopped trading (the only 'dead' names present)."""
    import numpy as np
    ok = df[(df["status_120"] == "ok") & df["eligible"]]
    by_cohort = {}
    for (c, d), g in ok.groupby(["cohort", "direction"]):
        by_cohort[f"{c}_{'long' if d == 1 else 'short'}"] = {"signals": int(len(g)), "bw5_rate": round(float(g["ge5r_120"].mean()), 4)}
    per_year = df.groupby("year")["symbol"].nunique()
    return {"symbols_with_signals_per_year": {int(k): int(v) for k, v in per_year.items()}, "bw5_rate_by_listing_cohort": by_cohort,
            "note": "the 'listed_later' cohort is the only visible difference in the data; companies that stopped trading before 2025 are absent entirely and cannot be examined"}
