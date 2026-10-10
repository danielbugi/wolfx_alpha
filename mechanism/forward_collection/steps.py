"""The composition root: the ONLY module that imports the existing writers (the Market Intelligence runner and the fwd_v1 label runner).

Each function takes the orchestrator's context and returns a StepResult. Nothing here computes a research value: it calls the writers that
already exist, and `verify` only READS the rows they wrote back. A step never reports success on the strength of another step.
"""
from __future__ import annotations

from datetime import datetime, time
from typing import Any, Dict, List, Optional

from market_intelligence import inputs as mi_inputs
from market_intelligence import runner as mi_runner
from market_intelligence.relative_strength import MODEL_VERSION as RS_MODEL_VERSION
from research.lab import dataset_reader as reader
from research.lab import sector_history as SH
from research.lab import sector_history_verification as SHV
from research.labels import fwd_v1
from research.labels import runner as label_runner

from . import contract as C
from .orchestrator import Ctx, db_clock

RS_HORIZONS = C.COLLECTOR_VERSIONS["stock_rs"]["horizons"]
FEATURE_SET_VERSION = mi_runner.FEATURE_SET_VERSION


def writers_pinned() -> Dict[str, Any]:
    """What the writers actually write now (the pure contract's COLLECTOR_VERSIONS must equal this; a test pins it)."""
    return {"market_feature_set_version": FEATURE_SET_VERSION, "sector_feature_set_version": FEATURE_SET_VERSION,
            "stock_rs": {"model_version": RS_MODEL_VERSION, "feature_set_version": FEATURE_SET_VERSION, "horizons": tuple(mi_runner.rs_mod.HORIZONS)},
            "label_version": fwd_v1.LABEL_VERSION, "label_methodology_version": fwd_v1.METHODOLOGY_VERSION, "label_horizons": tuple(fwd_v1.HORIZONS)}


def _iso(v: Optional[datetime]) -> Optional[str]:
    return v.isoformat() if v is not None else None


# ------------------------------------------------------------------ capture_verify (read-only)
def capture_verify(ctx: Ctx) -> C.StepResult:
    """Candidates are written inside the screener of their own session. This only checks that the capture hook recorded a COMPLETE run for the
    session; it cannot create a missing capture and says so.

    It applies only to a session for which capture was ENABLED (the same rule the screener's hook uses: the latest `research_capture_activation`
    row with effective_from_session <= session says 'enabled'). A session before the boundary was never meant to be captured, so it is not a
    failure; a session AT or AFTER the boundary with no complete run is, whatever the clock says (a late or failed pipeline stays INCOMPLETE)."""
    with ctx.connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT strategy_id FROM (SELECT DISTINCT ON (strategy_id) strategy_id, state FROM research_capture_activation "
                    "WHERE effective_from_session <= %s ORDER BY strategy_id, effective_from_session DESC) a WHERE state = 'enabled'", (ctx.session,))
        enabled = sorted(r[0] for r in cur.fetchall())
        if not enabled:
            conn.rollback()
            return C.StepResult(C.STEP_CAPTURE, C.ALREADY, {"applicable": False, "reason": "capture_not_enabled_for_session",
                                                           "meaning": "no strategy has an 'enabled' activation row effective on or before this session"})
        cur.execute("SELECT DISTINCT ON (strategy_id) strategy_id, status, captured, already_captured, candidates, run_finished_at "
                    "FROM candidate_capture_run WHERE session_date = %s AND strategy_id = ANY(%s) ORDER BY strategy_id, id DESC", (ctx.session, enabled))
        runs = cur.fetchall()
        cur.execute("SELECT count(*), count(*) FILTER (WHERE fs.sector IS NULL) FROM candidate_observation co "
                    "JOIN feature_snapshot fs ON fs.id = co.snapshot_id WHERE co.session_date = %s", (ctx.session,))
        n_cand, n_nosec = cur.fetchone()
        conn.rollback()
    detail = {"applicable": True, "enabled_strategies": enabled,
              "runs": [{"strategy_id": r[0], "status": r[1], "candidates": r[4], "captured": r[2], "already_captured": r[3]} for r in runs],
              "candidate_rows": n_cand, "candidate_rows_without_sector": n_nosec}
    missing_runs = sorted(set(enabled) - {r[0] for r in runs})
    if missing_runs:
        return C.StepResult(C.STEP_CAPTURE, C.FAILED, {**detail, "reason": "no_capture_run_for_session", "strategies_without_a_run": missing_runs},
                            "no candidate_capture_run exists for the session: the screener did not capture it (this collector cannot)")
    bad = [r for r in runs if r[1] != "complete"]
    if bad:
        return C.StepResult(C.STEP_CAPTURE, C.FAILED, {**detail, "reason": "capture_not_complete"},
                            f"capture run status is {sorted({r[1] for r in bad})} (only 'complete' counts)")
    return C.StepResult(C.STEP_CAPTURE, C.ALREADY, detail)


