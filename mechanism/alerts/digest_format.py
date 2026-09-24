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
from alerts.telegram_client import text_length
from alerts.texts import DEFINITIONS

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


FLAT_INDEX_PCT = 0.05            # an index move smaller than this (in %) counts as flat when the day's headline is chosen
BROAD_SHARE = 60                 # % of the liquid stocks that must move the same way for "broad rally" / "broad sell-off" (60 so a 64.97% day
                                 # that prints as "65%" is never called "mixed")


def headline(sp_pct: Optional[float], up_n: int, down_n: int, universe_n: int) -> Optional[str]:
    """The day's one-line hook, chosen by rule from facts only: the S&P 500's direction against how many of the liquid stocks rose or fell (the
    same shares the market card's breadth bar shows). None when there is nothing honest to say (no breadth). Divergence first, because it is
    what the index tile alone hides."""
    if universe_n <= 0 or up_n + down_n <= 0:
        return None
    up_share, down_share = up_n / universe_n * 100, down_n / universe_n * 100
    movers_up = up_n / (up_n + down_n)
    if sp_pct is not None:
        if sp_pct >= FLAT_INDEX_PCT and movers_up < 0.45:
            return f"Index up. {down_share:.0f}% of stocks down."
        if sp_pct <= -FLAT_INDEX_PCT and movers_up > 0.55:
            return f"Index down. {up_share:.0f}% of stocks up."
    if up_share >= BROAD_SHARE:
        return f"Broad rally. {up_share:.0f}% of stocks up."
    if down_share >= BROAD_SHARE:
        return f"Broad sell-off. {down_share:.0f}% of stocks down."
    return f"Mixed. {up_share:.0f}% of stocks up."


def _row(i: int, cat: str, lst: str, r: Dict, multi: Dict[str, List[str]], tag_new: bool = True) -> str:
    star = "★ " if r["symbol"] in multi else ""
    head = f"{i}. {star}<b>{_link(r['symbol'])}</b> {fmt_price(r['close'])} {_arrow(r['ret1_pct'])}"
    if cat == "near_breakout" and r["new"] and tag_new:
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


SHOW_N = 5                       # rows every list shows; deeper ranks sit in a collapsed quote (tap to expand)
MESSAGE_LIMIT = 3900                # parsed characters per message (Telegram allows 4096)


def _group_head(cat: str, digest: Dict) -> List[str]:
    title, rule = GROUPS[cat]
    board = digest["boards"][cat]
    return [f"<b>{title} · {digest['counts'][cat]}</b>", f"<i>{rule}</i>",
            f"<i>Ranked: the {board['eligible']} with ${digest['min_dv'] / 1e6:g}M+ average daily volume</i>"]


def _list_block(cat: str, lst: str, board: Dict, top_n: int, show_n: Optional[int]) -> str:
    """One list: a title, the first `show_n` rows in a quote block, and (when the list is deeper) the remaining ranks in an EXPANDABLE quote
    that Telegram shows collapsed. A quote block puts a vertical bar beside each list, so the lists read as separate blocks. The NEW tag is left
    off a list where every visible row carries it (a tag on every row says nothing)."""
    name, _what = LIST_TITLES[lst]
    listed = board[lst][:top_n]
    tag_new = not (cat == "near_breakout" and listed and all(r["new"] for r in listed))
    rows = [_row(i, cat, lst, r, board["multi"], tag_new) for i, r in enumerate(listed, 1)]
    lines = ["", f"<b>{name}</b>"]
    shown = rows if show_n is None else rows[:show_n]
    lines.append("<blockquote>" + "\n".join(shown or ["none today"]) + "</blockquote>")
    if len(shown) < len(rows):
        lines += [f"<i>More: ranks {len(shown) + 1}–{len(rows)} · tap to expand</i>",
                  "<blockquote expandable>" + "\n".join(rows[len(shown):]) + "</blockquote>"]
    return "\n".join(lines)


def format_group(cat: str, digest: Dict, top_n: int = 5, show_n: Optional[int] = None) -> str:
    """The whole group as ONE string (top_n rows per list, the first show_n visible, the rest collapsed; show_n=None = no collapse)."""
    board = digest["boards"][cat]
    return "\n".join(_group_head(cat, digest) + [_list_block(cat, lst, board, top_n, show_n) for lst in LISTS])


def group_messages(cat: str, digest: Dict, top_n: int = 15, show_n: Optional[int] = SHOW_N, limit: int = MESSAGE_LIMIT) -> List[str]:
    """The group as one or more Telegram messages, each within `limit` PARSED characters. Whole lists are kept together (a quote block is never cut),
    so a very deep digest continues in a second message that names the group again. Normally the group is a single message."""
    board = digest["boards"][cat]
    head = "\n".join(_group_head(cat, digest))
    msgs, cur = [], head
    for lst in LISTS:
        block = _list_block(cat, lst, board, top_n, show_n)
        if text_length(cur + "\n" + block) > limit and cur != head:
            msgs.append(cur)
            cur = f"<b>{GROUPS[cat][0]}</b> (continued)"
        cur = cur + "\n" + block
    msgs.append(cur)
    return msgs


