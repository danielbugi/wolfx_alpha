#!/usr/bin/env python3
# mechanism/validate_release_b.py
"""
Read-only validation tooling for the Release B activation runbook (stages S0-S13).

    python mechanism/validate_release_b.py schema      --expect absent|exact [--capture inactive|active|any]
                                                       [--ml-models present|absent|any]
    python mechanism/validate_release_b.py roles       --expect absent|present
    python mechanism/validate_release_b.py connections [--forbid-user trading_user]
    python mechanism/validate_release_b.py config      [--env-file PATH] [--expect-guards unset|set] [--expect-capture unset|on]
                                                       [--check-boundary-vs-db]
    python mechanism/validate_release_b.py guards      --session D --expect inert|active [--log FILE|-] [--results FILE]
    python mechanism/validate_release_b.py capture     --session E
    python mechanism/validate_release_b.py delivery    --session D

Every check prints `PASS|FAIL|WARN|INFO <name>: <detail>`; exit 0 = no FAIL, 1 = at least one FAIL (a runbook STOP
condition), 2 = bad usage / cannot connect.

This tool NEVER writes. Three independent layers enforce that, and the tests prove each one:
  1. the session is opened with `default_transaction_read_only=on` and psycopg2 `readonly=True`;
  2. every statement must start with SELECT / WITH / SHOW and contain no write keyword (anything else raises before it
     reaches the server);
  3. the transaction is always rolled back and the connection closed.
It reads database settings from the usual DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD variables. It never prints a
secret: `config --env-file` extracts only GUARDS_EFFECTIVE_FROM and RESEARCH_CAPTURE_ENABLED and ignores every other line.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
from datetime import date
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

sys.path.insert(0, os.path.dirname(__file__))

from research import schema_fingerprint as fp  # noqa: E402
from screeners import guards_boundary as gb  # noqa: E402

PASS, FAIL, WARN, INFO = "PASS", "FAIL", "WARN", "INFO"

# Created by migration 23 (mechanism/add_ml_models_registry_columns.sql); a test pins this to
# ml_training.models.momentum_predictor.REGISTRY_REQUIRED_COLUMNS so the two cannot drift.
ML_MODELS_REQUIRED_COLUMNS = ("evaluation", "feature_set_version", "target")
# The five SECURITY DEFINER entry points (the three remaining research_* functions are trigger functions).
CALLABLE_RESEARCH_FUNCTIONS = ("research_maintenance_open(text,text,integer)", "research_maintenance_approve(bigint)",
                               "research_maintenance_begin(bigint)", "research_maintenance_close(bigint)",
                               "research_capture_set_state(bigint,text,date,text)")
RESEARCH_ROLES = ("donchian_owner", "donchian_app", "donchian_research_admin")
CAPTURE_ENV_VAR = "RESEARCH_CAPTURE_ENABLED"

GUARD_MODE_RE = re.compile(r"Universe guards mode: (INERT|ACTIVE)\b[^\n]*")
GUARD_DROPPED_RE = re.compile(r"Universe guards: dropped (\d+)/(\d+) symbols")

# Runbook S8 thresholds.
UNIVERSE_MIN, UNIVERSE_MAX = 2900, 3100
MAX_DROP_FRACTION = 0.15
COLLAPSE_FRACTION = 0.5
EXPLODE_FACTOR = 2.0


# ------------------------------------------------------------------ reporting
class Report:
    def __init__(self) -> None:
        self.rows: List[Tuple[str, str, str]] = []

    def add(self, status: str, name: str, detail: str = "") -> None:
        self.rows.append((status, name, detail))

    def check(self, ok: bool, name: str, detail_ok: str, detail_bad: str) -> bool:
        self.add(PASS if ok else FAIL, name, detail_ok if ok else detail_bad)
        return ok

    @property
    def failed(self) -> bool:
        return any(s == FAIL for s, _, _ in self.rows)

    def render(self) -> str:
        lines = [f"{s:<4} {n}: {d}" if d else f"{s:<4} {n}" for s, n, d in self.rows]
        n_fail = sum(1 for s, _, _ in self.rows if s == FAIL)
        n_warn = sum(1 for s, _, _ in self.rows if s == WARN)
        lines.append(f"RESULT: {'FAILED' if n_fail else 'OK'} ({n_fail} fail, {n_warn} warn, "
                     f"{sum(1 for s, _, _ in self.rows if s == PASS)} pass)")
        return "\n".join(lines)


# ------------------------------------------------------------------ the read-only connection
class ReadOnlyViolation(RuntimeError):
    """A statement that is not a plain read was handed to the validator's connection."""


