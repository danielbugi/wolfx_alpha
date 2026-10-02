"""send_channel_posts.load_market_intelligence against a real throwaway schema: exact session, observed only, None (never an error or an
invented payload) when the snapshot is absent or the storage is not provisioned, and a full build of the channel post from stored rows."""
from contextlib import contextmanager
from datetime import date

import mi_samples as S
from mi_fixtures import conn, connect, mi_env  # noqa: F401  (explicit fixtures: no top-level `conftest` name that could shadow research/tests)
import psycopg2
import psycopg2.errors
import pytest
from market_intelligence import store


def _scp():
    try:
        from alerts import send_channel_posts as scp
    except Exception as ex:  # noqa: BLE001  (no shared database module in this environment)
        pytest.skip(f"send_channel_posts needs the shared database module: {ex}")
    return scp


def _patch(monkeypatch, scp, conn):
    @contextmanager
    def fake_conn():
        yield conn
    monkeypatch.setattr(scp.db, "get_sync_connection", fake_conn)


def _write(conn, provenance="observed"):
    basis = "recomputed from stored prices" if provenance == "reconstructed" else None
    store.write_session(conn.cursor(), S.regime(), S.relative_strength(provenance), provenance, "unit-test", "test@abc", reconstruction_basis=basis)
    conn.commit()


def test_loads_the_observed_payload_for_exactly_that_session_and_builds_the_post(conn, monkeypatch):
    scp = _scp()
    _patch(monkeypatch, scp, conn)
    _write(conn)
    p = scp.load_market_intelligence(S.T)
    assert p["available"] and p["observed"] and p["session_date"] == S.T.isoformat() and len(p["sectors"]) == 3
    from alerts import channel_content as cx
    text = cx.post_market_environment(cx.Ctx(session=S.T, intel=p)).text
    assert "Market environment: Broadly positive" in text and "Leading:" in text
    assert scp.load_market_intelligence(date(2026, 9, 29)) is None                         # never "the latest": another session has no snapshot


def test_a_reconstructed_snapshot_is_never_loaded(conn, monkeypatch):
    scp = _scp()
    _patch(monkeypatch, scp, conn)
    _write(conn, "reconstructed")
    assert scp.load_market_intelligence(S.T) is None


def test_unprovisioned_storage_yields_none_not_an_error(conn, monkeypatch):
    scp = _scp()
    conn.cursor().execute("DROP TABLE sector_snapshot, market_snapshot, universe_snapshot CASCADE")
    conn.commit()
    _patch(monkeypatch, scp, conn)
    assert scp.load_market_intelligence(S.T) is None


def test_missing_privilege_yields_none(monkeypatch):
    scp = _scp()

    class Denied:
        def cursor(self, *a, **k):
            raise psycopg2.errors.InsufficientPrivilege("permission denied for table market_snapshot")

        def rollback(self):
            pass

    _patch(monkeypatch, scp, Denied())
    assert scp.load_market_intelligence(S.T) is None
