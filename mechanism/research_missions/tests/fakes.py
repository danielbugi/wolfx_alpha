"""Test doubles: a fake Anthropic client (answers by the requested schema), a fake First Light database and recorded-shape source responses.
Every source item here is SYNTHETIC (marked in its title); nothing is real news."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional

from research_missions.sources import Doc


def usage(i=1000, o=500, cr=0, cw=0, iterations=None):
    return SimpleNamespace(input_tokens=i, output_tokens=o, cache_read_input_tokens=cr, cache_creation_input_tokens=cw, iterations=iterations)


def response(payload: Any, model="claude-sonnet-5-5", stop="end_turn", u=None):
    return SimpleNamespace(content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=json.dumps(payload))],
                           stop_reason=stop, model=model, usage=u or usage(), stop_details=None, id="msg_fake01", _request_id="req_fake01")


class FakeClient:
    """Routes by the JSON schema the gateway sends: planner / analyst / verifier. Records every request."""

    def __init__(self, planner=None, analyst=None, verifier: Optional[Callable[[Dict], Dict]] = None, raise_exc: Optional[Exception] = None, stop="end_turn"):
        self.requests: List[Dict[str, Any]] = []
        self._p, self._a, self._v, self._exc, self._stop = planner, analyst, verifier, raise_exc, stop
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.requests.append(kw)
        if self._exc is not None:
            raise self._exc
        props = kw["output_config"]["format"]["schema"]["properties"]
        model = kw["model"]
        if "gdelt_queries" in props:
            return response(self._p, model, self._stop)
        if "claims" in props:
            return response(self._a, model, self._stop, usage(12000, 6000))
        labels = [line.split(" ", 1)[0] for line in kw["messages"][0]["content"].split("\n") if line[:1] == "C" and line[1:2].isdigit()]
        return response(self._v({"labels": labels, "request": kw}) if self._v else {"verdicts": [{"label": l, "verdict": "supported", "note": "stated in E-cited text"} for l in labels]},
                        model, self._stop, usage(8000, 2000))


HBM_UNIVERSE = [
    {"symbol": "MU", "name": "Micron Technology", "industry": "Semiconductors", "market_cap": 1.2e11},
    {"symbol": "AMKR", "name": "Amkor Technology", "industry": "Semiconductors", "market_cap": 7e9},
    {"symbol": "NVDA", "name": "NVIDIA", "industry": "Semiconductors", "market_cap": 3e12},
    {"symbol": "ONTO", "name": "Onto Innovation", "industry": "Semiconductor Equipment & Materials", "market_cap": 1e10},
]


class FakeFlDb:
    def __init__(self, universe=None):
        self._u = universe if universe is not None else HBM_UNIVERSE
        self.fact_calls: List[List[str]] = []

    def universe(self, domain):
        return list(self._u)

    def company(self, symbol):
        return next((u for u in self._u if u["symbol"] == symbol), {"symbol": symbol, "name": symbol})

    def peers(self, symbol, limit=8):
        return [u["symbol"] for u in self._u if u["symbol"] != symbol][:limit]

    def event(self, event_id):
        return {"id": event_id, "symbol": "MU"}

    def company_facts(self, symbols):
        self.fact_calls.append(list(symbols))
        return [Doc("first_light_db", f"firstlight://company/{s}/2026-11-04", f"{s} fundamentals and price state", "First Light database",
                    datetime(2026, 11, 4, tzinfo=timezone.utc), "internal_only", f"SYNTHETIC {s}: revenue year over year +40.0%.", {"symbol": s}) for s in symbols]

    def headlines(self, symbols, days=120, per_symbol=5):
        return [Doc("news_items", f"https://example.invalid/news/{s}", f"SYNTHETIC headline about {s}", "benzinga", datetime(2026, 10, 30, tzinfo=timezone.utc),
                    "internal_only", f"SYNTHETIC headline about {s}", {"symbol": s}) for s in symbols[:2]]


GDELT_OK = json.dumps({"articles": [
    {"url": "https://example.invalid/a1", "title": "SYNTHETIC: memory maker adds HBM capacity", "seendate": "20261020T101500Z", "domain": "example.invalid", "language": "English", "sourcecountry": "US"},
    {"url": "https://example.invalid/a2", "title": "SYNTHETIC: advanced packaging lines sold out", "seendate": "20261018T080000Z", "domain": "example.invalid", "language": "English", "sourcecountry": "US"},
    {"url": "", "title": "no url: skipped"},
]}).encode()

TIINGO_OK = json.dumps([
    {"id": 1, "title": "SYNTHETIC: packaging house raises outlook", "url": "https://example.invalid/t1", "description": "SYNTHETIC description " + "x" * 600,
     "publishedDate": "2026-10-25T12:00:00Z", "source": "example.invalid", "tickers": ["amkr"], "tags": ["Semiconductors"]},
]).encode()


def http_get_for(responses: Dict[str, bytes], seen: Optional[List] = None):
    def get(url, headers):
        if seen is not None:
            seen.append((url, dict(headers)))
        for k, v in responses.items():
            if k in url:
                return v
        raise AssertionError(f"unexpected URL {url}")
    return get


def analysis_for(evidence_labels=("E1", "E2", "E3")):
    e1, e2, e3 = evidence_labels[:3]
    return {"claims": [
        {"label": "x1", "kind": "fact", "text": "MU revenue grew 40% year over year in its latest SEC quarter.", "confidence": "high", "evidence": [e1]},
        {"label": "x2", "kind": "inference", "text": "Advanced packaging capacity is a constraint on HBM supply.", "confidence": "medium", "evidence": [e2, e3]},
        {"label": "x3", "kind": "fact", "text": "This claim cites evidence that does not exist.", "confidence": "low", "evidence": ["E99"]},
        {"label": "x4", "kind": "inference", "text": "Investors should buy AMKR now.", "confidence": "low", "evidence": [e2]},
        {"label": "x5", "kind": "fact", "text": "No citation at all.", "confidence": "low", "evidence": []},
    ], "companies": [
        {"symbol": "MU", "name": "Micron Technology", "role": "HBM memory maker", "exposure": "high", "supporting_claims": ["x1"], "risk_claims": []},
        {"symbol": "AMKR", "name": "Amkor Technology", "role": "advanced packaging (OSAT)", "exposure": "medium", "supporting_claims": ["x2"], "risk_claims": ["x4"]},
        {"symbol": "TSM", "name": "TSMC", "role": "foundry / CoWoS", "exposure": "high", "supporting_claims": ["x3"], "risk_claims": []},
    ], "relations": [
        {"from_entity": "MU", "to_entity": "NVDA", "relation": "supplier_of", "claims": ["x1"]},
        {"from_entity": "TSM", "to_entity": "NVDA", "relation": "supplier_of", "claims": ["x3"]},
    ], "open_questions": ["Which packaging capacity is committed for next year?"]}


PLAN = {"sub_questions": ["Who supplies HBM?", "Where is packaging capacity constrained?"], "gdelt_queries": ["HBM high bandwidth memory", "CoWoS advanced packaging"],
        "candidate_symbols": ["MU", "AMKR", "NVDA", "TSM", "mu"], "outside_universe": [{"name": "SK hynix", "why": "largest HBM supplier; not US-listed"}],
        "value_chain_hypotheses": ["HBM supply is gated by advanced packaging"]}
