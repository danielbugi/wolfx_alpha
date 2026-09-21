# mechanism/alerts/digest_format.py
"""Telegram HTML rendering of the "First Light" digest: one header message + one message per group. Plain words only,
laid out for a phone (about 38 characters per line): a short first line per stock, details on a second line."""
from __future__ import annotations

import html
from datetime import date, datetime
from typing import Dict, List, Optional
from urllib.parse import quote

from alerts import deeplink
from alerts.digest_builder import LISTS, LONG_CATEGORIES
from alerts.message_format import fmt_price
from alerts.texts import DEFINITIONS, DISCLAIMER_SHORT

SMALL_CAP_USD = 2_000_000_000.0     # "small cap" tag: market value under $2B (a fact from daily_fundamentals; no tag when unknown)
INDENT =" "          # em space: survives Telegram's whitespace trimming, indents the detail line

GROUPS = {
    "breakout": ("BREAKOUT", "closed above their 20-day high"),
    "near_breakout": ("NEAR BREAKOUT", "within 3% below their 20-day high, not through it yet"),
}
HEADLINE_LABEL = {"breakout": "breakouts", "near_breakout": "near breakouts"}
LIST_TITLES = {
    "gainers": ("Top gainers", "biggest % gain today"),
    "atr": ("Top ATR", "today's range vs its 14-day average"),
    "volume": ("Top volume", "today's volume vs its 50-day median"),
}
LIST_WORD = {"gainers": "gainers", "atr": "ATR", "volume": "volume"}


def _link(sym: str) -> str:
    q = quote(sym, safe="")
    return f'<a href="https://finviz.com/quote.ashx?t={q}&amp;p=d">{html.escape(sym)}</a>'


def _arrow(pct: float) -> str:
    return f"{'▲' if pct > 0 else '▼' if pct < 0 else '■'}{abs(pct):.1f}%"


def _row(i: int, cat: str, lst: str, r: Dict, multi: Dict[str, List[str]]) -> str:
    star = "★ " if r["symbol"] in multi else ""
    head = f"{i}. {star}<b>{_link(r['symbol'])}</b> {fmt_price(r['close'])} {_arrow(r['ret1_pct'])}"
    if cat == "near_breakout" and r["new"]:
        head += " · NEW"                                       # a breakout is always new, so only near breakouts carry it
    detail = []
    if lst == "atr":
        detail.append(f"range {r['range_atr']:.1f}× ATR")
    elif r["rvol"] is not None:
        detail.append(f"vol {r['rvol']:.1f}×")
    if cat == "near_breakout" and r["below_high_pct"] is not None:
        detail.append(f"{r['below_high_pct']:.1f}% below high")
    if cat == "breakout" and r["prev_cat"] == "near_breakout":
        detail.append("was near yesterday")
    if r.get("mcap") is not None and r["mcap"] < SMALL_CAP_USD:
        detail.append("small cap")
    return head + (f"\n{INDENT}" + " · ".join(detail) if detail else "")


def format_group(cat: str, digest: Dict, top_n: int = 5) -> str:
    title, rule = GROUPS[cat]
    board = digest["boards"][cat]
    lines = [f"<b>{title}</b>", f"<i>{rule}</i>", f"{digest['counts'][cat]} stocks today",
             f"<i>Lists use the {board['eligible']} with ≥${digest['min_dv'] / 1e6:g}M average daily trading value</i>"]
    for lst in LISTS:
        name, what = LIST_TITLES[lst]
        items = board[lst][:top_n]
        body = [_row(i, cat, lst, r, board["multi"]) for i, r in enumerate(items, 1)] or ["none today"]
        # a quote block puts a vertical bar beside each list, so the three lists read as separate blocks
        lines += ["", f"<b>{name}</b>", f"<i>{what}</i>", "<blockquote>" + "\n".join(body) + "</blockquote>"]
    return "\n".join(lines)


def _legend() -> str:
    """Collapsed by default in Telegram (expandable quote), so the header stays short for people who know the terms."""
    body = "\n".join([
        "How to read",
        html.escape(DEFINITIONS["atr"], quote=False),
        html.escape(DEFINITIONS["vol"], quote=False),
        "★ = the stock is in more than one list.",
        "NEW = it was not a near breakout yesterday. \"was near yesterday\" = it was a near breakout the session before.",
        "small cap = market value under $2B (latest fundamentals; no tag when unknown).",
        "Tap a ticker to open its chart.",
        "In the bot: /levels TICKER shows an ATR-based risk framework for any stock in the scan (educational).",
    ])
    return f"<blockquote expandable>{body}</blockquote>"


def format_caption(session: date, digest: Dict) -> str:
    """Caption of the market-card photo (Telegram limit: 1024 chars). The first line is what the notification shows."""
    counts = ", ".join(f"{digest['counts'][c]} {HEADLINE_LABEL[c]}" for c in LONG_CATEGORIES)
    return f"<b>First Light</b> · {session:%a %d %b} · {counts}\nEducational data, not investment advice."


def format_header(session: date, now_local: datetime, digest: Dict, universe_n: int, breadth: Dict[str, int],
                  index_lines: Optional[List[str]] = None, notes: Optional[List[str]] = None,
                  with_market: bool = True) -> str:
    counts = ", ".join(f"{digest['counts'][c]} {HEADLINE_LABEL[c]}" for c in LONG_CATEGORIES)
    lines = [f"<b>First Light</b> · {session:%a %d %b} · {counts}",
             f"Daily long-side scan of {universe_n:,} liquid US stocks · educational data",
             ""]
    if with_market:                                            # with the market-card image this block is redundant
        lines.append(f"<b>Market</b> (US close {session:%a %d %b})")
        lines += [html.escape(x) for x in index_lines or []]
        lines += [f"Stocks up {breadth['up']:,} · down {breadth['down']:,}", ""]
    lines += ["<b>Groups</b>"]
    for cat in LONG_CATEGORIES:
        lines.append(f"{GROUPS[cat][0].capitalize()} — {GROUPS[cat][1]}")
    lines += ["", "<b>In more than one list</b>"]
    for cat in LONG_CATEGORIES:
        multi = digest["boards"][cat]["multi"]
        names = ", ".join(f"★ <b>{_link(s)}</b> ({', '.join(LIST_WORD[x] for x in ls)})" for s, ls in multi.items()) or "none today"
        lines.append(f"{GROUPS[cat][0].capitalize()}: {names}")
    lines += ["", _legend(), "", DISCLAIMER_SHORT, f"Sent {now_local:%a %d %b %H:%M} Jerusalem"]
    for n in notes or []:
        lines.append(f"Note: {html.escape(n)}")
    return "\n".join(lines)


PRIVATE_ASSISTANT_BUTTON = "Private assistant (invite only)"


def header_keyboard(bot_username: str) -> Dict:
    """Inline keyboard under the channel header: three educational popups (need the bot process running) and ONE neutral link to
    the bot. The bot is invite-only, so a reader without an invitation lands on a short refusal; there are no per-ticker or
    strategy buttons in the public channel (PRIVATE_ASSISTANT_PLAN.md decision D2)."""
    return {"inline_keyboard": [
        [{"text": "What is ATR?", "callback_data": "def:atr"}, {"text": "What is vol ×?", "callback_data": "def:vol"}],
        [{"text": "What are the groups?", "callback_data": "def:groups"}],
        [{"text": PRIVATE_ASSISTANT_BUTTON, "url": deeplink.link(bot_username, "ch")}],
    ]}
