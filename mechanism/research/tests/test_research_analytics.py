"""The Release B research read model (strategy_analytics/research.py) against a real throwaway Postgres schema:
unavailable (no migration 22), no-data, complete / partial / failed / stuck / disabled capture, funnel, pagination,
filters, candidate + snapshot detail, null features, ledger lineage and multi-strategy isolation.
Skips only when Postgres is unreachable; the CI real-Postgres step fails on any skip."""
import json
import os
import sys
from datetime import date

import pytest
from psycopg2.extras import RealDictCursor

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))

from strategy_analytics import analytics, research  # noqa: E402

S1, S2, S3 = date(2099, 1, 13), date(2099, 1, 14), date(2099, 1, 15)


def fetcher(conn):
    def fetch(sql, params=None):
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]
    return fetch


@pytest.fixture
def fetch(conn):
    return fetcher(conn)


@pytest.fixture
def strategy(fetch):
    return analytics.get_strategy(fetch, "donchian_breakout", "v1")


def finish(seed, run_id, status="complete", **counters):
    base = dict(candidates=0, captured=0, already_captured=0, stale_skipped=0, snapshot_skipped=0, invalid_skipped=0,
                guard_rejected=0, guard_not_evaluated=0, hash_drift=0, snapshot_drift=0, defaulted_flagged=0)
    base.update(counters)
    sets = ", ".join(f"{k} = %({k})s" for k in base)
    seed.execute(f"UPDATE candidate_capture_run SET status = %(status)s, run_finished_at = run_started_at + "
                 f"interval '95 seconds', {sets} WHERE id = %(id)s", {**base, "status": status, "id": run_id})


def capture(seed, session, n=3, **counters):
    """A complete capture of `n` candidates: AAA/BBB bullish (BBB guard-rejected), CCC bearish near-breakout."""
    run = seed.run(session)
    specs = [("AAA", 1, dict(passed_guard=True, session_rank=1, alignment_score=90, quality_grade="A",
                             combined_score=88.5, breakout_dist_atr=1.2)),
             ("BBB", 1, dict(passed_guard=False, guard_reasons=["illiquid_dollar_volume"], tracked_intent=False,
                             alignment_score=70, quality_grade="B", breakout_dist_atr=0.4)),
             ("CCC", -1, dict(passed_guard=True, session_rank=2, alignment_score=60, quality_grade="C",
                              signal_type="near_bearish", triggered=False, tracked_intent=False,
                              strategy_context=json.dumps({"urgency": "watch", "donchian_low_20": 7.5})))]
    ids = {}
    for sym, d, kw in specs[:n]:
        snap = seed.snapshot(sym, session)
        ids[sym] = seed.observation(snapshot_id=snap, run_id=run, symbol=sym, session=session, direction=d, **kw)
    finish(seed, run, candidates=n, captured=n, guard_rejected=1 if n >= 2 else 0, **counters)
    return run, ids


# ------------------------------------------------------------------ unavailable: production today
def test_before_migration_22_everything_is_not_available_never_zero(pre22_env):
    _, connect = pre22_env
    with connect() as conn:
        f = fetcher(conn)
        s = analytics.get_strategy(f, "donchian_breakout", "v1")
        assert research.schema_state(f) == {t: False for t in research.RESEARCH_TABLES}
        summ = research.summary(f, s)
        assert summ["availability"]["state"] == "not_available" and summ["availability"]["release"] == "B"
        assert [x["state"] for x in summ["funnel"]["stages"]] == ["not_available"] * 6
        assert all(c["value"] is None and c["state"] == "not_available" for c in summ["cards"].values())
        assert all(r["value"] is None and r["state"] == "not_available" for r in summ["rates"].values())
        assert summ["forward_outcomes"]["state"] == "not_available"
        runs = research.capture_runs(f, s)
        assert runs["overall"]["status"] == "not_available" and runs["history"] == [] and runs["latest"] is None
        cands = research.list_candidates(f, s)
        assert cands["availability"]["state"] == "not_available" and cands["items"] == [] and cands["total"] is None
        with pytest.raises(research.ResearchUnavailable):
            research.get_candidate(f, s, 1)
        with pytest.raises(research.ResearchUnavailable):
            research.get_snapshot(f, s, 1)


