"""Command line for the strategy lab (research only).

    PYTHONPATH=mechanism python -m research.strategy_lab run     --data DIR --out DIR [--spec earnings_drift --version v1] [--code-ref SHA] [--seed N] [--boot N]
    PYTHONPATH=mechanism python -m research.strategy_lab signals --data DIR [--sessions 3]
    PYTHONPATH=mechanism python -m research.strategy_lab record  --out DIR --host H --dbname D --user U [--port P] [--password-env VAR] --apply --confirm-database D

`run` writes report.md and result.json into --out and prints one line (verdict + hashes). `record` stores the spec and the run in the append-only research ledger
(a RESEARCH database only; it needs --apply and --confirm-database naming the connected database, like the other research CLIs). The data extract comes from `extract.py`.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys


def _code_ref(given):
    if given:
        return given
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL, text=True).strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def cmd_run(a) -> int:
    from research.strategy_lab import engine, report, specs
    from research.strategy_lab.data import load_extract
    spec = specs.get_spec(a.spec, a.version)
    panel = load_extract(a.data)
    result = engine.run_backtest(spec, panel, seed=a.seed, n_boot=a.boot)
    code = _code_ref(a.code_ref)
    sh, rh = specs.spec_hash(spec), engine.run_hash(spec, panel.data_hash, code, a.seed)
    text = report.render(spec, result, spec_hash=sh, data_hash=panel.data_hash, code_ref=code, run_hash=rh, seed=a.seed, data_meta=panel.meta)
    rsha = report.report_sha256(text)
    os.makedirs(a.out, exist_ok=True)
    open(os.path.join(a.out, "report.md"), "w", encoding="utf-8", newline="\n").write(text)
    doc = {"spec": spec, "spec_hash": sh, "data_hash": panel.data_hash, "code_ref": code, "run_hash": rh, "seed": a.seed, "report_sha256": rsha, "result": result}
    json.dump(doc, open(os.path.join(a.out, "result.json"), "w", encoding="utf-8", newline="\n"), indent=1, sort_keys=True, default=str)
    print(f"STRATEGY_LAB {a.spec} {a.version} verdict={result['verdict']} run_hash={rh[:16]} report_sha256={rsha[:16]} n_signal_test={result['n_positions_by_kind_split'].get('signal|test')}")
    return 0


def cmd_signals(a) -> int:
    from research.strategy_lab import engine, specs
    from research.strategy_lab.data import load_extract
    spec = specs.get_spec(a.spec, a.version)
    rows = engine.current_candidates(spec, load_extract(a.data), a.sessions)
    print(json.dumps(rows, indent=1))
    return 0


def cmd_inflection(a) -> int:
    """The fundamental-inflection event study: runs it on the real extracts, registers the follow-up hypotheses (forward only) and writes every result, including the failures."""
    import hashlib
    from datetime import date
    from inflection import runner as IR
    from research.lab.manifest import canonical_hash
    from research.strategy_lab import engine, inflection_report as RP, inflection_study as ST, loop as LP, specs
    inp = IR.load_inputs(a.data, a.fundamentals)
    asof = date.fromisoformat(a.asof)
    code = _code_ref(a.code_ref)
    spec = specs.get_spec("fundamental_inflection", "v1")
    result = ST.run_study(spec, inp, asof, seed=a.seed, n_boot=a.boot)
    data_hash = canonical_hash({"prices": inp.meta["prices_data_hash"], "fundamentals": {k: inp.meta["fundamentals_meta"][k] for k in ("quarterly_sha256", "valuation_sha256")}})
    sh, rh = specs.spec_hash(spec), engine.run_hash(spec, data_hash, code, a.seed)
    text = RP.render_report(spec, result, spec_hash=sh, data_hash=data_hash, code_ref=code, run_hash=rh, seed=a.seed)
    rsha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    result["ledger_metrics"] = {k: result[k] for k in ("cells", "diffs", "feasibility", "counts", "followups", "horizon_followups", "decision_checks")}
    os.makedirs(a.out, exist_ok=True)
    runs = [(spec, result, text, sh, rh, rsha)]
    for f in result["followups"]:
        cs = ST.combo_spec(spec, f, a.asof)
        cres = {"verdict": "INSUFFICIENT_DATA", "ledger_metrics": {"followup": f, "status": "registered_forward_only", "decision_checks": [{"check": "evaluated on events public after " + a.asof + " only; needs 150 with a complete window", "value": 0, "ok": False}]}}
        ctext = (f"# {f['label']}\n\nRegistered forward-only on {a.asof}; no confirmation is possible on the existing test period. "
                 f"Exploratory train+validation numbers: {f['exploratory_train_validation_h20']}\n")
        runs.append((cs, cres, ctext, specs.spec_hash(cs), engine.run_hash(cs, data_hash, code, a.seed), hashlib.sha256(ctext.encode("utf-8")).hexdigest()))
    for sp, res, txt, s_h, r_h, r_sha in runs:
        d = os.path.join(a.out, f"{sp['key']}_{sp['version']}")
        os.makedirs(d, exist_ok=True)
        open(os.path.join(d, "report.md"), "w", encoding="utf-8", newline="\n").write(txt)
        json.dump({"spec": sp, "spec_hash": s_h, "data_hash": data_hash, "code_ref": code, "run_hash": r_h, "seed": a.seed, "report_sha256": r_sha, "result": res},
                  open(os.path.join(d, "result.json"), "w", encoding="utf-8", newline="\n"), indent=1, sort_keys=True, default=str)
    if a.publish_dir:
        doc = RP.lab_report(spec, result, spec_hash=sh, data_hash=data_hash, code_ref=code, run_hash=rh, report_sha=rsha, run_date=a.run_date, extract=inp.meta["fundamentals_meta"])
        os.makedirs(a.publish_dir, exist_ok=True)
        open(os.path.join(a.publish_dir, "fundamental_inflection_research.json"), "w", encoding="utf-8", newline="\n").write(json.dumps(doc, indent=1, sort_keys=True, default=str))
        idx_path = os.path.join(a.publish_dir, "index.json")
        idx = json.load(open(idx_path, encoding="utf-8")) if os.path.exists(idx_path) else {"schema": "lab_index_v1", "reports": []}
        idx["reports"] = [e for e in idx["reports"] if e.get("id") != "fundamental_inflection_research"] + [{"id": "fundamental_inflection_research", "file": "fundamental_inflection_research.json", "kind": "strategy_lab", "title": doc["title"], "headline": doc["headline"]}]
        open(idx_path, "w", encoding="utf-8", newline="\n").write(json.dumps(idx, indent=1, default=str))
        print("published:", os.path.join(a.publish_dir, "fundamental_inflection_research.json"))
    print(f"STRATEGY_LAB fundamental_inflection v1 verdict={result['verdict']} run_hash={rh[:16]} report_sha256={rsha[:16]} followups={len(result['followups'])} horizon_leads={len(result['horizon_followups'])}")
    return 0


def cmd_loop(a) -> int:
    from research.strategy_lab import loop
    from research.strategy_lab.data import load_extract
    panel = load_extract(a.data)
    code = _code_ref(a.code_ref)
    result = loop.run_loop(panel, out_dir=a.out, code_ref=code, seed=a.seed, n_boot=a.boot, run_date=a.run_date)
    if a.publish_dir:
        print("published:", loop.publish(result, extract_meta=panel.meta, directory=a.publish_dir))
    for e in result["experiments"]:
        print(f"STRATEGY_LAB {e['key']} {e['version']} verdict={e['verdict']} n_test={e['n_test']} test_r={e['test_expectancy_r']}")
    print(f"STRATEGY_LAB loop next_steps={len(result['next_steps'])} derived={'yes' if result['derived_hypothesis'] else 'no'}")
    return 0


def cmd_record(a) -> int:
    import psycopg2
    from research.strategy_lab import ledger
    if not a.apply:
        print("dry run: nothing written (add --apply and --confirm-database <name>)")
        return 0
    if a.confirm_database != a.dbname:
        print("refused: --confirm-database must equal --dbname", file=sys.stderr)
        return 2
    files = sorted(os.path.join(root, f) for root, _, names in os.walk(a.out) for f in names if f == "result.json")
    if not files:
        print("no result.json under --out", file=sys.stderr)
        return 2
    conn = psycopg2.connect(host=a.host, port=a.port, dbname=a.dbname, user=a.user, password=os.environ.get(a.password_env, "") if a.password_env else None)
    try:
        # parents before children: a child's spec row references nothing, but the order keeps the ledger readable
        docs = [json.load(open(f, encoding="utf-8")) for f in files]
        docs.sort(key=lambda d: (1 if d["spec"].get("parent") else 0, d["spec"]["key"], d["spec"]["version"]))
        with conn, conn.cursor() as cur:
            for doc in docs:
                out = ledger.record_run(cur, doc["spec"], data_hash=doc["data_hash"], code_ref=doc["code_ref"], run_hash=doc["run_hash"], seed=doc["seed"],
                                        result=doc["result"], report_sha256=doc["report_sha256"])
                print(json.dumps({"strategy": f"{doc['spec']['key']} {doc['spec']['version']}", "verdict": doc["result"]["verdict"], "created": out["created"]}))
    finally:
        conn.close()
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="research.strategy_lab")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--data", required=True); r.add_argument("--out", required=True)
    r.add_argument("--spec", default="earnings_drift"); r.add_argument("--version", default="v1")
    r.add_argument("--code-ref"); r.add_argument("--seed", type=int, default=20261031); r.add_argument("--boot", type=int, default=1000)
    s = sub.add_parser("signals")
    s.add_argument("--data", required=True); s.add_argument("--sessions", type=int, default=3)
    s.add_argument("--spec", default="earnings_drift"); s.add_argument("--version", default="v1")
    lp = sub.add_parser("loop")
    lp.add_argument("--data", required=True); lp.add_argument("--out", required=True); lp.add_argument("--code-ref"); lp.add_argument("--seed", type=int, default=20261031)
    lp.add_argument("--boot", type=int, default=1000); lp.add_argument("--run-date", default=""); lp.add_argument("--publish-dir")
    ip = sub.add_parser("inflection")
    ip.add_argument("--data", required=True, help="strategy-lab price/earnings extract"); ip.add_argument("--fundamentals", required=True, help="fundamentals extract")
    ip.add_argument("--out", required=True); ip.add_argument("--asof", required=True); ip.add_argument("--code-ref"); ip.add_argument("--seed", type=int, default=20261031)
    ip.add_argument("--boot", type=int, default=1000); ip.add_argument("--run-date", default=""); ip.add_argument("--publish-dir")
    c = sub.add_parser("record")
    c.add_argument("--out", required=True); c.add_argument("--host", required=True); c.add_argument("--dbname", required=True); c.add_argument("--user", required=True)
    c.add_argument("--port", default="5432"); c.add_argument("--password-env"); c.add_argument("--apply", action="store_true"); c.add_argument("--confirm-database")
    a = p.parse_args(argv)
    return {"run": cmd_run, "signals": cmd_signals, "loop": cmd_loop, "inflection": cmd_inflection, "record": cmd_record}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
