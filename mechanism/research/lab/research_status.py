"""Research observation / readiness status (pure: no database, clock, file or environment access; deterministic).

Answers one owner question without building a dataset by hand: *is the system accumulating genuinely observed, point-in-time-safe history
that could eventually satisfy the existing Slice 5 research-readiness contract -- and if not, what is missing?*

    reproducible  !=  point-in-time correct  !=  vendor correct  !=  predictive edge

This module speaks only about the first two. It never claims an edge, and it does NOT define research eligibility: `dataset_readiness`
(Slice 5) stays the one authority. The contract section here is the output of the SAME shared function (`dataset_readiness.assess_data`,
checks 5-10) applied to a provisional dataset, plus a plain statement of which Slice 5 checks need a real build and are therefore not
evaluated here. Nothing is repaired or defaulted: questionable history is counted and reported as it is.

Input is plain data (aggregates the SELECT-only reader produced); output is one canonical document with its own hash. Everything volatile
(run time, database target, rows that arrived after the cutoff, code identity) lives in a separate context document and never enters the hash.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from research.lab import dataset_contract as C
from research.lab import dataset_readiness as READY
from research.lab import manifest as M
from research.lab.manifest import canonical_hash

STATUS_SCHEMA = "lab_research_status_v1"
DISTINCTION = READY.DISTINCTION

# Declared and printed in every document (not options).
RECENT_SESSIONS = 3                 # a source is `active` when it has a trustworthy observation among its last N due sessions
MIN_SESSIONS_FOR_ESTIMATE = 20      # fewer jointly-observed sessions than this cannot support a rate, so no estimate is produced
MAX_RANGES = 50                     # gap ranges listed per source (the count is always exact)
MAX_EXAMPLES = 5                    # example sessions listed per integrity finding
POLICY = {"recent_sessions": RECENT_SESSIONS, "min_sessions_for_estimate": MIN_SESSIONS_FOR_ESTIMATE, "max_gap_ranges_listed": MAX_RANGES,
          "readiness_thresholds": READY.THRESHOLDS}

PROVENANCE_TABLE_SOURCES = ("market", "sector", "stock_rs")
SESSION_ORDER = ("candidates", "market", "sector", "stock_rs")
SOURCE_TABLE = {"candidates": "candidate_observation / candidate_capture_run", "market": "market_snapshot", "sector": "sector_snapshot",
                "stock_rs": "stock_relative_strength", "labels": "forward_return_label", "catalyst": "market_event_revision / catalyst_classification",
                "first_seen": "source_observation"}

# What writes each source, declared from the repository at Slice 6 (the database cannot say this). `scheduled_forward_collector` is the fact
# that decides whether history can accumulate by itself once the gates are open.
COLLECTORS: Dict[str, Dict[str, Any]] = {
    "candidates": {"writer": "screener hook mechanism/research/observer.py (runs inside the daily pipeline)", "scheduled_forward_collector": True,
                   "gates": ["RESEARCH_CAPTURE_ENABLED=true", "an 'enabled' capture-activation row", "an image that contains the hook"]},
    "market": {"writer": "the Market Intelligence runner (an operator CLI)", "scheduled_forward_collector": False,
               "gates": ["migrations 24/25 applied", "an operator (or a future timer) runs the runner after each session"]},
    "sector": {"writer": "the Market Intelligence runner (an operator CLI)", "scheduled_forward_collector": False,
               "gates": ["migrations 24/25 applied", "an operator (or a future timer) runs the runner after each session"]},
    "stock_rs": {"writer": "the Market Intelligence runner (an operator CLI)", "scheduled_forward_collector": False,
                 "gates": ["migrations 24 and 29 applied", "an operator (or a future timer) runs the runner after each session"]},
    "labels": {"writer": "the manual label runner", "scheduled_forward_collector": False,
               "gates": ["migration 26 applied", "an operator (or a future timer) runs the label runner once a horizon has matured"]},
    "catalyst": {"writer": "none in the repository", "scheduled_forward_collector": False, "gates": ["no live collector exists"]},
    "first_seen": {"writer": "none in the repository", "scheduled_forward_collector": False, "gates": ["no live collector exists"]},
}

INTEGRITY_CATALOGUE = (
    # (id, source, severity, meaning)
    ("candidate_run_not_complete", "candidates", "blocker", "due sessions whose capture is partial / failed / still running and have no complete in-time run (a later complete run clears a session)"),
    ("candidate_run_hash_drift", "candidates", "blocker", "a re-run produced a different payload for an already-captured identity (conflicting observation; not applied)"),
    ("candidate_snapshot_drift", "candidates", "blocker", "an existing feature snapshot's recomputed content differed (conflicting observation; not applied)"),
    ("candidate_rows_late", "candidates", "warning", "candidate rows stamped at or after the session's decision deadline (a backfill, not a forward observation)"),
    ("candidate_rows_backdated", "candidates", "blocker", "candidate rows stamped before their session's UTC day began (impossible availability)"),
    ("candidate_rows_before_activation", "candidates", "blocker", "candidate rows for a session before capture was first enabled"),
    ("candidate_rows_off_calendar", "candidates", "warning", "candidate rows whose session is not a session of the calendar"),
    ("market_rows_late", "market", "warning", "observed market rows stamped at or after the decision deadline"),
    ("market_rows_backdated", "market", "blocker", "market rows stamped before their session's UTC day began"),
    ("market_reconstructed_rows", "market", "info", "reconstructed market rows (never usable as forward history)"),
    ("market_unknown_provenance_rows", "market", "blocker", "market rows whose provenance is neither observed nor reconstructed"),
    ("market_sessions_observed_and_reconstructed", "market", "info", "sessions holding both an observed and a reconstructed row (the reconstruction is shadowed, not used)"),
    ("market_rows_off_calendar", "market", "warning", "market rows whose session is not a session of the calendar"),
    ("sector_rows_late", "sector", "warning", "observed sector rows stamped at or after the decision deadline"),
    ("sector_rows_backdated", "sector", "blocker", "sector rows stamped before their session's UTC day began"),
    ("sector_reconstructed_rows", "sector", "info", "reconstructed sector rows (never usable as forward history)"),
    ("sector_unknown_provenance_rows", "sector", "blocker", "sector rows whose provenance is neither observed nor reconstructed"),
    ("sector_sessions_observed_and_reconstructed", "sector", "info", "sessions holding both an observed and a reconstructed sector row"),
    ("sector_rows_off_calendar", "sector", "warning", "sector rows whose session is not a session of the calendar"),
    ("sector_observed_sector_count_varies", "sector", "warning", "observed sessions whose number of distinct sectors differs from the most common count"),
    ("stock_rs_rows_late", "stock_rs", "warning", "observed relative-strength rows stamped at or after the decision deadline"),
    ("stock_rs_rows_backdated", "stock_rs", "blocker", "relative-strength rows stamped before their session's UTC day began"),
    ("stock_rs_reconstructed_rows", "stock_rs", "info", "reconstructed relative-strength rows (never usable as forward history)"),
    ("stock_rs_unknown_provenance_rows", "stock_rs", "blocker", "relative-strength rows whose provenance is neither observed nor reconstructed"),
    ("stock_rs_sessions_observed_and_reconstructed", "stock_rs", "info", "sessions holding both an observed and a reconstructed relative-strength row"),
    ("stock_rs_rows_off_calendar", "stock_rs", "warning", "relative-strength rows whose session is not a session of the calendar"),
    ("stock_rs_sessions_with_multiple_runs", "stock_rs", "blocker", "observed sessions carrying more than one distinct run content hash (conflicting observations)"),
    ("stock_rs_ok_cells_without_sector", "stock_rs", "warning", "observed 'ok' relative-strength cells for symbols with no sector: they carry sector_pit_safe=false, which Slice 5 check 6 counts"),
    ("stock_rs_ok_cells_sector_map_unsafe", "stock_rs", "blocker", "observed 'ok' relative-strength cells that have a sector but a sector map that is not point-in-time safe"),
    ("labels_computed_before_horizon", "labels", "blocker", "label rows computed before their own horizon session began (impossible availability)"),
    ("labels_off_calendar", "labels", "warning", "label rows whose t0 session is not a session of the calendar"),
)
SCHEMA_GUARANTEES = (
    "duplicate candidate observations: impossible by UNIQUE(strategy_id, symbol, session_date, direction) and first-valid-write-wins",
    "duplicate market / sector / relative-strength rows within one provenance: impossible by their UNIQUE keys (which include provenance)",
    "provenance outside {observed, reconstructed} on market / sector / relative strength: impossible by CHECK constraint (counted anyway; zero expected)",
    "mutation of a captured row: refused by triggers and privileges (a superuser can bypass them; this tool cannot see that)",
)
UNPROVEN = (
    "market and sector stamps (captured_at) and label stamps (computed_at) are writer-settable defaults: a stamp inside the deadline is evidence, not proof",
    "a session with no capture is reported missing; the tool cannot tell a crashed run from a run that was never scheduled",
    "the calendar is the one supplied in the spec; a wrong calendar makes expected-session counts wrong",
    "intraday timing is not modelled (daily decision-deadline model, as in Slice 4/5)",
)


# ------------------------------------------------------------------ small helpers
def _iso(d: Any) -> Optional[str]:
    return d.isoformat() if d is not None else None


def _frac(n: int, d: int) -> Optional[float]:
    return None if d == 0 else round(n / d, 6)


def _ranges(days: Sequence[date], cal_index: Mapping[date, int]) -> Tuple[List[List[str]], bool]:
    """Consecutive calendar sessions collapsed into [first, last] ranges."""
    out: List[List[date]] = []
    for d in sorted(days):
        if out and cal_index[d] == cal_index[out[-1][1]] + 1:
            out[-1][1] = d
        else:
            out.append([d, d])
    return [[_iso(a), _iso(b)] for a, b in out[:MAX_RANGES]], len(out) > MAX_RANGES


def _streaks(flags: Sequence[bool]) -> Dict[str, int]:
    longest = cur = 0
    for f in flags:
        cur = cur + 1 if f else 0
        longest = max(longest, cur)
    return {"current": cur, "longest": longest}


def _day_start(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


# ------------------------------------------------------------------ per-session states
def _provenance_states(rows: Sequence[Mapping[str, Any]]) -> Tuple[Dict[date, str], Dict[str, Any]]:
    by: Dict[date, Dict[str, int]] = {}
    for r in rows:
        a = by.setdefault(r["session"], {"obs_ok": 0, "obs_late": 0, "obs_back": 0, "recon": 0, "unknown": 0})
        if r["provenance"] == "observed":
            a["obs_ok"] += r["n_in_time"] - r["n_backdated"]
            a["obs_back"] += r["n_backdated"]
            a["obs_late"] += r["n_late"]
        elif r["provenance"] == "reconstructed":
            a["recon"] += r["n"]
        else:
            a["unknown"] += r["n"]
    states = {}
    for d, a in by.items():
        states[d] = ("observed" if a["obs_ok"] else "backdated" if a["obs_back"] else "late" if a["obs_late"] else
                     "reconstructed_only" if a["recon"] else "unknown_provenance" if a["unknown"] else "missing")
    return states, {"both": sorted(d for d, a in by.items() if (a["obs_ok"] or a["obs_late"] or a["obs_back"]) and a["recon"])}


def _candidate_states(runs: Sequence[Mapping[str, Any]], rows: Sequence[Mapping[str, Any]]) -> Dict[date, str]:
    back = {r["session"] for r in rows if r["n_backdated"]}
    by: Dict[date, Dict[str, int]] = {}
    for r in runs:
        a = by.setdefault(r["session"], {})
        key = "complete_ok" if r["status"] == "complete" and r["n_in_time"] else "complete_late" if r["status"] == "complete" else r["status"]
        a[key] = a.get(key, 0) + r["n"]
    states = {}
    for d, a in by.items():
        states[d] = ("backdated" if d in back else "observed" if a.get("complete_ok") else "late" if a.get("complete_late") else
                     "partial" if a.get("partial") else "failed" if a.get("failed") else "running" if a.get("running") else "missing")
    return states


# ------------------------------------------------------------------ one source
def _section(name: str, states: Mapping[date, str], cal: Sequence[date], due: Sequence[date], pending: int, expected_pred,
             rows_exist: bool) -> Dict[str, Any]:
    idx = {d: i for i, d in enumerate(cal)}
    observed = [d for d in due if states.get(d) == "observed"]
    first, latest = (observed[0], observed[-1]) if observed else (None, None)
    recent = due[-RECENT_SESSIONS:]
    if first is None:
        state = "never_captured"
    else:
        state = "active" if any(d in recent for d in observed) else "stalled"
    window = [d for d in due if first is not None and d >= first and expected_pred(d)]
    gaps = [d for d in window if states.get(d) != "observed"]
    breakdown: Dict[str, int] = {}
    for d in window:
        s = states.get(d, "missing")
        breakdown[s] = breakdown.get(s, 0) + 1
    ranges, truncated = _ranges(gaps, idx)
    st = _streaks([states.get(d) == "observed" for d in due])
    return {
        "source": name, "table": SOURCE_TABLE[name], "collector": COLLECTORS[name],
        "capture_exists": bool(rows_exist), "capture_state": state, "dormant": state != "active",
        "first_observed_session": _iso(first), "latest_observed_session": _iso(latest),
        "observed_sessions": len(observed),
        "expected_sessions": len(window) if first is not None else None,
        "expected_sessions_note": ("due calendar sessions from the first observed session to the last due session" if first is not None
                                   else "not determinable: nothing has been observed, so there is no start of history to count from"),
        "coverage_since_first_observed": _frac(len(observed), len(window)) if first is not None else None,
        "due_sessions_in_calendar": len(due), "coverage_of_due_calendar": _frac(len(observed), len(due)),
        "pending_sessions_within_grace": pending,
        "missing_sessions": {"count": len(gaps), "ranges": ranges, "ranges_truncated": truncated},
        "session_breakdown_since_first": dict(sorted(breakdown.items())),
        "consecutive_observed_sessions": st,
    }


def _state_source(name: str, summary: Mapping[str, Any]) -> Dict[str, Any]:
    n = summary.get("n", 0)
    return {"source": name, "table": SOURCE_TABLE[name], "collector": COLLECTORS[name], "capture_exists": n > 0,
            "capture_state": "no_scheduled_collector" if not COLLECTORS[name]["scheduled_forward_collector"] else "unknown",
            "dormant": True, "session_based": False,
            "session_based_note": "event-driven: there is no per-session expectation, so no session coverage or gap count exists for this source",
            **{k: summary[k] for k in sorted(summary)}}


# ------------------------------------------------------------------ labels
def _labels_section(rows: Sequence[Mapping[str, Any]], cal: Sequence[date], due: Sequence[date], primary_h: int, maturity: date) -> Dict[str, Any]:
    idx = {d: i for i, d in enumerate(cal)}
    tot = {"candidates": 0, "mature": 0, "final": 0, "void": 0, "other": 0, "unlabelled": 0, "immature": 0}
    for r in rows:
        d = r["session"]
        tot["candidates"] += r["n_candidates"]
        i = idx.get(d)
        mature = i is not None and i + primary_h < len(cal) and cal[i + primary_h] <= maturity
        if not mature:
            tot["immature"] += r["n_candidates"]
            continue
        tot["mature"] += r["n_candidates"]
        tot["final"] += r["n_final"]
        tot["void"] += r["n_void"]
        tot["other"] += r["n_other"]
        tot["unlabelled"] += r["n_unlabelled"]
    return {"source": "labels", "table": SOURCE_TABLE["labels"], "collector": COLLECTORS["labels"],
            "capture_exists": any(r["n_candidates"] - r["n_unlabelled"] > 0 for r in rows), "dormant": True, "session_based": False,
            "primary_horizon_sessions": primary_h, "label_maturity_session": _iso(maturity),
            "candidates_in_time": tot["candidates"], "mature_candidates": tot["mature"], "final_labelled": tot["final"], "void": tot["void"],
            "other_status": tot["other"], "mature_unlabelled": tot["unlabelled"], "not_yet_mature": tot["immature"],
            "final_label_fraction_of_mature": _frac(tot["final"], tot["mature"]),
            "note": "labels are produced after the horizon matures by a manual runner; 'unlabelled' means no label row exists (never zero-filled)"}


# ------------------------------------------------------------------ integrity
def _integrity(inp: Mapping[str, Any], cal_set: set, states: Mapping[str, Mapping[date, str]], both: Mapping[str, List[date]],
               runs_not_complete: List[date]) -> List[Dict[str, Any]]:
    counts: Dict[str, Tuple[int, List[date]]] = {}

    def put(cid: str, n: int, ex: Sequence[date] = ()) -> None:
        counts[cid] = (n, sorted(set(ex))[:MAX_EXAMPLES])

    crow, cruns = inp["candidates"]["rows"], inp["candidates"]["runs"]
    put("candidate_run_not_complete", len(runs_not_complete), runs_not_complete)
    put("candidate_run_hash_drift", sum(r["hash_drift"] for r in cruns), [r["session"] for r in cruns if r["hash_drift"]])
    put("candidate_snapshot_drift", sum(r["snapshot_drift"] for r in cruns), [r["session"] for r in cruns if r["snapshot_drift"]])
    put("candidate_rows_late", sum(r["n_late"] for r in crow), [r["session"] for r in crow if r["n_late"]])
    put("candidate_rows_backdated", sum(r["n_backdated"] for r in crow), [r["session"] for r in crow if r["n_backdated"]])
    put("candidate_rows_before_activation", sum(r["n_before_activation"] for r in crow), [r["session"] for r in crow if r["n_before_activation"]])
    put("candidate_rows_off_calendar", sum(r["n"] for r in crow if r["session"] not in cal_set), [r["session"] for r in crow if r["session"] not in cal_set])
    for s in PROVENANCE_TABLE_SOURCES:
        rows = inp[s]["rows"]
        obs = [r for r in rows if r["provenance"] == "observed"]
        put(f"{s}_rows_late", sum(r["n_late"] for r in obs), [r["session"] for r in obs if r["n_late"]])
        put(f"{s}_rows_backdated", sum(r["n_backdated"] for r in rows), [r["session"] for r in rows if r["n_backdated"]])
        put(f"{s}_reconstructed_rows", sum(r["n"] for r in rows if r["provenance"] == "reconstructed"),
            [r["session"] for r in rows if r["provenance"] == "reconstructed"])
        put(f"{s}_unknown_provenance_rows", sum(r["n"] for r in rows if r["provenance"] not in ("observed", "reconstructed")),
            [r["session"] for r in rows if r["provenance"] not in ("observed", "reconstructed")])
        put(f"{s}_sessions_observed_and_reconstructed", len(both[s]), both[s])
        put(f"{s}_rows_off_calendar", sum(r["n"] for r in rows if r["session"] not in cal_set), [r["session"] for r in rows if r["session"] not in cal_set])
    sec_obs = [r for r in inp["sector"]["rows"] if r["provenance"] == "observed" and r["n_in_time"] - r["n_backdated"] > 0]
    sizes: Dict[date, int] = {}
    for r in sec_obs:
        sizes[r["session"]] = sizes.get(r["session"], 0) + r["n_sectors"]
    if sizes:
        freq: Dict[int, int] = {}
        for v in sizes.values():
            freq[v] = freq.get(v, 0) + 1
        common = sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        odd = [d for d, v in sizes.items() if v != common]
    else:
        odd = []
    put("sector_observed_sector_count_varies", len(odd), odd)
    rs = inp["stock_rs"]["rows"]
    multi = [r["session"] for r in rs if r["provenance"] == "observed" and r["n_run_hashes"] > 1]
    put("stock_rs_sessions_with_multiple_runs", len(multi), multi)
    put("stock_rs_ok_cells_without_sector", sum(r["n_ok_no_sector"] for r in rs if r["provenance"] == "observed"),
        [r["session"] for r in rs if r["provenance"] == "observed" and r["n_ok_no_sector"]])
    put("stock_rs_ok_cells_sector_map_unsafe", sum(r["n_ok_sector_unsafe"] for r in rs if r["provenance"] == "observed"),
        [r["session"] for r in rs if r["provenance"] == "observed" and r["n_ok_sector_unsafe"]])
    lab = inp["labels"]
    put("labels_computed_before_horizon", lab["n_computed_before_horizon"], lab["computed_before_horizon_examples"])
    put("labels_off_calendar", sum(r["n_candidates"] for r in lab["rows"] if r["session"] not in cal_set), [r["session"] for r in lab["rows"] if r["session"] not in cal_set])
    out = []
    for cid, src, sev, meaning in INTEGRITY_CATALOGUE:
        n, ex = counts[cid]
        out.append({"id": cid, "source": src, "severity": sev, "count": int(n), "meaning": meaning, "example_sessions": [_iso(d) for d in ex]})
    return out


# ------------------------------------------------------------------ estimate
def _estimate(joint_sessions: int, joint_rows: int, required_rows: int, blockers: List[str], horizon: int, embargo_gap: int,
              data_checks_pass: bool = False) -> Dict[str, Any]:
    base = {"unit": "trading sessions", "min_sessions_for_estimate": MIN_SESSIONS_FOR_ESTIMATE, "required_final_labelled_rows_all_splits": required_rows,
            "jointly_observed_sessions": joint_sessions, "candidate_rows_in_jointly_observed_sessions": joint_rows}
    if blockers:
        return {**base, "remaining_sessions": None, "reason": "not estimable: no observed history is accumulating for " + ", ".join(blockers)
                + " -- waiting does not change that"}
    if joint_sessions < MIN_SESSIONS_FOR_ESTIMATE:
        return {**base, "remaining_sessions": None, "reason": f"not estimable: {joint_sessions} jointly observed session(s) cannot support a rate "
                f"(need {MIN_SESSIONS_FOR_ESTIMATE}); no extrapolation is made"}
    if data_checks_pass:
        return {**base, "remaining_sessions": 0, "reason": "every data-derived Slice 5 check already passes on the observed history, so no further history "
                "is required by them; a real build still decides eligibility and the structural findings above still apply"}
    rate = joint_rows / joint_sessions
    if rate <= 0:
        return {**base, "remaining_sessions": None, "reason": "not estimable: the observed history contains no candidate rows, so there is no rate"}
    short = max(0, required_rows - joint_rows)
    accrual = math.ceil(short / rate)
    total = accrual + horizon + embargo_gap
    return {**base, "remaining_sessions": total, "components": {"sessions_to_accrue_rows": accrual, "label_maturity_lag_sessions": horizon,
                                                                 "embargo_and_purge_between_splits_sessions": embargo_gap},
            "rate_candidate_rows_per_session": round(rate, 6),
            "reason": "an extrapolation of the observed per-session candidate rate, assuming every required source keeps being observed on every "
                      "session; it is a lower bound on sessions (a gap, a thin month or a failed 90% coverage gate extends it) and never a calendar date"}


# ------------------------------------------------------------------ the document
def build_status(inp: Mapping[str, Any]) -> Dict[str, Any]:
    """`inp` is the reader's output (see research_status_reader.collect). Pure and deterministic: same input -> same canonical document and hash."""
    cfg: C.DatasetConfig = inp["config"]
    cutoff: datetime = inp["cutoff"].astimezone(timezone.utc)
    cal: List[date] = sorted(inp["calendar"])
    grace = cfg.availability_grace_days
    cal_set = set(cal)
    due = [d for d in cal if C.decision_deadline(d, grace) <= cutoff]
    due_set = set(due)
    pending = sum(1 for d in cal if d not in due_set and d <= cutoff.date())
    idx = {d: i for i, d in enumerate(cal)}
    enabled = cfg.enabled()
    inp = dict(inp)
    inp["due_set"] = due_set

    # ---- per-session states
    activation = sorted(inp["candidates"]["activation"], key=lambda a: a["effective_from_session"])

    def cand_expected(d: date) -> bool:
        cur = None
        for a in activation:
            if a["effective_from_session"] <= d:
                cur = a["state"]
        return cur == "enabled"

    states: Dict[str, Dict[date, str]] = {"candidates": _candidate_states(inp["candidates"]["runs"], inp["candidates"]["rows"])}
    both: Dict[str, List[date]] = {}
    for s in PROVENANCE_TABLE_SOURCES:
        states[s], extra = _provenance_states(inp[s]["rows"])
        both[s] = extra["both"]

    sources: List[Dict[str, Any]] = []
    sources.append(_section("candidates", states["candidates"], cal, due, pending, cand_expected,
                            bool(inp["candidates"]["rows"] or inp["candidates"]["runs"])))
    required = ["candidates"]
    for s in PROVENANCE_TABLE_SOURCES:
        if enabled.get(s):
            sources.append(_section(s, states[s], cal, due, pending, lambda d: True, bool(inp[s]["rows"])))
            required.append(s)
        else:
            sources.append({"source": s, "table": SOURCE_TABLE[s], "enabled_in_config": False, "capture_state": "not_enabled", "dormant": True,
                            "collector": COLLECTORS[s], "note": "the spec config does not enable this source, so Slice 5 does not read it either"})
    sources.append(_labels_section(inp["labels"]["rows"], cal, due, cfg.primary_horizon, inp["maturity_session"]))
    sources.append(_state_source("catalyst", inp["catalyst"]))
    sources.append(_state_source("first_seen", inp["first_seen"]))
    for s in sources:
        if s["source"] in PROVENANCE_TABLE_SOURCES and "enabled_in_config" not in s:
            s["reconstructed_rows"] = sum(r["n"] for r in inp[s["source"]]["rows"] if r["provenance"] == "reconstructed")
            s["unknown_provenance_rows"] = sum(r["n"] for r in inp[s["source"]]["rows"] if r["provenance"] not in ("observed", "reconstructed"))
            s["late_observed_rows"] = sum(r["n_late"] for r in inp[s["source"]]["rows"] if r["provenance"] == "observed")
        if s["source"] == "candidates":
            s["reconstructed_rows"], s["unknown_provenance_rows"] = None, None
            s["provenance_note"] = "candidate observations carry no provenance column: a forward observation is a complete, in-time capture run"
            s["late_observed_rows"] = sum(r["n_late"] for r in inp["candidates"]["rows"])
            s["activation"] = [{"state": a["state"], "effective_from_session": _iso(a["effective_from_session"])} for a in activation]
            s["capture_runs"] = {k: v for k, v in sorted(_run_totals(inp["candidates"]["runs"]).items())}

    # ---- joint history over the required sources
    req_states = [states[s] for s in required]
    joint = [all(st.get(d) == "observed" for st in req_states) for d in due]
    firsts = []
    for s in required:
        o = [d for d in due if states[s].get(d) == "observed"]
        firsts.append(o[0] if o else None)
    floor = max(firsts) if all(f is not None for f in firsts) else None
    joint_days = [d for d, f in zip(due, joint) if f]
    window = [d for d in due if floor is not None and d >= floor]
    jr = {d: 0 for d in joint_days}
    for r in inp["candidates"]["rows"]:
        if r["session"] in jr:
            jr[r["session"]] += r["n_in_time"] - r["n_backdated"]
    history = {
        "required_sources": required,
        "jointly_observed_sessions": len(joint_days),
        "joint_observed_floor_session": _iso(floor),
        "joint_floor_note": ("the latest of each required source's first observed session: no jointly observed history can start earlier"
                             if floor is not None else "not determinable: at least one required source has never been observed"),
        "sessions_since_floor": len(window) if floor is not None else None,
        "joint_coverage_since_floor": _frac(len(joint_days), len(window)) if floor is not None else None,
        "consecutive_jointly_observed_sessions": _streaks(joint),
        "reconstructed_rows_per_required_source": {s: next((x["reconstructed_rows"] for x in sources if x["source"] == s), None) for s in required},
        "authoritative_earliest_trustworthy_pit_date": inp["contract"].get("earliest_trustworthy_pit_date"),
    }

    # ---- contract (Slice 5 stays authoritative)
    ct = inp["contract"]
    plan = inp["plan"]
    required_rows = sum(max(READY.MIN_FINAL_LABELS[s], cfg.min_sample) for s in M.SPLITS)
    contract: Dict[str, Any] = {
        "authority": "Slice 5 dataset_readiness: model_research_eligible is decided only by a real build. This section reports the data-derived "
                     "checks (5-10) of that same function on a PROVISIONAL dataset assembled from the database under the spec, and says which checks "
                     "need a real build. It never states eligibility.",
        "evaluated": bool(ct["evaluated"]),
    }
    failures: List[Dict[str, Any]] = []
    if ct["evaluated"]:
        contract.update({"provisional_rows": ct["provisional_rows"], "data_checks": ct["data_checks"],
                         "data_checks_all_pass": all(c["passed"] for c in ct["data_checks"]),
                         "earliest_trustworthy_pit_date": ct["earliest_trustworthy_pit_date"], "per_source_coverage": ct["coverage"],
                         "label_maturity": ct["label_maturity"], "sample": ct["sample"],
                         "sector_pit_unsafe_cells": ct["sector_unsafe_cells"]})
        for c in ct["data_checks"]:
            if not c["passed"]:
                failures.append({"id": c["id"], "scope": "slice5_data_check", "detail": c["detail"]})
    else:
        contract.update({"not_evaluated_reason": ct["reason"], "data_checks_all_pass": False})
        failures.append({"id": "slice5_data_checks_not_evaluable", "scope": "slice5_data_check", "detail": ct["reason"]})
    policy_ok = cfg.reconstructed_policy == "exclude"
    contract["build_level_checks"] = {
        "reconstructed_policy_is_exclude": {"passed": policy_ok, "detail": f"reconstructed_policy is '{cfg.reconstructed_policy}'"},
        "not_evaluated_here_need_a_real_build": ["audit_passed", "inputs_verified", "code_identity_exact", "test_split_unevaluated"],
    }
    if not policy_ok:
        failures.append({"id": "reconstructed_policy_is_exclude", "scope": "slice5_build_check", "detail": f"reconstructed_policy is '{cfg.reconstructed_policy}'"})

    # ---- failures that history alone explains
    for s in sources:
        if s["source"] in required and s["capture_state"] != "active":
            failures.append({"id": f"{s['source']}_capture_{s['capture_state']}", "scope": "capture",
                             "detail": f"{s['source']} has no trustworthy observation among the last {RECENT_SESSIONS} due sessions"
                                       if s["capture_state"] == "stalled" else f"{s['source']} has never been observed"})
    state_of = {x["source"]: x.get("capture_state") for x in sources}
    notes: List[Dict[str, str]] = []
    for s in [s for s in required if not COLLECTORS[s]["scheduled_forward_collector"]]:
        entry = {"id": f"{s}_no_scheduled_collector", "scope": "structural",
                 "detail": f"{s} is written only by {COLLECTORS[s]['writer']}; nothing schedules it, so history accumulates only if someone runs it"}
        # a source that IS being observed is evidence someone runs it: still fragile, but not what blocks the contract today
        (notes if state_of.get(s) == "active" else failures).append(entry)
    if not COLLECTORS["labels"]["scheduled_forward_collector"]:
        entry = {"id": "labels_no_scheduled_collector", "scope": "structural",
                 "detail": "labels are written only by a manual runner; without them no candidate becomes a final-labelled row"}
        (notes if contract.get("evaluated") and contract.get("data_checks_all_pass") else failures).append(entry)

    integ = _integrity(inp, cal_set, states, both, sorted(d for d in due if states["candidates"].get(d) in ("partial", "failed", "running")))
    for f in integ:
        if f["severity"] == "blocker" and f["count"]:
            failures.append({"id": f["id"], "scope": "integrity", "detail": f"{f['count']} -- {f['meaning']}"})

    blockers = [s for s in required if next(x for x in sources if x["source"] == s)["capture_state"] != "active"]
    embargo_gap = 2 * (plan["embargo_sessions"] + plan["purge_sessions"])
    estimate = _estimate(len(joint_days), sum(jr.values()), required_rows, blockers, cfg.primary_horizon, embargo_gap,
                         bool(contract.get("data_checks_all_pass")))

    doc: Dict[str, Any] = {
        "schema": STATUS_SCHEMA, "distinction": DISTINCTION, "predictive_edge_claim": "none",
        "scope": {"strategy": {"key": cfg.strategy_key, "version": cfg.strategy_version}, "knowledge_cutoff_at": cutoff,
                  "availability_grace_days": grace, "primary_horizon": cfg.primary_horizon, "reconstructed_policy": cfg.reconstructed_policy,
                  "feature_pins": {"market": cfg.market_feature_set_version, "sector": cfg.sector_feature_set_version,
                                   "stock_rs": None if cfg.stock_rs is None else {"model_version": cfg.stock_rs.model_version,
                                                                                   "feature_set_version": cfg.stock_rs.feature_set_version,
                                                                                   "horizon_sessions": cfg.stock_rs.horizon_sessions}},
                  "calendar": {"sessions": len(cal), "first": _iso(cal[0]), "last": _iso(cal[-1]), "source": inp["calendar_source"],
                               "due_sessions": len(due), "latest_due_session": _iso(due[-1] if due else None),
                               "pending_sessions_within_grace": pending},
                  "universe": inp["universe"], "windows": plan["windows"]},
        "sources": sources, "history": history, "integrity": integ, "contract": contract,
        "readiness_failures": failures, "structural_notes": notes, "remaining_history_estimate": estimate,
        "policy": POLICY, "schema_guarantees": list(SCHEMA_GUARANTEES), "unproven": list(UNPROVEN),
    }
    doc["status_hash"] = canonical_hash(doc)
    return doc


