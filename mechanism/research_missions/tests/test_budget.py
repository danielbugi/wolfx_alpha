"""Budget enforcement: the cap is enforced in code, before any request (owner requirement 2026-11-05: 'do not assume a budget cap alone prevents
charges'). No network; the database test needs PostgreSQL with migration 42 and is skipped without it."""
import os
import re
import sys
import threading
from types import SimpleNamespace

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))
sys.path.insert(0, os.path.join(ROOT, "mechanism", "research_missions", "tests"))

from research_missions import budget as B  # noqa: E402
from research_missions import config as C  # noqa: E402
from research_missions import llm as L  # noqa: E402
import fakes as F  # noqa: E402

LIM = B.Limits(monthly_usd=150.0, mission_usd=5.0, call_usd=2.0)


def gw(ledger=None, client=None, enabled=True, limits=LIM):
    built = []

    def factory():
        built.append(1)
        return client or F.FakeClient(planner=F.PLAN)
    g = L.Gateway(ledger or B.MemoryLedger(), limits, factory, enabled)
    return g, built


def call(g, mission_id=1, role="planner", mission_budget=None):
    from research_missions import agents as A
    return g.structured(mission_id=mission_id, role=role, system="s", user="u", schema=A.PLAN_SCHEMA, mission_budget=mission_budget)


# ------------------------------------------------------------------------------------------------------------------------------------------------ pure arithmetic
def test_the_decision_checks_call_month_and_mission_caps():
    assert B.decide(0, 0, 1.0, LIM) is None
    assert "per-call" in B.decide(0, 0, 2.01, LIM)
    assert "monthly" in B.decide(149.5, 0, 1.0, LIM)
    assert B.decide(149.0, 0, 1.0, LIM) is None                                     # exactly at the cap is allowed
    assert "mission" in B.decide(0, 4.5, 1.0, LIM)
    assert "mission" in B.decide(0, 0.5, 1.0, LIM, mission_budget=1.0)              # a mission row may carry a LOWER cap
    assert B.decide(0, 0.5, 1.0, LIM, mission_budget=50.0) is None and "mission" in B.decide(0, 4.5, 1.0, LIM, mission_budget=50.0)  # never a higher one


def test_the_environment_can_only_lower_limits():
    assert B.limits_from_env({"RESEARCH_MONTHLY_BUDGET_USD": "40"}).monthly_usd == 40.0
    assert B.limits_from_env({"RESEARCH_MONTHLY_BUDGET_USD": "1500"}).monthly_usd == C.MONTHLY_BUDGET_USD
    assert B.limits_from_env({"RESEARCH_MONTHLY_BUDGET_USD": "abc"}).monthly_usd == C.MONTHLY_BUDGET_USD
    assert B.limits_from_env({"RESEARCH_MAX_CALL_USD": "-3"}).call_usd == 0.0
    assert C.MONTHLY_BUDGET_USD == 150.0


def test_worst_case_is_an_upper_bound_and_includes_a_ceiling_priced_fallback():
    one = B.worst_case_usd("claude-sonnet-5-5", 30000, 24000, fallback=False)
    assert one == pytest.approx((10000 * 2 + 24000 * 10) / 1e6)
    both = B.worst_case_usd("claude-sonnet-5-5", 30000, 24000, fallback=True)
    assert both == pytest.approx(one + (10000 * 10 + 24000 * 50) / 1e6)
    assert B.worst_case_usd("some-future-model", 3000, 1000, fallback=False) == pytest.approx((1000 * 10 + 1000 * 50) / 1e6)   # unknown model: ceiling


def test_actual_cost_uses_the_served_model_and_the_ceiling_after_a_fallback():
    assert B.usage_cost_usd("claude-opus-5-5", F.usage(1_000_000, 100_000, 1_000_000, 0)) == pytest.approx(4 + 2 + 0.2)
    fb = F.usage(1000, 1000, iterations=[SimpleNamespace(type="message"), SimpleNamespace(type="fallback_message")])
    assert B.usage_cost_usd("claude-sonnet-5-5", fb) == pytest.approx((1000 * 10 + 1000 * 50) / 1e6)


# ------------------------------------------------------------------------------------------------------------------------------------------------ the gateway
def test_with_paid_calls_disabled_no_client_is_built_and_nothing_is_reserved():
    led = B.MemoryLedger()
    g, built = gw(led, enabled=False)
    with pytest.raises(L.PaidCallsDisabled):
        call(g)
    assert not built and not led.rows
    assert L.paid_calls_enabled({}) is False and L.paid_calls_enabled({"RESEARCH_PAID_CALLS_ENABLED": "1"}) is True


def test_a_refused_reservation_sends_no_request_and_is_recorded():
    led = B.MemoryLedger()
    led.rows.append({"id": 1, "mission_id": 9, "role": "analyst", "model": "m", "reserved_usd": 0, "cost_usd": 149.99, "status": "settled", "note": None})
    client = F.FakeClient(planner=F.PLAN)
    g, built = gw(led, client)
    with pytest.raises(B.BudgetExceeded):
        call(g)
    assert client.requests == [] and not built
    assert led.rows[-1]["status"] == "refused" and "monthly" in led.rows[-1]["note"]


