"""signal_ledger_writer.write_todays_signals() against real Postgres (skips if unreachable -- the
thing under test IS the idempotent ON CONFLICT write, which cannot be faithfully exercised against
a fake). No network. Matches mechanism/alerts/tests/test_post_delivery.py's conventions."""
import os
import sys
import uuid
from datetime import date, timedelta

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))
sys.path.insert(0, ROOT)

from screeners.signal_ledger_writer import write_todays_signals  # noqa: E402

SESSION = date(2099, 1, 15)  # synthetic, clearly-in-the-future date -- can't collide with real rows


def _db():
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(ROOT, ".env"))
        from shared import db
        # shared.database builds its singleton pool once, at import; a single transient connect failure (seen: Postgres
        # 'out of memory' under host memory pressure) leaves sync_pool None for the whole process, so retry once here
        # instead of letting every DB-backed test in the run skip.
        if db.sync_pool is None:
            db.initialize_sync_pool()
        db.execute_dict_query("SELECT 1 FROM signal_ledger LIMIT 1")
        return db
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Postgres unreachable or signal_ledger is missing: {type(e).__name__}")


@pytest.fixture
def db():
    d = _db()
    yield d
    d.execute_insert("DELETE FROM signal_ledger WHERE symbol LIKE 'ZT%'")


def _symbol():
    # symbol is VARCHAR(10), same as real tickers -- keep the fake prefix short enough to fit.
    return "ZT" + uuid.uuid4().hex[:6].upper()


def _signal(symbol, signal_type="bullish_breakout", **overrides):
    base = {
        "symbol": symbol, "signal_type": signal_type, "screening_date": SESSION,
        "current_price": 100.0, "atr_14": 2.0, "sector": "Technology", "quality_grade": "B",
    }
    base.update(overrides)
    return base


def test_bullish_breakout_writes_a_row_with_correct_levels(db):
    symbol = _symbol()
    write_todays_signals(db, [_signal(symbol)], SESSION)
    rows = db.execute_dict_query("SELECT * FROM signal_ledger WHERE symbol = %s", (symbol,))
    assert len(rows) == 1
    r = rows[0]
    assert r["direction"] == 1 and r["status"] == "open"
    assert float(r["entry_price"]) == 100.0
    assert float(r["stop_price"]) == 96.0       # 100 - 2*2
    assert float(r["target1_price"]) == 104.0   # 100 + 2*2
    assert float(r["target2_price"]) == 108.0   # 100 + 4*2
    assert float(r["target3_price"]) == 112.0   # 100 + 6*2
    assert r["sector"] == "Technology" and r["quality_grade"] == "B"
    assert r["strategy_version"] == "v1" and r["strategy_id"] is not None
    assert r["model_version"] is None  # no ml_model_version on this signal


def _scored(**kw):
    return dict(ml_prediction_available=True, ml_momentum_probability=61.0, **kw)


def test_model_version_is_captured_when_a_model_scored_the_signal(db):
    symbol = _symbol()
    write_todays_signals(db, [_signal(symbol, **_scored(ml_model_version="momentum_predictor_v20260918_2157"))], SESSION)
    row = db.execute_dict_query("SELECT * FROM signal_ledger WHERE symbol = %s", (symbol,))[0]
    assert row["model_version"] == "momentum_predictor_v20260918_2157"


@pytest.mark.parametrize("extra", [
    dict(ml_model_version="unknown"),                                               # the legacy placeholder
    dict(ml_model_version="momentum_v1", ml_prediction_available=False),            # loaded model, signal not scored
    dict(ml_model_version="momentum_v1", ml_prediction_available=True, ml_momentum_probability=None),
    dict(ml_prediction_available=True, ml_momentum_probability=61.0),               # scored but no version reported
])
def test_model_version_is_null_unless_a_validated_model_scored_the_signal(db, extra):
    symbol = _symbol()
    write_todays_signals(db, [_signal(symbol, **extra)], SESSION)
    row = db.execute_dict_query("SELECT * FROM signal_ledger WHERE symbol = %s", (symbol,))[0]
    assert row["model_version"] is None