# ------------------------------------------------------------------ no data: tables exist, capture never ran
def test_tables_without_a_run_is_no_data_and_disabled(fetch, strategy, seed):
    seed.registry()
    summ = research.summary(fetch, strategy)
    assert summ["availability"]["state"] == "no_data" and summ["availability"]["capture_runs"] == 0
    assert all(x["state"] == "no_data" and x["value"] is None for x in summ["funnel"]["stages"])
    assert [r["version"] for r in summ["feature_set"]["registered"]] == ["t0_v1"]
    assert summ["feature_set"]["current"] is None
    runs = research.capture_runs(fetch, strategy)
    assert runs["overall"]["status"] == "not_active" and runs["history"] == []
    assert runs["activation"]["state"] == "not_active" and runs["activation"]["active_from"] is None
    cands = research.list_candidates(fetch, strategy)
    assert cands["items"] == [] and cands["total"] is None and cands["availability"]["state"] == "no_data"


# ------------------------------------------------------------------ complete capture + funnel
def test_complete_capture_funnel_cards_and_rates(fetch, strategy, seed):
    capture(seed, S3)
    summ = research.summary(fetch, strategy)
    assert summ["availability"]["state"] == "ok"
    stages = {x["key"]: x for x in summ["funnel"]["stages"]}
    assert stages["evaluated"]["state"] == "not_available" and stages["evaluated"]["value"] is None  # never recorded
    assert (stages["candidates"]["value"], stages["guard_passed"]["value"], stages["ranked"]["value"],
            stages["selected"]["value"]) == (3, 2, 2, 1)
    assert stages["ledger_signals"]["value"] == 0 and stages["ledger_signals"]["state"] == "ok"
    assert summ["funnel"]["session_date"] == S3
    assert summ["latest_session"]["capture_status"] == "complete"
    assert summ["feature_set"]["current"] == "t0_v1"
    cards = summ["cards"]
    assert (cards["observations"]["value"], cards["snapshots"]["value"], cards["bullish"]["value"],
            cards["bearish"]["value"], cards["guard_rejected"]["value"]) == (3, 3, 2, 1, 1)
    assert cards["guard_not_evaluated"]["value"] == 0
    rates = summ["rates"]
    assert rates["guard_pass_rate"]["value"] == pytest.approx(2 / 3, abs=1e-4)
    assert rates["selected_rate"]["value"] == pytest.approx(1 / 3, abs=1e-4)
    assert rates["capture_coverage"]["value"] == 1 and rates["snapshot_coverage"]["value"] == 1
    assert rates["missing_feature_rate"]["value"] == 0       # measured: 0 of 3 slots missing, n=3 snapshots


def test_forward_outcomes_are_never_collected(fetch, strategy, seed):
    capture(seed, S3)
    fo = research.summary(fetch, strategy)["forward_outcomes"]
    assert fo["state"] == "not_available" and fo["value"] is None and "migration 23" in fo["reason"]


# ------------------------------------------------------------------ capture health classification (pure)
BASE = dict(status="complete", candidates=10, captured=10, already_captured=0, stale_skipped=0, snapshot_skipped=0,
            invalid_skipped=0, guard_not_evaluated=0, hash_drift=0, snapshot_drift=0, stuck=False, error=None)


def run_with(**kw):
    return {**BASE, **kw}


def test_classify_complete():
    h = research.classify_run(run_with())
    assert h["status"] == "complete" and h["reasons"] == []


