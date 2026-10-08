"""Market Intelligence runner (`mi_v2`): regime + breadth + sector + relative strength for ONE explicit session, written insert-only to the
migration-24 tables. NOT SCHEDULED and not imported by anything in the pipeline: there is no timer, no orchestrator stage and no Telegram path.
Writing needs migration 24 applied and INSERT on its tables; applying either in production is a separate, owner-authorised stage.

    python -m market_intelligence.runner --session 2026-09-23 --provenance reconstructed            # dry run (read-only)
    python -m market_intelligence.runner --session 2026-09-23 --provenance reconstructed --apply --code-ref <git-sha>

Properties
    session-explicit   the session is a required argument; the clock is never read (`--session` is validated against the stored panel)
    fails closed       a date that is not a real session of the stock panel raises SessionNotAvailable; it never produces an UNAVAILABLE row
    deterministic      the same stored inputs give the same content_hash (no timestamps, no ordering dependence)
    idempotent         the tables' UNIQUE keys + ON CONFLICT DO NOTHING: a re-run, or two concurrent runs, write each row exactly once; a re-run
                       whose inputs were restated reports `differs_from_stored` and leaves the stored row alone (a correction is a new
                       feature_set_version, never an edit)
    honest provenance  `observed` is allowed only for the latest stored session, with a point-in-time-evidenced sector map and no retroactive
                       discontinuity detections; everything else is `reconstructed` and says how
Never a Donchian filter, an eligibility rule, a screener score input or an ML feature.
"""
from __future__ import annotations

import argparse
import sys
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Dict, List, Optional, Sequence

import pandas as pd

from market_intelligence import breadth as bd
from market_intelligence import inputs
from market_intelligence import relative_strength as rs_mod
from market_intelligence import risk_regime as rr
from market_intelligence import sector_intel as sec
from market_intelligence import store

FEATURE_SET_VERSION = "mi_v2"            # risk_regime_v1 + rs_v1 + breadth_v1 + sector_v1
SOURCE = "mi_runner_v2"
ASOF_RULE_TEXT = {
    inputs.RULE_PIT_EVIDENCED: "latest daily_fundamentals row with date <= session whose write time (created_at/updated_at) is <= session",
    inputs.RULE_PROJECTED: "latest daily_fundamentals row with date <= session, whatever its write time (today's metadata projected backwards)",
}


class SessionNotAvailable(RuntimeError):
    """The requested date is not a real, loaded session of the stock panel."""


class ProvenanceRefused(RuntimeError):
    """`observed` was requested but the evidence for it is missing."""


@dataclass
class Computed:
    session_date: date
    provenance: str
    regime: rr.RiskRegime
    rs: rs_mod.RelativeStrength
    breadth: Dict[str, Any]
    sectors: List[Dict[str, Any]]
    measurements: Dict[str, Any]
    reconstruction_basis: Optional[str]
    warnings: List[str] = field(default_factory=list)


@dataclass
class RunReport:
    session_date: date
    provenance: str
    feature_set_version: str
    applied: bool
    regime_state: str
    regime_score: Optional[float]
    regime_present_weight: float
    n_universe: int
    n_classified: int
    n_sectors: int
    breadth_market: Dict[str, Any]
    sector_audit: Dict[str, Any]
    created: Optional[bool] = None
    sectors_written: int = 0
    stock_rs_written: Optional[int] = None      # None = not requested (--with-stock-rs)
    content_hash: Optional[str] = None
    differs_from_stored: bool = False
    warnings: List[str] = field(default_factory=list)


def _basis(rule: str) -> str:
    return (f"{SOURCE}: recomputed from stock_prices / market_index_prices as stored when the run was made (the provider restates history in "
            f"place, so a later recomputation can differ from what a live run saw); sector map rule={rule}: {ASOF_RULE_TEXT[rule]}")


