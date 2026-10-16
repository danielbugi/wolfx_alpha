"""Database side of the SEC catalyst feed (migration 37 + the existing 25 / 27 / 28 tables). INSERT-only; it reads no clock and no environment: every
availability timestamp (`ingested_at`, `observed_at`, `classified_at`, `recorded_at`, `first_fetched_at`) is stamped by the database.

What one ingest does, in this order, inside the caller's transaction (each filing under its own SAVEPOINT so one bad record cannot abort the batch):
  1. FIRST SEEN   append the filing to the first-seen log (`source_observation`: series = cik + accession). An identical re-fetch writes nothing; a
                  CHANGED filing (the source corrected it) appends the next link of the chain.
  2. FACT         append the event revision (`market_event_revision`). market_event.symbol stays NULL: the symbol is resolved at READ time from the
                  identifier links as of the decision cutoff, so a late mapping or a ticker change can never rewrite an earlier decision.
  3. INTERPRET    append the rule classifications (`catalyst_classification`), each pinned to the revision it read.
Idempotent: re-running the same filings writes nothing. `apply=False` reports what would be written and writes nothing.
"""
from __future__ import annotations

import dataclasses
import gzip
import hashlib
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

import psycopg2
import psycopg2.errors
from psycopg2.extras import Json

from market_intelligence import catalyst_rules as rules
from market_intelligence import classification_store as cls
from market_intelligence import first_seen as fs
from market_intelligence import first_seen_store as fss
from market_intelligence import identifier_links as il
from market_intelligence import store
from market_intelligence.edgar_collector import CollectedFact

SOURCE = "sec_edgar"
DATASET = "filing_presence"
PAYLOAD_SYMBOL_BASIS = "resolved_at_read"


# ------------------------------------------------------------------ source snapshots
def store_snapshot(cur, source: str, kind: str, subject: str, url: str, sha256: Optional[str], body: bytes, run_key: str, code_ref: str) -> Tuple[int, bool]:
    """(snapshot id, created). One row per distinct body: an unchanged document stores nothing and returns the existing id. The hash is computed
    here from the bytes; a caller-supplied `sha256` that disagrees is refused (a snapshot must describe exactly what it holds)."""
    actual = hashlib.sha256(body).hexdigest()
    if sha256 is not None and sha256 != actual:
        raise ValueError("supplied sha256 does not match the body; refusing to store a snapshot that misdescribes its content")
    sha256 = actual
    cur.execute("INSERT INTO source_snapshot (source, kind, subject, url, sha256, byte_length, body_gz, run_key, code_ref) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (source, kind, subject, sha256) DO NOTHING RETURNING id",
                (source, kind, subject, url, sha256, len(body), psycopg2.Binary(gzip.compress(body, 6, mtime=0)), run_key, code_ref))
    row = cur.fetchone()
    if row:
        return row[0], True
    cur.execute("SELECT id FROM source_snapshot WHERE source = %s AND kind = %s AND subject = %s AND sha256 = %s", (source, kind, subject, sha256))
    return cur.fetchone()[0], False


def read_snapshot(cur, snapshot_id: int) -> bytes:
    cur.execute("SELECT body_gz FROM source_snapshot WHERE id = %s", (snapshot_id,))
    return gzip.decompress(bytes(cur.fetchone()[0]))


# ------------------------------------------------------------------ identifier links
def load_open_links(cur) -> List[il.OpenLink]:
    cur.execute("SELECT l.id, l.cik, l.symbol, l.basis, l.valid_from FROM identifier_link l "
                "WHERE NOT EXISTS (SELECT 1 FROM identifier_link_end e WHERE e.link_id = l.id) ORDER BY l.id")
    return [il.OpenLink(*r) for r in cur.fetchall()]


def latest_applied_date(cur) -> Optional[date]:
    cur.execute("SELECT greatest((SELECT max(valid_from) FROM identifier_link), (SELECT max(absent_on) FROM identifier_link_end))")
    return cur.fetchone()[0]


def apply_link_plan(cur, plan: il.LinkPlan, snapshot_id: int, code_ref: str) -> Dict[str, int]:
    made = ended = 0
    for pair, basis, valid_from in plan.new_links:
        cur.execute("INSERT INTO identifier_link (cik, symbol, valid_from, basis, source, snapshot_id, code_ref) VALUES (%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT DO NOTHING", (pair.cik, pair.symbol, valid_from, basis, il.SOURCE, snapshot_id, code_ref))
        made += cur.rowcount
    for l in plan.closures:
        cur.execute("INSERT INTO identifier_link_end (link_id, absent_on, snapshot_id, code_ref) VALUES (%s,%s,%s,%s) ON CONFLICT (link_id) DO NOTHING",
                    (l.link_id, plan.snapshot_date, snapshot_id, code_ref))
        ended += cur.rowcount
    return {"links_created": made, "links_ended": ended, "links_unchanged": plan.unchanged}


