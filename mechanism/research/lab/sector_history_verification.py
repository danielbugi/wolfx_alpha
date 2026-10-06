"""Pure evaluation of "was the forward sector history refreshed for this session?" -- the collector's VERIFICATION of the one authoritative writer.

The fundamentals updater (through its flag-gated recorder) is the ONLY writer of the sector history. This module never writes and never repairs: it
reads what the writer left (polls and the observation chain) and answers, for one explicit session, whether the writer ran for the session's
universe in time to matter. It reads the history only through `dataset_reader` rows and judges the chain with the same `sector_history.select`
the dataset uses (one reading of the history, not two).

What it distinguishes (they are different facts and are never merged):

* the refresh did not RUN for the universe at all (`refresh_absent`: no authoritative poll in the window -- the fundamentals updater did not run,
  or the recorder flag is off);
* it ran but the vendor FAILED for most of the universe (`vendor_failure`: only `request_failed` polls);
* it ran for only PART of the universe (`partial_refresh`: too many symbols never polled);
* a symbol's chain is BROKEN (`chain_broken`: a tamper/regression signal, never repairable by a re-run).

A symbol is ACCOUNTED when the authoritative source gave a non-`request_failed` answer in the window: a usable sector, an inferred no-sector (ETF / mutual fund), or a
responded-but-unusable answer all prove the vendor was asked and answered, so a legitimate no-sector symbol (owner Option B) never fails a session.
The ONE exception is a SPARSE body (`invalid_response / response_too_sparse`, the live shape of an unknown symbol's 404): it describes nothing, so it
counts like a failure. That keeps a degraded vendor that answers with near-empty bodies from looking like a healthy refresh, while the 10 % tolerance
absorbs the handful of genuinely unknown symbols.
Per-symbol availability (fresh / stale / inferred no-sector / no history) is reported but never gates the session: an unavailable sector makes the
sector-relative cell unavailable downstream, not the session incomplete.

The only threshold is the existing readiness floor (`MIN_OBSERVED_COVERAGE`, 90 %) applied to the accounted share; the refresh-gap window is
derived from the production cadence, not tuned.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Mapping, Sequence

from research.lab import dataset_contract as C
from research.lab import dataset_readiness as READY
from research.lab import sector_history as SH

SCHEMA = "sector_history_verification_v1"
MIN_ACCOUNTED_SHARE = READY.MIN_OBSERVED_COVERAGE

# Cadence the requirement is derived from: the nightly pipeline refreshes fundamentals for the FULL active universe on every trading day and skips
# weekends/holidays, so the longest NORMAL gap between two refreshes of a symbol is a long weekend with a holiday (Friday evening -> Tuesday): 4 days.
MAX_REFRESH_GAP_DAYS = 4

REFRESH_ABSENT, VENDOR_FAILURE, PARTIAL_REFRESH = "refresh_absent", "vendor_failure", "partial_refresh"
CHAIN_BROKEN, NO_UNIVERSE, TABLES_ABSENT = "chain_broken", "no_universe", "sector_history_tables_absent"
REASONS = (REFRESH_ABSENT, VENDOR_FAILURE, PARTIAL_REFRESH, CHAIN_BROKEN, NO_UNIVERSE, TABLES_ABSENT)
PERMANENT_ALWAYS = (CHAIN_BROKEN,)           # every other reason is repairable only until the session's decision deadline
FAILED = "request_failed"
SPARSE = ("invalid_response", "response_too_sparse")   # a body with too few keys to describe any company (the live 404 shape): it carries no more proof than a failure
EXAMPLES = 5

MEANING = {
    REFRESH_ABSENT: "no authoritative poll exists in the window: the fundamentals updater did not run for the universe (or the recorder flag is off)",
    VENDOR_FAILURE: "the refresh ran but the authoritative vendor failed for most of the universe (only request_failed polls)",
    PARTIAL_REFRESH: "the refresh ran for only part of the universe: too many symbols were never polled in the window",
    CHAIN_BROKEN: "a symbol's observation chain fails verification: never repaired by a re-run, investigate before trusting any value",
    NO_UNIVERSE: "the session has no stock bars, so there is no universe to verify",
    TABLES_ABSENT: "the sector-history tables do not exist (migration 31 is not applied)",
}


def window(session: date, grace_days: int) -> "tuple[datetime, datetime]":
    """[start, end): start = the first instant of the day MAX_REFRESH_GAP_DAYS before the session; end = the session's decision deadline (a poll
    stamped at or after it cannot be known to the decision)."""
    start = datetime.combine(session - timedelta(days=MAX_REFRESH_GAP_DAYS), time(0), tzinfo=timezone.utc)
    return start, C.decision_deadline(session, grace_days)


def evaluate(*, session: date, grace_days: int, universe: Sequence[str], activity: Sequence[Mapping[str, Any]],
             history_rows: Sequence[Mapping[str, Any]], source: str, cutoff: datetime) -> Dict[str, Any]:
    """Deterministic document + `reasons` (codes). `activity` are `dataset_reader.poll_activity` rows, `history_rows` the `history_rows` read."""
    syms = sorted(set(universe))
    states: Dict[str, set] = {}
    last: Dict[str, datetime] = {}
    n_polls = 0
    sparse_symbols = set()
    for a in activity:
        sparse = (a["response_state"], a.get("failure_reason")) == SPARSE
        if sparse:
            sparse_symbols.add(a["symbol"])
        states.setdefault(a["symbol"], set()).add(FAILED if sparse else a["response_state"])
        n_polls += a["polls"]
        if a["symbol"] not in last or a["last_attempted_at"] > last[a["symbol"]]:
            last[a["symbol"]] = a["last_attempted_at"]
    accounted = [s for s in syms if states.get(s, set()) - {FAILED}]
    failed_only = [s for s in syms if states.get(s) == {FAILED}]
    never = [s for s in syms if s not in states]

    obs, conf = SH.group_history(history_rows, source)
    kinds: Dict[str, int] = {}
    avail: Dict[str, int] = {}
    broken: List[str] = []
    for s in syms:
        sel = SH.select(obs.get(s, []), conf.get(s, []), t0=session, grace_days=grace_days, cutoff=cutoff)
        kinds[sel.kind] = kinds.get(sel.kind, 0) + 1
        if sel.kind == SH.K_BROKEN:
            broken.append(s)
        elif sel.kind == SH.K_OBSERVED:
            avail[sel.evidence.state] = avail.get(sel.evidence.state, 0) + 1
    n = len(syms)
    share = (len(accounted) / n) if n else 0.0

    reasons: List[str] = []
    if n == 0:
        reasons.append(NO_UNIVERSE)
    elif len(never) == n:
        reasons.append(REFRESH_ABSENT)
    elif share < MIN_ACCOUNTED_SHARE:
        reasons.append(VENDOR_FAILURE if len(failed_only) >= len(never) else PARTIAL_REFRESH)
    if broken:
        reasons.append(CHAIN_BROKEN)
    lo, hi = window(session, grace_days)
    return {
        "schema": SCHEMA, "session": session.isoformat(), "source": source, "window": {"start": lo.isoformat(), "end": hi.isoformat(),
                                                                                       "max_refresh_gap_days": MAX_REFRESH_GAP_DAYS},
        "universe": n, "accounted": len(accounted), "accounted_share": round(share, 6), "min_accounted_share": MIN_ACCOUNTED_SHARE,
        "failed_only": len(failed_only), "symbols_with_a_sparse_response": len(sparse_symbols), "never_polled": len(never), "polls_in_window": n_polls,
        "history_by_kind": dict(sorted(kinds.items())), "observed_by_evidence_state": dict(sorted(avail.items())),
        "chain_broken_symbols": broken[:EXAMPLES], "chain_broken_count": len(broken),
        "failed_only_examples": failed_only[:EXAMPLES], "never_polled_examples": never[:EXAMPLES],
        "availability_gates_session": False, "reasons": reasons, "meaning": {r: MEANING[r] for r in reasons},
        "satisfied": not reasons
    }
