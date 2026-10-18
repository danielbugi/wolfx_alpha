# File: backend/services/sector_service.py
"""
Sector pages: per-sector performance, industries and member stocks, computed only from data the pipeline already
stores (stock_prices, daily_fundamentals, technical_indicators, market_index_prices). Nothing is fetched or written.

Definitions (shown on the pages too):
  * Members = stocks whose latest daily_fundamentals row (within 30 days of the newest price session) names the sector:
    TODAY'S membership. Every past window and the equal-weighted index use these current members for the whole
    period, so they carry survivorship and reclassification bias and are NOT a point-in-time record of the sector as it
    was. Point-in-time sector observations exist only in the market-intelligence snapshots (observed, per session);
    the pages show those separately. Stocks without a sector are left out.
  * All dates are anchored to the newest stored price session (`as_of`), never to the wall clock.
  * A stock's change over a window = its latest close vs its last close on/before the window's start date (calendar
    offset). None when the stock has no close on the latest market date (stale) or none within 10 days of the start.
  * A sector's / industry's change = the MEDIAN of its members' changes (robust to one crazy mover), with the share of
    members that rose. Equal-weighted index = compounded mean daily return of the members; a daily move beyond ±50% is
    treated as a data error (an unadjusted split, typically) and left out of that day's mean.
"""
import time
from datetime import date, timedelta
from statistics import median
from typing import Any, Dict, List, Optional

_CACHE_TTL_SECONDS = 600
_cache: Dict[str, Any] = {"data": None, "ts": 0.0}
_index_cache: Dict[Any, Dict[str, Any]] = {}

WINDOWS = ("1d", "1w", "1m", "3m", "ytd", "1y")

MEMBERSHIP = {
    "basis": "current",
    "point_in_time": False,
    "source": "daily_fundamentals.sector (each stock's latest row within 30 days of the newest price session)",
    "note": "Membership is today's sector assignment. Past windows and the sector index use today's members for the whole "
            "period (survivorship and reclassification bias); they are not a record of the sector as it was.",
}
MAX_DAILY_MOVE = 0.5  # |daily return| above this is a data error, not a move
STALE_GAP_DAYS = 10


def window_starts(last: date) -> Dict[str, date]:
    """Start date of each calendar window ending at `last` (1d is handled separately: the previous session)."""
    return {
        "1w": last - timedelta(days=7),
        "1m": last - timedelta(days=30),
        "3m": last - timedelta(days=91),
        "ytd": date(last.year - 1, 12, 31),
        "1y": last - timedelta(days=365),
    }


def pct(now: Optional[float], base: Optional[float]) -> Optional[float]:
    if now is None or base is None or base <= 0:
        return None
    return round((now / base - 1) * 100, 4)


def summarize(changes: List[Optional[float]]) -> Dict[str, Any]:
    """Median change and breadth (share up) over the members that have a value; None when none do."""
    vals = [v for v in changes if v is not None]
    if not vals:
        return {"median": None, "up_share": None, "n": 0}
    return {"median": round(median(vals), 4), "up_share": round(sum(v > 0 for v in vals) / len(vals) * 100, 1), "n": len(vals)}


def slugify(sector: str) -> str:
    return sector.lower().replace("&", "and").replace(" ", "-")


