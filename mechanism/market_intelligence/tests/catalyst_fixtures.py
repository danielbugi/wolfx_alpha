"""Shared builders for the Phase 1A database tests: synthetic submissions documents -> collected facts, a syntactically real SEC ticker file, and the
database clock (availability timestamps are stamped by the database, so a test places a cutoff BETWEEN two operations by reading the database clock)."""
import json
from datetime import date

import mi_samples  # noqa: F401  (path setup)
from edgar_samples import CIK_A, CIK_B, NOW, UA, doc, r  # noqa: F401
from market_intelligence import catalyst_rules as R
from market_intelligence import edgar_collector as E
from market_intelligence import edgar_store as ES

ACC = "0000320193-26-{:06d}"
SNAP_DATE = date(2026, 9, 1)            # earlier than every sample filing date, so an OBSERVED link from this file covers them


def collected(rows, cik=CIK_A, now=NOW):
    facts, rejected, counters = E.parse_submissions(doc(cik, rows), cik, now=now, forms=R.PHASE1A_FORMS)
    assert not rejected, rejected
    return facts


def filing(n, form="8-K", items="2.02,9.01", filed="2026-09-18", accepted="2026-09-18T20:05:11.000Z", cik=CIK_A):
    return collected([r(ACC.format(n), form=form, items=items, filed=filed, accepted=accepted)], cik=cik)[0]


def ticker_file(pairs, filler=1200):
    d = {str(i): {"cik_str": 1_000_000 + i, "ticker": f"F{i}", "title": "filler"} for i in range(filler)}
    for j, (c, t) in enumerate(pairs):
        d[f"p{j}"] = {"cik_str": c, "ticker": t, "title": "x"}
    return json.dumps(d).encode()


def db_now(conn):
    cur = conn.cursor()
    cur.execute("SELECT clock_timestamp()")
    t = cur.fetchone()[0]
    conn.rollback()
    return t


def ingest(conn, facts, run="run-1", apply=True, **kw):
    cur = conn.cursor()
    res = ES.ingest_facts(cur, facts, run, "test-ref", apply=apply, **kw)
    conn.commit()
    return res


def apply_links(conn, pairs, snap_date=SNAP_DATE, run="links-1", reconstruct=False, apply=True, body=None):
    body = body if body is not None else ticker_file(pairs)
    cur = conn.cursor()
    sid, _ = ES.store_snapshot(cur, "sec_edgar", "company_tickers", "-", "https://www.sec.gov/files/company_tickers.json", None, body, run, "test-ref")
    out = ES.refresh_links(cur, body, snap_date, sid, "test-ref", reconstruct=reconstruct, apply=apply)
    conn.commit()
    return out


def count(conn, table):
    cur = conn.cursor()
    cur.execute(f"SELECT count(*) FROM {table}")
    n = cur.fetchone()[0]
    conn.rollback()
    return n