@pytest.mark.parametrize("kw,fragment", [
    (dict(captured=8, snapshot_skipped=2), "no usable T0 snapshot"),
    (dict(captured=9, invalid_skipped=1), "unreadable"),
    (dict(captured=7), "unaccounted"),
    (dict(captured=11), "over-counted"),
    (dict(guard_not_evaluated=3), "guards were not evaluated"),
    (dict(captured=None), "never finalised"),
])
def test_classify_partial_is_never_healthy(kw, fragment):
    h = research.classify_run(run_with(**kw))
    assert h["status"] == "partial" and any(fragment in r for r in h["reasons"])


def test_classify_drift_and_stale_are_notes_not_failures():
    h = research.classify_run(run_with(captured=8, stale_skipped=2, hash_drift=1, snapshot_drift=1))
    assert h["status"] == "complete" and h["reasons"] == [] and len(h["notes"]) == 3


def test_classify_failed_running_and_stuck():
    f = research.classify_run(run_with(status="failed", error="boom"))
    assert f["status"] == "failed" and f["reasons"] == ["boom"]
    assert research.classify_run(run_with(status="running"))["status"] == "running"
    stuck = research.classify_run(run_with(status="running", stuck=True))
    assert stuck["status"] == "failed" and "still 'running'" in stuck["reasons"][0]


def test_classify_zero_candidates_is_complete_with_a_note():
    h = research.classify_run(run_with(candidates=0, captured=0))
    assert h["status"] == "complete" and h["notes"]


# ------------------------------------------------------------------ capture runs against real rows
def test_partial_run_is_partial_in_history_and_overall(fetch, strategy, seed):
    capture(seed, S1)
    run, _ = capture(seed, S2)
    seed.execute("UPDATE candidate_capture_run SET status = 'partial', candidates = 5, snapshot_skipped = 2, "
                 "skipped_symbols = %s::jsonb WHERE id = %s", (json.dumps({"ZZZ": "no_bar", "YYY": "no_bar"}), run))
    out = research.capture_runs(fetch, strategy)
    assert out["overall"]["status"] == "partial" and out["overall"]["session_date"] == S2
    assert [h["session_date"] for h in out["history"]] == [S2, S1]
    assert [h["health"]["status"] for h in out["history"]] == ["partial", "complete"]
    assert out["latest"]["skipped_symbols"] == {"ZZZ": "no_bar", "YYY": "no_bar"}
    assert out["latest"]["unaccounted"] == 0 and out["latest"]["counters"]["snapshot_skipped"] == 2


def test_failed_and_stuck_runs(fetch, strategy, seed):
    run = seed.run(S1)
    seed.execute("UPDATE candidate_capture_run SET status = 'failed', run_finished_at = NOW(), error = 'manifest mismatch' "
                 "WHERE id = %s", (run,))
    out = research.capture_runs(fetch, strategy)
    assert out["overall"]["status"] == "failed" and out["overall"]["reason"] == "manifest mismatch"
    run2 = seed.run(S2)
    assert research.capture_runs(fetch, strategy)["overall"]["status"] == "running"
    seed.execute("UPDATE candidate_capture_run SET run_started_at = NOW() - interval '2 hours' WHERE id = %s", (run2,))
    assert research.capture_runs(fetch, strategy)["overall"]["status"] == "failed"


def test_latest_attempt_per_session_wins_and_attempts_are_counted(fetch, strategy, seed):
    run = seed.run(S1)
    seed.execute("UPDATE candidate_capture_run SET status = 'failed', error = 'x', run_finished_at = NOW() WHERE id = %s", (run,))
    seed.execute("UPDATE candidate_capture_run SET run_started_at = NOW() - interval '1 hour' WHERE id = %s", (run,))
    capture(seed, S1)
    out = research.capture_runs(fetch, strategy)
    assert len(out["history"]) == 1 and out["history"][0]["health"]["status"] == "complete"
    assert out["history"][0]["run_attempts"] == 2


def ledger_signal(seed, session, symbol=None):
    symbol = symbol or f"L{session.day:02d}"      # one open position per (symbol, strategy, direction)
    seed.execute("INSERT INTO signal_ledger (symbol, signal_date, direction, entry_price, atr, stop_price, "
                 "target1_price, target2_price, target3_price, strategy_id, strategy_version) "
                 "VALUES (%s, %s, 1, 10, 1, 8, 12, 14, 16, %s, 'v1')", (symbol, session, seed.strategy_id()))