# ------------------------------------------------------------------ sector_history_verify (read-only: verifies the one authoritative writer)
def make_sector_history_verify(source: str = SH.AUTHORITATIVE_SOURCE):
    """The collector never writes the sector history (the fundamentals updater's recorder is its ONLY writer); it VERIFIES, for the explicit session,
    that the authoritative source was polled for the session's universe inside the cadence window and that no chain is broken. A symbol the vendor
    legitimately has no sector for is ACCOUNTED (Option B), so it never fails the session; per-symbol availability is reported, not gated."""
    def sector_history_verify(ctx: Ctx) -> C.StepResult:
        lo, hi = SHV.window(ctx.session, ctx.grace_days)
        now = (ctx.clock or db_clock(ctx.connect))()
        with ctx.connect() as conn:
            cur = conn.cursor()
            present = reader.sector_history_tables_present(cur)
            conn.rollback()
            if not present:
                doc = {"session": ctx.session.isoformat(), "source": source, "reasons": [SHV.TABLES_ABSENT], "meaning": {SHV.TABLES_ABSENT: SHV.MEANING[SHV.TABLES_ABSENT]}}
            else:
                with reader.read_only_session(conn):
                    cur = conn.cursor()
                    cur.execute("SELECT DISTINCT symbol FROM stock_prices WHERE date = %s", (ctx.session,))
                    universe = sorted(r[0] for r in cur.fetchall())
                    activity = reader.poll_activity(cur, universe, source, lo, hi)
                    rows = reader.history_rows(cur, universe, source, ctx.session, hi)
                doc = SHV.evaluate(session=ctx.session, grace_days=ctx.grace_days, universe=universe, activity=activity, history_rows=rows,
                                   source=source, cutoff=hi)
        reasons = doc["reasons"]
        if not reasons:
            return C.StepResult(C.STEP_SECTOR_HISTORY, C.ALREADY, doc)
        past = now >= hi
        permanent = [r for r in reasons if past or r in SHV.PERMANENT_ALWAYS]
        detail = {**doc, "reason": reasons[0], "problems": list(reasons), "permanent_problems": permanent, "checked_at": now.isoformat(),
                  "past_decision_deadline": past}
        return C.StepResult(C.STEP_SECTOR_HISTORY, C.FAILED, detail, "; ".join(reasons))
    return sector_history_verify


# ------------------------------------------------------------------ committed-snapshot verification (R1): reuse valid stored evidence, never rewrite it
SNAPSHOT_ABSENT, SNAPSHOT_VALID, SNAPSHOT_PARTIAL, SNAPSHOT_INCONSISTENT, SNAPSHOT_REPAIRABLE = "absent", "valid", "partial", "inconsistent", "repairable"
EARLIEST_CLOSE_NY = time(13, 0)         # the earliest an NYSE session can close (an early-close day): no honest row of a session is stamped before it


