"""Fixtures for the research-layer tests: a throwaway Postgres SCHEMA per test with migrations 19/20/21/22
applied, so the real triggers/constraints/ON CONFLICT semantics are exercised without ever touching the
public schema (the reference DB role has no CREATEDB). Skips only when Postgres is unreachable; the CI
real-Postgres job treats any skip as a failure."""
import json
import os
import sys
import uuid
from contextlib import contextmanager
from datetime import date

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))
sys.path.insert(0, ROOT)

MIGRATIONS = [
    "add_signal_ledger_tables.sql",
    "add_strategy_identity_release_a.sql",
    "add_signal_ledger_eval_flags.sql",
    "add_research_observation_tables.sql",
]
SESSION = date(2099, 1, 15)


def _connect_args():
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
    return dict(host=os.environ["DB_HOST"], port=os.environ.get("DB_PORT", "5432"),
                dbname=os.environ["DB_NAME"], user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"])


def _schema_env(migrations):
    import psycopg2
    try:
        args = _connect_args()
        admin = psycopg2.connect(**args)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Postgres unreachable: {type(e).__name__}")
    schema = "rb_t_" + uuid.uuid4().hex[:10]
    try:
        cur = admin.cursor()
        cur.execute(f'CREATE SCHEMA "{schema}"')
        cur.execute(f'SET search_path TO "{schema}"')
        for name in migrations:
            with open(os.path.join(ROOT, "mechanism", name), encoding="utf-8") as fh:
                cur.execute(fh.read())
        admin.commit()
    except Exception:
        admin.rollback()
        admin.close()
        raise

    @contextmanager
    def connect():
        conn = psycopg2.connect(options=f"-c search_path={schema}", **args)
        try:
            yield conn
        finally:
            conn.close()

    try:
        yield schema, connect
    finally:
        try:
            admin.rollback()
            admin.cursor().execute(f'DROP SCHEMA "{schema}" CASCADE')
            admin.commit()
        finally:
            admin.close()


@pytest.fixture
def schema_env():
    """(schema_name, connect) -- `connect()` is a context manager yielding a fresh connection whose
    search_path is the throwaway schema only."""
    yield from _schema_env(MIGRATIONS)


@pytest.fixture
def pre22_env():
    """The same, WITHOUT migration 22: the production state today (research tables absent)."""
    yield from _schema_env(MIGRATIONS[:3])


@pytest.fixture
def conn(schema_env):
    _, connect = schema_env
    with connect() as c:
        yield c
        c.rollback()


@pytest.fixture
def connect(schema_env):
    return schema_env[1]


def _json(v):
    return json.dumps(v, sort_keys=True)


class Seed:
    """Row builders with valid defaults; override any column via kwargs."""

    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql, params=None):
        cur = self.conn.cursor()
        cur.execute(sql, params)
        return cur

    def strategy_id(self):
        return self.execute("SELECT id FROM strategies LIMIT 1").fetchone()[0]

    def registry(self, version="t0_v1", manifest_hash="a" * 64):
        self.execute(
            "INSERT INTO feature_set_registry (feature_set_version, manifest, manifest_hash, impl_ref) "
            "VALUES (%s, %s::jsonb, %s, %s) ON CONFLICT DO NOTHING",
            (version, _json([{"name": "x"}]), manifest_hash, "test"))
        return version

    def run(self, session=SESSION, version="t0_v1"):
        self.registry(version)
        return self.execute(
            "INSERT INTO candidate_capture_run (strategy_id, strategy_version, session_date, "
            "feature_set_version, session_source) VALUES (%s, 'v1', %s, %s, 'explicit') RETURNING id",
            (self.strategy_id(), session, version)).fetchone()[0]

    def snapshot(self, symbol="AAA", session=SESSION, version="t0_v1", **kw):
        self.registry(version)
        row = dict(symbol=symbol, session_date=session, feature_set_version=version, bar_date=session,
                   open=10, high=11, low=9, close=10.5, volume=1000, prev_close=10, bars_available=300,
                   snapshot_status="complete", features=_json({"atr_14": 0.5}), missing_features=[],
                   manifest_hash="a" * 64, content_hash="b" * 64)
        row.update(kw)
        cols = ", ".join(row)
        return self.execute(
            f"INSERT INTO feature_snapshot ({cols}) VALUES ({', '.join('%(' + k + ')s' for k in row)}) RETURNING id",
            row).fetchone()[0]

    def observation(self, snapshot_id=None, run_id=None, symbol="AAA", session=SESSION, direction=1, **kw):
        snapshot_id = snapshot_id or self.snapshot(symbol, session)
        run_id = run_id or self.run(session)
        row = dict(strategy_id=self.strategy_id(), strategy_version="v1", symbol=symbol, session_date=session,
                   direction=direction, bar_date=session, session_source="explicit",
                   signal_type="bullish_breakout", triggered=True, entry_close=10.5, channel_high_prev=10,
                   channel_low_prev=8, distance_to_channel_pct=1.5, passed_guard=True, guard_reasons=None,
                   ml_status="not_processed", tracked_intent=True, snapshot_id=snapshot_id,
                   capture_run_id=run_id, capture_hash="c" * 64)
        row.update(kw)
        cols = ", ".join(row)
        return self.execute(
            f"INSERT INTO candidate_observation ({cols}) VALUES ({', '.join('%(' + k + ')s' for k in row)}) RETURNING id",
            row).fetchone()[0]

    def ticket(self, approved=True, closed=False, age_minutes=0, table="candidate_observation"):
        cur = self.execute(
            "INSERT INTO research_maintenance_log (opened_by, reason, target_table, opened_at, approved_by, "
            "approved_at, closed_at) VALUES ('tester', 'unit test', %s, NOW() - make_interval(mins => %s), "
            "%s, %s, %s) RETURNING id",
            (table, age_minutes, "owner" if approved else None,
             "2099-01-01" if approved else None, "2099-01-01" if closed else None))
        return cur.fetchone()[0]


@pytest.fixture
def seed(conn):
    return Seed(conn)