_READ_ONLY_HEAD = re.compile(r"^\s*(select|with|show)\b", re.IGNORECASE)
_WRITE_WORDS = re.compile(r"\b(insert|update|delete|truncate|create|alter|drop|grant|revoke|copy|call|do|merge|vacuum|"
                          r"reindex|cluster|refresh|lock|set|reset|nextval|setval)\b", re.IGNORECASE)


def assert_read_only_sql(sql: str) -> None:
    body = sql.strip().rstrip(";").strip()
    if not _READ_ONLY_HEAD.match(body):
        raise ReadOnlyViolation(f"refusing non-read statement: {body[:60]!r}")
    if ";" in body:
        raise ReadOnlyViolation("refusing multiple statements")
    if re.search(r"\bfor\s+(update|share|no\s+key\s+update|key\s+share)\b", body, re.IGNORECASE):
        raise ReadOnlyViolation("refusing a row-locking SELECT")
    if re.search(r"\binto\b", body, re.IGNORECASE):
        raise ReadOnlyViolation("refusing SELECT ... INTO")
    if _WRITE_WORDS.search(re.sub(r"'(?:[^']|'')*'", "''", body)):
        raise ReadOnlyViolation("refusing a statement containing a write keyword (e.g. a data-modifying CTE)")


class ReadOnlyDB:
    """psycopg2 connection that can only read. `q` is the sole way the checks reach the server."""

    def __init__(self, conn) -> None:
        self.conn = conn

    @classmethod
    def connect(cls, env: Optional[Dict[str, str]] = None) -> "ReadOnlyDB":
        import psycopg2
        e = os.environ if env is None else env
        conn = psycopg2.connect(host=e["DB_HOST"], port=e.get("DB_PORT", "5432"), dbname=e["DB_NAME"], user=e["DB_USER"],
                                password=e["DB_PASSWORD"], options="-c default_transaction_read_only=on",
                                application_name="validate_release_b", connect_timeout=15)
        conn.set_session(readonly=True, autocommit=False)
        return cls(conn)

    def q(self, sql: str, params: Sequence = ()) -> List[tuple]:
        assert_read_only_sql(sql)
        cur = self.conn.cursor()
        try:
            cur.execute(sql, tuple(params))
            return cur.fetchall()
        finally:
            cur.close()

    def scalar(self, sql: str, params: Sequence = ()):
        rows = self.q(sql, params)
        return rows[0][0] if rows else None

    def close(self) -> None:
        try:
            self.conn.rollback()
        finally:
            self.conn.close()


# ------------------------------------------------------------------ schema (S0 / S2 / S4 / S5 / capture inactivity)
def check_schema(db: ReadOnlyDB, rep: Report, expect: str, capture: str = "any", ml_models: str = "present") -> None:
    ro = db.scalar("SHOW transaction_read_only")
    rep.check(ro == "on", "session.read_only", "transaction_read_only=on", f"transaction_read_only={ro!r}")

    state, diffs = fp.classify(fp.fingerprint(db.conn))
    want = fp.ABSENT if expect == "absent" else fp.EXACT
    if state == want:
        rep.add(PASS, "migration22.state", f"{state} (as expected)")
    else:
        rep.add(FAIL, "migration22.state", f"{state}, expected {want}")
        for d in diffs[:25]:
            rep.add(FAIL, "migration22.diff", d)

    have = {r[0] for r in db.q("SELECT attname FROM pg_attribute WHERE attrelid = to_regclass('ml_models') AND attnum > 0 "
                                "AND NOT attisdropped AND attname = ANY(%s)", (list(ML_MODELS_REQUIRED_COLUMNS),))}
    missing = [c for c in ML_MODELS_REQUIRED_COLUMNS if c not in have]
    if ml_models == "present":
        rep.check(not missing, "migration23.ml_models_columns", "evaluation, feature_set_version, target present",
                  f"ml_models lacks {missing} (migration 23 not applied; register_model would fail closed)")
    elif ml_models == "absent":
        rep.check(not have, "migration23.ml_models_columns", "none of the 3 columns exist yet (migration 23 not applied)",
                  f"migration 23 columns already present: {sorted(have)}")
    else:
        rep.add(INFO, "migration23.ml_models_columns", f"present: {sorted(have)}")

    if expect != "exact":
        return
    non_always = db.q(
        "SELECT c.relname, t.tgname, t.tgenabled FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
        "WHERE c.oid = ANY(%s::regclass[]) AND NOT t.tgisinternal AND t.tgenabled <> 'A'", (list(fp.TABLES),))
    total = db.scalar("SELECT count(*) FROM pg_trigger t WHERE t.tgrelid = ANY(%s::regclass[]) AND NOT t.tgisinternal",
                      (list(fp.TABLES),))
    rep.check(total > 0 and not non_always, "migration22.triggers_always",
              f"{total} triggers, all ENABLE ALWAYS", f"{len(non_always)} not ALWAYS (of {total}): {non_always[:5]}")

    counts = {t: db.scalar(f"SELECT count(*) FROM {t}") for t in fp.TABLES}
    for t, n in counts.items():
        rep.add(INFO, f"rows.{t}", str(n))
    boundaries = db.q("SELECT strategy_id, state, effective_from_session FROM research_capture_activation "
                      "ORDER BY strategy_id, effective_from_session")
    if capture == "inactive":
        empty = [t for t in ("research_capture_activation", "candidate_capture_run", "candidate_observation",
                             "feature_snapshot") if counts[t]]
        rep.check(not empty, "capture.inactive", "no boundary, run, observation or snapshot rows",
                  f"unexpected rows (something wrote before activation): {empty}")
    elif capture == "active":
        ok = len(boundaries) == 1 and boundaries[0][1] == "enabled"
        rep.check(ok, "capture.active", f"exactly one enabled boundary: {boundaries[0][2]}" if ok else "",
                  f"expected exactly one 'enabled' boundary, found {boundaries}")
    else:
        rep.add(INFO, "capture.boundaries", str(boundaries) if boundaries else "none (Release B not active)")

    lineage_bad = db.scalar(
        "SELECT count(*) FROM signal_ledger WHERE (observation_id IS NULL) <> (feature_snapshot_id IS NULL) "
        "OR (observation_id IS NULL) <> (feature_set_version IS NULL)")
    rep.check(lineage_bad == 0, "ledger.lineage_all_or_none", "0 rows violate all-or-none lineage",
              f"{lineage_bad} signal_ledger rows have partial lineage")


