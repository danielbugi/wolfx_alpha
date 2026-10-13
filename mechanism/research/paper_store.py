"""Persistence for the forward paper tracker (migration 36: paper_session, paper_observation, paper_event). The ONLY module that writes them; it writes nothing else.

The tracker (`research.paper_tracker`) is pure and emits events; this module appends them, in one transaction per session, together with that session's cursor row (written LAST, so a session exists
iff it completed). Writes are idempotent (ON CONFLICT DO NOTHING plus an equality check: a conflicting row whose content differs is DRIFT and raises, it is never overwritten), hash-chained (every
observation has a root hash; every event carries the previous hash and its own), and verifiable (`verify` recomputes every hash and checks the order, read-only).

The caller owns the transaction: nothing here commits.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from psycopg2.extras import Json

from research import experiment_runner as E
from research import strategy_variants as SV


class StoreError(RuntimeError):
    pass


class Drift(StoreError):
    """A row that already exists differs from what this run computed: the evidence is not overwritten; a human looks."""


def _norm(o: Any) -> Any:
    """Numbers as they survive a round trip through a JSONB column: Postgres turns -0.0 into 0.0 and a float of 1e16 or more into a long integer, so both are normalised BEFORE hashing; otherwise a stored
    hash could stop verifying after the data was read back unchanged."""
    if isinstance(o, bool) or o is None or isinstance(o, str):
        return o
    if isinstance(o, float):
        return 0.0 if o == 0.0 else o
    if isinstance(o, int):
        return float(o) if abs(o) >= 10 ** 16 else o
    if isinstance(o, Mapping):
        return {k: _norm(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_norm(v) for v in o]
    return o


def canonical(o: Any) -> str:
    return json.dumps(_norm(o), sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha(o: Any) -> str:
    return hashlib.sha256(canonical(o).encode("utf-8")).hexdigest()


def observation_hash(p: Mapping[str, Any]) -> str:
    return sha({"kind": "paper_observation_v1", "variant_key": p["variant_key"], "definition_hash": p["definition_hash"], "symbol": p["symbol"], "direction": int(p["direction"]),
                "signal_session": p["signal_session"], "atr": float(p["atr"]), "hold_cap": int(p["hold_cap"]), "registered_cap": bool(p["registered_cap"]),
                "hist_h": [float(x) for x in p["hist_h"]], "hist_l": [float(x) for x in p["hist_l"]], "context": p["context"]})


def event_hash(ident: str, seq: int, event_type: str, session: str, payload: Mapping[str, Any], prev: str) -> str:
    return sha({"kind": "paper_event_v1", "ident": ident, "seq": int(seq), "type": event_type, "session": session, "payload": payload, "prev": prev})


def parse_ident(ident: str) -> Tuple[str, str, int, str]:
    key, symbol, direction, signal_session = ident.split("|")
    return key, symbol, int(direction), signal_session


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ identities
def load_variants(cur) -> Tuple[List[E.Variant], Dict[str, int]]:
    """The registered variants, read from the production registry and checked against the code: every definition hash is recomputed from its stored definition and must equal both the stored hash and
    the code's. Refuses unless all five registered variants are present, unchanged."""
    cur.execute("SELECT id, variant_key, variant_version, definition_hash, definition FROM strategy_variant ORDER BY id")
    rows = cur.fetchall()
    code = E.registered_variants()
    problems = E.identity_mismatches(code, [(r[1], r[2], r[3]) for r in rows])
    for r in rows:
        if SV.definition_hash(r[4]) != r[3].strip():
            problems.append(f"{r[1]} {r[2]}: stored hash does not match the stored definition")
    if problems:
        raise StoreError("registry identity mismatch: " + "; ".join(problems))
    ids = {r[1]: r[0] for r in rows}
    return code, {v.key: ids[v.key] for v in code}


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ cursor
def latest_session(cur) -> Optional[Dict[str, Any]]:
    """The newest processed session with its stored open-state snapshot, after re-verifying the snapshot's hash. None when nothing was ever processed."""
    cur.execute("SELECT session_date, scope, source, open_state, open_state_hash FROM paper_session ORDER BY session_date DESC LIMIT 1")
    r = cur.fetchone()
    if r is None:
        return None
    if sha(r[3]) != r[4].strip():
        raise StoreError(f"paper_session {r[0]}: the stored open_state does not match its stored hash (corrupted or altered); refusing to continue")
    return {"session_date": r[0], "scope": r[1], "source": r[2], "open_state": r[3], "open_state_hash": r[4].strip()}


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ writes
def _observation_id(cur, key: str, symbol: str, direction: int, signal_session: str) -> int:
    cur.execute("SELECT o.id FROM paper_observation o JOIN strategy_variant v ON v.id = o.variant_id WHERE v.variant_key = %s AND o.symbol = %s AND o.direction = %s AND o.signal_session = %s",
                (key, symbol, direction, signal_session))
    r = cur.fetchone()
    if r is None:
        raise StoreError(f"no observation {key}|{symbol}|{direction}|{signal_session}")
    return r[0]


