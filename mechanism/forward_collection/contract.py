"""The forward-collection contract (PURE: no database, clock, file, network or environment access).

One explicit session is processed by an ordered list of steps. A session is COMPLETE only when every required step succeeded AND the read-back
verification agrees with the database; nothing is ever reported as done because something else was absent. This module owns the vocabulary
(steps, outcomes, verdicts), the per-source collector contract, the pure session classifier, the declared scheduler design and the
owner-decided no-sector policy (Option B). It does not decide what any research value means: the writers and the Slice 5/6 contracts own that.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from research.lab.dataset_contract import decision_deadline

SCHEMA = "forward_collection_v1"

# ------------------------------------------------------------------ steps, outcomes, verdicts
STEP_CAPTURE = "capture_verify"      # read-only: did the screener's capture hook record this session? (candidates are written INSIDE the screener)
STEP_SECTOR_HISTORY = "sector_history_verify"   # read-only: did the ONE authoritative sector writer (the fundamentals updater) refresh the session's universe in time?
STEP_OBSERVE = "observe"             # market + sector + per-stock relative strength, atomically, via the Market Intelligence runner
STEP_LABELS = "labels"               # fwd_v1 labels for every candidate whose horizon has matured (catches up by itself)
STEP_VERIFY = "verify"               # read-only read-back: the rows exist, are complete, and were stamped before the decision deadline
STEP_ORDER: Tuple[str, ...] = (STEP_CAPTURE, STEP_SECTOR_HISTORY, STEP_OBSERVE, STEP_LABELS, STEP_VERIFY)

OK, ALREADY, FAILED, SKIPPED, REFUSED, DRY = "ok", "already_present", "failed", "skipped_dependency", "refused", "dry_run"
OUTCOMES = (OK, ALREADY, FAILED, SKIPPED, REFUSED, DRY)
SUCCESS = (OK, ALREADY)

COMPLETE, INCOMPLETE, MISSED, LOCKED, DRY_RUN = "COMPLETE", "INCOMPLETE", "MISSED", "LOCKED", "DRY_RUN"
VERDICTS = (COMPLETE, INCOMPLETE, MISSED, LOCKED, DRY_RUN)

EXIT_COMPLETE, EXIT_INCOMPLETE, EXIT_LOCKED, EXIT_MISSED, EXIT_REFUSED = 0, 2, 3, 4, 5
EXIT_CODES = {COMPLETE: EXIT_COMPLETE, DRY_RUN: EXIT_COMPLETE, INCOMPLETE: EXIT_INCOMPLETE, LOCKED: EXIT_LOCKED, MISSED: EXIT_MISSED}
ALERT_EXIT_CODES = (EXIT_INCOMPLETE, EXIT_MISSED, EXIT_REFUSED)   # LOCKED alone is not an alert: the second fire of the day re-checks

REASON_PAST_DEADLINE = "past_decision_deadline"
REASON_NOT_LATEST = "session_is_not_the_newest_loaded_session"
REASON_NOT_LOADED = "session_not_available"
REASON_SNAPSHOT_PARTIAL = "committed_snapshot_partial"            # some but not all of the session's observed rows exist: it cannot be completed, only investigated
REASON_SNAPSHOT_INCONSISTENT = "committed_snapshot_inconsistent"    # the stored rows contradict each other or fall outside the allowed window
REASON_SNAPSHOT_PROBLEMS = (REASON_SNAPSHOT_PARTIAL, REASON_SNAPSHOT_INCONSISTENT)

# the writers' versions the dataset spec must name (pinned against the writers themselves by a test, never trusted blindly)
COLLECTOR_VERSIONS: Dict[str, Any] = {
    "market_feature_set_version": "mi_v2", "sector_feature_set_version": "mi_v2",
    "stock_rs": {"model_version": "rs_v1", "feature_set_version": "mi_v2", "horizons": (5, 20, 60)},
    "label_version": "fwd_v1", "label_methodology_version": "fwd_v1.m1", "label_horizons": (1, 3, 5, 10, 20, 60),
}

# ------------------------------------------------------------------ the per-source collector contract
# `step` is the step that writes the source; None means "no collector exists". A source ENABLED in a dataset spec with no collector is a
# blocker for activation: it is never silently treated as optional (it is optional only by being absent from the spec).
SOURCE_CONTRACT: Dict[str, Dict[str, Any]] = {
    "candidates": {"step": STEP_CAPTURE, "writer": "the screener's capture hook (research.observer, inside the daily pipeline)", "orchestrated": False,
                   "catch_up": False, "why": "candidates exist only inside the screener run of their own session; this collector can verify them, never create them"},
    "market": {"step": STEP_OBSERVE, "writer": "the Market Intelligence run() writer (provenance=observed)", "orchestrated": True, "catch_up": False,
               "why": "an observed row requires the session to be the newest loaded bar: a missed session can never be observed later"},
    "breadth": {"step": STEP_OBSERVE, "writer": "the Market Intelligence run() writer (rides the market snapshot)", "orchestrated": True, "catch_up": False,
                "why": "stored inside the market snapshot"},
    "sector": {"step": STEP_OBSERVE, "writer": "the Market Intelligence run() writer (provenance=observed)", "orchestrated": True, "catch_up": False,
               "why": "written in the same transaction as the market row"},
    "stock_rs": {"step": STEP_OBSERVE, "writer": "the Market Intelligence run() writer (with_stock_rs=True)", "orchestrated": True, "catch_up": False,
                 "why": "computed from the same panel and the same PIT-evidenced sector map as the market row, in the same transaction"},
    "labels": {"step": STEP_LABELS, "writer": "research.labels.runner.run (fwd_v1)", "orchestrated": True, "catch_up": True,
               "why": "a label depends only on bars up to its own horizon session, so any later run writes the same value"},
    "catalyst": {"step": None, "writer": "none in the repository", "orchestrated": False, "catch_up": False,
                 "why": "no live catalyst collector exists; a spec that enables it cannot accumulate history"},
    "first_seen": {"step": None, "writer": "none in the repository", "orchestrated": False, "catch_up": False,
                   "why": "no live first-seen collector exists; a spec that enables it cannot accumulate history"},
    "sector_history": {"step": STEP_SECTOR_HISTORY, "writer": "the fundamentals updater's flag-gated sector recorder (outside this collector, default OFF)",
                       "orchestrated": False, "catch_up": False, "verified_by_collector": True,
                       "why": "forward sector history can only be recorded at the moment the vendor answers, by the one authoritative writer; this "
                              "collector VERIFIES that the writer refreshed the session's universe in time (it can neither create nor back-fill the "
                              "history), so a dataset that enables the source has a dependency that can fail visibly instead of a blocker"},
}


def spec_source_blockers(enabled: Mapping[str, bool]) -> List[Dict[str, str]]:
    """Every source the dataset spec ENABLES that has no collector (or is unknown). `enabled` is DatasetConfig.enabled()."""
    out: List[Dict[str, str]] = []
    for name in sorted(enabled):
        if not enabled[name]:
            continue
        c = SOURCE_CONTRACT.get(name)
        if c is None:
            out.append({"source": name, "problem": "enabled source is unknown to the collector contract"})
        elif c["step"] is None:
            out.append({"source": name, "problem": f"enabled in the dataset spec but {c['writer']}: remove it from the spec (optional only by being "
                                                  f"absent) or build the collector first. {c['why']}"})
    return out


def spec_version_blockers(market_fsv: Optional[str], sector_fsv: Optional[str], rs: Optional[Tuple[str, str, int]], label_version: str,
                          methodology: str, label_horizons: Sequence[int]) -> List[str]:
    """The collector writes ONE set of versions; a spec naming any other can never find its rows (it would read an empty source forever)."""
    v, out = COLLECTOR_VERSIONS, []
    if market_fsv is not None and market_fsv != v["market_feature_set_version"]:
        out.append(f"spec market feature_set_version {market_fsv!r} is not the one the collector writes ({v['market_feature_set_version']!r})")
    if sector_fsv is not None and sector_fsv != v["sector_feature_set_version"]:
        out.append(f"spec sector feature_set_version {sector_fsv!r} is not the one the collector writes ({v['sector_feature_set_version']!r})")
    if rs is not None:
        model, fsv, hz = rs
        want = v["stock_rs"]
        if model != want["model_version"] or fsv != want["feature_set_version"] or hz not in want["horizons"]:
            out.append(f"spec stock_rs {rs} is not what the collector writes ({want['model_version']!r}, {want['feature_set_version']!r}, "
                       f"horizon in {want['horizons']})")
    if label_version != v["label_version"] or methodology != v["label_methodology_version"]:
        out.append(f"spec label {label_version!r}/{methodology!r} is not what the label runner writes ({v['label_version']!r}/{v['label_methodology_version']!r})")
    stray = [h for h in label_horizons if h not in v["label_horizons"]]
    if stray:
        out.append(f"spec label horizons {stray} are not produced by the label runner {v['label_horizons']}")
    return out


# ------------------------------------------------------------------ the no-sector policy (OWNER DECISION: Option B)
# Decided by the owner for the lab (Slice 8): a symbol with no point-in-time-safe sector STAYS in the universe; its sector-relative RS value is
# unavailable (NULL, with the explicit state `no_sector`), never 0, never a market-relative substitute, and the symbol is never excluded.
NO_SECTOR_POLICY = "B_null_sector_relative"
NO_SECTOR_OPTIONS: Dict[str, Dict[str, str]] = {
    "A_exclude": {"what": "drop symbols without a PIT-safe sector from the relative-strength rows",
                  "verdict": "REJECTED: the writer's never-omit rule exists so an omitted row cannot be mistaken for a stock outside the universe; "
                             "it also silently changes the universe and every coverage figure"},
    "B_null_sector_relative": {"what": "keep the symbol; its sector-relative field is unavailable (NULL) with the explicit dataset state `no_sector`; the "
                                       "candidate stays valid, only the sector-relative strength is unavailable",
                               "verdict": "ADOPTED (owner decision, Slice 8): the writer already stores such a row this way (vs_sector_pp NULL, "
                                          "sector_pit_safe False); the dataset contract (lab_dataset_v2), audit and readiness now treat that NULL as a "
                                          "legitimate unavailable value and still fail any NON-NULL sector-relative value that is not backed by an "
                                          "observed, fresh, point-in-time-safe sector"},
    "C_market_relative_fallback": {"what": "substitute a market-relative value for the missing sector-relative one",
                                   "verdict": "REJECTED: changes the meaning of the existing RS feature and would mix two definitions in one column"},
}


def no_sector_policy_resolved() -> bool:
    return NO_SECTOR_POLICY in ("B_null_sector_relative",)


# ------------------------------------------------------------------ step / session results
def _clip(s: Any, n: int = 300) -> str:
    s = str(s)
    return s if len(s) <= n else s[:n] + "..."


@dataclass
class StepResult:
    name: str
    outcome: str
    detail: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None            # exception class and a clipped message: never a connection string or a secret
    attempts: int = 1

    def ok(self) -> bool:
        return self.outcome in SUCCESS

    def stable(self) -> Dict[str, Any]:
        return {"name": self.name, "outcome": self.outcome, "detail": self.detail, "error_class": (self.error or "").split(":")[0] or None}

    def to_dict(self) -> Dict[str, Any]:
        d = self.stable()
        d["error"] = _clip(self.error) if self.error else None
        d["attempts"] = self.attempts
        return d


@dataclass
class SessionReport:
    session: date
    verdict: str
    steps: List[StepResult]
    missing: List[str]
    next_action: str
    applied: bool
    grace_days: int
    deadline: datetime

    def exit_code(self) -> int:
        if self.verdict == DRY_RUN and any(s.outcome in (FAILED, REFUSED) for s in self.steps):
            return EXIT_INCOMPLETE          # a dry run that already sees a problem must not look healthy
        return EXIT_CODES[self.verdict]

    def stable(self) -> Dict[str, Any]:
        return {"schema": SCHEMA, "session": self.session.isoformat(), "verdict": self.verdict, "applied": self.applied,
                "grace_days": self.grace_days, "deadline": self.deadline.isoformat(), "missing": list(self.missing),
                "steps": [s.stable() for s in self.steps]}

    def report_hash(self) -> str:
        return hashlib.sha256(json.dumps(self.stable(), sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        d = self.stable()
        d["steps"] = [s.to_dict() for s in self.steps]
        d["next_action"] = self.next_action
        d["report_hash"] = self.report_hash()
        return d


NEXT_ACTIONS = {
    STEP_CAPTURE: "none by this collector: candidates are written only by the screener of that session; the gap stays visible in the research status",
    STEP_SECTOR_HISTORY: "none by this collector: only the fundamentals updater (recorder flag ON) writes the history. Make it run (a full-universe "
                         "refresh) before the session's decision deadline; the next fire re-checks. After the deadline the gap is permanent for this session",
    STEP_OBSERVE: "run the SAME session again before its decision deadline (the write is atomic: nothing partial exists, a re-run is idempotent)",
    STEP_LABELS: "run again: labels are idempotent and a later run catches up every matured horizon",
    STEP_VERIFY: "investigate the listed problems; a re-run of the same session repairs a missing relative-strength row, nothing else is repaired",
}


def classify(session: date, results: Sequence[StepResult], *, apply: bool, locked: bool, grace_days: int, require_capture: bool,
             required: Optional[Sequence[str]] = None) -> Tuple[str, List[str], str]:
    """(verdict, missing_steps, next_action). Pure. COMPLETE needs EVERY required step to have succeeded; a failed, refused, skipped or absent step
    can never be COMPLETE, and a dry run is never COMPLETE."""
    if locked:
        return LOCKED, [], "another run holds the collection lock; nothing was attempted. The next fire re-checks."
    if not apply:
        return DRY_RUN, [], "dry run: nothing was written. Re-run with --apply to collect."
    need = list(required) if required is not None else [STEP_OBSERVE, STEP_LABELS, STEP_VERIFY] + ([STEP_CAPTURE] if require_capture else [])
    by = {r.name: r for r in results}
    missing = [n for n in need if n not in by or by[n].outcome not in SUCCESS]
    permanent = [r for r in results if r.outcome == REFUSED and r.detail.get("reason") == REASON_PAST_DEADLINE] + \
                [r for r in results if r.name == STEP_VERIFY and r.detail.get("permanent_problems")] + \
                [r for r in results if r.name == STEP_OBSERVE and r.outcome == FAILED and r.detail.get("reason") == REASON_NOT_LATEST] + \
                [r for r in results if r.name == STEP_OBSERVE and r.outcome == FAILED and r.detail.get("reason") in REASON_SNAPSHOT_PROBLEMS]
    sector_gap = [r for r in results if r.name == STEP_SECTOR_HISTORY and r.detail.get("permanent_problems")]
    if sector_gap and not permanent:
        return MISSED, missing, ("the sector history for this session can no longer be completed (the refresh did not happen before the decision "
                                 f"deadline {decision_deadline(session, grace_days).isoformat()}, or a chain is broken). The market/sector/relative-strength "
                                 "rows, if verified, stay valid; research marks the affected sector-relative cells unavailable. Never backfilled.")
    if permanent:
        return MISSED, missing, ("the session can no longer be observed: its decision deadline "
                                 f"({decision_deadline(session, grace_days).isoformat()}) has passed, or a newer session's prices are already loaded. "
                                 "Never backfill it as observed; the gap stays visible in the status.")
    if not missing:
        return COMPLETE, [], "none: the session is complete and verified"
    return INCOMPLETE, missing, " | ".join(f"{n}: {NEXT_ACTIONS[n]}" for n in missing)


# ------------------------------------------------------------------ the declared scheduler design (documented and validated, NEVER installed)
SCHEDULER_DESIGN: Dict[str, Any] = {
    "unit": "donchian-forward-collection.{service,timer} (one pair, committed under deploy/vps but DORMANT: installed by an administrator only, and the wrapper refuses without its arming file)",
    "command": "python -m forward_collection run --latest-completed --apply --with-sector-history-check --with-capture-check --code-ref <mechanism image tag>",
    "wrapper": "run_channel_sender.sh-style one-shot `docker compose run --rm` at CURRENT_MECHANISM_SHA, flock'ed",
    "timezone": "Asia/Jerusalem",
    "fires_local": ("06:45", "08:15"),
    "scan": "donchian-discontinuity-scan.{service,timer} (one pair, committed under deploy/vps but NOT INSTALLED): exactly one `build_dataset.py --scan-only --session latest-completed` per night, after the final post-market retry has started AND finished (it takes the retry lock and holds it); never a recurring rescan loop",
    "scan_local": "06:20",
    "last_retry_start_local": "06:00",         # the post-market retry timer: 23:45 then every 20 min 00:00-05:40, then 06:00 Asia/Jerusalem (donchian-postmarket-retry.timer)
    "retry_max_runtime_minutes": 20,           # OBSERVED 9-16 min per run; 20 is the bound the scan timer is placed after
    "scan_budget_minutes": 25,                 # the scan may wait up to 17 min for a running retry, then runs ~1-2 min (measured 57-70 s on production data)
    "fires_on": "the calendar day AFTER the session (Tue..Sat for Mon..Fri sessions)",
    "timeout_minutes": 45,
    "persistent": True,
    "pipeline_worst_finish_local": "03:15",     # OBSERVED worst case: the 14-day earnings-calendar re-fetch night (2026-10-07) finished 03:13:43 local; a normal night ends ~02:25
    "first_fire_margin_minutes": 30,
    "evaluator_local": "03:30",
    "lock": "systemd/flock (one process) + a Postgres advisory lock (two processes anywhere)",
    "retry": "bounded in-process retry on transient connection errors only; otherwise the 08:15 fire is the retry",
    "catch_up": "labels catch up on any later run; an unobserved session is NEVER backfilled as observed",
    "logging": "one JSON SessionReport line per run to stdout (journald) and the wrapper's log file; no secrets",
    "health_verification": "python -m forward_collection verify --latest-completed (read-only, exit 0/2) and the Slice 6 research status",
    "alert_on": "exit status 2 (INCOMPLETE), 4 (MISSED) or 5 (refused); 3 (LOCKED) alone is not an alert",
}


def fire_instants_utc(session: date, design: Mapping[str, Any] = SCHEDULER_DESIGN) -> List[datetime]:
    tz = ZoneInfo(design["timezone"])
    day = session + timedelta(days=1)
    out = []
    for hhmm in design["fires_local"]:
        h, m = (int(x) for x in hhmm.split(":"))
        out.append(datetime.combine(day, time(h, m), tzinfo=tz).astimezone(timezone.utc))
    return out


def worst_case_finish_utc(session: date, design: Mapping[str, Any] = SCHEDULER_DESIGN) -> datetime:
    """The last fire of the session plus the unit's timeout, plus the timer's RandomizedDelaySec (60 s)."""
    return fire_instants_utc(session, design)[-1] + timedelta(minutes=int(design["timeout_minutes"]), seconds=60)


def scan_instant_utc(session: date, design: Mapping[str, Any] = SCHEDULER_DESIGN) -> datetime:
    """The nominal start of the night's single scan: `scan_local` on the calendar day after the session, in the schedule's own timezone."""
    h, m = (int(x) for x in design["scan_local"].split(":"))
    return datetime.combine(session + timedelta(days=1), time(h, m), tzinfo=ZoneInfo(design["timezone"])).astimezone(timezone.utc)


def last_retry_start_utc(session: date, design: Mapping[str, Any] = SCHEDULER_DESIGN) -> datetime:
    h, m = (int(x) for x in design["last_retry_start_local"].split(":"))
    return datetime.combine(session + timedelta(days=1), time(h, m), tzinfo=ZoneInfo(design["timezone"])).astimezone(timezone.utc)


def required_grace_days(design: Mapping[str, Any] = SCHEDULER_DESIGN, year: int = 2026) -> int:
    """The smallest availability_grace_days under which EVERY observation this schedule can produce is known before its decision deadline,
    over every weekday of `year` (both daylight-saving offsets of the schedule's timezone)."""
    need = 0
    d = date(year, 1, 1)
    while d.year == year:
        if d.weekday() < 5:
            late = worst_case_finish_utc(d, design) - decision_deadline(d, 0)
            need = max(need, 0 if late <= timedelta(0) else late // timedelta(days=1) + 1)
        d += timedelta(days=1)
    return need


def validate_design(grace_days: int, design: Mapping[str, Any] = SCHEDULER_DESIGN) -> List[str]:
    """Problems with the declared schedule against a spec's availability_grace_days (empty = consistent)."""
    problems: List[str] = []
    for key in ("command", "timezone", "fires_local", "timeout_minutes", "lock", "retry", "catch_up", "logging", "health_verification", "alert_on"):
        if not design.get(key):
            problems.append(f"scheduler design is missing '{key}'")
    if problems:
        return problems
    need = required_grace_days(design)
    if grace_days < need:
        problems.append(f"availability_grace_days={grace_days} is too small for this schedule (needs >= {need}): an observation written by the "
                        f"schedule's own last fire would be stamped after its decision deadline")
    h, m = (int(x) for x in design["pipeline_worst_finish_local"].split(":"))
    f1, f2 = (int(x) for x in design["fires_local"][0].split(":"))
    if (f1 * 60 + f2) < (h * 60 + m + int(design["first_fire_margin_minutes"])):
        problems.append("the first fire is earlier than the pipeline's worst-case finish plus the margin: prices might not be loaded yet")
    for key in ("scan_local", "last_retry_start_local", "retry_max_runtime_minutes", "scan_budget_minutes"):
        if not design.get(key):
            problems.append(f"scheduler design is missing '{key}'")
    if any(not design.get(k) for k in ("scan_local", "last_retry_start_local", "retry_max_runtime_minutes", "scan_budget_minutes")):
        return problems
    sh, sm = (int(x) for x in design["scan_local"].split(":"))
    rh, rm = (int(x) for x in design["last_retry_start_local"].split(":"))
    scan_min, retry_min = sh * 60 + sm, rh * 60 + rm
    if scan_min <= retry_min:
        problems.append("the scan is not scheduled after the final post-market retry's scheduled start: a later retry could still write prices after it")
    if scan_min < retry_min + int(design["retry_max_runtime_minutes"]):
        problems.append("the scan is scheduled before the final retry's worst-case end (its scheduled start plus the retry's maximum runtime)")
    if (f1 * 60 + f2) < scan_min + int(design["scan_budget_minutes"]):
        problems.append("the first collector fire is earlier than the scan's start plus its wait-and-run budget: the scan evidence might not exist yet")
    return problems


def local_fire_description() -> List[str]:
    return [f"{t} {SCHEDULER_DESIGN['timezone']} on {SCHEDULER_DESIGN['fires_on']}" for t in SCHEDULER_DESIGN["fires_local"]]
