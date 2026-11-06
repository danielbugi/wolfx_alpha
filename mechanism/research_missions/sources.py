"""Source collectors. Plain code, no model calls. Each returns `Doc`s carrying provenance: source, URL (or firstlight://... for our own
data), publication time, retrieval time, licence and a SHORT excerpt (never a full article).

  first_light_db  our database: universe by industry, prices and returns, market cap, SEC point-in-time revenue, inflection events
  news_items      the existing headline feed (internal only)
  gdelt           GDELT DOC 2.0 article search (free, no key; metadata only)
  tiingo_news     Tiingo News (internal research only; needs TIINGO_API_KEY, sent as a header and never logged)

HTTP goes through an injectable `http_get(url, headers) -> bytes`, so tests replay recorded responses and never touch the network.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence

from research_missions import config as C

HttpGet = Callable[[str, Dict[str, str]], bytes]


@dataclass
class Doc:
    source: str
    url: str
    title: Optional[str]
    publisher: Optional[str]
    published_at: Optional[datetime]
    license: str
    excerpt: str
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(json.dumps([self.source, self.url, self.title, self.excerpt], ensure_ascii=False).encode()).hexdigest()


def clip(text: Optional[str], n: int = C.EXCERPT_CHARS) -> str:
    t = " ".join((text or "").split())
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


def default_http_get(url: str, headers: Dict[str, str]) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "FirstLight-Research/1.0", **headers})
    with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 - fixed https endpoints only
        return r.read()


# ------------------------------------------------------------------------------------------------------------------------------------------------ our database
class FirstLightDb:
    def __init__(self, db) -> None:
        self.db = db

    def universe(self, domain: str) -> List[Dict[str, Any]]:
        industries = C.DOMAINS[domain]["industries"]
        if not industries:
            return []
        return self.db.execute_dict_query("""
            WITH latest AS (SELECT DISTINCT ON (symbol) symbol, sector, industry, market_cap FROM daily_fundamentals
                            WHERE date >= CURRENT_DATE - 30 AND industry IS NOT NULL ORDER BY symbol, date DESC)
            SELECT l.symbol, COALESCE(c.company_name, l.symbol) AS name, l.sector, l.industry, l.market_cap
            FROM latest l LEFT JOIN companies c ON c.symbol = l.symbol
            WHERE l.industry = ANY(%s) AND l.symbol NOT IN (SELECT symbol FROM inactive_symbols)
            ORDER BY l.market_cap DESC NULLS LAST""", (list(industries),))

    def company(self, symbol: str) -> Optional[Dict[str, Any]]:
        rows = self.db.execute_dict_query("""
            SELECT d.symbol, COALESCE(c.company_name, d.symbol) AS name, d.sector, d.industry, d.market_cap
            FROM (SELECT * FROM daily_fundamentals WHERE symbol = %s ORDER BY date DESC LIMIT 1) d LEFT JOIN companies c ON c.symbol = d.symbol""", (symbol,))
        return rows[0] if rows else None

    def event(self, event_id: int) -> Optional[Dict[str, Any]]:
        rows = self.db.execute_dict_query("SELECT id, symbol, quarter_end, event_type, severity, available_at, method_version FROM fundamental_inflection_event WHERE id = %s",
                                          (event_id,))
        return rows[0] if rows else None

    def peers(self, symbol: str, limit: int = 8) -> List[str]:
        rows = self.db.execute_dict_query("""
            WITH me AS (SELECT industry FROM daily_fundamentals WHERE symbol = %s AND industry IS NOT NULL ORDER BY date DESC LIMIT 1),
                 latest AS (SELECT DISTINCT ON (symbol) symbol, industry, market_cap FROM daily_fundamentals WHERE date >= CURRENT_DATE - 30 ORDER BY symbol, date DESC)
            SELECT l.symbol FROM latest l, me WHERE l.industry = me.industry AND l.symbol <> %s ORDER BY l.market_cap DESC NULLS LAST LIMIT %s""",
                                          (symbol, symbol, limit))
        return [r["symbol"] for r in rows]

    def company_facts(self, symbols: Sequence[str]) -> List[Doc]:
        """One document per company: price and return state, size, the latest SEC point-in-time revenue quarter, recent inflection events."""
        if not symbols:
            return []
        px = {r["symbol"]: r for r in self.db.execute_dict_query("""
            SELECT symbol, MAX(date) AS asof,
                   (ARRAY_AGG(close ORDER BY date DESC))[1] AS close,
                   (ARRAY_AGG(close ORDER BY date DESC))[127] AS close_126,
                   (ARRAY_AGG(close ORDER BY date DESC))[253] AS close_252
            FROM stock_prices WHERE symbol = ANY(%s) AND date >= CURRENT_DATE - 400 GROUP BY symbol""", (list(symbols),))}
        meta = {r["symbol"]: r for r in self.db.execute_dict_query("""
            SELECT DISTINCT ON (d.symbol) d.symbol, d.date, d.sector, d.industry, d.market_cap, COALESCE(c.company_name, d.symbol) AS name
            FROM daily_fundamentals d LEFT JOIN companies c ON c.symbol = d.symbol WHERE d.symbol = ANY(%s) ORDER BY d.symbol, d.date DESC""", (list(symbols),))}
        rev = {}
        for r in self.db.execute_dict_query("""
            SELECT m.symbol, q.period_end, q.value, q.filed_date, q.form FROM sec_quarter q JOIN sec_symbol_cik m ON m.cik = q.cik
            WHERE m.symbol = ANY(%s) AND q.metric = 'revenue' AND q.period_end >= CURRENT_DATE - 520 ORDER BY m.symbol, q.period_end""", (list(symbols),)):
            rev.setdefault(r["symbol"], []).append(r)
        evs = {}
        for r in self.db.execute_dict_query("""
            SELECT symbol, quarter_end, event_type, severity, available_at, method_version FROM fundamental_inflection_event
            WHERE symbol = ANY(%s) AND method_version = 'inflection_v2_sec_pit' AND available_at >= CURRENT_DATE - 200 ORDER BY symbol, available_at DESC""",
                                            (list(symbols),)):
            evs.setdefault(r["symbol"], []).append(r)
        docs = []
        for s in symbols:
            m, p = meta.get(s), px.get(s)
            if not m and not p:
                continue
            parts = [f"{s} ({(m or {}).get('name') or s}): {(m or {}).get('sector') or 'n/a'} / {(m or {}).get('industry') or 'n/a'}."]
            if p and p.get("close"):
                r6 = (float(p["close"]) / float(p["close_126"]) - 1) if p.get("close_126") else None
                r12 = (float(p["close"]) / float(p["close_252"]) - 1) if p.get("close_252") else None
                parts.append(f"Close {float(p['close']):.2f} on {p['asof']}; 6-month return {_pct(r6)}; 12-month return {_pct(r12)}.")
            if m and m.get("market_cap"):
                parts.append(f"Market cap ${float(m['market_cap']) / 1e9:.1f}B ({m['date']}).")
            q = rev.get(s) or []
            if q:
                last = q[-1]
                prior = next((x for x in q if abs((last["period_end"] - x["period_end"]).days - 365) <= 25), None)
                yoy = (float(last["value"]) / float(prior["value"]) - 1) if prior and float(prior["value"]) else None
                parts.append(f"SEC: revenue ${float(last['value']) / 1e9:.2f}B for the quarter ended {last['period_end']} ({last['form']} filed {last['filed_date']}), "
                             f"year over year {_pct(yoy)}.")
            for e in (evs.get(s) or [])[:3]:
                parts.append(f"SEC point-in-time inflection: {e['event_type'].replace('_', ' ')} ({e['severity']}), quarter {e['quarter_end']}, public {e['available_at']}.")
            asof = (p or {}).get("asof") or (m or {}).get("date")
            docs.append(Doc("first_light_db", f"firstlight://company/{s}/{asof}", f"{s} fundamentals and price state", "First Light database",
                            _dt(asof), C.SOURCES["first_light_db"]["license"], clip(" ".join(parts), 700), {"symbol": s}))
        return docs

    def headlines(self, symbols: Sequence[str], days: int = C.NEWS_LOOKBACK_DAYS, per_symbol: int = 5) -> List[Doc]:
        rows = self.db.execute_dict_query("""
            SELECT * FROM (SELECT symbol, published_at, headline, source, url, ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY published_at DESC) AS rn
                           FROM news_items WHERE symbol = ANY(%s) AND published_at >= NOW() - make_interval(days => %s)) x WHERE rn <= %s
            ORDER BY published_at DESC""", (list(symbols), days, per_symbol))
        return [Doc("news_items", r["url"], r["headline"], r["source"], r["published_at"], C.SOURCES["news_items"]["license"], clip(r["headline"]),
                    {"symbol": r["symbol"]}) for r in rows]


def _pct(v: Optional[float]) -> str:
    return "n/a" if v is None else f"{v * 100:+.1f}%"


def _dt(d: Any) -> Optional[datetime]:
    if d is None:
        return None
    if isinstance(d, datetime):
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    if isinstance(d, date):
        return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    return None


# ------------------------------------------------------------------------------------------------------------------------------------------------ GDELT
GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"


class Gdelt:
    """DOC 2.0 article list. Metadata only (title, URL, date, domain). One request per 5 seconds (GDELT's documented courtesy pace)."""

    def __init__(self, http_get: HttpGet = default_http_get, pause_s: float = 5.0, sleep: Callable[[float], None] = time.sleep) -> None:
        self.http_get, self.pause_s, self.sleep = http_get, pause_s, sleep
        self._last = 0.0

    def search(self, query: str, timespan: str = "3months", max_records: int = C.GDELT_PER_QUERY) -> List[Doc]:
        wait = self.pause_s - (time.monotonic() - self._last)
        if self._last and wait > 0:
            self.sleep(wait)
        url = GDELT_URL + "?" + urllib.parse.urlencode({"query": query, "mode": "ArtList", "format": "json", "maxrecords": max_records, "timespan": timespan,
                                                         "sort": "DateDesc"})
        raw = self.http_get(url, {})
        self._last = time.monotonic()
        try:
            arts = json.loads(raw.decode("utf-8", "replace") or "{}").get("articles") or []
        except ValueError:
            return []                                   # GDELT answers malformed queries with plain text
        out = []
        for a in arts:
            if not a.get("url") or not a.get("title"):
                continue
            out.append(Doc("gdelt", a["url"], clip(a["title"], 200), a.get("domain"), _gdelt_time(a.get("seendate")), C.SOURCES["gdelt"]["license"],
                           clip(a["title"]), {"query": query, "language": a.get("language"), "country": a.get("sourcecountry")}))
        return out


def _gdelt_time(s: Optional[str]) -> Optional[datetime]:
    try:
        return datetime.strptime(s, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc) if s else None
    except ValueError:
        return None


# ------------------------------------------------------------------------------------------------------------------------------------------------ Tiingo News
TIINGO_NEWS_URL = "https://api.tiingo.com/tiingo/news"


class TiingoNews:
    """Internal research only (licence). Stores title, URL, date, tickers and a <= 300-character description excerpt."""

    def __init__(self, http_get: HttpGet = default_http_get, token: Optional[str] = None) -> None:
        self.http_get = http_get
        self.token = token if token is not None else os.environ.get("TIINGO_API_KEY", "")

    @property
    def available(self) -> bool:
        return bool(self.token)

    def fetch(self, tickers: Sequence[str], days: int = C.NEWS_LOOKBACK_DAYS, limit: int = 60) -> List[Doc]:
        if not self.available or not tickers:
            return []
        start = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
        url = TIINGO_NEWS_URL + "?" + urllib.parse.urlencode({"tickers": ",".join(t.lower() for t in tickers), "startDate": start, "limit": limit, "sortBy": "publishedDate"})
        raw = self.http_get(url, {"Authorization": f"Token {self.token}", "Content-Type": "application/json"})
        try:
            items = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            return []
        out = []
        for it in items if isinstance(items, list) else []:
            if not it.get("url"):
                continue
            pub = None
            try:
                pub = datetime.fromisoformat(str(it.get("publishedDate")).replace("Z", "+00:00"))
            except ValueError:
                pass
            out.append(Doc("tiingo_news", it["url"], clip(it.get("title"), 200), it.get("source"), pub, C.SOURCES["tiingo_news"]["license"],
                           clip(it.get("description") or it.get("title")), {"tickers": it.get("tickers") or [], "tags": (it.get("tags") or [])[:10]}))
        return out
