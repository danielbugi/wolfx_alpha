"""A transiently failed import-time pool initialisation must not turn every DB-backed screener test into a silent skip."""
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))
sys.path.insert(0, ROOT)

import test_signal_ledger_writer as W  # noqa: E402


def test_a_dead_singleton_pool_is_reinitialised_instead_of_skipping():
    from shared import db
    try:
        db.execute_dict_query("SELECT 1")
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Postgres unreachable: {type(e).__name__}")
    healthy = db.sync_pool
    db.sync_pool = None
    try:
        assert W._db() is db and db.sync_pool is not None
    finally:
        if db.sync_pool is not healthy:
            db.sync_pool.closeall()
        db.sync_pool = healthy