def committed_snapshot(conn, session, feature_set_version: str, deadline: datetime) -> Dict[str, Any]:
    """READ-ONLY. Is the session's observed snapshot fully committed and internally consistent? One of:
      absent        none of its rows exist: the strict first-time path (fresh price and scan evidence required) applies.
      valid         universe + market + sector + relative-strength (every horizon) rows all exist, agree with each other, were stamped by the database inside the
                    allowed window [earliest close, min(knowledge cutoff, decision deadline)), and a COMPLETE price-discontinuity scan for the session existed
                    BEFORE the snapshot was written. Historical validity at capture time: current live prices are irrelevant to it.
      partial       some but not all of the universe / market / sector rows exist (the write is atomic, so this means something outside the collector touched the evidence).
      inconsistent  all rows exist but contradict each other, are stamped outside the window, or no scan preceded them.
      repairable    ONLY relative-strength rows are missing or short while universe/market/sector are present and consistent (the documented crash-after-the-market-write case).
                    It is NOT reused and NOT trusted as complete: it falls through to the strict path, which writes just the missing rows (never overwriting one) and only under
                    fresh price and scan evidence; with stale evidence it fails closed (INCOMPLETE) and writes nothing.
    It never writes. `problems` lists every reason; `detail` carries the ids, stamps and counts that were checked."""
    from zoneinfo import ZoneInfo
    ny = ZoneInfo("America/New_York")
    cur = conn.cursor()
    cur.execute("SELECT id, n_symbols, n_classified, captured_at, content_hash, code_ref, provenance FROM universe_snapshot WHERE session_date = %s AND provenance = 'observed'",
                (session,))
    u = cur.fetchone()
    cur.execute("SELECT id, universe_snapshot_id, captured_at, content_hash, code_ref, source, provenance FROM market_snapshot "
                "WHERE session_date = %s AND provenance = 'observed' AND feature_set_version = %s", (session, feature_set_version))
    m = cur.fetchone()
    sec = (0, None, None)
    if m is not None:
        cur.execute("SELECT count(*), min(captured_at), max(captured_at) FROM sector_snapshot WHERE market_snapshot_id = %s AND provenance = 'observed' "
                    "AND session_date = %s AND feature_set_version = %s", (m[0], session, feature_set_version))
        sec = cur.fetchone()
    cur.execute("SELECT horizon_sessions, count(*), min(created_at), max(created_at), count(DISTINCT run_content_hash), min(run_content_hash), count(*) FILTER (WHERE code_ref = '') "
                "FROM stock_relative_strength WHERE session_date = %s AND provenance = 'observed' AND model_version = %s AND feature_set_version = %s "
                "GROUP BY horizon_sessions", (session, RS_MODEL_VERSION, feature_set_version))
    rs = {r[0]: r for r in cur.fetchall()}
    scan_first = scan_error = None
    try:
        cur.execute("SELECT min(finished_at) FROM price_discontinuity_scan WHERE session_date = %s AND status = 'complete'", (session,))
        scan_first = cur.fetchone()[0]
    except Exception as e:  # noqa: BLE001  migration 32 not applied: no scan evidence can exist
        conn.rollback()
        scan_error = type(e).__name__
    conn.rollback()

    has = {"universe_snapshot": u is not None, "market_snapshot": m is not None, "sector_snapshot": bool(sec[0]),
           **{f"stock_relative_strength[h={h}]": h in rs for h in RS_HORIZONS}}
    detail: Dict[str, Any] = {"components_present": has}
    if not any(has.values()):
        return {"status": SNAPSHOT_ABSENT, "problems": [], "detail": detail}
    missing = sorted(k for k, v in has.items() if not v)
    if [k for k in missing if not k.startswith("stock_relative_strength")]:
        return {"status": SNAPSHOT_PARTIAL, "problems": [f"missing: {', '.join(missing)}"], "detail": detail}
    rs_gaps: List[str] = [f"missing: {k}" for k in missing]

    problems: List[str] = []
    lo = datetime.combine(session, EARLIEST_CLOSE_NY, ny)
    hi = min(mi_inputs.knowledge_cutoff(session), deadline)
    present_h = [h for h in RS_HORIZONS if h in rs]
    stamps = {"universe_snapshot": u[3], "market_snapshot": m[2], "sector_snapshot_min": sec[1], "sector_snapshot_max": sec[2],
              **{f"rs_h{h}_min": rs[h][2] for h in present_h}, **{f"rs_h{h}_max": rs[h][3] for h in present_h}}
    detail.update(universe_snapshot_id=u[0], market_snapshot_id=m[0], n_symbols=u[1], sector_rows=sec[0], stamps={k: _iso(v) for k, v in stamps.items()},
                  window={"not_before": lo.isoformat(), "before": hi.isoformat()}, rs_rows={str(h): rs[h][1] for h in present_h},
                  code_refs={"universe": u[5], "market": m[4]}, source=m[5], scan_first_complete_at=_iso(scan_first))
    if m[1] != u[0]:
        problems.append(f"market_snapshot points at universe_snapshot {m[1]}, not {u[0]}")
    if m[5] != mi_runner.SOURCE:
        problems.append(f"market_snapshot.source is {m[5]!r}, not {mi_runner.SOURCE!r}")
    if not u[4] or not m[3] or not u[5] or not m[4]:
        problems.append("a content hash or code_ref is empty")
    for h in present_h:
        if rs[h][1] != u[1]:
            rs_gaps.append(f"relative-strength rows for horizon {h}: {rs[h][1]} != universe {u[1]}")
    if present_h and (len({rs[h][5] for h in present_h}) != 1 or any(rs[h][4] != 1 for h in present_h)):
        problems.append("the relative-strength rows do not belong to one run (more than one run_content_hash)")
    outside = sorted(k for k, v in stamps.items() if v is None or v < lo or v >= hi)
    if outside:
        problems.append(f"stamped outside the allowed window [{lo.isoformat()}, {hi.isoformat()}): {', '.join(outside)}")
    first_written = min(v for v in stamps.values() if v is not None)
    if scan_error is not None:
        problems.append(f"no scan evidence can be read ({scan_error}: migration 32 not applied)")
    elif scan_first is None:
        problems.append("no complete price-discontinuity scan exists for the session, so the snapshot was not written under scan evidence")
    elif scan_first > first_written:
        problems.append(f"the first complete scan ({scan_first.isoformat()}) is later than the first snapshot row ({first_written.isoformat()})")
    elif scan_first > mi_inputs.knowledge_cutoff(session):
        problems.append("the first complete scan finished after the session's knowledge cutoff")
    if problems:
        return {"status": SNAPSHOT_INCONSISTENT, "problems": problems + rs_gaps, "detail": detail}
    if rs_gaps:
        return {"status": SNAPSHOT_REPAIRABLE, "problems": rs_gaps, "detail": detail}
    return {"status": SNAPSHOT_VALID, "problems": [], "detail": detail}


