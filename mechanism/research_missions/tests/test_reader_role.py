"""deploy/db/research_reader_{role,verify,rollback}.sql on a real PostgreSQL with the full schema (CI's real-Postgres job; skipped elsewhere).

The role is created, verified, used for EVERY FirstLightDb query through the read-only adapter (so a research query that touches a table outside the
8 granted ones fails here, not in production), and removed again. The password below exists only in the CI scratch database."""
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))

from research_missions import source_db as S  # noqa: E402
from research_missions.sources import FirstLightDb  # noqa: E402

SQL = os.path.join(ROOT, "deploy", "db")


def _cfg():
    from shared.config import config
    return config.database if hasattr(config, "database") else config


def _admin():
    psycopg2 = pytest.importorskip("psycopg2")
    c = _cfg()
    try:
        conn = psycopg2.connect(host=c.db_host, port=c.db_port, dbname=c.db_name, user=c.db_user, password=c.db_password, connect_timeout=3)
    except Exception:
        pytest.skip("no PostgreSQL reachable")
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("SELECT to_regclass('public.stock_prices') IS NOT NULL AND to_regclass('public.bot_users') IS NOT NULL")
    if not cur.fetchone()[0]:
        pytest.skip("database without the full schema")
    return conn, c


def _run(conn, name):
    with open(os.path.join(SQL, name), encoding="utf-8") as f:
        conn.cursor().execute(f.read())


def test_the_reader_role_lifecycle_and_every_research_query_under_it():
    import psycopg2
    conn, c = _admin()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM pg_roles WHERE rolname = 'research_reader'")
    if cur.fetchone():
        _run(conn, "research_reader_rollback.sql")                  # a leftover from an interrupted run
    try:
        _run(conn, "research_reader_role.sql")
        with pytest.raises(psycopg2.Error, match="already exists"):
            _run(conn, "research_reader_role.sql")                   # refuses to re-create
        _run(conn, "research_reader_verify.sql")                     # raises on any failed check
        cur.execute("ALTER ROLE research_reader PASSWORD 'ci_scratch_only'")
        src = S.ReadOnlySourceDb(f"host={c.db_host} port={c.db_port} dbname={c.db_name} user=research_reader password=ci_scratch_only connect_timeout=3")
        try:
            assert src.proof["write_probe"].startswith("refused (25006")
            fl = FirstLightDb(src)
            fl.universe("ai_semiconductors")
            fl.company("MU")
            fl.event(1)
            fl.peers("MU")
            fl.company_facts(["MU", "NVDA"])
            fl.headlines(["MU", "NVDA"])
            with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                src.execute_dict_query("SELECT 1 FROM bot_users LIMIT 1")
            src.conn.cursor().execute("SET default_transaction_read_only = off")   # even with read-only switched off by the client,
            with pytest.raises(psycopg2.errors.InsufficientPrivilege):             # the grants alone refuse a write
                src.conn.cursor().execute("INSERT INTO companies (symbol) VALUES ('CITEST_RR')")
        finally:
            src.close()
    finally:
        _run(conn, "research_reader_rollback.sql")
        cur.execute("SELECT count(*) FROM pg_roles WHERE rolname = 'research_reader'")
        assert cur.fetchone()[0] == 0
        conn.close()
