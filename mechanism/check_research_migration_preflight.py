#!/usr/bin/env python3
# mechanism/check_research_migration_preflight.py
"""
Gate and verifier for mechanism/add_research_observation_tables.sql (migration 22, Release B / B1).

    python mechanism/check_research_migration_preflight.py            # BEFORE applying
    python mechanism/check_research_migration_preflight.py --verify   # AFTER applying

Both modes read the REAL catalog definitions (columns, types, nullability, defaults, PKs, UNIQUEs, CHECKs, FKs,
indexes, trigger definitions AND enabled mode, function signature / SECURITY DEFINER / pinned search_path / body
hash / PUBLIC-execute, and the four signal_ledger lineage constraints) and compare them with the committed golden
fingerprint (mechanism/research/schema_fingerprint_22.json). Object names alone prove nothing.

Preflight (read-only): the Release A dependencies must exist, and the target must be either
  * ABSENT  -- no research table, function, index or ledger lineage constraint exists  -> OK to apply; or
  * EXACT   -- everything already matches the intended definitions (a re-apply is a no-op).
Anything else is INCOMPATIBLE: the differences are printed and the exit status is 1. `IF NOT EXISTS` in the
migration is never what decides this.

Verify (read-only): the schema must be EXACT. It also prints the row counts so an unexpected non-zero count on a
first apply is visible (something wrote before capture was activated), and the activation boundary rows.

Exit 0 = proceed / verified. Exit 1 = stop. This script never writes.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from research import schema_fingerprint as fp  # noqa: E402
from shared import db  # noqa: E402


def _dependency_problems(conn) -> list:
    cur = conn.cursor()
    problems = []
    cur.execute("SELECT to_regclass('strategies') IS NOT NULL, to_regclass('signal_ledger') IS NOT NULL")
    has_strategies, has_ledger = cur.fetchone()
    if not has_strategies:
        problems.append("`strategies` is missing -- migration 20 (add_strategy_identity_release_a.sql) must be applied first")
    else:
        cur.execute("SELECT 1 FROM strategies WHERE strategy_key = 'donchian_breakout' AND strategy_version = 'v1'")
        if not cur.fetchone():
            problems.append("`strategies` has no (donchian_breakout, v1) row")
    if not has_ledger:
        problems.append("`signal_ledger` is missing -- migration 19 must be applied first")
    else:
        cur.execute("SELECT count(*) FROM pg_attribute WHERE attrelid = to_regclass('signal_ledger') AND NOT attisdropped "
                    "AND attname IN ('observation_id', 'feature_snapshot_id', 'feature_set_version')")
        if cur.fetchone()[0] != 3:
            problems.append("`signal_ledger` lacks the lineage columns from migration 21")
    cur.close()
    return problems


def run_preflight(conn) -> int:
    print("=== research observation migration (22) PREFLIGHT ===")
    problems = _dependency_problems(conn)
    state, diffs = fp.classify(fp.fingerprint(conn))
    if state == fp.ABSENT:
        print("Target state: ABSENT -- no research object exists; a first apply is clean.")
    elif state == fp.EXACT:
        print("Target state: EXACT -- migration 22 is already applied with exactly the intended definitions "
              "(re-applying is a no-op).")
    else:
        print("Target state: INCOMPATIBLE -- research objects exist that are NOT the intended migration-22 definitions:")
        for d in diffs[:60]:
            print("   ", d)
        if len(diffs) > 60:
            print(f"    ... and {len(diffs) - 60} more")
        problems.append("existing research objects are neither absent nor exactly migration 22")
    for p in problems:
        print("BLOCKER:", p)
    print("RESULT:", "STOP" if problems else "OK to apply")
    return 1 if problems else 0


def run_verify(conn) -> int:
    print("=== research observation migration (22) VERIFY ===")
    state, diffs = fp.classify(fp.fingerprint(conn))
    if state != fp.EXACT:
        print("FAIL: schema state is", state)
        for d in diffs[:60]:
            print("FAIL:", d)
        print("RESULT: FAILED")
        return 1
    cur = conn.cursor()
    for t in fp.TABLES:
        cur.execute(f"SELECT count(*) FROM {t}")
        print(f"  {t}: {cur.fetchone()[0]} rows")
    cur.execute("SELECT strategy_id, state, effective_from_session FROM research_capture_activation "
                "ORDER BY strategy_id, effective_from_session")
    boundaries = cur.fetchall()
    print("  activation boundaries:", boundaries if boundaries else "none (Release B not active)")
    cur.close()
    print("RESULT: VERIFIED (every table/constraint/index/trigger/function matches the intended definition)")
    return 0


def main(argv) -> int:
    with db.get_sync_connection() as conn:
        try:
            return run_verify(conn) if "--verify" in argv else run_preflight(conn)
        finally:
            conn.rollback()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
