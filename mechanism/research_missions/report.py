"""Deterministic report: only VERIFIED and PARTIAL claims appear as findings; every company, relation and finding points at its claim labels,
and every claim at its evidence. No model writes report prose, so nothing unverified reaches the page."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Sequence

from research_missions import config as C

SHOWN = ("verified", "partial")


def _date(v: Any) -> str:
    return v.date().isoformat() if hasattr(v, "date") else (str(v)[:10] if v else "n/a")


def build(mission: Dict[str, Any], plan: Dict[str, Any], evidence: Sequence[Dict[str, Any]], claims: Sequence[Dict[str, Any]], companies: Sequence[Dict[str, Any]],
          relations: Sequence[Dict[str, Any]], open_questions: Sequence[str], usage: Dict[str, Any], sources: Sequence[str]) -> str:
    by = {c["label"]: c for c in claims}
    shown = [c for c in claims if c["status"] in SHOWN]
    dropped = [c for c in claims if c["status"] not in SHOWN]
    internal = any(e["license"] == "internal_only" for e in evidence)
    L: List[str] = [f"# {mission['title']}", "",
                    f"**Question:** {mission['question']}", "",
                    f"Research mission #{mission['id']} · {mission['kind']} · {C.DOMAINS[mission['domain']]['label']} · generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC", "",
                    "> Research notes, not investment advice. Findings are limited to the cited evidence; each was checked by an independent verifier. "
                    + ("Contains internal-only sources (licence): not for redistribution." if internal else ""), ""]
    L += ["## Findings", ""]
    if not shown:
        L += ["No claim passed verification.", ""]
    for kind, title in (("fact", "Facts"), ("inference", "Inferences"), ("forecast", "Forecasts reported in the evidence")):
        part = [c for c in shown if c["kind"] == kind]
        if part:
            L += [f"### {title}", ""]
            for c in part:
                flag = "" if c["status"] == "verified" else " *(partially supported)*"
                L.append(f"- **{c['label']}** {c['text']}{flag} [{', '.join(c['evidence'])}] · confidence {c['confidence']}")
            L.append("")

    def ok(labels):
        return [l for l in labels or [] if by.get(l, {}).get("status") in SHOWN]
    cos = [(co, ok(co.get("claim_labels")), ok(co.get("risk_labels"))) for co in companies]
    cos = [x for x in cos if x[1] or x[2]]
    if cos:
        L += ["## Companies and value-chain roles", "", "| Symbol | Company | Role | Exposure | Supporting findings | Risk findings | In our universe |", "|---|---|---|---|---|---|---|"]
        for co, sup, risk in cos:
            L.append(f"| {co['symbol']} | {co.get('name') or ''} | {co['role']} | {co['exposure']} | {', '.join(sup) or 'none'} | {', '.join(risk) or 'none'} | "
                     f"{'yes' if co.get('in_universe') else 'no (not scored)'} |")
        L.append("")
    rels = [(r, ok(r.get("claim_labels"))) for r in relations]
    rels = [x for x in rels if x[1]]
    if rels:
        L += ["## Supply-chain map", ""] + [f"- {r['from_entity']} **{r['relation'].replace('_', ' ')}** {r['to_entity']} [{', '.join(l)}]" for r, l in rels] + [""]
    if open_questions:
        L += ["## Open questions (not findings)", ""] + [f"- {q}" for q in open_questions] + [""]
    if plan.get("outside_universe"):
        L += ["## Named but outside our universe (not researched)", ""] + [f"- {o.get('name')}: {o.get('why')}" for o in plan["outside_universe"]] + [""]
    L += ["## Evidence", "", "| Label | Source | Publisher | Published | Licence | Title |", "|---|---|---|---|---|---|"]
    for e in evidence:
        title = (e.get("title") or "").replace("|", "/")
        link = f"[{title}]({e['url']})" if str(e.get("url", "")).startswith("http") else title
        L.append(f"| {e['label']} | {e['source']} | {e.get('publisher') or ''} | {_date(e.get('published_at'))} | {e['license']} | {link} |")
    L.append("")
    if dropped:
        L += ["## Claims that did not pass verification (excluded from the findings)", ""] + [f"- {c['label']} ({c['status']}): {c['text']} - {c.get('verifier_note') or ''}" for c in dropped] + [""]
    L += ["## Method and limits", "",
          f"- Sources used: {', '.join(sources)}. Evidence pack capped at {C.MAX_EVIDENCE_ITEMS} items with {C.EXCERPT_CHARS}-character excerpts; no full articles were read.",
          "- The models may know events after the evidence dates; claims were restricted to the evidence and checked against it, and the mission is evaluated forward only.",
          f"- Model calls: {usage.get('calls', 0)}, cost ${usage.get('cost_usd', 0):.4f}.", ""]
    return "\n".join(L)
