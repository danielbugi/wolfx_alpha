#!/usr/bin/env python3
# mechanism/check_signal_ledger_migration_preflight.py
"""
Preflight gate for applying mechanism/add_signal_ledger_tables.sql (migration 19) and
mechanism/add_strategy_identity_release_a.sql (migration 20) to a database that has never seen
either -- most importantly, production, where `signal_ledger` does not exist yet as of 2026-09-28.

Neither migration has ever been applied to the VPS, so this is a first deployment of the whole
ledger concept there, not an incremental change to existing production data. That makes every check
below very likely to come back clean -- but "very likely" is not an acceptable substitute for
actually checking on a live production database, and the partial unique index migration 20 creates
(idx_signal_ledger_one_open_position) will hard-fail mid-migration if it isn't, so failing loudly and
early here is strictly better than discovering it halfway through.

Checks, only meaningful if `signal_ledger` already exists (on a fresh target, table absence itself
is the "all clear"):
  1. Row count.
  2. Existing constraints/indexes on signal_ledger (so a re-run against a partially-migrated target
     doesn't collide with something already there under a different name).
  3. Duplicate event identities under (symbol, signal_date, direction) -- the pre-migration key.
     If the OLD UNIQUE constraint was ever bypassed (a direct INSERT, a restore, manual repair),
     duplicates here would make the new UNIQUE(symbol, strategy_id, direction, signal_date)
     constraint fail to create too.
  4. Multiple simultaneously-open positions per (symbol, direction) -- the check that actually
     matters. The OLD constraint never prevented two open rows for the same symbol/direction on
     different dates, so if that has ever happened, migration 20's partial unique index
     (idx_signal_ledger_one_open_position) will fail to create until it's resolved.

Exit 0 = safe to proceed with the migration. Exit 1 = stop, inspect, and resolve before migrating
-- never apply the migration on a non-zero exit from this script.

    python mechanism/check_signal_ledger_migration_preflight.py
"""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from shared import db  # noqa: E402


def table_exists(name: str) -> bool:
    rows = db.execute_dict_query("SELECT to_regclass(%s) AS oid", (name,))
    return rows[0]["oid"] is not None


def run() -> int:
    print("=== signal_ledger / strategy-identity migration preflight ===")

    if not table_exists("signal_ledger"):
        print("signal_ledger does not exist on this database -- first deployment, "
              "migrations 19 and 20 both apply cleanly to an empty target.")
        return 0

    problems = []

    count = db.execute_dict_query("SELECT count(*) AS n FROM signal_ledger")[0]["n"]
    print(f"1. Row count: {count}")

    constraints = db.execute_dict_query("""
        SELECT conname, pg_get_constraintdef(oid) AS def
        FROM pg_constraint WHERE conrelid = 'signal_ledger'::regclass
    """)
    print(f"2. Existing constraints on signal_ledger ({len(constraints)}):")
    for c in constraints:
        print(f"   - {c['conname']}: {c['def']}")

    indexes = db.execute_dict_query("""
        SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'signal_ledger'
    """)
    print(f"   Existing indexes on signal_ledger ({len(indexes)}):")
    for i in indexes:
        print(f"   - {i['indexname']}: {i['indexdef']}")

    if table_exists("strategies"):
        problems.append("strategies table already exists -- migration 20 may have partially run before; "
                         "inspect it before re-applying.")
        print("   WARNING: strategies table already exists.")

    dup_events = db.execute_dict_query("""
        SELECT symbol, signal_date, direction, count(*) AS n
        FROM signal_ledger
        GROUP BY symbol, signal_date, direction
        HAVING count(*) > 1
    """)
    print(f"3. Duplicate (symbol, signal_date, direction) groups: {len(dup_events)}")
    if dup_events:
        problems.append(f"{len(dup_events)} duplicate event-identity group(s) found -- "
                         "the new 4-column UNIQUE constraint will fail to create.")
        for d in dup_events[:20]:
            print(f"   - {d['symbol']} {d['signal_date']} dir={d['direction']}: {d['n']} rows")

    multi_open = db.execute_dict_query("""
        SELECT symbol, direction, count(*) AS n
        FROM signal_ledger
        WHERE status = 'open'
        GROUP BY symbol, direction
        HAVING count(*) > 1
    """)
    print(f"4. Symbols with more than one currently-open position (same symbol+direction): {len(multi_open)}")
    if multi_open:
        problems.append(f"{len(multi_open)} symbol/direction pair(s) have multiple open positions -- "
                         "idx_signal_ledger_one_open_position will fail to create until resolved.")
        for m in multi_open[:20]:
            print(f"   - {m['symbol']} dir={m['direction']}: {m['n']} open rows")

    print()
    if problems:
        print("PREFLIGHT FAILED -- do not apply the migration yet:")
        for p in problems:
            print(f"  - {p}")
        return 1

    print("PREFLIGHT PASSED -- safe to apply the migration.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