def write_session(cur, *, session_date: date, scope: str, source: str, variant_ids: Mapping[str, int], events: Sequence[Mapping[str, Any]], counts: Mapping[str, int],
                  open_state: Mapping[str, Any], run_id: str, writer: str, code_ref: str, started_at: datetime, candidate_ids: Optional[Mapping[str, Optional[int]]] = None) -> Dict[str, int]:
    """Append one session: its new observations, its events, then its cursor row. All or nothing (the caller's transaction)."""
    written = {"observations": 0, "events": 0}
    ident_chain: Dict[str, Tuple[int, str, int]] = {}                 # ident -> (last seq, last hash, observation id)
    for ev in events:
        ident, etype, session, payload = ev["ident"], ev["type"], ev["session"], ev["payload"]
        key, symbol, direction, signal_session = parse_ident(ident)
        if etype == "registered":
            ohash = observation_hash(payload)
            cand = (candidate_ids or {}).get(f"{symbol}|{direction}")
            cur.execute("INSERT INTO paper_observation (variant_id, symbol, direction, signal_session, candidate_observation_id, atr, hold_cap, registered_cap, hist_h, hist_l, context, observation_hash, run_id) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (variant_id, symbol, direction, signal_session) DO NOTHING RETURNING id",
                        (variant_ids[key], symbol, direction, signal_session, cand, float(payload["atr"]), int(payload["hold_cap"]), bool(payload["registered_cap"]), [float(x) for x in payload["hist_h"]],
                         [float(x) for x in payload["hist_l"]], Json(payload["context"]), ohash, run_id))
            r = cur.fetchone()
            if r is None:
                cur.execute("SELECT id, observation_hash FROM paper_observation WHERE variant_id = %s AND symbol = %s AND direction = %s AND signal_session = %s", (variant_ids[key], symbol, direction, signal_session))
                oid, stored = cur.fetchone()
                if stored.strip() != ohash:
                    raise Drift(f"observation {ident} already exists with a different registration")
            else:
                oid = r[0]
                written["observations"] += 1
            ident_chain[ident] = (0, ohash, oid)
            continue
        if ident not in ident_chain:
            oid = _observation_id(cur, key, symbol, direction, signal_session)
            cur.execute("SELECT seq, event_hash FROM paper_event WHERE observation_id = %s ORDER BY seq DESC LIMIT 1", (oid,))
            last = cur.fetchone()
            if last is None:
                cur.execute("SELECT observation_hash FROM paper_observation WHERE id = %s", (oid,))
                ident_chain[ident] = (0, cur.fetchone()[0].strip(), oid)
            else:
                ident_chain[ident] = (last[0], last[1].strip(), oid)
        seq, prev, oid = ident_chain[ident]
        cur.execute("SELECT seq, event_hash, payload FROM paper_event WHERE observation_id = %s AND event_type = %s", (oid, etype))
        existing = cur.fetchone()
        if existing is not None:                                        # this event type already exists for the observation: it must be byte-for-byte the same content
            if canonical(existing[2]) != canonical(payload):
                raise Drift(f"event {etype} of {ident} already exists with a different payload")
            continue
        seq += 1
        ehash = event_hash(ident, seq, etype, session, payload, prev)
        cur.execute("INSERT INTO paper_event (observation_id, seq, event_type, session_date, payload, prev_hash, event_hash, run_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (oid, seq, etype, session, Json(payload), prev, ehash, run_id))
        ident_chain[ident] = (seq, ehash, oid)
        written["events"] += 1
    state_hash = sha(open_state)
    cur.execute("INSERT INTO paper_session (session_date, scope, source, n_variants, n_candidates, n_registered, n_skipped_open, n_entered, n_closed, n_not_enterable, n_open_after, open_state, open_state_hash, "
                "run_id, writer, code_ref, started_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (session_date, scope, source, int(counts["n_variants"]), int(counts["n_candidates"]), int(counts["n_registered"]), int(counts["n_skipped_open"]), int(counts["n_entered"]),
                 int(counts["n_closed"]), int(counts["n_not_enterable"]), len(open_state["active"]), Json(open_state), state_hash, run_id, writer, code_ref, started_at))
    return written


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ audit
def verify(cur) -> List[str]:
    """Read-only audit of everything stored: every observation hash and event hash is recomputed, every chain link and the event order are checked, every session snapshot hash is checked, and the
    session cursor is contiguous in time. Returns the problems found (empty = healthy)."""
    problems: List[str] = []
    cur.execute("SELECT o.id, v.variant_key, v.definition_hash, o.symbol, o.direction, o.signal_session, o.atr, o.hold_cap, o.registered_cap, o.hist_h, o.hist_l, o.context, o.observation_hash "
                "FROM paper_observation o JOIN strategy_variant v ON v.id = o.variant_id ORDER BY o.id")
    obs = {}
    for r in cur.fetchall():
        payload = {"variant_key": r[1], "definition_hash": r[2].strip(), "symbol": r[3], "direction": r[4], "signal_session": r[5].isoformat(), "atr": r[6], "hold_cap": r[7], "registered_cap": r[8],
                   "hist_h": r[9], "hist_l": r[10], "context": r[11]}
        if observation_hash(payload) != r[12].strip():
            problems.append(f"observation {r[1]}|{r[3]}|{r[4]}|{r[5]}: stored hash does not match its content")
        obs[r[0]] = (f"{r[1]}|{r[3]}|{r[4]}|{r[5].isoformat()}", r[12].strip())
    cur.execute("SELECT observation_id, seq, event_type, session_date, payload, prev_hash, event_hash FROM paper_event ORDER BY observation_id, seq")
    last: Dict[int, Tuple[int, str, str]] = {}
    for oid, seq, etype, session, payload, prev, ehash in cur.fetchall():
        ident, root = obs.get(oid, (None, None))
        if ident is None:
            problems.append(f"event of unknown observation {oid}")
            continue
        l_seq, l_hash, l_type = last.get(oid, (0, root, None))
        if seq != l_seq + 1:
            problems.append(f"{ident}: seq {seq} after {l_seq}")
        if prev.strip() != l_hash:
            problems.append(f"{ident}: event {seq} does not link to the previous hash")
        if event_hash(ident, seq, etype, session.isoformat(), payload, prev.strip()) != ehash.strip():
            problems.append(f"{ident}: event {seq} ({etype}) stored hash does not match its content")
        if etype == "hindsight" and payload.get("decision_time_information") is not False:
            problems.append(f"{ident}: hindsight event is not flagged as hindsight-only")
        last[oid] = (seq, ehash.strip(), etype)
    cur.execute("SELECT session_date, open_state, open_state_hash FROM paper_session ORDER BY session_date")
    prev_date = None
    for d, st, h in cur.fetchall():
        if sha(st) != h.strip():
            problems.append(f"session {d}: open_state does not match its hash")
        if prev_date is not None and d <= prev_date:
            problems.append(f"session {d} is not after {prev_date}")
        prev_date = d
    return problems