def compute_session(conn, session_date: date, *, provenance: str, sector_rule: str = inputs.RULE_PIT_EVIDENCED) -> Computed:
    """Read-only. Raises SessionNotAvailable / ProvenanceRefused; never returns a partial or placeholder result."""
    if not isinstance(session_date, date):
        raise TypeError("session_date must be an explicit date")
    if provenance not in ("observed", "reconstructed"):
        raise ValueError("provenance must be 'observed' or 'reconstructed' (explicit; there is no default)")
    if sector_rule not in inputs.RULES:
        raise ValueError(f"sector_rule must be one of {inputs.RULES}")
    close, high, low = inputs.load_price_panel(conn, session_date)
    if close is None:
        raise SessionNotAvailable(f"no stock_prices rows on or before {session_date}")
    real = rr.real_sessions_only(close)[0]
    if len(real) == 0 or real.index[-1] != pd.Timestamp(session_date):
        raise SessionNotAvailable(f"{session_date} is not a real session of the stock panel (no or partial load): refusing to write a row")
    idx = inputs.load_index_closes(conn, session_date)
    disc, late_disc, untrusted_disc = inputs.load_discontinuities(conn, session_date)
    smap, audit = inputs.load_sector_map(conn, session_date, sector_rule)
    warnings: List[str] = []

    if provenance == "observed":
        latest = inputs.latest_stored_dates(conn)
        problems = []
        if sector_rule != inputs.RULE_PIT_EVIDENCED or not audit["pit_evidenced"]:
            problems.append("the sector map is not point-in-time evidenced")
        if latest["stock_prices"] != session_date:
            problems.append(f"the newest stored stock bar is {latest['stock_prices']}, not the session (a run for an older session is not a live capture)")
        if latest["market_index_prices"] is not None and latest["market_index_prices"] > session_date:
            problems.append("index bars exist after the session")
        if late_disc:
            problems.append(f"{late_disc} price discontinuities were detected after the session")
        if untrusted_disc:
            problems.append(f"{untrusted_disc} price discontinuities have no trustworthy detection time")
        if problems:
            raise ProvenanceRefused("observed refused: " + "; ".join(problems))
    else:
        if late_disc:
            warnings.append(f"{late_disc} discontinuity rows were detected after the session (retroactive information)")
        if untrusted_disc:
            warnings.append(f"{untrusted_disc} discontinuity rows have no trustworthy detection time")

    regime = rr.compute(session_date, idx, close, high, low)
    rel = rs_mod.compute(session_date, close, smap, idx, disc, sector_provenance=provenance)
    rel.coverage["sector_map"] = rs_mod.sector_map_semantics(provenance, ASOF_RULE_TEXT[sector_rule])
    rel.coverage["sector_audit"] = audit
    breadth = bd.compute(session_date, close, high, low, smap)
    sectors = sec.build(rel, breadth)
    if audit["n_classified"] == 0:
        warnings.append("no symbol has a point-in-time-evidenced sector: sector intelligence is empty, not neutral")
    measurements = {
        "versions": {"regime": rr.MODEL_VERSION, "rs": rs_mod.MODEL_VERSION, "breadth": bd.BREADTH_VERSION, "sector": sec.SECTOR_VERSION},
        "breadth": breadth, "sectors": sectors, "sector_audit": audit,
        "price_basis": "provider_adjusted_as_stored",
        "pit_limitations": ["prices are provider-adjusted and restated in place: a reconstructed run is not what a live run saw",
                            "the sector map is point-in-time only under rule pit_evidenced"],
    }
    return Computed(session_date, provenance, regime, rel, breadth, sectors, measurements,
                    None if provenance == "observed" else _basis(sector_rule), warnings)