def test_a_call_is_reserved_then_settled_at_its_actual_cost():
    led = B.MemoryLedger()
    client = F.FakeClient(planner=F.PLAN)
    g, _ = gw(led, client)
    out, cost = call(g)
    assert out["gdelt_queries"] and len(client.requests) == 1
    r = led.rows[0]
    assert r["status"] == "settled" and r["cost_usd"] == pytest.approx(cost) and r["reserved_usd"] >= cost
    assert cost == pytest.approx((1000 * 4 + 500 * 20) / 1e6)                     # FakeClient's default usage on the planner model (Opus 5.5)
    assert r["note"] == "message_id=msg_fake01 request_id=req_fake01 stop_reason=end_turn stop_category=None"   # traceable to the API's records
    req = client.requests[0]
    assert req["model"] == C.MODELS["planner"] and req["output_config"]["format"]["type"] == "json_schema" and req["fallbacks"] == "default"
    assert "temperature" not in req and "thinking" not in req and "tool_choice" not in req


def test_spending_stops_exactly_when_the_mission_budget_is_used_up():
    led = B.MemoryLedger()
    client = F.FakeClient(planner=F.PLAN)
    tiny = B.Limits(monthly_usd=150.0, mission_usd=1.0, call_usd=2.0)       # the planner reserves ~$0.57 (ceiling-priced fallback included)
    g, _ = gw(led, client, limits=tiny)
    n = 0
    with pytest.raises(B.BudgetExceeded):
        while n < 1000:                                                       # bounded: without enforcement this would spend forever
            call(g)
            n += 1
    assert n >= 1 and len(client.requests) == n                                   # the refused call never reached the client
    committed = sum(r["cost_usd"] for r in led.rows if r["status"] == "settled")
    assert committed <= tiny.mission_usd


@pytest.mark.parametrize("status, billed", [(400, False), (404, False), (429, True), (500, True), (None, True)])
def test_a_failed_call_is_settled_conservatively(status, billed):
    exc = RuntimeError("boom")
    exc.status_code = status
    led = B.MemoryLedger()
    g, _ = gw(led, F.FakeClient(raise_exc=exc))
    with pytest.raises(RuntimeError):
        call(g)
    r = led.rows[0]
    assert r["status"] == "failed" and (r["cost_usd"] == r["reserved_usd"] if billed else r["cost_usd"] == 0.0)


def test_a_refusal_or_truncation_is_paid_for_and_raised():
    for stop, err in (("refusal", L.ModelRefused), ("max_tokens", L.ModelOutputError)):
        led = B.MemoryLedger()
        g, _ = gw(led, F.FakeClient(planner=F.PLAN, stop=stop))
        with pytest.raises(err):
            call(g)
        assert led.rows[0]["status"] == "settled" and led.rows[0]["cost_usd"] > 0


def test_concurrent_reservations_cannot_overrun_the_cap_in_memory():
    led = B.MemoryLedger()
    lim = B.Limits(monthly_usd=1.0, mission_usd=1.0, call_usd=1.0)
    ok, refused = [], []

    def worker():
        try:
            ok.append(led.reserve(1, "analyst", "m", 0.3, lim))
        except B.BudgetExceeded:
            refused.append(1)
    ts = [threading.Thread(target=worker) for _ in range(10)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(ok) == 3 and len(refused) == 7


def test_only_the_gateway_imports_the_anthropic_sdk():
    pat = re.compile(r"^\s*(import anthropic|from anthropic\b)", re.M)
    offenders = []
    for base in ("mechanism", "backend"):
        for dp, _, files in os.walk(os.path.join(ROOT, base)):
            if "node_modules" in dp or "__pycache__" in dp:
                continue
            for f in files:
                if f.endswith(".py"):
                    p = os.path.join(dp, f)
                    if pat.search(open(p, encoding="utf-8", errors="replace").read()) and not p.endswith(os.path.join("research_missions", "llm.py")):
                        offenders.append(os.path.relpath(p, ROOT))
    assert offenders == []


# ------------------------------------------------------------------------------------------------------------------------------------------------ the database ledger
def _db():
    try:
        from shared import db as _d
        _d.execute_dict_query("SELECT 1 FROM research_llm_usage LIMIT 1")
        return _d
    except Exception as e:  # pragma: no cover
        pytest.skip(f"database with migration 42 not reachable: {e}")


def test_the_database_ledger_serialises_reservations_at_the_edge():
    d = _db()
    led = B.DbLedger(d)                                   # the ledger is append-only: test rows are told apart by their own mission id
    from research_missions.store import DbStore
    mid = DbStore(d).create_mission("thematic", "ci budget test", "q", "ai_semiconductors", {}, 1.0)
    lim = B.Limits(monthly_usd=10_000.0, mission_usd=1.0, call_usd=1.0)
    ok, refused = [], []

    def worker():
        try:
            ok.append(led.reserve(mid, "citest", "m", 0.3, lim, 1.0))
        except B.BudgetExceeded:
            refused.append(1)
    ts = [threading.Thread(target=worker) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(ok) == 3 and len(refused) == 5
    led.settle(ok[0], 0.1, {"input_tokens": 1}, "m")
    try:                                                                                              # a settled row is frozen by the trigger
        changed = d.execute_insert("UPDATE research_llm_usage SET cost_usd = 0 WHERE id = %s", (ok[0],))
    except Exception:  # noqa: BLE001
        changed = False
    assert changed is False
    assert float(d.execute_dict_query("SELECT cost_usd FROM research_llm_usage WHERE id = %s", (ok[0],))[0]["cost_usd"]) == pytest.approx(0.1)
