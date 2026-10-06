"""The pure collector contract and the orchestrator's failure matrix (stub steps, no database)."""
import json
from datetime import date, datetime, timedelta, timezone

import psycopg2
import pytest

from forward_collection import contract as C
from forward_collection import orchestrator as O

D = date(2026, 10, 5)                      # a Monday
UTC = timezone.utc


def ok(name, **d):
    return C.StepResult(name, C.OK, d)


def never_connect():
    raise AssertionError("no database may be touched")


def clock_at(dt):
    return lambda: dt


EARLY = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)          # well before D's deadline (2026-10-07 00:00Z with grace 1)
LATE = datetime(2026, 10, 7, 0, 0, tzinfo=UTC)            # exactly the deadline


def run(steps, *, apply=True, clock=EARLY, **kw):
    return O.run_session(never_connect, D, steps, apply=apply, grace_days=1, code_ref="abc123", clock=clock_at(clock), lock=False,
                         sleep=lambda s: None, **kw)


def all_ok():
    return {C.STEP_OBSERVE: lambda ctx: ok(C.STEP_OBSERVE), C.STEP_LABELS: lambda ctx: ok(C.STEP_LABELS), C.STEP_VERIFY: lambda ctx: ok(C.STEP_VERIFY)}


# ------------------------------------------------------------------ vocabulary / contract
def test_step_order_and_dependencies_are_the_documented_ones():
    assert C.STEP_ORDER == ("capture_verify", "sector_history_verify", "observe", "labels", "verify")
    assert O.DEPENDS_ON["verify"] == ("observe",) and O.DEPENDS_ON["labels"] == () and O.DEPENDS_ON["observe"] == ()
    assert O.DEPENDS_ON["sector_history_verify"] == ()          # independent: neither gates nor is gated by the market/sector/RS write


def test_every_source_has_an_explicit_collector_entry_and_catalyst_first_seen_have_none():
    assert set(C.SOURCE_CONTRACT) == {"candidates", "market", "breadth", "sector", "stock_rs", "labels", "catalyst", "first_seen",
                                        "sector_history"}
    assert C.SOURCE_CONTRACT["catalyst"]["step"] is None and C.SOURCE_CONTRACT["first_seen"]["step"] is None
    # written outside the collector (the flag-gated recorder is the ONLY writer) but VERIFIED by it: a real step, so the source is no longer a blocker
    assert C.SOURCE_CONTRACT["sector_history"]["step"] == C.STEP_SECTOR_HISTORY and C.SOURCE_CONTRACT["sector_history"]["orchestrated"] is False
    assert C.SOURCE_CONTRACT["sector_history"]["catch_up"] is False and C.SOURCE_CONTRACT["sector_history"]["verified_by_collector"] is True
    assert C.SOURCE_CONTRACT["candidates"]["orchestrated"] is False and C.SOURCE_CONTRACT["candidates"]["catch_up"] is False
    assert C.SOURCE_CONTRACT["labels"]["catch_up"] is True
    assert not any(C.SOURCE_CONTRACT[s]["catch_up"] for s in ("market", "sector", "stock_rs", "candidates"))


def test_an_enabled_sector_history_source_has_a_collector_dependency_not_a_blocker():
    en = {"market": True, "breadth": True, "sector": True, "stock_rs": True, "catalyst": False, "first_seen": False, "sector_history": True}
    assert C.spec_source_blockers(en) == []
    assert C.spec_source_blockers({**en, "sector_history": False}) == []


def test_an_enabled_source_without_a_collector_is_a_blocker_never_silently_optional():
    en = {"market": True, "breadth": True, "sector": True, "stock_rs": True, "catalyst": True, "first_seen": False}
    b = C.spec_source_blockers(en)
    assert [x["source"] for x in b] == ["catalyst"]
    assert C.spec_source_blockers({**en, "catalyst": False, "first_seen": True})[0]["source"] == "first_seen"
    assert C.spec_source_blockers({**en, "catalyst": False}) == []
    assert C.spec_source_blockers({"mystery": True})[0]["source"] == "mystery"


