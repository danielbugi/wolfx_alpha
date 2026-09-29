"""evaluate_signal_ledger._resolve() (pure, no DB) plus real-Postgres integration tests of run()'s
idempotency, flag persistence and write guards (skip if unreachable -- matches
mechanism/alerts/tests/test_post_delivery.py's convention: the atomic WHERE-guards are the thing worth
proving against a real table).
"""
import os
import sys
import uuid
from datetime import date, timedelta

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))
sys.path.insert(0, ROOT)

from screeners.evaluate_signal_ledger import (  # noqa: E402
    HOLD_SQL, RESOLVE_SQL, SAME_BAR_STOP_AND_TARGET, SPLIT_SUSPECT, STILL_OPEN_SQL,
    _guarded_update, _resolve, clear_evaluation_flag, run,
)

D = date(2099, 1, 15)
LONG = (1, 100.0, 96.0, [104.0, 108.0, 112.0])     # entry 100, R = 4
SHORT = (-1, 100.0, 104.0, [96.0, 92.0, 88.0])


def _bar(offset, high, low, close):
    return (D + timedelta(days=offset), high, low, close)


# =================================================================== _resolve() -- pure logic
def test_target1_hit_before_stop_resolves_target1():
    bars = [_bar(1, 101, 99, 100), _bar(2, 105, 99, 104)]  # bar 2 crosses target1 (104)
    res = _resolve(*LONG, bars)
    assert res.status == "target1" and res.outcome_r == 1.0 and res.bars_held == 2
    assert res.resolved_date == D + timedelta(days=2)
    assert res.resolution_flag is None and res.evaluation_flag is None


def test_stop_hit_before_any_target_resolves_stopped():
    bars = [_bar(1, 101, 95, 96)]  # low breaches stop (96) on the very first bar
    res = _resolve(*LONG, bars)
    assert res.status == "stopped" and res.outcome_r == -1.0 and res.bars_held == 1
    assert res.resolution_flag is None and res.evaluation_flag is None  # unambiguous stop


def test_same_bar_stop_and_target_the_stop_wins_the_tie_and_is_flagged():
    """A single huge-range bar that touches both the stop and target1 -- the shared convention
    (identical to ml_training's plan_outcomes()) says the stop is taken first, and the row records
    that the ordering was a convention, not an observation."""
    bars = [_bar(1, 110, 90, 100)]  # high clears target1 (104) AND low breaches stop (96)
    res = _resolve(*LONG, bars)
    assert res.status == "stopped" and res.outcome_r == -1.0
    assert res.resolution_flag == SAME_BAR_STOP_AND_TARGET
    assert res.evaluation_flag is None


def test_bearish_same_bar_stop_and_target_is_flagged_too():
    bars = [_bar(1, 105, 95, 100)]  # high breaches bearish stop (104) AND low clears target1 (96)
    res = _resolve(*SHORT, bars)
    assert res.status == "stopped" and res.resolution_flag == SAME_BAR_STOP_AND_TARGET


def test_one_bar_clearing_two_targets_resolves_the_higher_one():
    bars = [_bar(1, 109, 99, 108)]  # clears target1 (104) AND target2 (108) in one bar, no stop
    res = _resolve(*LONG, bars)
    assert res.status == "target2" and res.outcome_r == 2.0 and res.resolution_flag is None


def test_bearish_direction_flips_the_comparisons():
    bars = [_bar(1, 101, 95, 96)]  # low clears the bearish target1 (96 is below entry 100)
    res = _resolve(*SHORT, bars)
    assert res.status == "target1" and res.outcome_r == 1.0 and res.resolution_flag is None


def test_bearish_stop():
    bars = [_bar(1, 104.5, 99, 104)]  # high breaches the bearish stop (104), no target touched
    res = _resolve(*SHORT, bars)
    assert res.status == "stopped" and res.outcome_r == -1.0 and res.resolution_flag is None


def test_horizon_exhausted_with_nothing_touched_expires_at_mark_to_market():
    bars = [_bar(i, 101, 99, 100) for i in range(1, 21)]  # 20 bars, nothing ever touched
    bars[-1] = _bar(20, 102.5, 99, 102.0)  # final close = 102 -> mtm = (102-100)/4 = 0.5
    res = _resolve(*LONG, bars)
    assert res.status == "expired" and res.outcome_r == pytest.approx(0.5) and res.bars_held == 20
    assert res.resolution_flag is None and res.evaluation_flag is None


def test_fewer_than_horizon_bars_with_nothing_touched_stays_open():
    bars = [_bar(1, 101, 99, 100)]  # only 1 of 20 bars available so far
    res = _resolve(*LONG, bars)
    assert res.status is None and res.outcome_r is None and res.evaluation_flag is None


