"""The ONE authoritative writer of forward sector history (`sector_observation` / `sector_poll`, migration 31).

Called only from `FundamentalsUpdater.update_symbol`, behind the flag `SECTOR_HISTORY_RECORDER_ENABLED` (default OFF: unset or anything other
than exactly "1" means off). Nothing else in the repository may write these tables (guarded by a test).

What it records, per refresh attempt, in ONE transaction on its OWN connection (never the fundamentals upsert's) inside a SAVEPOINT:
  * the vendor answered with a sector equal to the chain head      -> a `confirmed_head` poll only (no duplicate observation);
  * the vendor answered with a different sector / the first one    -> a chained observation, then a `created_observation` poll;
  * the vendor explicitly answered "no sector" (yfinance only)     -> the same, with the sector NULL and a coded reason;
  * the request failed or the answer could not be validated        -> a poll with chain_effect = none (the head is neither changed nor confirmed).
A failure of any of this rolls back to the savepoint, is logged with the symbol and reason, is counted, and is NEVER raised into the fundamentals
ingestion. The vendor's own validity time is never invented: `source_asof` stays NULL (yfinance and Tiingo meta supply the CURRENT classification
only). `captured_at` / `effective_session` / `attempted_at` / the row hash are stamped by the database triggers, not by this code.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional

logger = logging.getLogger(__name__)

FLAG_ENV = "SECTOR_HISTORY_RECORDER_ENABLED"
WRITER = "fundamentals_updater.sector_recorder"
CODE_REF = "mechanism/data_updaters/sector_history_recorder.py#record_poll@v1"

SRC_TIINGO, SRC_YFINANCE = "tiingo_meta", "yfinance_info"
SECTOR_MAX, RAW_MAX, MIN_YF_KEYS = 100, 200, 5


def enabled() -> bool:
    return os.getenv(FLAG_ENV, "") == "1"


def new_run_id(now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    return "fund-" + now.strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]


@dataclass(frozen=True)
class Outcome:
    source: str
    response_state: str                  # sector | no_sector | request_failed | invalid_response
    sector: Optional[str] = None         # cleaned canonical sector (only for response_state == sector)
    sector_raw: Optional[str] = None     # the vendor's string verbatim
    no_sector_reason: Optional[str] = None
    failure_reason: Optional[str] = None
    payload_hash: Optional[str] = None


def payload_hash(raw: Any) -> str:
    body = json.dumps({"sector": raw}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def projection(raw: Any) -> Dict[str, Any]:
    return {"sector": raw}


def _fail(source: str, state: str, reason: str, raw: Any = None, with_hash: bool = False) -> Outcome:
    return Outcome(source=source, response_state=state, failure_reason=reason, payload_hash=payload_hash(raw) if with_hash else None)


def classify(source: str, info: Optional[Mapping[str, Any]], error: Optional[BaseException] = None) -> Outcome:
    """Pure: what one vendor interaction means for the chain. `info` is the dict `fetch_company_info` returned (or None), `error` what it raised.

    A Tiingo answer with no sector is AMBIGUOUS (a swallowed meta failure and the Power-plan placeholder are both turned into None upstream), so it
    is recorded as an invalid response, never as an explicit "no sector". Only yfinance's `info` (already required to carry >= 5 fields) can say
    "no sector" explicitly.
    """
    if error is not None:
        return _fail(source, "request_failed", "timeout" if isinstance(error, TimeoutError) else "request_error")
    if info is None:
        return _fail(source, "request_failed", "no_company_info")
    if "sector" not in info:
        return _fail(source, "invalid_response", "sector_field_missing")
    raw = info.get("sector")
    if raw is None:
        if source == SRC_TIINGO:
            return _fail(source, "invalid_response", "ambiguous_source_none", None, True)
        return Outcome(source=source, response_state="no_sector", no_sector_reason="vendor_null", payload_hash=payload_hash(None))
    if not isinstance(raw, str):
        return _fail(source, "invalid_response", "non_string_sector", str(raw), True)
    stripped = raw.strip()
    if not stripped:
        if source == SRC_TIINGO:
            return _fail(source, "invalid_response", "ambiguous_source_none", raw, True)
        return Outcome(source=source, response_state="no_sector", sector_raw=raw, no_sector_reason="vendor_blank", payload_hash=payload_hash(raw))
    if stripped.lower() == "unknown":
        if source == SRC_TIINGO:
            return _fail(source, "invalid_response", "ambiguous_source_none", raw, True)
        return Outcome(source=source, response_state="no_sector", sector_raw=raw, no_sector_reason="vendor_unknown_label",
                       payload_hash=payload_hash(raw))
    if len(stripped) > SECTOR_MAX or len(raw) > RAW_MAX:
        return _fail(source, "invalid_response", "oversized_sector", raw, True)
    return Outcome(source=source, response_state="sector", sector=stripped, sector_raw=raw, payload_hash=payload_hash(raw))


def _lock(cur, symbol: str, source: str) -> None:
    cur.execute("SELECT pg_advisory_xact_lock(hashtextextended('sector_history|' || %s || '|' || %s, 0))", (symbol, source))


def record_poll(conn, outcome: Outcome, *, run_id: str, symbol: str) -> str:
    """Record one poll on `conn` (a plain psycopg2 connection) inside a SAVEPOINT and commit it. Returns the chain_effect, or 'duplicate_poll'
    when this (run_id, symbol, source) was already recorded. On ANY error the savepoint is rolled back, the transaction is committed empty and the
    exception is re-raised for `SectorRecorder.record` to log and count -- this function never leaves a half-written unit behind."""
    src = outcome.source
    cur = conn.cursor()
    try:
        cur.execute("SAVEPOINT sector_history_unit")
        try:
            _lock(cur, symbol, src)
            cur.execute("SELECT 1 FROM sector_poll WHERE run_id = %s AND symbol = %s AND source = %s", (run_id, symbol, src))
            if cur.fetchone():
                effect = "duplicate_poll"
            elif outcome.response_state in ("request_failed", "invalid_response"):
                _insert_poll(cur, outcome, run_id, symbol, "none", None)
                effect = "none"
            else:
                effect = _record_answer(cur, outcome, run_id, symbol)
            cur.execute("RELEASE SAVEPOINT sector_history_unit")
        except BaseException:
            cur.execute("ROLLBACK TO SAVEPOINT sector_history_unit")
            raise
        finally:
            conn.commit()
        return effect
    finally:
        cur.close()


def _record_answer(cur, o: Outcome, run_id: str, symbol: str) -> str:
    cur.execute("SELECT id, seq, value_hash, sector, run_id FROM sector_observation WHERE symbol = %s AND source = %s ORDER BY seq DESC LIMIT 1",
                (symbol, o.source))
    head = cur.fetchone()
    if head is not None and head[3] == o.sector:                       # same effective sector (both NULL = same explicit no-sector)
        head_id, head_run = head[0], head[4]
        if head_run == run_id:                                         # a retry whose observation already committed: the poll that created it
            _insert_poll(cur, o, run_id, symbol, "created_observation", head_id)
            return "created_observation"
        _insert_poll(cur, o, run_id, symbol, "confirmed_head", head_id)
        return "confirmed_head"
    seq, prev = (1, None) if head is None else (head[1] + 1, head[2])
    if head is None:
        kind = "first"
    elif head[3] is None:
        kind = "became_set"
    elif o.sector is None:
        kind = "became_none"
    else:
        kind = "changed"
    cur.execute(
        "INSERT INTO sector_observation (symbol, source, seq, sector, sector_raw, no_sector_reason, change_kind, source_asof, provenance, "
        "raw_payload_hash, raw_payload, run_id, writer, code_ref, prev_value_hash) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, NULL, 'observed_forward', %s, %s::jsonb, %s, %s, %s, %s) RETURNING id",
        (symbol, o.source, seq, o.sector, o.sector_raw, o.no_sector_reason, kind, o.payload_hash, json.dumps(projection(o.sector_raw)),
         run_id, WRITER, CODE_REF, prev))
    obs_id = cur.fetchone()[0]
    _insert_poll(cur, o, run_id, symbol, "created_observation", obs_id)
    return "created_observation"


def _insert_poll(cur, o: Outcome, run_id: str, symbol: str, effect: str, observation_id: Optional[int]) -> None:
    cur.execute(
        "INSERT INTO sector_poll (run_id, symbol, source, response_state, chain_effect, response_sector, no_sector_reason, failure_reason, "
        "raw_payload_hash, observation_id, writer, code_ref) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (run_id, symbol, o.source, o.response_state, effect, o.sector, o.no_sector_reason, o.failure_reason, o.payload_hash, observation_id,
         WRITER, CODE_REF))


class SectorRecorder:
    """Per-run holder: the run id, the on/off decision and the observable counters. `connect` is a zero-arg callable returning a context manager
    that yields a psycopg2 connection (the shared pool by default); tests pass their own."""

    def __init__(self, run_id: Optional[str] = None, connect=None, is_enabled=None):
        self.run_id = run_id or new_run_id()
        self._connect = connect
        self._enabled = is_enabled if is_enabled is not None else enabled
        self.counters: Dict[str, int] = {"attempted": 0, "recorded": 0, "duplicate": 0, "failed": 0}

    def _conn(self):
        if self._connect is not None:
            return self._connect()
        from shared import db
        return db.get_sync_connection()

    def record(self, symbol: str, source: str, info: Optional[Mapping[str, Any]], error: Optional[BaseException] = None) -> Optional[str]:
        """Record one poll; NEVER raises. Returns the chain effect, or None when disabled or when recording failed (counted + logged)."""
        if not self._enabled():
            return None
        self.counters["attempted"] += 1
        try:
            outcome = classify(source, info, error)
            with self._conn() as conn:
                effect = record_poll(conn, outcome, run_id=self.run_id, symbol=symbol)
            self.counters["duplicate" if effect == "duplicate_poll" else "recorded"] += 1
            return effect
        except Exception as e:                                          # noqa: BLE001 - the isolation boundary: ingestion must continue
            self.counters["failed"] += 1
            logger.error("sector history NOT recorded for %s (%s, run %s): %s: %s", symbol, source, self.run_id, type(e).__name__, e)
            return None
