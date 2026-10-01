"""Catalog fingerprint of migration 22 (Release B / B1): the real definitions, not object names.

`fingerprint(conn)` reads, from the live catalog of the schema the research tables live in:
  * every column (type via format_type, NOT NULL, default expression),
  * every constraint (PK / UNIQUE / CHECK / FK definition via pg_get_constraintdef),
  * every index (pg_get_indexdef), every trigger (pg_get_triggerdef AND its enabled mode),
  * every function (identity signature, return type, language, SECURITY DEFINER, whether search_path is pinned,
    whether PUBLIC may execute it, a sha256 of its body) and the migration marker comment,
  * the four lineage constraints migration 22 adds to signal_ledger.
`schema_fingerprint_22.json` is the committed golden copy (generated from a fresh apply on Postgres 16, the
production major). `classify` turns a live fingerprint into ABSENT / EXACT / INCOMPATIBLE, which is what the
preflight and verify steps decide on -- an `IF NOT EXISTS` that quietly accepted a look-alike object can never
pass this. Read-only; schema-name independent (the schema qualifier is stripped from every definition).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

MARKER = "release_b_migration_22"
GOLDEN_PATH = os.path.join(os.path.dirname(__file__), "schema_fingerprint_22.json")

TABLES = ("feature_set_registry", "candidate_capture_run", "research_capture_activation",
          "research_maintenance_log", "research_maintenance_session", "research_maintenance_audit",
          "feature_snapshot", "candidate_observation")
FUNCTIONS = ("research_guard_immutable()", "research_audit_append_only()", "research_maintenance_log_guard()",
             "research_maintenance_open(text,text,integer)", "research_maintenance_approve(bigint)",
             "research_maintenance_begin(bigint)", "research_maintenance_close(bigint)",
             "research_capture_set_state(bigint,text,date,text)")
LEDGER_CONSTRAINTS = ("signal_ledger_observation_fk", "signal_ledger_snapshot_fk", "signal_ledger_feature_set_fk",
                      "signal_ledger_lineage_all_or_none")
INDEXES = ("idx_candidate_capture_run_session", "idx_feature_snapshot_session", "idx_candidate_observation_session",
           "idx_candidate_observation_tracked", "idx_candidate_observation_snapshot",
           "idx_research_maintenance_session_txid")

ABSENT, EXACT, INCOMPATIBLE = "ABSENT", "EXACT", "INCOMPATIBLE"


def _q(conn, sql: str, params: Tuple = ()) -> List[tuple]:
    cur = conn.cursor()
    cur.execute(sql, params)
    rows = cur.fetchall()
    cur.close()
    return rows


def _strip(text: Optional[str], schema: str) -> Optional[str]:
    if text is None:
        return None
    return re.sub(r'(?:"%s"|%s)\.' % (re.escape(schema), re.escape(schema)), "", text)


def _schema_of(conn, table: str) -> Optional[str]:
    rows = _q(conn, "SELECT n.nspname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE c.oid = to_regclass(%s)", (table,))
    return rows[0][0] if rows else None


def _table_fp(conn, table: str, schema: str) -> Dict[str, Any]:
    oid = f"to_regclass('{table}')"
    comment = _q(conn, f"SELECT obj_description({oid}, 'pg_class')")[0][0]
    cols = [[n, t, bool(nn), _strip(d, schema)] for n, t, nn, d in _q(
        conn, "SELECT a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull, "
              "pg_get_expr(d.adbin, d.adrelid) FROM pg_attribute a "
              "LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum "
              f"WHERE a.attrelid = {oid} AND a.attnum > 0 AND NOT a.attisdropped ORDER BY a.attnum")]
    cons = sorted([n, ct, _strip(d, schema)] for n, ct, d in _q(
        conn, "SELECT conname, contype::text, pg_get_constraintdef(oid) FROM pg_constraint "
              f"WHERE conrelid = {oid} AND contype <> 'n'"))
    idx = sorted([n, _strip(d, schema)] for n, d in _q(
        conn, "SELECT c.relname, pg_get_indexdef(i.indexrelid) FROM pg_index i "
              f"JOIN pg_class c ON c.oid = i.indexrelid WHERE i.indrelid = {oid} AND NOT i.indisprimary"))
    trg = sorted([n, _strip(d, schema), m] for n, d, m in _q(
        conn, "SELECT tgname, pg_get_triggerdef(oid), tgenabled::text FROM pg_trigger "
              f"WHERE tgrelid = {oid} AND NOT tgisinternal"))
    return {"comment": comment, "columns": cols, "constraints": cons, "indexes": idx, "triggers": trg}


def _function_fp(conn, signature: str) -> Optional[Dict[str, Any]]:
    rows = _q(conn, "SELECT p.oid, pg_get_function_result(p.oid), l.lanname, p.prosecdef, p.proconfig, p.prosrc, "
                    "obj_description(p.oid, 'pg_proc'), p.proacl::text[] FROM pg_proc p "
                    "JOIN pg_language l ON l.oid = p.prolang WHERE p.oid = to_regprocedure(%s)", (signature,))
    if not rows:
        return None
    _, result, lang, secdef, config, src, comment, acl = rows[0]
    public_exec = True if acl is None else any(
        e.startswith("=") and "X" in e.split("/")[0] for e in acl)  # NULL acl = default = PUBLIC may execute
    pins_path = any(c.startswith("search_path=") for c in (config or []))
    return {"returns": result, "language": lang, "security_definer": bool(secdef), "pins_search_path": pins_path,
            "public_execute": public_exec, "comment": comment,
            "body_sha256": hashlib.sha256(src.replace("\r\n", "\n").encode("utf-8")).hexdigest()}


def fingerprint(conn) -> Dict[str, Any]:
    """The live definitions of everything migration 22 owns. Tables/functions that do not exist map to None."""
    schema = _schema_of(conn, "feature_set_registry") or _schema_of(conn, "candidate_observation") \
        or _schema_of(conn, "signal_ledger") or "public"
    tables = {t: (_table_fp(conn, t, schema) if _schema_of(conn, t) else None) for t in TABLES}
    functions = {f: _function_fp(conn, f) for f in FUNCTIONS}
    ledger = []
    if _schema_of(conn, "signal_ledger"):
        ledger = sorted([n, ct, _strip(d, schema)] for n, ct, d in _q(
            conn, "SELECT conname, contype::text, pg_get_constraintdef(oid) FROM pg_constraint "
                  "WHERE conrelid = to_regclass('signal_ledger') AND conname = ANY(%s)", (list(LEDGER_CONSTRAINTS),)))
    stray_indexes = sorted(i for i in INDEXES if _q(conn, "SELECT to_regclass(%s)", (i,))[0][0] is not None
                           and not any(i == x[0] for t in tables.values() if t for x in t["indexes"]))
    return {"marker": MARKER, "tables": tables, "functions": functions, "ledger_lineage": ledger,
            "stray_indexes": stray_indexes}


def load_golden() -> Dict[str, Any]:
    with open(GOLDEN_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def diff(expected: Any, actual: Any, path: str = "") -> List[str]:
    """Human-readable differences between two fingerprints (empty list = identical)."""
    if isinstance(expected, dict) and isinstance(actual, dict):
        out: List[str] = []
        for k in sorted(set(expected) | set(actual)):
            if k not in expected:
                out.append(f"{path}/{k}: unexpected (present in database, not in the migration)")
            elif k not in actual:
                out.append(f"{path}/{k}: missing from the database")
            else:
                out += diff(expected[k], actual[k], f"{path}/{k}")
        return out
    if isinstance(expected, list) and isinstance(actual, list):
        if expected == actual:
            return []
        exp_set = {json.dumps(x, sort_keys=True) for x in expected}
        act_set = {json.dumps(x, sort_keys=True) for x in actual}
        out = [f"{path}: missing {x}" for x in sorted(exp_set - act_set)]
        out += [f"{path}: unexpected {x}" for x in sorted(act_set - exp_set)]
        return out or [f"{path}: same members, different order"]
    return [] if expected == actual else [f"{path}: expected {expected!r}, found {actual!r}"]


def classify(live: Dict[str, Any], golden: Optional[Dict[str, Any]] = None) -> Tuple[str, List[str]]:
    """(ABSENT, []) when nothing of migration 22 exists; (EXACT, []) when everything matches the golden copy;
    otherwise (INCOMPATIBLE, differences)."""
    golden = golden if golden is not None else load_golden()
    nothing = (all(v is None for v in live["tables"].values()) and all(v is None for v in live["functions"].values())
               and not live["ledger_lineage"] and not live["stray_indexes"])
    if nothing:
        return ABSENT, []
    problems = diff(golden, live)
    return (EXACT, []) if not problems else (INCOMPATIBLE, problems)


if __name__ == "__main__":  # pragma: no cover -- regenerate the golden from a database that holds a FRESH apply
    import sys

    import psycopg2

    schema = sys.argv[1] if len(sys.argv) > 1 else "public"
    c = psycopg2.connect(host=os.environ["DB_HOST"], port=os.environ.get("DB_PORT", "5432"),
                         dbname=os.environ["DB_NAME"], user=os.environ["DB_USER"],
                         password=os.environ.get("DB_PASSWORD", ""), options=f"-c search_path={schema}")
    with open(GOLDEN_PATH, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(fingerprint(c), fh, indent=1, sort_keys=True)
        fh.write("\n")
    print("wrote", GOLDEN_PATH)
