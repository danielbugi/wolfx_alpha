"""Versioned identity for strategy variants (a parent strategy x an exit policy), recorded in the append-only `strategy_variant` table (migration 35).

A variant is the live strategy's signal plus a different EXIT rule. Its identity is (parent strategy, variant key, variant version); its definition is a canonical JSON
document whose sha256 is stored with it. Rules, all enforced here and again by the database:
  * idempotent: registering the same definition twice changes nothing (`unchanged`);
  * never edited: a changed definition under an existing key is REFUSED unless the caller explicitly asks for a new version, which then supersedes the previous one
    (v1 <- v2 <- v3, a straight line); an older definition cannot be silently re-registered either;
  * one definition, one name: the same definition under a second key is refused;
  * no interference: this module only SELECTs from `strategies` and INSERTs into `strategy_variant`. It never writes `strategies` or `signal_ledger`, so the live
    strategy identity, the ledger and Strategy Intelligence are untouched.

Pure functions (definition, hash, key) are separated from the cursor functions so the identities can be pinned by tests without a database.

CLI (read-only by default; a write needs --apply AND --confirm-database <the connected database's name>; meant for an ISOLATED research database only):
    PYTHONPATH=mechanism python -m research.strategy_variants plan     [--parent KEY --parent-version V]
    PYTHONPATH=mechanism python -m research.strategy_variants verify   --host H --dbname D --user U [--port P] [--password-env VAR]
    PYTHONPATH=mechanism python -m research.strategy_variants register --host H --dbname D --user U --apply --confirm-database D [--new-version-on-change]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence

from research import exit_policies as EP

DEF_SCHEMA = "strategy_variant_def_v1"
KIND_EXIT_POLICY = "exit_policy"
ENGINE_VERSION = "exit_policies_v1"      # bump with a deliberate change of the simulation conventions in research/exit_policies.py
DEFAULT_PARENT = ("donchian_breakout", "v1")
FIRST_VERSION = "v1"
ATR_PERIOD = 14                          # the ATR the trade plan uses (ml_training/features/price_features.py)


class VariantError(RuntimeError):
    """Base class: the registry refused, nothing was written for that variant."""


class ParentMissing(VariantError):
    """The parent strategy row does not exist (the migration that creates it has not been applied here)."""


class DefinitionDrift(VariantError):
    """The code's definition differs from the stored latest version of this key. Pass new_version_on_change=True to register it as the next version."""


class DefinitionRegressed(VariantError):
    """The definition equals an OLDER stored version; reverting silently would hide a change. Register it deliberately as a new key or review the history."""


class DuplicateDefinition(VariantError):
    """The same definition is already registered under a different key."""


@dataclass(frozen=True)
class VariantSpec:
    key: str
    version: str
    kind: str
    definition: Mapping[str, Any]
    definition_hash: str
    description: str


@dataclass(frozen=True)
class Outcome:
    key: str
    version: str
    action: str              # created | unchanged | superseded
    variant_id: int


# ---- pure -----------------------------------------------------------------------------------------------------------------------------------------------------

def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def definition_hash(definition: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(definition).encode("utf-8")).hexdigest()


def variant_key(parent_key: str, policy_name: str) -> str:
    key = f"{parent_key}__exit_{policy_name}".lower()
    if not (3 <= len(key) <= 60) or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789_" for c in key):
        raise VariantError(f"variant key {key!r} is not 3-60 chars of [a-z0-9_]")
    return key


def exit_policy_definition(policy_name: str, parent_key: str, parent_version: str) -> Dict[str, Any]:
    spec = EP.POLICIES[policy_name]
    return {
        "schema": DEF_SCHEMA,
        "kind": KIND_EXIT_POLICY,
        "parent_strategy_key": parent_key,
        "parent_strategy_version": parent_version,
        "entry": {"signal": "parent strategy signal at the close of bar s", "fill": "open of bar s+1",
                  "stop_atr_multiple_is_1R": EP.STOP_ATR, "atr_period": ATR_PERIOD},
        "exit": {"policy_name": policy_name, "policy": spec.key, "policy_version": spec.version, "params": dict(spec.params),
                 "max_hold_bars": EP.DEFAULT_MAX_HOLD, "fills": "gap-aware, stop first on a tie, levels from bars before the current one"},
        "engine": ENGINE_VERSION,
    }