# ------------------------------------------------------------------ observe: market + sector + stock RS, one transaction
def make_observe(feature_set_version: str = FEATURE_SET_VERSION):
    def observe(ctx: Ctx) -> C.StepResult:
        # R1: a snapshot that is already fully committed and verified is REUSED, read-only, before any freshness check. Live prices that changed after capture
        # (a late bar) do not invalidate evidence that was valid when it was written; they are reported, never acted on. Only a session with NO rows reaches
        # the strict first-time path below; a partial or inconsistent one fails closed here and is never written over.
        with ctx.connect() as conn:
            snap = committed_snapshot(conn, ctx.session, feature_set_version, ctx.deadline)
            fresh = None
            if snap["status"] == SNAPSHOT_VALID:
                try:
                    f = mi_inputs.discontinuity_scan_evidence(conn, ctx.session)
                    fresh = {"live_scan_evidence_ok": f["ok"], "live_scan_evidence_reason": f["reason"]}
                except Exception as e:  # noqa: BLE001  diagnostics only
                    fresh = {"live_scan_evidence_reason": f"unavailable:{type(e).__name__}"}
            conn.rollback()
        if snap["status"] == SNAPSHOT_VALID:
            return C.StepResult(C.STEP_OBSERVE, C.ALREADY, {"reuse": True, "evidence_basis": "committed_snapshot_valid_at_capture", **snap["detail"],
                                                            **(fresh or {}), "note": "read-only: nothing was recomputed or written; current price freshness is "
                                                                                     "informational and does not invalidate evidence valid at capture time"})
        if snap["status"] in (SNAPSHOT_PARTIAL, SNAPSHOT_INCONSISTENT):
            reason = C.REASON_SNAPSHOT_PARTIAL if snap["status"] == SNAPSHOT_PARTIAL else C.REASON_SNAPSHOT_INCONSISTENT
            return C.StepResult(C.STEP_OBSERVE, C.FAILED, {"reason": reason, "problems": snap["problems"], **snap["detail"]},
                                f"the session's observed snapshot is {snap['status']}: " + "; ".join(snap["problems"])[:240])
        with ctx.connect() as conn:
            newest = mi_inputs.latest_stored_dates(conn)["stock_prices"]
        if newest != ctx.session:
            return C.StepResult(C.STEP_OBSERVE, C.FAILED, {"reason": C.REASON_NOT_LATEST if newest and newest > ctx.session else C.REASON_NOT_LOADED,
                                                          "newest_loaded_bar": newest.isoformat() if newest else None},
                                f"the newest stored stock bar is {newest}, not {ctx.session}: an observation needs the session's own prices as the newest bar")
        rep = mi_runner.run(ctx.connect, ctx.session, provenance="observed", apply=ctx.apply, code_ref=ctx.code_ref,
                            feature_set_version=feature_set_version, with_stock_rs=True)
        detail = {"regime_state": rep.regime_state, "n_universe": rep.n_universe, "n_classified": rep.n_classified, "n_sectors": rep.n_sectors,
                  "created": rep.created, "sectors_written": rep.sectors_written, "stock_rs_written": rep.stock_rs_written,
                  "differs_from_stored": rep.differs_from_stored, "warnings": list(rep.warnings)}
        if not ctx.apply:
            return C.StepResult(C.STEP_OBSERVE, C.DRY, detail)
        wrote = bool(rep.created) or (rep.stock_rs_written or 0) > 0 or rep.sectors_written > 0
        return C.StepResult(C.STEP_OBSERVE, C.OK if wrote else C.ALREADY, detail)
    return observe


