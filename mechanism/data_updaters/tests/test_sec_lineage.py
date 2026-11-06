"""SEC company identity (sec_lineage.py, migration 42; lab only): regression tests for XOM and the other split-history cases of 2026-11-05.

No network. The database test applies migration 42 to the test database and is skipped when PostgreSQL is unreachable (CI's real-Postgres job runs it)."""
import os
import sys
from dataclasses import replace
from datetime import date

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))

from data_updaters import sec_lineage as L  # noqa: E402
from data_updaters.sec_statements import RawFact  # noqa: E402
from inflection import pit as P  # noqa: E402

LINKS = L.by_symbol(L.load_seed())
XOM_OLD, XOM_NEW = 34088, 2115436


def fact(cik_tag, end, filed, value, accession, start=None, form="10-Q"):
    s = start or date(end.year, end.month - 2 if end.month > 2 else end.month, 1)
    return RawFact("revenue", f"us-gaap:Revenues/{cik_tag}", "USD", value, s, end, end.year, "Q2", form, accession, filed)


# ------------------------------------------------------------------------------------------------------------------------------------------------ the seed
def test_the_seed_covers_all_fourteen_split_history_symbols_and_validates():
    assert set(LINKS) == {"PBAM", "SWRD", "XOM", "EAI", "VNOM", "AVBH", "COSO", "GLIBA", "MTAL", "SHAZ", "PNFP", "SN", "LLYVK", "DEC"}


def test_only_the_two_proven_holding_company_reorganisations_inherit_history():
    inherit = {s: (l.predecessor_cik, l.evidence_form) for s, l in LINKS.items() if l.inherit_history}
    assert inherit == {"XOM": (34088, "8-K12B"), "VNOM": (1602065, "8-K12B")}


@pytest.mark.parametrize("change", [
    dict(status="needs_review"),                                   # unconfirmed
    dict(predecessor_cik=None),                                    # no predecessor
    dict(predecessor_cik=XOM_NEW),                                 # itself
    dict(relation="merger_successor"),                             # a combination of businesses
    dict(relation="ticker_reuse"),                                 # a shared ticker is not a succession
    dict(evidence_accession=None),                                 # no proof
    dict(evidence_form="25-NSE"),                                  # not a succession filing
])
def test_inheriting_history_requires_a_confirmed_succession_with_its_filing(change):
    with pytest.raises(L.LineageError):
        replace(LINKS["XOM"], **change).validate()


def test_pnfp_merger_of_equals_is_recorded_but_never_inherited():
    pnfp = LINKS["PNFP"]
    assert pnfp.relation == "merger_successor" and pnfp.status == "needs_review" and not pnfp.inherit_history
    assert L.sources("PNFP", 2082866, LINKS, date(2026, 6, 30)) == [(2082866, None)]
    with pytest.raises(L.LineageError):
        replace(pnfp, inherit_history=True).validate()


# ------------------------------------------------------------------------------------------------------------------------------------------------ XOM, point in time
def _xom_facts():
    old = [fact("old", date(2025, 6, 30), date(2025, 8, 4), 81.5e9, "0000034088-25-000050"),
           fact("old", date(2026, 3, 31), date(2026, 5, 1), 80.0e9, "0000034088-26-000020"),
           fact("old", date(2026, 9, 30), date(2026, 11, 3), 1.0, "0000034088-26-000099")]   # a post-succession stub under the old CIK
    new = [fact("new", date(2026, 6, 30), date(2026, 8, 3), 83.0e9, "0002115436-26-000010"),
           fact("new", date(2025, 6, 30), date(2026, 8, 3), 81.5e9, "0002115436-26-000010")]  # the comparative in the successor's first 10-Q
    return {XOM_OLD: old, XOM_NEW: new}


def test_xom_without_lineage_the_prior_year_quarter_first_appears_a_year_late():
    fp = P.first_public(_xom_facts()[XOM_NEW], {})
    assert fp[date(2025, 6, 30)][0] == date(2026, 8, 3)                  # 399 days after quarter end: the stale guard's case