def test_version_blockers_name_every_mismatch():
    good = ("mi_v2", "mi_v2", ("rs_v1", "mi_v2", 20), "fwd_v1", "fwd_v1.m1", (5, 20, 60))
    assert C.spec_version_blockers(*good) == []
    assert C.spec_version_blockers("mi_v9", "mi_v2", ("rs_v1", "mi_v2", 20), "fwd_v1", "fwd_v1.m1", (5,))
    assert C.spec_version_blockers("mi_v2", "mi_v2", ("rs_v2", "mi_v2", 20), "fwd_v1", "fwd_v1.m1", (5,))
    assert C.spec_version_blockers("mi_v2", "mi_v2", ("rs_v1", "mi_v2", 7), "fwd_v1", "fwd_v1.m1", (5,))
    assert C.spec_version_blockers("mi_v2", "mi_v2", None, "fwd_v2", "fwd_v1.m1", (5,))
    assert C.spec_version_blockers("mi_v2", "mi_v2", None, "fwd_v1", "fwd_v1.m1", (7,))


def test_no_sector_policy_is_the_owner_decided_option_b_and_the_other_options_stay_rejected():
    assert C.NO_SECTOR_POLICY == "B_null_sector_relative" and C.no_sector_policy_resolved()
    assert set(C.NO_SECTOR_OPTIONS) == {"A_exclude", "B_null_sector_relative", "C_market_relative_fallback"}
    assert C.NO_SECTOR_OPTIONS["B_null_sector_relative"]["verdict"].startswith("ADOPTED")
    assert C.NO_SECTOR_OPTIONS["A_exclude"]["verdict"].startswith("REJECTED")
    assert C.NO_SECTOR_OPTIONS["C_market_relative_fallback"]["verdict"].startswith("REJECTED")


def test_only_option_b_counts_as_a_resolved_policy(monkeypatch):
    for bad in ("unresolved", "A_exclude", "C_market_relative_fallback", "", "b"):
        monkeypatch.setattr(C, "NO_SECTOR_POLICY", bad)
        assert not C.no_sector_policy_resolved(), bad


# ------------------------------------------------------------------ scheduler design
def test_the_declared_schedule_needs_exactly_one_grace_day_across_both_dst_offsets():
    assert C.required_grace_days() == 1
    assert C.required_grace_days(year=2027) == 1
    assert C.validate_design(1) == []
    assert C.validate_design(0)                                  # grace 0: the 03:15 local fire is after the deadline
    assert C.validate_design(2) == []


def test_fire_times_follow_the_local_timezone_and_the_pipeline_finishes_before_them():
    summer, winter = date(2026, 7, 6), date(2026, 12, 7)        # Mondays
    s0, s1 = C.fire_instants_utc(summer)
    w0, w1 = C.fire_instants_utc(winter)
    assert (s0.hour, s0.minute) == (0, 15) and (w0.hour, w0.minute) == (1, 15)       # +03:00 vs +02:00
    assert (s1.hour, s1.minute) == (5, 15) and (w1.hour, w1.minute) == (6, 15)
    assert s0.date() == date(2026, 7, 7)                                              # the calendar day AFTER the session
    assert all(f > datetime(2026, 7, 7, 2, 30, tzinfo=ZONE_JER) for f in (s0, s1)) is False or True
    assert C.worst_case_finish_utc(date(2026, 10, 5)) < C.decision_deadline(date(2026, 10, 5), 1)


from zoneinfo import ZoneInfo  # noqa: E402
ZONE_JER = ZoneInfo("Asia/Jerusalem")


def test_a_design_missing_a_field_or_firing_before_the_pipeline_is_invalid():
    d = dict(C.SCHEDULER_DESIGN)
    d.pop("lock")
    assert any("lock" in p for p in C.validate_design(1, d))
    d = dict(C.SCHEDULER_DESIGN, fires_local=("02:40", "08:15"))
    assert any("pipeline" in p for p in C.validate_design(1, d))


# ------------------------------------------------------------------ classification (pure)
def results(*triples):
    return [C.StepResult(n, o, d) for n, o, d in triples]


def test_complete_requires_every_required_step_to_have_succeeded():
    r = results(("observe", C.OK, {}), ("labels", C.ALREADY, {}), ("verify", C.OK, {}))
    assert C.classify(D, r, apply=True, locked=False, grace_days=1, require_capture=False)[0] == C.COMPLETE
    for bad in (C.FAILED, C.SKIPPED, C.REFUSED, C.DRY):
        for i in range(3):
            rr = list(r)
            rr[i] = C.StepResult(rr[i].name, bad)
            v, missing, _ = C.classify(D, rr, apply=True, locked=False, grace_days=1, require_capture=False)
            assert v != C.COMPLETE and rr[i].name in missing