def boundary(seed, state, effective_from, note="activation boundary for the test"):
    seed.execute("SELECT research_capture_set_state(%s, %s, %s, %s)",
                 (seed.strategy_id(), state, effective_from, note))


def test_stored_partial_status_is_partial_even_without_counter_reasons():
    h = research.classify_run(run_with(status="partial"))
    assert h["status"] == "partial" and h["reasons"] == ["The run is recorded as partial."]
    h = research.classify_run(run_with(status="partial", captured=8, snapshot_skipped=2))
    assert h["status"] == "partial" and any("no usable T0 snapshot" in r for r in h["reasons"])


def test_sessions_before_the_activation_boundary_are_never_missing_or_failed(fetch, strategy, seed):
    for session in (S1, S2):
        ledger_signal(seed, session)                      # Release A era: signals, no capture, no boundary at all
    out = research.capture_runs(fetch, strategy)
    assert out["overall"]["status"] == "not_active" and out["history"] == []
    assert out["activation"]["state"] == "not_active" and out["activation"]["pre_activation_sessions"] == 2
    boundary(seed, "enabled", S3)                         # activation happens later: S1/S2 stay "before Release B"
    out = research.capture_runs(fetch, strategy)
    assert out["history"] == [] and out["activation"]["pre_activation_sessions"] == 2
    assert out["overall"]["status"] == "not_active" and str(S3) in out["overall"]["reason"]


def test_an_enabled_session_with_ledger_signals_and_no_run_is_missing(fetch, strategy, seed):
    ledger_signal(seed, S1)
    boundary(seed, "enabled", S2)
    ledger_signal(seed, S2)
    out = research.capture_runs(fetch, strategy)
    assert out["overall"]["status"] == "missing" and out["overall"]["session_date"] == S2
    assert [h["session_date"] for h in out["history"]] == [S2]       # S1 is pre-activation, not listed
    assert out["history"][0]["id"] is None and out["history"][0]["ledger_signals"] == 1
    assert out["activation"]["pre_activation_sessions"] == 1 and out["activation"]["active_from"] == S2


def test_missing_after_a_captured_session_and_a_disabled_interval(fetch, strategy, seed):
    boundary(seed, "enabled", S1)
    capture(seed, S1)
    boundary(seed, "disabled", S2, "pause capture for a data incident")
    ledger_signal(seed, S2)
    out = research.capture_runs(fetch, strategy)
    assert out["overall"]["status"] == "disabled" and out["overall"]["session_date"] == S2
    assert [h["health"]["status"] for h in out["history"]] == ["disabled", "complete"]
    assert out["activation"]["state"] == "disabled" and out["activation"]["current"]["effective_from_session"] == S2
    boundary(seed, "enabled", S3, "resume capture after the incident")
    ledger_signal(seed, S3)
    out = research.capture_runs(fetch, strategy)
    assert [h["health"]["status"] for h in out["history"]] == ["missing", "disabled", "complete"]


def test_state_on_is_the_latest_boundary_at_or_before_the_session():
    b = [dict(state="enabled", effective_from_session=S2), dict(state="disabled", effective_from_session=S3)]
    assert [research.state_on(b, d) for d in (S1, S2, S3)] == ["not_active", "enabled", "disabled"]
    assert research.state_on([], S1) == "not_active"


def test_a_session_with_a_run_is_never_missing(fetch, strategy, seed):
    boundary(seed, "enabled", S1)
    capture(seed, S1)
    ledger_signal(seed, S1)
    out = research.capture_runs(fetch, strategy)
    assert [h["health"]["status"] for h in out["history"]] == ["complete"]


