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


def cmd_record(a) -> int:
    import psycopg2
    from research.strategy_lab import ledger
    if not a.apply:
        print("dry run: nothing written (add --apply and --confirm-database <name>)")
        return 0
    if a.confirm_database != a.dbname:
        print("refused: --confirm-database must equal --dbname", file=sys.stderr)
        return 2
    doc = json.load(open(os.path.join(a.out, "result.json"), encoding="utf-8"))
    conn = psycopg2.connect(host=a.host, port=a.port, dbname=a.dbname, user=a.user, password=os.environ.get(a.password_env, "") if a.password_env else None)
    try:
        with conn, conn.cursor() as cur:
            out = ledger.record_run(cur, doc["spec"], data_hash=doc["data_hash"], code_ref=doc["code_ref"], run_hash=doc["run_hash"], seed=doc["seed"],
                                    result=doc["result"], report_sha256=doc["report_sha256"])
        print(json.dumps(out))
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
    c = sub.add_parser("record")
    c.add_argument("--out", required=True); c.add_argument("--host", required=True); c.add_argument("--dbname", required=True); c.add_argument("--user", required=True)
    c.add_argument("--port", default="5432"); c.add_argument("--password-env"); c.add_argument("--apply", action="store_true"); c.add_argument("--confirm-database")
    a = p.parse_args(argv)
    return {"run": cmd_run, "signals": cmd_signals, "record": cmd_record}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
