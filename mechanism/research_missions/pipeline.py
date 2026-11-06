"""Mission execution: plan -> collect -> analyze -> verify -> report. Each step is recorded; a re-run resumes after the last completed step.

Status: draft/queued -> running -> completed | failed | stopped_budget. A budget refusal or disabled paid calls stop the mission cleanly with
everything collected so far kept (re-run after the limit allows it)."""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Sequence

from research_missions import agents as A
from research_missions import config as C
from research_missions import report as R
from research_missions.budget import BudgetExceeded
from research_missions.llm import Gateway, PaidCallsDisabled
from research_missions.router import route
from research_missions.sources import Doc


class Sources:
    """The collectors a run may use (tests pass fakes)."""

    def __init__(self, fl_db=None, gdelt=None, tiingo=None) -> None:
        self.fl_db, self.gdelt, self.tiingo = fl_db, gdelt, tiingo


def _dedupe(docs: Sequence[Doc]) -> List[Doc]:
    seen, out = set(), []
    for d in docs:
        k = (d.source, d.url)
        if k not in seen:
            seen.add(k)
            out.append(d)
    return out


def run_mission(mission_id: int, store, gateway: Gateway, sources: Sources, log: Callable[[str], None] = print, env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    m = store.get_mission(mission_id)
    if m["status"] in ("running", "completed"):
        raise RuntimeError(f"mission {mission_id} is {m['status']}")
    allowed = route(m["kind"], m["domain"], env)
    store.set_status(mission_id, "running")
    cost0 = gateway.cost_usd
    try:
        plan = _plan(m, store, gateway, sources, allowed, log)
        evidence = _collect(m, store, sources, allowed, plan, log)
        analysis = _analyze(m, store, gateway, plan, evidence, log)
        _verify(m, store, gateway, evidence, log)
        version = _report(m, store, plan, analysis, allowed, gateway, log)
    except BudgetExceeded as exc:
        store.set_status(mission_id, "stopped_budget", str(exc))
        log(f"RESEARCH mission {mission_id} stopped: {exc}")
        return {"status": "stopped_budget", "error": str(exc), "cost_usd": round(gateway.cost_usd - cost0, 6)}
    except PaidCallsDisabled as exc:
        store.set_status(mission_id, "failed", str(exc))
        log(f"RESEARCH mission {mission_id} not run: {exc}")
        return {"status": "failed", "error": str(exc), "cost_usd": 0.0}
    except Exception as exc:  # noqa: BLE001 - recorded on the mission, then re-raised for the operator
        store.set_status(mission_id, "failed", f"{type(exc).__name__}: {exc}"[:1000])
        raise
    finally:
        spent = gateway.cost_usd - cost0
        if spent:
            store.add_spent(mission_id, spent)
    store.set_status(mission_id, "completed")
    return {"status": "completed", "report_version": version, "cost_usd": round(gateway.cost_usd - cost0, 6)}


def _step(store, mid: int, name: str, fn):
    done = store.last_done(mid, name)
    if done is not None:
        return done["detail"]
    sid = store.start_step(mid, name)
    try:
        detail = fn()
    except Exception as exc:
        store.finish_step(sid, "failed", {"error": f"{type(exc).__name__}: {exc}"[:500]})
        raise
    store.finish_step(sid, "done", detail)
    return detail


def _plan(m, store, gateway, sources: Sources, allowed: Sequence[str], log) -> Dict[str, Any]:
    def go():
        if m["kind"] == "company":
            sym = A.first_company_symbol(m["params"])
            if not sym and m["params"].get("event_id") and sources.fl_db:
                ev = sources.fl_db.event(int(m["params"]["event_id"]))
                sym = ev["symbol"] if ev else None
            if not sym:
                raise ValueError("a company mission needs params.symbol or params.event_id")
            co = sources.fl_db.company(sym) if sources.fl_db else None
            peers = sources.fl_db.peers(sym) if sources.fl_db else []
            name = (co or {}).get("name") or sym
            return {"kind": "company", "symbol": sym, "candidate_symbols": [sym] + peers, "universe": [sym] + peers,
                    "gdelt_queries": [f'"{name}"' if " " in name else name][:1], "sub_questions": [], "value_chain_hypotheses": [], "outside_universe": []}
        universe = sources.fl_db.universe(m["domain"]) if sources.fl_db else []
        system, user = A.planner_prompt(m, universe, allowed)
        raw, cost = gateway.structured(mission_id=m["id"], role="planner", system=system, user=user, schema=A.PLAN_SCHEMA, mission_budget=m["budget_usd"])
        plan = A.validate_plan(raw, [u["symbol"] for u in universe])
        plan.update(kind="thematic", universe=[u["symbol"] for u in universe], cost_usd=cost)
        log(f"RESEARCH plan: {len(plan['candidate_symbols'])} candidates, {len(plan['gdelt_queries'])} queries")
        return plan
    return _step(store, m["id"], "plan", go)


def _collect(m, store, sources: Sources, allowed: Sequence[str], plan: Dict[str, Any], log) -> List[Dict[str, Any]]:
    def go():
        syms = plan["candidate_symbols"]
        docs: List[Doc] = []
        counts: Dict[str, int] = {}
        if "first_light_db" in allowed and sources.fl_db:
            d = sources.fl_db.company_facts(syms)
            counts["first_light_db"] = len(d)
            docs += d
        if "gdelt" in allowed and sources.gdelt:
            g: List[Doc] = []
            for q in plan["gdelt_queries"]:
                g += sources.gdelt.search(q)
            counts["gdelt"] = len(g)
            docs += g
        if "tiingo_news" in allowed and sources.tiingo:
            t = sources.tiingo.fetch(syms[:10])
            counts["tiingo_news"] = len(t)
            docs += t
        if "news_items" in allowed and sources.fl_db:
            h = sources.fl_db.headlines(syms)
            counts["news_items"] = len(h)
            docs += h
        docs = _dedupe(docs)
        capped = len(docs) > C.MAX_EVIDENCE_ITEMS
        if capped:                                    # our own data first (one item per company), then the newest external items
            own = [d for d in docs if d.source == "first_light_db"]
            ext = sorted((d for d in docs if d.source != "first_light_db"), key=lambda d: d.published_at.timestamp() if d.published_at else 0, reverse=True)
            docs = (own + ext)[: C.MAX_EVIDENCE_ITEMS]
        store.add_evidence(m["id"], docs)
        log(f"RESEARCH collect: {len(docs)} evidence items {counts}{' (capped)' if capped else ''}")
        return {"counts": counts, "items": len(docs), "capped": capped}
    _step(store, m["id"], "collect", go)
    return store.get_evidence(m["id"])


def _analyze(m, store, gateway, plan, evidence, log) -> Dict[str, Any]:
    def go():
        if not evidence:
            raise ValueError("no evidence was collected; nothing to analyse")
        system, user = A.analyst_prompt(m, plan, evidence)
        raw, cost = gateway.structured(mission_id=m["id"], role="analyst", system=system, user=user, schema=A.ANALYSIS_SCHEMA, mission_budget=m["budget_usd"])
        analysis = A.validate_analysis(raw, [e["label"] for e in evidence])
        store.save_analysis(m["id"], analysis, plan.get("universe") or [])
        pre = sum(c["status"] == "unsupported" for c in analysis["claims"])
        log(f"RESEARCH analyze: {len(analysis['claims'])} claims ({pre} rejected before verification), {len(analysis['companies'])} companies")
        return {"claims": len(analysis["claims"]), "pre_rejected": pre, "open_questions": analysis["open_questions"], "cost_usd": cost}
    return _step(store, m["id"], "analyze", go)


def _verify(m, store, gateway, evidence, log) -> None:
    def go():
        claims = store.get_claims(m["id"])
        todo = [c for c in claims if c["status"] == "unverified"]
        by = {e["label"]: e for e in evidence}
        verdicts: List[Dict[str, Any]] = []
        cost = 0.0
        for batch in A.chunk(todo, 40):
            system, user = A.verifier_prompt(batch, by)
            raw, c = gateway.structured(mission_id=m["id"], role="verifier", system=system, user=user, schema=A.VERIFY_SCHEMA, mission_budget=m["budget_usd"])
            verdicts += raw.get("verdicts") or []
            cost += c
        updated = A.apply_verdicts(claims, verdicts)
        store.update_claims(m["id"], updated)
        tally = {s: sum(c["status"] == s for c in updated) for s in ("verified", "partial", "unsupported")}
        log(f"RESEARCH verify: {tally}")
        return {"tally": tally, "cost_usd": cost}
    _step(store, m["id"], "verify", go)


def _report(m, store, plan, analysis_detail, allowed, gateway, log) -> int:
    def go():
        usage = {"calls": gateway.calls, "cost_usd": store.get_mission(m["id"])["spent_usd"] + gateway.cost_usd}
        md = R.build(store.get_mission(m["id"]), plan, store.get_evidence(m["id"]), store.get_claims(m["id"]), store.get_companies(m["id"]),
                     store.get_relations(m["id"]), analysis_detail.get("open_questions") or [], usage, allowed)
        v = store.save_report(m["id"], md)
        log(f"RESEARCH report: version {v}, {len(md)} characters")
        return {"version": v}
    return _step(store, m["id"], "report", go)["version"]
