"""Persistence for Market Intelligence: insert-only writers and observed-by-default readers. The ONLY module here that talks to a database.

Writers never UPDATE or DELETE (the tables refuse both even for their owner). A re-write of an existing key leaves the stored row
untouched and reports created=False with the stored hash, so reconstructing an already-observed session cannot alter it -- the two live
under different provenance keys anyway. Every reader returns `provenance` and honours provenance.read_scope().
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Mapping, Optional

from psycopg2.extras import Json, RealDictCursor

from market_intelligence import events as ev
from market_intelligence import provenance as prov
from market_intelligence.relative_strength import RelativeStrength
from market_intelligence.risk_regime import RiskRegime

FEATURE_SET_VERSION = "mi_v1"        # risk_regime_v1 + rs_v1 as one combined definition


@dataclass
class Written:
    id: int
    created: bool
    content_hash: Optional[str] = None


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, default=_json_default, separators=(",", ":"), allow_nan=False)


def _json_default(o: Any) -> Any:
    if isinstance(o, (date,)):
        return o.isoformat()
    if isinstance(o, Decimal):
        return float(o)
    if hasattr(o, "item"):                 # numpy scalar
        return o.item()
    raise TypeError(f"not JSON-serialisable: {type(o).__name__}")


def _clean(v: Any) -> Any:
    """NaN / inf -> None recursively (a number that is not a number is missing, never a value); numpy scalars -> python."""
    if isinstance(v, dict):
        return {str(k): _clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    if hasattr(v, "item") and not isinstance(v, (str, bytes)):
        v = v.item()
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return v


def _f(v: Any) -> Optional[float]:
    v = _clean(v)
    return None if v is None else float(v)


def content_hash(obj: Any) -> str:
    return hashlib.sha256(canonical(_clean(obj)).encode()).hexdigest()


def _json(obj: Any) -> Json:
    return Json(_clean(obj), dumps=canonical)


# ------------------------------------------------------------------ writers
def write_universe_snapshot(cur, session_date: date, sector_map: Mapping[str, Optional[str]], sector_semantics: Mapping[str, Any],
                            provenance: str, code_ref: str, reconstruction_basis: Optional[str] = None) -> Written:
    if provenance not in prov.PROVENANCES:
        raise ValueError(f"provenance must be one of {prov.PROVENANCES}")
    smap = {str(k): v for k, v in sorted(sector_map.items())}
    h = content_hash({"session_date": session_date, "provenance": provenance, "sector_map": smap})
    cur.execute(
        "INSERT INTO universe_snapshot (session_date, provenance, n_symbols, n_classified, sector_map, sector_source, sector_asof_rule, "
        "sector_pit_safe, content_hash, code_ref, reconstruction_basis) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT (session_date, provenance) DO NOTHING RETURNING id",
        (session_date, provenance, len(smap), sum(1 for v in smap.values() if v), _json(smap), sector_semantics["source"],
         sector_semantics["asof_rule"], bool(sector_semantics["pit_safe"]), h, code_ref, reconstruction_basis))
    row = cur.fetchone()
    if row:
        return Written(row[0], True, h)
    cur.execute("SELECT id, content_hash FROM universe_snapshot WHERE session_date = %s AND provenance = %s", (session_date, provenance))
    ex = cur.fetchone()
    return Written(ex[0], False, ex[1])


def write_session(cur, regime: RiskRegime, rs: RelativeStrength, provenance: str, source: str, code_ref: str,
                  reconstruction_basis: Optional[str] = None, feature_set_version: str = FEATURE_SET_VERSION,
                  measurements: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """One session's universe + market + sector snapshots, insert-only, in the caller's transaction (the caller commits).
    The sector map's own provenance (rs.coverage['sector_map']) must equal the row provenance: an observed row can never be built on a
    reconstructed sector map, and a reconstructed one can never claim PIT-safety."""
    if regime.session_date != rs.session_date:
        raise ValueError("regime and relative-strength are for different sessions")
    sem = rs.coverage.get("sector_map") or {}
    if sem.get("provenance") != provenance:
        raise ValueError(f"sector map provenance {sem.get('provenance')!r} does not match the row provenance {provenance!r}")
    sector_map = ({str(s): (None if v is None or (isinstance(v, float) and math.isnan(v)) else v) for s, v in rs.stocks["sector"].items()}
                  if len(rs.stocks) else {})
    uni = write_universe_snapshot(cur, rs.session_date, sector_map, sem, provenance, code_ref, reconstruction_basis)

    rec, srec = regime.to_record(), rs.session_record()
    sectors = rs.sector_records()
    hashed = {"regime": rec, "rs": srec, "sectors": sectors, "universe": uni.content_hash, "provenance": provenance}
    coverage = dict(rs.coverage)
    if measurements:                        # breadth / sector measurements ride in the JSONB coverage column (migration 24 has no typed home)
        coverage["measurements"] = dict(measurements)
        hashed["measurements"] = dict(measurements)
    h = content_hash(hashed)
    u, sp = rs.universe, rs.spx_ret

    def hz(d: Mapping[int, Any], h_: int, key: Optional[str] = None) -> Any:
        v = d.get(h_)
        return v.get(key) if key and isinstance(v, Mapping) else (None if key else v)

    cur.execute(
        "INSERT INTO market_snapshot (session_date, provenance, feature_set_version, regime_model_version, rs_model_version, regime_state, "
        "regime_score, regime_strength, regime_strength_label, regime_agreement, regime_present_weight, regime_components, regime_reasons, "
        "spx_ret_5, spx_ret_20, spx_ret_60, univ_ret_5, univ_ret_20, univ_ret_60, univ_n_valid_5, univ_n_valid_20, univ_n_valid_60, "
        "coverage, source, universe_snapshot_id, content_hash, code_ref, reconstruction_basis) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT (session_date, feature_set_version, provenance) DO NOTHING RETURNING id",
        (rs.session_date, provenance, feature_set_version, regime.model_version, rs.model_version, regime.state,
         _f(regime.score), _f(regime.strength), regime.strength_label, _f(regime.agreement), _f(regime.present_weight),
         _json(rec["components"]), _json(rec["reasons"]),
         _f(hz(sp, 5)), _f(hz(sp, 20)), _f(hz(sp, 60)),
         _f(hz(u, 5, "ret")), _f(hz(u, 20, "ret")), _f(hz(u, 60, "ret")),
         hz(u, 5, "n_valid"), hz(u, 20, "n_valid"), hz(u, 60, "n_valid"),
         _json(coverage), source, uni.id, h, code_ref, reconstruction_basis))
    row = cur.fetchone()
    if not row:
        cur.execute("SELECT id, content_hash FROM market_snapshot WHERE session_date = %s AND feature_set_version = %s AND provenance = %s",
                    (rs.session_date, feature_set_version, provenance))
        ex = cur.fetchone()
        return {"universe": uni, "market": Written(ex[0], False, ex[1]), "sectors_written": 0, "computed_hash": h}
    market = Written(row[0], True, h)
    n = 0
    for s in rs.sectors:
        ph = s.per_horizon
        vals = [rs.session_date, provenance, feature_set_version, s.sector, s.n_members]
        vals += [ph[k]["n_valid"] for k in (5, 20, 60)] + [ph[k]["n_excluded"] for k in (5, 20, 60)]
        vals += [_f(ph[k]["ret"]) for k in (5, 20, 60)] + [_f(ph[k]["vs_spx"]) for k in (5, 20, 60)]
        vals += [_f(ph[k]["vs_univ"]) for k in (5, 20, 60)] + [s.rank_20, market.id, reconstruction_basis]
        cur.execute(
            "INSERT INTO sector_snapshot (session_date, provenance, feature_set_version, sector, n_members, n_valid_5, n_valid_20, n_valid_60, "
            "n_excluded_5, n_excluded_20, n_excluded_60, sec_ret_5, sec_ret_20, sec_ret_60, sec_vs_spx_5, sec_vs_spx_20, sec_vs_spx_60, "
            "sec_vs_univ_5, sec_vs_univ_20, sec_vs_univ_60, rank_20, market_snapshot_id, reconstruction_basis) "
            "VALUES (" + ",".join(["%s"] * 23) + ") ON CONFLICT (session_date, feature_set_version, provenance, sector) DO NOTHING", vals)
        n += cur.rowcount
    return {"universe": uni, "market": market, "sectors_written": n, "computed_hash": h}


def append_event_revision(cur, d: ev.EventDraft, declared_basis: Optional[str] = None) -> Written:
    """Append one revision of an event. Idempotent: if the latest stored revision has the same payload hash nothing is written.
    ingested_at (and known_at for basis `ingested`) are stamped by the database; the caller cannot supply them."""
    ev.validate(d, declared_basis)
    cur.execute("INSERT INTO market_event (event_key, symbol, event_type) VALUES (%s,%s,%s) ON CONFLICT (event_key) DO NOTHING",
                (d.event_key, d.symbol, d.event_type))
    cur.execute("SELECT symbol, event_type FROM market_event WHERE event_key = %s", (d.event_key,))
    sym, typ = cur.fetchone()
    if sym != d.symbol or typ != d.event_type:
        raise ev.EventValidationError(f"event_key {d.event_key!r} already identifies a different (symbol, type): {(sym, typ)}")
    h = d.payload_hash()
    cur.execute("SELECT revision, payload_hash, id FROM market_event_revision WHERE event_key = %s ORDER BY revision DESC LIMIT 1", (d.event_key,))
    last = cur.fetchone()
    if last and last[1] == h:
        return Written(last[2], False, h)
    known_at = d.published_at if d.known_at_basis == "vendor_published" else None
    cur.execute(
        "INSERT INTO market_event_revision (event_key, revision, event_time, session_timing, published_at, known_at, known_at_basis, source, "
        "source_ref, pit_grade, status, fiscal_period, eps_estimate, eps_actual, revenue_estimate, revenue_actual, payload, payload_hash, "
        "provenance, reconstruction_basis) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (d.event_key, (last[0] + 1) if last else 1, d.event_time, d.session_timing, d.published_at, known_at, d.known_at_basis, d.source,
         d.source_ref, d.pit_grade, d.status, d.fiscal_period, d.eps_estimate, d.eps_actual, d.revenue_estimate, d.revenue_actual,
         _json(dict(d.payload)), h, d.provenance, d.reconstruction_basis))
    return Written(cur.fetchone()[0], True, h)


def write_stock_rs(cur, rs: RelativeStrength, provenance: str, feature_set_version: str, code_ref: str,
                   reconstruction_basis: Optional[str] = None) -> Dict[str, Any]:
    """Per-stock rs_v1 rows (migration 29), insert-only, in the caller's transaction. Every universe stock gets one row per horizon; an unavailable
    measurement is stored as state='unavailable' with NULLs. Idempotent on the table's UNIQUE key: a re-run writes nothing it already has, and a
    re-run whose inputs were restated leaves the stored rows alone (`differs_from_stored`) -- a correction is a new model_version."""
    from market_intelligence.stock_rs_rows import records_hash, stock_rs_records
    sem = rs.coverage.get("sector_map") or {}
    if sem.get("provenance") != provenance:
        raise ValueError(f"sector map provenance {sem.get('provenance')!r} does not match the row provenance {provenance!r}")
    recs = stock_rs_records(rs)
    if not recs:
        return {"n_records": 0, "written": 0, "run_content_hash": None, "differs_from_stored": False}
    h = records_hash(recs, rs.session_date.isoformat(), rs.model_version, feature_set_version, provenance)
    written = 0
    for r in recs:
        cur.execute(
            "INSERT INTO stock_relative_strength (session_date, symbol, horizon_sessions, model_version, feature_set_version, provenance, "
            "reconstruction_basis, sector, sector_pit_safe, state, ret_pct, vs_spx_pp, vs_sector_pp, rs_percentile, n_universe_valid, "
            "benchmark_symbol, run_content_hash, code_ref) VALUES (" + ",".join(["%s"] * 18) + ") "
            "ON CONFLICT (session_date, symbol, horizon_sessions, model_version, feature_set_version, provenance) DO NOTHING",
            (rs.session_date, r["symbol"], r["horizon_sessions"], rs.model_version, feature_set_version, provenance, reconstruction_basis,
             r["sector"], r["sector_pit_safe"], r["state"], r["ret_pct"], r["vs_spx_pp"], r["vs_sector_pp"], r["rs_percentile"],
             r["n_universe_valid"], r["benchmark_symbol"], h, code_ref))
        written += cur.rowcount
    cur.execute("SELECT count(*) FROM stock_relative_strength WHERE session_date = %s AND model_version = %s AND feature_set_version = %s "
                "AND provenance = %s AND run_content_hash <> %s",
                (rs.session_date, rs.model_version, feature_set_version, provenance, h))
    return {"n_records": len(recs), "written": written, "run_content_hash": h, "differs_from_stored": cur.fetchone()[0] > 0}


# ------------------------------------------------------------------ readers (observed-only unless explicitly widened)
def _num(v: Any) -> Any:
    return float(v) if isinstance(v, Decimal) else v


def _row(r: Mapping[str, Any]) -> Dict[str, Any]:
    out = {k: _num(v) for k, v in r.items()}
    for k, v in list(out.items()):
        if type(v) is date:                # exact date only: datetimes (known_at...) stay datetimes for PIT comparisons
            out[k] = v.isoformat()
    return out


def get_market_snapshot(cur, session_date: Optional[date] = None, provenance: Optional[str] = None, include_reconstructed: bool = False,
                        feature_set_version: str = FEATURE_SET_VERSION) -> Optional[Dict[str, Any]]:
    """The market snapshot for a session (default: the latest observed one) with its universe/sector-map semantics. None when absent."""
    clause, params = prov.sql_filter("m.provenance", provenance, include_reconstructed)
    where, args = [clause, "m.feature_set_version = %s"], list(params) + [feature_set_version]
    if session_date is not None:
        where.append("m.session_date = %s")
        args.append(session_date)
    cur2 = cur.connection.cursor(cursor_factory=RealDictCursor)
    cur2.execute(
        "SELECT m.*, u.sector_source, u.sector_asof_rule, u.sector_pit_safe, u.n_symbols, u.n_classified FROM market_snapshot m "
        "JOIN universe_snapshot u ON u.id = m.universe_snapshot_id WHERE " + " AND ".join(where) +
        " ORDER BY m.session_date DESC, (m.provenance = 'observed') DESC LIMIT 1", args)
    r = cur2.fetchone()
    return _row(r) if r else None


def list_market_snapshots(cur, limit: int = 30, provenance: Optional[str] = None, include_reconstructed: bool = False,
                          feature_set_version: str = FEATURE_SET_VERSION) -> List[Dict[str, Any]]:
    clause, params = prov.sql_filter("provenance", provenance, include_reconstructed)
    cur2 = cur.connection.cursor(cursor_factory=RealDictCursor)
    cur2.execute("SELECT session_date, provenance, regime_state, regime_score, regime_strength_label, regime_present_weight, "
                 "spx_ret_20, univ_ret_20, regime_model_version, rs_model_version FROM market_snapshot WHERE " + clause +
                 " AND feature_set_version = %s ORDER BY session_date DESC, provenance LIMIT %s",
                 list(params) + [feature_set_version, max(1, min(int(limit), 500))])
    return [_row(r) for r in cur2.fetchall()]


def get_sector_snapshots(cur, session_date: date, provenance: Optional[str] = None, include_reconstructed: bool = False,
                         feature_set_version: str = FEATURE_SET_VERSION) -> List[Dict[str, Any]]:
    clause, params = prov.sql_filter("provenance", provenance, include_reconstructed)
    cur2 = cur.connection.cursor(cursor_factory=RealDictCursor)
    cur2.execute("SELECT * FROM sector_snapshot WHERE " + clause + " AND session_date = %s AND feature_set_version = %s "
                 "ORDER BY provenance, rank_20 NULLS LAST, sector", list(params) + [session_date, feature_set_version])
    return [_row(r) for r in cur2.fetchall()]


def get_event_revisions(cur, event_keys: Optional[List[str]] = None, include_reconstructed: bool = False) -> List[Dict[str, Any]]:
    """Every stored revision (observed-only by default). Feed the result to events.visible_as_of / events.ml_view; do not filter here."""
    clause, params = prov.sql_filter("r.provenance", None, include_reconstructed)
    where, args = [clause], list(params)
    if event_keys is not None:
        where.append("r.event_key = ANY(%s)")
        args.append(list(event_keys))
    cur2 = cur.connection.cursor(cursor_factory=RealDictCursor)
    cur2.execute("SELECT r.*, e.symbol, e.event_type FROM market_event_revision r JOIN market_event e USING (event_key) WHERE "
                 + " AND ".join(where) + " ORDER BY r.event_key, r.revision", args)
    return [_row(r) for r in cur2.fetchall()]


def get_stock_rs(cur, session_date: date, symbols: Optional[List[str]] = None, model_version: str = "rs_v1",
                 feature_set_version: str = "mi_v2", include_reconstructed: bool = False, known_by: Optional[Any] = None) -> List[Dict[str, Any]]:
    """Per-stock rows for one session. Observed-only by default; `known_by` (tz-aware) restricts to rows whose DB-stamped created_at is <= it, so a
    research read can never see a row that did not yet exist. A missing measurement is state='unavailable' with None, never 0."""
    clause, params = prov.sql_filter("provenance", None, include_reconstructed)
    where, args = [clause, "session_date = %s", "model_version = %s", "feature_set_version = %s"], list(params) + [session_date, model_version, feature_set_version]
    if symbols is not None:
        where.append("symbol = ANY(%s)")
        args.append(list(symbols))
    if known_by is not None:
        if getattr(known_by, "tzinfo", None) is None:
            raise ValueError("known_by must be timezone-aware")
        where.append("created_at <= %s")
        args.append(known_by)
    cur2 = cur.connection.cursor(cursor_factory=RealDictCursor)
    cur2.execute("SELECT * FROM stock_relative_strength WHERE " + " AND ".join(where) + " ORDER BY symbol, horizon_sessions, provenance", args)
    return [_row(r) for r in cur2.fetchall()]
