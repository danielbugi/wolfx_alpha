"""Research missions CLI (manual only; nothing here is scheduled).

    python -m research_missions create-thematic --domain ai_semiconductors --title "HBM and advanced packaging" \
           --question "..." [--theme "..."] [--budget 2]
    python -m research_missions create-company --symbol MU [--event-id 123] [--budget 1]
    python -m research_missions run <id>           # paid calls need RESEARCH_PAID_CALLS_ENABLED=1 and ANTHROPIC_API_KEY
    python -m research_missions show <id>          # status, steps, report
    python -m research_missions budget             # this month's spend against the cap
    python -m research_missions preflight          # read-only source proof, store isolation, limits, switches (no paid call)

(run from mechanism/, like the other modules)

With RESEARCH_SOURCE_DSN set, FirstLightDb reads through a proven read-only session (source_db.py) and `run` refuses unless the store
(shared.db, where missions and the ledger are written) is a different database. Without it, both use shared.db (local development).
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from research_missions import budget as B  # noqa: E402
from research_missions import config as C  # noqa: E402
from research_missions.llm import Gateway, paid_calls_enabled  # noqa: E402
from research_missions import source_db as SDB  # noqa: E402
from research_missions.pipeline import Sources, run_mission  # noqa: E402
from research_missions.sources import FirstLightDb, Gdelt, TiingoNews  # noqa: E402
from research_missions.store import DbStore  # noqa: E402


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Research missions")
    sub = p.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("create-thematic")
    t.add_argument("--domain", required=True, choices=sorted(C.DOMAINS))
    t.add_argument("--title", required=True)
    t.add_argument("--question", required=True)
    t.add_argument("--theme", default="")
    t.add_argument("--budget", type=float, default=C.MISSION_BUDGET_USD)
    c = sub.add_parser("create-company")
    c.add_argument("--symbol")
    c.add_argument("--event-id", type=int)
    c.add_argument("--question")
    c.add_argument("--domain", default="general", choices=sorted(C.DOMAINS))
    c.add_argument("--budget", type=float, default=C.MISSION_BUDGET_USD)
    r = sub.add_parser("run")
    r.add_argument("id", type=int)
    s = sub.add_parser("show")
    s.add_argument("id", type=int)
    sub.add_parser("budget")
    sub.add_parser("preflight")
    a = p.parse_args(argv)

    from shared import db
    store = DbStore(db)
    limits = B.limits_from_env()
    if a.cmd == "create-thematic":
        mid = store.create_mission("thematic", a.title, a.question, a.domain, {"theme": a.theme}, min(a.budget, limits.mission_usd))
        print(json.dumps({"id": mid, "status": "draft"}))
    elif a.cmd == "create-company":
        if not a.symbol and not a.event_id:
            p.error("--symbol or --event-id is required")
        params = {k: v for k, v in (("symbol", (a.symbol or "").upper() or None), ("event_id", a.event_id)) if v}
        title = f"Company deep-dive: {params.get('symbol') or 'event ' + str(a.event_id)}"
        question = a.question or "What changed in this company's fundamentals, why, is it likely to persist, and what are the main risks?"
        mid = store.create_mission("company", title, question, a.domain, params, min(a.budget, limits.mission_usd))
        print(json.dumps({"id": mid, "status": "draft"}))
    elif a.cmd == "run":
        source = SDB.from_env()
        if source is not None:
            SDB.assert_isolated(db, source)
        gateway = Gateway(B.DbLedger(db), limits)
        res = run_mission(a.id, store, gateway, Sources(FirstLightDb(source or db), Gdelt(), TiingoNews()))
        print(json.dumps(res, default=str))
        return 0 if res["status"] == "completed" else 1
    elif a.cmd == "preflight":
        source = SDB.from_env()
        out = {"source": "RESEARCH_SOURCE_DSN not set: sources read the store database" if source is None else
               {"read_only_proof": source.proof, **SDB.assert_isolated(db, source)},
               "limits": {"monthly_usd": limits.monthly_usd, "mission_usd": limits.mission_usd, "call_usd": limits.call_usd},
               "budget": B.DbLedger(db).status(limits), "paid_calls_enabled": paid_calls_enabled(),
               "anthropic_api_key_present": bool(os.environ.get("ANTHROPIC_API_KEY")), "tiingo_key_present": bool(os.environ.get("TIINGO_API_KEY"))}
        print(json.dumps(out, default=str, indent=2))
    elif a.cmd == "show":
        m = store.get_mission(a.id)
        rep = db.execute_dict_query("SELECT markdown FROM research_report WHERE mission_id = %s ORDER BY version DESC LIMIT 1", (a.id,))
        print(json.dumps({k: m[k] for k in ("id", "kind", "title", "status", "spent_usd", "budget_usd", "error")}, default=str))
        if rep:
            print(rep[0]["markdown"])
    elif a.cmd == "budget":
        print(json.dumps({**B.DbLedger(db).status(limits), "paid_calls_enabled": paid_calls_enabled()}, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
