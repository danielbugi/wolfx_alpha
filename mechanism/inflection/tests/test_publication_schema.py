"""Migration 41 hardening against a real PostgreSQL: the database-enforced public allowlist, fail-closed (re)application, the runtime role's exact
privileges, the read-only verify script catching drift, and a full backup -> rollback -> re-apply -> restore drill with the real pg_dump / psql.

Everything that changes shared state runs inside a transaction that is rolled back; the restore drill uses its own throwaway database. The
allowlist-equality test needs no database. Skipped without a database with migration 41 (the CI db-bootstrap job has one and fails on any skip)."""
import contextlib
import json
import os
import random
import re
import shutil
import string
import subprocess
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))

from inflection import update_model as UM  # noqa: E402
from inflection.update_ledger import Ledger  # noqa: E402

MIGRATION = os.path.join(ROOT, "mechanism", "add_fundamental_update_publication.sql")
VERIFY = os.path.join(ROOT, "deploy", "db", "fundamental_update_publication_verify.sql")
ROLLBACK = os.path.join(ROOT, "deploy", "db", "fundamental_update_publication_rollback.sql")


def _read(p):
    return open(p, encoding="utf-8").read()


# ======================================================================================================== no database needed
def test_the_sql_allowlist_is_exactly_public_fields():
    sql = _read(MIGRATION)
    block = sql[sql.index("CONSTRAINT fundamental_update_publication_public_keys_chk"):sql.index("CONSTRAINT fundamental_update_publication_public_nested_chk")]
    arrays = re.findall(r"ARRAY\[('key'.*?)\]", block, flags=re.S)
    assert len(arrays) == 2                                                        # ?& (all present) and - (nothing else)
    for a in arrays:
        assert tuple(re.findall(r"'([a-z_]+)'", a)) == UM.PUBLIC_FIELDS


def test_the_writer_refuses_unapproved_nested_fields_before_connecting():
    """Write-time check: the ledger validates the dict before it opens a connection, so an unapproved field never reaches the database."""
    def no_db():
        raise AssertionError("the ledger must not connect for a refused dict")
    ledger = Ledger(no_db)
    for tamper in (lambda d: d["figures"][0].update(internal_score="91.5"), lambda d: d["links"].update(admin="https://internal.example"),
                   lambda d: d.update(news=[{"published_at": "t", "source": "s", "headline": "h", "url": "u", "rank": 1}]),
                   lambda d: d.update(market_cap={"value": "$1B", "as_of": "2026-11-05", "raw": 1.0e9})):
        u = _update("ZXWRITE")
        d = u.to_public_dict()
        tamper(d)
        assert UM.public_dict_problem(d) is not None
        u.to_public_dict = lambda d=d: d                                           # the ledger serialises through to_public_dict()
        with pytest.raises(ValueError, match="public_json refused") as e:
            ledger.publish(u, 1, "v")
        assert "91.5" not in str(e.value) and "internal.example" not in str(e.value)   # the message names the field, never its content
    assert UM.public_dict_problem(_update("ZXWRITE").to_public_dict()) is None    # what build_update produces passes


# ======================================================================================================== real PostgreSQL
psycopg2 = pytest.importorskip("psycopg2")
from psycopg2 import errors  # noqa: E402


def _dsn(**over):
    d = dict(host=os.getenv("DB_HOST", "localhost"), port=int(os.getenv("DB_PORT", "5432")), dbname=os.getenv("DB_NAME", "trading_production"),
             user=os.getenv("DB_USER", "postgres"), password=os.getenv("DB_PASSWORD", ""), connect_timeout=5)
    d.update(over)
    return d


@contextlib.contextmanager
def _connect(**over):
    conn = psycopg2.connect(**_dsn(**over))
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture(scope="module")
def db():
    try:
        with _connect() as c:
            c.cursor().execute("SELECT 1 FROM fundamental_update_publication LIMIT 1")
    except Exception as e:  # pragma: no cover
        pytest.skip(f"database with migration 41 not reachable: {e}")
    return True


@contextlib.contextmanager
def _rolled_back():
    """A connection whose every change is rolled back at the end (DDL and roles included: both are transactional in PostgreSQL)."""
    with _connect() as c:
        try:
            yield c, c.cursor()
        finally:
            c.rollback()