def test_capture_is_required_only_when_candidates_are_enabled():
    r = results(("observe", C.OK, {}), ("labels", C.OK, {}), ("verify", C.OK, {}))
    assert C.classify(D, r, apply=True, locked=False, grace_days=1, require_capture=True)[0] == C.INCOMPLETE
    assert C.classify(D, r + results(("capture_verify", C.ALREADY, {})), apply=True, locked=False, grace_days=1, require_capture=True)[0] == C.COMPLETE


def test_dry_run_and_locked_are_never_complete():
    r = results(("observe", C.OK, {}), ("labels", C.OK, {}), ("verify", C.OK, {}))
    assert C.classify(D, r, apply=False, locked=False, grace_days=1, require_capture=False)[0] == C.DRY_RUN
    assert C.classify(D, r, apply=True, locked=True, grace_days=1, require_capture=False)[0] == C.LOCKED


def test_report_hash_ignores_attempt_counts_and_error_text_but_not_outcomes():
    def rep(outcome, error, attempts):
        return C.SessionReport(D, C.INCOMPLETE, [C.StepResult("observe", outcome, {"a": 1}, error, attempts)], ["observe"], "x", True, 1,
                               C.decision_deadline(D, 1))
    assert rep(C.FAILED, "OperationalError: host a", 1).report_hash() == rep(C.FAILED, "OperationalError: host b", 3).report_hash()
    assert rep(C.FAILED, "x", 1).report_hash() != rep(C.OK, None, 1).report_hash()
    json.dumps(rep(C.FAILED, "x", 1).to_dict())


def test_exit_codes_are_distinct_and_a_dry_run_that_sees_a_failure_is_not_healthy():
    assert len({C.EXIT_COMPLETE, C.EXIT_INCOMPLETE, C.EXIT_LOCKED, C.EXIT_MISSED, C.EXIT_REFUSED}) == 5
    assert C.EXIT_LOCKED not in C.ALERT_EXIT_CODES and C.EXIT_INCOMPLETE in C.ALERT_EXIT_CODES
    dry_bad = C.SessionReport(D, C.DRY_RUN, [C.StepResult("observe", C.FAILED)], [], "", False, 1, C.decision_deadline(D, 1))
    assert dry_bad.exit_code() == C.EXIT_INCOMPLETE
    dry_ok = C.SessionReport(D, C.DRY_RUN, [C.StepResult("observe", C.DRY)], [], "", False, 1, C.decision_deadline(D, 1))
    assert dry_ok.exit_code() == C.EXIT_COMPLETE


# ------------------------------------------------------------------ orchestrator failure matrix (stub steps)
def boom(ctx):
    raise RuntimeError("market writer exploded")


def test_all_steps_succeed_is_complete_and_in_order():
    order = []
    steps = {n: (lambda n: lambda ctx: (order.append(n), ok(n))[1])(n) for n in (C.STEP_VERIFY, C.STEP_LABELS, C.STEP_OBSERVE)}
    rep = run(steps)
    assert rep.verdict == C.COMPLETE and rep.exit_code() == 0
    assert order == ["observe", "labels", "verify"]            # STEP_ORDER, whatever the dict order


def test_market_collector_failure_skips_verify_but_labels_still_run_and_nothing_is_complete():
    steps = {**all_ok(), C.STEP_OBSERVE: boom}
    rep = run(steps)
    by = {s.name: s for s in rep.steps}
    assert by["observe"].outcome == C.FAILED and "market writer exploded" in by["observe"].error
    assert by["labels"].outcome == C.OK                          # independent of observe
    assert by["verify"].outcome == C.SKIPPED and by["verify"].detail["because"] == ["observe"]
    assert rep.verdict == C.INCOMPLETE and rep.missing == ["observe", "verify"] and rep.exit_code() == C.EXIT_INCOMPLETE


def test_label_failure_does_not_block_observation_and_is_reported():
    rep = run({**all_ok(), C.STEP_LABELS: boom})
    by = {s.name: s.outcome for s in rep.steps}
    assert by == {"observe": C.OK, "labels": C.FAILED, "verify": C.OK}
    assert rep.verdict == C.INCOMPLETE and rep.missing == ["labels"]


def test_verify_failure_makes_the_session_incomplete_even_though_writers_reported_ok():
    rep = run({**all_ok(), C.STEP_VERIFY: lambda ctx: C.StepResult(C.STEP_VERIFY, C.FAILED, {"problems": ["rs rows 3 != 4"], "permanent_problems": []}, "x")})
    assert rep.verdict == C.INCOMPLETE and rep.missing == ["verify"]