# ------------------------------------------------------------------ labels
def labels(ctx: Ctx) -> C.StepResult:
    rep = label_runner.run(ctx.connect, ctx.session, apply=ctx.apply)
    detail = {"observations": rep.observations, "evaluated": rep.evaluated, "final": rep.final, "void": rep.void, "pending": dict(rep.pending),
              "inserted": rep.inserted, "already_present": rep.already_present, "restated": len(rep.restated),
              "calendar_source": rep.calendar_source}
    if not ctx.apply:
        return C.StepResult(C.STEP_LABELS, C.DRY, detail)
    return C.StepResult(C.STEP_LABELS, C.OK if rep.inserted else C.ALREADY, detail)


# ------------------------------------------------------------------ verify (read-only read-back)
def make_verify(feature_set_version: str = FEATURE_SET_VERSION):
    def verify(ctx: Ctx) -> C.StepResult:
        if not ctx.apply:
            return C.StepResult(C.STEP_VERIFY, C.DRY, {})
        problems, permanent = [], []
        det: Dict[str, Any] = {}
        with ctx.connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, n_symbols, n_classified, captured_at FROM universe_snapshot WHERE session_date = %s AND provenance = 'observed'",
                        (ctx.session,))
            u = cur.fetchone()
            cur.execute("SELECT id, captured_at, universe_snapshot_id FROM market_snapshot WHERE session_date = %s AND provenance = 'observed' "
                        "AND feature_set_version = %s", (ctx.session, feature_set_version))
            m = cur.fetchone()
            if u is None:
                problems.append("no observed universe_snapshot")
            if m is None:
                problems.append("no observed market_snapshot")
            if u is not None and m is not None:
                det.update(n_symbols=u[1], n_classified=u[2], universe_captured_at=_iso(u[3]), market_captured_at=_iso(m[1]))
                if u[3] >= ctx.deadline or m[1] >= ctx.deadline:
                    permanent.append(f"market/universe stamped at or after the decision deadline {ctx.deadline.isoformat()}")
                cur.execute("SELECT count(*), max(captured_at) FROM sector_snapshot WHERE market_snapshot_id = %s AND provenance = 'observed'", (m[0],))
                ns, smax = cur.fetchone()
                det["sector_rows"] = ns
                if ns == 0:
                    permanent.append("market row has no sector rows (sector rows are written only with a newly created market row)")
                elif smax >= ctx.deadline:
                    permanent.append("a sector row is stamped at or after the decision deadline")
                cur.execute("SELECT horizon_sessions, count(*), max(created_at), "
                            "count(*) FILTER (WHERE state = 'ok' AND sector IS NULL), "
                            "count(*) FILTER (WHERE state = 'ok' AND NOT sector_pit_safe), "
                            "count(*) FILTER (WHERE state = 'ok' AND vs_sector_pp IS NOT NULL AND (sector IS NULL OR NOT sector_pit_safe)) "
                            "FROM stock_relative_strength WHERE session_date = %s AND provenance = 'observed' AND model_version = %s "
                            "AND feature_set_version = %s GROUP BY horizon_sessions", (ctx.session, RS_MODEL_VERSION, feature_set_version))
                by_h = {r[0]: r for r in cur.fetchall()}
                det["rs_rows"] = {str(h): (by_h[h][1] if h in by_h else 0) for h in RS_HORIZONS}
                for h in RS_HORIZONS:
                    if h not in by_h or by_h[h][1] != u[1]:
                        problems.append(f"relative-strength rows for horizon {h}: {by_h[h][1] if h in by_h else 0} != universe {u[1]}")
                    elif by_h[h][2] >= ctx.deadline:
                        permanent.append(f"relative-strength rows for horizon {h} are stamped at or after the decision deadline")
                det["rs_ok_cells_without_sector"] = sum(r[3] for r in by_h.values())
                det["rs_ok_cells_not_sector_pit_safe"] = sum(r[4] for r in by_h.values())
                # Option B: a cell with no PIT-safe sector is a LEGITIMATE row whose sector-relative value is NULL. Only a sector-relative
                # VALUE without a PIT-safe sector is a violation (the table CHECKs forbid it; this re-reads the facts rather than trusting that).
                unsafe_values = sum(r[5] for r in by_h.values())
                det["rs_sector_relative_values_without_pit_safe_sector"] = unsafe_values
                if unsafe_values:
                    permanent.append(f"{unsafe_values} relative-strength rows carry a sector-relative value without a point-in-time-safe sector")
            cur.execute("SELECT (SELECT count(*) FROM market_snapshot WHERE session_date = %s AND provenance = 'reconstructed'), "
                        "(SELECT count(*) FROM stock_relative_strength WHERE session_date = %s AND provenance = 'reconstructed')",
                        (ctx.session, ctx.session))
            det["reconstructed_rows_ignored"] = sum(cur.fetchone())
            conn.rollback()
        det["problems"], det["permanent_problems"] = problems + permanent, permanent
        if problems or permanent:
            return C.StepResult(C.STEP_VERIFY, C.FAILED, det, "; ".join((problems + permanent))[:300])
        return C.StepResult(C.STEP_VERIFY, C.OK, det)
    return verify