def _sym():
    return "ZX" + "".join(random.choices(string.ascii_uppercase, k=6))


def _update(symbol):
    ev = {"id": 1, "symbol": symbol, "event_type": "margin_expansion", "fiscal_year": 2026, "fiscal_quarter": 3, "quarter_end": "2026-09-30",
          "available_at": "2026-11-05", "availability_basis": "sec_acceptance", "first_detected_on": "2026-11-06", "context": {},
          "metrics": {"rev_yoy": 0.055, "op_margin": 0.1524, "op_margin_py": -0.0332, "op_margin_yoy_bps": 1856.6}}
    u, why = UM.build_update(ev, company_name="Test Co", cik=1, accession="0000000001-26-000001")
    assert u, why
    return u


_INSERT = ("INSERT INTO fundamental_update_publication (update_key, version, kind, changed, public_json, content_hash, render_version, symbol, quarter_end, "
           "event_type, public_date, method_version) VALUES (%s, 1, 'initial', %s::jsonb, %s::jsonb, %s, %s, %s, '2026-09-30', %s, '2026-11-05', 'v')")


def _row(d, changed="[]", **col):
    return (col.get("update_key", d["key"]), changed, json.dumps(d), col.get("content_hash", d["content_hash"]), col.get("render_version", d["render_version"]),
            col.get("symbol", d["symbol"]), col.get("event_type", d["event_type"]))


def _ok(d):
    return d


def _extra_top(d):
    return dict(d, score=91.5)


def _missing(d):
    return {k: v for k, v in d.items() if k != "why"}


def _figures_object(d):
    return dict(d, figures={"label": "x"})


def _event_id_inside(d):
    return dict(d, event_id=11)


def _score_in_figure(d):
    return dict(d, figures=[dict(d["figures"][0], internal_score=91.5)] + d["figures"][1:])


def _internal_link(d):
    return dict(d, links=dict(d["links"], admin="https://internal.example"))


def _news_rank(d):
    return dict(d, news=[{"published_at": "2026-11-05T12:00:00Z", "source": "S", "headline": "H", "url": "https://x.example", "rank": 1}])


def _raw_market_cap(d):
    return dict(d, market_cap={"value": "$1.00B", "as_of": "2026-11-05", "raw": 1.0e9})


def _numeric_figure(d):
    return dict(d, figures=[dict(d["figures"][0], value=0.1524)] + d["figures"][1:])


@pytest.mark.parametrize("mutate,col,constraint", [
    (_extra_top, {}, "public_keys_chk"),                                           # an internal score cannot be stored as a public field
    (_event_id_inside, {}, "public_keys_chk"),                                     # nor internal provenance
    (_missing, {}, "public_keys_chk"),
    (_figures_object, {}, "public_keys_chk"),
    (_score_in_figure, {}, "public_nested_chk"),                                    # nor inside a figure,
    (_internal_link, {}, "public_nested_chk"),                                      # an internal link,
    (_news_rank, {}, "public_nested_chk"),                                          # a ranking in a news item,
    (_raw_market_cap, {}, "public_nested_chk"),                                     # an unformatted figure beside the public one,
    (_numeric_figure, {}, "public_nested_chk"),                                     # or a raw number where the public text belongs
    (_ok, {"update_key": "ZZOTHER/2026-09-30/margin-expansion"}, "public_match_chk"),
    (_ok, {"symbol": "ZZOTHER"}, "public_match_chk"),
    (_ok, {"content_hash": "f" * 64}, "public_match_chk"),
    (_ok, {"render_version": "fu9"}, "public_match_chk"),
    (_ok, {"event_type": "revenue_acceleration"}, "public_match_chk"),
])
def test_the_database_enforces_the_public_allowlist_and_column_consistency(db, mutate, col, constraint):
    d = mutate(_update(_sym()).to_public_dict())
    with _rolled_back() as (c, cur):
        with pytest.raises(errors.CheckViolation, match=constraint):
            cur.execute(_INSERT, _row(d, **col))


