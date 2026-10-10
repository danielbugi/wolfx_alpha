"""The collector against a late, failed or partial pipeline, with the real writers in a disposable world and a simulated clock.

The collector never trusts the clock to mean "upstream is done". It resolves the session from the US calendar and then READS what exists: the session's own price bar
(the newest stored bar must be that session), the snapshots it wrote, the sector-history polls and, once capture is enabled, a COMPLETE candidate_capture_run.
Proven here, at the production schedule's own fire instants (04:00 first attempt, 08:15 recovery):
  * a pipeline that is late (prices loaded, screener/capture not finished) leaves the session INCOMPLETE at the first fire, observably (exit 2 = an alert state),
    and the recovery fire completes it WITHOUT duplicating a single row; a third run changes nothing;
  * a pipeline that never loaded the session is INCOMPLETE at both fires and writes nothing; once the data arrives a later fire collects it;
  * a capture run that did not finish (status running/partial/failed) is never COMPLETE, whatever the clock says;
  * capture is required only from the activation boundary: sessions before it are not failures, sessions on/after it without a complete run are;
  * a session is attributed to its own bar: once a later bar is the newest, the earlier session is never stamped as observed."""
from datetime import timedelta

import pytest

import forward_world as FW
from forward_collection import contract as C
from forward_collection import orchestrator as O
from forward_collection import steps as S

K = 10
TABLES = ("universe_snapshot", "market_snapshot", "sector_snapshot", "stock_relative_strength", "forward_return_label", "candidate_observation", "feature_snapshot")


@pytest.fixture
def world(monkeypatch):
    FW.scale_universe_minimums(monkeypatch)
    yield from FW.make_world(n_days=16, n_stocks=FW.SMALL_UNIVERSE)


def first_fire(w, k):
    return C.fire_instants_utc(w.sessions[k])[0] + timedelta(minutes=1)


def recovery_fire(w, k):
    return C.fire_instants_utc(w.sessions[k])[1] + timedelta(minutes=1)


def run_at(w, k, steps, at):
    w.now = at
    return O.run_session(w.connect, w.sessions[k], steps, apply=True, grace_days=1, code_ref="t", clock=w.clock)


def counts(w):
    return {t: w.count(t) for t in TABLES}


def outcomes(rep):
    return {s.name: s.outcome for s in rep.steps}


def finish_capture(w, k, strat):
    """The screener's capture hook finishing (late): the real hook, then the run stamped as finished at a simulated instant after the first fire."""
    w.now = first_fire(w, k) + timedelta(minutes=20)
    w.capture(k, K, strat)
    w.settle_capture_run(w.sessions[k], w.now)


# ---------------------------------------------------------------------------------------------------------- late pipeline
def test_a_late_pipeline_is_incomplete_at_the_first_fire_and_the_recovery_fire_completes_it_with_no_duplicates(world):
    strat, steps = FW.start_world(world)
    world.load_session(0)                                               # step 1 of the pipeline (prices) is done; the screener has not captured yet
    r1 = run_at(world, 0, steps, first_fire(world, 0))
    assert r1.verdict == C.INCOMPLETE and C.STEP_CAPTURE in r1.missing and r1.exit_code() == C.EXIT_INCOMPLETE and r1.exit_code() in C.ALERT_EXIT_CODES
    assert outcomes(r1)[C.STEP_OBSERVE] in C.SUCCESS                    # the market/sector/RS write itself is fine and stays valid
    after_first = counts(world)
    assert after_first["market_snapshot"] == 1 and after_first["candidate_observation"] == 0

    finish_capture(world, 0, strat)                                     # the pipeline finishes late
    r2 = run_at(world, 0, steps, recovery_fire(world, 0))
    assert r2.verdict == C.COMPLETE and r2.exit_code() == C.EXIT_COMPLETE, r2.to_dict()
    after_second = counts(world)
    for t in ("universe_snapshot", "market_snapshot", "sector_snapshot", "stock_relative_strength"):
        assert after_second[t] == after_first[t], t                     # nothing the first attempt wrote was written again
    assert after_second["candidate_observation"] == K

    r3 = run_at(world, 0, steps, recovery_fire(world, 0) + timedelta(hours=1))
    assert r3.verdict == C.COMPLETE and counts(world) == after_second                      # an idempotent re-check
    assert all(o in (C.ALREADY, C.OK) for o in outcomes(r3).values())


def test_the_first_attempt_never_reports_complete_just_because_it_fired_late_enough(world):
    """Clock alone proves nothing: even many hours after the fire, a missing capture keeps the session INCOMPLETE."""
    strat, steps = FW.start_world(world)
    world.load_session(0)
    for hours in (0, 1, 3, 8):
        rep = run_at(world, 0, steps, first_fire(world, 0) + timedelta(hours=hours))
        assert rep.verdict == C.INCOMPLETE, hours


