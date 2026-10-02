"""The ONE normalised Market Intelligence payload, shared by the dashboard API and the Telegram renderer.

Pure: it reshapes rows returned by `store` (a market_snapshot row, its sector_snapshot rows) and recomputes nothing. A missing value stays
None -- never 0, never a carried-forward number, never an invented classification. An absent snapshot is `unavailable(reason)`.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

from market_intelligence import risk_regime as RR

SCHEMA = "mi_payload_v1"
HORIZONS = (5, 20, 60)
REGIME_DISCLAIMER = ("V1 heuristic: fixed weights and thresholds that were declared, not fitted to outcomes. "
                     "Descriptive context only; not validated as predictive and not a screening or eligibility rule.")
RECONSTRUCTED_NOTE = ("Reconstructed after the fact from stored history. It is not a record of what was known on that session, and its "
                      "sector tags are today's metadata projected backwards.")


def unavailable(reason: str, message: Optional[str] = None) -> Dict[str, Any]:
    return {"schema": SCHEMA, "available": False, "reason": reason, "message": message}


def _hz(row: Mapping[str, Any], pattern: str) -> Dict[str, Any]:
    return {str(h): row.get(pattern.format(h=h)) for h in HORIZONS}


def _components(raw: Optional[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """In the model's own component order; a component the row does not carry is reported as missing, not skipped."""
    raw = raw or {}
    out = []
    for key, label, weight in RR.COMPONENTS:
        c = raw.get(key) or {}
        out.append({"key": key, "label": label, "weight": weight, "present": bool(c.get("present", False)), "raw": c.get("raw") or {},
                    "value": c.get("value"), "score": c.get("score"), "missing_reason": c.get("missing_reason") or ("not stored" if not c else None)})
    return out


def _sector(row: Mapping[str, Any]) -> Dict[str, Any]:
    horizons = {}
    for h in HORIZONS:
        horizons[str(h)] = {"n_valid": row.get(f"n_valid_{h}"), "n_excluded": row.get(f"n_excluded_{h}"), "ret": row.get(f"sec_ret_{h}"),
                            "vs_spx": row.get(f"sec_vs_spx_{h}"), "vs_univ": row.get(f"sec_vs_univ_{h}")}
    return {"sector": row["sector"], "n_members": row["n_members"], "rank_20": row.get("rank_20"), "horizons": horizons}


def build(market: Optional[Mapping[str, Any]], sectors: Sequence[Mapping[str, Any]] = ()) -> Dict[str, Any]:
    """`market`: a store.get_market_snapshot row (None -> unavailable). `sectors`: store.get_sector_snapshots rows for the same session
    and provenance."""
    if not market:
        return unavailable("no_snapshot", "No market snapshot has been captured for this session.")
    prov = market["provenance"]
    state = market["regime_state"]
    mine = [s for s in sectors if s.get("provenance") == prov and str(s.get("session_date")) == str(market["session_date"])]
    return {
        "schema": SCHEMA, "available": True,
        "session_date": str(market["session_date"]),
        "provenance": prov,
        "observed": prov == "observed",
        "provenance_note": None if prov == "observed" else RECONSTRUCTED_NOTE,
        "regime": {
            "model_version": market["regime_model_version"], "state": state, "available": state != RR.UNAVAILABLE,
            "score": market.get("regime_score"), "strength": market.get("regime_strength"),
            "strength_label": market.get("regime_strength_label"), "agreement": market.get("regime_agreement"),
            "present_weight": market.get("regime_present_weight"), "min_present_weight": RR.MIN_PRESENT_WEIGHT,
            "thresholds": {"risk_on": RR.THRESHOLD, "risk_off": -RR.THRESHOLD},
            "components": _components(market.get("regime_components")), "reasons": list(market.get("regime_reasons") or []),
            "is_heuristic": True, "disclaimer": REGIME_DISCLAIMER,
        },
        "returns": {
            "spx": _hz(market, "spx_ret_{h}"),
            "universe_median": {str(h): {"ret": market.get(f"univ_ret_{h}"), "n_valid": market.get(f"univ_n_valid_{h}")} for h in HORIZONS},
            "unit": "percent", "universe_definition": "equal-weight median of all valid stock returns; S&P 500 is cap-weighted",
        },
        "sectors": sorted((_sector(s) for s in mine), key=lambda s: (s["rank_20"] is None, s["rank_20"] or 0, s["sector"])),
        "sector_map": {"source": market.get("sector_source"), "asof_rule": market.get("sector_asof_rule"),
                       "pit_safe": bool(market.get("sector_pit_safe")), "n_symbols": market.get("n_symbols"),
                       "n_classified": market.get("n_classified")},
        "coverage": market.get("coverage") or {},
        "meta": {"feature_set_version": market.get("feature_set_version"), "regime_model_version": market["regime_model_version"],
                 "rs_model_version": market.get("rs_model_version"), "source": market.get("source"), "captured_at": str(market.get("captured_at")),
                 "content_hash": market.get("content_hash"), "code_ref": market.get("code_ref")},
    }


def definitions() -> Dict[str, Any]:
    """Static description of the models (what the dashboard's explainer shows). No database."""
    from market_intelligence import relative_strength as RS
    return {
        "schema": SCHEMA,
        "risk_regime": {
            "model_version": RR.MODEL_VERSION, "is_heuristic": True, "disclaimer": REGIME_DISCLAIMER,
            "components": [{"key": k, "label": label, "weight": w} for k, label, w in RR.COMPONENTS],
            "risk_on_at_or_above": RR.THRESHOLD, "risk_off_at_or_below": -RR.THRESHOLD,
            "min_present_weight": RR.MIN_PRESENT_WEIGHT, "min_breadth_stocks": RR.MIN_BREADTH_STOCKS,
            "states": list(RR.STATES),
        },
        "relative_strength": {
            "model_version": RS.MODEL_VERSION, "horizons": list(RS.HORIZONS), "rank_horizon": RS.RANK_HORIZON,
            "min_sector_members": RS.MIN_SECTOR_MEMBERS, "min_universe": RS.MIN_UNIVERSE,
            "sector_return": "median of valid member returns (no liquidity filter in V1)",
            "comparisons": ["sector vs S&P 500 (cap-weighted)", "sector vs universe median (like-for-like)"],
        },
        "provenance": {"observed": "captured by the pipeline for that session", "reconstructed": RECONSTRUCTED_NOTE,
                       "default_reads": "observed only; reconstructed rows need an explicit opt-in"},
    }
