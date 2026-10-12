"""Database side of the catalyst-classification layer (migration 28): append a `CatalystClassification` (filing_contract) against the immutable
event revision it read, supersede one, and read what was believed AS OF a time.

Facts are never touched: this module has no statement that writes `market_event*`. The database stamps `classified_at` (this module never reads a
clock) and refuses a classification whose pinned `fact_hash` is not the revision's, so a result can never be re-pointed at different content."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional

from psycopg2.extras import Json, RealDictCursor

from market_intelligence.filing_contract import CatalystClassification, FilingContractError

_COLS = ("id, revision_id, event_key, fact_hash, classifier, classifier_version, method, classifier_identity, label, evidence, confidence, "
         "supersedes_id, classified_at, code_ref")


def _dumps(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False)


@dataclass
class Appended:
    id: int
    created: bool


def append_classification(cur, c: CatalystClassification, identity: Mapping[str, Any], revision_id: int, event_key: str, code_ref: str) -> Appended:
    """Append one classification. Idempotent on (revision, classifier, classifier_version): re-running the same classifier version writes nothing
    and returns the stored row (a different outcome needs a new classifier_version). `identity` is the typed classifier identity the database
    requires: rule -> {rule_id}; model -> {model_id, prompt_hash, ...}; human -> {reviewer}. `c.supersedes` names the row it replaces."""
    if not identity:
        raise FilingContractError("a classifier identity is required (rule_id / model_id+prompt_hash / reviewer)")
    cur = cur.connection.cursor()
    cur.execute(
        "INSERT INTO catalyst_classification (revision_id, event_key, fact_hash, classifier, classifier_version, method, classifier_identity, "
        "label, evidence, confidence, supersedes_id, code_ref) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT (revision_id, classifier, classifier_version) DO NOTHING RETURNING id",
        (revision_id, event_key, c.fact_hash, c.classifier, c.classifier_version, c.method, Json(dict(identity), dumps=_dumps), c.label,
         Json(dict(c.evidence), dumps=_dumps), c.confidence, c.supersedes, code_ref))
    row = cur.fetchone()
    if row:
        return Appended(row[0], True)
    cur.execute("SELECT id FROM catalyst_classification WHERE revision_id = %s AND classifier = %s AND classifier_version = %s",
                (revision_id, c.classifier, c.classifier_version))
    return Appended(cur.fetchone()[0], False)


def _rows(cur, sql: str, args: List[Any]) -> List[Dict[str, Any]]:
    cur = cur.connection.cursor(cursor_factory=RealDictCursor)
    cur.execute(sql, args)
    out = []
    for r in cur.fetchall():
        r = dict(r)
        r["fact_hash"] = r["fact_hash"].strip()
        if r["confidence"] is not None:
            r["confidence"] = float(r["confidence"])
        out.append(r)
    return out


def classifications_as_of(cur, cutoff: datetime, *, revision_id: Optional[int] = None, classifier: Optional[str] = None) -> List[Dict[str, Any]]:
    """The CURRENT (not superseded) classifications as they stood at `cutoff`: classified_at <= cutoff, and not replaced by a row that was itself
    classified at or before `cutoff`. Something classified after the cutoff does not exist yet."""
    if cutoff.tzinfo is None:
        raise ValueError("cutoff must be timezone-aware")
    where, args = ["c.classified_at <= %s", "NOT EXISTS (SELECT 1 FROM catalyst_classification s WHERE s.supersedes_id = c.id AND s.classified_at <= %s)"], [cutoff, cutoff]
    if revision_id is not None:
        where.append("c.revision_id = %s")
        args.append(revision_id)
    if classifier is not None:
        where.append("c.classifier = %s")
        args.append(classifier)
    cols = ", ".join("c." + x.strip() for x in _COLS.split(","))
    return _rows(cur, f"SELECT {cols} FROM catalyst_classification c WHERE {' AND '.join(where)} ORDER BY c.id", args)


def history(cur, revision_id: int) -> List[Dict[str, Any]]:
    """Every classification of one revision, oldest first, superseded rows included."""
    return _rows(cur, f"SELECT {_COLS} FROM catalyst_classification WHERE revision_id = %s ORDER BY id", [revision_id])
