# mechanism/strategy_analytics/definitions.py
"""
The canonical Strategy Intelligence vocabulary: what "signal", "open", "held", "resolved", "winner",
"R", "win rate" and "holding period" mean for every consumer of signal_ledger -- the dashboard API,
the private Telegram assistant, any public track record. analytics.py computes every number from
these constants; nothing else in the repo should re-derive a win rate or an R average.

Semantics are taken from the writer and evaluator as shipped, not re-imagined here:
  mechanism/screeners/signal_ledger_writer.py     (what becomes a row)
  mechanism/screeners/evaluate_signal_ledger.py   (how a row resolves, flags, holds)
  mechanism/add_signal_ledger_tables.sql, add_strategy_identity_release_a.sql,
  add_signal_ledger_eval_flags.sql                (schema, statuses, flag vocabularies)

This module is pure (no I/O, no DB, no pool) so it is importable from the backend image and the
mechanism image alike.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

DEFINITIONS_VERSION = "2026-10-04.1"

# Legacy placeholder the screener once wrote to signal_ledger.model_version when no model scored the signal.
# Current semantics: model_version is NULL unless a validated model scored the signal (shared/model_provenance.py).
# Historical rows are not rewritten, so read-side consumers must treat this value as "not scored", never as a model.
LEGACY_UNSCORED_MODEL_VERSIONS = ("unknown",)

# signal_ledger.status vocabulary (CHECK constraint, migration 19).
STATUS_OPEN = "open"
TARGET_STATUSES = ("target1", "target2", "target3")
TERMINAL_STATUSES = ("stopped",) + TARGET_STATUSES + ("expired",)
ALL_STATUSES = (STATUS_OPEN,) + TERMINAL_STATUSES

# Flag vocabularies (CHECK constraints, migration 21).
RESOLUTION_FLAGS = ("same_bar_stop_and_target",)
EVALUATION_FLAGS = ("split_suspect",)

DIRECTIONS = {1: "bullish", -1: "bearish"}
DIRECTION_VALUES = {v: k for k, v in DIRECTIONS.items()}

# Lifecycle = the one derived state every consumer groups by.
LIFECYCLE_OPEN = "open"          # status='open' AND evaluation_flag IS NULL -- progressing normally
LIFECYCLE_HELD = "held"          # status='open' AND evaluation_flag IS NOT NULL -- evaluation paused
LIFECYCLE_RESOLVED = "resolved"  # status in TERMINAL_STATUSES
LIFECYCLES = (LIFECYCLE_OPEN, LIFECYCLE_HELD, LIFECYCLE_RESOLVED)

# Sample-size discipline. Below this many observations a rate/average is shown only as
# "preliminary"; surfaces that must not show preliminary numbers (the track record) suppress it.
MIN_SAMPLE_SIZE = 5

# An open signal whose symbol is this many market sessions behind the latest landed session is
# "stale" (possible delisting / feed gap), not merely "lagging".
STALE_AFTER_SESSIONS = 3
# How many recent market sessions data health inspects when measuring lag; a lag beyond this is
# reported as ">= this" (capped) rather than scanning the whole price history.
SESSION_WINDOW = 30

# Metric states -- every rate/average carries one, so no consumer has to infer validity.
STATE_OK = "ok"                       # n >= MIN_SAMPLE_SIZE
STATE_PRELIMINARY = "preliminary"     # 0 < n < MIN_SAMPLE_SIZE: value shown, flagged as preliminary
STATE_NO_DATA = "no_data"             # n == 0: nothing to compute from yet (value is null)
STATE_NOT_AVAILABLE = "not_available"  # the stored data cannot support this metric at all

DEFINITIONS: Dict[str, str] = {
    "signal": "One row in signal_ledger: a bullish or bearish Donchian breakout the live screener "
              "produced on signal_date (near-breakouts are never recorded). One strategy/version, "
              "symbol and direction can have at most one signal per session and at most one open "
              "signal at a time (both enforced by the database).",
    "open": "status='open' and evaluation_flag IS NULL: the signal is progressing normally and the "
            "evaluator re-checks it every session.",
    "held": "status='open' and evaluation_flag IS NOT NULL (today only 'split_suspect'): the "
            "evaluator found a split-shaped price step on the signal's path and stopped evaluating it "
            "until a person reviews and clears the flag. Still open -- never counted as resolved -- and "
            "it keeps occupying its open-position slot.",
    "resolved": "status in (stopped, target1, target2, target3, expired): the signal reached a "
                "terminal outcome and is never re-evaluated.",
    "winner": "A resolved signal whose status is target1, target2 or target3: price touched at least "
              "the first profit target (entry +/- 2 x ATR) before touching the stop. An expired "
              "signal is never a winner, even with a positive mark-to-market R.",
    "stopped": "status='stopped': the stop (entry -/+ 2 x ATR = 1R) was touched before any target, "
               "or on the same daily bar as a target (see ambiguous). outcome_r = -1.",
    "expired": "status='expired': 20 forward trading bars passed with neither stop nor target touched; "
               "closed at the 20th bar's close (mark-to-market).",
    "ambiguous": "A resolved signal with resolution_flag='same_bar_stop_and_target': one daily bar "
                 "touched both the stop and a target, so the true order is unknowable from daily OHLC. "
                 "It is recorded conservatively as stopped (-1R) and counted as a loss in every "
                 "rate; the flag lets research include, exclude or separately analyse it.",
    "outcome_r": "Single-exit result in units of initial risk R = |entry - stop| = 2 x ATR. "
                 "target1 = +1, target2 = +2, target3 = +3 (the highest target touched on the "
                 "resolving bar), stopped = -1, expired = direction x (close_bar20 - entry) / R. "
                 "This is NOT ml_training's 3-tranche plan_outcomes() average.",
    "win_rate": "Target win rate = winners / resolved. Denominator = every resolved signal (stopped incl. "
                "ambiguous, target1-3, expired). Open and held signals are excluded from numerator and "
                "denominator.",
    "average_r": "Mean outcome_r over resolved signals; median R is the 50th percentile of the same set. "
                 "Expired signals contribute their actual mark-to-market R here, so a profitable expiry "
                 "raises average R even though it is not a target win. Expiries are also reported split "
                 "into positive, negative and flat.",
    "holding_period": "bars_held: the number of forward trading bars (stock_prices sessions after "
                      "signal_date) up to and including the resolving bar. Trading bars, not calendar "
                      "days. Defined for resolved signals only.",
    "mae_r": "Maximum adverse excursion in R over the bars evaluated so far (up to resolution for "
             "resolved signals). Maximum favourable excursion is not stored in Release A.",
    "target_milestones": "reached_target_k = signals whose terminal status is target_k or higher. The "
                         "trade exits at the first target touched, so these are milestones reached "
                         "within the trade; the ledger does not track price after exit (Release B).",
    "reference_session": "The latest market session with prices in stock_prices (never the wall-clock "
                         "date). 'This week' / 'this month' are the ISO week / calendar month "
                         "containing it.",
    "sample_size": f"Every rate and average carries n (its denominator) and a state: "
                   f"'{STATE_NO_DATA}' (n = 0, value null), '{STATE_PRELIMINARY}' "
                   f"(0 < n < {MIN_SAMPLE_SIZE}), '{STATE_OK}' (n >= {MIN_SAMPLE_SIZE}) or "
                   f"'{STATE_NOT_AVAILABLE}' (the stored data cannot support it).",
}


def metric(value: Optional[float], n: int, digits: int = 4) -> Dict[str, Any]:
    """A rate/average with its sample size and state. `value` is dropped to null when n == 0 so a
    consumer can never render '0%' or '0R' for 'nothing measured yet'."""
    if n <= 0 or value is None:
        return {"value": None, "n": int(n), "state": STATE_NO_DATA}
    state = STATE_OK if n >= MIN_SAMPLE_SIZE else STATE_PRELIMINARY
    return {"value": round(float(value), digits), "n": int(n), "state": state}


def ratio(numerator: int, n: int) -> Dict[str, Any]:
    return metric(numerator / n if n else None, n)


def not_available(reason: str, release: Optional[str] = None) -> Dict[str, Any]:
    out = {"value": None, "n": None, "state": STATE_NOT_AVAILABLE, "reason": reason}
    if release:
        out["release"] = release
    return out


def lifecycle(status: str, evaluation_flag: Optional[str]) -> str:
    if status != STATUS_OPEN:
        return LIFECYCLE_RESOLVED
    return LIFECYCLE_HELD if evaluation_flag else LIFECYCLE_OPEN


# What Release A does and does not collect -- so "0 observations" is never confused with
# "not collected yet".
CAPABILITIES: Dict[str, Dict[str, Any]] = {
    "signal_ledger": {"available": True, "release": "A"},
    "outcome_evaluation": {"available": True, "release": "A"},
    "same_bar_ambiguity_flag": {"available": True, "release": "A"},
    "split_suspect_hold": {"available": True, "release": "A"},
    "max_adverse_excursion": {"available": True, "release": "A"},
    "max_favourable_excursion": {"available": False, "release": "B"},
    "post_exit_trajectory": {"available": False, "release": "B"},
    "forward_return_labels": {"available": False, "release": "B"},
    "candidate_observations": {"available": False, "release": "B"},
    "feature_snapshots": {"available": False, "release": "B"},
    "training_ready_observations": {"available": False, "release": "B"},
    "evaluator_run_history": {"available": False, "release": None,
                              "reason": "evaluator run results are only in the VPS wrapper log, not the "
                                        "database -- see data health"},
}