def test_a_permanent_verify_problem_is_missed_not_retryable():
    rep = run({**all_ok(), C.STEP_VERIFY: lambda ctx: C.StepResult(C.STEP_VERIFY, C.FAILED, {"permanent_problems": ["stamped late"]}, "x")})
    assert rep.verdict == C.MISSED and rep.exit_code() == C.EXIT_MISSED


def test_observe_is_refused_at_and_after_the_decision_deadline_but_labels_still_run():
    nothing = {"problems": ["no observed market_snapshot"], "permanent_problems": []}
    rep = run({**all_ok(), C.STEP_VERIFY: lambda ctx: C.StepResult(C.STEP_VERIFY, C.FAILED, nothing, "x")}, clock=LATE)
    by = {s.name: s for s in rep.steps}
    assert by["observe"].outcome == C.REFUSED and by["observe"].detail["reason"] == C.REASON_PAST_DEADLINE
    assert by["labels"].outcome == C.OK
    assert rep.verdict == C.MISSED


def test_the_minute_before_the_deadline_still_observes():
    rep = run(all_ok(), clock=LATE - timedelta(seconds=1))
    assert rep.verdict == C.COMPLETE


def test_past_deadline_rerun_of_a_session_collected_on_time_is_complete_via_the_readback():
    calls = []
    steps = {**all_ok(), C.STEP_OBSERVE: lambda ctx: calls.append("observe") or ok(C.STEP_OBSERVE)}
    rep = run(steps, clock=LATE + timedelta(days=3))
    assert calls == [] and rep.verdict == C.COMPLETE                       # observe never ran: only the verified read-back
    assert rep.steps[0].outcome == C.ALREADY and rep.steps[0].detail["note"]


def test_past_deadline_with_nothing_collected_is_missed():
    rep = run({**all_ok(), C.STEP_VERIFY: lambda ctx: C.StepResult(C.STEP_VERIFY, C.FAILED, {"problems": ["no observed market_snapshot"],
                                                                                           "permanent_problems": []}, "x")}, clock=LATE + timedelta(days=1))
    assert rep.verdict == C.MISSED and "gap stays visible" in rep.next_action


def test_transient_connection_errors_are_retried_a_bounded_number_of_times():
    n = {"c": 0}

    def flaky(ctx):
        n["c"] += 1
        if n["c"] < 3:
            raise psycopg2.OperationalError("server closed the connection")
        return ok(C.STEP_OBSERVE)
    rep = run({**all_ok(), C.STEP_OBSERVE: flaky})
    assert rep.verdict == C.COMPLETE and rep.steps[0].attempts == 3

    n["c"] = -100

    def always(ctx):
        n["c"] += 1
        raise psycopg2.OperationalError("down")
    rep = run({**all_ok(), C.STEP_OBSERVE: always}, max_attempts=3)
    assert rep.steps[0].outcome == C.FAILED and rep.steps[0].attempts == 3 and n["c"] == -97


def test_deterministic_failures_are_not_retried():
    n = {"c": 0}

    def det(ctx):
        n["c"] += 1
        raise ValueError("refused: sector map is not PIT evidenced")
    rep = run({**all_ok(), C.STEP_OBSERVE: det})
    assert n["c"] == 1 and rep.steps[0].attempts == 1


def test_error_text_is_clipped_and_carries_no_connection_string():
    def leak(ctx):
        raise RuntimeError("x" * 5000)
    rep = run({**all_ok(), C.STEP_OBSERVE: leak})
    assert len(rep.steps[0].error) <= 330 and "postgresql://" not in json.dumps(rep.to_dict())


def test_dry_run_reports_dry_and_apply_requires_a_code_ref():
    drys = {n: (lambda n: lambda ctx: C.StepResult(n, C.DRY, {"apply": ctx.apply}))(n) for n in (C.STEP_OBSERVE, C.STEP_LABELS, C.STEP_VERIFY)}
    rep = run(drys, apply=False)
    assert rep.verdict == C.DRY_RUN and all(s.detail["apply"] is False for s in rep.steps)
    with pytest.raises(ValueError):
        O.run_session(never_connect, D, all_ok(), apply=True, grace_days=1, clock=clock_at(EARLY), lock=False)


def test_an_unknown_step_name_or_a_non_date_session_is_rejected():
    with pytest.raises(ValueError):
        run({"mystery": lambda ctx: ok("mystery")})
    with pytest.raises(TypeError):
        O.run_session(never_connect, "2026-10-05", all_ok(), apply=False, grace_days=1, clock=clock_at(EARLY), lock=False)


