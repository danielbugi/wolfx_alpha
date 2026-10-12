"""SQL for fwd_v1, on a raw psycopg2 connection.

Reads: candidate_observation, stock_prices, market_index_prices, forward_return_label.
Writes: ONE statement -- INSERT INTO forward_return_label ... ON CONFLICT DO NOTHING. Nothing here updates, deletes or
truncates (the table's triggers would refuse anyway), and nothing touches signal_ledger.
The minimum runtime privileges are therefore: SELECT on the four tables above, INSERT on forward_return_label.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from psycopg2.extras import Json, RealDictCursor

from research.labels import fwd_v1 as f

PRICE_CHUNK = 200

LABEL_COLUMNS = (
    "observation_id", "horizon_sessions", "label_version", "methodology_version", "label_status", "void_reason", "symbol",
    "direction", "t0_session", "horizon_session", "computed_as_of_session", "reference_close", "entry_close_captured",
    "basis_ratio", "horizon_close", "raw_return", "directional_return", "benchmark_symbol", "benchmark_state",
    "benchmark_return", "excess_return", "directional_excess_return", "path_state", "mfe", "mae", "bars_expected",
    "bars_observed", "data_quality", "dq_details", "price_source", "price_basis", "calendar_source", "input_hash", "code_ref")


def _chunks(items: Sequence[str], size: int) -> Iterable[List[str]]:
    for i in range(0, len(items), size):
        yield list(items[i:i + size])


def _num(x) -> Optional[float]:
    return None if x is None else float(x)


def fetch_observations(conn, *, before: date, label_version: str, horizons: Sequence[int],
                       limit: Optional[int] = None) -> List[Tuple[f.Observation, Tuple[int, ...]]]:
    """Observations whose session precedes `before`, each with the horizons that have NO label row yet (any status)."""
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        "SELECT o.id, o.symbol, o.direction, o.session_date, o.entry_close, "
        "  ARRAY(SELECT h FROM unnest(%s::int[]) AS h WHERE NOT EXISTS (SELECT 1 FROM forward_return_label l "
        "        WHERE l.observation_id = o.id AND l.horizon_sessions = h AND l.label_version = %s) ORDER BY h) AS todo "
        "FROM candidate_observation o WHERE o.session_date < %s ORDER BY o.session_date, o.id " +
        ("LIMIT %s" if limit else ""),
        (list(horizons), label_version, before) + ((limit,) if limit else ()))
    rows = cur.fetchall()
    conn.commit()
    return [(f.Observation(r["id"], r["symbol"], int(r["direction"]), r["session_date"], float(r["entry_close"])),
             tuple(r["todo"])) for r in rows if r["todo"]]


def fetch_bars(conn, symbols: Sequence[str], start: date, end: date) -> Dict[str, Dict[date, f.Bar]]:
    out: Dict[str, Dict[date, f.Bar]] = {}
    for chunk in _chunks(sorted(set(symbols)), PRICE_CHUNK):
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT symbol, date, open, high, low, close FROM stock_prices WHERE symbol = ANY(%s) "
                    "AND date BETWEEN %s AND %s ORDER BY symbol, date", (chunk, start, end))
        for r in cur.fetchall():
            out.setdefault(r["symbol"], {})[r["date"]] = f.Bar(r["date"], _num(r["close"]), _num(r["open"]),
                                                                 _num(r["high"]), _num(r["low"]))
        conn.commit()
    return out


def fetch_benchmark(conn, symbol: str, start: date, end: date) -> Dict[date, float]:
    cur = conn.cursor()
    cur.execute("SELECT date, close FROM market_index_prices WHERE symbol = %s AND date BETWEEN %s AND %s",
                (symbol, start, end))
    out = {r[0]: float(r[1]) for r in cur.fetchall() if r[1] is not None}
    conn.commit()
    return out


def insert_label(cur, label: f.Label) -> Tuple[bool, Optional[str]]:
    """(inserted, stored_input_hash_if_not_inserted). First valid write wins; an existing label is never touched."""
    params: Dict[str, Any] = {c: getattr(label, c) for c in LABEL_COLUMNS}
    params["dq_details"] = Json(label.dq_details, dumps=f.canonical)
    cur.execute(f"INSERT INTO forward_return_label ({', '.join(LABEL_COLUMNS)}) "
                f"VALUES ({', '.join('%(' + c + ')s' for c in LABEL_COLUMNS)}) "
                "ON CONFLICT (observation_id, horizon_sessions, label_version) DO NOTHING RETURNING id", params)
    if cur.fetchone():
        return True, None
    cur.execute("SELECT input_hash, label_status FROM forward_return_label WHERE observation_id = %s "
                "AND horizon_sessions = %s AND label_version = %s",
                (label.observation_id, label.horizon_sessions, label.label_version))
    row = cur.fetchone()
    return False, (row[0].strip() if row else None)


def fetch_labels(conn, label_version: str, *, status: str = "final") -> List[Dict[str, Any]]:
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT l.observation_id, l.horizon_sessions, l.input_hash, l.label_status, o.symbol, o.direction, "
                "o.session_date, o.entry_close FROM forward_return_label l JOIN candidate_observation o "
                "ON o.id = l.observation_id WHERE l.label_version = %s AND l.label_status = %s "
                "ORDER BY l.observation_id, l.horizon_sessions", (label_version, status))
    rows = [dict(r) for r in cur.fetchall()]
    conn.commit()
    return rows


def fetch_mature_labels(conn, label_version: str = f.LABEL_VERSION, *, horizon: Optional[int] = None) -> List[Dict[str, Any]]:
    """THE read path for training/dataset code: final labels only. A pending horizon has no row, and a void row carries
    no outcome, so this can never return a partial label."""
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM forward_return_label WHERE label_version = %s AND label_status = 'final' "
                + ("AND horizon_sessions = %s " if horizon else "") + "ORDER BY observation_id, horizon_sessions",
                (label_version,) + ((horizon,) if horizon else ()))
    rows = [dict(r) for r in cur.fetchall()]
    conn.commit()
    return rows