class SectorService:
    def __init__(self, db_connection_func):
        self.get_db_connection = db_connection_func

    # ---- data access ------------------------------------------------------------------------------------------------

    def _closes_on_or_before(self, cursor, target: date, before: bool = False) -> Dict[str, float]:
        """Each symbol's last close on/before (or strictly before) `target`, within STALE_GAP_DAYS."""
        cursor.execute(
            f"""
            SELECT DISTINCT ON (symbol) symbol, close
            FROM stock_prices
            WHERE date {'<' if before else '<='} %s AND date > %s::date - %s AND close IS NOT NULL
            ORDER BY symbol, date DESC
            """,
            (target, target, STALE_GAP_DAYS),
        )
        return {s: float(c) for s, c in cursor.fetchall()}

    def _load(self) -> Dict[str, Any]:
        now = time.time()
        if _cache["data"] is not None and now - _cache["ts"] < _CACHE_TTL_SECONDS:
            return _cache["data"]
        conn = self.get_db_connection()
        if not conn:
            raise Exception("Database connection failed")
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT MAX(date) FROM stock_prices")
            last = cursor.fetchone()[0]
            if last is None:
                data = {"as_of": None, "stocks": []}
                _cache["data"], _cache["ts"] = data, now
                return data

            cursor.execute(
                """
                SELECT DISTINCT ON (df.symbol) df.symbol, df.sector, df.industry, df.market_cap, df.pe_ratio,
                       df.quality_grade
                FROM daily_fundamentals df
                WHERE df.date >= %s::date - 30
                ORDER BY df.symbol, df.date DESC
                """,
                (last,),
            )
            members = {r[0]: r for r in cursor.fetchall() if r[1]}

            cursor.execute(
                "SELECT symbol, close FROM stock_prices WHERE date = %s AND close IS NOT NULL", (last,)
            )
            latest = {s: float(c) for s, c in cursor.fetchall()}
            prev = self._closes_on_or_before(cursor, last, before=True)
            bases = {w: self._closes_on_or_before(cursor, d) for w, d in window_starts(last).items()}

            cursor.execute(
                """
                SELECT DISTINCT ON (symbol) symbol, rsi_14, sma_50, volume_ratio
                FROM technical_indicators
                WHERE date >= %s::date - %s
                ORDER BY symbol, date DESC
                """,
                (last, STALE_GAP_DAYS),
            )
            tech = {r[0]: r for r in cursor.fetchall()}
            cursor.close()
        finally:
            conn.close()

        f = lambda v: float(v) if v is not None else None  # noqa: E731
        stocks = []
        for symbol, (_, sector, industry, mcap, pe, grade) in members.items():
            close = latest.get(symbol)
            t = tech.get(symbol)
            sma50 = f(t[2]) if t else None
            changes = {"1d": pct(close, prev.get(symbol))}
            changes.update({w: pct(close, bases[w].get(symbol)) for w in bases})
            stocks.append({
                "symbol": symbol,
                "sector": sector,
                "industry": industry,
                "close": close,
                "change_pct": changes,
                "market_cap": int(mcap) if mcap is not None else None,
                "pe_ratio": f(pe),
                "quality_grade": grade,
                "rsi_14": f(t[1]) if t else None,
                "volume_ratio": f(t[3]) if t else None,
                "above_sma50": (close > sma50) if close is not None and sma50 is not None else None,
            })
        data = {"as_of": str(last), "stocks": stocks}
        _cache["data"], _cache["ts"] = data, now
        return data

    def _equal_weight_indices(self, days: int, sector: Optional[str] = None) -> Dict[str, Any]:
        """Equal-weighted index per sector (compounded mean daily return of current members, from 100) and the S&P 500
        closes, over the last `days` calendar days. `sector` limits the work to one sector (the detail page)."""
        now = time.time()
        key = (days, sector)
        hit = _index_cache.get(key)
        if hit is not None and now - hit["ts"] < _CACHE_TTL_SECONDS:
            return hit["data"]
        conn = self.get_db_connection()
        if not conn:
            raise Exception("Database connection failed")
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                WITH last AS (SELECT MAX(date) AS d FROM stock_prices),
                members AS (
                    SELECT DISTINCT ON (symbol) symbol, sector
                    FROM daily_fundamentals, last
                    WHERE date >= last.d - 30 AND sector IS NOT NULL
                    ORDER BY symbol, date DESC
                ),
                picked AS (SELECT symbol, sector FROM members WHERE %s::text IS NULL OR sector = %s::text),
                rets AS (
                    SELECT sp.symbol, sp.date,
                           sp.close / NULLIF(LAG(sp.close) OVER (PARTITION BY sp.symbol ORDER BY sp.date), 0) - 1 AS r
                    FROM stock_prices sp JOIN picked USING (symbol), last
                    WHERE sp.date >= last.d - %s
                )
                SELECT m.sector, rets.date, AVG(rets.r), COUNT(*)
                FROM rets JOIN picked m USING (symbol)
                WHERE rets.r IS NOT NULL AND ABS(rets.r) <= %s
                GROUP BY m.sector, rets.date
                ORDER BY m.sector, rets.date
                """,
                (sector, sector, days, MAX_DAILY_MOVE),
            )
            rows = cursor.fetchall()
            # Anchored to the newest stored price session, like the sector series above (not to the wall clock).
            cursor.execute(
                "SELECT date, close FROM market_index_prices WHERE symbol = '^GSPC' "
                "AND date >= (SELECT MAX(date) FROM stock_prices) - %s AND close IS NOT NULL ORDER BY date",
                (days,),
            )
            spx = [(d, float(c)) for d, c in cursor.fetchall()]
            cursor.close()
        finally:
            conn.close()

        series: Dict[str, List[Dict[str, Any]]] = {}
        for sector, d, r, _n in rows:
            pts = series.setdefault(sector, [])
            level = (pts[-1]["level"] if pts else 100.0) * (1 + float(r))
            pts.append({"date": str(d), "level": level})
        data = {"sectors": series, "spx": [{"date": str(d), "close": c} for d, c in spx]}
        _index_cache[key] = {"data": data, "ts": now}
        return data

    # ---- API shapes -------------------------------------------------------------------------------------------------

    def get_sectors(self) -> Dict[str, Any]:
        data = self._load()
        idx = self._equal_weight_indices(100)["sectors"]
        by_sector: Dict[str, List[Dict[str, Any]]] = {}
        for s in data["stocks"]:
            by_sector.setdefault(s["sector"], []).append(s)
        out = []
        for sector, members in by_sector.items():
            above = [m["above_sma50"] for m in members if m["above_sma50"] is not None]
            out.append({
                "sector": sector,
                "slug": slugify(sector),
                "stock_count": len(members),
                "priced_count": sum(1 for m in members if m["close"] is not None),
                "industry_count": len({m["industry"] for m in members if m["industry"]}),
                "market_cap": sum(m["market_cap"] or 0 for m in members) or None,
                "change": {w: summarize([m["change_pct"][w] for m in members]) for w in WINDOWS},
                "above_sma50_pct": round(sum(above) / len(above) * 100, 1) if above else None,
                "sparkline": [p["level"] for p in idx.get(sector, [])[-63:]],
            })
        out.sort(key=lambda s: -(s["market_cap"] or 0))
        return {"as_of": data["as_of"], "membership": MEMBERSHIP, "sectors": out}

    def get_sector(self, slug: str) -> Optional[Dict[str, Any]]:
        data = self._load()
        members = [s for s in data["stocks"] if slugify(s["sector"]) == slug]
        if not members:
            return None
        sector = members[0]["sector"]
        industries: Dict[str, List[Dict[str, Any]]] = {}
        for m in members:
            industries.setdefault(m["industry"] or "Other", []).append(m)
        industry_rows = []
        for name, ms in industries.items():
            above = [m["above_sma50"] for m in ms if m["above_sma50"] is not None]
            industry_rows.append({
                "industry": name,
                "stock_count": len(ms),
                "market_cap": sum(m["market_cap"] or 0 for m in ms) or None,
                "change": {w: summarize([m["change_pct"][w] for m in ms]) for w in WINDOWS},
                "above_sma50_pct": round(sum(above) / len(above) * 100, 1) if above else None,
            })
        industry_rows.sort(key=lambda r: -(r["market_cap"] or 0))
        above = [m["above_sma50"] for m in members if m["above_sma50"] is not None]
        idx = self._equal_weight_indices(400, sector)
        return {
            "as_of": data["as_of"],
            "sector": sector,
            "slug": slug,
            "stock_count": len(members),
            "priced_count": sum(1 for m in members if m["close"] is not None),
            "membership": MEMBERSHIP,
            "market_cap": sum(m["market_cap"] or 0 for m in members) or None,
            "change": {w: summarize([m["change_pct"][w] for m in members]) for w in WINDOWS},
            "above_sma50_pct": round(sum(above) / len(above) * 100, 1) if above else None,
            "industries": industry_rows,
            "stocks": sorted(members, key=lambda m: -(m["market_cap"] or 0)),
            "index": idx["sectors"].get(sector, []),
            "spx": idx["spx"],
        }
