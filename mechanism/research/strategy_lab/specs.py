"""Versioned strategy definitions. A definition is a canonical JSON document; its sha256 is the strategy's identity. A changed definition is a NEW version
(never an edit), exactly like `research.strategy_variants`. Everything a result depends on is declared here BEFORE any result is looked at: thresholds, exit, hold
caps, costs, the chronological split and the decision rule. Nothing is fitted to the data.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, Mapping

from research.lab.manifest import canonical_hash

SPEC_SCHEMA = "strategy_spec_v1"

# ------------------------------------------------------------------------------------------------------------------------------------------------------------ earnings_drift v1
# Post-earnings-announcement drift: a company reports a clear beat/miss, the market reacts the same way, and the move keeps going for a few weeks.
# The whole definition is a priori (the literature's shape, round thresholds); the only freedom taken is none: no parameter is tuned on this data.
EARNINGS_DRIFT_V1: Dict[str, Any] = {
    "schema": SPEC_SCHEMA,
    "key": "earnings_drift",
    "version": "v1",
    "hypothesis": "After an earnings report whose surprise and first price reaction agree, the stock keeps drifting in that direction over the following weeks by more than costs.",
    "event": {
        "source": "earnings_calendar rows with a reported EPS and an estimate (yfinance; research grade C: not point-in-time, report time of day unknown)",
        "min_abs_surprise_pct": 10.0,
        "reaction": {
            "window": "close of the last session before the report date -> close of the first session after the report session (the report session is the first session on or after the report date)",
            "min_abs_return_pct": 3.0,
            "max_abs_return_pct": 40.0,                  # larger moves are splits or deal prices, not drift candidates (counted and reported)
            "must_agree_with_surprise_sign": True,
        },
        "report_session_max_gap_days": 4,                # the stock must trade within 4 calendar days of the report date, otherwise the event is skipped
    },
    "liquidity_at_signal": {"min_price": 5.0, "min_avg_dollar_volume_20_sessions_before_report": 5_000_000.0},
    "direction": "sign of the surprise: positive -> long, negative -> short",
    "signal": "close of the reaction window's last session; the position is entered at the OPEN of the next session (the same convention as every other strategy here)",
    "exit": {"policy": "A_atr_stop_fixed_3R", "initial_risk": "2 x ATR14 at the signal bar (1R)", "target": "3R", "primary_max_hold_bars": 20, "sensitivity_max_hold_bars": 60},
    "data_guard": {"skip_if_any_daily_close_change_exceeds_pct": 45.0,
                   "window": "the 25 sessions before the report session (information known at the signal; nothing after the signal is used to include or exclude a trade)"},
    "position_rule": "at most one open position per (symbol, direction)",
    "costs_bps_round_trip": {"primary": 20.0, "sensitivity": [10.0, 20.0, 40.0]},
    "controls": {
        "all_reports": "every report with a reported EPS and estimate that meets the liquidity rule, direction = sign of the surprise, no surprise or reaction threshold (what the thresholds add)",
        "random_date": "for each signal a random session of the same symbol (not within 15 sessions of any report), same direction, same entry convention and exit",
    },
    "split": {"basis": "signal date", "train_start": "2021-01-01", "train_end_exclusive": "2024-01-01", "test_start_inclusive": "2024-02-01",
              "purge": "a train signal whose position is still open at test_start is dropped from train"},
    "decision_rule": {
        "primary_cell": "test period, both directions pooled, max_hold 20, 20 bps round trip, standardised R",
        "pass_for_forward_shadow_requires_all": [
            "n_test >= 150 positions",
            "net expectancy > 0 with the month-block bootstrap 95% interval entirely above 0",
            "expectancy minus the random-date control > 0 with its 95% interval entirely above 0",
            "net expectancy on the TRAIN period > 0 (same sign)",
            "the result is not carried by a handful of trades: expectancy without the top 1% of winners is still > 0",
        ],
        "otherwise": "NO_EDGE_DEMONSTRATED (or INSUFFICIENT_DATA when n_test < 150). PASS only authorises a forward SHADOW run; it is never a trading claim.",
        "multiple_testing": "1 strategy x 2 directions x 2 hold caps x 3 costs were looked at; only the primary cell decides, the rest is reported as context.",
    },
}



def child_spec(parent: Mapping[str, Any], *, key: str, version: str, hypothesis: str, extra: Any, derived_from: Any = None, excluded_inputs: Any = None) -> Dict[str, Any]:
    """A refinement of `parent`: the same event, liquidity, exit, split, costs and controls, plus extra conditions on signal-time features. The parent's own signals are a
    control (the added conditions must beat them out of sample). The definition is complete and declared before any result of the child exists; `derived_from` records how the
    conditions were chosen (a train-only analysis, or the owner's a-priori hypothesis)."""
    c = copy.deepcopy(dict(parent))
    c["key"], c["version"], c["hypothesis"] = key, version, hypothesis
    c["parent"] = {"key": parent["key"], "version": parent["version"], "spec_hash": canonical_hash(parent)}
    c["event"]["extra"] = [dict(x) for x in extra]
    if derived_from is not None:
        c["derived_from"] = derived_from
    if excluded_inputs is not None:
        c["excluded_inputs"] = excluded_inputs
    rule = c["decision_rule"]
    rule["pass_for_forward_shadow_requires_all"] = list(rule["pass_for_forward_shadow_requires_all"]) + [
        "signal minus its parent strategy (the added conditions must improve on it) > 0 with its 95% interval entirely above 0"]
    rule["multiple_testing"] = ("Registered with the thresholds fixed beforehand and run ONCE on the test period; the test period played no part in choosing them. "
                                + rule["multiple_testing"])
    return c


# The owner's a-priori hypothesis (2026-10-31): the drift is real only when the surprise comes with unusual participation and a range expansion. The thresholds are round numbers
# fixed beforehand, not tuned. Short interest was requested too: there is no point-in-time source for it (yfinance exposes only today's value), so it is NOT used and nothing stands in for it.
EARNINGS_CONFLUENCE_V1 = child_spec(
    EARNINGS_DRIFT_V1, key="earnings_confluence", version="v1",
    hypothesis="The drift after an earnings surprise exists only when the reaction comes with unusual participation (volume at least twice normal) and a range expansion "
               "(true range at least 1.5 times the ATR known before the report); weaker reactions are noise.",
    extra=[{"feature": "relvol_event", "op": ">=", "value": 2.0}, {"feature": "range_expansion", "op": ">=", "value": 1.5}],
    derived_from={"kind": "owner hypothesis, a priori", "note": "thresholds fixed before any result of this strategy existed"},
    excluded_inputs={"short_interest": "no point-in-time source: yfinance exposes only the current value, which would leak the future into every historical event"})

SPECS: Mapping[str, Mapping[str, Mapping[str, Any]]] = {"earnings_drift": {"v1": EARNINGS_DRIFT_V1}, "earnings_confluence": {"v1": EARNINGS_CONFLUENCE_V1}}


def _register_study_specs() -> None:
    try:
        from research.strategy_lab.inflection_study import SPEC as _S
    except Exception:  # noqa: BLE001 - the inflection package may be absent in a minimal install
        return
    SPECS = globals()["SPECS"]
    SPECS = dict(SPECS)
    SPECS["fundamental_inflection"] = {"v1": _S}
    globals()["SPECS"] = SPECS



class SpecError(ValueError):
    pass


def get_spec(key: str, version: str) -> Dict[str, Any]:
    if (key, version) == ("fundamental_inflection", "v1"):                  # the event study's spec lives with the study (lazy import: it imports this module)
        from research.strategy_lab.inflection_study import SPEC
        return copy.deepcopy(dict(SPEC))
    if (key, version) == ("fundamental_inflection_pit", "v1"):
        from research.strategy_lab.inflection_study import pit_spec
        return pit_spec()
    try:
        return copy.deepcopy(dict(SPECS[key][version]))
    except KeyError as e:
        raise SpecError(f"unknown strategy {key} {version}") from e


def spec_hash(definition: Mapping[str, Any]) -> str:
    return canonical_hash(definition)