def run(connect: Callable[[], AbstractContextManager], session_date: date, *, provenance: str,
        sector_rule: str = inputs.RULE_PIT_EVIDENCED, apply: bool = False, code_ref: Optional[str] = None,
        feature_set_version: str = FEATURE_SET_VERSION, with_stock_rs: bool = False) -> RunReport:
    if apply and not code_ref:
        raise ValueError("code_ref (the git revision of the code that computed the row) is required to write")
    with connect() as conn:
        c = compute_session(conn, session_date, provenance=provenance, sector_rule=sector_rule)
        cov = c.rs.coverage
        report = RunReport(
            session_date=session_date, provenance=provenance, feature_set_version=feature_set_version, applied=apply,
            regime_state=c.regime.state, regime_score=c.regime.score, regime_present_weight=c.regime.present_weight,
            n_universe=cov.get("n_universe", 0), n_classified=cov.get("n_classified", 0), n_sectors=len(c.sectors),
            breadth_market={k: {kk: v[kk] for kk in ("numerator", "denominator", "universe", "missing", "pct", "state")}
                            for k, v in c.breadth["market"].items()},
            sector_audit=c.measurements["sector_audit"], warnings=c.warnings)
        if not apply:
            conn.rollback()
            return report
        cur = conn.cursor()
        res = store.write_session(cur, c.regime, c.rs, provenance, SOURCE, code_ref, c.reconstruction_basis,
                                  feature_set_version=feature_set_version, measurements=c.measurements)
        if with_stock_rs:                   # opt-in: migration 29's per-stock table; default off so mi_v2's requirements are unchanged
            srs = store.write_stock_rs(cur, c.rs, provenance, feature_set_version, code_ref, c.reconstruction_basis)
            report.stock_rs_written = srs["written"]
            if srs["differs_from_stored"]:
                report.warnings.append("per-stock rows for this session/version/provenance exist with a different run hash; they were not changed")
        conn.commit()
        report.created = res["market"].created
        report.sectors_written = res["sectors_written"]
        report.content_hash = res["market"].content_hash
        report.differs_from_stored = (not res["market"].created) and res["market"].content_hash != res["computed_hash"]
        if report.differs_from_stored:
            report.warnings.append("a row for this session/version/provenance already exists with a different content hash; it was not "
                                   "changed (a correction needs a new feature_set_version)")
    return report


def stock_relative_strength(connect: Callable[[], AbstractContextManager], session_date: date, symbols: Optional[Sequence[str]] = None,
                            *, sector_rule: str = inputs.RULE_PIT_EVIDENCED) -> pd.DataFrame:
    """Read-only per-stock rs_v1 measurements for one explicit session: ret_h, vs_spx_h (stock vs market), vs_sector_h (stock vs its sector
    median), rs_pctile_h, for h in 5/20/60. NaN where a measurement is unavailable (never 0). Not persisted: migration 24 has no per-stock table."""
    with connect() as conn:
        close, _, _ = inputs.load_price_panel(conn, session_date)
        real = None if close is None else rr.real_sessions_only(close)[0]
        if real is None or len(real) == 0 or real.index[-1] != pd.Timestamp(session_date):
            raise SessionNotAvailable(f"{session_date} is not a real session of the stock panel")
        idx = inputs.load_index_closes(conn, session_date)
        disc, _, _ = inputs.load_discontinuities(conn, session_date)
        smap, _ = inputs.load_sector_map(conn, session_date, sector_rule)
        conn.rollback()
    out = rs_mod.compute(session_date, close, smap, idx, disc, sector_provenance="reconstructed").stocks
    return out if symbols is None else out.reindex(list(symbols))


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--session", required=True, type=date.fromisoformat)
    ap.add_argument("--provenance", required=True, choices=("observed", "reconstructed"))
    ap.add_argument("--sector-rule", default=inputs.RULE_PIT_EVIDENCED, choices=inputs.RULES)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--code-ref")
    ap.add_argument("--with-stock-rs", action="store_true", help="also write per-stock rs_v1 rows (needs migration 29; default off)")
    a = ap.parse_args(argv)
    from shared.database import db
    if not db.initialize_sync_pool():
        print("database unavailable", file=sys.stderr)
        return 2
    try:
        print(run(db.get_sync_connection, a.session, provenance=a.provenance, sector_rule=a.sector_rule, apply=a.apply, code_ref=a.code_ref,
                        with_stock_rs=a.with_stock_rs))
        return 0
    except (SessionNotAvailable, ProvenanceRefused) as e:
        print(f"refused (failing closed): {e}", file=sys.stderr)
        return 3
    finally:
        db.close_pools()


if __name__ == "__main__":
    sys.exit(main())