# ------------------------------------------------------------------ roles (S9 / S9b)
def check_roles(db: ReadOnlyDB, rep: Report, expect: str, names: Optional[Dict[str, str]] = None) -> None:
    """`names` maps the canonical role names to the ones actually installed (tests use throwaway names)."""
    n = names or {r: r for r in RESEARCH_ROLES}
    owner, app, group = n["donchian_owner"], n["donchian_app"], n["donchian_research_admin"]
    rows = {r[0]: r for r in db.q(
        "SELECT rolname, rolsuper, rolcanlogin, rolcreaterole, rolcreatedb, rolreplication, rolbypassrls "
        "FROM pg_roles WHERE rolname = ANY(%s)", (list(n.values()),))}
    if expect == "absent":
        rep.check(not rows, "roles.absent", "no donchian_* role exists (services still run as the legacy user)",
                  f"roles already exist: {sorted(rows)}")
        return

    missing = [r for r in (owner, app, group) if r not in rows]
    if missing:
        rep.add(FAIL, "roles.present", f"missing roles: {missing}")
        return
    rep.add(PASS, "roles.present", ", ".join((owner, app, group)))
    for name, want_login in ((owner, False), (app, True), (group, False)):
        _, sup, login, crole, cdb, repl, byp = rows[name]
        rep.check(not (sup or crole or cdb or repl or byp), f"roles.{name}.no_privileged_attributes",
                  "no SUPERUSER/CREATEROLE/CREATEDB/REPLICATION/BYPASSRLS",
                  f"privileged attribute set: super={sup} createrole={crole} createdb={cdb} replication={repl} bypassrls={byp}")
        rep.check(login == want_login, f"roles.{name}.login", f"rolcanlogin={login}", f"rolcanlogin={login}, expected {want_login}")

    for grp in (owner, group):
        member = db.scalar("SELECT pg_has_role(%s, %s, 'member')", (app, grp))
        rep.check(not member, f"roles.app_not_in.{grp}", f"{app} is not a member",
                  f"{app} is a member of {grp} (it would inherit that authority)")
    admins = [r[0] for r in db.q(
        "SELECT m.rolname FROM pg_auth_members am JOIN pg_roles g ON g.oid = am.roleid JOIN pg_roles m ON m.oid = am.member "
        "WHERE g.rolname = %s ORDER BY 1", (group,))]
    rep.add(INFO, "roles.admin_group_members", ", ".join(admins) if admins else "none yet (S9b not done)")
    if len(admins) == 1:
        rep.add(WARN, "roles.admin_group_members", "only one approver; the maintenance hatch needs two distinct people")

    wrong_owner = db.q(
        "SELECT c.relname, pg_get_userbyid(c.relowner) FROM pg_class c WHERE c.oid = ANY(%s::regclass[]) "
        "AND pg_get_userbyid(c.relowner) <> %s", (list(fp.TABLES), owner))
    wrong_fn_owner = db.q(
        "SELECT p.oid::regprocedure::text FROM pg_proc p WHERE p.oid = ANY(%s::regprocedure[]) "
        "AND pg_get_userbyid(p.proowner) <> %s", (list(fp.FUNCTIONS), owner))
    rep.check(not wrong_owner and not wrong_fn_owner, "roles.research_objects_owned_by_owner",
              f"8 tables + 8 functions owned by {owner}", f"wrong owner: {wrong_owner} {wrong_fn_owner}")
    owns = db.q("SELECT relname FROM pg_class WHERE relowner = (SELECT oid FROM pg_roles WHERE rolname = %s) LIMIT 5", (app,))
    rep.check(not owns, "roles.app_owns_nothing", f"{app} owns no relation", f"{app} owns {owns}")

    check_app_privileges(db, rep, app, group)


