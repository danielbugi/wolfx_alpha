"""One throwaway schema per test MODULE for the runner tests: the real base tables (stock_prices, daily_fundamentals, market_index_prices,
price_discontinuities) plus migrations 24/25, seeded once with a deterministic synthetic market. The Market Intelligence tables are immutable
(no DELETE), so every test that writes uses its own `feature_set_version`. Skips only when Postgres is unreachable (the CI real-Postgres job
treats any skip as a failure). Imported EXPLICITLY (never a conftest.py: see mi_fixtures)."""
import os
import sys
import uuid
from contextlib import contextmanager
from datetime import date, datetime
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(ROOT, "ml_training", "config"))

MIGRATIONS = ["create_trading_schema.sql", "add_market_data_tables.sql", "add_ml_dataset_tables.sql",
              "add_market_snapshot_tables.sql", "add_market_event_tables.sql", "add_stock_relative_strength_table.sql",
              "add_price_discontinuity_scan.sql"]
T = date(2026, 9, 30)
EARLY = date(2026, 9, 25)          # a real session earlier than the newest one
N_DAYS, N_STOCKS = 260, 1100
SECTORS = ("Technology", "Energy", "Healthcare")
TINY = ("S1097", "S1098", "S1099")  # a 3-member sector: below the 5-member minimum


def sector_of(i):
    if f"S{i:04d}" in TINY:
        return "Tiny"
    return None if i % 17 == 0 else SECTORS[i % len(SECTORS)]


def synthetic_market(seed=7):
    rng = np.random.default_rng(seed)
    days = pd.bdate_range(end=pd.Timestamp(T), periods=N_DAYS)
    drift = rng.normal(0.0004, 0.002, N_STOCKS)
    steps = drift + rng.normal(0.0, 0.01, (N_DAYS, N_STOCKS))
    close = 100.0 * np.exp(np.cumsum(steps, axis=0))
    syms = [f"S{i:04d}" for i in range(N_STOCKS)]
    idx = {"^GSPC": 4000 * np.exp(np.cumsum(rng.normal(0.0004, 0.006, N_DAYS))),
           "^VIX": 15 + 3 * np.abs(rng.normal(0, 1, N_DAYS)),
           "^RUT": 2000 * np.exp(np.cumsum(rng.normal(0.0004, 0.008, N_DAYS)))}
    return days, syms, close, idx


def _args():
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
    return dict(host=os.environ["DB_HOST"], port=os.environ.get("DB_PORT", "5432"), dbname=os.environ["DB_NAME"],
                user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"])


def _seed(cur):
    from psycopg2.extras import execute_values
    days, syms, close, idx = synthetic_market()
    rows = []
    for j, s in enumerate(syms):
        for i, d in enumerate(days):
            c = round(float(close[i, j]), 4)
            rows.append((s, d.date(), c, round(c * 1.01, 4), round(c * 0.99, 4), c, 1000))      # open, high, low, close, volume
    execute_values(cur, "INSERT INTO stock_prices (symbol, date, open, high, low, close, volume) VALUES %s", rows, page_size=20000)
    execute_values(cur, "INSERT INTO market_index_prices (symbol, date, close) VALUES %s",
                   [(k, d.date(), round(float(v[i]), 4)) for k, v in idx.items() for i, d in enumerate(days)])
    fund = []
    for i, s in enumerate(syms):
        sec = sector_of(i)
        fund.append((s, date(2026, 9, 28), sec, datetime(2026, 9, 28, 3), datetime(2026, 9, 28, 3)))   # captured the day it is dated
        if i < 100:                                                                                    # bulk backfill: dated long ago, written later
            fund.append((s, date(2025, 12, 1), sec, datetime(2026, 9, 29, 3), datetime(2026, 9, 29, 3)))
        elif i < 150:                                                                                  # captured on its own date
            fund.append((s, date(2026, 9, 1), sec, datetime(2026, 9, 1, 3), datetime(2026, 9, 1, 3)))
    execute_values(cur, "INSERT INTO daily_fundamentals (symbol, date, sector, created_at, updated_at) VALUES %s", fund)


SCAN_TRIGGERS = ("price_discontinuity_scan_immutable_row", "price_discontinuity_scan_immutable_truncate", "price_discontinuity_scan_stamp")


def record_scan_on(cur, session, *, finished=None, run_id="test-scan"):
    """The nightly builder's last step, for real (build_dataset.finish_scan: the database recomputes both fingerprints and refuses a false claim). TESTS ONLY, in a
    disposable schema: the database stamps completion with ITS clock, which in a simulated past would be 'after the cutoff', so the new row's stamp is moved to
    `finished` (default: three hours before the session's knowledge cutoff, i.e. inside the overnight cycle) with the triggers briefly off and then ENABLE ALWAYS again."""
    from datetime import timedelta
    from market_intelligence import inputs
    from ml_training.data_preparation import build_dataset as bd
    cur.execute("SELECT clock_timestamp()")
    started = cur.fetchone()[0]
    cur.execute("SELECT newest_bar, n_symbols, n_price_rows, fingerprint FROM research_price_input_fingerprint(%s)", (session,))
    claim = cur.fetchone()
    scan_id = bd.finish_scan(cur, session, started, run_id, claim)
    if scan_id is not None:
        when = finished if finished is not None else inputs.knowledge_cutoff(session) - timedelta(hours=3)
        cur.execute("ALTER TABLE price_discontinuity_scan DISABLE TRIGGER USER")
        cur.execute("UPDATE price_discontinuity_scan SET finished_at = %s WHERE id = %s", (when, scan_id))
        for name in SCAN_TRIGGERS:
            cur.execute(f'ALTER TABLE price_discontinuity_scan ENABLE ALWAYS TRIGGER "{name}"')
    return scan_id


@pytest.fixture(scope="module")
def runner_env():
    import psycopg2
    try:
        args = _args()
        admin = psycopg2.connect(**args)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Postgres unreachable: {type(e).__name__}")
    schema = "mi_r_" + uuid.uuid4().hex[:10]
    try:
        cur = admin.cursor()
        cur.execute(f'CREATE SCHEMA "{schema}"')
        cur.execute(f'SET search_path TO "{schema}"')
        for name in MIGRATIONS:
            with open(os.path.join(ROOT, "mechanism", name), encoding="utf-8") as fh:
                cur.execute(fh.read())
        _seed(cur)
        record_scan_on(cur, T)      # the nightly builder's evidence for the newest session, completed inside its overnight cycle
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

    counter = {"n": 0}

    def fsv(prefix="t"):
        counter["n"] += 1
        return f"{prefix}{counter['n']}_{uuid.uuid4().hex[:6]}"

    try:
        yield SimpleNamespace(schema=schema, connect=connect, fsv=fsv)
    finally:
        try:
            admin.rollback()
            admin.cursor().execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            admin.commit()
        finally:
            admin.close()