def test_no_bars_at_all_stays_open_unflagged():
    res = _resolve(*LONG, [])
    assert res.status is None and res.evaluation_flag is None and res.mae_r == 0.0


def test_mae_r_tracks_the_worst_adverse_excursion_even_when_still_open():
    bars = [_bar(1, 101, 97, 98), _bar(2, 102, 100, 101)]  # dips to 97 (1R away is 96) then recovers
    res = _resolve(*LONG, bars)
    assert res.status is None
    assert res.mae_r == pytest.approx((100.0 - 97.0) / 4.0)  # (entry-low)/R = 3/4 = 0.75


# --------------------------------------------------------------- split-suspect (evaluation_flag)
def test_split_like_step_from_entry_holds_instead_of_fabricating_a_stop():
    """The whole series restated 2-for-1 under a static pre-split snapshot: bar 1 is ~half the entry
    close, so its low is far below the stored stop. Without the guard this would resolve 'stopped'."""
    bars = [_bar(1, 51, 49, 50)]
    res = _resolve(*LONG, bars)
    assert res.status is None and res.outcome_r is None
    assert res.evaluation_flag == SPLIT_SUSPECT and res.flag_date == D + timedelta(days=1)
    assert res.resolution_flag is None


def test_split_like_step_mid_window_holds_and_keeps_mae_from_the_trusted_bars_only():
    bars = [_bar(1, 101, 98, 100), _bar(2, 201, 199, 200)]  # 1-for-2 reverse-split shape on bar 2
    res = _resolve(*LONG, bars)
    assert res.status is None and res.evaluation_flag == SPLIT_SUSPECT
    assert res.flag_date == D + timedelta(days=2)
    assert res.mae_r == pytest.approx(0.5)  # only bar 1 counted: (100-98)/4


def test_bearish_split_like_step_holds():
    bars = [_bar(1, 34, 33, 33.4)]  # ~1/3 of entry: a 3-for-1 split shape, would fake a bearish target3
    res = _resolve(*SHORT, bars)
    assert res.status is None and res.evaluation_flag == SPLIT_SUSPECT


def test_resolution_before_a_later_split_step_stands():
    """Only the path up to the resolving bar matters: a target reached before a later jump is real."""
    bars = [_bar(1, 105, 99, 104), _bar(2, 53, 51, 52)]
    res = _resolve(*LONG, bars)
    assert res.status == "target1" and res.evaluation_flag is None


@pytest.mark.parametrize("close", [108.0, 92.0, 80.0, 125.0])
def test_ordinary_price_moves_are_not_flagged(close):
    """+8%, -8%, -20%, +25% closes: real moves, not split shapes (price_guard's own boundaries)."""
    bars = [_bar(1, max(close, 100) + 0.5, min(close, 100) - 0.5, close)]
    res = _resolve(*LONG, bars)
    assert res.evaluation_flag is None


# --------------------------------------------------------------- invalid bars (never an outcome)
@pytest.mark.parametrize("bar, reason_part", [
    ((101, 0, 100), "low is non-positive"),          # a zero low would otherwise be a false stop
    ((101, -5, 100), "low is non-positive"),
    ((101, 99, 0), "close is non-positive"),
    ((0, 99, 100), "high is non-positive"),
    ((None, 99, 100), "high is NULL"),
    ((101, None, 100), "low is NULL"),
    ((101, 99, None), "close is NULL"),
    ((float("nan"), 99, 100), "high is not finite"),
    ((101, float("inf"), 100), "low is not finite"),
    ((99, 101, 100), "low 101"),                     # low > high
    ((101, 99, 102), "outside"),                     # close above high
])
def test_invalid_bar_never_resolves_and_stays_open(bar, reason_part):
    res = _resolve(*LONG, [_bar(1, *bar)])
    assert res.status is None and res.outcome_r is None and res.resolved_date is None
    assert res.evaluation_flag is None and res.resolution_flag is None
    assert reason_part in res.invalid_reason and res.flag_date == D + timedelta(days=1)


def test_zero_low_cannot_fake_a_bullish_stop_even_alongside_a_real_target_touch():
    res = _resolve(*LONG, [_bar(1, 110, 0, 105)])
    assert res.status is None and res.invalid_reason is not None


def test_zero_low_cannot_fake_a_bearish_target3():
    res = _resolve(*SHORT, [_bar(1, 101, 0, 100)])
    assert res.status is None and res.invalid_reason is not None


