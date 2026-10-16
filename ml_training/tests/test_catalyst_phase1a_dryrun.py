"""The Phase 1A dry-run script is scratch-only: it refuses a database that is not named scratch*, refuses without the explicit PHASE1A_SCRATCH=1, refuses a
scratch database that lacks the migrations, and never prints or stores the User-Agent (it contains a contact address)."""
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "ml_training", "experiments"))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))

import catalyst_phase1a_dryrun as D  # noqa: E402


class FakeCursor:
    def __init__(self, db_name, missing=()):
        self.db_name, self.missing, self._last = db_name, set(missing), None

    def execute(self, sql, params=None):
        self._last = (sql, params)

    def fetchone(self):
        sql, params = self._last
        if "current_database" in sql:
            return (self.db_name,)
        if "to_regclass" in sql:
            return (None,) if params[0] in self.missing else (params[0],)
        raise AssertionError(sql)


class FakeConn:
    def __init__(self, db_name, missing=()):
        self.c = FakeCursor(db_name, missing)

    def cursor(self):
        return self.c

    def commit(self):
        pass


@pytest.fixture
def env(monkeypatch):
    for k in ("DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD"):
        monkeypatch.setenv(k, "x")
    monkeypatch.setenv("DB_NAME", "whatever")
    monkeypatch.setenv("PHASE1A_SCRATCH", "1")
    return monkeypatch


@pytest.mark.parametrize("name", ["trading_production", "postgres", "donchian", "prod_scratch"])
def test_a_database_that_is_not_named_scratch_is_refused(env, name):
    env.setattr(D.psycopg2, "connect", lambda **kw: FakeConn(name))
    with pytest.raises(SystemExit, match="REFUSED"):
        D.connect()


def test_the_explicit_scratch_switch_is_required_even_for_a_scratch_name(env):
    env.setattr(D.psycopg2, "connect", lambda **kw: FakeConn("scratch_catalyst"))
    env.delenv("PHASE1A_SCRATCH")
    with pytest.raises(SystemExit, match="PHASE1A_SCRATCH=1"):
        D.connect()


def test_a_scratch_database_without_the_migrations_is_refused_naming_the_missing_table(env):
    env.setattr(D.psycopg2, "connect", lambda **kw: FakeConn("scratch_catalyst", missing=("identifier_link",)))
    with pytest.raises(SystemExit, match="identifier_link"):
        D.connect()


def test_a_correctly_prepared_scratch_database_is_accepted(env):
    env.setattr(D.psycopg2, "connect", lambda **kw: FakeConn("scratch_catalyst"))
    assert D.connect() is not None


def test_a_missing_or_placeholder_user_agent_stops_the_run_before_any_request(env, monkeypatch, tmp_path):
    f = tmp_path / "s.txt"
    f.write_text("AAPL\n")
    monkeypatch.setattr(sys, "argv", ["x", "--symbols-file", str(f), "--since", "2026-09-01", "--out", str(tmp_path / "o.json")])
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    with pytest.raises(SystemExit, match="SEC_USER_AGENT is required"):
        D.main()
    monkeypatch.setenv("SEC_USER_AGENT", "Your Name you@example.com")
    with pytest.raises(Exception, match="placeholder|real contact"):
        D.main()


def test_the_script_never_prints_or_writes_the_user_agent():
    text = open(D.__file__, encoding="utf-8").read()
    assert "print(ua" not in text and "ua)" not in text.replace("EdgarConfig(user_agent=ua", "")
    assert "user_agent_declared" in text and "cfg.user_agent" not in text


def test_percentile_helper():
    assert D.pct([], 0.5) is None and D.pct([1, 2, 3, 4, 10], 0.5) == 3 and D.pct([1, 2, 3, 4, 10], 1.0) == 10