def build_specs(parent_key: str = DEFAULT_PARENT[0], parent_version: str = DEFAULT_PARENT[1], policies: Optional[Sequence[str]] = None) -> List[VariantSpec]:
    out = []
    for name in (policies if policies is not None else list(EP.POLICIES)):
        d = exit_policy_definition(name, parent_key, parent_version)
        out.append(VariantSpec(variant_key(parent_key, name), FIRST_VERSION, KIND_EXIT_POLICY, d, definition_hash(d),
                               f"{parent_key} {parent_version} signals exited by {name} ({EP.POLICIES[name].identity})"))
    return out


def _vnum(version: str) -> int:
    return int(version[1:])


# ---- cursor functions (the caller owns the transaction) -------------------------------------------------------------------------------------------------------

def parent_id(cur, parent_key: str, parent_version: str) -> int:
    cur.execute("SELECT id FROM strategies WHERE strategy_key = %s AND strategy_version = %s", (parent_key, parent_version))
    row = cur.fetchone()
    if row is None:
        raise ParentMissing(f"strategy {parent_key} {parent_version} does not exist in this database")
    return row[0]


def _stored(cur, pid: int, key: str) -> List[tuple]:
    cur.execute("SELECT id, variant_version, definition_hash FROM strategy_variant WHERE parent_strategy_id = %s AND variant_key = %s", (pid, key))
    return sorted(((r[0], r[1], r[2].strip()) for r in cur.fetchall()), key=lambda r: _vnum(r[1]))