def test_walk_stops_at_an_invalid_bar_instead_of_skipping_past_it():
    """Skipping the bad day could hide the day the stop was really hit, so a later valid stop bar
    must NOT resolve the row either."""
    bars = [_bar(1, 101, 99, 100), _bar(2, 101, 0, 100), _bar(3, 97, 95, 95.5)]
    res = _resolve(*LONG, bars)
    assert res.status is None and res.flag_date == D + timedelta(days=2)
    assert res.mae_r == pytest.approx(0.25)  # only bar 1 counted: (100-99)/4


def test_resolution_before_a_later_invalid_bar_stands():
    bars = [_bar(1, 105, 99, 104), _bar(2, 101, 0, 100)]
    res = _resolve(*LONG, bars)
    assert res.status == "target1" and res.invalid_reason is None


# =================================================================== run() -- real Postgres
def _db():
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(ROOT, ".env"))
        from shared import db
        db.execute_dict_query("SELECT resolution_flag, evaluation_flag FROM signal_ledger LIMIT 1")
        return db
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Postgres unreachable or signal_ledger lacks migration 21: {type(e).__name__}")


@pytest.fixture
def db():
    yield _db()


@pytest.fixture
def symbol(db):
    # symbol is VARCHAR(10), same as real tickers -- keep the fake prefix short enough to fit.
    sym = "ZT" + uuid.uuid4().hex[:6].upper()
    yield sym
    db.execute_insert("DELETE FROM signal_ledger WHERE symbol = %s", (sym,))
    db.execute_insert("DELETE FROM stock_prices WHERE symbol = %s", (sym,))


def _open_row(db, symbol, signal_date=D):
    db.execute_insert(
        """INSERT INTO signal_ledger
           (symbol, signal_date, direction, entry_price, atr, stop_price,
            target1_price, target2_price, target3_price, last_evaluated_date,
            strategy_id, strategy_version)
           VALUES (%s, %s, 1, 100.0, 2.0, 96.0, 104.0, 108.0, 112.0, %s,
            (SELECT id FROM strategies WHERE strategy_key = 'donchian_breakout' AND strategy_version = 'v1'),
            'v1')""",
        (symbol, signal_date, signal_date))
    return db.execute_dict_query("SELECT id FROM signal_ledger WHERE symbol = %s", (symbol,))[0]["id"]


def _price(db, symbol, day, high, low, close):
    db.execute_insert(
        """INSERT INTO stock_prices (symbol, date, open, high, low, close, volume)
           VALUES (%s, %s, %s, %s, %s, %s, 1000)""",
        (symbol, day, close, high, low, close))


def _row(db, symbol):
    return db.execute_dict_query("SELECT * FROM signal_ledger WHERE symbol = %s", (symbol,))[0]


def test_run_resolves_a_row_and_never_touches_it_again(db, symbol):
    _open_row(db, symbol)
    bar_date = D + timedelta(days=1)
    _price(db, symbol, bar_date, 105, 99, 104)  # clears target1

    run(bar_date)
    row = _row(db, symbol)
    assert row["status"] == "target1" and row["resolution_flag"] is None and row["evaluation_flag"] is None

    # A second run for the same (or a later) session must not re-touch the now-resolved row.
    db.execute_insert(
        "UPDATE signal_ledger SET outcome_r = -999 WHERE symbol = %s AND status = 'stopped'", (symbol,))
    run(bar_date)
    run(bar_date + timedelta(days=1))
    row2 = _row(db, symbol)
    assert row2["status"] == "target1" and float(row2["outcome_r"]) == 1.0


def test_run_persists_same_bar_resolution_flag(db, symbol):
    _open_row(db, symbol)
    bar_date = D + timedelta(days=1)
    _price(db, symbol, bar_date, 110, 90, 100)

    run(bar_date)
    row = _row(db, symbol)
    assert row["status"] == "stopped" and float(row["outcome_r"]) == -1.0
    assert row["resolution_flag"] == SAME_BAR_STOP_AND_TARGET and row["evaluation_flag"] is None


def test_run_with_no_new_bar_leaves_the_row_untouched(db, symbol):
    _open_row(db, symbol)
    run(D + timedelta(days=1))  # no stock_prices rows for this symbol at all
    row = _row(db, symbol)
    assert row["status"] == "open" and row["last_evaluated_date"] == D and row["evaluation_flag"] is None


