"""Opportunity Intelligence v0 on a REAL schema (every init migration, throwaway database): the repository's SQL, the accession join, the
read-only session, and an end-to-end brief. Skips when no superuser Postgres is reachable (CI's db job provides one)."""
import os
import sys
from datetime import date, datetime, timedelta, timezone

import psycopg2
import pytest
from psycopg2.extras import Json

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..", "forward_collection", "tests")))

import forward_world  # noqa: E402,F401  (puts mechanism/ and the repo root on sys.path)
import full_env as FE  # noqa: E402

from opportunity import brief as B  # noqa: E402
from opportunity import evidence as E  # noqa: E402
from opportunity import repository as R  # noqa: E402

SESSION = date(2026, 11, 10)
UTC = timezone.utc
METRICS = {"revenue": 972e6, "revenue_py": 749e6, "rev_yoy": 0.2977, "operating_income": 266e6, "op_margin": 0.2737, "op_margin_py": 0.1362,
           "op_margin_yoy_bps": 1375.0, "net_income": 78e6, "net_income_py": -10e6, "free_cash_flow": 99e6, "free_cash_flow_py": -11e6,
           "fcf_margin": 0.1019, "fcf_margin_py": -0.0147, "fcf_margin_yoy_pp": 11.65}


def weekdays(start, end):
    d, out = start, []
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


@pytest.fixture(scope="module")
def env():
    e, teardown = FE.make_env("oi")
    try:
        yield e
    finally:
        teardown()


@pytest.fixture(scope="module")
def seeded(env):
    conn = env.admin()
    cur = conn.cursor()
    days = weekdays(SESSION - timedelta(days=170), SESSION + timedelta(days=1))
    for sym in ("AAA", "LATE", "FILL1", "FILL2"):
        cur.execute("INSERT INTO companies (symbol, company_name) VALUES (%s, %s) ON CONFLICT DO NOTHING", (sym, sym + " Corp."))
        for i, d in enumerate(days):
            close = 110.0 if (sym == "AAA" and d >= date(2026, 11, 4)) else 100.0
            cur.execute("INSERT INTO stock_prices (symbol, date, open, high, low, close, volume) VALUES (%s,%s,%s,%s,%s,%s,1000)",
                        (sym, d, close, close, close, close))
    for d in days:
        cur.execute("INSERT INTO market_index_prices (symbol, date, close) VALUES ('^GSPC', %s, 5000)", (d,))
    acc = "0000123456-26-000077"
    cur.execute("INSERT INTO sec_filing (accession, cik, form, filed_date, accepted_at, period_end) VALUES (%s, 123456, '10-Q', %s, %s, %s)",
                (acc, date(2026, 11, 4), datetime(2026, 11, 4, 21, 5, tzinfo=UTC), date(2026, 9, 30)))
    for sym, detected in (("AAA", datetime(2026, 11, 5, 0, 30, tzinfo=UTC)), ("LATE", datetime(2026, 11, 12, 1, 0, tzinfo=UTC))):
        cur.execute("""INSERT INTO fundamental_feature (symbol, quarter_end, method_version, available_at, availability_basis, metrics, score, components,
                       data_quality, source, computed_at) VALUES (%s, '2026-09-30', 'inflection_v2_sec_pit', '2026-11-04', 'sec_acceptance', %s, 50, '[]', 'ok', 'sec', %s)""",
                    (sym, Json(METRICS), detected))
        for t in ("margin_expansion", "revenue_acceleration", "improvement_with_limited_repricing"):
            cur.execute("""INSERT INTO fundamental_inflection_event (symbol, quarter_end, event_type, method_version, strength, severity, score, metrics,
                           explanation, components, available_at, availability_basis, source, data_quality, quality_notes, event_hash, first_detected_at, updated_at)
                           VALUES (%s, '2026-09-30', %s, 'inflection_v2_sec_pit', 90, 'high', 80, '{}', %s, '[]', '2026-11-04', 'sec_acceptance', 'sec', 'ok', %s,
                                   %s, %s, %s)""",
                        (sym, t, Json([f"{t} explained"]), Json(["pit_as_of:2026-11-04", f"prov:q={acc}"]), (sym + t).encode().hex()[:64].ljust(64, "0"),
                         detected, detected))
    cur.execute("INSERT INTO price_discontinuities (symbol, date, kind, prev_close, close, ratio, detected_at) VALUES ('AAA', '2026-10-01', 'jump_up', 25, 100, 4, '2026-10-02')")
    cur.execute("INSERT INTO earnings_calendar (symbol, report_date, eps_estimate, eps_actual, surprise_pct, fetched_at) VALUES ('AAA', '2026-11-04', 1.00, 1.08, 8.0, %s)",
                (datetime(2026, 11, 6, 22, 0, tzinfo=UTC),))
    conn.commit()
    conn.close()
    return env


def test_the_brief_end_to_end_on_the_real_schema(seeded):
    rep = B.run(seeded.admin_connect_factory(), SESSION)
    assert [b["symbol"] for b in rep["briefs"]] == ["AAA"]                                    # LATE: detected after the cutoff
    assert rep["pit_exclusions"] == {"event_detected_after_cutoff": 2}                         # its 2 headline events (the price-built type is not read)
    b = rep["briefs"][0]
    assert b["company_name"] == "AAA Corp." and b["filing"]["accession"] == "0000123456-26-000077" and b["filing"]["form"] == "10-Q"
    assert b["filing"]["accepted_at"] == "2026-11-04T21:05:00+00:00"
    assert [w["event_type"] for w in b["what_changed"]] == ["margin_expansion", "revenue_acceleration"]
    a = b["assessment"]
    assert a["dimensions"] == {"business": E.SUPPORTS, "expectations": E.UNVERIFIED, "market": E.POSITIVE} and a["converging"]
    assert b["evidence"]["market"]["note"] == "window 2026-11-03 close to 2026-11-06 close"     # the October discontinuity is outside the window
    assert B.wording_violations(rep) == []


def test_the_repository_reads_discontinuities_and_has_no_pre_release_store(seeded):
    conn = seeded.admin()
    try:
        data = R.load(conn, SESSION, SESSION - timedelta(days=14), E.HEADLINE_TYPES, SESSION - timedelta(days=134))
    finally:
        conn.close()
    assert [(d["symbol"], d["date"]) for d in data["discontinuities"]] == [("AAA", date(2026, 10, 1))]
    assert data["pre_release"] == {}                                                           # no verified consensus snapshots exist yet


def test_reads_run_in_a_read_only_session_and_the_connection_is_restored(seeded):
    conn = seeded.admin()
    try:
        with R.read_only(conn):
            with pytest.raises(psycopg2.errors.ReadOnlySqlTransaction):
                conn.cursor().execute("INSERT INTO companies (symbol) VALUES ('ZZZ')")
        assert conn.readonly is None or conn.readonly is False
    finally:
        conn.close()
