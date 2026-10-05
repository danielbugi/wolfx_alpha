"""The no-sector policy, measured (not decided): the SAME full simulation, but a few symbols have no point-in-time sector.

The collector still observes every session completely (a symbol is never omitted from the relative-strength rows), yet the Slice 5 data checks do
NOT all pass: those symbols' cells carry sector_pit_safe = false, which Slice 5 check 6 counts. Until the owner chooses a policy (see
docs/research/LAB_SLICE7_COLLECTOR_READINESS.md) the convergence guarantee is conditional on "no candidate symbol lacks a sector".
"""
import pytest

import forward_world as FW
from forward_collection import contract as C
from forward_collection import preflight as P
from test_convergence_db import K, MAT, N_SESSIONS, status_at

NO_SECTOR = ("S0003", "S0004", "S0021")


@pytest.fixture(scope="module")
def sim(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("conv_ns")
    with pytest.MonkeyPatch.context() as mp:
        FW.scale_universe_minimums(mp)
        gen = FW.make_world(n_days=N_SESSIONS, n_stocks=FW.SMALL_UNIVERSE, no_sector=NO_SECTOR)
        w = next(gen)
        try:
            ev = {"verdicts": []}
            strat, steps = FW.start_world(w)
            FW.run_forward_sessions(w, steps, range(N_SESSIONS), n_candidates=K, strategy=strat,
                                    on_report=lambda k, rep: ev["verdicts"].append(rep.verdict))
            ev.update(world=w, tmp=tmp, final=status_at(w, MAT, tmp))
            yield ev
        finally:
            try:
                next(gen)
            except StopIteration:
                pass


def test_every_session_is_still_collected_complete_and_no_symbol_is_omitted(sim):
    w = sim["world"]
    assert sim["verdicts"] == [C.COMPLETE] * N_SESSIONS
    assert w.count("stock_relative_strength", "symbol = ANY(%s) AND state = 'ok' AND sector IS NULL AND NOT sector_pit_safe", (list(NO_SECTOR),)) > 0
    assert w.count("stock_relative_strength", "symbol = ANY(%s)", (list(NO_SECTOR),)) > 0


def test_the_slice5_data_checks_do_not_all_pass_and_the_failure_is_the_sector_one(sim):
    doc = sim["final"]
    assert doc["contract"]["data_checks_all_pass"] is False
    ids = {f["id"] for f in doc["readiness_failures"] if f["scope"] == "slice5_data_check"}
    assert "relative_strength_sector_pit_safe" in ids, ids
    print("NO-SECTOR failing data checks:", sorted(ids))


def test_the_preflight_stays_no_until_the_owner_decides(sim):
    w = sim["world"]
    spec = FW.status_spec_doc(w, train=(0, 1), validation=(2, 3), test=(4, 5), maturity=5, cutoff=w.at(w.sessions[5], 6, days_after=2))
    rep = P.run_preflight(w.connect, spec, env={})
    assert rep["collector_stack_ready_for_activation"] == "NO" and "no_sector_policy_resolved" in rep["blockers"]
    n = next(c for c in rep["checks"] if c["id"] == "no_sector_policy_resolved")["detail"]
    assert n["symbols_without_pit_safe_sector_now"] == len(NO_SECTOR)
