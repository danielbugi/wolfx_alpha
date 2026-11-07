"""Fundamental Updates publication record (migration 41) against a real PostgreSQL: idempotent publish, one version per real correction, frozen news,
withdrawal, the race guard, append-only enforcement and the current-state view. Skipped when no database with migration 41 is reachable (the CI
db-bootstrap job has one and fails on any skip). Keys are random per test: rows cannot be deleted (append-only), so tests never collide."""
import contextlib
import json
import os
import random
import string
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))

from inflection import update_model as UM  # noqa: E402
from inflection.update_ledger import Ledger  # noqa: E402

psycopg2 = pytest.importorskip("psycopg2")


def _dsn():
    return dict(host=os.getenv("DB_HOST", "localhost"), port=int(os.getenv("DB_PORT", "5432")), dbname=os.getenv("DB_NAME", "trading_production"),
                user=os.getenv("DB_USER", "postgres"), password=os.getenv("DB_PASSWORD", ""), connect_timeout=5)


@contextlib.contextmanager
def _connect():
    conn = psycopg2.connect(**_dsn())
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture(scope="module")
def ledger():
    try:
        with _connect() as c:
            cur = c.cursor()
            cur.execute("SELECT 1 FROM fundamental_update_publication LIMIT 1")
    except Exception as e:  # pragma: no cover
        pytest.skip(f"database with migration 41 not reachable: {e}")
    return Ledger(_connect)


def _sql(q, params=()):
    with _connect() as c:
        cur = c.cursor()
        cur.execute(q, params)
        rows = cur.fetchall() if cur.description else None
        c.commit()
        return rows


METRICS = {"rev_yoy": 0.055, "op_margin": 0.1524, "op_margin_py": -0.0332, "op_margin_yoy_bps": 1856.6}


def _event(symbol, **over):
    e = {"id": 1, "symbol": symbol, "event_type": "margin_expansion", "fiscal_year": 2026, "fiscal_quarter": 3, "quarter_end": "2026-09-30", "available_at": "2026-11-05",
         "availability_basis": "sec_acceptance", "metrics": dict(METRICS), "context": {}, "first_detected_on": "2026-11-06"}
    e.update(over)
    return e


def _update(symbol, news=None, check_freshness=True, **over):
    u, why = UM.build_update(_event(symbol, **over), company_name="Test Co", cik=1, accession="0000000001-26-000001", news=news, check_freshness=check_freshness)
    assert u is not None, why
    return u


@pytest.fixture
def sym():
    return "ZZ" + "".join(random.choices(string.ascii_uppercase, k=6))


NEWS = [UM.NewsItem("2026-11-04T23:03:00Z", "Fiscal.ai", "Test Co Reports Q3 2026 Financial Results", "https://example.com/r")]


def test_publish_is_idempotent(ledger, sym):
    u = _update(sym, news=NEWS)
    assert ledger.publish(u, 11, "inflection_v2_sec_pit") == "published"
    assert ledger.publish(u, 11, "inflection_v2_sec_pit") == "exists"
    assert ledger.publish(_update(sym), 11, "inflection_v2_sec_pit") == "exists"          # even with different content: version 1 is written once
    rows = _sql("SELECT version, kind, public_json, event_id, method_version FROM fundamental_update_publication WHERE update_key = %s", (u.key,))
    assert len(rows) == 1 and rows[0][:2] == (1, "initial")
    assert rows[0][2] == json.loads(json.dumps(u.to_public_dict())) and set(rows[0][2]) == set(UM.PUBLIC_FIELDS)
    assert rows[0][3:] == (11, "inflection_v2_sec_pit")


def test_correction_versions_and_frozen_news(ledger, sym):
    u = _update(sym, news=NEWS)
    ledger.publish(u, 11, "inflection_v2_sec_pit")
    assert ledger.revise(u.key, _update(sym), 11, "inflection_v2_sec_pit") == "unchanged"
    restated = _update(sym, metrics={**METRICS, "op_margin": 0.12}, check_freshness=False)
    assert ledger.revise(u.key, restated, 11, "inflection_v2_sec_pit") == "restated"
    assert ledger.revise(u.key, restated, 11, "inflection_v2_sec_pit") == "unchanged"   # the same correction twice is one version
    redated = _update(sym, metrics={**METRICS, "op_margin": 0.12}, available_at="2026-11-07", check_freshness=False)
    assert ledger.revise(u.key, redated, 11, "inflection_v2_sec_pit") == "redated"
    assert ledger.revise(u.key, None, 11, "inflection_v2_sec_pit") == "withdrawn"
    assert ledger.revise(u.key, restated, 11, "inflection_v2_sec_pit") == "already_withdrawn"
    rows = _sql("SELECT version, kind, changed, public_json FROM fundamental_update_publication WHERE update_key = %s ORDER BY version", (u.key,))
    assert [(r[0], r[1]) for r in rows] == [(1, "initial"), (2, "restated"), (3, "redated"), (4, "withdrawn")]
    assert "Operating margin" in rows[1][2] and "why" in rows[1][2] and rows[2][2] == ["public_date"]
    assert all(r[3]["news"] == rows[0][3]["news"] and r[3]["news"] for r in rows)        # news frozen at first publication
    assert rows[3][3] == rows[2][3]                                                       # a withdrawal keeps the last published facts
    cur = _sql("SELECT version, kind, first_published_at <= updated_at, first_public_date FROM fundamental_update_current WHERE update_key = %s", (u.key,))
    assert cur == [(4, "withdrawn", True, __import__("datetime").date(2026, 11, 5))]