def _run_totals(runs: Sequence[Mapping[str, Any]]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for r in runs:
        out[r["status"]] = out.get(r["status"], 0) + r["n"]
    return out


def verify_status_hash(doc: Mapping[str, Any]) -> bool:
    return canonical_hash({k: v for k, v in doc.items() if k != "status_hash"}) == doc.get("status_hash")


# ------------------------------------------------------------------ text
def render_text(d: Mapping[str, Any]) -> str:
    sc = d["scope"]
    out = [f"RESEARCH OBSERVATION STATUS  ({d['schema']})", f"  {d['distinction']}",
           f"  status_hash {d['status_hash']}   predictive edge claimed: {d['predictive_edge_claim']}", "",
           f"strategy {sc['strategy']['key']} {sc['strategy']['version']}   cutoff {sc['knowledge_cutoff_at']}   grace {sc['availability_grace_days']}d   "
           f"primary horizon {sc['primary_horizon']}   reconstructed policy {sc['reconstructed_policy']}",
           f"calendar {sc['calendar']['sessions']} sessions {sc['calendar']['first']}..{sc['calendar']['last']} ({sc['calendar']['source']}); "
           f"{sc['calendar']['due_sessions']} due (latest {sc['calendar']['latest_due_session']}), {sc['calendar']['pending_sessions_within_grace']} within grace",
           f"universe {sc['universe']['n_symbols']} symbols ({sc['universe']['rule']})", "", "SOURCES"]
    for s in d["sources"]:
        if s["source"] in PROVENANCE_TABLE_SOURCES + ("candidates",) and "observed_sessions" in s:
            cov = "n/a" if s["coverage_since_first_observed"] is None else f"{s['coverage_since_first_observed']:.1%}"
            out += [f"  {s['source']:<10} {s['capture_state'].upper():<14} observed {s['observed_sessions']:>4}  expected "
                    f"{'n/a' if s['expected_sessions'] is None else s['expected_sessions']:>4}  coverage {cov:>6}  first {s['first_observed_session']}  "
                    f"latest {s['latest_observed_session']}  missing {s['missing_sessions']['count']}  streak now {s['consecutive_observed_sessions']['current']} "
                    f"/ best {s['consecutive_observed_sessions']['longest']}"
                    + (f"  reconstructed {s['reconstructed_rows']}  unknown-prov {s['unknown_provenance_rows']}  late {s['late_observed_rows']}"
                       if s["reconstructed_rows"] is not None else f"  late rows {s['late_observed_rows']}")]
            if s["missing_sessions"]["count"]:
                out.append("             gaps: " + ", ".join(a if a == b else f"{a}..{b}" for a, b in s["missing_sessions"]["ranges"])
                           + (" ..." if s["missing_sessions"]["ranges_truncated"] else ""))
        elif s["source"] == "labels":
            out.append(f"  labels     candidates {s['candidates_in_time']}  mature {s['mature_candidates']}  final {s['final_labelled']}  void {s['void']}  "
                       f"unlabelled-mature {s['mature_unlabelled']}  not-yet-mature {s['not_yet_mature']}")
        elif "n" in s:
            out.append(f"  {s['source']:<10} {s['capture_state']:<22} rows {s['n']}  first {s.get('first_stamp_session')}  latest {s.get('latest_stamp_session')}")
        else:
            out.append(f"  {s['source']:<10} {s['capture_state']}")
    h = d["history"]
    out += ["", "JOINT HISTORY (candidates + every enabled provenance source observed on the same session)",
            f"  jointly observed sessions {h['jointly_observed_sessions']}   floor {h['joint_observed_floor_session']}   coverage since floor "
            f"{h['joint_coverage_since_floor']}   consecutive now {h['consecutive_jointly_observed_sessions']['current']} / best "
            f"{h['consecutive_jointly_observed_sessions']['longest']}",
            f"  authoritative earliest trustworthy PIT date (Slice 5 rule): {h['authoritative_earliest_trustworthy_pit_date'] or 'not determinable'}"]
    c = d["contract"]
    out += ["", "SLICE 5 CONTRACT (data-derived checks on a provisional dataset; eligibility is decided only by a real build)"]
    if c["evaluated"]:
        for k in c["data_checks"]:
            out.append(f"  [{'pass' if k['passed'] else 'FAIL'}] {k['id']}: {k['detail']}")
        out.append("  sample (final-labelled primary-horizon rows): " + ", ".join(f"{k} {v['final_labelled_rows']}/{v['required']}" for k, v in c["sample"].items()))
    else:
        out.append(f"  not evaluated: {c['not_evaluated_reason']}")
    out.append("  needs a real build (not evaluated here): " + ", ".join(c["build_level_checks"]["not_evaluated_here_need_a_real_build"]))
    out += ["", "INTEGRITY (reported, never repaired)"]
    for f in d["integrity"]:
        if f["count"]:
            out.append(f"  [{f['severity']:<7}] {f['id']}: {f['count']}  e.g. {', '.join(f['example_sessions'])}")
    if not any(f["count"] for f in d["integrity"]):
        out.append("  no finding on any of the " + str(len(d["integrity"])) + " checks")
    e = d["remaining_history_estimate"]
    out += ["", "REMAINING HISTORY (sessions, never a date)",
            f"  {'about ' + str(e['remaining_sessions']) + ' sessions at least' if e['remaining_sessions'] is not None else 'no estimate'} -- {e['reason']}",
            "", "READINESS FAILURES (what keeps the Slice 5 contract from passing)"]
    out += [f"  - [{f['scope']}] {f['id']}: {f['detail']}" for f in d["readiness_failures"]] or ["  none"]
    if d["structural_notes"]:
        out += ["", "STRUCTURAL NOTES (history is accumulating today, but depends on a manual step -- not a current blocker)"]
        out += [f"  - {f['id']}: {f['detail']}" for f in d["structural_notes"]]
    out += ["", "NOT PROVEN BY THIS STATUS"] + [f"  - {u}" for u in d["unproven"]]
    return "\n".join(out) + "\n"
