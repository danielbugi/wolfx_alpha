"""Reconcile a filing's SEC acceptance time against the SEC's own header record. PURE: stdlib + filing_contract (no database, clock, network, environment).

Why this exists (found by the real-SEC dry run, 2026-10-16): in a sample of 123 filings, `submissions` JSON `acceptanceDateTime` was exactly +4.00 h later than the instant
given by the filing's own SGML header (`<ACCEPTANCE-DATETIME>`, Eastern wall clock) and by its index page, for 81 of them (66%), and EQUAL for the other 42; never earlier. The
header, the index page and the live Atom list agree with each other. Consequences of using the feed value alone: a filing could look "from the future" for up to 4 hours (and
be refused by the database rule known_at <= ingested_at), and BMO / intraday / AMC timing would be wrong for the shifted ones.

Policy (strict, no repair beyond one documented, evidenced correction):
  * header == feed                      -> use it; source "agree".
  * header earlier than feed by 0 < d <= MAX_FEED_LATE (6 h) -> the feed is late: use the HEADER time (the earlier, SEC-recorded instant); source "feed_late".
  * anything else (header later than the feed, or the feed later by more than 6 h, or an unparsable / ambiguous header) -> REFUSED with a reason; nothing is stored.
Both values and the decision travel in the event payload (`acceptance`) so the correction is auditable. The fact's other fields are untouched.
"""
from __future__ import annotations

import dataclasses
import re
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from market_intelligence import filing_contract as F
from market_intelligence.edgar_collector import CollectedFact
from market_intelligence.events import EventDraft

NY = ZoneInfo("America/New_York")
MAX_FEED_LATE = timedelta(hours=6)
_HEADER_RE = re.compile(r"<ACCEPTANCE-DATETIME>(\d{14})\b")


class AcceptanceError(ValueError):
    """The two SEC records cannot be reconciled; the filing is refused rather than guessed."""


def collector_now(now: datetime) -> datetime:
    """The clock to hand `edgar_collector.collect` when reconciliation follows. The collector refuses an acceptance time that lies in the future of the clock it is given; a feed value that is
    late by up to MAX_FEED_LATE (the documented shift) would be refused there and never reach the header check, so such a filing would not be seen until the feed clock caught up. Moving the
    collector's clock forward by exactly the documented tolerance lets it through to `reconcile`, which then enforces the real rule: the HEADER instant (not the feed value) is what the
    database sees, and an unexplained disagreement or a far-future feed value is refused there."""
    if now.tzinfo is None:
        raise AcceptanceError("now must be timezone-aware")
    return now + MAX_FEED_LATE


def header_url(cik: int, accession: str) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/{accession}.hdr.sgml"


def parse_header_acceptance(text: str) -> datetime:
    """The header's acceptance instant in UTC. The header carries Eastern wall-clock digits without a zone; they are interpreted in America/New_York, and a wall time that does not exist or occurs twice
    (the DST changeovers, when EDGAR is closed) is refused."""
    m = _HEADER_RE.search(text or "")
    if not m:
        raise AcceptanceError("header carries no ACCEPTANCE-DATETIME")
    try:
        wall = datetime.strptime(m.group(1), "%Y%m%d%H%M%S")
    except ValueError as e:
        raise AcceptanceError(f"ACCEPTANCE-DATETIME {m.group(1)!r} is not a date-time") from e
    a, b = wall.replace(tzinfo=NY, fold=0), wall.replace(tzinfo=NY, fold=1)
    if a.utcoffset() != b.utcoffset():
        raise AcceptanceError(f"wall time {wall.isoformat()} is ambiguous or non-existent in America/New_York")
    return a.astimezone(timezone.utc)


def reconcile(c: CollectedFact, header_utc: datetime) -> CollectedFact:
    """The collected fact with its acceptance time reconciled against the header, and the event draft rebuilt from it. Raises AcceptanceError when the policy refuses."""
    if header_utc.tzinfo is None:
        raise AcceptanceError("header time must be timezone-aware")
    feed = c.fact.accepted_at
    late = feed - header_utc
    if late == timedelta(0):
        used, source = feed, "agree"
    elif timedelta(0) < late <= MAX_FEED_LATE:
        used, source = header_utc, "feed_late"
    else:
        raise AcceptanceError(f"feed acceptance {feed.isoformat()} and header acceptance {header_utc.isoformat()} differ by {late.total_seconds() / 3600:+.2f} h (outside the documented 0 < d <= 6 h)")
    fact = c.fact if used == feed else F.FilingFact(accession=c.fact.accession, cik=c.fact.cik, form_type=c.fact.form_type, filing_date=c.fact.filing_date, accepted_at=used,
                                                    items=c.fact.items, report_period=c.fact.report_period, primary_document=c.fact.primary_document,
                                                    amends_accession=c.fact.amends_accession)
    draft: EventDraft = F.to_event_draft(fact, c.symbol, c.symbol_basis)
    audit = {"feed": feed.isoformat(), "header": header_utc.isoformat(), "used": "header" if used != feed else "feed", "source": source,
             "feed_minus_header_hours": round(late.total_seconds() / 3600, 4)}
    draft = dataclasses.replace(draft, payload={**dict(draft.payload), "acceptance": audit})
    return dataclasses.replace(c, fact=fact, draft=draft)
