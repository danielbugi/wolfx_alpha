"""The collector against first-detection provenance of price discontinuities (real writers, disposable schema).

The nightly dataset rebuild used to re-stamp every discontinuity after every session, so the collector's `observe` step was refused for ANY session whose
lookback held one. With first-detection preserved, a discontinuity known to the overnight cycle that loaded the session no longer blocks the collection, while
retroactive information (first detected after the session's knowledge cutoff) and untrustworthy stamps still fail closed, and a later rebuild changes nothing."""
import os
import sys
from datetime import date, datetime, timedelta, timezone

import pytest

import forward_world as FW
from forward_collection import contract as C
from forward_collection import orchestrator as O
from market_intelligence import inputs

sys.path.insert(0, os.path.join(FW.ROOT, "ml_training", "config"))
from ml_training.data_preparation import build_dataset as bd  # noqa: E402

UTC = timezone.utc


@pytest.fixture
def world(monkeypatch):
    FW.scale_universe_minimums(monkeypatch)
    yield from FW.make_world(n_days=16, n_stocks=FW.SMALL_UNIVERSE)


def put(w, symbol, event, detected_utc, ratio=1.5):
    with w.connect() as c:
        c.cursor().execute(
            "INSERT INTO price_discontinuities (symbol, date, kind, prev_close, close, ratio, detected_at) "
            "VALUES (%s, %s, 'jump_up', 100, 150, %s, (%s::timestamptz AT TIME ZONE current_setting('TimeZone')))",
            (symbol, event, ratio, detected_utc))
        c.commit()


def rebuild(w, rows):
    """A nightly rebuild that re-detects exactly these rows (the real writer, not a hand-made UPDATE)."""
    with w.connect() as c:
        bd.upsert_discontinuities(c.cursor(), rows)
        c.commit()


def collect(w, steps, k):
    w.now = C.fire_instants_utc(w.sessions[k])[0] + timedelta(minutes=1)
    return O.run_session(w.connect, w.sessions[k], steps, apply=True, grace_days=1, code_ref="t", clock=w.clock)


def observe_outcome(rep):
    return next(s for s in rep.steps if s.name == C.STEP_OBSERVE)


def test_a_known_discontinuity_rebuilt_every_night_no_longer_blocks_the_collection(world):
    strat, steps = FW.start_world(world, with_capture=False)
    s = world.sessions[0]
    first_seen = inputs.knowledge_cutoff(s - timedelta(days=5)) - timedelta(hours=10)             # found in an earlier overnight cycle
    sym = "S0001"
    put(world, sym, s - timedelta(days=8), first_seen)
    for _ in range(3):                                                                              # the nightly rebuilds of the previous nights
        rebuild(world, [(sym, s - timedelta(days=8), "jump_up", 100.0, 150.0, 1.5)])
    world.load_session(0, scan=False)
    world.record_scan(0)                    # the builder's last step, after the discontinuity table is as it will be read
    rep = collect(world, steps, 0)
    assert observe_outcome(rep).outcome in C.SUCCESS, rep.to_dict()
    assert world.count("market_snapshot") == 1


def test_a_discontinuity_first_detected_after_the_session_cutoff_is_refused_and_writes_nothing(world):
    strat, steps = FW.start_world(world, with_capture=False)
    s = world.sessions[0]
    put(world, "S0001", s - timedelta(days=8), inputs.knowledge_cutoff(s) + timedelta(hours=1))
    world.load_session(0, scan=False)
    world.record_scan(0)                    # the builder's last step, after the discontinuity table is as it will be read
    rep = collect(world, steps, 0)
    o = observe_outcome(rep)
    assert o.outcome == C.FAILED and "detected after the session" in (o.error or ""), rep.to_dict()
    assert rep.verdict != C.COMPLETE and world.count("market_snapshot") == 0 and world.count("stock_relative_strength") == 0


def test_rebuilding_after_the_cutoff_cannot_launder_a_late_discontinuity_into_an_eligible_one(world):
    strat, steps = FW.start_world(world, with_capture=False)
    s = world.sessions[0]
    put(world, "S0001", s - timedelta(days=8), inputs.knowledge_cutoff(s) + timedelta(hours=1))
    for _ in range(3):
        rebuild(world, [("S0001", s - timedelta(days=8), "jump_up", 100.0, 150.0, 1.5)])
    world.load_session(0, scan=False)
    world.record_scan(0)                    # the builder's last step, after the discontinuity table is as it will be read
    assert observe_outcome(collect(world, steps, 0)).outcome == C.FAILED


def test_a_future_dated_stamp_fails_closed(world):
    strat, steps = FW.start_world(world, with_capture=False)
    put(world, "S0001", world.sessions[0] - timedelta(days=8), datetime.now(UTC) + timedelta(days=3))
    world.load_session(0, scan=False)
    world.record_scan(0)                    # the builder's last step, after the discontinuity table is as it will be read
    o = observe_outcome(collect(world, steps, 0))
    assert o.outcome == C.FAILED and "trustworthy detection time" in (o.error or "")