def test_runtime_and_missing_feature_rate(fetch, strategy, seed):
    run = seed.run(S1)
    a = seed.snapshot("AAA", S1, features=json.dumps({"atr_14": 0.5, "rsi_14": None, "ret_5d": 0.1, "ret_20d": 0.2}),
                      snapshot_status="partial", missing_features=["rsi_14"])
    b = seed.snapshot("BBB", S1, features=json.dumps({"atr_14": 0.7, "rsi_14": 50.0, "ret_5d": 0.1, "ret_20d": 0.2}))
    seed.observation(snapshot_id=a, run_id=run, symbol="AAA", session=S1)
    seed.observation(snapshot_id=b, run_id=run, symbol="BBB", session=S1)
    finish(seed, run, candidates=2, captured=2)
    latest = research.capture_runs(fetch, strategy)["latest"]
    assert latest["runtime_seconds"] == 95.0
    mf = latest["missing_features"]
    assert (mf["snapshots"], mf["partial_snapshots"], mf["missing_slots"], mf["total_slots"]) == (2, 1, 1, 8)
    assert mf["rate"]["value"] == pytest.approx(1 / 8)
    assert research.summary(fetch, strategy)["rates"]["missing_feature_rate"]["value"] == pytest.approx(1 / 8)


def test_history_limit_validation(fetch, strategy, seed):
    capture(seed, S1)
    with pytest.raises(ValueError):
        research.capture_runs(fetch, strategy, limit=0)
    with pytest.raises(ValueError):
        research.capture_runs(fetch, strategy, limit=research.HISTORY_MAX + 1)


# ------------------------------------------------------------------ candidate explorer
def many(seed, n=7, session=S3):
    run = seed.run(session)
    for i in range(n):
        sym = f"S{i:02d}"
        snap = seed.snapshot(sym, session)
        seed.observation(snapshot_id=snap, run_id=run, symbol=sym, session=session, direction=1 if i % 2 == 0 else -1,
                         session_rank=i + 1, alignment_score=100 - i, tracked_intent=i < 3,
                         passed_guard=None if i == 6 else (i != 5),
                         guard_reasons=["price_discontinuity"] if i == 5 else None,
                         quality_grade="A" if i < 4 else "B")
    finish(seed, run, candidates=n, captured=n)


def test_pagination_is_server_side_with_stable_order(fetch, strategy, seed):
    many(seed)
    p1 = research.list_candidates(fetch, strategy, limit=3, offset=0)
    p2 = research.list_candidates(fetch, strategy, limit=3, offset=3)
    p3 = research.list_candidates(fetch, strategy, limit=3, offset=6)
    assert (p1["total"], p1["has_more"], p2["has_more"], p3["has_more"]) == (7, True, True, False)
    assert [i["symbol"] for i in p1["items"] + p2["items"] + p3["items"]] == [f"S{i:02d}" for i in range(7)]
    assert research.list_candidates(fetch, strategy, limit=3, offset=99)["items"] == []
    assert p1["session_date"] == S3


def test_default_session_is_the_latest_captured_not_the_wall_clock(fetch, strategy, seed):
    capture(seed, S1)
    capture(seed, S3)
    assert research.list_candidates(fetch, strategy)["session_date"] == S3
    assert research.list_candidates(fetch, strategy, session_date=S1)["session_date"] == S1
    assert research.list_candidates(fetch, strategy, session_date=S2)["total"] == 0


def test_filters(fetch, strategy, seed):
    many(seed)
    lc = lambda **k: research.list_candidates(fetch, strategy, **k)  # noqa: E731
    assert {i["symbol"] for i in lc(direction="bullish")["items"]} == {"S00", "S02", "S04", "S06"}
    assert lc(direction="bearish")["total"] == 3
    assert [i["symbol"] for i in lc(guard="rejected")["items"]] == ["S05"]
    assert [i["symbol"] for i in lc(guard="not_evaluated")["items"]] == ["S06"]
    assert lc(guard="passed")["total"] == 5
    assert lc(selected=True)["total"] == 3 and lc(selected=False)["total"] == 4
    assert lc(grade="a")["total"] == 4 and lc(grade="B")["total"] == 3
    assert [i["symbol"] for i in lc(symbol="s03")["items"]] == ["S03"]
    assert lc(symbol="S0")["total"] == 7 and lc(symbol="ZZ")["total"] == 0
    assert lc(direction="bullish", guard="passed", selected=True)["total"] == 2
    assert lc(candidate_class="bullish_breakout")["total"] == 4 and lc(candidate_class="near_bullish")["total"] == 0