def test_second_signal_while_one_is_open_is_skipped_not_duplicated(db):
    """The position invariant (2026-09-28 correction): a symbol/strategy/direction can have at
    most one OPEN row at a time, regardless of which day each signal fired on -- three separate
    NVDA/donchian_breakout/LONG signals on three different days must not become three open rows."""
    symbol = _symbol()
    day1, day2, day3 = SESSION, SESSION + timedelta(days=1), SESSION + timedelta(days=2)
    written1 = write_todays_signals(db, [_signal(symbol, screening_date=day1)], day1)
    written2 = write_todays_signals(db, [_signal(symbol, current_price=105.0, screening_date=day2)], day2)
    written3 = write_todays_signals(db, [_signal(symbol, current_price=110.0, screening_date=day3)], day3)
    assert (written1, written2, written3) == (1, 0, 0)

    rows = db.execute_dict_query("SELECT * FROM signal_ledger WHERE symbol = %s", (symbol,))
    assert len(rows) == 1
    assert rows[0]["signal_date"] == day1 and float(rows[0]["entry_price"]) == 100.0


def test_a_new_signal_can_open_once_the_prior_one_resolves(db):
    symbol = _symbol()
    day1, day2 = SESSION, SESSION + timedelta(days=1)
    write_todays_signals(db, [_signal(symbol, screening_date=day1)], day1)
    db.execute_insert("UPDATE signal_ledger SET status = 'stopped' WHERE symbol = %s", (symbol,))

    written2 = write_todays_signals(db, [_signal(symbol, current_price=110.0, screening_date=day2)], day2)
    assert written2 == 1
    rows = db.execute_dict_query(
        "SELECT * FROM signal_ledger WHERE symbol = %s ORDER BY signal_date", (symbol,))
    assert len(rows) == 2
    assert rows[0]["status"] == "stopped" and rows[1]["status"] == "open"
    assert rows[1]["signal_date"] == day2


def test_bearish_breakout_flips_stop_and_targets(db):
    symbol = _symbol()
    write_todays_signals(db, [_signal(symbol, signal_type="bearish_breakout")], SESSION)
    row = db.execute_dict_query("SELECT * FROM signal_ledger WHERE symbol = %s", (symbol,))[0]
    assert row["direction"] == -1
    assert float(row["stop_price"]) == 104.0    # 100 + 2*2
    assert float(row["target1_price"]) == 96.0  # 100 - 2*2


def test_near_breakout_signals_are_never_written(db):
    symbol = _symbol()
    write_todays_signals(db, [_signal(symbol, signal_type="near_bullish")], SESSION)
    rows = db.execute_dict_query("SELECT * FROM signal_ledger WHERE symbol = %s", (symbol,))
    assert rows == []


def test_same_day_rerun_updates_the_row_not_duplicates_it(db):
    symbol = _symbol()
    write_todays_signals(db, [_signal(symbol, current_price=100.0)], SESSION)
    write_todays_signals(db, [_signal(symbol, current_price=105.0)], SESSION)  # re-run, price moved
    rows = db.execute_dict_query("SELECT * FROM signal_ledger WHERE symbol = %s", (symbol,))
    assert len(rows) == 1  # UNIQUE (symbol, signal_date, direction) -- never a second row
    assert float(rows[0]["entry_price"]) == 105.0  # same-day re-run still refreshes an untouched row


def test_once_evaluator_has_advanced_a_row_a_same_day_rerun_cannot_clobber_it(db):
    symbol = _symbol()
    write_todays_signals(db, [_signal(symbol, current_price=100.0)], SESSION)
    # Simulate evaluate_signal_ledger.py having advanced this row on a later session.
    db.execute_insert(
        "UPDATE signal_ledger SET last_evaluated_date = %s WHERE symbol = %s",
        (SESSION + timedelta(days=1), symbol))

    write_todays_signals(db, [_signal(symbol, current_price=999.0)], SESSION)  # a stray same-day re-run
    row = db.execute_dict_query("SELECT * FROM signal_ledger WHERE symbol = %s", (symbol,))[0]
    assert float(row["entry_price"]) == 100.0  # untouched -- the WHERE guard excluded the update


def test_a_bad_signal_is_skipped_without_raising_or_blocking_the_rest(db):
    good_symbol, bad_symbol = _symbol(), _symbol()
    signals = [_signal(bad_symbol, atr_14=0.0), _signal(good_symbol)]  # atr=0 -> compute_levels raises
    written = write_todays_signals(db, signals, SESSION)
    assert written == 1
    assert db.execute_dict_query("SELECT * FROM signal_ledger WHERE symbol = %s", (bad_symbol,)) == []
    assert len(db.execute_dict_query("SELECT * FROM signal_ledger WHERE symbol = %s", (good_symbol,))) == 1
