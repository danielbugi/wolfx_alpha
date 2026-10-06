"""The composition root: the ONLY module that imports the existing writers (the Market Intelligence runner and the fwd_v1 label runner).

Each function takes the orchestrator's context and returns a StepResult. Nothing here computes a research value: it calls the writers that
already exist, and `verify` only READS the rows they wrote back. A step never reports success on the strength of another step.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

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
    session; it cannot create a missing capture and says so."""
    with ctx.connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT ON (strategy_id) strategy_id, status, captured, already_captured, candidates, run_finished_at "
                    "FROM candidate_capture_run WHERE session_date = %s ORDER BY strategy_id, id DESC", (ctx.session,))
        runs = cur.fetchall()
        cur.execute("SELECT count(*), count(*) FILTER (WHERE fs.sector IS NULL) FROM candidate_observation co "
                    "JOIN feature_snapshot fs ON fs.id = co.snapshot_id WHERE co.session_date = %s", (ctx.session,))
        n_cand, n_nosec = cur.fetchone()
        conn.rollback()
    detail = {"runs": [{"strategy_id": r[0], "status": r[1], "candidates": r[4], "captured": r[2], "already_captured": r[3]} for r in runs],
              "candidate_rows": n_cand, "candidate_rows_without_sector": n_nosec}
    if not runs:
        return C.StepResult(C.STEP_CAPTURE, C.FAILED, {**detail, "reason": "no_capture_run_for_session"},
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


# ------------------------------------------------------------------ observe: market + sector + stock RS, one transaction
def make_observe(feature_set_version: str = FEATURE_SET_VERSION):
    def observe(ctx: Ctx) -> C.StepResult:
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
