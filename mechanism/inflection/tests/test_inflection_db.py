"""Inflection updater against a real PostgreSQL (skipped when none is reachable or migration 40 is absent): idempotent writes, restatement revisions, incremental selection,
dry run, point-in-time reproduction, per-symbol error isolation and the run log. Rows live under symbols ZZI* and are removed before and after each test."""
import os
import sys
from datetime import date, timedelta

import numpy as np
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))
sys.path.insert(0, ROOT)

from inflection import config as C  # noqa: E402

D = date.fromisoformat
TARGET = "ZZI00"
PEERS = [f"ZZI{i:02d}" for i in range(1, 12)]
ALL = [TARGET] + PEERS
ENDS = ["2024-06-30", "2024-09-30", "2024-12-31", "2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31", "2026-03-31"]


def _db():
    try:
        from shared import db as _d
        _d.execute_dict_query("SELECT 1 FROM fundamental_inflection_event LIMIT 1")
        _d.execute_dict_query("SELECT 1 FROM quarterly_fundamentals LIMIT 1")
        _d.execute_dict_query("SELECT 1 FROM daily_fundamentals LIMIT 1")
        return _d
    except Exception as e:  # pragma: no cover
        pytest.skip(f"database with migration 40 not reachable: {e}")


def _clean(d):
    for t in ("fundamental_inflection_event", "fundamental_feature", "quarterly_fundamentals", "daily_fundamentals", "stock_prices", "earnings_calendar"):
        d.execute_insert(f"DELETE FROM {t} WHERE symbol LIKE 'ZZI%%'")
    d.execute_insert("DELETE FROM inflection_run_log WHERE method_version = %s AND note IS NOT DISTINCT FROM NULL AND asof BETWEEN '2026-04-01' AND '2026-12-31'", (C.METHOD_VERSION,))


@pytest.fixture
def dbx():
    d = _db()
    _clean(d)
    rev = [100, 100, 100, 100, 110, 120, 140, 170]
    oi = [10, 10, 10, 10, 12, 14, 20, 34]
    ni = [5, 5, 5, 5, -1, -1, 2, 9]
    for i, end in enumerate(ENDS):
        d.execute_insert("INSERT INTO quarterly_fundamentals (symbol, quarter, revenue, operating_income, net_income, operating_cash_flow, free_cash_flow, updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s, '2026-04-20')",
                         (TARGET, end, rev[i] * 1_000_000, oi[i] * 1_000_000, ni[i] * 1_000_000, 8_000_000, (4 if i < 4 else [-2, -2, 3, 8][i - 4]) * 1_000_000))
    d.execute_insert("INSERT INTO earnings_calendar (symbol, report_date, eps_actual) VALUES (%s, '2026-04-28', 1.0)", (TARGET,))
    days, x = [], D("2025-06-02")
    while len(days) < 330:
        if x.weekday() < 5:
            days.append(x)
        x += timedelta(days=1)
    rng = np.random.default_rng(4)
    for s in ALL:
        c = 50.0 * np.exp(np.cumsum(rng.normal(0.0002, 0.01, len(days))))
        for dt, cl in zip(days, c):
            d.execute_insert("INSERT INTO stock_prices (symbol, date, close) VALUES (%s,%s,%s)", (s, dt, round(float(cl), 4)))
        d.execute_insert("INSERT INTO daily_fundamentals (symbol, date, sector, ps_ratio, pe_ratio, market_cap) VALUES (%s,%s,'Technology',%s,20,1000000000)", (s, D("2026-04-28"), 3.0))
    yield d
    _clean(d)


def _u():
    from inflection.updater import InflectionUpdater
    import logging
    return InflectionUpdater(logger=logging.getLogger("t"))


def _n(d, table, where="symbol LIKE 'ZZI%%'"):
    return d.execute_dict_query(f"SELECT COUNT(*) n FROM {table} WHERE {where}")[0]["n"]


ASOF = D("2026-06-15")


def test_first_run_writes_features_and_explained_events_with_provenance(dbx):
    st = _u().run(asof=ASOF, symbols=[TARGET])
    assert st["processed"] == 1 and st["errors"] == 0 and st["events_new"] > 0
    assert _n(dbx, "fundamental_feature") == len(ENDS) - 0 or _n(dbx, "fundamental_feature") >= 7
    e = dbx.execute_dict_query("SELECT * FROM fundamental_inflection_event WHERE symbol=%s AND event_type=%s AND quarter_end='2026-03-31'", (TARGET, C.MARGIN_EXPANSION))[0]
    assert e["method_version"] == C.METHOD_VERSION and e["availability_basis"] == "earnings_calendar" and e["available_at"] == D("2026-04-28")
    assert e["explanation"] and e["components"] and e["event_hash"] and e["first_detected_at"] is not None and e["source"] == C.SOURCE_QUARTERLY
    assert e["context"]["valuation_at_publication"]["ps_ratio"] == 3.0 and "reaction" in e["context"]


def test_a_second_run_with_nothing_new_creates_nothing_and_rewrites_no_feature(dbx):
    _u().run(asof=ASOF, symbols=[TARGET])
    before = dbx.execute_dict_query("SELECT MAX(computed_at) m, COUNT(*) n FROM fundamental_feature WHERE symbol=%s", (TARGET,))[0]
    ev_before = _n(dbx, "fundamental_inflection_event")
    st = _u().run(asof=ASOF, symbols=[TARGET])
    assert st["events_new"] == 0 and st["events_revised"] == 0 and st["events_unchanged"] == ev_before
    after = dbx.execute_dict_query("SELECT MAX(computed_at) m, COUNT(*) n FROM fundamental_feature WHERE symbol=%s", (TARGET,))[0]
    assert before == after and _n(dbx, "fundamental_inflection_event") == ev_before