def test_changed_must_be_a_list_and_a_valid_row_is_accepted(db):
    d = _update(_sym()).to_public_dict()
    with _rolled_back() as (c, cur):
        cur.execute("SAVEPOINT s")
        with pytest.raises(errors.CheckViolation, match="changed_chk"):
            cur.execute(_INSERT, _row(d, changed='{"a": 1}'))
        cur.execute("ROLLBACK TO SAVEPOINT s")
        cur.execute(_INSERT, _row(d))                                              # the same row, valid, goes in


def test_the_runtime_role_can_only_add_and_read(db):
    d = _update(_sym()).to_public_dict()
    with _rolled_back() as (c, cur):
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = 'donchian_app'")
        if cur.fetchone() is None:
            cur.execute("CREATE ROLE donchian_app NOLOGIN")                       # rolled back with everything else
        cur.execute(_read(MIGRATION))                                              # re-apply: grants for the role, and its own post-check
        cur.execute("SET LOCAL ROLE donchian_app")
        cur.execute(_INSERT + " RETURNING id", _row(d))
        assert cur.fetchone()[0] > 0                                               # INSERT works (USAGE on the id sequence, EXECUTE on the check)
        cur.execute("SAVEPOINT s")
        with pytest.raises(errors.CheckViolation, match="public_nested_chk"):      # the nested allowlist binds the runtime role
            cur.execute(_INSERT, _row(_score_in_figure(_update(_sym()).to_public_dict())))
        cur.execute("ROLLBACK TO SAVEPOINT s")
        cur.execute("SELECT count(*) FROM fundamental_update_current WHERE update_key = %s", (d["key"],))
        assert cur.fetchone() == (1,)                                              # SELECT on the view works
        for stmt in ("UPDATE fundamental_update_publication SET kind = 'restated' WHERE update_key = %(k)s",
                     "DELETE FROM fundamental_update_publication WHERE update_key = %(k)s",
                     "TRUNCATE fundamental_update_publication",
                     "SELECT setval('fundamental_update_publication_id_seq', 1)",
                     "ALTER TABLE fundamental_update_publication DISABLE TRIGGER fundamental_update_publication_no_update",
                     "DROP TABLE fundamental_update_publication",
                     "INSERT INTO fundamental_update_current (update_key) VALUES ('x')"):
            cur.execute("SAVEPOINT s")
            with pytest.raises((errors.InsufficientPrivilege, errors.ObjectNotInPrerequisiteState, errors.WrongObjectType, errors.FeatureNotSupported)):
                cur.execute(stmt, {"k": d["key"]})
            cur.execute("ROLLBACK TO SAVEPOINT s")
        cur.execute("RESET ROLE")
        cur.execute("SELECT count(*) FROM fundamental_update_publication WHERE update_key = %s", (d["key"],))
        assert cur.fetchone() == (1,)                                              # the row is untouched


def test_append_only_also_binds_the_owner_and_replica_sessions(db):
    d = _update(_sym()).to_public_dict()
    with _rolled_back() as (c, cur):
        cur.execute(_INSERT, _row(d))
        cur.execute("SET LOCAL session_replication_role = replica")             # ENABLE ALWAYS: still fires
        for stmt in ("UPDATE fundamental_update_publication SET kind = 'restated' WHERE update_key = %(k)s",
                     "DELETE FROM fundamental_update_publication WHERE update_key = %(k)s", "TRUNCATE fundamental_update_publication"):
            cur.execute("SAVEPOINT s")
            with pytest.raises(psycopg2.Error, match="append-only"):
                cur.execute(stmt, {"k": d["key"]})
            cur.execute("ROLLBACK TO SAVEPOINT s")