def test_symbol_search_treats_like_wildcards_literally(fetch, strategy, seed):
    many(seed)
    assert research.list_candidates(fetch, strategy, symbol="%")["total"] == 0
    assert research.list_candidates(fetch, strategy, symbol="S_0")["total"] == 0


def test_sorts_and_facets(fetch, strategy, seed):
    many(seed)
    syms = lambda sort: [i["symbol"] for i in research.list_candidates(fetch, strategy, sort=sort)["items"]]  # noqa: E731
    assert syms("symbol") == sorted(syms("symbol"))
    assert syms("alignment_desc")[0] == "S00" and syms("rank")[0] == "S00"
    out = research.list_candidates(fetch, strategy)
    assert out["facets"]["classes"] == [{"value": "bullish_breakout", "count": 4}, {"value": "bearish_breakout", "count": 3}]
    assert out["facets"]["grades"] == [{"value": "A", "count": 4}, {"value": "B", "count": 3}]


@pytest.mark.parametrize("kw", [dict(sort="bogus"), dict(limit=0), dict(limit=201), dict(offset=-1),
                                dict(direction="up"), dict(guard="maybe")])
def test_bad_inputs_raise(fetch, strategy, seed, kw):
    capture(seed, S3)
    with pytest.raises(ValueError):
        research.list_candidates(fetch, strategy, **kw)


def test_item_shape_uses_stored_fields_only(fetch, strategy, seed):
    capture(seed, S3)
    items = {i["symbol"]: i for i in research.list_candidates(fetch, strategy)["items"]}
    a, b, c = items["AAA"], items["BBB"], items["CCC"]
    assert a["direction"] == "bullish" and c["direction"] == "bearish"
    assert (a["guard_status"], b["guard_status"]) == ("passed", "rejected") and b["guard_reasons"] == ["illiquid_dollar_volume"]
    assert a["guard_reasons"] == [] and a["selected"] is True and b["selected"] is False
    assert c["candidate_class"] == "near_bearish" and c["triggered"] is False
    assert a["combined_score"] == 88.5 and isinstance(a["alignment_score"], float)
    assert b["combined_score"] is None                                   # absent, not 0
    assert a["snapshot_id"] and a["snapshot_status"] == "complete" and a["missing_count"] == 0
    assert a["ledger_signal_id"] is None
    assert "strategy_context" not in a and "ml_confidence" not in a


# ------------------------------------------------------------------ candidate detail + lineage
def test_candidate_detail_separates_strategy_context_from_the_snapshot(fetch, strategy, seed):
    _, ids = capture(seed, S3)
    d = research.get_candidate(fetch, strategy, ids["CCC"])
    assert d["identity"] == {"symbol": "CCC", "session_date": S3, "bar_date": S3, "direction": "bearish",
                             "strategy_version": "v1"}
    sc = d["strategy_context"]
    assert sc["candidate_class"] == "near_bearish" and sc["triggered"] is False
    assert sc["levels"]["channel_low_prev"] == 8 and sc["guard"] == {"status": "passed", "reasons": []}
    assert sc["extension"] == {"urgency": "watch", "donchian_low_20": 7.5}
    assert sc["model"]["status"] == "not_processed" and sc["model"]["score"] is None
    assert d["snapshot"]["feature_set_version"] == "t0_v1" and d["snapshot"]["missing_count"] == 0
    assert d["capture"]["run_status"] == "complete" and d["lineage"]["signal"] is None
    rej = research.get_candidate(fetch, strategy, ids["BBB"])
    assert rej["strategy_context"]["guard"] == {"status": "rejected", "reasons": ["illiquid_dollar_volume"]}


