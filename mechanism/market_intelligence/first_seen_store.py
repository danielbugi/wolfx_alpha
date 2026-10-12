"""Database side of the first-seen log (migration 27). The only writes are INSERTs into `source_observation` / `source_poll`; the database stamps
`observed_at` / `polled_at` (this module never reads a clock) and enforces the hash chain, so the caller cannot back-date or fork a series.

`record_run(..., apply=False)` is a dry run: it reads the chain heads, returns the plan, and writes nothing."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

import psycopg2.errors
from psycopg2.extras import Json, RealDictCursor

from market_intelligence import first_seen as fs

POLL_STATUSES = ("complete", "partial", "failed")


def _dumps(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False)


@dataclass
class RunResult:
    applied: bool
    plan: fs.AppendPlan
    written: int = 0
    lost_race: int = 0           # a concurrent writer took the seq first; the row was NOT written (the next run sees the new head)
    poll_id: Optional[int] = None


def load_heads(cur, series_keys: Iterable[str]) -> Dict[str, fs.ChainHead]:
    keys = sorted(set(series_keys))
    if not keys:
        return {}
    cur = cur.connection.cursor()
    cur.execute("SELECT DISTINCT ON (series_key) series_key, seq, value_hash FROM source_observation "
                "WHERE series_key = ANY(%s) ORDER BY series_key, seq DESC", (keys,))
    return {r[0]: fs.ChainHead(r[0], r[1], r[2].strip()) for r in cur.fetchall()}


def record_run(cur, observations: Sequence[fs.Observation], run_key: str, code_ref: str, *, apply: bool = False,
               poll: Optional[Mapping[str, Any]] = None) -> RunResult:
    """Plan (always) and, when `apply`, append the changed values. `poll` = {source, dataset, status, subjects_polled, detail} records what the run
    asked and got answered, so an unchanged value is provably "seen again, same" and a source outage is provably "not asked"."""
    if poll is not None and poll["status"] not in POLL_STATUSES:
        raise fs.FirstSeenError(f"poll status must be one of {POLL_STATUSES}")
    cur = cur.connection.cursor()
    obs = list(observations)
    plan = fs.plan_appends(obs, load_heads(cur, (o.key for o in obs)))
    if not apply:
        return RunResult(False, plan)
    written = lost = 0
    for a in plan.appends:
        o = a.observation
        # A writer whose planned seq was taken meanwhile is refused by the chain trigger (a sequential stale writer) or by the unique key
        # (a simultaneous one). Either way nothing is forked: the row is skipped and the next run re-plans from the new head.
        cur.execute("SAVEPOINT first_seen_append")
        try:
            cur.execute(
                "INSERT INTO source_observation (series_key, source, dataset, subject_type, subject_id, period_key, seq, prev_value_hash, value, "
                "value_hash, source_asof, run_key, code_ref) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (series_key, seq) DO NOTHING",
                (a.series_key, o.source, o.dataset, o.subject_type, o.subject_id, o.period_key, a.seq, a.prev_value_hash,
                 Json(dict(o.value), dumps=_dumps), a.value_hash, o.source_asof, run_key, code_ref))
            inserted = cur.rowcount == 1
        except psycopg2.errors.IntegrityConstraintViolation:
            cur.execute("ROLLBACK TO SAVEPOINT first_seen_append")
            inserted = False
        cur.execute("RELEASE SAVEPOINT first_seen_append")
        if inserted:
            written += 1
        else:
            lost += 1
    poll_id = None
    if poll is not None:
        subjects = sorted(set(poll.get("subjects_polled") or ()))
        cur.execute(
            "INSERT INTO source_poll (run_key, source, dataset, status, subjects_polled, n_new_observations, detail, code_ref) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (run_key, source, dataset) DO NOTHING RETURNING id",
            (run_key, poll["source"], poll["dataset"], poll["status"], subjects, written, Json(dict(poll.get("detail") or {}), dumps=_dumps),
             code_ref))
        row = cur.fetchone()
        poll_id = row[0] if row else None
    return RunResult(True, plan, written, lost, poll_id)


_COLS = ("id, series_key, source, dataset, subject_type, subject_id, period_key, seq, prev_value_hash, value, value_hash, source_asof, "
         "observed_at, run_key, code_ref")


def read_chain(cur, key: str) -> List[Dict[str, Any]]:
    """Every row of one series, oldest first (full history -- for verification and for revision features)."""
    cur = cur.connection.cursor(cursor_factory=RealDictCursor)
    cur.execute(f"SELECT {_COLS} FROM source_observation WHERE series_key = %s ORDER BY seq", (key,))
    return [_row(r) for r in cur.fetchall()]


def read_as_of(cur, cutoff: datetime, *, dataset: Optional[str] = None, subject_type: Optional[str] = None,
               subject_id: Optional[str] = None, source: Optional[str] = None) -> List[Dict[str, Any]]:
    """The value of each series as First Light knew it at `cutoff` (highest seq with observed_at <= cutoff). A series first seen later is absent."""
    if cutoff.tzinfo is None:
        raise fs.FirstSeenError("cutoff must be timezone-aware")
    where, args = ["observed_at <= %s"], [cutoff]
    for col, val in (("dataset", dataset), ("subject_type", subject_type), ("subject_id", subject_id), ("source", source)):
        if val is not None:
            where.append(f"{col} = %s")
            args.append(val)
    cur = cur.connection.cursor(cursor_factory=RealDictCursor)
    cur.execute(f"SELECT DISTINCT ON (series_key) {_COLS} FROM source_observation WHERE {' AND '.join(where)} "
                "ORDER BY series_key, seq DESC", args)
    return [_row(r) for r in cur.fetchall()]


def _row(r: Mapping[str, Any]) -> Dict[str, Any]:
    out = dict(r)
    for k in ("prev_value_hash", "value_hash"):
        if out.get(k) is not None:
            out[k] = out[k].strip()
    return out
