"""The Slice 7 acceptance test: from ZERO trustworthy history, letting the collector stack run session by session (through the real writers, on a
simulated clock) accumulates exactly the history the Slice 5 research-readiness contract asks for -- with no reconstruction and no backfill.

One ~260-session simulation is shared by every test in this module (module-scoped). The Slice 6 status is read through the real owner CLI at
explicit knowledge cutoffs; because every row carries its own availability stamp, the status at an EARLIER cutoff can be read from the final
database and equals what a live reader would have seen then -- that is how the "how many sessions are needed" scan is done.
"""
import os
from datetime import timedelta

import pytest

import forward_world as FW
from forward_collection import contract as C

TR, VA, TE, MAT, K = (0, 29), (90, 113), (174, 198), 261, 40       # windows in simulated-session indexes; maturity = test_end + 63; K candidates/session
N_SESSIONS = MAT + 1
MI_TABLES = ("universe_snapshot", "market_snapshot", "sector_snapshot", "stock_relative_strength")
ALL_TABLES = MI_TABLES + ("forward_return_label", "candidate_observation", "candidate_capture_run")


_CALLS = iter(range(10 ** 6))


def status_at(w, k, tmp):
    n = next(_CALLS)                                           # status never overwrites an artifact directory
    cutoff = w.at(w.sessions[k], 6, days_after=2)
    doc = FW.status_spec_doc(w, train=TR, validation=VA, test=TE, maturity=MAT, cutoff=cutoff)
    sp = FW.write_status_spec(w, tmp / f"s{n}_{k}", doc, last_session_index=MAT)
    rc, out, err = FW.read_status(w, sp, cutoff, tmp / f"o{n}_{k}")
    assert rc == 0 and out is not None, err
    return out


@pytest.fixture(scope="module")
def sim(tmp_path_factory):
    from forward_collection import steps as S
    tmp = tmp_path_factory.mktemp("conv")
    with pytest.MonkeyPatch.context() as mp:
        FW.scale_universe_minimums(mp)
        gen = FW.make_world(n_days=N_SESSIONS, n_stocks=FW.SMALL_UNIVERSE)
        w = next(gen)
        try:
            ev = {"pre": {t: w.count(t) for t in ALL_TABLES}, "verdicts": [], "mid": {}}
            strat, steps = FW.start_world(w)

            def on_report(k, rep):
                ev["verdicts"].append(rep.verdict)
                if k in (60, 120):
                    ev["mid"][k] = status_at(w, k, tmp)
            FW.run_forward_sessions(w, steps, range(N_SESSIONS), n_candidates=K, strategy=strat, on_report=on_report)
            ev.update(world=w, strat=strat, steps=steps, tmp=tmp)
            ev["final"] = status_at(w, MAT, tmp)
            yield ev
        finally:
            try:
                next(gen)
            except StopIteration:
                pass


def test_the_world_starts_with_no_trustworthy_history(sim):
    assert all(v == 0 for v in sim["pre"].values()), sim["pre"]


def test_every_simulated_session_was_collected_complete_by_the_scheduled_first_fire(sim):
    assert sim["verdicts"] == [C.COMPLETE] * N_SESSIONS


def test_history_accumulates_one_observed_session_at_a_time_with_no_reconstruction(sim):
    for k, doc in sim["mid"].items():
        assert doc["history"]["jointly_observed_sessions"] == k + 1
        assert all(not v for v in doc["history"]["reconstructed_rows_per_required_source"].values() if v is not None)
    assert sim["final"]["history"]["jointly_observed_sessions"] == N_SESSIONS
    w = sim["world"]
    for t in MI_TABLES:
        assert w.count(t, "provenance <> 'observed'") == 0, t
    assert w.count("market_snapshot") == N_SESSIONS and w.count("universe_snapshot") == N_SESSIONS


def test_early_in_the_history_the_contract_cannot_pass_and_says_why(sim):
    for k in (60, 120):
        c = sim["mid"][k]["contract"]
        assert c["data_checks_all_pass"] is False
        assert (c["evaluated"] is False and c["not_evaluated_reason"]) or sim["mid"][k]["readiness_failures"]


def test_with_the_full_history_every_data_derived_slice5_check_passes(sim):
    doc = sim["final"]
    c = doc["contract"]
    assert c["evaluated"] is True and c["data_checks_all_pass"] is True, doc["readiness_failures"]
    assert not [f for f in doc["readiness_failures"] if f["scope"] == "slice5_data_check"]
    assert doc["history"]["authoritative_earliest_trustworthy_pit_date"] is not None


def test_labels_exist_exactly_for_matured_horizons_and_never_before(sim):
    w = sim["world"]
    horizons = C.COLLECTOR_VERSIONS["label_horizons"]
    idx = {d: i for i, d in enumerate(w.sessions)}
    with w.connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT session_date FROM candidate_observation")
        obs = [idx[r[0]] for r in cur.fetchall()]
        cur.execute("SELECT count(*), count(*) FILTER (WHERE computed_as_of_session < horizon_session), "
                    "count(*) FILTER (WHERE computed_as_of_session < t0_session) FROM forward_return_label")
        n_labels, early, before_t0 = cur.fetchone()
        cur.execute("SELECT count(*) FROM (SELECT observation_id, horizon_sessions FROM forward_return_label GROUP BY 1, 2 HAVING count(*) > 1) d")
        dups = cur.fetchone()[0]
        conn.rollback()
    expected = sum(1 for i in obs for h in horizons if i + h <= MAT)
    assert (early, before_t0, dups) == (0, 0, 0)
    assert n_labels == expected and expected > 0


def test_scanning_the_cutoffs_shows_readiness_is_monotonic_and_first_passes_at_the_designed_floor(sim):
    w, tmp = sim["world"], sim["tmp"]
    ks = (150, 230, 255, 258, 259, 260, MAT)
    passed = {k: status_at(w, k, tmp)["contract"]["data_checks_all_pass"] for k in ks}
    flags = [passed[k] for k in ks]
    assert flags == sorted(flags), passed                      # once it passes it never regresses as history accumulates
    assert passed[150] is False and passed[MAT] is True
    first = min(k for k in ks if passed[k])
    assert first <= MAT and (first + 1) <= N_SESSIONS         # sessions needed = first + 1 <= 262: the intrinsic floor (embargo 60 + label horizon + windows)


def test_the_status_is_deterministic_and_a_rerun_of_the_whole_stack_changes_nothing(sim):
    w, steps = sim["world"], sim["steps"]
    from forward_collection import orchestrator as O
    before = {t: w.count(t) for t in ALL_TABLES}
    last = N_SESSIONS - 1
    w.now = w.at(w.sessions[last], 12, days_after=3)
    rep = O.run_session(w.connect, w.sessions[last], steps, apply=True, grace_days=1, code_ref="sim", clock=w.clock)
    assert rep.verdict == C.COMPLETE
    assert {t: w.count(t) for t in ALL_TABLES} == before
    again = status_at(w, MAT, sim["tmp"])
    assert again["status_hash"] == sim["final"]["status_hash"]
