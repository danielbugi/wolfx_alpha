"""STRICT point-in-time reader for the SEC catalyst feed. SELECT only; it reads no clock and no environment: the decision `cutoff` is always supplied.

A filing is visible to a decision taken at `cutoff` only if EVERY one of these held at or before `cutoff`:

  1. SEC acceptance      revision.known_at (= the SEC acceptance time, grade A, observed provenance)
  2. first observed      the first-seen log row of that revision's content (`source_observation.observed_at`)
  3. database ingestion  revision.ingested_at (stamped by the database)
  4. the symbol link     identifier_link.recorded_at, with no closure recorded at or before the cutoff that ended it for the filing date
and the classification label additionally needs
  5. classification      classified_at <= cutoff, and not replaced by a row classified at or before cutoff.

If a filing was corrected (revision n+1) after the cutoff, the decision sees revision n. Nothing later than the cutoff can change what a cutoff
returns: re-running the same question after more rows were appended gives the same answer (tested).

Why the older tracker reader cannot be trusted for this feed (see docs/research/CATALYST_PHASE1A.md): it filters on `known_at <= cutoff` only, so a
filing accepted before the cutoff but INGESTED after it (the normal case: acceptance precedes our first sight) is shown as decision-time context
when the context is built later than the cutoff; and it matches `market_event.symbol`, which for SEC events is deliberately NULL.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Mapping, Optional, Sequence

from market_intelligence import identifier_links as il

SOURCE = "sec_edgar"
FIRST_SEEN_DATASET = "filing_presence"

_REVISIONS_SQL = """
SELECT DISTINCT ON (r.event_key)
       r.event_key, r.revision, r.id AS revision_id, r.source_ref AS accession, r.known_at, r.published_at, r.ingested_at, r.session_timing,
       r.event_time, r.pit_grade, r.payload, o.observed_at AS first_observed_at
FROM market_event_revision r
JOIN source_observation o
  ON o.source = %(source)s AND o.dataset = %(dataset)s AND o.period_key = r.source_ref AND o.seq = r.revision AND o.observed_at <= %(cutoff)s
WHERE r.source = %(source)s
  AND r.provenance = 'observed' AND r.pit_grade IN ('A', 'B')
  AND r.known_at IS NOT NULL AND r.known_at <= %(cutoff)s AND r.ingested_at <= %(cutoff)s
  AND r.known_at > %(since)s
ORDER BY r.event_key, r.revision DESC
"""

_LINKS_SQL = """
SELECT l.id, l.cik, l.symbol, l.basis, l.valid_from, l.recorded_at, z.absent_on, z.recorded_at
FROM identifier_link l
LEFT JOIN identifier_link_end z ON z.link_id = l.id AND z.recorded_at <= %(cutoff)s
WHERE (l.cik = ANY(%(ciks)s) OR upper(replace(replace(l.symbol, '.', '-'), '/', '-')) = ANY(%(canon)s)) AND l.recorded_at <= %(cutoff)s
"""

_CLASS_SQL = """
SELECT c.revision_id, c.classifier, c.classifier_version, c.label, c.classified_at
FROM catalyst_classification c
WHERE c.revision_id = ANY(%(rids)s) AND c.classified_at <= %(cutoff)s
  AND NOT EXISTS (SELECT 1 FROM catalyst_classification s WHERE s.supersedes_id = c.id AND s.classified_at <= %(cutoff)s)
