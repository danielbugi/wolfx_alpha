"""The activation preflight: read-only, honest about the owner decision, never a silent YES."""
from contextlib import contextmanager
from datetime import timedelta

import pytest

import forward_world as FW
from forward_collection import contract as C
from forward_collection import preflight as P

TABLES = ("universe_snapshot", "market_snapshot", "sector_snapshot", "stock_relative_strength", "forward_return_label",
          "candidate_observation", "candidate_capture_run", "research_capture_activation")


@pytest.fixture
def world(monkeypatch):
    FW.scale_universe_minimums(monkeypatch)
    yield from FW.make_world(n_days=6, n_stocks=FW.SMALL_UNIVERSE)


def spec(w, **over):
    return FW.status_spec_doc(w, train=(0, 1), validation=(2, 3), test=(4, 5), maturity=5, cutoff=w.at(w.sessions[5], 6, days_after=2), **over)


def readonly(w):
    @contextmanager
    def connect():
        with w.connect() as conn:
            conn.set_session(readonly=True)
            yield conn
    return connect


def by_id(rep):
    return {c["id"]: c for c in rep["checks"]}


def test_preflight_is_read_only_and_reports_the_owner_decision_as_the_only_blocker(world):
    before = {t: world.count(t) for t in TABLES}
    rep = P.run_preflight(readonly(world), spec(world), env={})
    assert {t: world.count(t) for t in TABLES} == before
    assert rep["read_only"] is True and rep["schema"] == P.SCHEMA
    assert rep["ready_ignoring_owner_decision"] is True, [c for c in rep["checks"] if not c["ok"]]
    assert rep["collector_stack_ready_for_activation"] == "NO"
    assert rep["blockers"] == ["no_sector_policy_resolved"]
    assert by_id(rep)["no_sector_policy_resolved"]["owner_decision"] is True
    assert by_id(rep)["capture_state_reported"]["blocking"] is False


def test_it_becomes_yes_only_when_the_owner_decision_is_recorded(world, monkeypatch):
    monkeypatch.setattr(C, "NO_SECTOR_POLICY", "B_null_sector_relative")
    rep = P.run_preflight(readonly(world), spec(world), env={})
    assert rep["collector_stack_ready_for_activation"] == "YES" and rep["blockers"] == []


def test_an_enabled_catalyst_or_first_seen_source_blocks_activation(world, monkeypatch):
    monkeypatch.setattr(C, "NO_SECTOR_POLICY", "B_null_sector_relative")
    cat = {"catalyst": {"lookback_days": 5, "classifier": "c", "classifier_version": "1"}}
    rep = P.run_preflight(readonly(world), spec(world, config_over=cat), env={})
    assert rep["collector_stack_ready_for_activation"] == "NO" and "dataset_sources_have_collectors" in rep["blockers"]
    d = by_id(rep)["dataset_sources_have_collectors"]["detail"]
    assert [b["source"] for b in d["blockers"]] == ["catalyst"]


def test_a_missing_table_is_reported_not_crashed_on(world):
    with world.connect() as conn:
        conn.cursor().execute("DROP TABLE forward_return_label")
        conn.commit()
    rep = P.run_preflight(readonly(world), spec(world), env={})
    c = by_id(rep)
    assert not c["required_tables_exist"]["ok"] and "forward_return_label" in c["required_tables_exist"]["detail"]["missing"]
    assert rep["collector_stack_ready_for_activation"] == "NO"


def test_a_spec_whose_grace_days_are_shorter_than_the_scheduler_needs_is_a_blocker(world):
    rep = P.run_preflight(readonly(world), spec(world, grace_days=0), env={})
    assert "scheduler_design_consistent" in rep["blockers"]


def test_a_spec_pinning_other_versions_than_the_collector_writes_is_a_blocker(world):
    rep = P.run_preflight(readonly(world), spec(world, config_over={"market": {"feature_set_version": "mi_v9"}}), env={})
    assert "spec_versions_match_collectors" in rep["blockers"]


def test_capture_state_is_reported_never_changed(world):
    strat, _ = FW.start_world(world)
    rep = P.run_preflight(readonly(world), spec(world), env={"RESEARCH_CAPTURE_ENABLED": "1"})
    d = by_id(rep)["capture_state_reported"]["detail"]
    assert d["env_enabled"] is True and d["activation_rows"][0]["state"] == "enabled"


def test_an_empty_stock_price_table_means_the_calendar_is_not_derivable(world):
    with world.connect() as conn:
        conn.cursor().execute("DELETE FROM stock_prices")
        conn.commit()
    rep = P.run_preflight(readonly(world), spec(world), env={})
    assert not by_id(rep)["trading_calendar_derivable"]["ok"]