# ---------------------------------------------------------------------------------------------------------- failed pipeline
def test_a_pipeline_that_never_loaded_the_session_is_incomplete_at_both_fires_writes_nothing_and_a_later_load_is_collected(world):
    strat, steps = FW.start_world(world)
    before = counts(world)
    for at in (first_fire(world, 0), recovery_fire(world, 0)):
        rep = run_at(world, 0, steps, at)
        assert rep.verdict == C.INCOMPLETE and rep.exit_code() in C.ALERT_EXIT_CODES
        assert outcomes(rep)[C.STEP_OBSERVE] == C.FAILED
    assert counts(world) == before                                      # a refusal to observe leaves no partial rows
    world.load_session(0)                                               # the 05:00 price safety net (or the next pipeline attempt) loads it
    finish_capture(world, 0, strat)
    assert run_at(world, 0, steps, recovery_fire(world, 0) + timedelta(hours=2)).verdict == C.COMPLETE


# ---------------------------------------------------------------------------------------------------------- partial capture
@pytest.mark.parametrize("state", ["running", "failed"])
def test_a_capture_run_that_did_not_finish_is_never_complete_and_the_finished_run_is(world, state):
    strat, steps = FW.start_world(world)
    world.load_session(0)
    finish_capture(world, 0, strat)
    with world.connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT tgname FROM pg_trigger WHERE tgrelid = 'candidate_capture_run'::regclass AND NOT tgisinternal")
        triggers = [r[0] for r in cur.fetchall()]
        cur.execute("ALTER TABLE candidate_capture_run DISABLE TRIGGER USER")           # test-only: forge the unfinished state in the disposable schema
        if state == "running":
            cur.execute("UPDATE candidate_capture_run SET status = 'running', run_finished_at = NULL WHERE session_date = %s", (world.sessions[0],))
        else:
            cur.execute("UPDATE candidate_capture_run SET status = 'failed', error = 'simulated' WHERE session_date = %s", (world.sessions[0],))
        for name in triggers:
            cur.execute(f'ALTER TABLE candidate_capture_run ENABLE ALWAYS TRIGGER "{name}"')
        conn.commit()
    rep = run_at(world, 0, steps, first_fire(world, 0))
    assert rep.verdict == C.INCOMPLETE and C.STEP_CAPTURE in rep.missing, rep.to_dict()
    cap = next(s for s in rep.steps if s.name == C.STEP_CAPTURE)
    assert cap.detail["reason"] == "capture_not_complete" and cap.detail["applicable"] is True


# ---------------------------------------------------------------------------------------------------------- the boundary
def test_capture_is_required_only_from_the_activation_boundary_and_not_after_a_disable(world):
    strat = world.strategy_ref()
    steps = S.make_steps(feature_set_version="mi_v2", with_capture=True)
    world.now = world.at(world.sessions[0], 6)
    world.activate_capture(strat.id, world.sessions[2])                                    # enabled from the third session
    for k in (0, 1):
        world.load_session(k)
        rep = run_at(world, k, steps, first_fire(world, k))
        assert rep.verdict == C.COMPLETE, (k, rep.to_dict())                                 # before the boundary a missing capture is not a failure
        cap = next(s for s in rep.steps if s.name == C.STEP_CAPTURE)
        assert cap.outcome == C.ALREADY and cap.detail == {"applicable": False, "reason": "capture_not_enabled_for_session",
                                                            "meaning": cap.detail["meaning"]}
    world.load_session(2)
    assert run_at(world, 2, steps, first_fire(world, 2)).verdict == C.INCOMPLETE             # on the boundary a missing capture IS a failure
    finish_capture(world, 2, strat)
    assert run_at(world, 2, steps, recovery_fire(world, 2)).verdict == C.COMPLETE
    world.now = world.at(world.sessions[2], 22)
    with world.connect() as conn:
        conn.cursor().execute("SELECT research_capture_set_state(%s, 'disabled', %s, 'forward-collection test disable')", (strat.id, world.sessions[4]))
        conn.commit()
    for k in (3, 4):
        world.load_session(k)
        if k == 3:
            finish_capture(world, k, strat)
        rep = run_at(world, k, steps, first_fire(world, k))
        assert rep.verdict == C.COMPLETE, (k, rep.to_dict())                                 # session 3 captured; session 4 is after the disable boundary


# ---------------------------------------------------------------------------------------------------------- identity of the session
def test_an_earlier_session_is_never_stamped_as_observed_once_a_later_bar_is_the_newest(world):
    strat, steps = FW.start_world(world)
    world.load_session(0)
    world.load_session(1)                                               # the next session's bars arrived before session 0 was collected
    rep = run_at(world, 0, steps, first_fire(world, 0))
    assert rep.verdict == C.MISSED and rep.exit_code() == C.EXIT_MISSED and rep.exit_code() in C.ALERT_EXIT_CODES
    assert outcomes(rep)[C.STEP_OBSERVE] == C.FAILED
    assert counts(world)["market_snapshot"] == 0 and counts(world)["stock_relative_strength"] == 0       # never attributed to the wrong session