def test_xom_with_lineage_every_quarter_is_dated_by_the_filing_that_first_made_it_public():
    facts, acc = L.merge_facts("XOM", XOM_NEW, LINKS, date(2026, 9, 1), _xom_facts(), {})
    fp = P.first_public(facts, acc)
    assert fp[date(2025, 6, 30)] == (date(2025, 8, 4), "sec_filed_date", "0000034088-25-000050")
    assert fp[date(2026, 3, 31)][2] == "0000034088-26-000020"
    assert fp[date(2026, 6, 30)][2] == "0002115436-26-000010"
    assert date(2026, 9, 30) not in fp                                  # the predecessor's post-succession stub is not read
    assert {f.accession[:10] for f in facts} == {"0000034088", "0002115436"}   # each fact keeps its own filing: identities stay separate


def test_xom_before_the_succession_was_public_the_symbol_reads_the_predecessor_only():
    assert L.sources("XOM", XOM_NEW, LINKS, date(2026, 6, 30)) == [(XOM_OLD, None)]
    assert L.sources("XOM", XOM_NEW, LINKS, date(2026, 7, 1)) == [(XOM_NEW, None), (XOM_OLD, date(2026, 7, 1))]


def test_xom_pit_items_restore_the_history_the_stale_guard_dropped():
    facts = _xom_facts()
    q = lambda items: {i["feature"]["quarter"]: i["available_at"] for i in items if i.get("feature")}  # noqa: E731
    alone = q(P.pit_items(facts[XOM_NEW], {}))
    merged = q(P.pit_items(*L.merge_facts("XOM", XOM_NEW, LINKS, date(2026, 9, 1), facts, {})))
    assert date(2025, 6, 30) not in alone                               # skipped as stale (first public 399 days late)
    assert merged[date(2025, 6, 30)] == date(2025, 8, 4)               # dated by ExxonMobil's own 10-Q
    assert merged[date(2026, 6, 30)] == date(2026, 8, 3)


def test_a_link_for_another_current_cik_is_not_applied():
    """If the ticker file later maps XOM to a third CIK, the reviewed link no longer describes it."""
    assert L.sources("XOM", 999999, LINKS, date(2026, 9, 1)) == [(999999, None)]


def test_vnom_inherits_from_the_renamed_predecessor():
    assert L.sources("VNOM", 2074176, LINKS, date(2025, 9, 1)) == [(2074176, None), (1602065, date(2025, 8, 19))]


# ------------------------------------------------------------------------------------------------------------------------------------------------ the other cases
@pytest.mark.parametrize("symbol,cik", [("SN", 1957132), ("DEC", 1922446), ("GLIBA", 2057463), ("SHAZ", 2068385), ("LLYVK", 2078416),
                                        ("AVBH", 1443575), ("COSO", 1297107), ("PBAM", 1705284), ("SWRD", 1795851)])
def test_no_other_case_borrows_another_registrants_filings(symbol, cik):
    assert L.sources(symbol, cik, LINKS, date(2026, 9, 1)) == [(cik, None)]


def test_ticker_reuse_and_shells_cut_the_prices_of_the_earlier_entity():
    assert L.price_identity_from("GLIBA", LINKS) == date(2025, 7, 15)   # 2018-2020 prices were the old GCI Liberty (CIK 808461)
    assert L.price_identity_from("MTAL", LINKS) == date(2026, 2, 6)     # MAC Copper until 2025-10-10
    assert L.price_identity_from("SHAZ", LINKS) == date(2026, 2, 18)    # the blank-check shell before the combination
    assert L.price_identity_from("LLYVK", LINKS) is None                # a tracking stock: the same economic interest throughout
    assert L.price_identity_from("AAPL", LINKS) is None


def test_debt_listings_and_blank_check_companies_are_excluded_from_common_stock_research():
    assert not L.research_eligible("EAI", LINKS)
    assert not L.research_eligible("MTAL", LINKS)
    assert not L.research_eligible("ANYSPAC", LINKS, sic=6770)
    assert L.research_eligible("XOM", LINKS) and L.research_eligible("AAPL", LINKS)
    with pytest.raises(L.LineageError):
        replace(LINKS["EAI"], research_eligible=True).validate()


def test_detection_proposes_candidates_and_never_links_them():
    rows = [{"symbol": "NEWCO", "cik": 1, "first_price": "2018-01-02", "first_filing": "2026-01-05"},
            {"symbol": "XOM", "cik": XOM_NEW, "first_price": "2016-09-20", "first_filing": "2026-08-03"},   # already reviewed
            {"symbol": "IPO", "cik": 2, "first_price": "2025-06-02", "first_filing": "2025-08-10"}]           # a new listing
    assert [c["symbol"] for c in L.detect(rows, LINKS)] == ["NEWCO"]
    assert "NEWCO" not in LINKS and L.sources("NEWCO", 1, LINKS, date(2026, 9, 1)) == [(1, None)]