def check_app_privileges(db: ReadOnlyDB, rep: Report, app: str = "donchian_app", group: str = "donchian_research_admin") -> None:
    """The P3 coverage proof, run from the outside via the catalog privilege functions (no role switch, no write)."""
    def tp(oid, priv):
        return db.scalar("SELECT has_table_privilege(%s, %s::oid, %s)", (app, oid, priv))

    research = tuple(fp.TABLES)
    bad: List[str] = []
    nrel = 0
    rels = db.q(
        "SELECT c.oid, c.relname, c.relkind FROM pg_class c WHERE c.relnamespace = to_regnamespace(current_schema()) "
        "AND c.relkind IN ('r','p','v','m','S') AND c.relname <> ALL(%s) "
        "AND NOT (c.relkind = 'S' AND EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid = c.oid AND d.deptype IN ('a','i') "
        "          AND d.refobjid = ANY(%s::regclass[]))) ORDER BY c.relname", (list(research), list(research)))
    for oid, name, kind in rels:
        nrel += 1
        if kind == "S":
            if not all(db.scalar("SELECT has_sequence_privilege(%s, %s::oid, %s)", (app, oid, p))
                       for p in ("USAGE", "SELECT", "UPDATE")):
                bad.append(f"sequence {name}: needs USAGE/SELECT/UPDATE")
        elif kind in ("v", "m"):
            if not tp(oid, "SELECT"):
                bad.append(f"view {name}: no SELECT")
            for p in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
                if tp(oid, p):
                    bad.append(f"view {name}: has {p}")
        else:
            for p in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                if not tp(oid, p):
                    bad.append(f"table {name}: lacks {p}")
            for p in ("TRUNCATE", "TRIGGER", "REFERENCES"):
                if tp(oid, p):
                    bad.append(f"table {name}: has {p}")
    rep.check(not bad, "privileges.runtime_coverage", f"{nrel} relations reachable with the intended privileges",
              f"{len(bad)} problems, e.g. {bad[:8]}")

    fns = db.q("SELECT p.oid, p.oid::regprocedure::text FROM pg_proc p WHERE p.pronamespace = to_regnamespace(current_schema()) "
               "AND p.prokind IN ('f','p') AND p.proname NOT LIKE %s", ("research\\_%",))
    no_exec = [sig for oid, sig in fns if not db.scalar("SELECT has_function_privilege(%s, %s::oid, 'EXECUTE')", (app, oid))]
    rep.check(not no_exec, "privileges.functions", f"{len(fns)} non-research functions executable", f"cannot EXECUTE {no_exec[:5]}")

    maint = [sig for sig in CALLABLE_RESEARCH_FUNCTIONS
             if db.scalar("SELECT has_function_privilege(%s, %s::regprocedure, 'EXECUTE')", (app, sig))
             or db.scalar("SELECT has_function_privilege('public', %s::regprocedure, 'EXECUTE')", (sig,))]
    rep.check(not maint, "privileges.maintenance_functions_denied",
              "neither the runtime role nor PUBLIC can execute a maintenance / set_state function",
              f"executable by the runtime role or PUBLIC: {maint}")
    no_admin = [sig for sig in CALLABLE_RESEARCH_FUNCTIONS
                if not db.scalar("SELECT has_function_privilege(%s, %s::regprocedure, 'EXECUTE')", (group, sig))]
    rep.check(not no_admin, "privileges.admin_can_execute", "the admin group can execute all five",
              f"admin group cannot execute {no_admin}")

    imm = [f"{t}:{p}" for t in fp.TABLES for p in ("UPDATE", "DELETE", "TRUNCATE")
           if db.scalar("SELECT has_table_privilege(%s, %s::regclass, %s)", (app, t, p))]
    rep.check(not imm, "privileges.research_tables_read_insert_only", "no UPDATE/DELETE/TRUNCATE on any research table",
              f"mutating privileges: {imm}")

    rep.check(bool(db.scalar("SELECT has_schema_privilege(%s, current_schema(), 'USAGE')", (app,))),
              "privileges.schema_usage", "USAGE on schema", "no USAGE on the schema")
    rep.check(not db.scalar("SELECT has_schema_privilege(%s, current_schema(), 'CREATE')", (app,)),
              "privileges.no_schema_create", "no CREATE on schema", "the runtime role (or PUBLIC) may CREATE in the schema")
    rep.check(not db.scalar("SELECT has_database_privilege(%s, current_database(), 'CREATE')", (app,)),
              "privileges.no_database_create", "no CREATE on database", "the runtime role may CREATE schemas")
    rep.check(bool(db.scalar("SELECT has_database_privilege(%s, current_database(), 'CONNECT')", (app,))),
              "privileges.connect", "may CONNECT", "cannot CONNECT")