def make_steps(*, feature_set_version: str = FEATURE_SET_VERSION, with_capture: bool = False, with_sector_history: bool = False) -> Dict[str, Any]:
    out = {C.STEP_OBSERVE: make_observe(feature_set_version), C.STEP_LABELS: labels, C.STEP_VERIFY: make_verify(feature_set_version)}
    if with_capture:
        out[C.STEP_CAPTURE] = capture_verify
    if with_sector_history:
        out[C.STEP_SECTOR_HISTORY] = make_sector_history_verify()
    return out


def sector_coverage(conn) -> Dict[str, Any]:
    """Read-only: how many symbols of the newest loaded session have a point-in-time-evidenced sector under the writer's own rule."""
    newest = mi_inputs.latest_stored_dates(conn)["stock_prices"]
    if newest is None:
        return {"session": None, "n_universe": 0, "n_classified": 0, "n_without_sector": 0}
    smap, audit = mi_inputs.load_sector_map(conn, newest, mi_inputs.RULE_PIT_EVIDENCED)
    cur = conn.cursor()
    cur.execute("SELECT symbol FROM stock_prices WHERE date = %s", (newest,))
    syms = {r[0] for r in cur.fetchall()}
    conn.rollback()
    classified = sum(1 for s in syms if (smap.get(s) or "").strip() and str(smap[s]).strip().lower() != "unknown")
    return {"session": newest.isoformat(), "n_universe": len(syms), "n_classified": classified, "n_without_sector": len(syms) - classified,
            "pit_evidenced": audit["pit_evidenced"]}