def test_two_current_rows_for_one_symbol_are_refused():
    with pytest.raises(L.LineageError):
        L.by_symbol([LINKS["XOM"], LINKS["XOM"]])


# ------------------------------------------------------------------------------------------------------------------------------------------------ migration 42 on PostgreSQL
def _conn():
    psycopg2 = pytest.importorskip("psycopg2")
    from shared.config import config
    c = config.database if hasattr(config, "database") else config
    try:
        conn = psycopg2.connect(host=c.db_host, port=c.db_port, dbname=c.db_name, user=c.db_user, password=c.db_password, connect_timeout=3)
    except Exception:
        pytest.skip("no PostgreSQL reachable")
    conn.autocommit = True
    return conn


def test_migration_42_enforces_the_rules_and_is_append_only():
    import psycopg2
    conn = _conn()
    cur = conn.cursor()
    with open(os.path.join(ROOT, "mechanism", "add_sec_cik_lineage.sql"), encoding="utf-8") as f:
        sql = f.read()
    cur.execute(sql)
    cur.execute(sql)                                                    # re-runnable
    cols = "symbol, successor_cik, predecessor_cik, relation, effective_date, known_at, evidence_accession, evidence_form, inherit_history, research_eligible, price_identity_from, status, note"
    x = LINKS["XOM"]
    row = ("CITEST_XOM", x.successor_cik, x.predecessor_cik, x.relation, x.effective_date, x.known_at, x.evidence_accession, x.evidence_form, True, True, None, "confirmed", "ci")
    cur.execute(f"INSERT INTO sec_cik_lineage ({cols}) VALUES ({', '.join(['%s'] * 13)}) RETURNING id", row)
    first = cur.fetchone()[0]
    for bad in (dict(relation="ticker_reuse"), dict(status="needs_review"), dict(evidence="NULL")):
        r = list(row)
        if "relation" in bad:
            r[3] = bad["relation"]
        if "status" in bad:
            r[11] = bad["status"]
        if "evidence" in bad:
            r[6] = None
        with pytest.raises(psycopg2.errors.CheckViolation):
            cur.execute(f"INSERT INTO sec_cik_lineage ({cols}) VALUES ({', '.join(['%s'] * 13)})", r)
    with pytest.raises(psycopg2.errors.CheckViolation):                # non-equity listings are never research eligible
        cur.execute(f"INSERT INTO sec_cik_lineage ({cols}) VALUES ({', '.join(['%s'] * 13)})",
                    ("CITEST_EAI", 7323, None, "non_equity_listing", date(2018, 12, 3), date(2018, 12, 3), None, None, False, True, None, "confirmed", "ci"))
    with pytest.raises(psycopg2.Error, match="append-only"):
        cur.execute("UPDATE sec_cik_lineage SET note = 'x' WHERE id = %s", (first,))
    with pytest.raises(psycopg2.Error, match="append-only"):
        cur.execute("DELETE FROM sec_cik_lineage WHERE id = %s", (first,))
    cur.execute(f"INSERT INTO sec_cik_lineage ({cols}, supersedes_id) VALUES ({', '.join(['%s'] * 14)}) RETURNING id", (*row[:12], "ci correction", first))
    second = cur.fetchone()[0]

    class Db:
        def execute_dict_query(self, q, p=None):
            from psycopg2.extras import RealDictCursor
            c2 = conn.cursor(cursor_factory=RealDictCursor)
            c2.execute(q.replace("ORDER BY symbol, id", "AND symbol = 'CITEST_XOM' ORDER BY symbol, id"), p)
            return [dict(r) for r in c2.fetchall()]
    current = L.load_db(Db())
    assert [l.id for l in current] == [second] and current[0].supersedes_id == first
    cur.execute("SELECT tgenabled FROM pg_trigger WHERE tgrelid = 'sec_cik_lineage'::regclass AND NOT tgisinternal")
    assert {r[0] for r in cur.fetchall()} == {"A"}
    conn.close()
