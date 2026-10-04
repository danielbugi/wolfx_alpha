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
            admin.cursor().execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
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


LABEL_MIGRATIONS = ["create_trading_schema.sql", "add_market_data_tables.sql"] + MIGRATIONS + [
    "add_forward_return_label_table.sql"]


@pytest.fixture
def labels_env():
    """Migrations 19-22 + 26 on top of the real base schema (stock_prices) and market_index_prices, in a throwaway schema."""
    yield from _schema_env(LABEL_MIGRATIONS)


@pytest.fixture
def lconn(labels_env):
    with labels_env[1]() as c:
        yield c
        c.rollback()


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
                   signal_type="bullish_breakout" if direction == 1 else "bearish_breakout", triggered=True,
                   entry_close=10.5, channel_high_prev=10,
                   channel_low_prev=8, distance_to_channel_pct=1.5, atr_source="measured", passed_guard=True,
                   guard_reasons=None,
                   ml_status="not_processed", tracked_intent=True, snapshot_id=snapshot_id,
                   capture_run_id=run_id, capture_hash="c" * 64)
        row.update(kw)
        cols = ", ".join(row)
        return self.execute(
            f"INSERT INTO candidate_observation ({cols}) VALUES ({', '.join('%(' + k + ')s' for k in row)}) RETURNING id",
            row).fetchone()[0]

    def ticket(self, approved=True, closed=False, expired=False, table="candidate_observation"):
        """A ticket row inserted directly (superuser / owner connection) -- for CHECK-constraint and read-layer
        tests. It does NOT authorise anything: only research_maintenance_begin() inside the transaction does
        (see test_maintenance_hatch.py)."""
        cur = self.execute(
            "INSERT INTO research_maintenance_log (opened_by, reason, target_table, opened_at, approved_by, "
            "approved_at, expires_at, closed_at) VALUES ('tester', 'unit test ticket', %s, NOW() - INTERVAL '3 hours', "
            "%s, %s, %s, %s) RETURNING id",
            (table, "owner" if approved else None,
             (None if not approved else "2099-01-01 00:00+00" if not expired else "2000-01-01 00:00+00"),
             (None if not approved else "2099-01-01 01:00+00" if not expired else "2000-01-01 01:00+00"),
             "2099-01-01 00:30+00" if closed else None))
        return cur.fetchone()[0]


@pytest.fixture
def seed(conn):
    return Seed(conn)


# ---------------------------------------------------------------------------------------------------------------
# Role-model fixtures. They need a SUPERUSER connection (CREATE ROLE, ALTER ... OWNER, SET SESSION AUTHORIZATION).
# The CI database user is a superuser, so these must run there (a skip is a CI failure). Locally the default dev
# role is not a superuser: point DB_* at a disposable cluster to run them.
# ---------------------------------------------------------------------------------------------------------------
ROLES_SQL = os.path.join(ROOT, "deploy", "db", "research_roles.sql")
ROLES_VERIFY_SQL = os.path.join(ROOT, "deploy", "db", "research_roles_verify.sql")
ROLES_ROLLBACK_SQL = os.path.join(ROOT, "deploy", "db", "research_roles_rollback.sql")


def read_sql(path, names=None):
    """The script text with the fixed role names replaced by throwaway ones (`names`: fixed -> throwaway)."""
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    for fixed in sorted(names or {}, key=len, reverse=True):
        text = text.replace(fixed, names[fixed])
    return text


def strip_psql_meta(text):
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("\\"))


class RoleEnv:
    def __init__(self, schema, connect, prefix, admin):
        self.schema, self.connect, self.admin = schema, connect, admin
        self.names = {"donchian_owner": f"{prefix}_owner", "donchian_app": f"{prefix}_app",
                      "donchian_research_admin": f"{prefix}_admin"}
        self.owner, self.app, self.group = (self.names[k] for k in ("donchian_owner", "donchian_app",
                                                                    "donchian_research_admin"))
        self.alice, self.bob, self.carol = f"{prefix}_alice", f"{prefix}_bob", f"{prefix}_carol"
        self.prefix = prefix

    def sql(self, path):
        return strip_psql_meta(read_sql(path, self.names))

    def run_script(self, path, **gucs):
        cur = self.admin.cursor()
        cur.execute(f'SET search_path TO "{self.schema}"')
        for k, v in gucs.items():
            cur.execute("SELECT set_config(%s, %s, false)", (k.replace("__", "."), v))
        self.admin.commit()  # session-level SETs must survive a ROLLBACK inside the script
        cur.execute(self.sql(path))
        self.admin.commit()

    def as_user(self, conn, role):
        """Switch the connection's session_user (superuser connections only). Commits first."""
        conn.commit()
        conn.autocommit = True
        conn.cursor().execute(f'SET SESSION AUTHORIZATION "{role}"')
        conn.autocommit = False

    def back_to_super(self, conn):
        conn.rollback()
        conn.autocommit = True
        conn.cursor().execute("RESET SESSION AUTHORIZATION")
        conn.autocommit = False


@pytest.fixture
def role_env(schema_env):
    import psycopg2
    schema, connect = schema_env
    admin = psycopg2.connect(**_connect_args())
    cur = admin.cursor()
    cur.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
    if not cur.fetchone()[0]:
        admin.close()
        pytest.skip("role-model tests need a superuser DB role (CI's trading_user is one; locally use a disposable "
                    "cluster via DB_HOST/DB_PORT/DB_USER)")
    prefix = "rbt_" + uuid.uuid4().hex[:8]
    env = RoleEnv(schema, connect, prefix, admin)
    try:
        yield env
    finally:
        try:
            admin.rollback()
            cur = admin.cursor()
            cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            admin.commit()
            for r in (env.alice, env.bob, env.carol, env.app, env.group, env.owner):
                cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (r,))
                if cur.fetchone():
                    cur.execute(f'DROP OWNED BY "{r}" CASCADE')
                    cur.execute(f'DROP ROLE "{r}"')
            admin.commit()
        finally:
            admin.close()


@pytest.fixture
def roles(role_env):
    """The role model applied to the throwaway schema, plus three maintenance people (alice, bob, carol), each with
    their own login role that is a member of the research-admin group."""
    role_env.run_script(ROLES_SQL)
    cur = role_env.admin.cursor()
    for person in (role_env.alice, role_env.bob, role_env.carol):
        cur.execute(f'CREATE ROLE "{person}" LOGIN IN ROLE "{role_env.group}"')
    role_env.admin.commit()
    return role_env
