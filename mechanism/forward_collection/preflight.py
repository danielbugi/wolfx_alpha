"""Read-only activation preflight: `collector stack ready for activation: YES/NO`.

Inspects the database catalog, the dataset spec against the collector contract, the declared scheduler design and the no-sector policy
(owner-decided Option B, so it is a recorded fact, not a blocker). It never writes, never installs, never changes capture state: every query is
a SELECT / catalog lookup, run in a transaction that is rolled back.
"""
from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from research.labels import fwd_v1
from research.labels import sessions as label_sessions
from research.lab import dataset_authoring as AUTH
from research.lab import research_status_reader as SR

from . import contract as C
from . import steps as S

SCHEMA = "forward_collection_preflight_v2"
REPO_ROOT = Path(__file__).resolve().parents[2]
REQUIRED_TABLES = ("stock_prices", "market_index_prices", "daily_fundamentals", "universe_snapshot", "market_snapshot", "sector_snapshot",
                   "stock_relative_strength", "forward_return_label", "candidate_observation", "candidate_capture_run",
                   "research_capture_activation")
REQUIRED_FUNCTIONS = ("research_rs_stamp()",)
WRITE_TABLES = ("universe_snapshot", "market_snapshot", "sector_snapshot", "stock_relative_strength", "forward_return_label")
READ_TABLES = ("stock_prices", "market_index_prices", "daily_fundamentals", "candidate_observation", "candidate_capture_run")
CAPTURE_ENV = "RESEARCH_CAPTURE_ENABLED"


def _check(cid: str, ok: bool, detail: Any, *, blocking: bool = True) -> Dict[str, Any]:
    return {"id": cid, "ok": bool(ok), "blocking": blocking, "detail": detail}


def run_preflight(connect, spec: Mapping[str, Any], *, env: Optional[Mapping[str, str]] = None, repo_root: Path = REPO_ROOT) -> Dict[str, Any]:
    env = os.environ if env is None else env
    checks: List[Dict[str, Any]] = []
    authoring = AUTH.parse_authoring(spec)
    cfg = SR.parse_cfg(authoring, [])
    sectors: Dict[str, Any] = {}
    with connect() as conn:
        cur = conn.cursor()
        try:
            cur.execute("SELECT current_user")
            role = cur.fetchone()[0]
            missing = []
            for t in REQUIRED_TABLES:
                cur.execute("SELECT to_regclass(%s) IS NOT NULL", (t,))
                if not cur.fetchone()[0]:
                    missing.append(t)
            checks.append(_check("required_tables_exist", not missing, {"missing": missing, "note": "migrations 19-22, 24-26, 29 provide these"}))
            miss_fn = []
            for f in REQUIRED_FUNCTIONS:
                cur.execute("SELECT to_regprocedure(%s) IS NOT NULL", (f,))
                if not cur.fetchone()[0]:
                    miss_fn.append(f)
            checks.append(_check("required_functions_exist", not miss_fn, {"missing": miss_fn}))
            lacking = []
            for t in WRITE_TABLES:
                if t in missing:
                    continue
                cur.execute("SELECT has_table_privilege(current_user, %s, 'INSERT') AND has_table_privilege(current_user, %s, 'SELECT')", (t, t))
                if not cur.fetchone()[0]:
                    lacking.append(f"{t}: INSERT+SELECT")
            for t in READ_TABLES:
                if t in missing:
                    continue
                cur.execute("SELECT has_table_privilege(current_user, %s, 'SELECT')", (t,))
                if not cur.fetchone()[0]:
                    lacking.append(f"{t}: SELECT")
            checks.append(_check("runtime_role_privileges", not lacking, {"role": role, "lacking": lacking}))
            conn.rollback()

            cal: Dict[str, Any] = {"ok": False, "error": "required tables are missing"}
            if not missing:
                newest = S.mi_inputs.latest_stored_dates(conn)["stock_prices"]
                if newest is None:
                    cal = {"ok": False, "error": "stock_prices is empty"}
                else:
                    try:
                        sess, src = label_sessions.derive_sessions(conn, newest - timedelta(days=45), newest)
                        cal = {"ok": newest in sess and len(sess) >= 15, "newest_loaded_bar": newest.isoformat(),
                               "sessions_in_45d": len(sess), "source": src}
                    except fwd_v1.CalendarError as e:
                        cal = {"ok": False, "error": str(e)[:200]}
                conn.rollback()
                sectors = S.sector_coverage(conn)
                conn.rollback()
            checks.append(_check("trading_calendar_derivable", cal.pop("ok"), cal))

            act: List[Any] = []
            if "research_capture_activation" not in missing:
                cur.execute("SELECT DISTINCT ON (strategy_id) strategy_id, state, effective_from_session FROM research_capture_activation "
                            "ORDER BY strategy_id, effective_from_session DESC, id DESC")
                act = [{"strategy_id": r[0], "state": r[1], "effective_from_session": r[2].isoformat()} for r in cur.fetchall()]
                conn.rollback()
            env_on = str(env.get(CAPTURE_ENV, "")).strip().lower() in ("1", "true", "yes", "on")
            checks.append(_check("capture_state_reported", True,
                                 {"env_enabled": env_on, "activation_rows": act,
                                  "note": "informational: enabling capture is a separate approved activation step"}, blocking=False))
        finally:
            conn.rollback()

    en = cfg.enabled()
    src_blockers = C.spec_source_blockers(en)
    checks.append(_check("dataset_sources_have_collectors", not src_blockers,
                         {"enabled": sorted(k for k, v in en.items() if v), "blockers": src_blockers}))
    rs = (cfg.stock_rs.model_version, cfg.stock_rs.feature_set_version, cfg.stock_rs.horizon_sessions) if cfg.stock_rs else None
    ver = C.spec_version_blockers(cfg.market_feature_set_version, cfg.sector_feature_set_version, rs, authoring.label_version,
                                  authoring.label_methodology_version, authoring.label_horizons)
    if S.writers_pinned() != C.COLLECTOR_VERSIONS:
        ver.append("the contract's COLLECTOR_VERSIONS no longer equal what the writers write")
    checks.append(_check("spec_versions_match_collectors", not ver, {"problems": ver}))
    dsg = C.validate_design(cfg.availability_grace_days)
    vps = repo_root / "deploy" / "vps"
    installed = sorted(p.name for p in vps.glob("donchian-forward-collection*")) if vps.is_dir() else []
    checks.append(_check("scheduler_design_consistent", not dsg,
                         {"problems": dsg, "required_grace_days": C.required_grace_days(), "spec_grace_days": cfg.availability_grace_days,
                          "installed_units_in_repo": installed, "note": "design only: nothing is installed by this slice"}))
    nsp = {"policy": C.NO_SECTOR_POLICY, "options": C.NO_SECTOR_OPTIONS,
           "symbols_without_pit_safe_sector_now": sectors.get("n_without_sector"), "universe_now": sectors.get("n_universe")}
    checks.append(_check("no_sector_policy_resolved", C.no_sector_policy_resolved(), nsp))

    blocking = [c for c in checks if c["blocking"]]
    ready = all(c["ok"] for c in blocking)
    return {"schema": SCHEMA, "collector_stack_ready_for_activation": "YES" if ready else "NO",
            "blockers": [c["id"] for c in blocking if not c["ok"]], "checks": checks, "read_only": True}