@pytest.mark.parametrize("tamper,message", [
    ("ALTER TABLE fundamental_update_publication ALTER COLUMN method_version TYPE VARCHAR(80)", "unexpected schema"),
    ("ALTER TABLE fundamental_update_publication ADD COLUMN score NUMERIC", "unexpected schema"),
    ("ALTER TABLE fundamental_update_publication ALTER COLUMN event_id SET NOT NULL", "unexpected schema"),
    ("ALTER TABLE fundamental_update_publication ALTER COLUMN changed DROP DEFAULT", "unexpected schema"),
    ("ALTER TABLE fundamental_update_publication DROP CONSTRAINT fundamental_update_publication_public_keys_chk", "unexpected constraints"),
    ("ALTER TABLE fundamental_update_publication DROP CONSTRAINT fundamental_update_publication_public_nested_chk", "unexpected constraints"),
    ("ALTER TABLE fundamental_update_publication ADD CONSTRAINT extra_chk CHECK (version < 1000)", "unexpected constraints"),
    ("CREATE INDEX extra_idx ON fundamental_update_publication (symbol)", "unexpected indexes"),
    ("CREATE TRIGGER extra_trg BEFORE INSERT ON fundamental_update_publication FOR EACH ROW EXECUTE FUNCTION fundamental_update_publication_append_only()",
     "unexpected triggers"),
])
def test_reapplying_over_an_unexpected_schema_fails_closed(db, tamper, message):
    with _rolled_back() as (c, cur):
        cur.execute(tamper)
        with pytest.raises(psycopg2.Error, match=message):
            cur.execute(_read(MIGRATION))


@pytest.mark.parametrize("tamper,message", [
    ("ALTER TABLE fundamental_update_publication ENABLE TRIGGER fundamental_update_publication_no_update", "unexpected triggers"),   # ALWAYS -> origin
    ("ALTER TABLE fundamental_update_publication DISABLE TRIGGER fundamental_update_publication_no_truncate", "unexpected triggers"),
    ("GRANT SELECT ON fundamental_update_publication TO PUBLIC", "PUBLIC has a privilege"),
    ("GRANT USAGE ON SEQUENCE fundamental_update_publication_id_seq TO PUBLIC", "PUBLIC has a privilege"),
    ("CREATE ROLE donchian_app NOLOGIN; GRANT SELECT, INSERT, UPDATE ON fundamental_update_publication TO donchian_app; "
     "GRANT SELECT ON fundamental_update_current TO donchian_app; GRANT USAGE ON SEQUENCE fundamental_update_publication_id_seq TO donchian_app", "must not have UPDATE"),
    ("CREATE ROLE donchian_app NOLOGIN; GRANT SELECT, INSERT ON fundamental_update_publication TO donchian_app; "
     "GRANT SELECT ON fundamental_update_current TO donchian_app; GRANT USAGE, UPDATE ON SEQUENCE fundamental_update_publication_id_seq TO donchian_app", "not UPDATE (setval)"),
    ("CREATE OR REPLACE VIEW fundamental_update_current AS SELECT DISTINCT ON (p.update_key) p.update_key, p.version, p.kind, p.public_json, p.content_hash, p.symbol, "
     "p.quarter_end, p.event_type, p.sector, p.public_date, p.published_at AS updated_at, p.published_at AS first_published_at, p.public_date AS first_public_date "
     "FROM fundamental_update_publication p ORDER BY p.update_key, p.version ASC", "not the latest version"),
    ("CREATE OR REPLACE FUNCTION fundamental_update_public_json_ok(p jsonb) RETURNS boolean LANGUAGE sql IMMUTABLE AS $f$SELECT jsonb_typeof(p) = 'object'$f$",
     "does not enforce the nested allowlist"),                                     # a weakened function
    ("ALTER FUNCTION fundamental_update_public_json_ok(jsonb) VOLATILE", "must be IMMUTABLE"),
    ("ALTER FUNCTION fundamental_update_public_json_ok(jsonb) SECURITY DEFINER", "must be IMMUTABLE and not SECURITY DEFINER"),
    ("GRANT EXECUTE ON FUNCTION fundamental_update_public_json_ok(jsonb) TO PUBLIC", "PUBLIC can execute"),
])
def test_the_verify_script_catches_drift(db, tamper, message):
    d = _update(_sym()).to_public_dict()
    with _rolled_back() as (c, cur):
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = 'donchian_app'")
        if cur.fetchone() is not None and "CREATE ROLE donchian_app" in tamper:
            tamper = tamper.replace("CREATE ROLE donchian_app NOLOGIN; ", "REVOKE ALL ON fundamental_update_publication FROM donchian_app; ")
        cur.execute(_INSERT, _row(d))
        # a second, corrected version makes the view's "latest version" observable
        d2 = dict(d, why=d["why"] + " Restated.")
        cur.execute(_INSERT.replace("VALUES (%s, 1, 'initial'", "VALUES (%s, 2, 'restated'"), _row(d2, changed='["why"]'))
        cur.execute(_read(VERIFY))
        assert cur.fetchone() == ("migration 41 verified",)                       # clean before the tamper
        cur.execute(tamper)
        with pytest.raises(psycopg2.Error, match=re.escape(message)):
            cur.execute(_read(VERIFY))


