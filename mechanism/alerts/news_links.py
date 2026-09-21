# mechanism/alerts/news_links.py
"""
A few recent news articles per stock, rendered as short clickable Telegram HTML links, e.g.
    <a href="...">Reuters · Company wins $2B contract…</a> (3h)

Source: yfinance `Ticker.news` (Yahoo Finance). yfinance changed the payload shape, so both the current
({"content": {...}}) and the legacy ({"title", "link", "publisher", "providerPublishTime"}) shapes are handled.
"Shortened" means a shortened *title* on the link text -- no third-party URL shortener is used (it would add a
redirect through someone else's server and break if that service disappears). Anything missing/failed -> [].
"""
from __future__ import annotations

import html
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
from urllib.parse import urlparse

PUBLISHER_ALIASES = {
    "the motley fool": "Motley Fool", "motley fool": "Motley Fool", "yahoo finance": "Yahoo", "yahoo finance video": "Yahoo",
    "reuters": "Reuters", "bloomberg": "Bloomberg", "the wall street journal": "WSJ", "marketwatch": "MarketWatch",
    "investor's business daily": "IBD", "benzinga": "Benzinga", "seeking alpha": "Seeking Alpha", "barrons.com": "Barron's",
    "simply wall st.": "Simply Wall St", "zacks": "Zacks", "gurufocus.com": "GuruFocus", "investopedia": "Investopedia",
    "associated press finance": "AP", "cnbc": "CNBC", "tipranks": "TipRanks", "insider monkey": "Insider Monkey",
}


def _parse_time(value) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _safe_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    p = urlparse(url)
    return url if p.scheme in ("http", "https") and p.netloc else None


def normalize(item: Dict) -> Optional[Dict]:
    """-> {title, url, publisher, published(datetime, UTC)} or None if unusable."""
    c = item.get("content") if isinstance(item.get("content"), dict) else item
    title = (c.get("title") or "").strip()
    url = _safe_url(((c.get("canonicalUrl") or {}).get("url")) or ((c.get("clickThroughUrl") or {}).get("url")) or c.get("link"))
    prov = c.get("provider")
    publisher = (prov.get("displayName") if isinstance(prov, dict) else None) or c.get("publisher") or ""
    published = _parse_time(c.get("pubDate") or c.get("displayTime") or c.get("providerPublishTime"))
    if not title or not url or published is None:
        return None
    return {"title": title, "url": url, "publisher": publisher.strip(), "published": published}


def shorten_title(title: str, max_len: int = 46) -> str:
    """Cut at a word boundary and add an ellipsis; collapses whitespace."""
    t = re.sub(r"\s+", " ", title).strip()
    if len(t) <= max_len:
        return t
    cut = t[: max_len - 1]
    if " " in cut[max_len // 2:]:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip(" ,;:-–—") + "…"


def short_publisher(name: str, max_len: int = 14) -> str:
    n = PUBLISHER_ALIASES.get(name.strip().lower(), name.strip())
    return n if len(n) <= max_len else n[: max_len - 1].rstrip() + "…"


def age_label(published: datetime, now: datetime) -> str:
    secs = max(0, int((now - published).total_seconds()))
    if secs < 3600:
        return f"{max(1, secs // 60)}m"
    if secs < 86400:
        return f"{secs // 3600}h"
    return f"{secs // 86400}d"


def _dedupe_key(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", title.lower())[:40]


def pick_articles(items: List[Dict], n: int, now: Optional[datetime] = None, max_age_days: float = 5) -> List[Dict]:
    """Newest first, drop near-duplicate headlines, prefer distinct publishers, drop stale items."""
    now = now or datetime.now(timezone.utc)
    norm = [x for x in (normalize(i) for i in items) if x and (now - x["published"]) <= timedelta(days=max_age_days)]
    norm.sort(key=lambda a: a["published"], reverse=True)
    seen_titles, seen_pubs, out, spill = set(), set(), [], []
    for a in norm:
        k = _dedupe_key(a["title"])
        if k in seen_titles:
            continue
        seen_titles.add(k)
        if a["publisher"] in seen_pubs:
            spill.append(a)          # same publisher again: only used if we lack variety
            continue
        seen_pubs.add(a["publisher"]); out.append(a)
    return (out + spill)[:n]


def link_html(article: Dict, now: Optional[datetime] = None, max_title: int = 46) -> str:
    now = now or datetime.now(timezone.utc)
    label = f"{short_publisher(article['publisher'])} · {shorten_title(article['title'], max_title)}" if article["publisher"] \
        else shorten_title(article["title"], max_title)
    return f'<a href="{html.escape(article["url"], quote=True)}">{html.escape(label, quote=False)}</a> ({age_label(article["published"], now)})'


def fetch_news(symbol: str, n: int = 3, max_age_days: float = 5, now: Optional[datetime] = None) -> List[Dict]:
    """Best effort: any failure (network, Yahoo change, unknown symbol) returns []."""
    try:
        import yfinance as yf
        items = yf.Ticker(symbol).news or []
        return pick_articles(items, n, now=now, max_age_days=max_age_days)
    except Exception:
        return []
