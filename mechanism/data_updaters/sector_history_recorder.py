"""The ONE authoritative writer of forward sector history (`sector_observation` / `sector_poll`, migration 31).

Called only from `FundamentalsUpdater.update_symbol`, behind the flag `SECTOR_HISTORY_RECORDER_ENABLED` (default OFF: unset or anything other
than exactly "1" means off). Nothing else in the repository may write these tables (guarded by a test).

What it records, per refresh attempt, in ONE transaction on its OWN connection (never the fundamentals upsert's) inside a SAVEPOINT:
  * the vendor answered with a sector equal to the chain head      -> a `confirmed_head` poll only (no duplicate observation);
  * the vendor answered with a different sector / the first one    -> a chained observation, then a `created_observation` poll;
  * the answer has no sector AND is structurally a no-sector instrument (yfinance only: quoteType ETF / MUTUALFUND, see `NON_OPERATING_QUOTE_TYPES`)
    -> a `no_sector` row: the sector NULL, `no_sector_reason` coded. This is an INFERENCE by this recorder from the vendor's own `quoteType`; the
    vendor does not assert "no sector" (it simply omits the key), and `no_sector_reason` (vendor_null / vendor_blank / vendor_unknown_label) only
    describes what the sector field looked like;
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

from data_updaters import vendor_symbols

logger = logging.getLogger(__name__)

FLAG_ENV = "SECTOR_HISTORY_RECORDER_ENABLED"
WRITER = "fundamentals_updater.sector_recorder"
CODE_REF = "mechanism/data_updaters/sector_history_recorder.py#record_poll@v1"

SRC_TIINGO, SRC_YFINANCE = "tiingo_meta", "yfinance_info"
SECTOR_MAX, RAW_MAX, MIN_YF_KEYS = 100, 200, 5

# SOURCE IDENTITY (Slice 11, policy A): ONE vendor owns the forward sector identity. The other is recorded as its own DIAGNOSTIC chain (evidence
# for cross-source disagreement) and can never stand in for the authoritative one: there is no fallback identity, so a provider outage can never
# splice two vendors into one chain. `research.lab.sector_history.AUTHORITATIVE_SOURCE` pins the same value (a test keeps them equal).
AUTHORITATIVE_SOURCE = SRC_YFINANCE
DIAGNOSTIC_SOURCES = (SRC_TIINGO,)

# yfinance has no field that ASSERTS "no sector": the key is simply absent for ETFs and mutual funds (probed live 2026-10-06) but it is ALSO absent
# from a partial or degraded answer for an operating company. The only structural evidence that the absence is meaningful is `quoteType`. Only the
# values actually observed to carry no sector are listed; any other quoteType (or none) is "responded, no usable sector", never a no-sector state.
# `no_sector` therefore means "no sector, INFERRED from an approved quoteType" -- never "the vendor asserted it". The stored enum keeps its name:
# renaming it would need a schema change, and the meaning is carried by this note and `no_sector_basis` in the dry run.
NO_SECTOR_BASIS = "inferred_from_quote_type"
NON_OPERATING_QUOTE_TYPES = ("ETF", "MUTUALFUND")
OPERATING_QUOTE_TYPES = ("EQUITY",)


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
    quote_type: Optional[str] = None     # the structural evidence behind an INFERRED no-sector (yfinance only); part of the hashed projection


def projection(raw: Any, quote_type: Optional[str] = None) -> Dict[str, Any]:
    """The minimal projection of the vendor response that is hashed and stored (never a full vendor payload)."""
    out: Dict[str, Any] = {"sector": raw}
    if quote_type is not None:
        out["quote_type"] = quote_type
    return out


def payload_hash(raw: Any, quote_type: Optional[str] = None) -> str:
    body = json.dumps(projection(raw, quote_type), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def yfinance_meta(raw: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """The structural evidence of ONE raw yfinance `Ticker.info` dict, taken BEFORE `fetch_company_info` flattens it (`info.get('sector')` turns an
    absent key and an explicit null into the same None). The updater and the dry run both call this one function."""
    raw = raw or {}
    qt = raw.get("quoteType")
    return {"quote_type": qt.strip().upper() if isinstance(qt, str) and qt.strip() else None, "n_keys": len(raw),
            "sector_key_present": "sector" in raw}


def yfinance_company_info(raw: Optional[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
    """The sector-relevant view of a raw yfinance answer, exactly as `FundamentalsUpdater.fetch_company_info` flattens it: no/short answer -> None
    (the updater's `len(info) < 5` gate), else `{'sector': info.get('sector')}`. The dry run uses this so it never carries a second parser."""
    if not raw or len(raw) < MIN_YF_KEYS:
        return None
    return {"sector": raw.get("sector")}


def _fail(source: str, state: str, reason: str, raw: Any = None, with_hash: bool = False) -> Outcome:
    return Outcome(source=source, response_state=state, failure_reason=reason, payload_hash=payload_hash(raw) if with_hash else None)


def _no_usable_yfinance_sector(reason: str, raw: Any, meta: Optional[Mapping[str, Any]]) -> Outcome:
    """yfinance gave no usable sector. Two DIFFERENT meanings, never merged:
       * the response's quoteType is a verified non-operating type -> a no-sector state INFERRED from that type (the vendor asserts nothing);
       * anything else (an operating company, an unknown type, no evidence) -> `invalid_response`: it RESPONDED but supplied no usable sector and
         nothing proves the absence is meaningful. That never creates an observation, so it can never contradict a candidate's sector."""
    if meta is None:
        return _fail(SRC_YFINANCE, "invalid_response", "quote_type_evidence_missing", raw, True)
    qt = meta.get("quote_type")
    if qt in NON_OPERATING_QUOTE_TYPES:
        return Outcome(source=SRC_YFINANCE, response_state="no_sector", sector_raw=raw if isinstance(raw, str) else None, no_sector_reason=reason,
                       payload_hash=payload_hash(raw, qt), quote_type=qt)
    if qt in OPERATING_QUOTE_TYPES:
        return _fail(SRC_YFINANCE, "invalid_response", "operating_company_sector_absent", raw, True)
    return _fail(SRC_YFINANCE, "invalid_response", "quote_type_missing" if qt is None else "quote_type_unrecognised", raw, True)


def classify(source: str, info: Optional[Mapping[str, Any]], error: Optional[BaseException] = None,
             meta: Optional[Mapping[str, Any]] = None) -> Outcome:
    """Pure: what one vendor interaction means for the chain. `info` is the dict `fetch_company_info` returned (or None), `error` what it raised,
    `meta` the `yfinance_meta` evidence of the raw response (yfinance only; None when unavailable).

    Four states, kept distinct:  request_failed (the vendor did not answer)  /  invalid_response (it answered but supplied no usable sector)  /
    no_sector (it answered AND the answer is structurally a no-sector instrument)  /  sector.

    A Tiingo answer with no sector is AMBIGUOUS (a swallowed meta failure and the Power-plan placeholder are both turned into None upstream), so it
    is recorded as an invalid response, never as a no-sector state. A yfinance answer is classified `no_sector` ONLY with non-operating quoteType
    evidence (Slice 11): a bare absent / null / blank / "Unknown" sector is not evidence, and the vendor never asserts "no sector" itself.

    A request that was REFUSED before it was made (`UnsupportedSymbolFormat`: no vendor symbol could be derived without guessing) is `request_failed`
    with the coded reason `unsupported_symbol_format`: no information was obtained, so it is neither an answer nor a vendor failure.
    """
    if error is not None:
        if isinstance(error, vendor_symbols.UnsupportedSymbolFormat):
            return _fail(source, "request_failed", "unsupported_symbol_format")
        return _fail(source, "request_failed", "timeout" if isinstance(error, TimeoutError) else "request_error")
    if info is None:
        if meta is not None and meta.get("n_keys", 0) > 0:             # it answered, with too little to use (e.g. a 404 body)
            return _fail(source, "invalid_response", "response_too_sparse")
        return _fail(source, "request_failed", "no_company_info")
    if "sector" not in info:
        return _fail(source, "invalid_response", "sector_field_missing")
    raw = info.get("sector")
    if raw is None:
        if source == SRC_TIINGO:
            return _fail(source, "invalid_response", "ambiguous_source_none", None, True)
        return _no_usable_yfinance_sector("vendor_null", None, meta)
    if not isinstance(raw, str):
        return _fail(source, "invalid_response", "non_string_sector", str(raw), True)
    stripped = raw.strip()
    if not stripped:
        if source == SRC_TIINGO:
            return _fail(source, "invalid_response", "ambiguous_source_none", raw, True)
        return _no_usable_yfinance_sector("vendor_blank", raw, meta)
    if stripped.lower() == "unknown":
        if source == SRC_TIINGO:
            return _fail(source, "invalid_response", "ambiguous_source_none", raw, True)
        return _no_usable_yfinance_sector("vendor_unknown_label", raw, meta)
    if len(stripped) > SECTOR_MAX or len(raw) > RAW_MAX:
        return _fail(source, "invalid_response", "oversized_sector", raw, True)
    return Outcome(source=source, response_state="sector", sector=stripped, sector_raw=raw, payload_hash=payload_hash(raw))


@dataclass(frozen=True)
class Decision:
    """What recording one answered poll against a chain head does. `head` is `(id, seq, value_hash, sector, run_id)` or None."""
    chain_effect: str                    # created_observation | confirmed_head | none
    change_kind: Optional[str] = None    # first | changed | became_none | became_set (only when a NEW observation is proposed)
    seq: Optional[int] = None
    prev_value_hash: Optional[str] = None
    observation_id: Optional[int] = None  # the existing observation a poll points at (confirmed head, or a retry's own observation)


def decide(head: Optional[tuple], o: Outcome, run_id: str) -> Decision:
    """Pure: the chain decision for one outcome. The ONE place the same-value / new-observation rule lives (the writer and the dry run share it)."""
    if o.response_state not in ("sector", "no_sector"):
        return Decision("none")
    if head is not None and head[3] == o.sector:                       # same effective sector (both NULL = the same inferred no-sector)
        if head[4] == run_id:                                          # a retry whose observation already committed: the poll that created it
            return Decision("created_observation", observation_id=head[0])
        return Decision("confirmed_head", observation_id=head[0])
    if head is None:
        return Decision("created_observation", "first", 1, None)
    kind = "became_set" if head[3] is None else ("became_none" if o.sector is None else "changed")
    return Decision("created_observation", kind, head[1] + 1, head[2])


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
    d = decide(head, o, run_id)
    if d.observation_id is not None:                                   # confirmed head, or a retry whose observation already committed
        _insert_poll(cur, o, run_id, symbol, d.chain_effect, d.observation_id)
        return d.chain_effect
    seq, prev, kind = d.seq, d.prev_value_hash, d.change_kind
    cur.execute(
        "INSERT INTO sector_observation (symbol, source, seq, sector, sector_raw, no_sector_reason, change_kind, source_asof, provenance, "
        "raw_payload_hash, raw_payload, run_id, writer, code_ref, prev_value_hash) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, NULL, 'observed_forward', %s, %s::jsonb, %s, %s, %s, %s) RETURNING id",
        (symbol, o.source, seq, o.sector, o.sector_raw, o.no_sector_reason, kind, o.payload_hash, json.dumps(projection(o.sector_raw, o.quote_type)),
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

    def record(self, symbol: str, source: str, info: Optional[Mapping[str, Any]], error: Optional[BaseException] = None,
               meta: Optional[Mapping[str, Any]] = None) -> Optional[str]:
        """Record one poll; NEVER raises. Returns the chain effect, or None when disabled or when recording failed (counted + logged)."""
        if not self._enabled():
            return None
        self.counters["attempted"] += 1
        try:
            outcome = classify(source, info, error, meta)
            with self._conn() as conn:
                effect = record_poll(conn, outcome, run_id=self.run_id, symbol=symbol)
            self.counters["duplicate" if effect == "duplicate_poll" else "recorded"] += 1
            return effect
        except Exception as e:                                          # noqa: BLE001 - the isolation boundary: ingestion must continue
            self.counters["failed"] += 1
            logger.error("sector history NOT recorded for %s (%s, run %s): %s: %s", symbol, source, self.run_id, type(e).__name__, e)
            return None


# ---------------------------------------------------------------------------------------------------------------------------------------------
# NON-MUTATING DRY RUN (Slice 11). Reuses `classify` / `decide` / `payload_hash` / `yfinance_meta` / `yfinance_company_info` verbatim: there is no second
# parser. It takes NO connection and writes NOTHING (it cannot: it has no handle to a database); `writes` is always False in its output.
# ---------------------------------------------------------------------------------------------------------------------------------------------
_ERRORS = {"timeout": TimeoutError, "request_error": RuntimeError}


def source_identity(source: str) -> str:
    return "authoritative" if source == AUTHORITATIVE_SOURCE else ("diagnostic" if source in DIAGNOSTIC_SOURCES else "unknown")


def dry_run(symbol: str, source: str, *, raw: Optional[Mapping[str, Any]] = None, error: Optional[str] = None,
            head: Optional[Mapping[str, Any]] = None, run_id: str = "dry-run") -> Dict[str, Any]:
    """What ONE vendor interaction would do, without doing it. `raw` is the vendor answer as the updater sees it for that source (for yfinance the raw
    `Ticker.info` dict; for Tiingo the flattened company-info dict); `error` is `timeout` / `request_error` / None; `head` is the chain head the
    interaction would be recorded against (`{"seq", "value_hash", "sector", "run_id"}`) or None for a new chain."""
    meta = None
    vendor_symbol = None
    exc = _ERRORS[error]("dry run: simulated " + error) if error else None
    if source == SRC_YFINANCE:
        # The SAME translation the writer applies before its request (`FundamentalsUpdater._fetch_yfinance_raw`). A refused symbol means the writer would
        # never have asked the vendor, so whatever `raw` / `error` were supplied is irrelevant: the interaction is the refusal.
        try:
            vendor_symbol = vendor_symbols.to_yfinance(symbol).as_dict()
        except vendor_symbols.UnsupportedSymbolFormat as refusal:
            vendor_symbol = {"canonical": symbol, "request": None, "status": "refused", "rule": None, "refusal_reason": refusal.reason}
            exc, raw = refusal, None
        meta = yfinance_meta(raw)
        info = yfinance_company_info(raw)
    else:
        info = None if raw is None else dict(raw)
    o = classify(source, info, exc, meta)
    head_t = None if head is None else (0, head.get("seq", 1), head.get("value_hash"), head.get("sector"), head.get("run_id"))
    d = decide(head_t, o, run_id)
    identity = source_identity(source)
    answered = o.response_state in ("sector", "no_sector")
    if identity != "authoritative":
        reason = "diagnostic_source_is_not_identity" if identity == "diagnostic" else "unknown_source"
    elif not answered:
        reason = f"{o.response_state}:{o.failure_reason}"
    else:
        reason = None
    observation = None
    if d.chain_effect == "created_observation" and d.seq is not None:
        observation = {"seq": d.seq, "change_kind": d.change_kind, "sector": o.sector, "no_sector_reason": o.no_sector_reason,
                       "prev_value_hash": d.prev_value_hash}
    return {
        "symbol": symbol, "source": source, "source_identity": identity,
        "vendor_symbol": vendor_symbol,                 # canonical -> request; only the request is translated, the stored identity is `symbol`
        "no_sector_basis": NO_SECTOR_BASIS if o.response_state == "no_sector" else None,
        "outcome": {"response_state": o.response_state, "sector": o.sector, "sector_raw": o.sector_raw, "no_sector_reason": o.no_sector_reason,
                    "failure_reason": o.failure_reason, "quote_type": o.quote_type},
        "proposed_poll": {"response_state": o.response_state, "chain_effect": d.chain_effect},
        "proposed_observation": observation,
        "source_asof": None,
        "raw_payload_hash": o.payload_hash,
        "recorded_on_own_chain": answered,
        "admissible_for_forward_history": identity == "authoritative" and answered,
        "rejection_reason": reason,
        "writes": False,
    }


def fetch_yfinance_raw(symbol: str, timeout: float = 20.0) -> Optional[Dict[str, Any]]:
    """READ-ONLY live probe used only by the dry-run CLI: one yfinance `Ticker.info` request with a hard wall-clock timeout. Raises TimeoutError."""
    import concurrent.futures
    import yfinance as yf
    request_symbol = vendor_symbols.to_yfinance(symbol).request      # the writer's translation; raises UnsupportedSymbolFormat before any request
    ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    fut = ex.submit(lambda: yf.Ticker(request_symbol).info)
    try:
        return fut.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        raise TimeoutError(f"yfinance probe timed out for {symbol}")
    finally:
        ex.shutdown(wait=False)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Non-mutating dry run of the forward sector-history normalization (writes nothing, needs no database).")
    ap.add_argument("--fixture", help="JSON list of {symbol, source, raw, error, head}")
    ap.add_argument("--live-yfinance", default="", help="comma-separated CANONICAL symbols (BRK.B, not BRK-B) probed LIVE against yfinance after the writer's "
                    "vendor-symbol translation (read-only; network)")
    args = ap.parse_args(argv)
    import time
    cases = []
    if args.fixture:
        with open(args.fixture, encoding="utf-8") as fh:
            cases += json.load(fh)
    # Symbols are taken exactly as given (no upper-casing): a malformed canonical symbol must be REFUSED by the translation, not silently repaired.
    for sym in [s.strip() for s in args.live_yfinance.split(",") if s.strip()]:
        case = {"symbol": sym, "source": SRC_YFINANCE}
        started = time.monotonic()
        try:
            case["raw"] = fetch_yfinance_raw(sym)
        except vendor_symbols.UnsupportedSymbolFormat:                  # `dry_run` reports the refusal itself; no request was made
            pass
        except TimeoutError:
            case["error"] = "timeout"
        except Exception as exc:                                        # noqa: BLE001 - a probe failure is a result, not a crash
            case["error"], case["error_type"] = "request_error", type(exc).__name__
        case["latency_ms"] = round((time.monotonic() - started) * 1000)
        cases.append(case)
    out = []
    for c in cases:
        r = dry_run(c["symbol"], c["source"], raw=c.get("raw"), error=c.get("error"), head=c.get("head"))
        if "latency_ms" in c:
            r["probe"] = {"latency_ms": c["latency_ms"], "error_type": c.get("error_type"), "n_keys": len(c["raw"]) if c.get("raw") else 0}
        out.append(r)
    print(json.dumps(out, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
