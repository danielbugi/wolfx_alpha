# mechanism/strategy_analytics/research.py
"""
Release B research-layer read model: what the candidate-capture observer (mechanism/research) stored, shaped for the
Quant Lab UI. Same rules as analytics.py -- I/O injected as `fetch(sql, params)`, every number computed here and
only presented by the dashboard, all SQL parameterized, sort keys and filter columns from whitelists.

Read-only. The research tables are immutable and are written only by the screener's capture hook; nothing here writes.

Availability is explicit, never inferred from a zero:
  not_available -- the research tables do not exist (migration 22 not applied): every value is "Not collected"
  no_data       -- the tables exist but this strategy has no capture run yet (capture not activated)
  ok            -- at least one capture run is recorded
A count is a real 0 only when a capture run exists for the session; otherwise it is null with a state.

Capture health is derived from the run counters, not from `candidate_capture_run.status` alone: the observer finishes a
run as 'complete' even when some candidates could not be captured, and a partial capture must never look healthy.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

from strategy_analytics.analytics import MAX_PAGE_SIZE, Fetch, _clean
from strategy_analytics.definitions import (
    DIRECTION_VALUES, DIRECTIONS, STATE_NO_DATA, STATE_NOT_AVAILABLE, STATE_OK, lifecycle, metric, not_available,
)

RELEASE = "B"
RESEARCH_TABLES = ("feature_set_registry", "candidate_capture_run", "feature_snapshot", "candidate_observation")
STUCK_RUN_MINUTES = 30
HISTORY_DEFAULT = 30
HISTORY_MAX = 120
SKIPPED_SYMBOLS_SHOWN = 25
GUARD_STATUSES = ("passed", "rejected", "not_evaluated")

FORWARD_OUTCOMES = not_available(
    "Forward outcome labels are not collected: migration 23 and the fwd_v1 label set do not exist yet.")

DEFINITIONS: Dict[str, str] = {
    "candidate": "One row in candidate_observation: a breakout or near-breakout the strategy's own detection produced "
                 "on the session, recorded BEFORE the universe guards drop anything -- the rejected candidates are "
                 "the control group. Identity: strategy, symbol, session, direction.",
    "evaluated": "The size of the universe the screener scanned, as recorded by the capture run. The screener does "
                 "not record it yet, so this stage reads 'Not recorded' rather than a guess.",
    "guard_passed": "passed_guard = TRUE: the universe / data-quality guards kept the candidate. A candidate whose "
                    "guards were not evaluated is neither passed nor rejected.",
    "ranked": "Guard-passed candidates that received a position (session_rank) in the final post-guard ranking.",
    "selected": "tracked_intent = TRUE: the shared ledger-eligibility predicate accepted it, i.e. the ledger writer "
                "takes this candidate. It is a statement of intent; the Ledger Signals stage counts the rows "
                "actually written.",
    "ledger_signals": "signal_ledger rows for the session. 'Linked' = rows carrying this candidate's observation id.",
    "guard_pass_rate": "guard passed / (guard passed + guard rejected) for the session; candidates whose guards were "
                       "not evaluated are excluded from the denominator.",
    "selected_rate": "selected / candidates for the session.",
    "capture_coverage": "(captured + already captured) / candidates, from the capture run's own counters.",
    "snapshot_coverage": "(candidates - snapshot skipped) / candidates: the share for which a usable T0 snapshot "
                         "could be built.",
    "missing_feature_rate": "missing feature slots / total feature slots over the session's snapshots. A missing "
                            "feature is stored as null and named in missing_features -- never as 0.",
    "capture_status": "COMPLETE = every candidate accounted for and captured. PARTIAL = the run finished but "
                      "candidates were lost (no usable snapshot, unreadable payload, guards not evaluated, a counter "
                      "gap). FAILED = the run failed, or stayed 'running' past "
                      f"{STUCK_RUN_MINUTES} minutes. DISABLED = no capture run exists for a session the ledger "
                      "recorded after capture was first seen (capture was off, or the run never reached capture).",
    "drift": "A re-run produced a different payload for an already-captured identity. The stored row is never "
             "replaced (first valid write wins); the count is a warning, not a capture failure.",
}


class ResearchUnavailable(LookupError):
    """The research tables do not exist (migration 22 not applied)."""

    def __init__(self, availability: Dict[str, Any]):
        super().__init__(availability["reason"])
        self.availability = availability


# ============================================================================ availability
def schema_state(fetch: Fetch) -> Dict[str, bool]:
    row = fetch("SELECT " + ", ".join(f"to_regclass('{t}') IS NOT NULL AS {t}" for t in RESEARCH_TABLES), None)[0]
    return {t: bool(row[t]) for t in RESEARCH_TABLES}


def availability(fetch: Fetch, strategy: Dict[str, Any]) -> Dict[str, Any]:
    schema = schema_state(fetch)
    if not all(schema.values()):
        return {"state": STATE_NOT_AVAILABLE, "release": RELEASE, "schema": schema, "capture_runs": None,
                "reason": "Candidate observations and T0 feature snapshots are not collected yet "
                          "(research tables not installed: migration 22 / Release B)."}
    runs = int(fetch("SELECT count(*) AS n FROM candidate_capture_run WHERE strategy_id = %s",
                     (strategy["id"],))[0]["n"])
    if runs == 0:
        return {"state": STATE_NO_DATA, "release": RELEASE, "schema": schema, "capture_runs": 0,
                "reason": "The research tables exist but no capture run is recorded for this strategy: "
                          "capture has not been activated."}
    return {"state": STATE_OK, "release": RELEASE, "schema": schema, "capture_runs": runs, "reason": None}


def _require_available(fetch: Fetch, strategy: Dict[str, Any]) -> Dict[str, Any]:
    avail = availability(fetch, strategy)
    if avail["state"] == STATE_NOT_AVAILABLE:
        raise ResearchUnavailable(avail)
    return avail


# ============================================================================ capture-run health
RUN_COUNTERS = ("candidates", "captured", "already_captured", "stale_skipped", "snapshot_skipped", "invalid_skipped",
                "guard_rejected", "guard_not_evaluated", "hash_drift", "snapshot_drift", "defaulted_flagged")
_IDENTITY_COUNTERS = ("candidates", "captured", "already_captured", "stale_skipped", "snapshot_skipped",
                      "invalid_skipped")


def _n(run: Dict[str, Any], key: str) -> int:
    return int(run.get(key) or 0)


def classify_run(run: Dict[str, Any]) -> Dict[str, Any]:
    """The capture health of one run row. Pure. `run['stuck']` is computed in SQL (running past the threshold).
    Returns {status: complete|partial|failed|running, reasons: [why it is not healthy], notes: [worth knowing]}."""
    stored = run["status"]
    if stored == "failed":
        return {"status": "failed", "reasons": [run.get("error") or "The capture run failed."], "notes": []}
    if stored == "running":
        if run.get("stuck"):
            return {"status": "failed", "notes": [],
                    "reasons": [f"The run is still 'running' after {STUCK_RUN_MINUTES} minutes; "
                                "the process did not finish."]}
        return {"status": "running", "reasons": [], "notes": ["The capture run is in progress."]}

    reasons: List[str] = []
    notes: List[str] = []
    if any(run.get(k) is None for k in _IDENTITY_COUNTERS):
        reasons.append("The run is marked complete but its counters were never finalised.")
    else:
        gap = _n(run, "candidates") - sum(_n(run, k) for k in _IDENTITY_COUNTERS[1:])
        if gap != 0:
            reasons.append(f"{abs(gap)} candidate(s) {'unaccounted for' if gap > 0 else 'over-counted'}: the counters "
                           "do not add up to the candidate count.")
        if _n(run, "snapshot_skipped"):
            reasons.append(f"{_n(run, 'snapshot_skipped')} candidate(s) had no usable T0 snapshot and were not captured.")
        if _n(run, "invalid_skipped"):
            reasons.append(f"{_n(run, 'invalid_skipped')} candidate payload(s) were unreadable and were not captured.")
        if _n(run, "candidates") == 0:
            notes.append("The screener produced no candidates for this session.")
    if _n(run, "guard_not_evaluated"):
        reasons.append(f"The universe guards were not evaluated for {_n(run, 'guard_not_evaluated')} candidate(s).")
    if _n(run, "stale_skipped"):
        notes.append(f"{_n(run, 'stale_skipped')} candidate(s) were skipped because their own bar is not the session.")
    if _n(run, "hash_drift"):
        notes.append(f"{_n(run, 'hash_drift')} re-run payload(s) differed from the stored observation (not applied).")
    if _n(run, "snapshot_drift"):
        notes.append(f"{_n(run, 'snapshot_drift')} recomputed snapshot(s) differed from the stored one (not applied).")
    return {"status": "partial" if reasons else "complete", "reasons": reasons, "notes": notes}


def _run_out(r: Dict[str, Any], *, detail: bool = False) -> Dict[str, Any]:
    r = _clean(r)
    health = classify_run(r)
    counters = {k: r.get(k) for k in RUN_COUNTERS}
    accounted = None
    if all(r.get(k) is not None for k in _IDENTITY_COUNTERS):
        accounted = sum(_n(r, k) for k in _IDENTITY_COUNTERS[1:])
    skipped = r.get("skipped_symbols") or {}
    out = {
        "id": r["id"], "session_date": r["session_date"], "stored_status": r["status"], "health": health,
        "feature_set_version": r["feature_set_version"], "run_attempts": int(r.get("run_attempts") or 1),
        "started_at": r["run_started_at"], "finished_at": r["run_finished_at"],
        "runtime_seconds": round((r["run_finished_at"] - r["run_started_at"]).total_seconds(), 1)
        if r["run_finished_at"] and r["run_started_at"] else None,
        "universe_size": r["universe_size"], "counters": counters,
        "accounted": accounted, "unaccounted": None if accounted is None else _n(r, "candidates") - accounted,
        "skipped_symbol_count": len(skipped), "error": r.get("error"), "code_ref": r.get("code_ref"),
    }
    if detail:
        out["skipped_symbols"] = dict(list(skipped.items())[:SKIPPED_SYMBOLS_SHOWN])
    return out


def _disabled_row(session: date, ledger_signals: int) -> Dict[str, Any]:
    return {"id": None, "session_date": session, "stored_status": None, "run_attempts": 0,
            "health": {"status": "disabled", "notes": [],
                       "reasons": [f"No capture run is recorded for this session, but the ledger recorded "
                                   f"{ledger_signals} signal(s). Capture was off or the run did not reach capture."]},
            "feature_set_version": None, "started_at": None, "finished_at": None, "runtime_seconds": None,
            "universe_size": None, "counters": {k: None for k in RUN_COUNTERS}, "accounted": None,
            "unaccounted": None, "skipped_symbol_count": 0, "error": None, "code_ref": None,
            "ledger_signals": ledger_signals}


def _missing_stats(fetch: Fetch, strategy_id: int, sessions: List[date]) -> Dict[date, Dict[str, Any]]:
    if not sessions:
        return {}
    rows = fetch("""
        SELECT s.session_date, count(*) AS snapshots,
               count(*) FILTER (WHERE s.snapshot_status = 'partial') AS partial_snapshots,
               coalesce(sum(s.missing), 0) AS missing_slots, coalesce(sum(s.slots), 0) AS total_slots
        FROM (SELECT DISTINCT ON (fs.id) co.session_date, fs.snapshot_status,
                     cardinality(fs.missing_features) AS missing,
                     CASE WHEN jsonb_typeof(fs.features) = 'object'
                          THEN (SELECT count(*) FROM jsonb_object_keys(fs.features)) ELSE 0 END AS slots
              FROM candidate_observation co JOIN feature_snapshot fs ON fs.id = co.snapshot_id
              WHERE co.strategy_id = %s AND co.session_date = ANY(%s)) s
        GROUP BY s.session_date
    """, (strategy_id, sessions))
    out = {}
    for r in rows:
        total = int(r["total_slots"])
        out[r["session_date"]] = {
            "snapshots": int(r["snapshots"]), "partial_snapshots": int(r["partial_snapshots"]),
            "missing_slots": int(r["missing_slots"]), "total_slots": total,
            "rate": metric(int(r["missing_slots"]) / total if total else None, int(r["snapshots"])),
        }
    return out


def capture_runs(fetch: Fetch, strategy: Dict[str, Any], limit: int = HISTORY_DEFAULT) -> Dict[str, Any]:
    """Capture health: the latest session's run in full, then a compact per-session history (latest run per session,
    plus DISABLED rows for ledger sessions after capture was first seen that have no run)."""
    if not 1 <= limit <= HISTORY_MAX:
        raise ValueError(f"limit must be 1..{HISTORY_MAX}")
    avail = availability(fetch, strategy)
    base = {"strategy": strategy, "availability": avail, "stuck_after_minutes": STUCK_RUN_MINUTES,
            "definitions": {k: DEFINITIONS[k] for k in ("capture_status", "drift")}}
    if avail["state"] != STATE_OK:
        overall = {"status": "not_available" if avail["state"] == STATE_NOT_AVAILABLE else "disabled",
                   "session_date": None, "reason": avail["reason"]}
        return {**base, "overall": overall, "latest": None, "history": []}

    sid = strategy["id"]
    runs = fetch("""
        WITH latest AS (
            SELECT DISTINCT ON (session_date) * FROM candidate_capture_run WHERE strategy_id = %(sid)s
            ORDER BY session_date, run_started_at DESC, id DESC)
        SELECT l.*, (SELECT count(*) FROM candidate_capture_run r
                     WHERE r.strategy_id = l.strategy_id AND r.session_date = l.session_date) AS run_attempts,
               (l.status = 'running' AND l.run_started_at < now() - make_interval(mins => %(stuck)s)) AS stuck
        FROM latest l ORDER BY l.session_date DESC LIMIT %(limit)s
    """, {"sid": sid, "stuck": STUCK_RUN_MINUTES, "limit": limit})
    missing = _missing_stats(fetch, sid, [r["session_date"] for r in runs])
    history = [_run_out(r) for r in runs]
    for h in history:
        h["missing_features"] = missing.get(h["session_date"])

    disabled = fetch("""
        SELECT sl.signal_date AS session_date, count(*) AS ledger_signals FROM signal_ledger sl
        WHERE sl.strategy_id = %(sid)s
          AND sl.signal_date > (SELECT min(session_date) FROM candidate_capture_run WHERE strategy_id = %(sid)s)
          AND NOT EXISTS (SELECT 1 FROM candidate_capture_run r
                          WHERE r.strategy_id = sl.strategy_id AND r.session_date = sl.signal_date)
        GROUP BY sl.signal_date ORDER BY sl.signal_date DESC LIMIT %(limit)s
    """, {"sid": sid, "limit": limit})
    history += [_disabled_row(d["session_date"], int(d["ledger_signals"])) for d in disabled]
    history.sort(key=lambda h: h["session_date"], reverse=True)
    history = history[:limit]

    top = history[0]
    latest = next((h for h in history if h["id"] is not None), None)
    if latest is not None:
        full = next(r for r in runs if r["id"] == latest["id"])
        latest = {**_run_out(full, detail=True), "missing_features": latest["missing_features"]}
    overall = {"status": top["health"]["status"], "session_date": top["session_date"],
               "reason": (top["health"]["reasons"] or [None])[0]}
    return {**base, "overall": overall, "latest": latest, "history": history}


# ============================================================================ summary / funnel
def _count(value: Optional[int], state: str = STATE_OK, reason: Optional[str] = None) -> Dict[str, Any]:
    out = {"value": value if state == STATE_OK else None, "state": state}
    if reason:
        out["reason"] = reason
    if state == STATE_NOT_AVAILABLE:
        out["release"] = RELEASE
    return out


def _unavailable_summary(strategy: Dict[str, Any], avail: Dict[str, Any]) -> Dict[str, Any]:
    state = avail["state"]
    na = lambda: _count(None, state, avail["reason"])  # noqa: E731
    stage_keys = (("evaluated", "Evaluated"), ("candidates", "Candidates"), ("guard_passed", "Guard Passed"),
                  ("ranked", "Qualified / Ranked"), ("selected", "Selected"), ("ledger_signals", "Ledger Signals"))
    rate = lambda: {"value": None, "n": None, "state": state, "reason": avail["reason"], "release": RELEASE}  # noqa: E731
    return {
        "strategy": strategy, "availability": avail, "definitions": DEFINITIONS, "forward_outcomes": FORWARD_OUTCOMES,
        "latest_session": None, "feature_set": {"current": None, "registered": []},
        "funnel": {"session_date": None, "stages": [{"key": k, "label": lab, **na()} for k, lab in stage_keys]},
        "cards": {k: na() for k in ("observations", "snapshots", "sessions_captured", "bullish", "bearish",
                                    "guard_passed", "guard_rejected", "guard_not_evaluated", "selected")},
        "rates": {k: rate() for k in ("guard_pass_rate", "selected_rate", "missing_feature_rate",
                                      "snapshot_coverage", "capture_coverage")},
    }


def _registered_feature_sets(fetch: Fetch) -> List[Dict[str, Any]]:
    rows = fetch("""SELECT feature_set_version, description, manifest_hash, extends, created_at,
                           CASE WHEN jsonb_typeof(manifest) = 'object' AND jsonb_typeof(manifest->'features') = 'array'
                                THEN jsonb_array_length(manifest->'features') END AS feature_count
                    FROM feature_set_registry ORDER BY created_at DESC, feature_set_version""", None)
    return [{"version": r["feature_set_version"], "description": r["description"], "extends": r["extends"],
             "manifest_hash": r["manifest_hash"].strip(), "feature_count": r["feature_count"],
             "registered_at": r["created_at"]} for r in rows]


def summary(fetch: Fetch, strategy: Dict[str, Any]) -> Dict[str, Any]:
    avail = availability(fetch, strategy)
    if avail["state"] == STATE_NOT_AVAILABLE:
        return _unavailable_summary(strategy, avail)
    sid = strategy["id"]
    registered = _registered_feature_sets(fetch)
    if avail["state"] == STATE_NO_DATA:
        out = _unavailable_summary(strategy, avail)
        out["feature_set"] = {"current": None, "registered": registered}
        return out

    runs = capture_runs(fetch, strategy, limit=HISTORY_DEFAULT)
    latest = runs["latest"]
    session = latest["session_date"] if latest else None
    tot = fetch("""SELECT count(*) AS observations, count(DISTINCT snapshot_id) AS snapshots,
                          count(DISTINCT session_date) AS sessions
                   FROM candidate_observation WHERE strategy_id = %s""", (sid,))[0]
    f = fetch("""
        SELECT count(*) AS candidates,
               count(*) FILTER (WHERE passed_guard) AS passed,
               count(*) FILTER (WHERE passed_guard IS FALSE) AS rejected,
               count(*) FILTER (WHERE passed_guard IS NULL) AS not_evaluated,
               count(*) FILTER (WHERE passed_guard AND session_rank IS NOT NULL) AS ranked,
               count(*) FILTER (WHERE tracked_intent) AS selected,
               count(*) FILTER (WHERE direction = 1) AS bullish, count(*) FILTER (WHERE direction = -1) AS bearish
        FROM candidate_observation WHERE strategy_id = %s AND session_date = %s
    """, (sid, session))[0] if session else None
    led = fetch("""SELECT count(*) AS signals, count(*) FILTER (WHERE observation_id IS NOT NULL) AS linked
                   FROM signal_ledger WHERE strategy_id = %s AND signal_date = %s""", (sid, session))[0] if session else None

    # A session whose run did not complete may hold no observations: that is "no data", never a measured 0.
    has_obs = bool(f and f["candidates"])
    session_ok = has_obs or (latest is not None and latest["health"]["status"] == "complete")

    def sc(key: str) -> Dict[str, Any]:
        if f is None or not session_ok:
            return _count(None, STATE_NO_DATA, "The latest capture run holds no observations.")
        return _count(int(f[key]))

    universe = latest["universe_size"] if latest else None
    stages = [
        {"key": "evaluated", "label": "Evaluated",
         **(_count(int(universe)) if universe is not None
            else _count(None, STATE_NOT_AVAILABLE, "Universe size is not recorded by the screener."))},
        {"key": "candidates", "label": "Candidates", **sc("candidates")},
        {"key": "guard_passed", "label": "Guard Passed", **sc("passed")},
        {"key": "ranked", "label": "Qualified / Ranked", **sc("ranked")},
        {"key": "selected", "label": "Selected", **sc("selected")},
        {"key": "ledger_signals", "label": "Ledger Signals",
         **(_count(int(led["signals"])) if led is not None and session_ok
            else _count(None, STATE_NO_DATA, "No captured session."))},
    ]
    if led is not None and session_ok:
        stages[-1]["linked"] = int(led["linked"])

    counters = latest["counters"] if latest else {}
    cands = counters.get("candidates")

    def rate(num: Optional[int], den: Optional[int]) -> Dict[str, Any]:
        if num is None or den is None or not session_ok:
            return metric(None, 0)
        return metric(num / den if den else None, int(den))

    passed, rejected = (int(f["passed"]), int(f["rejected"])) if f else (None, None)
    missing = latest["missing_features"] if latest else None
    rates = {
        "guard_pass_rate": rate(passed, None if passed is None else passed + rejected),
        "selected_rate": rate(int(f["selected"]) if f else None, int(f["candidates"]) if f else None),
        "capture_coverage": rate(None if cands is None or counters.get("captured") is None
                                 else _n(counters, "captured") + _n(counters, "already_captured"), cands),
        "snapshot_coverage": rate(None if cands is None else cands - _n(counters, "snapshot_skipped"), cands),
        "missing_feature_rate": missing["rate"] if missing else metric(None, 0),
    }
    return {
        "strategy": strategy, "availability": avail, "definitions": DEFINITIONS, "forward_outcomes": FORWARD_OUTCOMES,
        "latest_session": {"session_date": session, "run_id": latest["id"], "capture_status": latest["health"]["status"],
                           "finished_at": latest["finished_at"]} if latest else None,
        "feature_set": {"current": latest["feature_set_version"] if latest else None, "registered": registered},
        "funnel": {"session_date": session, "stages": stages},
        "cards": {
            "observations": _count(int(tot["observations"])), "snapshots": _count(int(tot["snapshots"])),
            "sessions_captured": _count(int(tot["sessions"])),
            "bullish": sc("bullish"), "bearish": sc("bearish"), "guard_passed": sc("passed"),
            "guard_rejected": sc("rejected"), "guard_not_evaluated": sc("not_evaluated"), "selected": sc("selected"),
        },
        "rates": rates,
    }


# ============================================================================ candidate explorer
CANDIDATE_SORTS = {
    "rank": "co.session_rank ASC NULLS LAST, co.alignment_score DESC NULLS LAST, co.symbol ASC, co.direction ASC",
    "combined_desc": "co.combined_score DESC NULLS LAST, co.symbol ASC, co.direction ASC",
    "alignment_desc": "co.alignment_score DESC NULLS LAST, co.symbol ASC, co.direction ASC",
    "breakout_desc": "co.breakout_dist_atr DESC NULLS LAST, co.symbol ASC, co.direction ASC",
    "symbol": "co.symbol ASC, co.direction ASC",
    "grade": "co.quality_grade ASC NULLS LAST, co.symbol ASC, co.direction ASC",
}

_CANDIDATE_COLUMNS = """co.id, co.symbol, co.session_date, co.direction, co.signal_type, co.triggered, co.entry_close,
    co.breakout_dist_atr, co.distance_to_channel_pct, co.passed_guard, co.guard_reasons, co.alignment_score,
    co.quality_grade, co.combined_score, co.session_rank, co.ml_status, co.ml_score, co.tracked_intent,
    co.snapshot_id, fs.snapshot_status, cardinality(fs.missing_features) AS missing_count,
    sl.id AS ledger_signal_id"""
_CANDIDATE_FROM = """candidate_observation co
    JOIN feature_snapshot fs ON fs.id = co.snapshot_id
    LEFT JOIN signal_ledger sl ON sl.observation_id = co.id AND sl.strategy_id = co.strategy_id"""


def _guard_status(passed: Optional[bool]) -> str:
    return "not_evaluated" if passed is None else "passed" if passed else "rejected"


def _candidate_item(r: Dict[str, Any]) -> Dict[str, Any]:
    r = _clean(r)
    r["direction"] = DIRECTIONS[r["direction"]]
    r["guard_status"] = _guard_status(r["passed_guard"])
    r["selected"] = r.pop("tracked_intent")
    r["candidate_class"] = r.pop("signal_type")
    r["guard_reasons"] = r["guard_reasons"] or []
    return r


def _latest_session(fetch: Fetch, strategy_id: int) -> Optional[date]:
    return fetch("SELECT max(session_date) AS s FROM candidate_capture_run WHERE strategy_id = %s",
                 (strategy_id,))[0]["s"]


def _like_prefix(symbol: str) -> str:
    return symbol.upper().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def list_candidates(fetch: Fetch, strategy: Dict[str, Any], *, session_date: Optional[date] = None,
                    direction: Optional[str] = None, guard: Optional[str] = None, selected: Optional[bool] = None,
                    symbol: Optional[str] = None, candidate_class: Optional[str] = None,
                    grade: Optional[str] = None, sort: str = "rank", limit: int = 50,
                    offset: int = 0) -> Dict[str, Any]:
    """Paginated, filtered candidate observations for ONE strategy and ONE session (default: the latest captured
    session, never the wall clock). Raises ValueError on a bad filter/sort (the caller maps it to a 4xx)."""
    if sort not in CANDIDATE_SORTS:
        raise ValueError(f"unknown sort '{sort}'")
    if not 1 <= limit <= MAX_PAGE_SIZE or offset < 0:
        raise ValueError(f"limit must be 1..{MAX_PAGE_SIZE} and offset >= 0")
    if direction is not None and direction not in DIRECTION_VALUES:
        raise ValueError(f"unknown direction '{direction}'")
    if guard is not None and guard not in GUARD_STATUSES:
        raise ValueError(f"unknown guard status '{guard}'")
    avail = availability(fetch, strategy)
    empty = {"strategy": strategy, "availability": avail, "items": [], "total": None, "limit": limit,
             "offset": offset, "sort": sort, "has_more": False, "session_date": session_date,
             "facets": {"classes": [], "grades": []}}
    if avail["state"] != STATE_OK:
        return empty
    session = session_date or _latest_session(fetch, strategy["id"])
    if session is None:
        return empty

    where = ["co.strategy_id = %(sid)s", "co.session_date = %(session)s"]
    params: Dict[str, Any] = {"sid": strategy["id"], "session": session}
    if direction:
        where.append("co.direction = %(direction)s")
        params["direction"] = DIRECTION_VALUES[direction]
    if guard == "passed":
        where.append("co.passed_guard IS TRUE")
    elif guard == "rejected":
        where.append("co.passed_guard IS FALSE")
    elif guard == "not_evaluated":
        where.append("co.passed_guard IS NULL")
    if selected is not None:
        where.append("co.tracked_intent = %(selected)s")
        params["selected"] = selected
    if symbol:
        where.append("co.symbol LIKE %(symbol)s ESCAPE '\\'")
        params["symbol"] = _like_prefix(symbol)
    if candidate_class:
        where.append("co.signal_type = %(cls)s")
        params["cls"] = candidate_class
    if grade:
        where.append("co.quality_grade = %(grade)s")
        params["grade"] = grade.upper()
    where_sql = " AND ".join(where)

    total = int(fetch(f"SELECT count(*) AS n FROM candidate_observation co WHERE {where_sql}", params)[0]["n"])
    rows = fetch(f"SELECT {_CANDIDATE_COLUMNS} FROM {_CANDIDATE_FROM} WHERE {where_sql} "
                 f"ORDER BY {CANDIDATE_SORTS[sort]} LIMIT %(limit)s OFFSET %(offset)s",
                 {**params, "limit": limit, "offset": offset})
    items = [_candidate_item(r) for r in rows]
    base = {"sid": strategy["id"], "session": session}
    classes = fetch("""SELECT signal_type AS value, count(*) AS count FROM candidate_observation
                       WHERE strategy_id = %(sid)s AND session_date = %(session)s GROUP BY 1 ORDER BY 2 DESC, 1""", base)
    grades = fetch("""SELECT quality_grade AS value, count(*) AS count FROM candidate_observation
                      WHERE strategy_id = %(sid)s AND session_date = %(session)s AND quality_grade IS NOT NULL
                      GROUP BY 1 ORDER BY 1""", base)
    return {"strategy": strategy, "availability": avail, "items": items, "total": total, "limit": limit,
            "offset": offset, "sort": sort, "has_more": offset + len(items) < total, "session_date": session,
            "facets": {"classes": [{"value": c["value"], "count": int(c["count"])} for c in classes],
                       "grades": [{"value": g["value"], "count": int(g["count"])} for g in grades]}}


# ============================================================================ candidate detail
def get_candidate(fetch: Fetch, strategy: Dict[str, Any], observation_id: int) -> Optional[Dict[str, Any]]:
    """One observation: the strategy's own decision (`strategy_context`), a pointer to the T0 snapshot it was taken
    against, and the ledger lineage. Raises ResearchUnavailable before migration 22; None when not found."""
    _require_available(fetch, strategy)
    rows = fetch(f"""
        SELECT co.*, fs.snapshot_status, fs.feature_set_version AS snapshot_feature_set,
               cardinality(fs.missing_features) AS missing_count,
               r.run_started_at, r.run_finished_at, r.status AS run_status,
               sl.id AS ledger_signal_id, sl.status AS ledger_status, sl.evaluation_flag AS ledger_evaluation_flag,
               sl.outcome_r AS ledger_outcome_r, sl.signal_date AS ledger_signal_date
        FROM candidate_observation co
        JOIN feature_snapshot fs ON fs.id = co.snapshot_id
        JOIN candidate_capture_run r ON r.id = co.capture_run_id
        LEFT JOIN signal_ledger sl ON sl.observation_id = co.id AND sl.strategy_id = co.strategy_id
        WHERE co.id = %s AND co.strategy_id = %s
    """, (observation_id, strategy["id"]))
    if not rows:
        return None
    r = _clean(rows[0])
    signal = None
    if r["ledger_signal_id"] is not None:
        signal = {"id": r["ledger_signal_id"], "signal_date": r["ledger_signal_date"], "status": r["ledger_status"],
                  "lifecycle": lifecycle(r["ledger_status"], r["ledger_evaluation_flag"]),
                  "outcome_r": r["ledger_outcome_r"]}
    return {
        "id": r["id"], "strategy": strategy,
        "identity": {"symbol": r["symbol"], "session_date": r["session_date"], "bar_date": r["bar_date"],
                     "direction": DIRECTIONS[r["direction"]], "strategy_version": r["strategy_version"]},
        "strategy_context": {
            "candidate_class": r["signal_type"], "triggered": r["triggered"],
            "levels": {"entry_close": r["entry_close"], "channel_high_prev": r["channel_high_prev"],
                       "channel_low_prev": r["channel_low_prev"]},
            "breakout_dist_atr": r["breakout_dist_atr"], "distance_to_channel_pct": r["distance_to_channel_pct"],
            "guard": {"status": _guard_status(r["passed_guard"]), "reasons": r["guard_reasons"] or []},
            "alignment_score": r["alignment_score"], "quality_grade": r["quality_grade"],
            "combined_score": r["combined_score"], "session_rank": r["session_rank"],
            "selected": r["tracked_intent"], "screener_defaults": r["screener_defaults"] or [],
            "model": {"status": r["ml_status"], "score": r["ml_score"], "confidence": r["ml_confidence"],
                      "version": r["ml_model_version"]},
            "extension": r["strategy_context"] or {},
        },
        "capture": {"run_id": r["capture_run_id"], "run_status": r["run_status"], "captured_at": r["captured_at"],
                    "run_finished_at": r["run_finished_at"], "code_ref": r["code_ref"]},
        "snapshot": {"id": r["snapshot_id"], "feature_set_version": r["snapshot_feature_set"],
                     "snapshot_status": r["snapshot_status"], "missing_count": r["missing_count"]},
        "lineage": {"observation_id": r["id"], "snapshot_id": r["snapshot_id"], "signal": signal},
    }


# ============================================================================ feature snapshot detail
FEATURE_GROUPS = (
    ("price", "Price"), ("trend", "Trend"), ("momentum", "Momentum"), ("volatility", "Volatility"),
    ("volume_liquidity", "Volume / Liquidity"), ("week52", "52-week Position"),
    ("market_metadata", "Market Metadata"), ("data_quality", "Data Quality"),
)
# Presentation grouping for t0_v1. A feature the table does not name lands in "Other", so a later feature set never
# loses a value from the view; grouping is display only and never affects what is stored.
_GROUP_OF = {
    "open": "price", "high": "price", "low": "price", "close": "price", "prev_close": "price", "gap_pct": "price",
    "close_location": "price",
    "sma10_vs_20": "trend", "dist_sma50_pct": "trend", "dist_sma200_pct": "trend",
    "rsi_14": "momentum", "macd_norm": "momentum", "ret_5d": "momentum", "ret_20d": "momentum",
    "ret_60d": "momentum", "bollinger_pos": "momentum",
    "atr_14": "volatility", "atr_pct": "volatility", "chan_width_pct": "volatility",
    "chan_squeeze_pctile": "volatility", "bar_range_atr": "volatility",
    "volume": "volume_liquidity", "vol_ratio_10": "volume_liquidity", "vol_ratio_50": "volume_liquidity",
    "dollar_vol_20": "volume_liquidity", "log_dollar_vol_20": "volume_liquidity",
    "pct_from_52w_high": "week52", "pct_from_52w_low": "week52",
    "bar_date": "market_metadata", "sector": "market_metadata", "sector_source": "market_metadata",
    "sector_asof": "market_metadata",
    "bars_available": "data_quality", "has_discontinuity_253": "data_quality", "snapshot_status": "data_quality",
}
_RAW_COLUMNS = ("open", "high", "low", "close", "prev_close", "volume", "bars_available")
_UNIT_OF = {"open": "price", "high": "price", "low": "price", "close": "price", "prev_close": "price",
            "volume": "shares", "bars_available": "count", "bar_date": "date", "sector": "text",
            "sector_source": "text", "sector_asof": "date", "snapshot_status": "text"}


def _manifest_index(manifest: Any) -> Dict[str, Dict[str, Any]]:
    index: Dict[str, Dict[str, Any]] = {}
    sections: List[Any] = []
    if isinstance(manifest, dict):
        sections = [manifest.get("raw_columns") or [], manifest.get("features") or []]
    elif isinstance(manifest, list):
        sections = [manifest]
    for section in sections:
        for f in section:
            if isinstance(f, dict) and f.get("name"):
                index[f["name"]] = {"unit": f.get("unit"), "definition": f.get("definition")}
    return index


def get_snapshot(fetch: Fetch, strategy: Dict[str, Any], snapshot_id: int) -> Optional[Dict[str, Any]]:
    """A T0 feature snapshot, grouped for display. Snapshots are strategy-neutral, but one is served only through a
    strategy that has an observation on it, so a strategy never reads another's research data."""
    _require_available(fetch, strategy)
    rows = fetch("""
        SELECT fs.* FROM feature_snapshot fs
        WHERE fs.id = %(id)s AND EXISTS (SELECT 1 FROM candidate_observation co
                                         WHERE co.snapshot_id = fs.id AND co.strategy_id = %(sid)s)
    """, {"id": snapshot_id, "sid": strategy["id"]})
    if not rows:
        return None
    s = _clean(rows[0])
    reg = fetch("SELECT manifest, manifest_hash, description FROM feature_set_registry WHERE feature_set_version = %s",
                (s["feature_set_version"],))
    index = _manifest_index(reg[0]["manifest"]) if reg else {}
    missing = list(s["missing_features"] or [])
    values: Dict[str, Any] = {c: s[c] for c in _RAW_COLUMNS}
    values.update(s["features"] or {})
    values.update({"bar_date": s["bar_date"], "sector": s["sector"], "sector_source": s["sector_source"],
                   "sector_asof": s["sector_asof"], "snapshot_status": s["snapshot_status"]})

    buckets: Dict[str, List[Dict[str, Any]]] = {k: [] for k, _ in FEATURE_GROUPS}
    buckets["other"] = []
    for name, value in values.items():
        meta = index.get(name, {})
        buckets[_GROUP_OF.get(name, "other")].append({
            "name": name, "value": value, "unit": meta.get("unit") or _UNIT_OF.get(name),
            "definition": meta.get("definition"), "missing": name in missing or value is None})
    groups = [{"key": k, "label": label, "items": buckets[k]} for k, label in FEATURE_GROUPS if buckets[k]]
    if buckets["other"]:
        groups.append({"key": "other", "label": "Other", "items": buckets["other"]})

    obs = fetch("""SELECT co.id, co.direction, co.signal_type, co.session_date FROM candidate_observation co
                   WHERE co.snapshot_id = %s AND co.strategy_id = %s ORDER BY co.direction DESC""",
                (snapshot_id, strategy["id"]))
    return {
        "id": s["id"], "strategy": strategy,
        "identity": {"symbol": s["symbol"], "session_date": s["session_date"], "bar_date": s["bar_date"],
                     "feature_set_version": s["feature_set_version"]},
        "snapshot_status": s["snapshot_status"], "missing_features": missing, "groups": groups,
        "feature_set": {"version": s["feature_set_version"], "manifest_hash": s["manifest_hash"].strip(),
                        "description": reg[0]["description"] if reg else None},
        "provenance": {"content_hash": s["content_hash"].strip(), "code_ref": s["code_ref"],
                       "captured_at": s["captured_at"]},
        "observations": [{"id": o["id"], "direction": DIRECTIONS[o["direction"]], "candidate_class": o["signal_type"],
                          "session_date": o["session_date"]} for o in obs],
    }