def register(cur, specs: Sequence[VariantSpec], parent_key: str = DEFAULT_PARENT[0], parent_version: str = DEFAULT_PARENT[1], *,
             new_version_on_change: bool = False) -> List[Outcome]:
    """Idempotently record each spec. Raises (without having written that variant) on drift / regression / duplicate definition; the caller rolls back the batch."""
    from psycopg2.extras import Json
    pid = parent_id(cur, parent_key, parent_version)
    out: List[Outcome] = []
    for s in specs:
        rows = _stored(cur, pid, s.key)
        cur.execute("SELECT variant_key FROM strategy_variant WHERE parent_strategy_id = %s AND definition_hash = %s", (pid, s.definition_hash))
        same_hash = [r[0] for r in cur.fetchall()]
        if any(k != s.key for k in same_hash):
            raise DuplicateDefinition(f"definition {s.definition_hash[:12]} is already registered as {sorted(set(same_hash))}")
        if not rows:
            cur.execute("INSERT INTO strategy_variant (parent_strategy_id, variant_key, variant_version, variant_kind, definition, definition_hash, description) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id",
                        (pid, s.key, FIRST_VERSION, s.kind, Json(dict(s.definition)), s.definition_hash, s.description))
            got = cur.fetchone()
            if got is None:       # a concurrent registration won: it must be the same definition
                rows = _stored(cur, pid, s.key)
                if not rows or rows[-1][2] != s.definition_hash:
                    raise DefinitionDrift(f"{s.key}: a concurrent registration stored a different definition")
                out.append(Outcome(s.key, rows[-1][1], "unchanged", rows[-1][0]))
            else:
                out.append(Outcome(s.key, FIRST_VERSION, "created", got[0]))
            continue
        latest_id, latest_ver, latest_hash = rows[-1]
        if latest_hash == s.definition_hash:
            out.append(Outcome(s.key, latest_ver, "unchanged", latest_id))
            continue
        if any(h == s.definition_hash for _, _, h in rows):
            raise DefinitionRegressed(f"{s.key}: the definition equals an older version, not the latest ({latest_ver})")
        if not new_version_on_change:
            raise DefinitionDrift(f"{s.key} {latest_ver}: stored definition {latest_hash[:12]} differs from the code's {s.definition_hash[:12]}; "
                                  f"a changed definition needs a new version (new_version_on_change=True)")
        nxt = f"v{_vnum(latest_ver) + 1}"
        cur.execute("INSERT INTO strategy_variant (parent_strategy_id, variant_key, variant_version, variant_kind, definition, definition_hash, supersedes_variant_id, description) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                    (pid, s.key, nxt, s.kind, Json(dict(s.definition)), s.definition_hash, latest_id, s.description))
        out.append(Outcome(s.key, nxt, "superseded", cur.fetchone()[0]))
    return out


def verify(cur, parent_key: str = DEFAULT_PARENT[0], parent_version: str = DEFAULT_PARENT[1], specs: Optional[Sequence[VariantSpec]] = None) -> List[str]:
    """Read-only audit. Returns problems (empty = healthy): stored hash != recomputed hash, broken lineage, parent echo mismatch, and code drift of the latest version."""
    problems: List[str] = []
    pid = parent_id(cur, parent_key, parent_version)
    cur.execute("SELECT id, variant_key, variant_version, definition, definition_hash, supersedes_variant_id FROM strategy_variant WHERE parent_strategy_id = %s", (pid,))
    rows = cur.fetchall()
    by_key: Dict[str, List[tuple]] = {}
    for r in rows:
        rid, key, ver, definition, h, sup = r
        by_key.setdefault(key, []).append(r)
        if definition_hash(definition) != h.strip():
            problems.append(f"{key} {ver}: stored hash does not match the stored definition")
        if definition.get("parent_strategy_key") != parent_key or definition.get("parent_strategy_version") != parent_version:
            problems.append(f"{key} {ver}: definition names a different parent")
    for key, rs in by_key.items():
        rs.sort(key=lambda r: _vnum(r[2]))
        if [r[2] for r in rs] != [f"v{i}" for i in range(1, len(rs) + 1)]:
            problems.append(f"{key}: versions are not contiguous from v1")
        for prev, cur_r in zip(rs, rs[1:]):
            if cur_r[5] != prev[0]:
                problems.append(f"{key} {cur_r[2]}: does not supersede {prev[2]}")
    if specs is not None:
        for s in specs:
            rs = by_key.get(s.key)
            if rs and rs[-1][4].strip() != s.definition_hash:
                problems.append(f"{s.key}: the code's definition differs from the latest stored version {rs[-1][2]} (code drift)")
    return problems


# ---- CLI ------------------------------------------------------------------------------------------------------------------------------------------------------

def _connect(a):
    import psycopg2
    pw = os.environ.get(a.password_env) if a.password_env else os.environ.get("DB_PASSWORD")
    return psycopg2.connect(host=a.host, port=a.port, dbname=a.dbname, user=a.user, password=pw)


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="research.strategy_variants")
    p.add_argument("command", choices=["plan", "verify", "register"])
    p.add_argument("--parent", default=DEFAULT_PARENT[0])
    p.add_argument("--parent-version", default=DEFAULT_PARENT[1])
    p.add_argument("--host"), p.add_argument("--dbname"), p.add_argument("--user")
    p.add_argument("--port", default="5432")
    p.add_argument("--password-env", default=None)
    p.add_argument("--apply", action="store_true")
    p.add_argument("--confirm-database", default=None)
    p.add_argument("--new-version-on-change", action="store_true")
    a = p.parse_args(argv)
    specs = build_specs(a.parent, a.parent_version)
    if a.command == "plan":
        for s in specs:
            print(f"{s.key} {s.version} {s.definition_hash}")
        return 0
    if not (a.host and a.dbname and a.user):
        print("usage: --host --dbname --user are required", file=sys.stderr)
        return 2
    try:
        conn = _connect(a)
    except Exception as e:  # noqa: BLE001
        print(f"database connection failed: {type(e).__name__}", file=sys.stderr)
        return 3
    try:
        cur = conn.cursor()
        if a.command == "verify":
            conn.set_session(readonly=True)
            problems = verify(cur, a.parent, a.parent_version, specs)
            for x in problems:
                print("PROBLEM", x)
            print("OK" if not problems else f"{len(problems)} problem(s)")
            return 0 if not problems else 1
        if not a.apply:
            print("dry run: nothing written (add --apply --confirm-database NAME to record)")
            return 0
        if a.confirm_database != a.dbname:
            print("refused: --confirm-database must equal --dbname (writes are for an isolated research database only)", file=sys.stderr)
            return 2
        try:
            res = register(cur, specs, a.parent, a.parent_version, new_version_on_change=a.new_version_on_change)
            conn.commit()
        except VariantError as e:
            conn.rollback()
            print(f"refused: {e}", file=sys.stderr)
            return 1
        for o in res:
            print(o.action, o.key, o.version, o.variant_id)
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