def _legend() -> str:
    """Collapsed by default in Telegram (expandable quote), so the header stays short for people who know the terms."""
    body = "\n".join([
        "How to read",
        html.escape(DEFINITIONS["atr"], quote=False),
        html.escape(DEFINITIONS["vol"], quote=False),
        "★ = in the top 5 of more than one list.",
        "NEW = it was not a near breakout yesterday. \"was near yesterday\" = it was a near breakout the session before.",
        "small cap = market value under $2B (latest fundamentals; no tag when unknown).",
        "Tap a ticker to open its chart.",
        "In the bot: /levels TICKER shows an ATR-based risk framework for any stock in the scan (educational).",
    ])
    return f"<blockquote expandable>{body}</blockquote>"


def _star_lines(digest: Dict) -> List[str]:
    out = []
    for cat in LONG_CATEGORIES:
        multi = digest["boards"][cat]["multi"]
        if multi:
            out.append(f"★ {GROUPS[cat][0].capitalize()}: " + " · ".join(f"<b>{_link(s)}</b>" for s in multi))
    return out


def format_caption(session: date, digest: Dict, hook: Optional[str] = None, notes: Optional[List[str]] = None) -> str:
    """Caption of the market-card photo (Telegram limit: 1024 chars) - the whole opening of the digest. The first line is what the notification
    shows, so it is the day's hook (see headline()); then the counts and the stocks that are in more than one list. Without a hook it falls
    back to the plain counts line."""
    b, n = digest["counts"]["breakout"], digest["counts"]["near_breakout"]
    if hook:
        lines = [f"<b>{html.escape(hook, quote=False)}</b>", f"{session:%a %d %b} · {b} broke out · {n} within 3% of it"]
    else:
        lines = [f"<b>First Light</b> · {session:%a %d %b} · {b} breakouts, {n} near breakouts"]
    lines += _star_lines(digest)
    lines += [f"Note: {html.escape(x)}" for x in notes or []]
    return "\n".join(lines)


def format_header(session: date, now_local: datetime, digest: Dict, universe_n: int, breadth: Dict[str, int],
                  index_lines: Optional[List[str]] = None, notes: Optional[List[str]] = None,
                  with_market: bool = True, hook: Optional[str] = None) -> str:
    """The text-only opening (used when the market card could not be rendered). `now_local` is kept for callers but no longer printed: a send
    time tells a reader in another time zone nothing."""
    counts = ", ".join(f"{digest['counts'][c]} {HEADLINE_LABEL[c]}" for c in LONG_CATEGORIES)
    lines = [f"<b>{html.escape(hook, quote=False)}</b>" if hook else f"<b>First Light</b> · {session:%a %d %b} · {counts}",
             f"{session:%a %d %b} · {counts}" if hook else f"Daily long-side scan of {universe_n:,} liquid US stocks · educational data",
             ""]
    if with_market:                                            # with the market-card image this block is redundant
        lines.append(f"<b>Market</b> (US close {session:%a %d %b})")
        lines += [html.escape(x) for x in index_lines or []]
        lines += [f"Stocks up {breadth['up']:,} · down {breadth['down']:,}", ""]
    lines += ["<b>Groups</b>"]
    for cat in LONG_CATEGORIES:
        lines.append(f"{GROUPS[cat][0].capitalize()} — {GROUPS[cat][1]}")
    lines += ["", "<b>Top 5 of more than one list</b>"]
    for cat in LONG_CATEGORIES:
        multi = digest["boards"][cat]["multi"]
        names = ", ".join(f"★ <b>{_link(s)}</b> ({', '.join(LIST_WORD[x] for x in ls)})" for s, ls in multi.items()) or "none today"
        lines.append(f"{GROUPS[cat][0].capitalize()}: {names}")
    lines += ["", _legend(), "", "New here? Read the pinned message."]
    for n in notes or []:
        lines.append(f"Note: {html.escape(n)}")
    return "\n".join(lines)


PRIVATE_ASSISTANT_BUTTON = "Request access"


def header_keyboard(bot_username: str) -> Dict:
    """Inline keyboard under the channel header: three educational popups (need the bot process running) and ONE neutral link to
    the bot. The bot is invite-only, so a reader without an invitation lands on a short refusal; there are no per-ticker or
    strategy buttons in the public channel (PRIVATE_ASSISTANT_PLAN.md decision D2)."""
    return {"inline_keyboard": [
        [{"text": "What is ATR?", "callback_data": "def:atr"}, {"text": "What is vol ×?", "callback_data": "def:vol"}],
        [{"text": "What are the groups?", "callback_data": "def:groups"}],
        [{"text": PRIVATE_ASSISTANT_BUTTON, "url": deeplink.link(bot_username, "ch")}],
    ]}
