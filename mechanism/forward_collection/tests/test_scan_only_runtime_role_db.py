"""R2 under the REAL least-privilege runtime role: `build_dataset.py --scan-only` run as `donchian_app` against a production-shaped database (the compose init list, the real
role script). Proves the nightly job needs nothing the role does not already have: SELECT on prices, the price-discontinuity upserts and prune, TEMP tables, EXECUTE on the two
fingerprint functions, and INSERT (only) on the append-only scan table, whose immutability still holds for the same role."""
import sys
from types import SimpleNamespace

import psycopg2
import pytest

import forward_world  # noqa: F401  (puts mechanism/ and the repo root on sys.path)
import full_env as FE

from ml_training.data_preparation import build_dataset as bd  # noqa: E402


@pytest.fixture(scope="module")
def env():
    e, teardown = FE.make_env("so")
    try:
        e.load_market()
        yield e
    finally:
        teardown()


def run_as_app(env, monkeypatch, *argv):
    cfg = dict(env.db_args, user=env.app, password=FE.APP_PASSWORD)
    monkeypatch.setattr(bd, "ml_config", SimpleNamespace(db_config=cfg))
    monkeypatch.setattr(bd, "latest_completed_session", lambda: FE.ANCHOR)
    monkeypatch.setattr(sys, "argv", ["build_dataset.py", *argv])
    try:
        bd.main()
        return 0
    except SystemExit as e:
        return e.code


def admin_rows(env, sql):
    conn = env.admin()
    try:
        cur = conn.cursor()
        cur.execute(sql)
        return cur.fetchall()
    finally:
        conn.close()


def test_the_runtime_role_can_run_a_scan_only_scan_and_an_identical_retry_adds_nothing(env, monkeypatch):
    assert run_as_app(env, monkeypatch, "--scan-only", "--session", "latest-completed") == 0
    rows = admin_rows(env, "SELECT session_date, status, writer, code_ref, n_symbols FROM price_discontinuity_scan")
    assert len(rows) == 1 and rows[0][0] == FE.ANCHOR and rows[0][1] == "complete" and rows[0][2] == "build_dataset" and rows[0][3].endswith("#scan_only@v1") and rows[0][4] == 3
    assert run_as_app(env, monkeypatch, "--scan-only", "--session", FE.ANCHOR.isoformat()) == 0
    assert len(admin_rows(env, "SELECT 1 FROM price_discontinuity_scan")) == 1


def test_the_same_role_still_cannot_alter_or_delete_the_evidence_it_wrote(env):
    conn = psycopg2.connect(**dict(env.db_args, user=env.app, password=FE.APP_PASSWORD))
    try:
        cur = conn.cursor()
        for sql in ("UPDATE price_discontinuity_scan SET status = 'failed'", "DELETE FROM price_discontinuity_scan", "TRUNCATE price_discontinuity_scan"):
            with pytest.raises(psycopg2.Error):
                cur.execute(sql)
            conn.rollback()
    finally:
        conn.close()


def test_the_dataset_stays_untouched_when_the_runtime_role_scans(env, monkeypatch):
    before = admin_rows(env, "SELECT count(*) FROM ml_breakout_dataset_v2")
    assert run_as_app(env, monkeypatch, "--scan-only", "--session", "latest-completed") == 0
    assert admin_rows(env, "SELECT count(*) FROM ml_breakout_dataset_v2") == before == [(0,)]