# ------------------------------------------------------------------ connections (S10 / S11)
def check_connections(db: ReadOnlyDB, rep: Report, forbid_user: Optional[str], allow_app: str) -> None:
    sees_all = db.scalar("SELECT rolsuper OR pg_has_role(current_user, 'pg_read_all_stats', 'member') "
                         "FROM pg_roles WHERE rolname = current_user")
    if not sees_all:
        # a runtime role sees other users' sessions with their identity hidden: "no trading_user connections" would pass falsely
        rep.add(FAIL, "connections.visibility", "this connection cannot see other users' sessions (needs a superuser or "
                "pg_read_all_stats); run the tool with the bootstrap identity, not the runtime role")
        return
    rows =db.q("SELECT usename, application_name, count(*) FROM pg_stat_activity WHERE datname = current_database() "
                "AND pid <> pg_backend_pid() AND usename IS NOT NULL GROUP BY 1, 2 ORDER BY 1, 2")
    for u, a, n in rows:
        rep.add(INFO, "connection", f"user={u} application_name={a or '-'} count={n}")
    if forbid_user:
        allowed = re.compile(allow_app)
        offenders = [(u, a, n) for u, a, n in rows if u == forbid_user and not allowed.search(a or "")]
        rep.check(not offenders, f"connections.no_{forbid_user}", f"no service connections as {forbid_user}",
                  f"{forbid_user} still used by {offenders}")


# ------------------------------------------------------------------ config (S7 / S12): env values only
def read_env_file(path: str, keys: Iterable[str]) -> Dict[str, List[str]]:
    """Values of ONLY the wanted keys (a list per key, to detect duplicates). Other lines are never kept."""
    wanted = set(keys)
    found: Dict[str, List[str]] = {k: [] for k in wanted}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            if k in wanted:
                found[k].append(v.strip().strip('"').strip("'"))
    return found


def check_config(rep: Report, values: Dict[str, List[str]], expect_guards: Optional[str], expect_capture: Optional[str],
                 latest_session: Optional[date] = None) -> Optional[date]:
    g = values.get(gb.ENV_VAR, [])
    if len(g) > 1:
        rep.add(FAIL, "config.guards.duplicate", f"{gb.ENV_VAR} appears {len(g)} times")
    boundary: Optional[date] = None
    try:
        boundary = gb.parse_effective_from(g[-1] if g else None)
        rep.add(INFO, "config.guards.value", f"{gb.ENV_VAR}={boundary}" if boundary else f"{gb.ENV_VAR} is unset (guards INERT)")
    except gb.GuardsBoundaryError as e:
        rep.add(FAIL, "config.guards.valid", f"{e} -- the screener would refuse to start")
    if expect_guards == "unset":
        rep.check(boundary is None and not g, "config.guards.expected_unset", "unset", f"set: {g}")
    elif expect_guards == "set":
        rep.check(boundary is not None, "config.guards.expected_set", f"{boundary}", "unset or invalid")
    if boundary is not None and latest_session is not None:
        rep.check(boundary > latest_session, "config.guards.boundary_in_future",
                  f"{boundary} is after the latest completed session {latest_session}",
                  f"{boundary} <= latest completed session {latest_session}: the boundary must be a session that has not started")

    c = values.get(CAPTURE_ENV_VAR, [])
    if len(c) > 1:
        rep.add(FAIL, "config.capture.duplicate", f"{CAPTURE_ENV_VAR} appears {len(c)} times")
    cval = c[-1] if c else None
    rep.add(INFO, "config.capture.value", f"{CAPTURE_ENV_VAR}={cval}" if cval is not None else f"{CAPTURE_ENV_VAR} is unset")
    if cval is not None and cval not in ("0", "1", ""):
        rep.add(WARN, "config.capture.value", f"unrecognised value {cval!r}")
    on = cval is not None and cval.strip().lower() in ("1", "true", "yes", "on")
    if expect_capture == "unset":
        rep.check(not on, "config.capture.expected_off", "capture kill switch is off", f"{CAPTURE_ENV_VAR} is on")
    elif expect_capture == "on":
        rep.check(on, "config.capture.expected_on", "capture kill switch is on", f"{CAPTURE_ENV_VAR} is not on")
    return boundary