def test_the_verify_script_finds_rows_stored_under_a_weaker_check(db):
    """A row that got in while the nested check was weaker (here: replaced by a permissive version, then restored by re-applying the migration)
    is found by the verify script; the migration's own post-check checks the function, the verify script also checks every stored row."""
    with _rolled_back() as (c, cur):
        cur.execute("CREATE OR REPLACE FUNCTION fundamental_update_public_json_ok(p jsonb) RETURNS boolean LANGUAGE sql IMMUTABLE AS 'SELECT true'")
        cur.execute(_INSERT, _row(_score_in_figure(_update(_sym()).to_public_dict())))
        cur.execute(_read(MIGRATION))                                              # the function is correct again; the row is still there
        with pytest.raises(psycopg2.Error, match="stored rows hold an unapproved nested field"):
            cur.execute(_read(VERIFY))


def test_reapplying_heals_grants_and_trigger_state(db):
    with _rolled_back() as (c, cur):
        cur.execute("GRANT SELECT ON fundamental_update_publication TO PUBLIC")
        cur.execute("ALTER TABLE fundamental_update_publication ENABLE TRIGGER fundamental_update_publication_no_update")
        cur.execute("CREATE OR REPLACE FUNCTION fundamental_update_public_json_ok(p jsonb) RETURNS boolean LANGUAGE sql IMMUTABLE AS 'SELECT true'")
        cur.execute(_read(MIGRATION))                                              # revokes, recreates the triggers as ALWAYS, restores the check
        cur.execute(_read(VERIFY))
        assert cur.fetchone() == ("migration 41 verified",)


def test_the_verify_script_runs_in_a_read_only_session(db):
    with _rolled_back() as (c, cur):
        cur.execute("SET TRANSACTION READ ONLY")
        cur.execute(_read(VERIFY))
        assert cur.fetchone() == ("migration 41 verified",)


def test_rollback_is_idempotent_and_touches_nothing_else(db):
    with _rolled_back() as (c, cur):
        cur.execute("SET LOCAL fup.confirm_drop_with_rows = 'yes'")                # the shared test database holds other tests' rows
        cur.execute("SELECT to_regclass('public.fundamental_inflection_event') IS NOT NULL")
        had_inflection = cur.fetchone()[0]
        cur.execute(_read(ROLLBACK))
        assert cur.fetchone() == ("migration 41 removed",)
        cur.execute(_read(ROLLBACK))                                               # a second run is harmless
        assert cur.fetchone() == ("migration 41 removed",)
        cur.execute("SELECT to_regclass('public.fundamental_inflection_event') IS NOT NULL")
        assert cur.fetchone()[0] == had_inflection                                 # nothing outside migration 41 was dropped


# ======================================================================================================== backup -> rollback -> re-apply -> restore drill
def _client_tools(server_major):
    """Command prefixes for pg_dump / psql of the SERVER's major version. A newer pg_dump writes settings an older server rejects (pg_dump 17+
    emits `SET transaction_timeout`, unknown to PostgreSQL 16: found by CI 2026-11-08), so the drill, like the production procedure (pg_dump run
    inside the postgres:16 container), must use the matching major version. FUP_PG_CLIENT_IMAGE (set by CI) runs the tools in that image."""
    image = os.getenv("FUP_PG_CLIENT_IMAGE")
    if image and shutil.which("docker"):
        base = ["docker", "run", "--rm", "-i", "--network", "host", "-e", "PGPASSWORD", image]
        return base + ["pg_dump"], base + ["psql"]
    for d in [f"/usr/lib/postgresql/{server_major}/bin", f"C:/Program Files/PostgreSQL/{server_major}/bin", None]:
        dump = shutil.which("pg_dump", path=d) if d else shutil.which("pg_dump")
        sql = shutil.which("psql", path=d) if d else shutil.which("psql")
        if dump and sql:
            m = re.search(r"(\d+)", subprocess.run([dump, "--version"], capture_output=True, text=True).stdout.split(")")[-1])
            if m and int(m.group(1)) == server_major:
                return [dump], [sql]
    return None, None


