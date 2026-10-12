"""Throwaway-schema Postgres fixtures for the Market Intelligence tests (imported EXPLICITLY by each test module; deliberately NOT a conftest.py --
two conftest.py files in directories without __init__.py share the module name `conftest`, so one would shadow mechanism/research/tests/conftest.py): migrations 24 and 25 applied in a fresh schema per test, so the
real triggers, CHECKs and composite FKs are exercised without touching the public schema. Skips only when Postgres is unreachable (the
CI real-Postgres job treats any skip as a failure)."""
import os
import sys
import uuid
from contextlib import contextmanager

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(__file__))

MIGRATIONS = ["add_market_snapshot_tables.sql", "add_market_event_tables.sql", "add_source_observation_tables.sql",
              "add_catalyst_classification_table.sql", "add_stock_relative_strength_table.sql"]


def _args():
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
    return dict(host=os.environ["DB_HOST"], port=os.environ.get("DB_PORT", "5432"), dbname=os.environ["DB_NAME"],
                user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"])


@pytest.fixture
def mi_env():
    """(schema, connect): `connect()` yields a connection whose search_path is the throwaway schema only."""
    import psycopg2
    try:
        args = _args()
        admin = psycopg2.connect(**args)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Postgres unreachable: {type(e).__name__}")
    schema = "mi_t_" + uuid.uuid4().hex[:10]
    try:
        cur = admin.cursor()
        cur.execute(f'CREATE SCHEMA "{schema}"')
        cur.execute(f'SET search_path TO "{schema}"')
        for name in MIGRATIONS:
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
            admin.cursor().execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            admin.commit()
        finally:
            admin.close()


@pytest.fixture
def conn(mi_env):
    with mi_env[1]() as c:
        yield c
        c.rollback()


@pytest.fixture
def connect(mi_env):
    return mi_env[1]