# ------------------------------------------------------------------ guards (S6 / S8)
def parse_guard_log(text: str) -> Dict[str, object]:
    modes = [m.group(0) for m in GUARD_MODE_RE.finditer(text)]
    states = [GUARD_MODE_RE.match(m).group(1) for m in modes]
    dropped = [(int(a), int(b)) for a, b in GUARD_DROPPED_RE.findall(text)]
    return {"modes": modes, "states": states, "dropped": dropped}


def check_guard_log(rep: Report, text: str, expect: str, lo: int = UNIVERSE_MIN, hi: int = UNIVERSE_MAX) -> None:
    p = parse_guard_log(text)
    states, dropped = p["states"], p["dropped"]
    if len(states) != 1:
        rep.add(FAIL, "guards.log.mode_line", f"expected exactly one 'Universe guards mode:' line, found {len(states)}")
    else:
        rep.check(states[0].lower() == expect, "guards.log.mode", f"{states[0]} (as expected)",
                  f"{states[0]}, expected {expect.upper()}")
    if expect == "inert":
        rep.check(not dropped, "guards.log.no_drop_line", "no 'dropped N/M' line while inert",
                  f"inert run logged a drop line {dropped}")
        return
    if len(dropped) != 1:
        rep.add(FAIL, "guards.log.dropped_line", f"expected exactly one 'dropped N/M symbols' line, found {len(dropped)}")
        return
    n, m = dropped[0]
    rep.check(lo <= m <= hi, "guards.log.universe_size", f"M={m} within {lo}-{hi}", f"M={m} outside {lo}-{hi}")
    frac = (n / m) if m else 1.0
    rep.check(frac <= MAX_DROP_FRACTION, "guards.log.drop_fraction", f"dropped {n}/{m} = {frac:.1%} (<= {MAX_DROP_FRACTION:.0%})",
              f"dropped {n}/{m} = {frac:.1%} exceeds {MAX_DROP_FRACTION:.0%}")


def check_guard_results(rep: Report, results: dict, session: date, expect: str) -> Optional[int]:
    """Validates the screener's results JSON metadata; returns the breakout count (bullish + bearish) it contains."""
    ug = (results.get("metadata") or {}).get("universe_guards")
    if ug is None:
        rep.add(FAIL, "guards.results.metadata", "metadata.universe_guards is missing (image predates P4?)")
    else:
        rep.check(ug.get("applied") is (expect == "active"), "guards.results.applied", f"applied={ug.get('applied')}",
                  f"applied={ug.get('applied')}, expected {expect == 'active'}")
        rep.check(ug.get("session") == session.isoformat(), "guards.results.session", f"session={ug.get('session')}",
                  f"results are for session {ug.get('session')}, not {session}")
    sig = results.get("signals") or {}
    return len(sig.get("bullish_breakout", [])) + len(sig.get("bearish_breakout", []))


def check_guard_db(db: ReadOnlyDB, rep: Report, session: date, expect: str, breakouts_in_results: Optional[int]) -> None:
    n = db.scalar("SELECT count(*) FROM signal_ledger WHERE signal_date = %s", (session,))
    rep.add(INFO, "guards.db.ledger_rows", f"{n} signal_ledger rows for {session}")
    if breakouts_in_results is not None:
        rep.check(n <= breakouts_in_results, "guards.db.ledger_vs_results",
                  f"ledger {n} <= {breakouts_in_results} breakouts in the results file",
                  f"ledger has {n} rows but the results file only has {breakouts_in_results} breakouts (rows the screener did not emit)")
        if n < breakouts_in_results:
            rep.add(WARN, "guards.db.ledger_vs_results", f"ledger {n} < results {breakouts_in_results}: confirm the difference is understood")

    prior = [r[0] for r in db.q("SELECT n FROM (SELECT signal_date, count(*) n FROM signal_ledger WHERE signal_date < %s "
                                "GROUP BY 1 ORDER BY signal_date DESC LIMIT 5) t", (session,))]
    if len(prior) < 3:
        rep.add(WARN, "guards.db.candidate_range", f"only {len(prior)} prior sessions in the ledger; cannot judge the range")
    else:
        med = statistics.median(prior)
        rep.add(INFO, "guards.db.prior_5_session_median", f"{med} (from {prior})")
        ok_lo = n >= COLLAPSE_FRACTION * med
        ok_hi = n <= EXPLODE_FACTOR * med
        rep.check(ok_lo and ok_hi, "guards.db.candidate_range", f"{n} vs median {med}: within {COLLAPSE_FRACTION:.0%}-{EXPLODE_FACTOR:.0%}x",
                  f"{n} vs median {med}: {'collapsed below' if not ok_lo else 'exploded above'} the allowed range")

    if expect == "active":
        try:
            from ml_training.features import price_features as pf
            lookback = int(pf.LOOKBACK_BARS)
        except Exception as e:  # noqa: BLE001
            rep.add(FAIL, "guards.db.discontinuity_proxy", f"cannot import price_features: {type(e).__name__}")
            return
        rows = db.q(
            "SELECT DISTINCT l.symbol FROM signal_ledger l JOIN price_discontinuities d ON d.symbol = l.symbol "
            "WHERE l.signal_date = %s AND d.date <= %s AND (SELECT count(*) FROM stock_prices p WHERE p.symbol = l.symbol "
            "AND p.date > d.date AND p.date <= %s) <= %s ORDER BY 1", (session, session, session, lookback))
        rep.check(not rows, "guards.db.no_discontinuity_in_ledger",
                  "no ledger row for the session has a price_discontinuities row within the guard lookback",
                  f"{len(rows)} ledger symbols have a recent discontinuity (independent proxy for the guard): "
                  f"{[r[0] for r in rows[:10]]}")
    rep.add(INFO, "guards.db.price_discontinuities_total", str(db.scalar("SELECT count(*) FROM price_discontinuities")))


