"""SEC company identity behind a listed symbol (migration 42, sec_cik_lineage; LAB ONLY, the live inflection engine does not call this yet).

The SEC ticker file maps a symbol to today's registrant only. This module answers, for a symbol at an as-of date:
  * which CIKs its filing history is read from (`sources`): the current CIK, plus a predecessor's filings for periods that ended before the succession,
    but only for a CONFIRMED holding-company reorganisation or redomicile backed by an 8-K12B / 8-K12G3. Nothing is ever linked on the ticker alone;
  * from which date its prices belong to the current company (`price_identity_from`): ticker reuse and blank-check shells cut earlier prices;
  * whether it belongs in common-stock research at all (`research_eligible`): debt listings and blank-check companies do not.

Identity is preserved: predecessor facts keep their own CIK, accession and filing date, so the point-in-time engine (inflection/pit.py) dates every
quarter by the filing that first made it public. A successor's comparatives for pre-succession quarters no longer look like late first appearances,
because the predecessor's original filing is earlier.

Point in time: a link is only applied from its `known_at`. Before that date the symbol was the predecessor (the ticker file then pointed to it), so the
predecessor alone is the source.
"""
from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

SEED = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sec_lineage_seed.csv")
INHERITING = ("holding_company_reorganization", "redomicile")
SUCCESSION_FORMS = ("8-K12B", "8-K12G3")
DETECT_MIN_PRICE_LEAD_DAYS = 365


class LineageError(ValueError):
    pass


@dataclass(frozen=True)
class Link:
    symbol: str
    successor_cik: int
    predecessor_cik: Optional[int]
    relation: str
    effective_date: date
    known_at: date
    evidence_accession: Optional[str]
    evidence_form: Optional[str]
    inherit_history: bool
    research_eligible: bool
    price_identity_from: Optional[date]
    status: str
    note: str = ""
    id: Optional[int] = None
    supersedes_id: Optional[int] = None

    def validate(self) -> "Link":
        """The same rules as the table's CHECK constraints, so a seed file is refused before it reaches a database."""
        if self.inherit_history and not (self.status == "confirmed" and self.predecessor_cik and self.predecessor_cik != self.successor_cik
                                         and self.relation in INHERITING and self.evidence_accession and self.evidence_form in SUCCESSION_FORMS):
            raise LineageError(f"{self.symbol}: inheriting history needs a confirmed {'/'.join(INHERITING)} with a predecessor CIK and an 8-K12B/8-K12G3")
        if self.price_identity_from is not None and self.relation not in ("ticker_reuse", "spac_combination"):
            raise LineageError(f"{self.symbol}: a price identity cut is only for ticker reuse or a SPAC combination")
        if self.relation == "non_equity_listing" and self.research_eligible:
            raise LineageError(f"{self.symbol}: a non-equity listing cannot be research eligible")
        return self


def _d(s: Any) -> Optional[date]:
    if s in (None, ""):
        return None
    return s if isinstance(s, date) else date.fromisoformat(str(s)[:10])


def _i(s: Any) -> Optional[int]:
    return None if s in (None, "") else int(s)


def _b(s: Any) -> bool:
    return s if isinstance(s, bool) else str(s).strip().lower() in ("true", "t", "1", "yes")


def from_row(r: Mapping[str, Any]) -> Link:
    return Link(symbol=str(r["symbol"]).upper(), successor_cik=int(r["successor_cik"]), predecessor_cik=_i(r.get("predecessor_cik")), relation=r["relation"],
                effective_date=_d(r["effective_date"]), known_at=_d(r["known_at"]), evidence_accession=r.get("evidence_accession") or None,
                evidence_form=r.get("evidence_form") or None, inherit_history=_b(r["inherit_history"]), research_eligible=_b(r["research_eligible"]),
                price_identity_from=_d(r.get("price_identity_from")), status=r["status"], note=r.get("note") or "", id=_i(r.get("id")),
                supersedes_id=_i(r.get("supersedes_id"))).validate()


