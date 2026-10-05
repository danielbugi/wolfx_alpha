"""Runs the collection steps for ONE explicit session. Pure orchestration: every write happens inside a step supplied by the caller.

Guarantees (each pinned by a test): one collector at a time (Postgres advisory lock, released explicitly because pooled connections outlive
the `with`); the observe step is refused once the session's decision deadline has passed (it could only be stamped after the deadline, i.e. it
would be unusable and would pretend to be an observation); steps are independent (labels never wait for observe); a step whose dependency did
not succeed is `skipped_dependency`, never "ok"; only transient connection errors are retried, in-process and bounded; the report never contains
a connection string (error text is the class name plus a clipped message).
"""
from __future__ import annotations

import hashlib
import time
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Callable, Dict, Mapping, Optional

import psycopg2

from research.lab.dataset_contract import decision_deadline

from . import contract as C

LOCK_KEY = int.from_bytes(hashlib.sha256(b"forward_collection_v1").digest()[:8], "big", signed=True)
DEPENDS_ON: Dict[str, tuple] = {C.STEP_CAPTURE: (), C.STEP_OBSERVE: (), C.STEP_LABELS: (), C.STEP_VERIFY: (C.STEP_OBSERVE,)}
TRANSIENT = (psycopg2.OperationalError, psycopg2.InterfaceError)


@dataclass
class Ctx:
    connect: Callable[[], Any]
    session: date
    apply: bool
    code_ref: Optional[str]
    grace_days: int
    deadline: datetime
    results: Dict[str, C.StepResult]


StepFn = Callable[[Ctx], C.StepResult]


def db_clock(connect: Callable[[], Any]) -> Callable[[], datetime]:
    def now() -> datetime:
        with connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT clock_timestamp()")
            v = cur.fetchone()[0]
            conn.rollback()
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    return now


def _past_deadline(r: C.StepResult) -> bool:
    """The observe step could not WRITE because it is too late (deadline passed, or a newer session's bar is already the newest): the read-back
    verification, not this step, decides whether the session was collected on time."""
    return (r.outcome == C.REFUSED and r.detail.get("reason") == C.REASON_PAST_DEADLINE) or            (r.outcome == C.FAILED and r.detail.get("reason") == C.REASON_NOT_LATEST)


def _satisfied(r: C.StepResult) -> bool:
    """A dependency is satisfied when it succeeded, was a dry run, or was refused only because the deadline passed (verify then decides)."""
    return r.ok() or r.outcome == C.DRY or _past_deadline(r)


def _err(e: BaseException) -> str:
    return f"{type(e).__name__}: {str(e).strip()[:300]}"


def _acquire(stack: ExitStack, connect) -> bool:
    conn = stack.enter_context(connect())
    cur = conn.cursor()
    cur.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_KEY,))
    got = bool(cur.fetchone()[0])
    conn.commit()
    if got:
        def release():
            try:
                conn.rollback()
                c2 = conn.cursor()
                c2.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))
                conn.commit()
            except Exception:       # a dead connection has already dropped its session lock
                pass
        stack.callback(release)
    return got


def _run_step(name: str, fn: StepFn, ctx: Ctx, max_attempts: int, sleep: Callable[[float], None]) -> C.StepResult:
    attempt = 0
    while True:
        attempt += 1
        try:
            r = fn(ctx)
            r.attempts = attempt
            return r
        except TRANSIENT as e:
            if attempt >= max_attempts:
                return C.StepResult(name, C.FAILED, {"transient": True}, _err(e), attempt)
            sleep(min(2 ** attempt, 30))
        except Exception as e:     # noqa: BLE001  a failed step is a result, never a crash that hides the other steps
            return C.StepResult(name, C.FAILED, {}, _err(e), attempt)


def run_session(connect: Callable[[], Any], session: date, steps: Mapping[str, StepFn], *, apply: bool, grace_days: int,
                code_ref: Optional[str] = None, clock: Optional[Callable[[], datetime]] = None, lock: bool = True,
                max_attempts: int = 3, sleep: Callable[[float], None] = time.sleep) -> C.SessionReport:
    if not isinstance(session, date):
        raise TypeError("session must be an explicit date")
    if apply and not code_ref:
        raise ValueError("code_ref is required to write")
    unknown = set(steps) - set(C.STEP_ORDER)
    if unknown:
        raise ValueError(f"unknown steps {sorted(unknown)}")
    deadline = decision_deadline(session, grace_days)
    clock = clock or db_clock(connect)
    need = [s for s in C.STEP_ORDER if s in steps]
    with ExitStack() as stack:
        if lock and not _acquire(stack, connect):
            v, miss, nxt = C.classify(session, [], apply=apply, locked=True, grace_days=grace_days, require_capture=False)
            return C.SessionReport(session, v, [], miss, nxt, apply, grace_days, deadline)
        ctx = Ctx(connect, session, apply, code_ref, grace_days, deadline, {})
        results = []
        for name in need:
            blocked = [d for d in DEPENDS_ON[name] if d in steps and not _satisfied(ctx.results[d])]
            if blocked:
                r = C.StepResult(name, C.SKIPPED, {"because": blocked})
            elif name == C.STEP_OBSERVE and clock() >= deadline:
                r = C.StepResult(name, C.REFUSED, {"reason": C.REASON_PAST_DEADLINE, "deadline": deadline.isoformat()})
            else:
                r = _run_step(name, steps[name], ctx, max_attempts, sleep)
            ctx.results[name] = r
            results.append(r)
        obs, ver = ctx.results.get(C.STEP_OBSERVE), ctx.results.get(C.STEP_VERIFY)
        if obs is not None and _past_deadline(obs) and ver is not None and ver.ok():
            # a re-run after the deadline of a session that WAS collected on time: the read-back proves it, so it is complete, not missed
            fixed = C.StepResult(C.STEP_OBSERVE, C.ALREADY, {"note": "too late to write; the read-back verified the on-time rows"})
            results[results.index(obs)] = ctx.results[C.STEP_OBSERVE] = fixed
        v, miss, nxt = C.classify(session, results, apply=apply, locked=False, grace_days=grace_days, require_capture=C.STEP_CAPTURE in steps,
                                  required=[s for s in need])
        return C.SessionReport(session, v, results, miss, nxt, apply, grace_days, deadline)