# ------------------------------------------------------------------ capture (S13)
def check_capture(db: ReadOnlyDB, rep: Report, session: date) -> None:
    runs = db.q("SELECT session_date, status, candidates, captured, already_captured, stale_skipped, hash_drift, guard_rejected, run_finished_at "
                "FROM candidate_capture_run ORDER BY run_started_at DESC LIMIT 3")
    for r in runs:
        rep.add(INFO, "capture.recent_run", str(r))
    mine = db.q("SELECT status, candidates, captured, already_captured, stale_skipped, hash_drift, guard_rejected FROM candidate_capture_run "
                "WHERE session_date = %s", (session,))
    if len(mine) != 1:
        rep.add(FAIL, "capture.run_row", f"expected exactly one candidate_capture_run row for {session}, found {len(mine)}")
        return
    status, cand, captured, already, stale, drift, rejected = mine[0]
    rep.check(status == "complete", "capture.status", "complete", f"status={status} (partial/failed is a STOP)")
    rep.check(drift == 0, "capture.hash_drift", "0", f"hash_drift={drift}")
    rep.add(INFO, "capture.guard_rejected", str(rejected))
    obs = db.scalar("SELECT count(*) FROM candidate_observation WHERE session_date = %s", (session,))
    snap = db.scalar("SELECT count(*) FROM feature_snapshot WHERE session_date = %s", (session,))
    rep.check(obs == (captured or 0) + (already or 0), "capture.observation_count",
              f"{obs} observations == captured {captured} + already_captured {already}",
              f"{obs} observations != captured {captured} + already_captured {already}")
    rep.add(INFO, "capture.snapshot_count", str(snap))
    rep.check((captured or 0) + (already or 0) + (stale or 0) == (cand or 0), "capture.accounting",
              f"captured {captured} + already {already} + stale_skipped {stale} = candidates {cand}",
              f"captured {captured} + already {already} + stale_skipped {stale} != candidates {cand}")
    linked = db.scalar("SELECT count(*) FROM signal_ledger WHERE signal_date = %s AND observation_id IS NOT NULL", (session,))
    rep.add(INFO, "capture.ledger_rows_linked", str(linked))
    partial = db.scalar("SELECT count(*) FROM signal_ledger WHERE signal_date = %s AND "
                        "((observation_id IS NULL) <> (feature_snapshot_id IS NULL))", (session,))
    rep.check(partial == 0, "capture.lineage_all_or_none", "0 partially-linked ledger rows", f"{partial} partially-linked ledger rows")
    non_always = db.scalar("SELECT count(*) FROM pg_trigger t WHERE t.tgrelid = ANY(%s::regclass[]) AND NOT t.tgisinternal "
                           "AND t.tgenabled <> 'A'", (list(fp.TABLES),))
    rep.check(non_always == 0, "capture.triggers_always", "immutability triggers still ENABLE ALWAYS", f"{non_always} triggers weakened")


# ------------------------------------------------------------------ delivery (S8.5)
def check_delivery(db: ReadOnlyDB, rep: Report, session: date) -> None:
    rows = db.q("SELECT post_kind, target, status, attempts FROM telegram_post_delivery WHERE market_session = %s "
                "ORDER BY post_kind, target", (session,))
    for r in rows:
        rep.add(INFO, "delivery.row", str(r))
    prod_sent = [r for r in rows if r[1] == "prod" and r[2] == "sent"]
    rep.check(len(prod_sent) >= 1, "delivery.prod_sent", f"{len(prod_sent)} prod posts sent for {session}",
              f"no prod 'sent' row for {session}")
    dupes = db.q("SELECT market_session, post_kind, target, count(*) FROM telegram_post_delivery WHERE market_session = %s "
                 "GROUP BY 1, 2, 3 HAVING count(*) > 1", (session,))
    rep.check(not dupes, "delivery.no_duplicates", "no duplicate (session, kind, target) rows", f"duplicates: {dupes}")