def test_not_published_and_lost_race(ledger, sym, monkeypatch):
    assert ledger.revise(f"{sym}/2026-09-30/margin-expansion", _update(sym), 1, "v") == "not_published"
    u = _update(sym)
    ledger.publish(u, 1, "inflection_v2_sec_pit")
    stale = ledger.latest(u.key)
    restated = _update(sym, metrics={**METRICS, "op_margin": 0.12}, check_freshness=False)
    assert ledger.revise(u.key, restated, 1, "inflection_v2_sec_pit") == "restated"          # another writer wins version 2 ...
    monkeypatch.setattr(ledger, "latest", lambda key: stale)                                 # ... while this one still sees version 1
    again = _update(sym, metrics={**METRICS, "op_margin": 0.10}, check_freshness=False)
    assert ledger.revise(u.key, again, 1, "inflection_v2_sec_pit") == "lost_race"
    assert _sql("SELECT count(*) FROM fundamental_update_publication WHERE update_key = %s", (u.key,)) == [(2,)]


@pytest.mark.parametrize("stmt", ["UPDATE fundamental_update_publication SET kind = 'restated' WHERE update_key = %s",
                                  "DELETE FROM fundamental_update_publication WHERE update_key = %s"])
def test_append_only(ledger, sym, stmt):
    u = _update(sym)
    ledger.publish(u, 1, "inflection_v2_sec_pit")
    with pytest.raises(psycopg2.Error, match="append-only"):
        _sql(stmt, (u.key,))
    with pytest.raises(psycopg2.Error, match="append-only"):
        _sql("TRUNCATE fundamental_update_publication")


@pytest.mark.parametrize("key,version,kind", [("not a key", 1, "initial"), ("ZZBAD/2026-09-30/margin-expansion", 2, "initial"),
                                              ("ZZBAD/2026-09-30/margin-expansion", 1, "restated"), ("ZZBAD/2026-09-30/margin-expansion", 1, "deleted")])
def test_constraints(ledger, key, version, kind):
    with pytest.raises(psycopg2.Error):
        _sql("INSERT INTO fundamental_update_publication (update_key, version, kind, public_json, content_hash, render_version, symbol, quarter_end, event_type, public_date, method_version) "
             "VALUES (%s, %s, %s, '{}'::jsonb, %s, 'fu1', 'ZZBAD', '2026-09-30', 'margin_expansion', '2026-11-05', 'v')", (key, version, kind, "0" * 64))


# ======================================================================================================== migration scripts (apply twice, verify, rollback)
def _script(name):
    base = "mechanism" if name.startswith("add_") else os.path.join("deploy", "db")
    return open(os.path.join(ROOT, base, name), encoding="utf-8").read()


def test_verify_script_passes_and_reapplying_the_migration_is_harmless(ledger, sym):
    ledger.publish(_update(sym), 1, "inflection_v2_sec_pit")
    with _connect() as c:
        cur = c.cursor()
        cur.execute(_script("add_fundamental_update_publication.sql"))                     # second apply: IF NOT EXISTS / OR REPLACE / DROP TRIGGER IF EXISTS
        cur.execute(_script("fundamental_update_publication_verify.sql"))
        assert cur.fetchone() == ("migration 41 verified",)
        cur.execute("SELECT count(*) FROM fundamental_update_publication WHERE update_key = %s", (f"{sym}/2026-09-30/margin-expansion",))
        assert cur.fetchone() == (1,)                                                     # re-applying kept the data
        c.rollback()


def test_rollback_refuses_with_rows_and_removes_everything_when_confirmed(ledger, sym):
    ledger.publish(_update(sym), 1, "inflection_v2_sec_pit")
    rollback = _script("fundamental_update_publication_rollback.sql")
    with _connect() as c:
        cur = c.cursor()
        with pytest.raises(psycopg2.Error, match="has rows"):
            cur.execute(rollback)
        c.rollback()
        cur = c.cursor()
        cur.execute("SET LOCAL fup.confirm_drop_with_rows = 'yes'")
        cur.execute(rollback)
        assert cur.fetchone() == ("migration 41 removed",)
        c.rollback()                                                                      # DDL is transactional: the test leaves everything in place
    assert _sql("SELECT count(*) > 0 FROM fundamental_update_publication") == [(True,)]
