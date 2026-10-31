"""Writes the strategy spec and the run record to the append-only research ledger (migration 39) on a raw psycopg2 cursor. SELECT + INSERT only; nothing here updates or deletes.

Idempotent by content: registering the same spec twice returns the stored row; a DIFFERENT definition under an existing (key, version) is refused (register a new version);
the same run (spec, data, code, split, seed, rule) is recorded once. It never touches `strategies` or `signal_ledger`.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping

from psycopg2.extras import Json

from research.strategy_lab.specs import spec_hash


class LedgerError(RuntimeError):
    pass


def register_spec(cur, spec: Mapping[str, Any]) -> Dict[str, Any]:
    h = spec_hash(spec)
    cur.execute("SELECT id, spec_hash FROM research_strategy_spec WHERE strategy_key = %s AND version = %s", (spec["key"], spec["version"]))
    row = cur.fetchone()
    if row:
        if row[1].strip() != h:
            raise LedgerError(f"{spec['key']} {spec['version']} is already registered with a different definition ({row[1][:12]} vs {h[:12]}); register a new version")
        return {"id": row[0], "created": False, "spec_hash": h}
    cur.execute("INSERT INTO research_strategy_spec (strategy_key, version, spec_hash, definition) VALUES (%s, %s, %s, %s) RETURNING id", (spec["key"], spec["version"], h, Json(dict(spec))))
    return {"id": cur.fetchone()[0], "created": True, "spec_hash": h}


def record_run(cur, spec: Mapping[str, Any], *, data_hash: str, code_ref: str, run_hash: str, seed: int, result: Mapping[str, Any], report_sha256: str) -> Dict[str, Any]:
    reg = register_spec(cur, spec)
    metrics = result.get("ledger_metrics") or {"cells": result["cells"], "controls_diff": result["controls_diff"], "by_year": result["by_year"], "decision_checks": result["decision_checks"],
               "event_accounting": result["event_accounting"], "n_positions_by_kind_split": result["n_positions_by_kind_split"]}
    cur.execute("SELECT id FROM research_strategy_run WHERE run_hash = %s", (run_hash,))
    row = cur.fetchone()
    if row:
        return {"id": row[0], "created": False, "spec": reg}
    cur.execute("INSERT INTO research_strategy_run (spec_hash, data_hash, code_ref, run_hash, split, decision_rule, seed, metrics, verdict, report_sha256) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (reg["spec_hash"], data_hash, code_ref, run_hash, Json(spec["split"]), Json(spec["decision_rule"]), seed, Json(metrics), result["verdict"], report_sha256))
    return {"id": cur.fetchone()[0], "created": True, "spec": reg}
