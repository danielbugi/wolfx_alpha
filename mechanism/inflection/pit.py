"""Filing-version-aware (point-in-time) SEC quarters for the inflection engine. PURE (standard library; reuses the SEC statement normalizer).

The problem this solves: `sec_quarter` keeps the LATEST value of every quarter, so a feature built from it for a quarter published in 2019 can use a figure restated in 2021. For live monitoring
that is harmless; for a historical study it leaks the future. Here each company-quarter is evaluated with ONLY the filings that were public at the quarter's own first-public date:

    first_public(q)   the earliest availability among the filings that carry the quarter's revenue / operating income / net income. Availability = the SEC acceptance date (US Eastern)
                      when `sec_filing` has the filing, otherwise the filing date (kind recorded: 'sec_acceptance' | 'sec_filed_date').
    facts known at D  every raw fact (sec_fact) whose filing was public on or before D; a restating or amending filing accepted after D is invisible. `normalize` (the same code that builds
                      sec_quarter) then resolves, per period and metric, the newest of those filings.
    feature of q      compute_features over the quarters known at D that end on or before q (the quarter itself, its prior year and previous quarter, as THEY were reported by D),
                      keeping q's row.

Provenance: every feature carries the accession of the filing behind the quarter, its prior-year and previous quarter (`prov:` notes), the availability kind and `pit_as_of`. A later restatement of a
quarter is reported (`restated_since:` note on a re-evaluation) but never rewrites the stored feature or event, so re-running on unchanged filings is a no-op and a restatement can never
move a historical event.

What is NOT claimed: availability is the 10-Q / 10-K acceptance date, which can follow the earnings press release (an 8-K) by days; it is therefore LATE, never early, relative to when the figures
first became public. That is the safe direction for leakage, and the limit on how early an event could really have been known.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from data_updaters import sec_statements as S
from inflection import config as C
from inflection import features as F

FIRST_PUBLIC_METRICS = ("revenue", "operating_income", "net_income")
WINDOW_DAYS = 530                 # acceleration needs the previous quarter (91 +- 25) AND its prior year (a further 365 +- 25): up to 506 days back
FLOW_METRICS = ("revenue", "operating_income", "net_income", "operating_cash_flow", "free_cash_flow")


def _day(x: Any) -> date:
    return x.date() if isinstance(x, datetime) else x


def availability_of(accession: str, filed: date, accepted: Mapping[str, date]) -> Tuple[date, str]:
    a = accepted.get(accession)
    return (a, "sec_acceptance") if a is not None else (filed, "sec_filed_date")


def with_availability(facts: Sequence[S.RawFact], accepted: Mapping[str, date]) -> List[S.RawFact]:
    """The facts with `filed` replaced by the date the filing was PUBLIC (acceptance date when known), so every as-of cut and every 'newest filing wins' rule uses the public date."""
    return [replace(f, filed=availability_of(f.accession, f.filed, accepted)[0]) for f in facts]


def first_public(facts: Sequence[S.RawFact], accepted: Mapping[str, date]) -> Dict[date, Tuple[date, str, str]]:
    """period end -> (date first public, kind, accession of that filing), over the filings carrying revenue / operating income / net income for the period."""
    out: Dict[date, Tuple[date, str, str]] = {}
    for f in facts:
        if f.metric not in FIRST_PUBLIC_METRICS:
            continue
        d, kind = availability_of(f.accession, f.filed, accepted)
        cur = out.get(f.end)
        if cur is None or (d, f.accession) < (cur[0], cur[2]):
            out[f.end] = (d, kind, f.accession)
    return out


def known_quarters(pub_facts: Sequence[S.RawFact], asof: date) -> Dict[date, Dict[str, S.QuarterRow]]:
    """period end -> {metric: normalized row} using only filings public on or before `asof` (`pub_facts` already carry public dates)."""
    table: Dict[date, Dict[str, S.QuarterRow]] = {}
    for r in S.normalize(pub_facts, asof=asof):
        if r.metric in FLOW_METRICS:
            table.setdefault(r.period_end, {})[r.metric] = r
    return table


def _quarter_data(period: date, rows: Mapping[str, S.QuarterRow], accepted_at: Optional[date] = None, kind: Optional[str] = None) -> F.QuarterData:
    any_row = next(iter(rows.values()))
    v = lambda k: rows[k].value if k in rows else None  # noqa: E731
    return F.QuarterData(period, any_row.fiscal_year, any_row.fiscal_quarter, v("revenue"), v("operating_income"), v("net_income"), v("operating_cash_flow"), v("free_cash_flow"),
                         None, accepted_at, kind)


def pit_items(facts: Sequence[S.RawFact], accepted: Mapping[str, date], sector: Optional[str] = None, since: Optional[date] = None, latest: Optional[Mapping[date, Mapping[str, S.QuarterRow]]] = None
              ) -> List[Dict[str, Any]]:
    """One item per company-quarter first public on or after `since` (all when None): the feature computed from the figures known at that date, its availability and provenance.

    `latest` (the quarters as known today) is optional: when given, a quarter whose revenue / operating income / net income have since been restated gets a `restated_since` note (information
    only: the stored feature stays the as-published one)."""
    pub = with_availability(facts, accepted)
    items: List[Dict[str, Any]] = []
    for q, (d, kind, accn) in sorted(first_public(facts, accepted).items()):
        if since is not None and d < since:
            continue
        table = known_quarters(pub, d)
        if q not in table or not (set(table[q]) & {"revenue", "operating_income", "net_income"}):
            continue
        window = [p for p in table if q - timedelta(days=WINDOW_DAYS) <= p <= q]
        qd = [_quarter_data(p, table[p], d if p == q else None, kind if p == q else None) for p in sorted(window)]
        ft = next((x for x in F.compute_features(qd, sector) if x["quarter"] == q), None)
        if ft is None:
            continue
        srt = sorted(qd, key=lambda r: r.quarter)
        idx = [r.quarter for r in srt].index(q)
        py, pq = F._near(srt, idx, C.YEAR_DAYS, C.YEAR_TOL), F._near(srt, idx, C.QUARTER_DAYS, C.QUARTER_TOL)
        notes = list(ft["notes"])
        notes.append(f"pit_as_of:{d.isoformat()}")
        notes.append(f"prov:q={_accessions(table[q])}")
        if py is not None:
            notes.append(f"prov:py={_accessions(table[py.quarter])}")
        if pq is not None:
            notes.append(f"prov:pq={_accessions(table[pq.quarter])}")
        if latest is not None and q in latest:
            changed = [m for m in FIRST_PUBLIC_METRICS if m in latest[q] and m in table[q] and abs(latest[q][m].value - table[q][m].value) > 1e-6 * max(1.0, abs(table[q][m].value))]
            if changed:
                notes.append("restated_since:" + ",".join(changed))
        ft = {**ft, "notes": notes, "accepted_at": d, "accepted_kind": kind}
        items.append({"feature": ft, "available_at": d, "availability_basis": kind, "source": C.SOURCE_SEC_PIT, "accession": accn})
    return items


def _accessions(rows: Mapping[str, S.QuarterRow]) -> str:
    """The distinct filings behind a quarter's figures, newest metric-sources first, as short accession list (a derived value names the newest of its components)."""
    seen: List[str] = []
    for m in FLOW_METRICS:
        r = rows.get(m)
        if r is not None and r.accession not in seen:
            seen.append(r.accession)
    return "+".join(seen)


def audit_leakage(items: Sequence[Mapping[str, Any]], pub_facts: Sequence[S.RawFact]) -> List[str]:
    """Self-check used by tests and the study: no figure behind an item may come from a filing public AFTER the item's availability. Returns the violations (empty = clean)."""
    pub_date = {f.accession: f.filed for f in pub_facts}
    bad: List[str] = []
    for it in items:
        d = it["available_at"]
        for n in it["feature"]["notes"]:
            if n.startswith("prov:"):
                for accn in n.split("=", 1)[1].split("+"):
                    if accn and pub_date.get(accn, d) > d:
                        bad.append(f"{it['feature']['quarter']}: {n} uses {accn} public {pub_date[accn]} > {d}")
    return bad
