"""SEC fundamentals updater: parsing helpers (pure) and the incremental, idempotent ingestion against a real PostgreSQL (skipped when none is reachable).

The network is replaced by a fake transport returning canned SEC documents, so these tests never contact the SEC and need no User-Agent. Rows are written
under CIKs 9990001+ and symbols ZZ*, and removed before and after each test.
"""
import json
import logging
import os
import sys
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))
sys.path.insert(0, ROOT)

from data_updaters import sec_fundamentals_updater as U  # noqa: E402
from data_updaters import sec_statements as S  # noqa: E402
from data_updaters.sec_client import SecNotFound, UserAgentError  # noqa: E402

CIK_A, CIK_B = 9990001, 9990002
SYM_A, SYM_B = "ZZSECA", "ZZSECB"


# ------------------------------------------------------------------ canned SEC documents
def entry(end, val, accn, form, filed, start=None, fy=2025, fp="Q2"):
    out = {"end": end, "val": val, "accn": accn, "fy": fy, "fp": fp, "form": form, "filed": filed}
    if start:
        out["start"] = start
    return out


def facts_doc(cik, revenue_q2=110, extra=()):
    ents = [entry("2025-03-31", 100, "0000000001-25-000001", "10-Q", "2025-05-02", "2025-01-01", fp="Q1"),
            entry("2025-06-30", revenue_q2, "0000000001-25-000002", "10-Q", "2025-08-01", "2025-04-01", fp="Q2"), *extra]
    return {"cik": cik, "facts": {"us-gaap": {"Revenues": {"units": {"USD": ents}}}}}


def subs_doc(cik, accns):
    rows = sorted(accns, key=lambda a: a[2])
    return {"cik": f"{cik:010d}", "filings": {"recent": {
        "accessionNumber": [a[0] for a in rows], "form": [a[1] for a in rows], "filingDate": [a[2] for a in rows],
        "reportDate": [a[3] for a in rows], "acceptanceDateTime": [f"{a[2]}T20:31:11.000Z" for a in rows]}}}


TICKERS = {"0": {"cik_str": CIK_A, "ticker": SYM_A, "title": "Alpha Corp"}, "1": {"cik_str": CIK_B, "ticker": SYM_B, "title": "Beta Corp"}}
ACC1 = ("0000000001-25-000001", "10-Q", "2025-05-02", "2025-03-31")
ACC2 = ("0000000001-25-000002", "10-Q", "2025-08-01", "2025-06-30")


class FakeTransport:
    def __init__(self, docs):
        self.docs, self.urls = docs, []

    def fetch(self, url, *, capture=True):
        self.urls.append(url)
        if url not in self.docs:
            raise SecNotFound(url, "not found", 404)
        return SimpleNamespace(body=json.dumps(self.docs[url]).encode())


def urls(cik):
    return f"{U.DATA_URL}/submissions/CIK{cik:010d}.json", f"{U.DATA_URL}/api/xbrl/companyfacts/CIK{cik:010d}.json"


def make_docs(revenue_q2=110, accns=(ACC1, ACC2), extra=()):
    s, f = urls(CIK_A)
    return {U.TICKER_FILE_URL: TICKERS, s: subs_doc(CIK_A, accns), f: facts_doc(CIK_A, revenue_q2, extra)}


# ------------------------------------------------------------------ pure helpers
def test_ticker_file_normalizes_symbols_and_skips_malformed_entries():
    doc = {"0": {"cik_str": 1, "ticker": "brk.b", "title": "B"}, "1": {"cik_str": "x", "ticker": "BAD"}, "2": {"ticker": "NOCIK"}, "3": {"cik_str": 2, "ticker": "BRK-B"}}
    assert U.parse_ticker_file(doc) == {"BRK-B": (1, "B")}        # first entry wins; '.' becomes '-'


def test_recent_periodic_filings_keep_only_periodic_forms_with_the_acceptance_instant():
    doc = subs_doc(5, [("a1", "10-Q", "2025-05-02", "2025-03-31"), ("a2", "8-K", "2025-05-01", ""), ("a3", "10-K/A", "2025-06-01", "2024-12-31")])
    got = U.parse_recent_periodic(doc, 5)
    assert [(g["accession"], g["is_amendment"]) for g in got] == [("a1", False), ("a3", True)]
    assert got[0]["accepted_at"] == datetime(2025, 5, 2, 20, 31, 11, tzinfo=timezone.utc)


def test_a_submissions_document_for_another_company_is_refused():
    with pytest.raises(S.SecStatementError):
        U.parse_recent_periodic(subs_doc(6, []), 5)


def test_comparison_tolerances():
    assert U.compare_value("revenue", 1_000_000, 1_004_000) and not U.compare_value("revenue", 1_000_000, 1_010_000)
    assert U.compare_value("eps_diluted", 0.55, 0.56) and not U.compare_value("eps_diluted", 0.55, 0.60)


def test_missing_contact_skips_the_run_without_touching_anything(monkeypatch):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    assert U.SecFundamentalsUpdater(logger=logging.getLogger("t")).run() == {"skipped": True}