def test_a_restated_quarter_revises_the_event_in_place_and_keeps_its_first_detection_time(dbx):
    _u().run(asof=ASOF, symbols=[TARGET])
    q = "SELECT id, strength, metrics, revision_count, first_detected_at, event_hash FROM fundamental_inflection_event WHERE symbol=%s AND event_type=%s AND quarter_end='2026-03-31'"
    a = dbx.execute_dict_query(q, (TARGET, C.MARGIN_EXPANSION))[0]
    dbx.execute_insert("UPDATE quarterly_fundamentals SET operating_income = 38000000, updated_at = NOW() WHERE symbol=%s AND quarter='2026-03-31'", (TARGET,))
    st = _u().run(asof=ASOF, symbols=[TARGET])
    b = dbx.execute_dict_query(q, (TARGET, C.MARGIN_EXPANSION))[0]
    assert st["events_revised"] >= 1 and b["id"] == a["id"] and b["revision_count"] == a["revision_count"] + 1
    assert b["first_detected_at"] == a["first_detected_at"] and b["event_hash"] != a["event_hash"] and b["metrics"]["op_margin_yoy_bps"] > a["metrics"]["op_margin_yoy_bps"]


def test_incremental_selection_only_picks_symbols_with_new_fundamentals_or_recent_events(dbx):
    from inflection import store as S
    assert TARGET in S.due_symbols(ASOF)                                           # never computed
    _u().run(asof=ASOF, symbols=[TARGET])
    assert TARGET in S.due_symbols(ASOF)                                           # its events are recent: their context and windows still move
    assert TARGET not in S.due_symbols(ASOF + timedelta(days=400))                 # long after: nothing is pending
    dbx.execute_insert("UPDATE quarterly_fundamentals SET updated_at = NOW() WHERE symbol=%s AND quarter='2026-03-31'", (TARGET,))
    assert TARGET in S.due_symbols(ASOF + timedelta(days=400))                     # a newer fundamentals row brings it back


def test_dry_run_computes_but_writes_nothing(dbx):
    st = _u().run(asof=ASOF, symbols=[TARGET], dry_run=True)
    assert st["dry_run"] and st["events_new"] > 0
    assert _n(dbx, "fundamental_inflection_event") == 0 and _n(dbx, "fundamental_feature") == 0


def test_a_historical_asof_reproduces_what_was_known_that_day(dbx):
    _u().run(asof=D("2026-04-20"), symbols=[TARGET])                              # before the 2026-03-31 quarter was reported
    assert _n(dbx, "fundamental_inflection_event", f"symbol = '{TARGET}' AND quarter_end = '2026-03-31'") == 0
    assert _n(dbx, "fundamental_feature", f"symbol = '{TARGET}' AND quarter_end = '2026-03-31'") == 0
    _u().run(asof=ASOF, symbols=[TARGET])
    assert _n(dbx, "fundamental_inflection_event", f"symbol = '{TARGET}' AND quarter_end = '2026-03-31'") > 0


def test_one_bad_symbol_is_logged_and_does_not_stop_the_run(dbx, monkeypatch):
    from inflection import pipeline as P
    real = P.build_symbol

    def flaky(sym, *a, **k):
        if sym == "ZZI01":
            raise RuntimeError("boom")
        return real(sym, *a, **k)
    monkeypatch.setattr(P, "build_symbol", flaky)
    dbx.execute_insert("INSERT INTO quarterly_fundamentals (symbol, quarter, revenue, updated_at) VALUES ('ZZI01','2026-03-31',1,'2026-04-20')")
    st = _u().run(asof=ASOF, symbols=[TARGET, "ZZI01"])
    assert st["errors"] == 1 and st["processed"] == 1 and _n(dbx, "fundamental_inflection_event", f"symbol = '{TARGET}'") > 0


def test_every_run_is_logged_with_its_counts(dbx):
    _u().run(asof=ASOF, symbols=[TARGET])
    r = dbx.execute_dict_query("SELECT * FROM inflection_run_log ORDER BY id DESC LIMIT 1")[0]
    assert r["status"] == "ok" and r["symbols_processed"] == 1 and r["events_new"] > 0 and r["finished_at"] is not None and r["method_version"] == C.METHOD_VERSION
    assert _u().audit()["events"] >= r["events_new"]


def test_the_latest_stored_sector_label_wins_whatever_the_physical_row_order(dbx):
    """Regression (found in production 2026-10-31): an unordered daily_fundamentals scan let a relabelled symbol's sector flip between runs and revised borderline composite events."""
    from inflection import store as S
    peer = PEERS[0]
    dbx.execute_insert("INSERT INTO daily_fundamentals (symbol, date, sector, ps_ratio, pe_ratio, market_cap) VALUES (%s,%s,'Energy',3.0,20,1000000000)", (peer, D("2026-04-20")))     # OLDER, different label
    dbx.execute_insert("UPDATE daily_fundamentals SET ps_ratio = 3.1 WHERE symbol = %s AND date = %s", (peer, D("2026-04-20")))                                                      # moves the old row physically after the newer one
    for _ in range(3):
        assert S.load_inputs(ASOF, [TARGET]).sector_of[peer] == "Technology"
