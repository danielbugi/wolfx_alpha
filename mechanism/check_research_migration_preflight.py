#!/usr/bin/env python3
# mechanism/check_research_migration_preflight.py
"""
Gate and verifier for mechanism/add_research_observation_tables.sql (migration 22, Release B / B1).

    python mechanism/check_research_migration_preflight.py            # BEFORE applying: is the target ready?
    python mechanism/check_research_migration_preflight.py --verify   # AFTER applying: is the schema really there?

Preflight (read-only) checks that the migration's dependencies exist -- `strategies` with the Donchian v1 row
and `signal_ledger` (migrations 19/20) -- and reports whether any research table already exists (a re-apply is
safe because every statement is IF NOT EXISTS, but you should know it is a re-apply, not a first apply).

Verify (read-only) is the "verified by a real query afterward" step the migration policy requires: every table,
the identity keys, the session CHECKs, and all eight immutability/append-only triggers must be present, and the
immutable tables must still hold exactly the rows the operator expects (it prints the counts; an unexpected
non-zero count on a first apply means something wrote before capture was activated).

Exit 0 = proceed / verified. Exit 1 = stop. This script never writes.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from shared import db  # noqa: E402

TABLES = ["feature_set_registry", "feature_snapshot", "candidate_observation", "candidate_capture_run",
          "research_maintenance_log", "research_maintenance_audit"]
CONSTRAINTS = ["feature_snapshot_identity_key", "feature_snapshot_bar_is_session",
               "candidate_observation_identity_key", "candidate_observation_bar_is_session"]
TRIGGERS = ["candidate_observation_immutable_row", "candidate_observation_immutable_truncate",
            "feature_snapshot_immutable_row", "feature_snapshot_immutable_truncate",
            "feature_set_registry_immutable_row", "feature_set_registry_immutable_truncate",
            "research_maintenance_audit_append_only_row", "research_maintenance_audit_append_only_truncate"]


def _exists(name: str) -> bool:
    return db.execute_dict_query("SELECT to_regclass(%s) AS oid", (name,))[0]["oid"] is not None


def preflight() -> int:
    print("=== research observation migration (22) PREFLIGHT ===")
    problems = []
    if not _exists("strategies"):
        problems.append("`strategies` is missing -- migration 20 (add_strategy_identity_release_a.sql) must be applied first")
    elif not db.execute_dict_query(
            "SELECT 1 FROM strategies WHERE strategy_key = 'donchian_breakout' AND strategy_version = 'v1'"):
        problems.append("`strategies` has no (donchian_breakout, v1) row")
    if not _exists("signal_ledger"):
        problems.append("`signal_ledger` is missing -- migration 19 must be applied first")
    present = [t for t in TABLES if _exists(t)]
    if present:
        print(f"NOTE: research tables already present: {present} -- this would be a re-apply (idempotent).")
    else:
        print("No research table exists yet -- first apply.")
    for p in problems:
        print("BLOCKER:", p)
    print("RESULT:", "STOP" if problems else "OK to apply")
    return 1 if problems else 0


def verify() -> int:
    print("=== research observation migration (22) VERIFY ===")
    problems = []
    for t in TABLES:
        if not _exists(t):
            problems.append(f"table missing: {t}")
    if problems:
        for p in problems:
            print("FAIL:", p)
        return 1
    have_c = {r["conname"] for r in db.execute_dict_query(
        "SELECT conname FROM pg_constraint WHERE conrelid = ANY(%s::regclass[])", (["feature_snapshot", "candidate_observation"],))}
    problems += [f"constraint missing: {c}" for c in CONSTRAINTS if c not in have_c]
    have_t = {r["tgname"] for r in db.execute_dict_query(
        "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal")}
    problems += [f"trigger missing: {t}" for t in TRIGGERS if t not in have_t]
    for t in TABLES:
        n = db.execute_dict_query(f"SELECT count(*) AS n FROM {t}")[0]["n"]
        print(f"  {t}: {n} rows")
    for p in problems:
        print("FAIL:", p)
    print("RESULT:", "FAILED" if problems else "VERIFIED")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(verify() if "--verify" in sys.argv[1:] else preflight())
