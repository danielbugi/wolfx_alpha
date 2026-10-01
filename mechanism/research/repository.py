"""SQL for the research layer, on a raw psycopg2 connection so rowcounts and transactions are real.

(`shared.db.execute_insert` returns True regardless of rowcount and commits internally, so it cannot express
"first valid write wins".) The only writes here are INSERT ... ON CONFLICT DO NOTHING into the immutable tables
and counter updates on `candidate_capture_run`; nothing here updates, deletes, truncates, or touches the
maintenance ticket/audit objects -- tests/test_import_separation.py pins that.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, Iterable, List, Optional, Tuple

from psycopg2.extras import Json, RealDictCursor

from research import snapshot_builder as sb

PRICE_CHUNK = 250
SECTOR_WINDOW_DAYS = 30  # the same recency window the screener's own fundamentals join uses


@dataclass(frozen=True)
class Written:
    id: int
    inserted: bool          # True: this call created the row; False: the identity already existed
    stored_hash: Optional[str] = None  # the existing row's content/capture hash when not inserted

    def drifted(self, new_hash: str) -> bool:
        return (not self.inserted) and self.stored_hash is not None and self.stored_hash.strip() != new_hash


def resolve_strategy(conn, key: str, version: str) -> Tuple[int, str]:
    cur = conn.cursor()
    cur.execute("SELECT id, strategy_version FROM strategies WHERE strategy_key = %s AND strategy_version = %s",
                (key, version))
    row = cur.fetchone()
    conn.commit()
    if not row:
        raise RuntimeError(f"strategies row ({key}, {version}) is missing")
    return row[0], row[1]


def start_run(conn, strategy_id: int, strategy_version: str, session_date: date, feature_set_version: str,
              code_ref: str, universe_size: Optional[int]) -> int:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO candidate_capture_run (strategy_id, strategy_version, session_date, feature_set_version, "
        "session_source, status, universe_size, code_ref) VALUES (%s, %s, %s, %s, 'explicit', 'running', %s, %s) "
        "RETURNING id", (strategy_id, strategy_version, session_date, feature_set_version, universe_size, code_ref))
    run_id = cur.fetchone()[0]
    conn.commit()
    return run_id


COUNTER_COLUMNS = ("candidates", "captured", "already_captured", "hash_drift", "guard_rejected",
                   "guard_not_evaluated", "stale_skipped", "snapshot_skipped", "snapshot_drift", "invalid_skipped",
                   "defaulted_flagged")


def finish_run(conn, run_id: int, status: str, counters: Dict[str, int], skipped: Dict[str, str],
               error: Optional[str]) -> None:
    sets = ", ".join(f"{c} = %({c})s" for c in COUNTER_COLUMNS)
    params: Dict[str, Any] = {c: counters.get(c) for c in COUNTER_COLUMNS}
    params.update(run_id=run_id, status=status, skipped=Json(skipped), error=error)
    cur = conn.cursor()
    cur.execute(f"UPDATE candidate_capture_run SET status = %(status)s, run_finished_at = NOW(), {sets}, "
                f"skipped_symbols = %(skipped)s, error = %(error)s WHERE id = %(run_id)s", params)
    conn.commit()


def _chunks(items: List[str], size: int) -> Iterable[List[str]]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


def fetch_price_rows(conn, symbols: List[str], session_date: date) -> Dict[str, List[Dict[str, Any]]]:
    """Every stock_prices bar with date <= session_date for the symbols. The bound is the session, never the
    newest bar, so a bar from a later session can never enter a snapshot of this one."""
    out: Dict[str, List[Dict[str, Any]]] = {}
    for chunk in _chunks(sorted(set(symbols)), PRICE_CHUNK):
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT symbol, date, open, high, low, close, volume FROM stock_prices "
                    "WHERE symbol = ANY(%s) AND date <= %s ORDER BY symbol, date", (chunk, session_date))
        for r in cur.fetchall():
            out.setdefault(r["symbol"], []).append(dict(r))
        conn.commit()
    return out


def fetch_sectors(conn, symbols: List[str], session_date: date) -> Dict[str, Tuple[Optional[str], date]]:
    """symbol -> (sector or None, the date of the daily_fundamentals row it came from): the latest row on or
    before the session within SECTOR_WINDOW_DAYS -- the same row the screener's own join reads, with its date kept
    so the snapshot says how old the sector is."""
    out: Dict[str, Tuple[Optional[str], date]] = {}
    for chunk in _chunks(sorted(set(symbols)), 1000):
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT DISTINCT ON (symbol) symbol, date, sector FROM daily_fundamentals "
                    "WHERE symbol = ANY(%s) AND date <= %s AND date >= %s::date - %s * INTERVAL '1 day' "
                    "ORDER BY symbol, date DESC", (chunk, session_date, session_date, SECTOR_WINDOW_DAYS))
        for r in cur.fetchall():
            out[r["symbol"]] = (r["sector"] or None, r["date"])
        conn.commit()
    return out


def insert_snapshot(cur, s: "sb.Snapshot") -> Written:
    cur.execute(
        "INSERT INTO feature_snapshot (symbol, session_date, feature_set_version, bar_date, open, high, low, close, "
        "volume, prev_close, bars_available, snapshot_status, features, missing_features, sector, sector_source, "
        "sector_asof, manifest_hash, content_hash, code_ref) "
        "VALUES (%(symbol)s, %(session_date)s, %(fsv)s, %(bar_date)s, %(open)s, %(high)s, %(low)s, %(close)s, "
        "%(volume)s, %(prev_close)s, %(bars)s, %(status)s, %(features)s, %(missing)s, %(sector)s, %(sector_source)s, "
        "%(sector_asof)s, %(manifest_hash)s, %(content_hash)s, %(code_ref)s) "
        "ON CONFLICT (symbol, session_date, feature_set_version) DO NOTHING RETURNING id",
        dict(symbol=s.symbol, session_date=s.session_date, fsv=s.feature_set_version, bar_date=s.bar_date,
             open=s.open, high=s.high, low=s.low, close=s.close, volume=s.volume, prev_close=s.prev_close,
             bars=s.bars_available, status=s.status, features=Json(s.features, dumps=sb.canonical),
             missing=list(s.missing_features), sector=s.sector, sector_source=s.sector_source,
             sector_asof=s.sector_asof, manifest_hash=s.manifest_hash, content_hash=s.content_hash,
             code_ref=s.code_ref))
    row = cur.fetchone()
    if row:
        return Written(row[0], True)
    cur.execute("SELECT id, content_hash FROM feature_snapshot WHERE symbol = %s AND session_date = %s "
                "AND feature_set_version = %s", (s.symbol, s.session_date, s.feature_set_version))
    existing = cur.fetchone()
    return Written(existing[0], False, existing[1])


OBSERVATION_COLUMNS = (
    "strategy_id", "strategy_version", "symbol", "session_date", "direction", "bar_date", "session_source",
    "signal_type", "triggered", "entry_close", "channel_high_prev", "channel_low_prev", "breakout_dist_atr",
    "distance_to_channel_pct", "passed_guard", "guard_reasons", "alignment_score", "quality_grade",
    "combined_score", "session_rank", "ml_status", "ml_score", "ml_confidence", "ml_model_version",
    "tracked_intent", "screener_defaults", "strategy_context", "snapshot_id", "capture_run_id", "capture_hash",
    "code_ref")


def insert_observation(cur, row: Dict[str, Any]) -> Written:
    """`row` carries exactly OBSERVATION_COLUMNS. First valid write wins; an existing identity is left untouched
    and its capture_hash is returned so the caller can count a drifted re-run."""
    params = dict(row)
    params["strategy_context"] = Json(row["strategy_context"], dumps=sb.canonical) \
        if row["strategy_context"] is not None else None
    cur.execute(
        f"INSERT INTO candidate_observation ({', '.join(OBSERVATION_COLUMNS)}) "
        f"VALUES ({', '.join('%(' + c + ')s' for c in OBSERVATION_COLUMNS)}) "
        "ON CONFLICT (strategy_id, symbol, session_date, direction) DO NOTHING RETURNING id", params)
    got = cur.fetchone()
    if got:
        return Written(got[0], True)
    cur.execute("SELECT id, capture_hash FROM candidate_observation WHERE strategy_id = %s AND symbol = %s "
                "AND session_date = %s AND direction = %s",
                (row["strategy_id"], row["symbol"], row["session_date"], row["direction"]))
    existing = cur.fetchone()
    return Written(existing[0], False, existing[1])
