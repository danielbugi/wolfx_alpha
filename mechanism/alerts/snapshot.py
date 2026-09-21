# mechanism/alerts/snapshot.py
"""Persist one digest snapshot per US session (tables in mechanism/add_digest_tables.sql). The interactive bot only ever
reads these rows, so its cost does not grow with the audience."""
from __future__ import annotations

from typing import Dict, List, Optional

from psycopg2.extras import Json, execute_values


def _stock_row(session, r: Dict) -> tuple:
    return (session, r["symbol"], r["cat"], r["prev_cat"], r["close"], r["ret1_pct"], r["rvol"], r["range_atr"],
            r["below_high_pct"], r["dv20"], Json(r["list_ranks"]) if r.get("list_ranks") else None, r.get("atr"))


def save_snapshot(db, session, rows: List[Dict], digest: Dict, universe_n: int, breadth: Dict[str, int],
                  index_lines: Optional[List[str]], top_n: int) -> int:
    """Replace the snapshot for `session` atomically (a re-run overwrites it). Returns the number of stock rows."""
    with db.get_sync_connection() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM digest_runs WHERE session_date = %s", (session,))          # cascades to digest_stocks
        cur.execute(
            "INSERT INTO digest_runs (session_date, universe_n, up_n, down_n, counts, index_lines, min_dv, top_n) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (session, universe_n, breadth["up"], breadth["down"], Json(digest["counts"]), Json(index_lines or []),
             digest["min_dv"], top_n))
        execute_values(
            cur,
            "INSERT INTO digest_stocks (session_date, symbol, category, prev_category, close, ret1_pct, rvol, range_atr, "
            "below_high_pct, dv20, list_ranks, atr) VALUES %s",
            [_stock_row(session, r) for r in rows])
        conn.commit()
    return len(rows)