@pytest.mark.parametrize("high, low, close", [(101, 0, 100), (None, 99, 100), (101, 99, 102)])
def test_run_invalid_bar_leaves_the_row_completely_unchanged(db, symbol, high, low, close):
    _open_row(db, symbol)
    before = _row(db, symbol)
    day1 = D + timedelta(days=1)
    db.execute_insert(
        """INSERT INTO stock_prices (symbol, date, open, high, low, close, volume)
           VALUES (%s, %s, 100, %s, %s, %s, 1000)""", (symbol, day1, high, low, close))

    counts = run(day1)
    after = _row(db, symbol)
    assert counts["invalid_blocked"] >= 1
    assert after == before  # no status, outcome, mae_r, last_evaluated_date or flag change
    run(day1)                # re-checked every run, still no mutation
    assert _row(db, symbol) == before


def test_split_suspect_is_held_once_then_skipped_until_cleared(db, symbol):
    ledger_id = _open_row(db, symbol)
    day1 = D + timedelta(days=1)
    _price(db, symbol, day1, 51, 49, 50)  # 2-for-1 shape vs the 100 entry close

    counts = run(day1)
    row = _row(db, symbol)
    assert row["status"] == "open" and row["evaluation_flag"] == SPLIT_SUSPECT
    assert row["outcome_r"] is None and row["resolution_flag"] is None
    assert row["last_evaluated_date"] == day1  # when the hold began
    assert counts["flagged"] >= 1 and counts["held"] >= 1

    # Later runs skip it entirely: no re-flag, no advance, no resolution, even with more bars.
    day2 = D + timedelta(days=2)
    _price(db, symbol, day2, 52, 40, 45)
    run(day2)
    row2 = _row(db, symbol)
    assert row2["status"] == "open" and row2["evaluation_flag"] == SPLIT_SUSPECT
    assert row2["last_evaluated_date"] == day1

    # The controlled path out: clears once, refuses a second time, and the next run re-evaluates
    # from signal_date -- re-holding it because the jump is still in the stored series.
    assert clear_evaluation_flag(ledger_id) is True
    assert _row(db, symbol)["evaluation_flag"] is None
    assert clear_evaluation_flag(ledger_id) is False
    run(day2)
    row3 = _row(db, symbol)
    assert row3["status"] == "open" and row3["evaluation_flag"] == SPLIT_SUSPECT and row3["last_evaluated_date"] == day2


def test_clear_refuses_a_resolved_row(db, symbol):
    ledger_id = _open_row(db, symbol)
    db.execute_insert("UPDATE signal_ledger SET status = 'stopped', outcome_r = -1 WHERE id = %s", (ledger_id,))
    assert clear_evaluation_flag(ledger_id) is False


def test_write_guards_refuse_rows_another_run_already_resolved_or_held(db, symbol):
    """The overlap case: a row selected as open, then resolved/held by a concurrent run before this
    run's UPDATE lands. Each guarded write must change nothing and report 0 rows."""
    ledger_id = _open_row(db, symbol)
    common = {"id": ledger_id, "mae_r": 0.0, "last_evaluated_date": D + timedelta(days=1)}
    resolve = {**common, "status": "target1", "outcome_r": 1.0, "resolved_date": D + timedelta(days=1),
               "bars_held": 1, "resolution_flag": None}

    # Held by "the other run" -> this run can neither resolve, advance, nor re-hold it.
    assert _guarded_update(HOLD_SQL, {**common, "evaluation_flag": SPLIT_SUSPECT}) == 1
    assert _guarded_update(RESOLVE_SQL, resolve) == 0
    assert _guarded_update(STILL_OPEN_SQL, common) == 0
    assert _guarded_update(HOLD_SQL, {**common, "evaluation_flag": SPLIT_SUSPECT}) == 0
    assert _row(db, symbol)["status"] == "open"

    # Resolved by "the other run" -> no second resolution, no hold on a closed row.
    db.execute_insert("UPDATE signal_ledger SET evaluation_flag = NULL WHERE id = %s", (ledger_id,))
    assert _guarded_update(RESOLVE_SQL, resolve) == 1
    assert _guarded_update(RESOLVE_SQL, {**resolve, "status": "stopped", "outcome_r": -1.0}) == 0
    assert _guarded_update(HOLD_SQL, {**common, "evaluation_flag": SPLIT_SUSPECT}) == 0
    row = _row(db, symbol)
    assert row["status"] == "target1" and row["evaluation_flag"] is None


def test_database_rejects_unknown_flag_values(db, symbol):
    import psycopg2
    ledger_id = _open_row(db, symbol)
    with pytest.raises(psycopg2.errors.CheckViolation):
        db.execute_insert("UPDATE signal_ledger SET evaluation_flag = 'whatever' WHERE id = %s", (ledger_id,))
    with pytest.raises(psycopg2.errors.CheckViolation):
        db.execute_insert("UPDATE signal_ledger SET resolution_flag = 'whatever' WHERE id = %s", (ledger_id,))