def test_missing_candidate_is_none(fetch, strategy, seed):
    capture(seed, S3)
    assert research.get_candidate(fetch, strategy, 999999) is None


def test_lineage_links_candidate_to_the_ledger_signal(fetch, strategy, seed):
    _, ids = capture(seed, S3)
    sid = seed.strategy_id()
    snap = seed.execute("SELECT snapshot_id FROM candidate_observation WHERE id = %s", (ids["AAA"],)).fetchone()[0]
    ledger_id = seed.execute(
        "INSERT INTO signal_ledger (symbol, signal_date, direction, entry_price, atr, stop_price, target1_price, "
        "target2_price, target3_price, strategy_id, strategy_version, observation_id, feature_snapshot_id, "
        "feature_set_version) VALUES ('AAA', %s, 1, 10.5, 1, 8, 12, 14, 16, %s, 'v1', %s, %s, 't0_v1') RETURNING id",
        (S3, sid, ids["AAA"], snap)).fetchone()[0]
    d = research.get_candidate(fetch, strategy, ids["AAA"])
    sig = d["lineage"]["signal"]
    assert sig["id"] == ledger_id and sig["lifecycle"] == "open" and sig["status"] == "open"
    assert d["lineage"]["snapshot_id"] == snap and d["lineage"]["observation_id"] == ids["AAA"]
    listed = {i["symbol"]: i for i in research.list_candidates(fetch, strategy)["items"]}
    assert listed["AAA"]["ledger_signal_id"] == ledger_id and listed["BBB"]["ledger_signal_id"] is None
    stages = {x["key"]: x for x in research.summary(fetch, strategy)["funnel"]["stages"]}
    assert stages["ledger_signals"]["value"] == 1 and stages["ledger_signals"]["linked"] == 1


# ------------------------------------------------------------------ snapshot detail
def test_snapshot_groups_values_and_units_from_the_manifest(fetch, strategy, seed):
    manifest = {"raw_columns": [{"name": "close", "unit": "price", "definition": "raw close"}],
                "features": [{"name": "atr_14", "unit": "price", "definition": "Wilder ATR(14)"},
                             {"name": "rsi_14", "unit": "index", "definition": "RSI(14)"},
                             {"name": "future_feature", "unit": "ratio", "definition": "unmapped"}]}
    seed.execute("INSERT INTO feature_set_registry (feature_set_version, manifest, manifest_hash, impl_ref) "
                 "VALUES ('t0_v1', %s::jsonb, %s, 'test')", (json.dumps(manifest), "a" * 64))
    run = seed.run(S3)
    snap = seed.snapshot("AAA", S3, snapshot_status="partial", missing_features=["rsi_14"], sector="Technology",
                         sector_source="fundamentals", features=json.dumps(
                             {"atr_14": 0.5, "rsi_14": None, "future_feature": 2.0}))
    obs = seed.observation(snapshot_id=snap, run_id=run, symbol="AAA", session=S3)
    finish(seed, run, candidates=1, captured=1)
    s = research.get_snapshot(fetch, strategy, snap)
    assert s["identity"] == {"symbol": "AAA", "session_date": S3, "bar_date": S3, "feature_set_version": "t0_v1"}
    assert s["snapshot_status"] == "partial" and s["missing_features"] == ["rsi_14"]
    groups = {g["key"]: g for g in s["groups"]}
    items = {i["name"]: i for g in s["groups"] for i in g["items"]}
    assert groups["volatility"]["label"] == "Volatility" and "atr_14" in {i["name"] for i in groups["volatility"]["items"]}
    assert items["atr_14"]["unit"] == "price" and items["atr_14"]["definition"] == "Wilder ATR(14)"
    assert items["rsi_14"]["value"] is None and items["rsi_14"]["missing"] is True       # null, never 0
    assert items["atr_14"]["missing"] is False
    assert items["close"]["unit"] == "price" and items["close"]["value"] == 10.5
    assert {"future_feature"} <= {i["name"] for i in groups["other"]["items"]}           # unmapped feature not dropped
    assert items["sector"]["value"] == "Technology" and "market_metadata" in groups and "data_quality" in groups
    assert s["feature_set"]["manifest_hash"] == "a" * 64 and s["provenance"]["content_hash"] == "b" * 64
    assert s["observations"] == [{"id": obs, "direction": "bullish", "candidate_class": "bullish_breakout",
                                  "session_date": S3}]


