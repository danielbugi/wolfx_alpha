"""The read-only source connection (source_db.py): refused unless read-only is proven on the session, single SELECT only, store isolation,
and the DSN never appears in an error. Fake connections need no database; the real-PostgreSQL test is skipped when none is reachable."""
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))

from research_missions import source_db as S  # noqa: E402

SECRET_DSN = "postgresql://reader:s3cret-pw@127.0.0.1:15432/trading_production"


class PgError(Exception):
    def __init__(self, pgcode):
        super().__init__(f"pg {pgcode}")
        self.pgcode = pgcode


class FakeCursor:
    def __init__(self, conn):
        self.conn, self._row = conn, None

    def execute(self, sql, params=None):
        self.conn.sql.append(sql)
        if sql.startswith("SHOW "):
            self._row = (self.conn.settings[sql.split()[1]],)
        elif "rolsuper" in sql:
            self._row = (self.conn.superuser,)
        elif sql.startswith("CREATE TEMP TABLE"):
            if self.conn.probe_error is not None:
                raise PgError(self.conn.probe_error)
        else:
            self._row = None

    def fetchone(self):
        return self._row


class FakeConn:
    def __init__(self, ro="on", default_ro="on", probe_error="25006", superuser=False):
        self.superuser = superuser
        self.settings = {"transaction_read_only": ro, "default_transaction_read_only": default_ro}
        self.probe_error, self.sql, self.session, self.closed, self.kw = probe_error, [], None, False, None

    def cursor(self, cursor_factory=None):
        return FakeCursor(self)

    def set_session(self, **kw):
        self.session = kw

    def close(self):
        self.closed = True


def _connect_with(conn):
    def connect(dsn, **kw):
        conn.kw = kw
        return conn
    return connect


def test_a_proven_read_only_session_is_accepted_and_configured_at_both_ends():
    c = FakeConn()
    src = S.ReadOnlySourceDb(SECRET_DSN, connect=_connect_with(c))
    assert "default_transaction_read_only=on" in c.kw["options"] and "statement_timeout=" in c.kw["options"]
    assert c.session == {"readonly": True, "autocommit": True}
    assert src.proof["write_probe"].startswith("refused (25006")


@pytest.mark.parametrize("conn,why", [
    (FakeConn(ro="off"), "not read-only"),
    (FakeConn(default_ro="off"), "not read-only"),
    (FakeConn(probe_error=None), "write probe succeeded"),
    (FakeConn(probe_error="42501"), "unexpected reason"),          # permission denied is not proof of read-only
    (FakeConn(superuser=True), "superuser"),                        # could switch read-only off
])
def test_a_session_that_is_not_proven_read_only_is_refused_and_closed(conn, why):
    with pytest.raises(S.SourceNotReadOnly, match=why):
        S.ReadOnlySourceDb(SECRET_DSN, connect=_connect_with(conn))
    assert conn.closed


def test_connection_errors_never_echo_the_dsn():
    def boom(dsn, **kw):
        raise RuntimeError(f"could not connect: {dsn}")
    with pytest.raises(S.SourceNotReadOnly) as e:
        S.ReadOnlySourceDb(SECRET_DSN, connect=boom)
    assert "s3cret" not in str(e.value) and e.value.__cause__ is None and e.value.__suppress_context__


@pytest.mark.parametrize("sql", ["UPDATE companies SET company_name = 'x'", "DELETE FROM signal_ledger", "SELECT 1; DROP TABLE x",
                                 "INSERT INTO research_mission VALUES (1)", "  copy companies to stdout"])
def test_only_a_single_select_statement_runs(sql):
    src = S.ReadOnlySourceDb(SECRET_DSN, connect=_connect_with(FakeConn()))
    with pytest.raises(S.SourceNotReadOnly, match="single SELECT"):
        src.execute_dict_query(sql)


class FpDb:
    def __init__(self, fp):
        self.fp = fp

    def execute_dict_query(self, q, p=None):
        return [dict(self.fp)]


def test_the_store_must_be_a_different_database_from_the_source():
    prod = {"database": "trading_production", "started": "2026-10-01 09:00:00.123456+00", "port": 5432, "version": "PostgreSQL 16.4"}
    local = {**prod, "started": "2026-11-06 07:12:44.654321+00"}           # same name, same port, a different server
    assert S.assert_isolated(FpDb(local), FpDb(prod))["source"]["database"] == "trading_production"
    with pytest.raises(S.StoreNotIsolated):
        S.assert_isolated(FpDb(prod), FpDb(dict(prod)))


def test_no_source_dsn_means_no_source_connection(monkeypatch):
    monkeypatch.delenv(S.ENV, raising=False)
    assert S.from_env() is None


# ------------------------------------------------------------------------------------------------------------------------------------------------ real PostgreSQL
def _cfg():
    from shared.config import config
    return config.database if hasattr(config, "database") else config


def _dsn(user, password):
    c = _cfg()
    return f"host={c.db_host} port={c.db_port} dbname={c.db_name} user={user} password={password} connect_timeout=3"


def _admin():
    import psycopg2
    c = _cfg()
    try:
        conn = psycopg2.connect(_dsn(c.db_user, c.db_password))
    except Exception:
        pytest.skip("no PostgreSQL reachable")
    conn.autocommit = True
    return conn, c


def test_against_real_postgres_writes_are_refused_by_the_server():
    """On a scratch test database (CI): an ordinary login role is proven read-only and the server refuses its writes; the superuser is refused."""
    admin, c = _admin()
    try:
        cur = admin.cursor()
        cur.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
        if cur.fetchone()[0]:
            with pytest.raises(S.SourceNotReadOnly, match="superuser"):
                S.ReadOnlySourceDb(_dsn(c.db_user, c.db_password))
        cur.execute("DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'research_ro_citest') THEN "
                    "CREATE ROLE research_ro_citest LOGIN PASSWORD 'ci_only_ro'; END IF; END $$")
        cur.execute(f"GRANT CREATE, TEMPORARY ON DATABASE {c.db_name} TO research_ro_citest")   # only read-only mode may stop it
        src = S.ReadOnlySourceDb(_dsn("research_ro_citest", "ci_only_ro"))
        try:
            assert src.proof == {"transaction_read_only": "on", "default_transaction_read_only": "on",
                                 "write_probe": "refused (25006 read_only_sql_transaction)"}
            assert src.execute_dict_query("SELECT 1 AS one") == [{"one": 1}]
            with pytest.raises(Exception) as e:                # even bypassing the SELECT filter, the server refuses the write
                src.conn.cursor().execute("CREATE TABLE public.research_missions_should_never_exist (x int)")
            assert getattr(e.value, "pgcode", None) == "25006"
            assert set(S.fingerprint(src)) == {"database", "started", "port", "version"}
        finally:
            src.close()
    finally:
        admin.close()