def refresh_links(cur, body: bytes, snapshot_date: date, snapshot_id: int, code_ref: str, *, reconstruct: bool = False, apply: bool = False) -> Dict[str, Any]:
    """Parse the ticker file, plan against the stored open links, and (when `apply`) append. Refusals raise IdentifierError and write nothing."""
    parsed = il.parse_company_tickers(body)
    plan = il.plan_update(parsed, snapshot_date, load_open_links(cur), latest_applied=latest_applied_date(cur), reconstruct=reconstruct)
    out: Dict[str, Any] = {"applied": apply, "plan": plan.summary(), "parse": dict(parsed.counters), "rejected_entries": len(parsed.rejected),
                           "symbols_on_several_ciks": len(parsed.symbols_on_several_ciks)}
    if apply:
        out.update(apply_link_plan(cur, plan, snapshot_id, code_ref))
    return out


# ------------------------------------------------------------------ filings
@dataclass
class IngestResult:
    applied: bool
    seen: int = 0
    events_created: int = 0
    revisions_created: int = 0        # revisions > 1: the source CHANGED a filing it had already shown us
    unchanged: int = 0
    classifications_created: int = 0
    observations_written: int = 0
    errors: List[Dict[str, str]] = field(default_factory=list)

    def summary(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


def _draft(c: CollectedFact):
    d = dataclasses.replace(c.draft, symbol=None)
    return dataclasses.replace(d, payload={**dict(d.payload), "symbol_basis": PAYLOAD_SYMBOL_BASIS})


def _observation(c: CollectedFact) -> fs.Observation:
    f = c.fact
    return fs.Observation(SOURCE, DATASET, "cik", str(f.cik), {**f.content(), "source_record_hash": c.source_record_hash}, period_key=f.accession,
                          source_asof=f.accepted_at)


def ingest_facts(cur, facts: Sequence[CollectedFact], run_key: str, code_ref: str, *, apply: bool = False, poll_subjects: Sequence[str] = (),
                 poll_status: str = "complete", poll_detail: Optional[Dict[str, Any]] = None, classify: bool = True) -> IngestResult:
    res = IngestResult(apply)
    res.seen = len(facts)
    if not apply:
        plan = fss.record_run(cur, [_observation(c) for c in facts], run_key, code_ref, apply=False).plan
        res.observations_written = len(plan.appends)
        return res
    for c in facts:
        cur.execute("SAVEPOINT ingest_one")
        try:
            # 1. first seen -- the same savepoint as the fact, so "seen" and "stored" can never disagree
            wrote = fss.record_run(cur, [_observation(c)], run_key, code_ref, apply=True).written
            before = _latest_revision(cur, c.draft.event_key)
            # 2. the fact
            w = store.append_event_revision(cur, _draft(c), declared_basis="vendor_published")
            if w.created:
                if before is None:
                    res.events_created += 1
                else:
                    res.revisions_created += 1
            else:
                res.unchanged += 1
            # 3. the interpretation
            if classify:
                for k in rules.classify_all(c.fact):
                    a = cls.append_classification(cur, k, rules.RULE_IDENTITY[k.classifier], w.id, c.draft.event_key, code_ref)
                    res.classifications_created += int(a.created)
            res.observations_written += wrote
        except (psycopg2.Error, ValueError) as e:
            cur.execute("ROLLBACK TO SAVEPOINT ingest_one")
            res.errors.append({"accession": c.fact.accession, "error": f"{type(e).__name__}: {str(e).splitlines()[0][:200]}"})
        cur.execute("RELEASE SAVEPOINT ingest_one")
    cur.execute("INSERT INTO source_poll (run_key, source, dataset, status, subjects_polled, n_new_observations, detail, code_ref) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (run_key, source, dataset) DO NOTHING",
                (run_key, SOURCE, DATASET, poll_status, sorted(set(poll_subjects)), res.observations_written, Json(dict(poll_detail or {})), code_ref))
    return res


def load_known_headers(cur) -> Dict[str, datetime]:
    """accession -> the header acceptance instant already stored for it (latest revision). A re-poll reconciles a known filing against THIS value instead of fetching the
    header again, so an unchanged filing yields an identical payload and writes nothing."""
    cur.execute("SELECT DISTINCT ON (source_ref) source_ref, payload->'acceptance'->>'header' FROM market_event_revision "
                "WHERE source = %s AND payload ? 'acceptance' ORDER BY source_ref, revision DESC", (SOURCE,))
    return {acc: datetime.fromisoformat(h) for acc, h in cur.fetchall() if h}


def _latest_revision(cur, event_key: str) -> Optional[int]:
    cur.execute("SELECT max(revision) FROM market_event_revision WHERE event_key = %s", (event_key,))
    return cur.fetchone()[0]