def test_snapshot_with_null_volume_and_sector_renders_missing_not_zero(fetch, strategy, seed):
    run = seed.run(S3)
    snap = seed.snapshot("AAA", S3, volume=None, prev_close=None, snapshot_status="partial",
                         missing_features=["vol_ratio_10"], features=json.dumps({"vol_ratio_10": None}))
    seed.observation(snapshot_id=snap, run_id=run, symbol="AAA", session=S3)
    items = {i["name"]: i for g in research.get_snapshot(fetch, strategy, snap)["groups"] for i in g["items"]}
    assert items["volume"]["value"] is None and items["volume"]["missing"] is True
    assert items["prev_close"]["value"] is None and items["sector"]["value"] is None
    assert items["vol_ratio_10"]["missing"] is True


# ------------------------------------------------------------------ multi-strategy isolation
def second_strategy(seed, key="mean_reversion"):
    return seed.execute("INSERT INTO strategies (strategy_key, strategy_version, description) "
                        "VALUES (%s, 'v1', 'second') RETURNING id", (key,)).fetchone()[0]


def test_a_second_strategy_is_isolated_and_uses_the_same_read_model(fetch, strategy, seed):
    capture(seed, S3)
    sid2 = second_strategy(seed)
    seed.registry()
    run2 = seed.execute("INSERT INTO candidate_capture_run (strategy_id, strategy_version, session_date, "
                        "feature_set_version, session_source) VALUES (%s, 'v1', %s, 't0_v1', 'explicit') RETURNING id",
                        (sid2, S2)).fetchone()[0]
    snap = seed.snapshot("MMM", S2)
    obs2 = seed.observation(snapshot_id=snap, run_id=run2, symbol="MMM", session=S2, strategy_id=sid2,
                            signal_type="near_bullish", triggered=False, strategy_context=json.dumps({"urgency": "watch"}))
    finish(seed, run2, candidates=1, captured=1)
    s2 = analytics.get_strategy(fetch, "mean_reversion", "v1")

    one = research.list_candidates(fetch, strategy)
    two = research.list_candidates(fetch, s2)
    assert one["session_date"] == S3 and two["session_date"] == S2           # each strategy's own latest session
    assert {i["symbol"] for i in one["items"]} == {"AAA", "BBB", "CCC"} and [i["symbol"] for i in two["items"]] == ["MMM"]
    assert research.summary(fetch, s2)["cards"]["observations"]["value"] == 1
    assert research.summary(fetch, strategy)["cards"]["observations"]["value"] == 3
    assert research.capture_runs(fetch, s2)["history"][0]["session_date"] == S2
    d = research.get_candidate(fetch, s2, obs2)
    assert d["strategy_context"]["candidate_class"] == "near_bullish" and d["strategy_context"]["extension"] == {"urgency": "watch"}
    # a strategy cannot read another strategy's candidate or snapshot through its own namespace
    assert research.get_candidate(fetch, strategy, obs2) is None
    assert research.get_snapshot(fetch, strategy, snap) is None
    assert research.get_snapshot(fetch, s2, snap) is not None


def test_no_strategy_is_hardcoded_a_strategy_without_runs_is_no_data(fetch, seed):
    capture(seed, S3)
    second_strategy(seed)
    s2 = analytics.get_strategy(fetch, "mean_reversion", "v1")
    assert research.summary(fetch, s2)["availability"]["state"] == "no_data"
    assert research.capture_runs(fetch, s2)["overall"]["status"] == "not_active"