def test_backup_rollback_reapply_restore_drill(db):
    """The documented procedure, end to end, with the real client tools of the server's major version, in a throwaway database. All SQL goes in
    on stdin, exactly like the production commands (`docker exec -i ... psql ... < file`)."""
    with _connect() as c:
        cur = c.cursor()
        cur.execute("SHOW server_version_num")
        server_major = int(cur.fetchone()[0]) // 10000
    pg_dump, psql = _client_tools(server_major)
    if not pg_dump:
        pytest.skip(f"pg_dump / psql {server_major} not available (CI sets FUP_PG_CLIENT_IMAGE=postgres:{server_major}-alpine)")
    name = "fup_drill_" + "".join(random.choices(string.ascii_lowercase, k=8))
    admin = psycopg2.connect(**_dsn())
    admin.autocommit = True
    admin.cursor().execute(f"CREATE DATABASE {name}")
    env = dict(os.environ, PGPASSWORD=_dsn()["password"])
    conn_args = ["-h", _dsn()["host"], "-p", str(_dsn()["port"]), "-U", _dsn()["user"], "-d", name]
    tx = ["-v", "ON_ERROR_STOP=1", "-1", "-q"]

    def call(cmd, stdin=None):
        return subprocess.run(cmd, input=stdin, capture_output=True, text=True, env=env, timeout=180)

    def run(cmd, stdin=None):
        r = call(cmd, stdin)
        assert r.returncode == 0, r.stderr
        return r.stdout

    try:
        run(psql + conn_args + tx, _read(MIGRATION))
        # with no rows the rollback needs no confirmation; re-applying afterwards is clean
        assert "migration 41 removed" in run(psql + conn_args + tx, _read(ROLLBACK))
        run(psql + conn_args + tx, _read(MIGRATION))
        ledger = Ledger(lambda: _connect(dbname=name))
        syms = [_sym() for _ in range(3)]
        for s in syms:
            assert ledger.publish(_update(s), 7, "inflection_v2_sec_pit") == "published"
        assert ledger.revise(_update(syms[0]).key, None, 7, "inflection_v2_sec_pit") == "withdrawn"
        snapshot = "SELECT row_to_json(p)::text FROM fundamental_update_publication p ORDER BY id"
        with _connect(dbname=name) as c:
            cur = c.cursor()
            cur.execute(snapshot)
            before = [r[0] for r in cur.fetchall()]
        assert len(before) == 4

        # 1. back up (data only, this table only) -- the exact command of the rollback script's header
        dump = run(pg_dump + conn_args + ["--data-only", "--table=public.fundamental_update_publication"])
        assert "COPY public.fundamental_update_publication" in dump and "setval" in dump and "DISABLE TRIGGER" not in dump
        # 2. the rollback refuses while rows exist, then removes everything once confirmed in the same session
        r = call(psql + conn_args + tx, _read(ROLLBACK))
        assert r.returncode != 0 and "has rows" in r.stderr
        assert "migration 41 removed" in run(psql + conn_args + tx, "SET fup.confirm_drop_with_rows = 'yes';\n" + _read(ROLLBACK))
        # 3. re-apply the migration, 4. restore the backup in one transaction, 5. verify
        run(psql + conn_args + tx, _read(MIGRATION))
        run(psql + conn_args + tx, dump)
        assert "migration 41 verified" in run(psql + conn_args + ["-v", "ON_ERROR_STOP=1"], _read(VERIFY))
        with _connect(dbname=name) as c:
            cur = c.cursor()
            cur.execute(snapshot)
            assert [r[0] for r in cur.fetchall()] == before                       # every row, every column, every id
            cur.execute("SELECT nextval('fundamental_update_publication_id_seq') > (SELECT max(id) FROM fundamental_update_publication)")
            assert cur.fetchone() == (True,)                                       # new rows cannot collide with restored ids
            c.rollback()
        assert ledger.publish(_update(_sym()), 8, "inflection_v2_sec_pit") == "published"   # and the ledger works on the restored table
    finally:
        admin.cursor().execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
        admin.close()
