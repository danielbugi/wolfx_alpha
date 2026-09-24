# File: backend/services/momentum_board_service.py
"""
Momentum Board — the dashboard's "find big winners" panel.

Reuses the exact methodology already validated and running for the Telegram channel
(mechanism/alerts/digest_builder.py, star.py — see CLAUDE.md's 2026-09-20 tail-economics
studies: the channel's top-gainers list carries a real positive 60-day abnormal drift, and
a wide-trailing-stop exit on that list reaches +100% far more often than a random liquid
stock). That machinery already writes one precomputed snapshot per session to
digest_runs/digest_stocks (mechanism/add_digest_tables.sql) when send_daily_digest.py runs —
this service reads that snapshot rather than recomputing the whole-universe analysis inline
on every dashboard request.

Deliberately NOT what the old "Alpha Finder" widget did: no ML contribution (no model
currently passes the promotion gate — see CLAUDE.md's ML audit) and no alignment-only score
dressed up as "AI-Enhanced". A stock earns a place here by being in the top ranks of 2+ of
the day's three FACT lists (gainers / ATR expansion / volume surge) -- the star.py rule,
shared verbatim with the bot and channel so the definition of "confirmed" cannot drift
between surfaces.
"""

import sys
import time
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

from psycopg2.extras import RealDictCursor
import logging

logger = logging.getLogger(__name__)

_MECHANISM = str(Path(__file__).resolve().parents[2] / "mechanism")
if _MECHANISM not in sys.path:
    sys.path.append(_MECHANISM)

from alerts.star import is_starred, starred_lists  # noqa: E402

# digest_stocks is written once per session (when send_daily_digest.py --send runs), not on
# every dashboard load -- a short cache still avoids a repeat query burst from the 5-minute
# auto-refresh, but there is no data-freshness reason to keep it below a few minutes.
_CACHE_TTL_SECONDS = 300
_board_cache: Dict[str, Any] = {"data": None, "ts": 0.0}

LONG_CATEGORIES = ("breakout", "near_breakout")
QUALITY_RANK = {"A": 4, "B": 3, "C": 2, "D": 1, "F": 0}


def _today() -> date:
    return date.today()


class MomentumBoardService:
    """DB-backed reader for the digest snapshot (digest_runs / digest_stocks)."""

    def __init__(self, db_connection_func):
        self.get_db_connection = db_connection_func

    def _load_latest_session(self) -> Dict[str, Any]:
        now = time.time()
        if _board_cache["data"] is not None and (now - _board_cache["ts"]) < _CACHE_TTL_SECONDS:
            return _board_cache["data"]

        conn = self.get_db_connection()
        if not conn:
            raise Exception("Database connection failed")

        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            WITH latest_run AS (
                SELECT session_date, created_at, universe_n, counts
                FROM digest_runs
                ORDER BY session_date DESC
                LIMIT 1
            ),
            fundamentals AS (
                SELECT DISTINCT ON (symbol)
                    symbol, sector, quality_grade, overall_quality_score, market_cap
                FROM daily_fundamentals
                ORDER BY symbol, date DESC
            )
            SELECT
                lr.session_date, lr.created_at, lr.universe_n, lr.counts,
                ds.symbol, ds.category, ds.close, ds.ret1_pct, ds.rvol, ds.range_atr,
                ds.below_high_pct, ds.dv20, ds.list_ranks, ds.atr,
                f.sector, f.quality_grade, f.overall_quality_score, f.market_cap,
                nxt.report_date AS next_earnings_date
            FROM latest_run lr
            JOIN digest_stocks ds ON ds.session_date = lr.session_date
            LEFT JOIN fundamentals f ON f.symbol = ds.symbol
            LEFT JOIN LATERAL (
                SELECT report_date FROM earnings_calendar
                WHERE symbol = ds.symbol AND report_date >= CURRENT_DATE
                ORDER BY report_date
                LIMIT 1
            ) nxt ON true
            WHERE ds.category = ANY(%s) AND ds.list_ranks IS NOT NULL
        """, (list(LONG_CATEGORIES),))
        rows = cursor.fetchall()
        cursor.close()
        conn.close()

        if not rows:
            data = {"session_date": None, "generated_at": None, "universe_n": 0, "counts": {}, "rows": []}
        else:
            head = rows[0]
            data = {
                "session_date": head["session_date"].isoformat() if head["session_date"] else None,
                "generated_at": head["created_at"].isoformat() if head["created_at"] else None,
                "universe_n": head["universe_n"],
                "counts": head["counts"] or {},
                "rows": rows,
            }

        _board_cache["data"] = data
        _board_cache["ts"] = now
        return data

    def get_board(
        self,
        category: Optional[str] = None,
        sector: Optional[str] = None,
        min_quality_grade: Optional[str] = None,
        limit: int = 100,
    ) -> Dict[str, Any]:
        snapshot = self._load_latest_session()
        min_rank = QUALITY_RANK.get((min_quality_grade or "").upper(), -1) if min_quality_grade else -1

        # Unfiltered count of the day's confirmed (2+ list) names -- independent of this call's
        # category/sector/grade filters, so the dashboard's summary strip has one stable number
        # for "how many are confirmed today" regardless of what the board itself is showing.
        starred_total = sum(1 for r in snapshot["rows"] if is_starred(r["list_ranks"] or {}))

        results: List[Dict[str, Any]] = []
        for r in snapshot["rows"]:
            if category and category != "all" and r["category"] != category:
                continue
            if sector and r["sector"] != sector:
                continue
            grade = r["quality_grade"]
            grade_rank = QUALITY_RANK.get(grade, -1) if grade else -1
            if min_rank >= 0 and grade_rank < min_rank:
                continue

            list_ranks = r["list_ranks"] or {}
            starred = is_starred(list_ranks)
            next_earnings = r["next_earnings_date"]
            days_to_earnings = (next_earnings - _today()).days if next_earnings else None
            results.append({
                "symbol": r["symbol"],
                "category": r["category"],
                "close": float(r["close"]) if r["close"] is not None else None,
                "ret1_pct": float(r["ret1_pct"]) if r["ret1_pct"] is not None else None,
                "rvol": float(r["rvol"]) if r["rvol"] is not None else None,
                "range_atr": float(r["range_atr"]) if r["range_atr"] is not None else None,
                "below_high_pct": float(r["below_high_pct"]) if r["below_high_pct"] is not None else None,
                "atr": float(r["atr"]) if r["atr"] is not None else None,
                "dv20": float(r["dv20"]) if r["dv20"] is not None else None,
                "list_ranks": list_ranks,
                "starred": starred,
                "star_lists": starred_lists(list_ranks) if starred else [],
                "sector": r["sector"],
                "quality_grade": grade,
                "overall_quality_score": float(r["overall_quality_score"]) if r["overall_quality_score"] is not None else None,
                "market_cap": int(r["market_cap"]) if r["market_cap"] is not None else None,
                "next_earnings_date": next_earnings.isoformat() if next_earnings else None,
                "days_to_earnings": days_to_earnings,
            })

        # Confirmed-by-2+-lists names first, then biggest 1-day movers among the rest --
        # the same priority the channel's ★ marker communicates, not a fabricated score.
        results.sort(key=lambda x: (not x["starred"], -(x["ret1_pct"] or float("-inf"))))
        total = len(results)
        results = results[:limit]

        return {
            "session_date": snapshot["session_date"],
            "generated_at": snapshot["generated_at"],
            "universe_n": snapshot["universe_n"],
            "counts": snapshot["counts"],
            "starred_total": starred_total,
            "total": total,
            "results": results,
        }
