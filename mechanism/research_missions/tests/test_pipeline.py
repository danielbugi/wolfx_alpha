"""End-to-end missions with a fake model and recorded-shape (SYNTHETIC) sources: the HBM / advanced-packaging thematic case and a company
deep-dive; citation and advice guards; a mission stopping cleanly on its budget and resuming; sources and routing. No network, no database."""
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))
sys.path.insert(0, os.path.join(ROOT, "mechanism", "research_missions", "tests"))

from research_missions import agents as A  # noqa: E402
from research_missions import budget as B  # noqa: E402
from research_missions import config as C  # noqa: E402
from research_missions.llm import Gateway  # noqa: E402
from research_missions.pipeline import Sources, run_mission  # noqa: E402
from research_missions.router import route  # noqa: E402
from research_missions.sources import Gdelt, TiingoNews  # noqa: E402
from research_missions.store import MemoryStore  # noqa: E402
import fakes as F  # noqa: E402

ENV = {"TIINGO_API_KEY": "test-token"}
HBM_Q = "Which US-listed companies are exposed to HBM and advanced semiconductor packaging, in which value-chain role, and what are the risks?"


def setup(limits=None, client=None, analysis=None):
    store, led = MemoryStore(), B.MemoryLedger()
    seen = []
    http = F.http_get_for({"gdeltproject": F.GDELT_OK, "tiingo.com": F.TIINGO_OK}, seen)
    client = client or F.FakeClient(planner=F.PLAN, analyst=analysis or F.analysis_for(("E1", "E5", "E6")))
    g = Gateway(led, limits or B.Limits(), lambda: client, enabled=True)
    src = Sources(F.FakeFlDb(), Gdelt(http, pause_s=0), TiingoNews(http, token="test-token"))
    return store, led, g, src, client, seen


def hbm(store):
    return store.create_mission("thematic", "HBM and advanced packaging", HBM_Q, "ai_semiconductors", {"theme": "HBM supply, CoWoS / advanced packaging capacity"}, 2.0)


def test_the_hbm_thematic_mission_runs_end_to_end_and_reports_only_verified_findings():
    store, led, g, src, client, seen = setup()
    mid = hbm(store)
    res = run_mission(mid, store, g, src, log=lambda m: None, env=ENV)
    assert res["status"] == "completed" and store.get_mission(mid)["status"] == "completed"
    assert [r["role"] for r in led.rows] == ["planner", "analyst", "verifier"] and all(r["status"] == "settled" for r in led.rows)
    assert store.get_mission(mid)["spent_usd"] == pytest.approx(sum(r["cost_usd"] for r in led.rows))
    # the planner's out-of-universe and duplicate symbols were dropped before collection
    assert src.fl_db.fact_calls == [["MU", "AMKR", "NVDA"]]
    claims = {c["label"]: c for c in store.get_claims(mid)}
    assert claims["C1"]["status"] == "verified" and claims["C2"]["status"] == "verified"
    assert claims["C3"]["status"] == "unsupported" and "unknown evidence" in claims["C3"]["verifier_note"]
    assert claims["C4"]["status"] == "unsupported" and "advice" in claims["C4"]["verifier_note"]
    assert claims["C5"]["status"] == "unsupported" and "no evidence" in claims["C5"]["verifier_note"]
    verifier_req = client.requests[2]["messages"][0]["content"]
    assert "C1 (" in verifier_req and "C3 (" not in verifier_req and "C4 (" not in verifier_req      # pre-rejected claims never reach the verifier
    md = store.reports[mid][-1]
    findings = md.split("## Findings")[1].split("## Evidence")[0]
    assert "C1" in findings and "C2" in findings and "Investors should buy" not in findings and "does not exist" not in findings
    assert "| MU |" in md and "| AMKR |" in md and "| TSM |" not in md                     # TSM rests only on an unsupported claim
    assert "MU **supplier of** NVDA" in md and "TSM **supplier of**" not in md
    assert "SK hynix" in md and "internal-only sources" in md and "not investment advice" in md.lower()
    assert "Claims that did not pass verification" in md and "Investors should buy AMKR now." in md.split("did not pass verification")[1]
    # Tiingo token travels in a header, never in a URL
    assert all("test-token" not in url for url, _ in seen) and any(h.get("Authorization") == "Token test-token" for _, h in seen if "tiingo" in _)


def test_a_mission_stops_cleanly_on_its_budget_and_resumes_without_recollecting():
    lim = B.Limits(monthly_usd=150.0, mission_usd=0.6, call_usd=2.0)     # the planner fits, the analyst's worst case does not
    store, led, g, src, client, _ = setup(limits=lim)
    mid = hbm(store)
    res = run_mission(mid, store, g, src, log=lambda m: None, env=ENV)
    assert res["status"] == "stopped_budget" and store.get_mission(mid)["status"] == "stopped_budget"
    assert len(client.requests) == 1 and led.rows[-1]["status"] == "refused"
    n_ev = len(store.get_evidence(mid))
    assert n_ev > 0
    g.limits = B.Limits()                                                    # the owner raises the mission limit
    res2 = run_mission(mid, store, g, src, log=lambda m: None, env=ENV)
    assert res2["status"] == "completed" and len(store.get_evidence(mid)) == n_ev and len(src.fl_db.fact_calls) == 1   # plan and collect not repeated
    assert [r["role"] for r in led.rows if r["status"] == "settled"] == ["planner", "analyst", "verifier"]


