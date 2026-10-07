"""The complete read-only activation preflight: `forward research collection ready for activation: YES/NO`.

It wraps the unchanged `preflight.run_preflight` (v2) and adds the rest of the stack, in three layers that are deliberately NOT one boolean:

  ARCHITECTURE-ready  the code and the declared design are coherent: every enabled dataset source has a collector step (the sector history has the
                      read-only verification step, not `None`), writer versions match, the scheduler design is self-consistent, the sector writer is
                      default-OFF. Needs no database.
  ENVIRONMENT-ready   the target database/process has what the stack needs: migrations 22 and 24-31 by catalog (derived from the compose init list
                      and the SQL files themselves, never a second hand-kept list), every research table guarded by an ENABLE ALWAYS trigger, the
                      runtime role proven least-privilege (SELECT+INSERT on append-only history, no UPDATE/DELETE/TRUNCATE, cannot change the capture
                      boundary), a derivable trading calendar and a fresh-enough fundamentals refresh.
  ACTIVATION-ready    the owner/operational gates (S11 passed, the `model_version` defect fixed, owner approval, a verified backup, a vendor check, the
                      mechanism image pinned) were supplied by a human as `yes`; the scheduler units are committed in the repo. A gate that was not
                      supplied is NO. Nothing here can set one.

The answer is YES only when all three are. Every query is a SELECT or a catalog lookup in a transaction that is rolled back; nothing is installed,
enabled or written. There is no override and no way to make a layer YES except by the thing it asks about being true.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from research.lab import sector_history_verification as SHV

from . import contract as C
from . import preflight as P
from . import steps as S

SCHEMA = "forward_collection_activation_v1"
REPO_ROOT = P.REPO_ROOT
RUNTIME_ROLE = "donchian_app"
SECTOR_FLAG_ENV = "SECTOR_HISTORY_RECORDER_ENABLED"

# the research migrations activation depends on (23 only adds ml_models columns and is checked by the Release B validator)
ACTIVATION_MIGRATIONS: Tuple[int, ...] = (22, 24, 25, 26, 27, 28, 29, 30, 31)
APPEND_ONLY_FROM = 24                      # migrations 24-31 create the append-only research tables
CAPTURE_TABLES_WITH_INSERT = ("candidate_observation", "feature_snapshot")
CAPTURE_BOUNDARY_TABLE = "research_capture_activation"

# owner / operational gates: name -> what it means. Supplied by a human, never inferred.
GATES: Dict[str, str] = {
    "s11_passed": "S11 production validation concluded 'RELEASE B S11 PASS - PROVEN UNDER LEAST PRIVILEGE'",
    "model_version_fix_applied": "the isolated model_version='unknown' -> NULL fix is deployed and verified (candidate capture must not copy the defect)",
    "owner_activation_approved": "the owner explicitly approved the first production forward-observed session",
    "backup_verified": "a fresh backup was taken AND restore-verified immediately before the first migration",
    "authoritative_vendor_verified_live": "the authoritative sector vendor (yfinance) was verified live from the production image",
    "mechanism_image_pinned": "the mechanism image tag the collector/updater run is pinned to the activation sha",
}
AUTO_GATE_UNITS = "scheduler_units_committed"
MOUNT = re.compile(r"\./(mechanism/[\w./-]+\.sql):/docker-entrypoint-initdb\.d/(\d+)_")
TABLE_RE = re.compile(r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+(?:public\.)?([a-z_][a-z0-9_]*)", re.I)
FUNC_RE = re.compile(r"CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+(?:public\.)?([a-z_][a-z0-9_]*)\s*\(", re.I)


def _chk(cid: str, ok: bool, detail: Any, *, blocking: bool = True) -> Dict[str, Any]:
    return {"id": cid, "ok": bool(ok), "blocking": blocking, "detail": detail}


def migration_catalog(repo_root: Path = REPO_ROOT) -> Dict[int, Dict[str, List[str]]]:
    """migration number -> {tables, functions}, derived from the compose init list and each SQL file (production's own bootstrap order)."""
    compose = repo_root / "docker-compose.yml"
    if not compose.is_file():
        return {}
    out: Dict[int, Dict[str, List[str]]] = {}
    for src, num in MOUNT.findall(compose.read_text(encoding="utf-8")):
        n = int(num)
        if n not in ACTIVATION_MIGRATIONS:
            continue
        sql = re.sub(r"--[^\n]*", "", (repo_root / src).read_text(encoding="utf-8"))
        out[n] = {"file": src, "tables": sorted(set(TABLE_RE.findall(sql))), "functions": sorted(set(FUNC_RE.findall(sql)))}
    return out


# ------------------------------------------------------------------ architecture layer (no database)
EVIDENCED_VENDOR_SYMBOLS = {"BRK.B": "BRK-B", "BF.B": "BF-B", "BRK/A": "BRK-A"}      # probed from the production VPS network, 2026-10-06 (Slice 12)
REFUSED_VENDOR_SYMBOLS = ("XYZ.U", "ABC.WS", "brk.b")
CODE_ROOT = Path(__file__).resolve().parents[1]                                        # the mechanism/ directory this package runs from
WRITER_REQUEST_SITE = "fundamentals_updater.py"
MIN_REQUEST_SITES = 2                                                                  # the writer's vendor call and the dry-run probe


UNIT_SERVICE, UNIT_TIMER, UNIT_ALERT, UNIT_WRAPPER = ("donchian-forward-collection.service", "donchian-forward-collection.timer",
                                                      "donchian-forward-collection-alert.service", "run_forward_collection.sh")
UNIT_FILES = (UNIT_SERVICE, UNIT_TIMER, UNIT_ALERT, UNIT_WRAPPER)
ONCALENDAR = re.compile(r"^OnCalendar=\*-\*-\* (\d\d:\d\d):00 (\S+)\s*$", re.M)


def scheduler_units_check(repo_root: Path) -> Tuple[bool, Dict[str, Any]]:
    """The collector's service, timer, alert unit and wrapper are committed under deploy/vps, agree with the scheduler design in the contract, and are
    DORMANT: the wrapper needs its arming file, and no other committed unit orders itself after, wants or requires the collector. Reading files only:
    whether a host has them installed is a runtime fact this preflight cannot see (an activation step)."""
    vps = repo_root / "deploy" / "vps"
    problems: List[str] = []
    texts: Dict[str, str] = {}
    for name in UNIT_FILES:
        p = vps / name
        if p.is_file():
            texts[name] = p.read_text(encoding="utf-8")
        else:
            problems.append(f"{name}: not committed under deploy/vps")
    service, timer, wrapper = texts.get(UNIT_SERVICE, ""), texts.get(UNIT_TIMER, ""), texts.get(UNIT_WRAPPER, "")
    if service and "ExecStart=/opt/donchian/scripts/" + UNIT_WRAPPER not in service:
        problems.append(f"{UNIT_SERVICE}: ExecStart is not the tracked wrapper")
    if service and "OnFailure=" + UNIT_ALERT not in service:
        problems.append(f"{UNIT_SERVICE}: no OnFailure alert")
    if timer:
        want = [(h, C.SCHEDULER_DESIGN["timezone"]) for h in C.SCHEDULER_DESIGN["fires_local"]]
        if ONCALENDAR.findall(timer) != want:
            problems.append(f"{UNIT_TIMER}: OnCalendar lines are not the contract's fires {want}")
    if wrapper:
        for needle, why in (("--with-sector-history-check", "the sector-history verification"), ("--apply", "the apply flag the contract design names"),
                            ("FORWARD_COLLECTION_ARMED", "the arming file that keeps it inert until activation")):
            if needle not in wrapper:
                problems.append(f"{UNIT_WRAPPER}: missing {why}")
    approved = set(UNIT_FILES)
    if vps.is_dir():
        for p in sorted(vps.glob("*.service")) + sorted(vps.glob("*.timer")):
            if p.name not in approved and "forward-collection" in p.read_text(encoding="utf-8"):
                problems.append(f"{p.name}: another unit references the collector (it must stand alone)")
    return not problems, {"problems": problems, "committed": sorted(texts), "fires_local": list(C.SCHEDULER_DESIGN["fires_local"]),
                          "dormancy": "installed only by an administrator; the wrapper refuses without FORWARD_COLLECTION_ARMED"}


def vendor_symbol_check(code_root: Path = CODE_ROOT) -> Tuple[bool, Dict[str, Any]]:
    """The yfinance request symbol is translated ONCE, at the vendor call, and by one function: behaviour of that function on the evidenced production
    share classes, its refusals, and that EVERY request site under data_updaters/ that uses it (the writer and the dry-run probe) takes its symbol from
    it. Sites are discovered, not listed: a file that imports the translation must route every `yf.Ticker(...)` through it."""
    from data_updaters import vendor_symbols as V
    problems: List[str] = []
    for canonical, want in EVIDENCED_VENDOR_SYMBOLS.items():
        got = V.to_yfinance(canonical).request
        if got != want:
            problems.append(f"{canonical} -> {got!r}, expected {want!r}")
    for bad in REFUSED_VENDOR_SYMBOLS:
        try:
            V.to_yfinance(bad)
            problems.append(f"{bad!r} was not refused")
        except V.UnsupportedSymbolFormat:
            pass
    sites: List[str] = []
    updaters = code_root / "data_updaters"
    for path in sorted(updaters.glob("*.py")) if updaters.is_dir() else []:
        text = path.read_text(encoding="utf-8")
        if "vendor_symbols.to_yfinance(" not in text:
            continue
        sites.append(path.name)
        if re.findall(r"yf\.Ticker\((\w+)\)", text) != ["request_symbol"] or not re.search(r"request_symbol\s*=\s*vendor_symbols\.to_yfinance\(", text):
            problems.append(f"{path.name}: a yfinance request does not take its symbol from vendor_symbols.to_yfinance")
    if len(sites) < MIN_REQUEST_SITES or WRITER_REQUEST_SITE not in sites:
        problems.append(f"expected at least {MIN_REQUEST_SITES} translated request sites including {WRITER_REQUEST_SITE}; found {sites}")
    return not problems, {"problems": problems, "evidenced": EVIDENCED_VENDOR_SYMBOLS, "refused": list(REFUSED_VENDOR_SYMBOLS), "sites": sites,
                          "canonical_identity": "never translated: only the outbound request symbol is"}


def architecture_checks(spec: Mapping[str, Any], *, repo_root: Path = REPO_ROOT, env: Optional[Mapping[str, str]] = None) -> List[Dict[str, Any]]:
    from research.lab import dataset_authoring as AUTH
    from research.lab import research_status_reader as SR
    env = os.environ if env is None else env
    authoring = AUTH.parse_authoring(spec)
    cfg = SR.parse_cfg(authoring, [])
    enabled = cfg.enabled()
    out: List[Dict[str, Any]] = []
    blockers = C.spec_source_blockers(enabled)
    out.append(_chk("dataset_sources_have_collectors", not blockers, {"enabled": sorted(k for k, v in enabled.items() if v), "blockers": blockers}))
    steps = S.make_steps(with_capture=True, with_sector_history=True)
    missing = sorted(src for src, on in enabled.items() if on and C.SOURCE_CONTRACT.get(src, {}).get("step") not in set(C.STEP_ORDER) & set(steps))
    out.append(_chk("every_enabled_source_step_is_implemented", not missing,
                    {"missing": missing, "step_order": list(C.STEP_ORDER), "implemented": sorted(steps)}))
    sector = C.SOURCE_CONTRACT.get("sector_history", {})
    out.append(_chk("sector_history_has_a_collector_dependency", sector.get("step") == C.STEP_SECTOR_HISTORY and C.STEP_SECTOR_HISTORY in steps,
                    {"step": sector.get("step"), "verified_by_collector": sector.get("verified_by_collector"),
                     "single_writer": "the fundamentals updater's recorder; the collector only verifies (read-only)"}))
    rs = (cfg.stock_rs.model_version, cfg.stock_rs.feature_set_version, cfg.stock_rs.horizon_sessions) if cfg.stock_rs else None
    ver = C.spec_version_blockers(cfg.market_feature_set_version, cfg.sector_feature_set_version, rs, authoring.label_version,
                                  authoring.label_methodology_version, authoring.label_horizons)
    if S.writers_pinned() != C.COLLECTOR_VERSIONS:
        ver.append("the contract's COLLECTOR_VERSIONS no longer equal what the writers write")
    out.append(_chk("spec_versions_match_collectors", not ver, {"problems": ver}))
    dsg = C.validate_design(cfg.availability_grace_days)
    out.append(_chk("scheduler_design_consistent", not dsg, {"problems": dsg, "required_grace_days": C.required_grace_days(),
                                                              "spec_grace_days": cfg.availability_grace_days,
                                                              "command": C.SCHEDULER_DESIGN.get("command")}))
    out.append(_chk("scheduler_command_verifies_sector_history", "--with-sector-history-check" in str(C.SCHEDULER_DESIGN.get("command")),
                    {"command": C.SCHEDULER_DESIGN.get("command")}))
    ok_symbols, symbols_detail = vendor_symbol_check()
    out.append(_chk("sector_vendor_requests_use_the_translation", ok_symbols, symbols_detail))
    out.append(_chk("no_sector_policy_resolved", C.no_sector_policy_resolved(), {"policy": C.NO_SECTOR_POLICY}))
    out.append(_chk("sector_writer_flag_reported", True,
                    {"note": "informational: the flag is read in the fundamentals updater's own process, so this preflight can only report its own "
                             "environment. Turning it on is an activation step, not a precondition",
                     "preflight_process_value_is_on": str(env.get(SECTOR_FLAG_ENV, "")) == "1"}, blocking=False))
    ok_units, units_detail = scheduler_units_check(repo_root)
    out.append(_chk("scheduler_units_committed_and_dormant", ok_units, units_detail))
    return out


# ------------------------------------------------------------------ environment layer (database)
def _scalar(cur, sql: str, params: Sequence[Any] = ()) -> Any:
    cur.execute(sql, params or None)
    row = cur.fetchone()
    return row[0] if row else None


def environment_checks(connect, repo_root: Path = REPO_ROOT, *, runtime_role: str = RUNTIME_ROLE) -> List[Dict[str, Any]]:
    cat = migration_catalog(repo_root)
    out: List[Dict[str, Any]] = []
    if set(cat) != set(ACTIVATION_MIGRATIONS):
        out.append(_chk("migration_catalog_derivable", False, {"found": sorted(cat), "need": list(ACTIVATION_MIGRATIONS),
                                                              "note": "docker-compose.yml and the migration SQL files must be readable"}))
        return out
    research_tables = sorted(t for n, v in cat.items() if n >= APPEND_ONLY_FROM for t in v["tables"])
    with connect() as conn:
        cur = conn.cursor()
        try:
            missing: Dict[str, Dict[str, List[str]]] = {}
            for n, v in cat.items():
                tm = [t for t in v["tables"] if not _scalar(cur, "SELECT to_regclass(%s) IS NOT NULL", (t,))]
                fm = [f for f in v["functions"] if not _scalar(cur, "SELECT EXISTS (SELECT 1 FROM pg_proc WHERE proname = %s)", (f,))]
                if tm or fm:
                    missing[str(n)] = {"tables": tm, "functions": fm}
            out.append(_chk("migrations_22_and_24_to_31_applied", not missing,
                            {"checked": {str(n): {"tables": len(v["tables"]), "functions": len(v["functions"])} for n, v in cat.items()},
                             "missing_by_migration": missing}))
            conn.rollback()

            guard_missing = []
            for t in research_tables + list(CAPTURE_TABLES_WITH_INSERT):
                if not _scalar(cur, "SELECT to_regclass(%s) IS NOT NULL", (t,)):
                    continue
                n = _scalar(cur, "SELECT count(*) FROM pg_trigger WHERE tgrelid = to_regclass(%s) AND NOT tgisinternal AND tgenabled = 'A'", (t,))
                if not n:
                    guard_missing.append(t)
            out.append(_chk("research_tables_guarded_by_enable_always_triggers", not guard_missing, {"unguarded": guard_missing}))
            conn.rollback()

            cur.execute("SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls, rolcanlogin FROM pg_roles WHERE rolname = %s",
                        (runtime_role,))
            row = cur.fetchone()
            attrs = dict(zip(("superuser", "createdb", "createrole", "replication", "bypassrls", "can_login"), row)) if row else None
            role_ok = bool(row) and not any(row[:5]) and bool(row[5])
            out.append(_chk("runtime_role_exists_with_no_privileged_attributes", role_ok, {"role": runtime_role, "attributes": attrs}))
            conn.rollback()

            problems: List[str] = []
            if row:
                def priv(t: str, p: str) -> bool:
                    return bool(_scalar(cur, "SELECT has_table_privilege(%s, %s, %s)", (runtime_role, t, p)))
                existing = [t for t in research_tables + list(CAPTURE_TABLES_WITH_INSERT) + [CAPTURE_BOUNDARY_TABLE]
                            if _scalar(cur, "SELECT to_regclass(%s) IS NOT NULL", (t,))]
                for t in existing:
                    owner = _scalar(cur, "SELECT tableowner FROM pg_tables WHERE tablename = %s ORDER BY (schemaname = current_schema()) DESC LIMIT 1", (t,))
                    if owner == runtime_role:
                        problems.append(f"{t}: owned by the runtime role")
                    if not priv(t, "SELECT"):
                        problems.append(f"{t}: lacks SELECT")
                    writable = t != CAPTURE_BOUNDARY_TABLE
                    if writable and not priv(t, "INSERT"):
                        problems.append(f"{t}: lacks INSERT")
                    forbidden = ("UPDATE", "DELETE", "TRUNCATE") + (() if writable else ("INSERT",))
                    problems += [f"{t}: has {p}" for p in forbidden if priv(t, p)]
            conn.rollback()
            out.append(_chk("runtime_role_is_least_privilege_on_research_tables", bool(row) and not problems,
                            {"role": runtime_role, "problems": problems,
                             "rule": "SELECT+INSERT on append-only history and capture rows; SELECT only on the capture boundary; never UPDATE/DELETE/TRUNCATE"}))

            fresh: Dict[str, Any] = {"ok": False, "error": "daily_fundamentals or stock_prices unreadable"}
            try:
                prices = _scalar(cur, "SELECT max(date) FROM stock_prices")
                fund = _scalar(cur, "SELECT max(date) FROM daily_fundamentals")
                if prices is None or fund is None:
                    fresh = {"ok": False, "error": "no stock_prices or no daily_fundamentals rows"}
                else:
                    gap = (prices - fund).days
                    fresh = {"ok": gap <= SHV.MAX_REFRESH_GAP_DAYS, "newest_price_session": prices.isoformat(),
                             "newest_fundamentals_date": fund.isoformat(), "gap_days": gap, "max_gap_days": SHV.MAX_REFRESH_GAP_DAYS,
                             "note": "the nightly pipeline refreshes fundamentals for the full universe every trading day; the longest normal gap is the weekend"}
            except Exception as e:  # noqa: BLE001
                fresh = {"ok": False, "error": type(e).__name__}
            conn.rollback()
            out.append(_chk("fundamentals_refresh_is_recent", fresh.pop("ok"), fresh))
        finally:
            conn.rollback()
    return out


# ------------------------------------------------------------------ activation layer (owner / operational gates)
def gate_checks(gates: Optional[Mapping[str, str]], repo_root: Path = REPO_ROOT) -> List[Dict[str, Any]]:
    gates = gates or {}
    out = [_chk(f"gate:{name}", str(gates.get(name, "")).strip().lower() == "yes", {"means": meaning, "supplied": name in gates})
           for name, meaning in GATES.items()]
    vps = repo_root / "deploy" / "vps"
    units = sorted(p.name for p in vps.glob("donchian-forward-collection*")) if vps.is_dir() else []
    has_service = any(n.endswith(".service") for n in units)
    has_timer = any(n.endswith(".timer") for n in units)
    out.append(_chk(f"gate:{AUTO_GATE_UNITS}", has_service and has_timer,
                    {"units": units, "means": "the collector's systemd service and timer are committed under deploy/vps. Committed is not installed: "
                                              "installing them on the host is a manual administrator step in the activation runbook"}))
    return out


def _yes(checks: Sequence[Dict[str, Any]]) -> bool:
    return all(c["ok"] for c in checks if c["blocking"])


def run_activation_preflight(connect, spec: Mapping[str, Any], *, env: Optional[Mapping[str, str]] = None, repo_root: Path = REPO_ROOT,
                             gates: Optional[Mapping[str, str]] = None, runtime_role: str = RUNTIME_ROLE) -> Dict[str, Any]:
    env = os.environ if env is None else env
    base = P.run_preflight(connect, spec, env=env, repo_root=repo_root)
    base_by_id = {c["id"]: c for c in base["checks"]}
    arch = architecture_checks(spec, repo_root=repo_root, env=env)
    arch_ids = {c["id"] for c in arch}
    arch += [c for cid, c in base_by_id.items() if cid in {"scheduler_design_consistent", "dataset_sources_have_collectors", "spec_versions_match_collectors",
                                                           "no_sector_policy_resolved"} and cid not in arch_ids]
    envc = [c for cid, c in base_by_id.items() if cid in {"required_tables_exist", "required_functions_exist", "runtime_role_privileges",
                                                          "trading_calendar_derivable", "capture_state_reported"}]
    envc += environment_checks(connect, repo_root, runtime_role=runtime_role)
    act = gate_checks(gates, repo_root)
    layers = {"architecture": arch, "environment": envc, "activation": act}
    ready = {k: _yes(v) for k, v in layers.items()}
    final = all(ready.values())
    return {"schema": SCHEMA,
            "architecture_ready": "YES" if ready["architecture"] else "NO",
            "environment_ready": "YES" if ready["environment"] else "NO",
            "activation_ready": "YES" if ready["activation"] else "NO",
            "forward_research_collection_ready_for_activation": "YES" if final else "NO",
            "collector_stack_ready_for_activation": base["collector_stack_ready_for_activation"],
            "blockers": {k: [c["id"] for c in v if c["blocking"] and not c["ok"]] for k, v in layers.items()},
            "layers": layers, "read_only": True,
            "note": "YES means every layer is YES. 'activation' gates are owner decisions that this preflight can only read, never set."}