def test_a_placeholder_contact_is_refused(monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "Your Name you@example.com")
    with pytest.raises(UserAgentError):
        U.SecFundamentalsUpdater.config_from_env()


# ------------------------------------------------------------------ database
def _db():
    try:
        from shared import db as _d
        _d.execute_dict_query("SELECT 1 FROM sec_fact LIMIT 1")
        _d.execute_dict_query("SELECT 1 FROM stock_prices LIMIT 1")
        return _d
    except Exception as e:  # pragma: no cover
        pytest.skip(f"database with the SEC tables not reachable: {e}")


def _clean(d):
    for t in ("sec_quarter", "sec_fact", "sec_filing", "sec_fundamentals_state"):
        d.execute_insert(f"DELETE FROM {t} WHERE cik BETWEEN 9990000 AND 9990999")
    d.execute_insert("DELETE FROM sec_symbol_cik WHERE symbol LIKE 'ZZSEC%%'")
    d.execute_insert("DELETE FROM earnings_calendar WHERE symbol LIKE 'ZZSEC%%'")
    d.execute_insert("DELETE FROM quarterly_fundamentals WHERE symbol LIKE 'ZZSEC%%'")
    d.execute_insert("DELETE FROM stock_prices WHERE symbol LIKE 'ZZSEC%%'")


@pytest.fixture
def dbx():
    d = _db()
    _clean(d)
    for s in (SYM_A, SYM_B):
        d.execute_insert("INSERT INTO stock_prices (symbol, date, close) VALUES (%s, CURRENT_DATE, 1)", (s,))
    yield d
    _clean(d)


def updater(docs):
    return U.SecFundamentalsUpdater(transport=FakeTransport(docs), logger=logging.getLogger("t"))


def q(d, sql, p=()):
    return d.execute_dict_query(sql, p)


def test_first_run_ingests_filings_facts_quarters_and_state(dbx):
    stats = updater(make_docs()).run(symbols=[SYM_A])
    assert stats["checked"] == 1 and stats["new_filings"] == 1 and stats["errors"] == 0
    assert q(dbx, "SELECT COUNT(*) n FROM sec_filing WHERE cik=%s", (CIK_A,))[0]["n"] == 2
    f = q(dbx, "SELECT accepted_at FROM sec_filing WHERE accession=%s", (ACC2[0],))[0]["accepted_at"]
    assert f == datetime(2025, 8, 1, 20, 31, 11, tzinfo=timezone.utc)
    rows = q(dbx, "SELECT period_end, value::float8 v, basis, accession, fiscal_year, fiscal_quarter FROM sec_quarter WHERE cik=%s AND metric='revenue' ORDER BY period_end", (CIK_A,))
    assert [(r["v"], r["basis"], r["fiscal_quarter"]) for r in rows] == [(100.0, "reported", 1), (110.0, "reported", 2)]
    st = q(dbx, "SELECT last_status, newest_periodic_accession, facts_fetched_at FROM sec_fundamentals_state WHERE cik=%s", (CIK_A,))[0]
    assert st["last_status"] == "ok" and st["newest_periodic_accession"] == ACC2[0] and st["facts_fetched_at"] is not None


def test_a_second_run_with_nothing_new_fetches_no_facts_and_changes_nothing(dbx):
    updater(make_docs()).run(symbols=[SYM_A])
    before = q(dbx, "SELECT COUNT(*) n, MAX(first_seen_at) m FROM sec_fact WHERE cik=%s", (CIK_A,))[0]
    t = FakeTransport(make_docs())
    again = U.SecFundamentalsUpdater(transport=t, logger=logging.getLogger("t")).run(symbols=[SYM_A])
    assert again["new_filings"] == 0 and again["facts"] == 0
    assert not [u for u in t.urls if "companyfacts" in u]                       # the large document is not fetched again
    after = q(dbx, "SELECT COUNT(*) n, MAX(first_seen_at) m FROM sec_fact WHERE cik=%s", (CIK_A,))[0]
    assert before == after
    assert q(dbx, "SELECT SUM(revision_count) s FROM sec_quarter WHERE cik=%s", (CIK_A,))[0]["s"] == 0


def test_forcing_the_same_filing_again_is_idempotent(dbx):
    updater(make_docs()).run(symbols=[SYM_A])
    dbx.execute_insert("DELETE FROM sec_fundamentals_state WHERE cik=%s", (CIK_A,))            # as if the state were lost
    updater(make_docs()).run(symbols=[SYM_A])
    assert q(dbx, "SELECT COUNT(*) n FROM sec_fact WHERE cik=%s", (CIK_A,))[0]["n"] == 2
    assert q(dbx, "SELECT COUNT(*) n, SUM(revision_count) s FROM sec_quarter WHERE cik=%s AND metric='revenue'", (CIK_A,))[0] == {"n": 2, "s": 0}


