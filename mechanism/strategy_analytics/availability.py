# mechanism/strategy_analytics/availability.py
"""Data-driven research capabilities: what the database really holds for one strategy, measured at request time.

Replaces the static `definitions.CAPABILITIES` constants (last edited 2026-10-04), which kept declaring forward labels,
candidate observations and feature snapshots "not collected (Release B)" after production held 29k / 15k / 15k rows.

Every capability reports:
  state            available | partial | unavailable
                   (partial = rows exist but do not cover every ledger row of the strategy, e.g. capture that started
                   after the ledger did; unavailable = table absent, no rows, or not computed by this API)
  available        bool, = state != unavailable   (kept for existing consumers)
  provenance       observed | derived | none      (observed = captured forward by our own job, DB-stamped;
                   derived = computed from observed prices after the fact; none = nothing stored)
  rows             stored rows for this strategy (None when the table is absent)
  period           {first, last} sessions those rows cover (None when there are none)
  coverage         {ledger_rows_covered, ledger_rows_total}
  computed         whether this API computes/serves the values (False = derivable but not implemented here)
  source           exact table / column (and migration)
  pit_limitations  what the data can and cannot claim about decision time
  release, reason  kept for existing consumers

Nothing here writes, backfills, or turns retrospective data into "observed". ML-dependent capabilities stay unavailable
until a model passes the promotion gate.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

Fetch = Callable[[str, Any], List[Dict[str, Any]]]   # same shape as analytics.Fetch (no import: analytics imports this module)

AVAILABLE, PARTIAL, UNAVAILABLE = "available", "partial", "unavailable"
OBSERVED, DERIVED, NONE = "observed", "derived", "none"

# Static facts about each capability (source and point-in-time limits). Availability itself is never static.
CATALOG: Dict[str, Dict[str, Any]] = {
    "signal_ledger": {"release": "A", "source": "signal_ledger (migration 18)",
                      "pit": "Written at the signal session's close (nightly pipeline); trade plan frozen at signal time."},
    "outcome_evaluation": {"release": "A", "source": "signal_ledger.status/outcome_r (nightly evaluator)",
                           "pit": "Outcome of the live 1R first-target rule (close entry, no gaps, no cost); not the 3R backtest rule."},
    "same_bar_ambiguity_flag": {"release": "A", "source": "signal_ledger.resolution_flag", "pit": "Daily bars cannot order a stop and a target touched on one bar; recorded as stopped."},
    "split_suspect_hold": {"release": "A", "source": "signal_ledger.evaluation_flag", "pit": "Rows held out of evaluation until reviewed."},
    "max_adverse_excursion": {"release": "A", "source": "signal_ledger.mae_r",
                              "pit": "Outcome metric over the bars evaluated so far (up to resolution)."},
    "candidate_observations": {"release": "B", "source": "candidate_observation (migration 22), linked by signal_ledger.observation_id",
                               "pit": "Captured at the session close, before the universe guards (rejected candidates kept). Starts at the capture activation session; earlier ledger rows have no candidate record."},
    "feature_snapshots": {"release": "B", "source": "feature_snapshot (migration 22), linked by signal_ledger.feature_snapshot_id",
                          "pit": "T0 features from bars up to and including the session; same start as candidate capture."},
    "forward_return_labels": {"release": "B", "source": "forward_return_label (fwd_v1), via candidate_observation",
                              "pit": "Outcome labels (fixed horizons from the signal close), computed after the horizon matures from observed prices; not decision-time information. Only for captured candidates."},
    "max_favourable_excursion": {"release": "B", "source": "stock_prices (observed daily bars) after signal_date",
                                 "pit": "Outcome metric; derivable for every ledger row with an evaluated bar, but not stored and not computed by this API yet."},
    "post_exit_trajectory": {"release": "B", "source": "stock_prices after signal_ledger.resolved_date",
                             "pit": "Price after the trade ended; not part of the strategy's result. Derivable, not computed by this API yet."},
    "training_ready_observations": {"release": "B", "source": "candidate_observation + feature_snapshot + forward_return_label",
                                    "pit": "Not exposed: no model passes the promotion gate, so ML stays out of the signal path and no training set is served."},
    "evaluator_run_history": {"release": None, "source": "VPS wrapper log only",
                              "pit": "Evaluator run results are only in the VPS wrapper log, not the database -- see data health."},
}


def _exists(fetch: Fetch, table: str) -> bool:
    return bool(fetch("SELECT to_regclass(%s) IS NOT NULL AS ok", (table,))[0]["ok"])


def _rec(name: str, state: str, provenance: str, *, rows: Optional[int], first=None, last=None, covered: Optional[int] = None,
         total: int = 0, computed: bool, reason: Optional[str] = None, **extra: Any) -> Dict[str, Any]:
    c = CATALOG[name]
    out: Dict[str, Any] = {
        "available": state != UNAVAILABLE, "state": state, "provenance": provenance, "rows": rows,
        "period": {"first": first, "last": last} if first is not None else None,
        "coverage": {"ledger_rows_covered": covered, "ledger_rows_total": total} if covered is not None else None,
        "computed": computed, "source": c["source"], "pit_limitations": c["pit"], "release": c["release"],
    }
    if reason:
        out["reason"] = reason
    out.update(extra)
    return out


def _linked_state(rows: Optional[int], covered: int, total: int) -> str:
    if not rows:
        return UNAVAILABLE
    return AVAILABLE if total and covered >= total else PARTIAL


def capabilities(fetch: Fetch, strategy: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    sid = strategy["id"]
    led = fetch("""
        SELECT count(*) AS total, min(signal_date) AS first, max(signal_date) AS last,
               count(*) FILTER (WHERE status <> 'open') AS resolved,
               count(*) FILTER (WHERE mae_r IS NOT NULL) AS with_mae,
               count(*) FILTER (WHERE resolution_flag IS NOT NULL) AS ambiguous,
               count(*) FILTER (WHERE evaluation_flag IS NOT NULL) AS held,
               count(*) FILTER (WHERE last_evaluated_date > signal_date) AS with_bar,
               count(*) FILTER (WHERE observation_id IS NOT NULL) AS with_obs,
               count(*) FILTER (WHERE feature_snapshot_id IS NOT NULL) AS with_snap,
               min(signal_date) FILTER (WHERE observation_id IS NOT NULL) AS first_obs
        FROM signal_ledger WHERE strategy_id = %s""", (sid,))[0]
    total = int(led["total"])
    first, last = led["first"], led["last"]
    out: Dict[str, Dict[str, Any]] = {}

    ledger_state = AVAILABLE if total else UNAVAILABLE
    out["signal_ledger"] = _rec("signal_ledger", ledger_state, OBSERVED if total else NONE, rows=total, first=first, last=last,
                                covered=total, total=total, computed=True)
    out["outcome_evaluation"] = _rec("outcome_evaluation", ledger_state, DERIVED if total else NONE, rows=int(led["resolved"]),
                                     first=first, last=last, covered=total, total=total, computed=True)
    out["same_bar_ambiguity_flag"] = _rec("same_bar_ambiguity_flag", ledger_state, DERIVED if total else NONE, rows=int(led["ambiguous"]),
                                          computed=True)
    out["split_suspect_hold"] = _rec("split_suspect_hold", ledger_state, DERIVED if total else NONE, rows=int(led["held"]), computed=True)
    out["max_adverse_excursion"] = _rec("max_adverse_excursion", _linked_state(int(led["with_mae"]), int(led["with_mae"]), total),
                                        DERIVED if led["with_mae"] else NONE, rows=int(led["with_mae"]), first=first, last=last,
                                        covered=int(led["with_mae"]), total=total, computed=True)

    # ---- captured research tables (Release B, observed)
    if _exists(fetch, "candidate_observation"):
        co = fetch("""SELECT count(*) AS n, min(session_date) AS first, max(session_date) AS last, count(DISTINCT session_date) AS sessions
                      FROM candidate_observation WHERE strategy_id = %s""", (sid,))[0]
        n = int(co["n"])
        out["candidate_observations"] = _rec("candidate_observations", _linked_state(n, int(led["with_obs"]), total), OBSERVED if n else NONE,
                                             rows=n, first=co["first"], last=co["last"], covered=int(led["with_obs"]), total=total,
                                             computed=True, sessions=int(co["sessions"]),
                                             reason=None if n else "no candidate has been captured for this strategy")
    else:
        out["candidate_observations"] = _rec("candidate_observations", UNAVAILABLE, NONE, rows=None, computed=False,
                                             reason="table candidate_observation does not exist (migration 22 not applied)")
    if _exists(fetch, "feature_snapshot") and _exists(fetch, "candidate_observation"):
        fs = fetch("""SELECT count(DISTINCT f.id) AS n, min(f.session_date) AS first, max(f.session_date) AS last
                      FROM feature_snapshot f JOIN candidate_observation c ON c.snapshot_id = f.id AND c.strategy_id = %s""", (sid,))[0]
        n = int(fs["n"])
        out["feature_snapshots"] = _rec("feature_snapshots", _linked_state(n, int(led["with_snap"]), total), OBSERVED if n else NONE,
                                        rows=n, first=fs["first"], last=fs["last"], covered=int(led["with_snap"]), total=total, computed=True,
                                        reason=None if n else "no feature snapshot has been captured for this strategy")
    else:
        out["feature_snapshots"] = _rec("feature_snapshots", UNAVAILABLE, NONE, rows=None, computed=False,
                                        reason="table feature_snapshot does not exist (migration 22 not applied)")
    if _exists(fetch, "forward_return_label") and _exists(fetch, "candidate_observation"):
        fl = fetch("""SELECT count(*) AS n, count(*) FILTER (WHERE f.label_status = 'final') AS final,
                             min(f.t0_session) AS first, max(f.t0_session) AS last,
                             array_agg(DISTINCT f.horizon_sessions ORDER BY f.horizon_sessions) AS horizons
                      FROM forward_return_label f JOIN candidate_observation c ON c.id = f.observation_id AND c.strategy_id = %s""", (sid,))[0]
        cov = int(fetch("""SELECT count(DISTINCT l.id) AS n FROM signal_ledger l
                           JOIN forward_return_label f ON f.observation_id = l.observation_id AND f.label_status = 'final'
                           WHERE l.strategy_id = %s""", (sid,))[0]["n"])
        n = int(fl["n"])
        out["forward_return_labels"] = _rec("forward_return_labels", _linked_state(n, cov, total), DERIVED if n else NONE, rows=n,
                                            first=fl["first"], last=fl["last"], covered=cov, total=total, computed=False,
                                            final_labels=int(fl["final"]), horizons_sessions=[int(h) for h in (fl["horizons"] or []) if h is not None],
                                            reason=None if n else "no forward label has been computed for this strategy's candidates")
    else:
        out["forward_return_labels"] = _rec("forward_return_labels", UNAVAILABLE, NONE, rows=None, computed=False,
                                            reason="table forward_return_label does not exist")

    # ---- derivable but not computed by this API (honest: unavailable here, with what it would cover)
    out["max_favourable_excursion"] = _rec("max_favourable_excursion", UNAVAILABLE, NONE, rows=None, computed=False,
                                           derivable_ledger_rows=int(led["with_bar"]),
                                           reason="not stored and not computed by this API yet; derivable from stock_prices")
    out["post_exit_trajectory"] = _rec("post_exit_trajectory", UNAVAILABLE, NONE, rows=None, computed=False,
                                       derivable_ledger_rows=int(led["resolved"]),
                                       reason="not stored and not computed by this API yet; derivable from stock_prices")
    out["training_ready_observations"] = _rec("training_ready_observations", UNAVAILABLE, NONE, rows=None, computed=False,
                                              reason="not exposed: no model passes the promotion gate")
    out["evaluator_run_history"] = _rec("evaluator_run_history", UNAVAILABLE, NONE, rows=None, computed=False,
                                        reason=CATALOG["evaluator_run_history"]["pit"])
    return out


def catalog() -> Dict[str, Dict[str, Any]]:
    """Static description for the no-database definitions endpoint: names, sources and limits -- never availability."""
    return {k: {"release": v["release"], "source": v["source"], "pit_limitations": v["pit"],
                "availability": "measured per strategy: see /api/strategies/{key}/{version}/summary"} for k, v in CATALOG.items()}