ORDER BY c.revision_id, c.classifier, c.classifier_version
"""


def _rows(cur, sql: str, params: Mapping[str, Any]) -> List[Any]:
    cur.execute(sql, dict(params))
    return cur.fetchall()


def _get(row: Any, key: str, idx: int) -> Any:
    return row[key] if isinstance(row, Mapping) else row[idx]


def visible_filings(cur, symbols: Sequence[str], cutoff: datetime, *, lookback_days: int = 30, include_reconstructed_links: bool = False,
                    include_unmapped: bool = False) -> List[Dict[str, Any]]:
    """The SEC filings of the requested symbols that a decision at `cutoff` could have known. Each row carries every availability timestamp so the
    caller can audit the decision. By default only filings whose symbol link is OBSERVED are returned; `include_reconstructed_links` adds those mapped
    through a reconstructed link (flagged), `include_unmapped` adds filings with no resolvable symbol (symbol None, flagged) -- both for research
    that wants the full picture, never for a model input."""
    if cutoff.tzinfo is None:
        raise ValueError("cutoff must be timezone-aware")
    if lookback_days <= 0:
        raise ValueError("lookback_days must be positive")
    want = {il.canonical_symbol(s): s for s in symbols}
    params = {"source": SOURCE, "dataset": FIRST_SEEN_DATASET, "cutoff": cutoff, "since": cutoff - timedelta(days=lookback_days)}
    revs = _rows(cur, _REVISIONS_SQL, params)
    if not revs:
        return []
    cols = ("event_key", "revision", "revision_id", "accession", "known_at", "published_at", "ingested_at", "session_timing", "event_time", "pit_grade",
            "payload", "first_observed_at")
    parsed = [{c: _get(r, c, i) for i, c in enumerate(cols)} for r in revs]
    ciks = sorted({int(p["payload"]["cik"]) for p in parsed})
    links = [il.StoredLink(r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7]) for r in _rows(cur, _LINKS_SQL, {"cutoff": cutoff, "ciks": ciks, "canon": sorted(want)})]
    labels: Dict[int, List[Dict[str, Any]]] = {}
    for r in _rows(cur, _CLASS_SQL, {"cutoff": cutoff, "rids": [p["revision_id"] for p in parsed]}):
        labels.setdefault(r[0], []).append({"classifier": r[1], "version": r[2], "label": r[3], "classified_at": r[4]})
    out: List[Dict[str, Any]] = []
    for p in parsed:
        pay = p["payload"]
        cik = int(pay["cik"])
        filing_date = date.fromisoformat(pay["filing_date"])
        syms, basis = il.symbols_as_of(links, cik, filing_date, cutoff)
        # one row per listed class of the filer that the caller asked about; a symbol claimed by two CIKs on that date is ambiguous and is not attached
        candidates: List[Any] = []
        if not syms:
            if include_unmapped:
                candidates.append((None, "unmapped"))
        else:
            for sym in syms:
                if il.canonical_symbol(sym) not in want:
                    continue
                if len(il.claimants(links, sym, filing_date, cutoff)) > 1:
                    if include_unmapped:
                        candidates.append((None, "ambiguous"))
                    continue
                if basis == "reconstructed" and not include_reconstructed_links:
                    continue
                candidates.append((sym, basis))
        for sym, sym_basis in candidates:
            link_times = [l.recorded_at for l in links if l.cik == cik and sym is not None and il.canonical_symbol(l.symbol) == il.canonical_symbol(sym)]
            rec_at = max(link_times) if link_times else None
            avail = [p["known_at"], p["ingested_at"], p["first_observed_at"]] + ([rec_at] if rec_at is not None else [])
            out.append({
                "accession": p["accession"], "cik": cik, "symbol": want.get(il.canonical_symbol(sym)) if sym else None, "symbol_basis": sym_basis,
                "form_type": pay["form_type"], "form_family": pay.get("form_family"), "items": list(pay.get("items") or []), "filing_date": filing_date,
                "accepted_at": p["published_at"], "session_timing": p["session_timing"], "revision": p["revision"], "amends_accession": pay.get("amends_accession"),
                "known_at": p["known_at"], "first_observed_at": p["first_observed_at"], "ingested_at": p["ingested_at"], "link_recorded_at": rec_at,
                "available_at": max(avail), "classifications": labels.get(p["revision_id"], []),
            })
    out.sort(key=lambda r: (r["accepted_at"], r["accession"], r["symbol"] or ""))
    return out
