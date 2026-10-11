# File: backend/services/news_service.py
"""
Stock headlines for the dashboard's stock page — the backend side of mechanism/alerts/news_service.py.

The fetching, caching and honesty rules all live in the mechanism module (the Telegram assistant already uses it):
headlines are fetched from the provider at most once per symbol per few hours, stored in Postgres (news_items /
news_fetched) and shared by every reader, so the dashboard and the bot reuse the same cache and the same provider
budget. Headline + source + time + link only; nothing is invented ("empty" / "unavailable" are real states).

This module only binds that service to the backend's connection pool, with the same SQL the bot's PgStore runs. The
mechanism package is imported by path, appended so the backend's own `services`/`routers` keep winning — the same
pattern as strategy_intelligence_service.py.
"""
import logging
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from psycopg2.extras import RealDictCursor

_MECHANISM = str(Path(__file__).resolve().parents[2] / "mechanism")
if _MECHANISM not in sys.path:
    sys.path.append(_MECHANISM)

from alerts import news_service as shared_news  # noqa: E402

log = logging.getLogger(__name__)

MAX_LIMIT = 20


class BackendNewsStore:
    """news_state / news_recent / save_news over the backend pool (tables: mechanism/add_tracker_tables.sql)."""

    def __init__(self, get_connection: Callable):
        self._get_connection = get_connection

    def _run(self, sql: str, params: Any, fetch: bool) -> List[Dict]:
        conn = self._get_connection()
        if not conn:
            raise RuntimeError("Database connection failed")
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql, params)
                rows = [dict(r) for r in cur.fetchall()] if fetch else []
            if not fetch:
                conn.commit()
            return rows
        finally:
            conn.close()  # pooled connection: returns it to the pool

    def news_state(self, symbol: str) -> Optional[Dict]:
        rows = self._run("SELECT fetched_at, ok FROM news_fetched WHERE symbol = %s", (symbol,), True)
        return rows[0] if rows else None

    def news_recent(self, symbol: str, limit: int) -> List[Dict]:
        return self._run(
            "SELECT published_at, headline, source, url, image_url FROM news_items WHERE symbol = %s ORDER BY published_at DESC LIMIT %s",
            (symbol, limit), True)

    def save_news(self, symbol: str, items: List[Dict], ok: bool, now) -> None:
        conn = self._get_connection()
        if not conn:
            raise RuntimeError("Database connection failed")
        try:
            with conn.cursor() as cur:
                for it in items:
                    # same upsert as the bot's PgStore: fills image_url (migration 33) for rows stored without one
                    cur.execute(
                        "INSERT INTO news_items (symbol, published_at, headline, source, url, url_hash, image_url, fetched_at) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (symbol, url_hash) "
                        "DO UPDATE SET image_url = COALESCE(news_items.image_url, EXCLUDED.image_url)",
                        (symbol, it["published_at"], it["headline"], it.get("source"), it["url"], it["url_hash"],
                         it.get("image_url"), now))
                cur.execute(
                    "INSERT INTO news_fetched (symbol, fetched_at, ok) VALUES (%s, %s, %s) "
                    "ON CONFLICT (symbol) DO UPDATE SET fetched_at = EXCLUDED.fetched_at, ok = EXCLUDED.ok",
                    (symbol, now, ok))
            conn.commit()
        finally:
            conn.close()


class StockNewsService:
    def __init__(self, store, fetch: Callable[[str], List[Dict]] = shared_news.alpaca_fetch):
        self._news = shared_news.NewsService(store, fetch=fetch)

    def get(self, symbol: str, limit: int) -> Dict[str, Any]:
        """{symbol, status: ok|empty|unavailable, stale, items}. Never raises: a database or provider problem is
        reported as 'unavailable' so the page can say so instead of failing."""
        limit = max(1, min(limit, MAX_LIMIT))
        try:
            result = self._news.get(symbol, limit)
        except Exception as e:  # noqa: BLE001 - e.g. news tables missing or not writable in this environment
            log.warning("stock news unavailable: %s", type(e).__name__)
            return {"symbol": symbol, "status": "unavailable", "stale": False, "items": []}
        return {
            "symbol": symbol,
            "status": result.status,
            "stale": result.stale,
            "items": [
                {
                    "published_at": it["published_at"].isoformat() if hasattr(it["published_at"], "isoformat") else str(it["published_at"]),
                    "headline": it["headline"],
                    "source": it.get("source"),
                    "url": it["url"],
                    "image_url": it.get("image_url"),
                }
                for it in result.items
            ],
        }
