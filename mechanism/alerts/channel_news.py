# mechanism/alerts/channel_news.py
"""
Headlines for the channel's "News on today's movers" post (CHANNEL_CONTENT_MILESTONES.md M3, report item P7).

Deliberately SEPARATE from the private assistant's news_service (a channel module may not import the assistant; test_channel_tools.py checks).
Provider: Alpaca's news endpoint with the keys already in .env. LICENCE IS NOT CHECKED: the post stays out of the rotation until
CHANNEL_NEWS_ENABLED=1 is set on purpose.

Rules: headline + source + link only (no article text, no summaries); one provider call per mover, once a day; a failing symbol is left out
and reported, never invented; headlines that read like a recommendation (ratings, price targets, "buy" calls) are dropped so the channel never
relays advice; the provider call is injectable so tests never touch the network.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional
from urllib.parse import urlparse

log = logging.getLogger("first_light_channel_news")

NEWS_URL = "https://data.alpaca.markets/v1beta1/news"
FETCH_LIMIT = 8
HEADLINE_MAX = 200
MAX_AGE_HOURS = 40

# advice-like headlines are not relayed (analyst ratings, price targets, "buy/sell" calls, "top picks", "should you ...")
ADVICE_LIKE = re.compile(
    r"\b(price targets?|target price|upgrad\w*|downgrad\w*|initiat\w+ coverage|reiterat\w*|rating|buy|buying|sell|selling|strong buy|hold rating|"
    r"outperform\w*|underperform\w*|overweight|underweight|top picks?|best stocks?|should (you|i)|is it (a )?(time|good)|worth buying|"
    r"free report|zacks|motley fool)\b", re.I)


def is_advice_like(headline: str) -> bool:
    return bool(ADVICE_LIKE.search(headline or ""))


def parse(payload: Dict, symbol: str) -> List[Dict]:
    """Alpaca JSON -> [{published_at, headline, source, url}] newest first; malformed rows, non-http links, other symbols dropped."""
    out, seen = [], set()
    for n in (payload or {}).get("news", []) or []:
        try:
            url = str(n["url"]).strip()
            if urlparse(url).scheme not in ("http", "https") or url in seen or symbol not in (n.get("symbols") or []):
                continue
            headline = " ".join(str(n["headline"]).split())[:HEADLINE_MAX]
            when = datetime.fromisoformat(str(n["created_at"]).replace("Z", "+00:00"))
            if not headline or when.tzinfo is None:
                continue
        except (KeyError, ValueError, TypeError):
            continue
        seen.add(url)
        out.append({"published_at": when, "headline": headline, "source": (n.get("source") or "")[:40] or None, "url": url[:1000]})
    return sorted(out, key=lambda x: x["published_at"], reverse=True)


def fetch_symbol(symbol: str) -> List[Dict]:
    """One provider call; raises on any failure (collect() turns that into 'left out')."""
    import requests
    key, secret = os.getenv("ALPACA_API_KEY", ""), os.getenv("ALPACA_API_SECRET", "")
    if not key or not secret:
        raise RuntimeError("Alpaca keys are not configured")
    r = requests.get(NEWS_URL, params={"symbols": symbol, "limit": FETCH_LIMIT, "sort": "desc"},
                     headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}, timeout=10)
    r.raise_for_status()
    return parse(r.json(), symbol)


def collect(symbols: List[str], fetch: Callable[[str], List[Dict]] = fetch_symbol, now: Optional[datetime] = None,
            max_age_hours: int = MAX_AGE_HOURS, per_symbol: int = 2) -> Dict:
    """{'items': {symbol: [up to per_symbol fresh, non-advice headlines]}, 'failed': n, 'dropped_advice': n}. Symbols with nothing fresh are absent."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=max_age_hours)
    items: Dict[str, List[Dict]] = {}
    failed = dropped = 0
    for sym in symbols:
        try:
            got = fetch(sym)
        except Exception as e:                                                # noqa: BLE001 - one provider failure must not stop the post
            log.warning("channel news fetch failed: %s", type(e).__name__)
            failed += 1
            continue
        keep = []
        for it in got:
            if it["published_at"] < cutoff:
                continue
            if is_advice_like(it["headline"]):
                dropped += 1
                continue
            keep.append(it)
            if len(keep) >= per_symbol:
                break
        if keep:
            items[sym] = keep
    return {"items": items, "failed": failed, "dropped_advice": dropped}
