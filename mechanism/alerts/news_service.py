# mechanism/alerts/news_service.py
"""
Headlines for the stock card (BOT_DESIGN_REPORT.md section 6). Provider: Alpaca's news endpoint (free plan, verified 2026-09-21; the
keys already in .env). Design rules:
  * ECONOMIC: a symbol's headlines are fetched at most once per NEWS_TTL_HOURS, stored in Postgres and shared by every user, so
    provider calls grow with the number of distinct symbols, not with users. A failed fetch is remembered (RETRY_MINUTES) so a provider
    outage cannot turn every tap into another failing call.
  * HONEST: no headlines -> "no recent news"; provider down -> "unavailable" (older stored headlines are shown, flagged). Never invented.
  * LEGAL-SAFE: headline + link + source + time only. No article text is stored or shown.
  * The provider call is injectable (`fetch`), so tests never touch the network.
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional
from urllib.parse import urlparse

log = logging.getLogger("first_light_news")

NEWS_URL = "https://data.alpaca.markets/v1beta1/news"
NEWS_TTL_HOURS = 6
RETRY_MINUTES = 10
FETCH_LIMIT = 15                 # stored per fetch
SHOW_LIMIT = 5                   # shown on the card
HEADLINE_MAX = 300


@dataclass
class NewsResult:
    items: List[Dict]
    status: str                  # 'ok' | 'empty' | 'unavailable'
    stale: bool = False          # items come from an earlier fetch because the provider is failing now


def url_hash(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def parse_alpaca(payload: Dict, symbol: str) -> List[Dict]:
    """Alpaca JSON -> [{published_at, headline, source, url, url_hash}]; skips anything malformed, non-http links and other symbols."""
    out, seen = [], set()
    for n in (payload or {}).get("news", []) or []:
        try:
            url = str(n["url"]).strip()
            if urlparse(url).scheme not in ("http", "https") or url in seen:
                continue
            if symbol not in (n.get("symbols") or []):
                continue
            headline = " ".join(str(n["headline"]).split())[:HEADLINE_MAX]
            when = datetime.fromisoformat(str(n["created_at"]).replace("Z", "+00:00"))
            if not headline or when.tzinfo is None:
                continue
        except (KeyError, ValueError, TypeError):
            continue
        seen.add(url)
        out.append({"published_at": when, "headline": headline, "source": (n.get("source") or "")[:60] or None,
                    "url": url[:1000], "url_hash": url_hash(url)})
    return out


def alpaca_fetch(symbol: str) -> List[Dict]:
    """One provider call. Raises on any failure (the service turns that into 'unavailable')."""
    import requests
    key, secret = os.getenv("ALPACA_API_KEY", ""), os.getenv("ALPACA_API_SECRET", "")
    if not key or not secret:
        raise RuntimeError("Alpaca keys are not configured")
    r = requests.get(NEWS_URL, params={"symbols": symbol, "limit": FETCH_LIMIT, "sort": "desc"},
                     headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}, timeout=10)
    r.raise_for_status()
    return parse_alpaca(r.json(), symbol)


class NewsService:
    def __init__(self, store, fetch: Callable[[str], List[Dict]] = alpaca_fetch,
                 now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.store, self._fetch, self._now = store, fetch, now
        self._locks: Dict[str, threading.Lock] = defaultdict(threading.Lock)      # one fetch per symbol at a time
        self._guard = threading.Lock()

    def _fresh(self, state: Optional[Dict]) -> bool:
        if not state:
            return False
        age = self._now() - state["fetched_at"]
        return age < (timedelta(hours=NEWS_TTL_HOURS) if state["ok"] else timedelta(minutes=RETRY_MINUTES))

    def get(self, symbol: str, limit: int = SHOW_LIMIT) -> NewsResult:
        with self._guard:
            lock = self._locks[symbol]
        with lock:                                        # a second user asking for the same symbol waits, then reads the cache
            state = self.store.news_state(symbol)
            if not self._fresh(state):
                try:
                    items = self._fetch(symbol)
                    self.store.save_news(symbol, items, True, self._now())
                except Exception as e:                    # noqa: BLE001 - any provider failure degrades to 'unavailable'
                    log.warning("news fetch failed for a symbol: %s", type(e).__name__)      # the symbol itself is not logged
                    self.store.save_news(symbol, [], False, self._now())
            state = self.store.news_state(symbol)
            items = self.store.news_recent(symbol, limit)
        if state and not state["ok"]:
            return NewsResult(items, "unavailable", stale=bool(items))
        return NewsResult(items, "ok" if items else "empty")
