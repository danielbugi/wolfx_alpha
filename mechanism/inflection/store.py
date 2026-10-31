"""Database access for the inflection engine: idempotent writes of features and events, run log, and the loaders that turn tables into the engine's in-memory inputs.

Writes are keyed on the identities in migration 40, so a second run with nothing new changes nothing: features are upserted only when their content differs; an event whose content hash is
unchanged only refreshes its market context; an event whose content changed (a restated quarter) is updated in place with revision_count + 1 and keeps its first_detected_at.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from psycopg2.extras import Json, execute_values

from inflection import config as C
from inflection import context as X
from inflection.runner import Inputs
from shared import db, retry_on_failure

FEATURE_COLS = ("symbol", "quarter_end", "method_version", "fiscal_year", "fiscal_quarter", "available_at", "availability_basis", "metrics", "score", "components",
                "data_quality", "quality_notes", "source", "source_updated_at")
EVENT_COLS = ("symbol", "quarter_end", "event_type", "method_version", "fiscal_year", "fiscal_quarter", "strength", "severity", "score", "metrics", "explanation", "components", "context",
              "available_at", "availability_basis", "source", "source_updated_at", "data_quality", "quality_notes", "event_hash")
JSON_FIELDS = {"metrics", "components", "quality_notes", "explanation", "context"}


def _json(v: Any) -> Json:
    return Json(v, dumps=lambda o: json.dumps(o, default=str, allow_nan=False))


def _row(rec: Mapping[str, Any], cols: Sequence[str]) -> Tuple:
    return tuple(_json(rec[c]) if c in JSON_FIELDS else rec[c] for c in cols)


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ writes
@retry_on_failure(max_retries=3, delay=1.0)
def upsert_features(records: Sequence[Mapping[str, Any]]) -> int:
    if not records:
        return 0
    sql = f"""INSERT INTO fundamental_feature ({', '.join(FEATURE_COLS)}) VALUES %s
        ON CONFLICT (symbol, quarter_end, method_version) DO UPDATE SET
            fiscal_year = EXCLUDED.fiscal_year, fiscal_quarter = EXCLUDED.fiscal_quarter, available_at = EXCLUDED.available_at, availability_basis = EXCLUDED.availability_basis,
            metrics = EXCLUDED.metrics, score = EXCLUDED.score, components = EXCLUDED.components, data_quality = EXCLUDED.data_quality, quality_notes = EXCLUDED.quality_notes,
            source = EXCLUDED.source, source_updated_at = EXCLUDED.source_updated_at, computed_at = NOW()
        WHERE (fundamental_feature.metrics, fundamental_feature.score, fundamental_feature.available_at, fundamental_feature.data_quality, fundamental_feature.quality_notes)
              IS DISTINCT FROM (EXCLUDED.metrics, EXCLUDED.score, EXCLUDED.available_at, EXCLUDED.data_quality, EXCLUDED.quality_notes)"""
    with db.get_sync_connection() as conn:
        cur = conn.cursor()
        execute_values(cur, sql, [_row(r, FEATURE_COLS) for r in records], page_size=500)
        conn.commit()
        return len(records)


@retry_on_failure(max_retries=3, delay=1.0)
def upsert_events(records: Sequence[Mapping[str, Any]]) -> Dict[str, int]:
    """Returns {'new', 'revised', 'unchanged'} by comparing the stored content hash first (so the counts are exact), then upserting."""
    if not records:
        return {"new": 0, "revised": 0, "unchanged": 0}
    symbols = sorted({r["symbol"] for r in records})
    existing = {(r["symbol"], r["quarter_end"], r["event_type"], r["method_version"]): r["event_hash"].strip()
                for r in db.execute_dict_query("SELECT symbol, quarter_end, event_type, method_version, event_hash FROM fundamental_inflection_event WHERE symbol = ANY(%s)", (symbols,))}
    counts = {"new": 0, "revised": 0, "unchanged": 0}
    for r in records:
        h = existing.get((r["symbol"], r["quarter_end"], r["event_type"], r["method_version"]))
        counts["new" if h is None else "unchanged" if h == r["event_hash"] else "revised"] += 1
    sql = f"""INSERT INTO fundamental_inflection_event ({', '.join(EVENT_COLS)}) VALUES %s
        ON CONFLICT (symbol, quarter_end, event_type, method_version) DO UPDATE SET
            context = EXCLUDED.context,
            fiscal_year = CASE WHEN fundamental_inflection_event.event_hash <> EXCLUDED.event_hash THEN EXCLUDED.fiscal_year ELSE fundamental_inflection_event.fiscal_year END,
            fiscal_quarter = CASE WHEN fundamental_inflection_event.event_hash <> EXCLUDED.event_hash THEN EXCLUDED.fiscal_quarter ELSE fundamental_inflection_event.fiscal_quarter END,
            strength = CASE WHEN fundamental_inflection_event.event_hash <> EXCLUDED.event_hash THEN EXCLUDED.strength ELSE fundamental_inflection_event.strength END,
            severity = CASE WHEN fundamental_inflection_event.event_hash <> EXCLUDED.event_hash THEN EXCLUDED.severity ELSE fundamental_inflection_event.severity END,
            score = CASE WHEN fundamental_inflection_event.event_hash <> EXCLUDED.event_hash THEN EXCLUDED.score ELSE fundamental_inflection_event.score END,
            metrics = CASE WHEN fundamental_inflection_event.event_hash <> EXCLUDED.event_hash THEN EXCLUDED.metrics ELSE fundamental_inflection_event.metrics END,
            explanation = CASE WHEN fundamental_inflection_event.event_hash <> EXCLUDED.event_hash THEN EXCLUDED.explanation ELSE fundamental_inflection_event.explanation END,
            components = CASE WHEN fundamental_inflection_event.event_hash <> EXCLUDED.event_hash THEN EXCLUDED.components ELSE fundamental_inflection_event.components END,
            available_at = CASE WHEN fundamental_inflection_event.event_hash <> EXCLUDED.event_hash THEN EXCLUDED.available_at ELSE fundamental_inflection_event.available_at END,
            availability_basis = CASE WHEN fundamental_inflection_event.event_hash <> EXCLUDED.event_hash THEN EXCLUDED.availability_basis ELSE fundamental_inflection_event.availability_basis END,
            source_updated_at = CASE WHEN fundamental_inflection_event.event_hash <> EXCLUDED.event_hash THEN EXCLUDED.source_updated_at ELSE fundamental_inflection_event.source_updated_at END,
            data_quality = CASE WHEN fundamental_inflection_event.event_hash <> EXCLUDED.event_hash THEN EXCLUDED.data_quality ELSE fundamental_inflection_event.data_quality END,
            quality_notes = CASE WHEN fundamental_inflection_event.event_hash <> EXCLUDED.event_hash THEN EXCLUDED.quality_notes ELSE fundamental_inflection_event.quality_notes END,
            revision_count = fundamental_inflection_event.revision_count + CASE WHEN fundamental_inflection_event.event_hash <> EXCLUDED.event_hash THEN 1 ELSE 0 END,
            updated_at = CASE WHEN fundamental_inflection_event.event_hash <> EXCLUDED.event_hash THEN NOW() ELSE fundamental_inflection_event.updated_at END,
            event_hash = EXCLUDED.event_hash"""
    with db.get_sync_connection() as conn:
        cur = conn.cursor()
        execute_values(cur, sql, [_row(r, EVENT_COLS) for r in records], page_size=300)
        conn.commit()
    return counts


def start_run(asof: date, due: int) -> int:
    with db.get_sync_connection() as conn:           # execute_dict_query does not commit: an INSERT ... RETURNING needs its own commit
        cur = conn.cursor()
        cur.execute("INSERT INTO inflection_run_log (method_version, asof, symbols_due) VALUES (%s, %s, %s) RETURNING id", (C.METHOD_VERSION, asof, due))
        run_id = cur.fetchone()[0]
        conn.commit()
        return run_id


def finish_run(run_id: int, *, processed: int, features: int, new: int, revised: int, errors: int, status: str, note: str = "") -> None:
    db.execute_insert("UPDATE inflection_run_log SET finished_at = NOW(), symbols_processed = %s, features_written = %s, events_new = %s, events_revised = %s, errors = %s, status = %s, note = %s WHERE id = %s",
                      (processed, features, new, revised, errors, status, note[:500], run_id))


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ loaders
def due_symbols(asof: date, active_event_days: int = 120) -> List[str]:
    """Symbols to (re)process: a quarter newer than their last computation, no features yet, or an event recent enough that its market context / repricing window still moves."""
    rows = db.execute_dict_query("""
        WITH last_calc AS (SELECT symbol, MAX(computed_at) AS at FROM fundamental_feature WHERE method_version = %s GROUP BY symbol),
             src AS (SELECT symbol, MAX(updated_at) AS at FROM quarterly_fundamentals GROUP BY symbol)
        SELECT s.symbol FROM src s LEFT JOIN last_calc l ON l.symbol = s.symbol
        WHERE l.symbol IS NULL OR s.at > l.at
           OR s.symbol IN (SELECT symbol FROM fundamental_inflection_event WHERE method_version = %s AND available_at >= %s::date - %s)
        ORDER BY s.symbol""", (C.METHOD_VERSION, C.METHOD_VERSION, asof, active_event_days))
    return [r["symbol"] for r in rows]


def load_inputs(asof: date, symbols: Optional[Sequence[str]] = None, price_days: int = 520) -> Inputs:
    """The engine's inputs from the database. Prices of ALL symbols are loaded (the sector benchmark needs them); fundamentals and earnings dates only for `symbols` (default all)."""
    sym_filter = ("WHERE symbol = ANY(%s)", (list(symbols),)) if symbols else ("", ())
    qrows: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in db.execute_dict_query(f"SELECT symbol, quarter, fiscal_year, fiscal_quarter, revenue, operating_income, net_income, operating_cash_flow, free_cash_flow, updated_at FROM quarterly_fundamentals {sym_filter[0]} ORDER BY symbol, quarter", sym_filter[1]):
        qrows[r["symbol"]].append(r)
    reports: Dict[str, List[date]] = defaultdict(list)
    for r in db.execute_dict_query(f"SELECT symbol, report_date FROM earnings_calendar WHERE eps_actual IS NOT NULL {'AND symbol = ANY(%s)' if symbols else ''} ORDER BY symbol, report_date", sym_filter[1]):
        reports[r["symbol"]].append(r["report_date"])
    px: Dict[str, Tuple[List[date], List[float]]] = defaultdict(lambda: ([], []))
    for r in db.execute_dict_query("SELECT symbol, date, close FROM stock_prices WHERE date >= %s::date - %s AND date <= %s AND close IS NOT NULL ORDER BY symbol, date", (asof, price_days, asof)):
        px[r["symbol"]][0].append(r["date"])
        px[r["symbol"]][1].append(float(r["close"]))
    import numpy as np
    prices = {s: X.Prices(d, np.array(c)) for s, (d, c) in px.items() if len(d) >= 30}
    val_rows = db.execute_dict_query("SELECT symbol, date, ps_ratio, pe_ratio, market_cap, sector FROM daily_fundamentals WHERE date >= '2023-07-01' AND date <= %s ORDER BY symbol, date", (asof,))
    sector_of: Dict[str, str] = {}      # the LATEST stored sector label wins: rows are ordered, because an unordered scan made a relabelled symbol flip between runs (found 2026-10-31)
    for r in val_rows:
        if r["sector"]:
            sector_of[r["symbol"]] = r["sector"]
    return Inputs(dict(qrows), dict(reports), prices, sector_of, X.ValuationStore(val_rows), {"loaded_from": "database", "asof": asof.isoformat()})