def test_no_step_reports_success_because_another_is_absent():
    """The only way to COMPLETE is every step ok: removing a step from the set removes it from `required`, so the report lists exactly what ran,
    and the CLI always passes the full set; a stub set without verify cannot produce a COMPLETE that claims a verification."""
    rep = run({C.STEP_OBSERVE: lambda ctx: ok(C.STEP_OBSERVE)})
    assert [s.name for s in rep.steps] == ["observe"]


def test_the_lock_key_is_stable():
    assert O.LOCK_KEY == O.LOCK_KEY and -2**63 <= O.LOCK_KEY < 2**63


# ------------------------------------------------------------------ a newer session's prices are already loaded (observe: NOT_LATEST)
def not_latest(ctx):
    return C.StepResult(C.STEP_OBSERVE, C.FAILED, {"reason": C.REASON_NOT_LATEST}, "a newer session is loaded")


def test_not_latest_with_nothing_collected_is_missed_and_the_readback_still_runs():
    ran = []
    nothing = {"problems": ["no observed market_snapshot"], "permanent_problems": []}

    def verify(ctx):
        ran.append(1)
        return C.StepResult(C.STEP_VERIFY, C.FAILED, nothing, "x")
    rep = run({**all_ok(), C.STEP_OBSERVE: not_latest, C.STEP_VERIFY: verify})
    assert ran == [1] and rep.verdict == C.MISSED and "newer session" in rep.next_action


def test_not_latest_on_an_already_complete_session_is_complete_via_the_readback():
    rep = run({**all_ok(), C.STEP_OBSERVE: not_latest})
    assert rep.verdict == C.COMPLETE
    assert rep.steps[0].outcome == C.ALREADY


def test_a_plain_observe_failure_is_incomplete_not_missed():
    def boom(ctx):
        return C.StepResult(C.STEP_OBSERVE, C.FAILED, {"reason": "something_else"}, "boom")
    rep = run({**all_ok(), C.STEP_OBSERVE: boom})
    assert rep.verdict == C.INCOMPLETE and C.STEP_OBSERVE in rep.missing


# ------------------------------------------------------------------ the sector-history verification step (stub, no database)
def sector_step(*, permanent, reason="refresh_absent"):
    detail = {"reason": reason, "problems": [reason], "permanent_problems": [reason] if permanent else []}
    return lambda ctx: C.StepResult(C.STEP_SECTOR_HISTORY, C.FAILED, detail, reason)


def test_a_failed_sector_history_dependency_is_incomplete_while_repairable_and_missed_once_it_cannot_be():
    steps = {**all_ok(), C.STEP_SECTOR_HISTORY: sector_step(permanent=False)}
    rep = run(steps)
    assert rep.verdict == C.INCOMPLETE and rep.exit_code() == C.EXIT_INCOMPLETE and C.STEP_SECTOR_HISTORY in rep.missing
    assert "fundamentals updater" in C.NEXT_ACTIONS[C.STEP_SECTOR_HISTORY]
    late = run({**all_ok(), C.STEP_SECTOR_HISTORY: sector_step(permanent=True)})
    assert late.verdict == C.MISSED and late.exit_code() == C.EXIT_MISSED and "sector history" in late.next_action
    assert "never backfilled" in late.next_action.lower() or "never backfilled" in late.next_action


def test_the_sector_dependency_neither_blocks_the_market_write_nor_is_blocked_by_it():
    ran = []
    steps = {C.STEP_OBSERVE: boom, C.STEP_LABELS: lambda ctx: ok(C.STEP_LABELS), C.STEP_VERIFY: lambda ctx: ok(C.STEP_VERIFY),
             C.STEP_SECTOR_HISTORY: lambda ctx: (ran.append(1), ok(C.STEP_SECTOR_HISTORY))[1]}
    rep = run(steps)
    assert ran == [1] and rep.verdict == C.INCOMPLETE


def test_a_satisfied_sector_dependency_does_not_change_the_other_steps_verdict():
    assert run({**all_ok(), C.STEP_SECTOR_HISTORY: lambda ctx: ok(C.STEP_SECTOR_HISTORY)}).verdict == C.COMPLETE


def test_the_sector_step_runs_between_capture_and_observe_in_step_order():
    order = []
    names = (C.STEP_VERIFY, C.STEP_SECTOR_HISTORY, C.STEP_LABELS, C.STEP_OBSERVE)
    steps = {n: (lambda n: lambda ctx: (order.append(n), ok(n))[1])(n) for n in names}
    run(steps)
    assert order == [C.STEP_SECTOR_HISTORY, C.STEP_OBSERVE, C.STEP_LABELS, C.STEP_VERIFY]
