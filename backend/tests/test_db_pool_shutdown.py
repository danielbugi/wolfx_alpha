"""App shutdown must close the connection pool without deadlocking.

ThreadedConnectionPool.closeall() holds the pool's (non-reentrant) lock while it calls close() on every connection;
_PooledConnection.close() used to hand the connection back with putconn(), which waits on that same lock -- so every
`uvicorn --reload` hung at "Waiting for application shutdown." and production shutdowns stalled until killed.
Needs a real Postgres (connections are psycopg2 objects); skipped when none is reachable."""
import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import main  # noqa: E402
from psycopg2.pool import ThreadedConnectionPool  # noqa: E402


def _pool():
    try:
        return ThreadedConnectionPool(
            1, 3,
            connection_factory=main._PooledConnection,
            host=os.getenv("DB_HOST", "localhost"),
            port=int(os.getenv("DB_PORT", 5432)),
            database=os.getenv("DB_NAME", "trading_production"),
            user=os.getenv("DB_USER", "trading_user"),
            password=os.getenv("DB_PASSWORD"),
            connect_timeout=3,
        )
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Postgres not reachable: {type(e).__name__}")


def test_shutdown_closes_idle_and_in_use_connections_without_deadlock(monkeypatch):
    pool = _pool()
    monkeypatch.setattr(main, "db_pool", pool)
    monkeypatch.setattr(main, "_pool_shutting_down", False)  # restored after the test
    idle = pool.getconn()
    in_use = pool.getconn()
    idle.close()  # the app's normal close(): goes back to the pool
    assert not idle.closed and not pool.closed

    worker = threading.Thread(target=main._shutdown_db_pool, daemon=True)
    worker.start()
    worker.join(timeout=5)

    assert not worker.is_alive(), "pool shutdown deadlocked"
    assert pool.closed and idle.closed and in_use.closed


def test_normal_close_still_returns_connections_to_the_pool(monkeypatch):
    pool = _pool()
    monkeypatch.setattr(main, "db_pool", pool)
    monkeypatch.setattr(main, "_pool_shutting_down", False)
    conn = pool.getconn()
    conn.close()
    assert not conn.closed and pool.getconn() is conn  # reused, not reopened
    main._shutdown_db_pool()
    assert pool.closed
