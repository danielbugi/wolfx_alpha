"""evaluate_signal_ledger._resolve() (pure, no DB) plus a real-Postgres integration test of run()'s
idempotency (skips if unreachable -- matches mechanism/alerts/tests/test_post_delivery.py's
convention: the atomic WHERE status='open' guard is the thing worth proving against a real table).
"""
import os
import sys
import uuid
from datetime import date, timedelta

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))
sys.path.insert(0, ROOT)

from screeners.evaluate_signal_ledger import _resolve, run  # noqa: E402

D = date(2099, 1, 15)


def _bar(offset, high, low, close):
    return (D + timedelta(days=offset), high, low, close)


# =================================================================== _resolve() -- pure logic
def test_target1_hit_before_stop_resolves_target1():
    bars = [_bar(1, 101, 99, 100), _bar(2, 105, 99, 104)]  # bar 2 crosses target1 (104)
    status, r, resolved_date, held, mae = _resolve(1, 100.0, 96.0, [104.0, 108.0, 112.0], bars)
    assert status == "target1" and r == 1.0 and held == 2 and resolved_date == D + timedelta(days=2)


def test_stop_hit_before_any_target_resolves_stopped():
    bars = [_bar(1, 101, 95, 96)]  # low breaches stop (96) on the very first bar
    status, r, resolved_date, held, mae = _resolve(1, 100.0, 96.0, [104.0, 108.0, 112.0], bars)
    assert status == "stopped" and r == -1.0 and held == 1


def test_same_bar_stop_and_target_the_stop_wins_the_tie():
    """A single huge-range bar that touches both the stop and target1 -- the shared convention
    (identical to ml_training's plan_outcomes()) says the stop is taken first."""
    bars = [_bar(1, 110, 90, 100)]  # high clears target1 (104) AND low breaches stop (96)
    status, r, _, _, _ = _resolve(1, 100.0, 96.0, [104.0, 108.0, 112.0], bars)
    assert status == "stopped" and r == -1.0


def test_one_bar_clearing_two_targets_resolves_the_higher_one():
    bars = [_bar(1, 109, 99, 108)]  # clears target1 (104) AND target2 (108) in one bar, no stop
    status, r, _, _, _ = _resolve(1, 100.0, 96.0, [104.0, 108.0, 112.0], bars)
    assert status == "target2" and r == 2.0


def test_bearish_direction_flips_the_comparisons():
    bars = [_bar(1, 101, 95, 96)]  # high clears the bearish target1 (96 is below entry 100)
    status, r, _, _, _ = _resolve(-1, 100.0, 104.0, [96.0, 92.0, 88.0], bars)
    assert status == "target1" and r == 1.0


def test_horizon_exhausted_with_nothing_touched_expires_at_mark_to_market():
    bars = [_bar(i, 101, 99, 100) for i in range(1, 21)]  # 20 bars, nothing ever touched
    bars[-1] = _bar(20, 101, 99, 102.0)  # final close = 102 -> mtm = (102-100)/4 = 0.5
    status, r, resolved_date, held, mae = _resolve(1, 100.0, 96.0, [104.0, 108.0, 112.0], bars)
    assert status == "expired" and r == pytest.approx(0.5) and held == 20


def test_fewer_than_horizon_bars_with_nothing_touched_stays_open():
    bars = [_bar(1, 101, 99, 100)]  # only 1 of 20 bars available so far
    status, r, resolved_date, held, mae = _resolve(1, 100.0, 96.0, [104.0, 108.0, 112.0], bars)
    assert status is None and r is None


def test_mae_r_tracks_the_worst_adverse_excursion_even_when_still_open():
    bars = [_bar(1, 101, 97, 98), _bar(2, 102, 100, 101)]  # dips to 97 (1R away is 96) then recovers
    status, r, _, _, mae = _resolve(1, 100.0, 96.0, [104.0, 108.0, 112.0], bars)
    assert status is None
    assert mae == pytest.approx((100.0 - 97.0) / 4.0)  # (entry-low)/R = 3/4 = 0.75


# =================================================================== run() -- real-Postgres idempotency
def _db():
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(ROOT, ".env"))
        from shared import db
        db.execute_dict_query("SELECT 1 FROM signal_ledger LIMIT 1")
        return db
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Postgres unreachable or signal_ledger is missing: {type(e).__name__}")


@pytest.fixture
def db():
    yield _db()


def _symbol():
    # symbol is VARCHAR(10), same as real tickers -- keep the fake prefix short enough to fit.
    return "ZT" + uuid.uuid4().hex[:6].upper()


def test_run_resolves_a_row_and_never_touches_it_again(db):
    symbol = _symbol()
    entry_date = D
    try:
        db.execute_insert(
            """INSERT INTO signal_ledger
               (symbol, signal_date, direction, entry_price, atr, stop_price,
                target1_price, target2_price, target3_price, last_evaluated_date,
                strategy_id, strategy_version)
               VALUES (%s, %s, 1, 100.0, 2.0, 96.0, 104.0, 108.0, 112.0, %s,
                (SELECT id FROM strategies WHERE strategy_key = 'donchian_breakout' AND strategy_version = 'v1'),
                'v1')""",
            (symbol, entry_date, entry_date))
        # One forward bar that clears target1 -- inserted into stock_prices under the same fake symbol.
        bar_date = entry_date + timedelta(days=1)
        db.execute_insert(
            """INSERT INTO stock_prices (symbol, date, open, high, low, close, volume)
               VALUES (%s, %s, 100, 105, 99, 104, 1000)""",
            (symbol, bar_date))

        run(bar_date)
        row = db.execute_dict_query("SELECT * FROM signal_ledger WHERE symbol = %s", (symbol,))[0]
        assert row["status"] == "target1"

        # A second run for the same (or a later) session must not re-touch the now-resolved row.
        db.execute_insert(
            "UPDATE signal_ledger SET outcome_r = -999 WHERE symbol = %s AND status = 'stopped'", (symbol,))
        run(bar_date)
        row2 = db.execute_dict_query("SELECT * FROM signal_ledger WHERE symbol = %s", (symbol,))[0]
        assert row2["status"] == "target1" and float(row2["outcome_r"]) == 1.0
    finally:
        db.execute_insert("DELETE FROM signal_ledger WHERE symbol = %s", (symbol,))
        db.execute_insert("DELETE FROM stock_prices WHERE symbol = %s", (symbol,))