def test_a_restatement_updates_the_quarter_counts_a_revision_and_keeps_the_original_fact(dbx):
    updater(make_docs()).run(symbols=[SYM_A])
    amend = ("0000000001-25-000009", "10-Q/A", "2025-09-15", "2025-06-30")
    restated = [entry("2025-06-30", 115, amend[0], "10-Q/A", "2025-09-15", "2025-04-01", fp="Q2")]
    updater(make_docs(revenue_q2=110, accns=(ACC1, ACC2, amend), extra=restated)).run(symbols=[SYM_A])
    row = q(dbx, "SELECT value::float8 v, accession, revision_count FROM sec_quarter WHERE cik=%s AND metric='revenue' AND period_end='2025-06-30'", (CIK_A,))[0]
    assert (row["v"], row["accession"], row["revision_count"]) == (115.0, amend[0], 1)
    vals = sorted(r["v"] for r in q(dbx, "SELECT value::float8 v FROM sec_fact WHERE cik=%s AND metric='revenue' AND period_end='2025-06-30'", (CIK_A,)))
    assert vals == [110.0, 115.0]                                                         # the earlier value is still on record


def test_dry_run_fetches_and_normalizes_but_writes_nothing(dbx):
    stats = updater(make_docs()).run(symbols=[SYM_A], dry_run=True)
    assert stats["new_filings"] == 1 and stats["quarters"] > 0
    assert q(dbx, "SELECT COUNT(*) n FROM sec_fact WHERE cik=%s", (CIK_A,))[0]["n"] == 0
    assert q(dbx, "SELECT COUNT(*) n FROM sec_fundamentals_state WHERE cik=%s", (CIK_A,))[0]["n"] == 0


def test_a_company_the_sec_does_not_have_is_recorded_not_retried_every_run(dbx):
    docs = make_docs()
    del docs[urls(CIK_A)[0]]                                                              # submissions: 404
    stats = updater(docs).run(symbols=[SYM_A])
    assert stats["not_found"] == 1
    assert q(dbx, "SELECT last_status s FROM sec_fundamentals_state WHERE cik=%s", (CIK_A,))[0]["s"] == "not_found"


def test_who_is_due_never_checked_report_pending_and_the_weekly_sweep(dbx):
    u = updater(make_docs())
    u.refresh_ticker_map(force=True)
    assert {s for s, _ in u.select_due()} >= {SYM_A, SYM_B}                                # never checked
    for cik, sym in ((CIK_A, SYM_A), (CIK_B, SYM_B)):
        dbx.execute_insert("INSERT INTO sec_fundamentals_state (cik, symbol, last_checked_at, last_status, newest_periodic_accession, newest_periodic_filed) "
                           "VALUES (%s, %s, NOW() - interval '1 hour', 'ok', 'x', %s)", (cik, sym, date.today() - timedelta(days=90)))
    assert not {s for s, _ in u.select_due()} & {SYM_A, SYM_B}                              # just checked: nobody is due
    dbx.execute_insert("INSERT INTO earnings_calendar (symbol, report_date, eps_actual) VALUES (%s, CURRENT_DATE - 2, 1.0)", (SYM_A,))
    assert not {s for s, _ in u.select_due()} & {SYM_A}                                     # report newer than the filing, but checked only 1 hour ago
    dbx.execute_insert("UPDATE sec_fundamentals_state SET last_checked_at = NOW() - interval '13 hours' WHERE cik=%s", (CIK_A,))
    due = u.select_due()
    assert due and due[0][0] == SYM_A and SYM_B not in {s for s, _ in due}                  # the pending report goes first and alone
    dbx.execute_insert("UPDATE sec_fundamentals_state SET last_checked_at = NOW() - interval '8 days' WHERE cik=%s", (CIK_B,))
    assert SYM_B in {s for s, _ in u.select_due()}                                          # weekly sweep


def test_reconcile_against_quarterly_fundamentals(dbx):
    updater(make_docs()).run(symbols=[SYM_A])
    for qd, rev in ((date(2025, 3, 31), 100), (date(2025, 6, 30), 140)):                  # Q1 agrees, Q2 does not
        dbx.execute_insert("INSERT INTO quarterly_fundamentals (symbol, quarter, revenue) VALUES (%s, %s, %s)", (SYM_A, qd, rev))
    r = updater(make_docs()).reconcile([SYM_A])
    assert (r["compared"], r["match"], r["mismatch"]) == (2, 1, 1)
    assert r["examples"][0]["period_end"] == "2025-06-30" and r["examples"][0]["sec"] == 110.0 and r["examples"][0]["yfinance"] == 140.0
    dbx.execute_insert("DELETE FROM quarterly_fundamentals WHERE symbol=%s AND quarter='2025-06-30'", (SYM_A,))
    r2 = updater(make_docs()).reconcile([SYM_A])
    assert r2["newer_than_yfinance_count"] == 1 and r2["newer_than_yfinance"] == [f"{SYM_A}:2025-06-30"]


def test_user_agent_env_value_with_surrounding_quotes_is_cleaned(monkeypatch):
    from data_updaters.sec_fundamentals_updater import SecFundamentalsUpdater as U
    monkeypatch.setenv("SEC_USER_AGENT", '"WolfX Digital wolfxdigitalsol@gmail.com"')
    assert U.config_from_env() == "WolfX Digital wolfxdigitalsol@gmail.com"