def load_seed(path: str = SEED) -> List[Link]:
    with open(path, encoding="utf-8", newline="") as f:
        return [from_row(r) for r in csv.DictReader(f)]


def load_db(db) -> List[Link]:
    """Current rows only: a row another row supersedes is history, not the current view."""
    return [from_row(r) for r in db.execute_dict_query("""
        SELECT * FROM sec_cik_lineage l WHERE NOT EXISTS (SELECT 1 FROM sec_cik_lineage s WHERE s.supersedes_id = l.id) ORDER BY symbol, id""")]


def by_symbol(links: Iterable[Link]) -> Dict[str, Link]:
    out: Dict[str, Link] = {}
    for l in links:
        if l.symbol in out:
            raise LineageError(f"{l.symbol}: more than one current lineage row")
        out[l.symbol] = l
    return out


# ------------------------------------------------------------------------------------------------------------------------------------------------ resolution
def sources(symbol: str, current_cik: int, links: Mapping[str, Link], asof: date) -> List[Tuple[int, Optional[date]]]:
    """[(cik, periods_before)]: read this CIK's filings, only for periods ending before `periods_before` when it is set."""
    l = links.get(symbol.upper())
    if l is None or not l.inherit_history or l.successor_cik != current_cik:
        return [(current_cik, None)]
    if asof < l.known_at:
        return [(l.predecessor_cik, None)]
    return [(current_cik, None), (l.predecessor_cik, l.effective_date)]


def merge_facts(symbol: str, current_cik: int, links: Mapping[str, Link], asof: date, facts_by_cik: Mapping[int, Sequence[Any]],
                accepted_by_cik: Mapping[int, Mapping[str, date]]) -> Tuple[List[Any], Dict[str, date]]:
    """The facts and acceptance dates the PIT engine reads for `symbol` (sec_statements.RawFact: .end is the period end). Each fact stays as filed."""
    facts: List[Any] = []
    accepted: Dict[str, date] = {}
    for cik, before in sources(symbol, current_cik, links, asof):
        facts.extend(f for f in facts_by_cik.get(cik, ()) if before is None or f.end < before)
        accepted.update(accepted_by_cik.get(cik, {}))
    return facts, accepted


def price_identity_from(symbol: str, links: Mapping[str, Link]) -> Optional[date]:
    l = links.get(symbol.upper())
    return l.price_identity_from if l else None


def research_eligible(symbol: str, links: Mapping[str, Link], sic: Optional[int] = None) -> bool:
    """False for a listed debt/preferred security or a blank-check company (SIC 6770), whatever the lineage table says."""
    if sic == 6770:
        return False
    l = links.get(symbol.upper())
    return l.research_eligible if l else True


# ------------------------------------------------------------------------------------------------------------------------------------------------ detection
def detect(rows: Iterable[Mapping[str, Any]], links: Mapping[str, Link]) -> List[Dict[str, Any]]:
    """Candidates for review: symbols whose prices start more than a year before their CIK's first periodic filing and have no lineage row yet.
    A candidate is never linked automatically; it is a row for a person to classify with evidence (rows: symbol, cik, first_price, first_filing)."""
    out = []
    for r in rows:
        fp, ff = _d(r.get("first_price")), _d(r.get("first_filing"))
        if fp and ff and (ff - fp).days > DETECT_MIN_PRICE_LEAD_DAYS and str(r["symbol"]).upper() not in links:
            out.append({"symbol": str(r["symbol"]).upper(), "cik": int(r["cik"]), "first_price": fp, "first_filing": ff, "lead_days": (ff - fp).days})
    return sorted(out, key=lambda x: -x["lead_days"])


DETECT_SQL = """
    SELECT m.symbol, m.cik, p.first_price, f.first_filing
    FROM sec_symbol_cik m
    JOIN (SELECT symbol, MIN(date) AS first_price FROM stock_prices GROUP BY symbol) p ON p.symbol = m.symbol
    JOIN (SELECT cik, MIN(filed_date) AS first_filing FROM sec_filing GROUP BY cik) f ON f.cik = m.cik"""
