#!/usr/bin/env python3
"""Fundamental Updates: choose at most two news items for one update, point-in-time safe.

A provider ticker tag and a date window are not enough. Real Alpaca items for the 2026-11-04/05 events showed ticker collisions with other
exchanges (TKO -> Taseko Mines, Tikehau Capital, Trekor Metals; FTK -> flatexDEGIRO), market roundups ("20 Stocks Moving Premarket",
CNBC "Final Trades") and analyst rating notes. An item is kept only if ALL of these hold:

  1. POINT IN TIME   published_at <= cutoff, and (when known) updated_at <= cutoff and fetched_at <= cutoff: nothing that was not visible at
                     the moment the update is prepared, and no headline later edited by the provider.
  2. WINDOW          published within [public date - 1 day, public date + 3 days] (and still before the cutoff).
  3. TAGGED, FOCUSED the provider tags the symbol, and tags at most MAX_TAGGED_SYMBOLS symbols in total (roundups tag many).
  4. NAMED           the headline names the company (its core name, legal suffixes removed, matched on word boundaries). This is what removes
                     the cross-exchange ticker collisions.
  5. NOT A ROUNDUP   no roundup wording ("stocks moving", "final trades", "top 10", ...).
  6. NOT A RATING    no analyst action (upgrades, price targets, "maintains buy" ...): someone else's recommendation is never forwarded.
  7. PUBLIC MODE     the headline also passes the channel's advice-word guard. Owner previews may skip only this rule.
Then items are de-duplicated (URL and normalised headline) and ranked: the company's own results release, then outlook/guidance, then the
rest; ties by closeness to the public date. Nothing here fetches anything: the caller passes items (stored or freshly fetched) and a cutoff.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence

from inflection.update_model import ADVICE_WORDS, NewsItem

MAX_ITEMS = 2
MAX_TAGGED_SYMBOLS = 2
WINDOW_BEFORE_DAYS, WINDOW_AFTER_DAYS = 1, 3

_SUFFIXES = {"inc", "incorporated", "corp", "corporation", "co", "company", "companies", "holdings", "holding", "group", "ltd", "limited", "plc",
             "llc", "lp", "l.p", "n.v", "nv", "s.a", "sa", "ag", "se", "the", "class", "a", "b", "c", "common", "stock", "shares", "ordinary", "adr", "&"}
ROUNDUP = re.compile(r"stocks? (moving|to watch|on the move)|movers|final trades|\bhere are \d+|\btop \d+|mid-?day|pre-?market|after-?hours|"
                     r"\bmarket (wrap|recap)|\bbiggest\b|\bthese \d+|\band more\b|\bstocks? (that|which)\b", re.I)
RATING = re.compile(r"price target|\b(upgrades?|downgrades?|initiates?|reiterates?|maintains|rating|overweight|underweight|equal-weight|"
                    r"market perform|sector perform|outperform|underperform|neutral|buy|sell|hold)\b|\bpt\b", re.I)
RESULTS = re.compile(r"(reports?|announces?|posts?)\b.*\b(results|quarter|q[1-4])\b|financial results|\b(10-q|10-k)\b", re.I)
OUTLOOK = re.compile(r"\b(guidance|outlook|sees q[1-4]|sees fy|forecast)\b", re.I)


def core_name(company_name: Optional[str]) -> Optional[str]:
    """'TKO Group Holdings, Inc.' -> 'tko'; 'Power Integrations, Inc.' -> 'power integrations'. None when nothing distinctive is left."""
    if not company_name:
        return None
    toks = [t.strip(".,()'\"") for t in re.split(r"[\s,]+", company_name.lower())]
    while toks and toks[-1] in _SUFFIXES | {""}:
        toks.pop()
    toks = [t for t in toks if t][:3]
    while toks and toks[0] == "the":
        toks.pop(0)
    name = " ".join(toks)
    return name if len(name.replace(" ", "")) >= 3 else None


# First words too common to identify a company on their own ("Power Integrations" must not match any headline saying "power").
_COMMON_FIRST = {"power", "american", "first", "general", "united", "global", "national", "international", "energy", "capital", "digital", "health",
                 "financial", "new", "great", "west", "east", "north", "south", "pacific", "atlantic", "golden", "real", "data", "applied", "advanced",
                 "life", "lifetime", "home", "royal", "summit", "pioneer", "liberty", "frontier", "central", "southern", "northern", "western",
                 "eastern", "public", "world", "smart", "green", "blue", "silver", "star", "bank", "trust", "medical", "service", "services"}


def _word(phrase: str, text: str) -> bool:
    return re.search(r"(?<![\w-])" + re.escape(phrase).replace(r"\ ", r"[\s\-]+") + r"(?![\w-])", text) is not None


def names_company(headline: str, company_name: Optional[str]) -> bool:
    """The full core name, or its first word when that word is distinctive on its own ('Flotek' for 'Flotek Industries')."""
    core = core_name(company_name)
    if not core:
        return False
    h = headline.lower()
    if _word(core, h):
        return True
    first = core.split(" ")[0]
    return " " in core and len(first) >= 5 and first not in _COMMON_FIRST and _word(first, h)


def _ts(v: Any) -> Optional[datetime]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _norm(h: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", h.lower()).strip()


def rejection(item: Mapping[str, Any], *, symbol: str, company_name: Optional[str], public_date: date, cutoff: datetime, public: bool) -> Optional[str]:
    """Why one item is not usable (None: usable)."""
    pub = _ts(item.get("published_at"))
    if pub is None or not item.get("headline") or not str(item.get("url") or "").startswith(("https://", "http://")):
        return "malformed"
    for k in ("published_at", "updated_at", "fetched_at"):
        t = _ts(item.get(k))
        if t is not None and t > cutoff:
            return f"{k}_after_cutoff"
    lo = datetime.combine(public_date - timedelta(days=WINDOW_BEFORE_DAYS), datetime.min.time(), tzinfo=timezone.utc)
    hi = datetime.combine(public_date + timedelta(days=WINDOW_AFTER_DAYS + 1), datetime.min.time(), tzinfo=timezone.utc)
    if not lo <= pub < hi:
        return "outside_window"
    syms = item.get("symbols")
    if syms is not None:
        if symbol not in syms:
            return "not_tagged"
        if len(syms) > MAX_TAGGED_SYMBOLS:
            return "too_many_symbols"
    h = str(item["headline"])
    if not names_company(h, company_name):
        return "company_not_named"
    if ROUNDUP.search(h):
        return "roundup"
    if RATING.search(h):
        return "analyst_rating"
    if public and ADVICE_WORDS.search(h):
        return "advice_words"
    return None


def select_news(items: Sequence[Mapping[str, Any]], *, symbol: str, company_name: Optional[str], public_date: date, cutoff: datetime,
                public: bool = True, limit: int = MAX_ITEMS) -> Dict[str, Any]:
    """{'items': [NewsItem, ...] (at most `limit`), 'rejected': {reason: count}}."""
    rejected: Dict[str, int] = {}
    keep: List[Mapping[str, Any]] = []
    seen_url, seen_head = set(), set()
    for it in items:
        why = rejection(it, symbol=symbol, company_name=company_name, public_date=public_date, cutoff=cutoff, public=public)
        if why is None and (it["url"] in seen_url or _norm(it["headline"]) in seen_head):
            why = "duplicate"
        if why:
            rejected[why] = rejected.get(why, 0) + 1
            continue
        seen_url.add(it["url"]); seen_head.add(_norm(it["headline"]))
        keep.append(it)
    anchor = datetime.combine(public_date, datetime.min.time(), tzinfo=timezone.utc)

    def rank(it):
        h = it["headline"]
        tier = 0 if RESULTS.search(h) else 1 if OUTLOOK.search(h) else 2
        return (tier, abs((_ts(it["published_at"]) - anchor).total_seconds()), it["url"])
    chosen = sorted(keep, key=rank)[:limit]
    out = [NewsItem(published_at=_ts(i["published_at"]).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), source=_source(i.get("source")),
                    headline=" ".join(str(i["headline"]).split())[:200], url=str(i["url"])) for i in chosen]
    return {"items": out, "rejected": rejected}


_SOURCES = {"benzinga": "Benzinga", "fiscal.ai": "Fiscal.ai", "reuters": "Reuters", "businesswire": "Business Wire", "globenewswire": "GlobeNewswire",
            "prnewswire": "PR Newswire"}


def _source(s: Optional[str]) -> str:
    s = (s or "").strip()
    return _SOURCES.get(s.lower(), s[:40] or "News")
