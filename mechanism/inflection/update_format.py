#!/usr/bin/env python3
"""Fundamental Updates, Telegram formatter v2: a photo caption (HTML parse mode) plus link buttons, rendered from a PublicUpdate only.

The current owner-alert formatter (alerts.telegram_text) stays the default and is not touched; this module is used only by previews until a
later, approved phase wires it in behind a flag.

Telegram limits respected here: a photo caption is at most 1,024 characters AFTER entity parsing (tags removed, entities unescaped; counted
with telegram_client.text_length, the same function the client checks with); a text message 4,096; an inline keyboard button needs an
http(s) URL. When the caption is too long, parts are dropped in a fixed order (second news item, first news item, the industry line), never
a figure or the date line, and the drop is reported.
"""
from __future__ import annotations

import html
from typing import Any, Dict, List, Tuple

from alerts.telegram_client import CAPTION_MAX, TEXT_MAX, text_length
from inflection.update_model import PublicUpdate, fmt_date

MARK = "\U0001F4CA"       # the Fundamental Updates marker (Market Daily uses none)
FOOTER = "Reported figures from the company's filings. Not advice."


def _when(iso_utc: str) -> str:
    """'2026-11-04T21:10:00Z' -> 'Nov 4, 21:10 UTC'."""
    d, t = iso_utc[:10], iso_utc[11:16]
    return f"{fmt_date(d).rsplit(',', 1)[0]}, {t} UTC"


def _esc(s: str) -> str:
    """Text escaping for Telegram HTML: &, < and > only (quotes are escaped only inside href attributes)."""
    return html.escape(s, quote=False)


def _news_lines(u: PublicUpdate, n: int) -> List[str]:
    e = _esc
    return [f'• {e(_when(i.published_at))} · {e(i.source)}: <a href="{html.escape(i.url, quote=True)}">{e(i.headline)}</a>' for i in u.news[:n]]


def _compose(u: PublicUpdate, news_n: int, with_industry: bool, with_links: bool) -> str:
    e = _esc
    name = f" · {e(u.company_name)}" if u.company_name else ""
    lines = [f"{MARK} <b>FUNDAMENTAL UPDATE</b>", f"<b>{e(u.symbol)}</b>{name}", f"<b>{e(u.label)}</b>", "", e(u.why), ""]
    lines.append(f"<b>{e(u.fiscal_label)}</b>")
    for f in u.figures:
        lines.append(f"• {e(f.label)}: {e(f.value)}" + (f" ({e(f.compare)})" if f.compare else ""))
    news = _news_lines(u, news_n)
    if news:
        lines += ["", "<b>News</b>"] + news
    lines.append("")
    lines.append(f"Public {e(fmt_date(u.public_date))} ({e(u.date_source)})")
    ctx = [x for x in (u.sector, u.industry if with_industry else None) if x]
    if u.market_cap:
        ctx.append(f"market cap {u.market_cap['value']} on {fmt_date(u.market_cap['as_of'])}")
    if ctx:
        lines.append(e(" · ".join(ctx)))
    if with_links:
        links = [f'<a href="{html.escape(u.links[k], quote=True)}">{label}</a>' for k, label in (("sec", "SEC filing" if u.links.get("sec_kind") == "filing" else "SEC filings"),
                                                                                    ("finviz", "Finviz")) if u.links.get(k)]
        lines.append(" · ".join(links))
    lines.append(f"<i>{e(FOOTER)}</i>")
    return "\n".join(lines)


def caption(u: PublicUpdate, limit: int = CAPTION_MAX) -> Tuple[str, List[str]]:
    """(caption HTML, what was dropped to fit). Links travel as buttons (reply_markup), so the caption carries none."""
    steps = [(len(u.news), True, []), (min(1, len(u.news)), True, ["news_2"]), (0, True, ["news_2", "news_1"]), (0, False, ["news_2", "news_1", "industry"])]
    for news_n, industry, dropped in steps:
        text = _compose(u, news_n, industry, with_links=False)
        if text_length(text) <= limit:
            return text, [d for d in dropped if (d != "news_2" or len(u.news) >= 2) and (d != "news_1" or len(u.news) >= 1)]
    raise ValueError(f"{u.key}: caption cannot fit {limit} characters even without news")


def text_message(u: PublicUpdate) -> str:
    """Fallback when no photo is sent: the same content with inline links (fits one 4,096-character message)."""
    text = _compose(u, len(u.news), True, with_links=True)
    if text_length(text) > TEXT_MAX:
        raise ValueError(f"{u.key}: text message exceeds {TEXT_MAX} characters")
    return text


def buttons(u: PublicUpdate) -> Dict[str, Any]:
    """Telegram inline keyboard: one row with SEC and Finviz (no First Light link: owner decision 2026-11-07). Only https URLs."""
    row1 = []
    if u.links.get("sec"):
        row1.append({"text": "SEC filing" if u.links.get("sec_kind") == "filing" else "SEC filings", "url": u.links["sec"]})
    row1.append({"text": "Finviz", "url": u.links["finviz"]})
    kb = {"inline_keyboard": [row1]}
    for row in kb["inline_keyboard"]:
        for b in row:
            if not b["url"].startswith("https://"):
                raise ValueError(f"button URL must be https: {b['url']}")
    return kb