# ------------------------------------------------------------------ CLI
def _date(s: str) -> date:
    try:
        return date.fromisoformat(s)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{s!r} is not YYYY-MM-DD")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("schema")
    s.add_argument("--expect", choices=("absent", "exact"), required=True)
    s.add_argument("--capture", choices=("inactive", "active", "any"), default="any")
    s.add_argument("--ml-models", choices=("present", "absent", "any"), default="present",
                   help="migration 23 columns on ml_models (default present; use absent before S5)")
    r = sub.add_parser("roles")
    r.add_argument("--expect", choices=("absent", "present"), required=True)
    c = sub.add_parser("connections")
    c.add_argument("--forbid-user")
    c.add_argument("--allow-app", default=r"^(psql|pg_dump|pg_dumpall|validate_release_b)$")
    cf = sub.add_parser("config")
    cf.add_argument("--env-file")
    cf.add_argument("--expect-guards", choices=("unset", "set"))
    cf.add_argument("--expect-capture", choices=("unset", "on"))
    cf.add_argument("--check-boundary-vs-db", action="store_true",
                    help="also require GUARDS_EFFECTIVE_FROM to be after the latest signal_ledger session (needs DB_*)")
    g = sub.add_parser("guards")
    g.add_argument("--session", type=_date, required=True)
    g.add_argument("--expect", choices=("inert", "active"), required=True)
    g.add_argument("--log", help="pipeline log text file, or - for stdin")
    g.add_argument("--results", help="the screener's multi_timeframe_ml_enhanced_*.json for the session")
    g.add_argument("--no-db", action="store_true")
    k = sub.add_parser("capture")
    k.add_argument("--session", type=_date, required=True)
    d = sub.add_parser("delivery")
    d.add_argument("--session", type=_date, required=True)
    return ap


def run(argv: Sequence[str], env: Optional[Dict[str, str]] = None, out=sys.stdout) -> int:
    args = build_parser().parse_args(list(argv))
    rep = Report()
    e = os.environ if env is None else env
    db: Optional[ReadOnlyDB] = None
    try:
        needs_db = args.cmd in ("schema", "roles", "connections", "capture", "delivery") or (
            args.cmd == "config" and args.check_boundary_vs_db) or (args.cmd == "guards" and not args.no_db)
        if needs_db:
            try:
                db = ReadOnlyDB.connect(e)
            except Exception as ex:  # noqa: BLE001
                print(f"cannot connect read-only: {type(ex).__name__}", file=sys.stderr)
                return 2
        if args.cmd == "schema":
            check_schema(db, rep, args.expect, args.capture, args.ml_models)
        elif args.cmd == "roles":
            check_roles(db, rep, args.expect)
        elif args.cmd == "connections":
            check_connections(db, rep, args.forbid_user, args.allow_app)
        elif args.cmd == "config":
            keys = (gb.ENV_VAR, CAPTURE_ENV_VAR)
            if args.env_file:
                values = read_env_file(args.env_file, keys)
            else:
                values = {k: ([e[k]] if k in e else []) for k in keys}
            latest = None
            if args.check_boundary_vs_db:
                latest = db.scalar("SELECT max(signal_date) FROM signal_ledger")
            check_config(rep, values, args.expect_guards, args.expect_capture, latest)
        elif args.cmd == "guards":
            if args.log:
                text = sys.stdin.read() if args.log == "-" else open(args.log, encoding="utf-8", errors="replace").read()
                check_guard_log(rep, text, args.expect)
            else:
                rep.add(WARN, "guards.log", "no --log given; log evidence not checked")
            breakouts = None
            if args.results:
                with open(args.results, encoding="utf-8") as fh:
                    breakouts = check_guard_results(rep, json.load(fh), args.session, args.expect)
            if db is not None:
                check_guard_db(db, rep, args.session, args.expect, breakouts)
        elif args.cmd == "capture":
            check_capture(db, rep, args.session)
        elif args.cmd == "delivery":
            check_delivery(db, rep, args.session)
    except ReadOnlyViolation:
        raise
    except Exception as ex:  # noqa: BLE001 -- a query that cannot run is a failed check, never a silent pass
        if type(ex).__module__.startswith("psycopg2"):
            rep.add(FAIL, "check.error", f"{type(ex).__name__}: {str(ex).splitlines()[0] if str(ex) else ''}")
        else:
            raise
    finally:
        if db is not None:
            db.close()
    print(rep.render(), file=out)
    return 1 if rep.failed else 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