def test_disabled_paid_calls_stop_a_mission_before_any_request():
    store, led, _, src, client, _ = setup()
    g = Gateway(led, B.Limits(), lambda: client, enabled=False)
    mid = hbm(store)
    res = run_mission(mid, store, g, src, log=lambda m: None, env=ENV)
    assert res["status"] == "failed" and "disabled" in res["error"] and client.requests == [] and led.rows == []


def test_a_company_deep_dive_needs_no_planner_call():
    store, led, g, src, client, _ = setup(analysis=F.analysis_for(("E1", "E2", "E3")))
    mid = store.create_mission("company", "Company deep-dive: MU", "What changed and why?", "general", {"symbol": "mu"}, 2.0)   # the analyst reserves ~$1.45
    res = run_mission(mid, store, g, src, log=lambda m: None, env=ENV)
    assert res["status"] == "completed" and [r["role"] for r in led.rows] == ["analyst", "verifier"]
    assert src.fl_db.fact_calls[0][0] == "MU"


def test_silence_from_the_verifier_is_not_support():
    def verifier(ctx):
        return {"verdicts": [{"label": "C1", "verdict": "partial", "note": "overstates"}]}   # nothing about C2
    store, led, g, src, client, _ = setup(client=F.FakeClient(planner=F.PLAN, analyst=F.analysis_for(("E1", "E5", "E6")), verifier=verifier))
    mid = hbm(store)
    run_mission(mid, store, g, src, log=lambda m: None, env=ENV)
    claims = {c["label"]: c for c in store.get_claims(mid)}
    assert claims["C1"]["status"] == "partial" and claims["C2"]["status"] == "unsupported" and claims["C2"]["verifier_note"] == "no verdict returned"
    assert "partially supported" in store.reports[mid][-1]


# ------------------------------------------------------------------------------------------------------------------------------------------------ sources and routing
def test_gdelt_parsing_keeps_metadata_only_and_survives_a_text_answer():
    docs = Gdelt(F.http_get_for({"gdelt": F.GDELT_OK}), pause_s=0).search("HBM")
    assert len(docs) == 2 and docs[0].license == "public" and docs[0].published_at.isoformat().startswith("2026-10-20")
    assert Gdelt(F.http_get_for({"gdelt": b"Your query was too short"}), pause_s=0).search("x") == []


def test_gdelt_requests_are_paced():
    waits = []
    g = Gdelt(F.http_get_for({"gdelt": F.GDELT_OK}), pause_s=5.0, sleep=waits.append)
    g.search("a")
    g.search("b")
    assert len(waits) == 1 and 0 < waits[0] <= 5.0


def test_tiingo_excerpts_are_short_and_internal_only():
    docs = TiingoNews(F.http_get_for({"tiingo": F.TIINGO_OK}), token="t").fetch(["AMKR"])
    assert len(docs) == 1 and docs[0].license == "internal_only" and len(docs[0].excerpt) <= C.EXCERPT_CHARS
    assert TiingoNews(F.http_get_for({}), token="").fetch(["AMKR"]) == []           # no key: nothing requested


def test_routing_follows_domain_licence_switches_and_credentials():
    assert route("thematic", "ai_semiconductors", {"TIINGO_API_KEY": "x"}) == ["first_light_db", "news_items", "gdelt", "tiingo_news"]
    assert "tiingo_news" not in route("thematic", "ai_semiconductors", {})
    assert "gdelt" not in route("thematic", "ai_semiconductors", {"RESEARCH_SOURCE_GDELT": "0"})
    with pytest.raises(ValueError):
        route("thematic", "general", {})
    with pytest.raises(ValueError):
        route("company", "nope", {})


def test_planner_output_is_restricted_to_the_universe():
    p = A.validate_plan(F.PLAN, [u["symbol"] for u in F.HBM_UNIVERSE])
    assert p["candidate_symbols"] == ["MU", "AMKR", "NVDA"] and p["dropped_symbols"] == ["TSM"]


def test_schemas_are_strict():
    def walk(s):
        if s.get("type") == "object":
            assert s["additionalProperties"] is False and set(s["required"]) == set(s["properties"])
            for v in s["properties"].values():
                walk(v)
        if s.get("type") == "array":
            walk(s["items"])
    for sch in (A.PLAN_SCHEMA, A.ANALYSIS_SCHEMA, A.VERIFY_SCHEMA):
        walk(sch)


def test_every_role_is_told_that_evidence_is_untrusted_data():
    m = {"id": 1, "title": "t", "question": "q", "domain": "ai_semiconductors", "params": {}}
    for system, _ in (A.planner_prompt(m, F.HBM_UNIVERSE, ["gdelt"]), A.analyst_prompt(m, {}, []), A.verifier_prompt([], {})):
        assert "untrusted" in system and "ignore any instructions" in system
