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
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from psycopg2.extras import RealDictCursor

_MECHANISM = str(Path(__file__).resolve().parents[2] / "mechanism")
if _MECHANISM not in sys.path:
    sys.path.append(_MECHANISM)

from alerts import news_service as shared_news  # noqa: E402

log = logging.getLogger(__name__)

MAX_LIMIT = 20

# news_items.image_url arrives with migration 33, which is applied to production by hand -- possibly after this code is
# deployed. Until the column exists, read and write without it (headlines still work, just no thumbnails). Absence is
# re-checked every few minutes, so the column is picked up after the migration without a restart.
_IMAGE_COLUMN_RECHECK_S = 300
_image_column = {"present": False, "checked_at": 0.0}
_image_column_lock = threading.Lock()


def _reason_for(exc: BaseException) -> str:
    """A short, safe code for why news is unavailable (shown to signed-in users; no secrets, no SQL)."""
    msg = str(exc).lower()
    if "keys are not configured" in msg:
        return "provider_keys_missing"
    if type(exc).__module__.startswith("psycopg2") or "database" in msg:
        return f"storage_error:{type(exc).__name__}"
    if type(exc).__module__.startswith("requests"):
        return f"provider_error:{type(exc).__name__}"
    return f"error:{type(exc).__name__}"


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

    def has_image_column(self) -> bool:
        with _image_column_lock:
            if _image_column["present"] or time.monotonic() - _image_column["checked_at"] < _IMAGE_COLUMN_RECHECK_S and _image_column["checked_at"]:
                return _image_column["present"]
            rows = self._run(
                "SELECT 1 FROM information_schema.columns WHERE table_name = 'news_items' AND column_name = 'image_url'",
                None, True)
            _image_column["present"] = bool(rows)
            _image_column["checked_at"] = time.monotonic()
            return _image_column["present"]

    def news_state(self, symbol: str) -> Optional[Dict]:
        rows = self._run("SELECT fetched_at, ok FROM news_fetched WHERE symbol = %s", (symbol,), True)
        return rows[0] if rows else None

    def news_recent(self, symbol: str, limit: int) -> List[Dict]:
        image = "image_url" if self.has_image_column() else "NULL AS image_url"
        return self._run(
            f"SELECT published_at, headline, source, url, {image} FROM news_items WHERE symbol = %s ORDER BY published_at DESC LIMIT %s",
            (symbol, limit), True)

    def save_news(self, symbol: str, items: List[Dict], ok: bool, now) -> None:
        with_image = self.has_image_column()
        conn = self._get_connection()
        if not conn:
            raise RuntimeError("Database connection failed")
        try:
            with conn.cursor() as cur:
                for it in items:
                    if not with_image:  # migration 33 not applied yet: store the headline without its thumbnail
                        cur.execute(
                            "INSERT INTO news_items (symbol, published_at, headline, source, url, url_hash, fetched_at) "
                            "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (symbol, url_hash) DO NOTHING",
                            (symbol, it["published_at"], it["headline"], it.get("source"), it["url"], it["url_hash"], now))
                        continue
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
        self._provider_error: Optional[BaseException] = None

        def recording_fetch(symbol: str) -> List[Dict]:
            try:
                return fetch(symbol)
            except Exception as e:  # noqa: BLE001 - remembered for the response's reason, then re-raised for the service
                self._provider_error = e
                raise

        self._news = shared_news.NewsService(store, fetch=recording_fetch)

    def get(self, symbol: str, limit: int) -> Dict[str, Any]:
        """{symbol, status: ok|empty|unavailable, stale, items}. Never raises: a database or provider problem is
        reported as 'unavailable' so the page can say so instead of failing."""
        limit = max(1, min(limit, MAX_LIMIT))
        try:
            result = self._news.get(symbol, limit)
        except Exception as e:  # noqa: BLE001 - e.g. news tables missing or not writable in this environment
            reason = _reason_for(e)
            log.warning("stock news unavailable: %s", reason)
            return {"symbol": symbol, "status": "unavailable", "reason": reason, "stale": False, "items": []}
        reason = None
        if result.status == "unavailable":
            # this request's provider call failed, or an earlier one did and is still inside its retry window
            reason = _reason_for(self._provider_error) if self._provider_error else "provider_error:recent_failure"
        return {
            "symbol": symbol,
            "status": result.status,
            "reason": reason,
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
