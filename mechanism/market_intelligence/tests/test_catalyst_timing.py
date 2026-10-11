"""Reaction-bar selection, release matching and filing-date integrity (pure)."""
import os
import sys
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))
sys.path.insert(0, ROOT)

from market_intelligence import catalyst_timing as T  # noqa: E402
from market_intelligence import filing_contract as F  # noqa: E402

NY = ZoneInfo("America/New_York")
# Mon 2026-10-05 .. Fri 2026-10-09, then Mon 2026-10-12
SESSIONS = [date(2026, 10, 5), date(2026, 10, 6), date(2026, 10, 7), date(2026, 10, 8), date(2026, 10, 9), date(2026, 10, 12)]


def at(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=NY).astimezone(timezone.utc)


def test_before_the_open_and_intraday_filings_react_in_that_same_session():
    assert T.reaction_bar(SESSIONS, at(2026, 10, 7, 8, 5)) == (2, "BMO")
    assert T.reaction_bar(SESSIONS, at(2026, 10, 7, 11, 30)) == (2, "INTRADAY")


def test_after_close_filings_react_in_the_next_session():
    assert T.reaction_bar(SESSIONS, at(2026, 10, 7, 16, 5)) == (3, "AMC")
    assert T.reaction_bar(SESSIONS, at(2026, 10, 9, 16, 30)) == (5, "AMC")          # Friday after close -> Monday, never rolled back


def test_a_weekend_filing_reacts_at_the_next_session_and_is_reported_unknown():
    assert T.reaction_bar(SESSIONS, at(2026, 10, 10, 10, 0)) == (5, "UNKNOWN")      # Saturday


def test_a_filing_after_the_last_known_session_has_no_reaction_bar():
    assert T.reaction_bar(SESSIONS, at(2026, 10, 12, 17, 0)) is None


def fact(acc, form, filed, accepted, items=()):
    return F.FilingFact(accession=acc, cik=1, form_type=form, filing_date=filed, accepted_at=accepted, items=tuple(items))


def test_the_release_8k_is_the_item_202_filing_between_period_end_and_the_periodic_report():
    r1 = fact("0000000001-26-000010", "8-K", date(2026, 4, 28), at(2026, 4, 28, 16, 10), ("2.02", "9.01"))
    other = fact("0000000001-26-000011", "8-K", date(2026, 4, 29), at(2026, 4, 29, 8, 0), ("5.02",))
    late = fact("0000000001-26-000020", "8-K", date(2026, 7, 28), at(2026, 7, 28, 16, 10), ("2.02",))
    early = fact("0000000001-26-000005", "8-K", date(2026, 3, 25), at(2026, 3, 25, 16, 10), ("2.02",))
    got = T.match_release(date(2026, 3, 31), date(2026, 5, 6), [r1, other, late, early])
    assert got is r1


def test_no_release_when_none_qualifies():
    assert T.match_release(date(2026, 3, 31), date(2026, 5, 6), []) is None
    far = fact("0000000001-26-000030", "8-K", date(2026, 9, 30), at(2026, 9, 30, 16, 0), ("2.02",))
    assert T.match_release(date(2026, 3, 31), date(2026, 10, 5), [far]) is None


def test_filing_date_integrity():
    assert T.filing_date_consistent(fact("0000000001-26-000001", "8-K", date(2026, 4, 28), at(2026, 4, 28, 16, 10), ("2.02",)))
    assert T.filing_date_consistent(fact("0000000001-26-000002", "8-K", date(2026, 4, 29), at(2026, 4, 28, 17, 45), ("2.02",)))      # after 17:30 -> next business day
    assert not T.filing_date_consistent(fact("0000000001-26-000003", "8-K", date(2026, 4, 30), at(2026, 4, 28, 10, 0), ("2.02",)))


def test_the_module_is_pure():
    import re
    src = open(os.path.join(ROOT, "mechanism", "market_intelligence", "catalyst_timing.py"), encoding="utf-8").read()
    code = "\n".join(l.split("  #")[0] for l in src.splitlines() if not l.strip().startswith("#"))
    assert not re.search(r"psycopg2|requests|urllib|datetime\.now|date\.today|utcnow|time\.time\(|os\.environ|open\(", code)
