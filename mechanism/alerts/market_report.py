# mechanism/alerts/market_report.py
"""
The channel's ONE daily market report (owner decision 2026-10-21): every section the separate post-market posts carried --
the day's hook and breadth (market health), the counts and the stocks in 2+ lists, the Breakout and Near-breakout lists
(gainers / range vs ATR / volume), the day's top gainers, the momentum board, and the next session's reporting companies
(formerly the 10:00 earnings post) -- in a single Telegram message under the market-card photo.

Pure formatting, no I/O: the caller (send_daily_digest.py --report) computes every number with the existing, tested code
and passes it in. Telegram allows 4096 parsed characters; the report is built at full depth and, if it would not fit,
the collapsed (tap-to-expand) parts are shortened step by step -- the visible lines never change. Facts only, the same
wording rules as every other channel post (test_channel_content.py's banned words apply).
"""
from __future__ import annotations

import html
from datetime import date
from typing import Dict, List, Optional, Sequence

from alerts.digest_builder import LISTS, LONG_CATEGORIES
from alerts.digest_format import GROUPS, _arrow, _link
from alerts.telegram_client import text_length

REPORT_LIMIT = 3900                  # parsed characters (Telegram allows 4096; a margin for entities and the footer)
SHOWN = 3                            # names shown inline per list; the rest of the list sits in a collapsed quote
DEPTHS = (10, 8, 6, 5, 4, 3)         # how deep each list goes, tried from the top until the message fits
EARNINGS_SHOWN = 10                  # reporting companies named inline (largest first); the rest collapsed
# The once-a-day disclaimer line now lives here (the separate notice is off, owner decision 2026-10-21), in the short form
# CHANNEL_VOICE_AND_COPY.md item 15 specifies; the full disclaimers stay in the pinned message.
FOOTER = ("<i>Educational data. Not advice. Read the pinned message.</i>\n"
          "Tap a ticker for its chart · Access on request · seats limited")

LIST_LABEL = {"gainers": "Gainers", "atr": "Range", "volume": "Volume"}
GROUP_RULE = {"breakout": "closed above their 20-day high", "near_breakout": "within 3% below their 20-day high"}


def _metric(lst: str, r: Dict) -> str:
    if lst == "atr" and r.get("range_atr") is not None:
        return f"{r['range_atr']:.1f}× ATR"
    if lst == "volume" and r.get("rvol") is not None:
        return f"vol {r['rvol']:.1f}×"
    return ""


def _name(r: Dict) -> str:
    return f"<b>{_link(r['symbol'])}</b> {_arrow(r['ret1_pct'])}"


def _inline(rows: Sequence[Dict], lst: Optional[str] = None) -> str:
    parts = []
    for r in rows:
        m = _metric(lst, r) if lst else ""
        parts.append(_name(r) + (f" {m}" if m else ""))
    return " · ".join(parts)


def _collapsed(rows: Sequence[Dict], first: int, lst: Optional[str] = None) -> str:
    if not rows:
        return ""
    lines = []
    for i, r in enumerate(rows, first):
        m = _metric(lst, r) if lst else ""
        lines.append(f"{i}. {_name(r)}" + (f" · {m}" if m else ""))
    return "<blockquote expandable>" + "\n".join(lines) + "</blockquote>"


def _list_lines(label: str, rows: Sequence[Dict], depth: int, lst: Optional[str] = None) -> List[str]:
    rows = list(rows)[:depth]
    if not rows:
        return [f"{label}: none today"]
    out = [f"{label}: {_inline(rows[:SHOWN], lst)}"]
    more = _collapsed(rows[SHOWN:], SHOWN + 1, lst)
    if more:
        out.append(more)
    return out


def _group_section(cat: str, digest: Dict, depth: int) -> List[str]:
    board = digest["boards"][cat]
    title = GROUPS[cat][0]
    out = [f"<b>{title} · {digest['counts'][cat]:,}</b> <i>{GROUP_RULE[cat]}</i>"]
    for lst in LISTS:
        out += _list_lines(LIST_LABEL[lst], board[lst], depth, lst)
    return out


def _star_section(digest: Dict) -> List[str]:
    out = []
    for cat in LONG_CATEGORIES:
        board = digest["boards"][cat]
        multi = board["multi"]
        if not multi:
            continue
        by_symbol = {r["symbol"]: r for lst in LISTS for r in board[lst]}
        names = [by_symbol[s] for s in multi if s in by_symbol]
        names.sort(key=lambda r: (-r["ret1_pct"], r["symbol"]))
        out.append(f"{GROUPS[cat][0].capitalize()}: {_inline(names)}")
    return (["<b>★ Top 5 in 2+ lists</b>"] + out) if out else []


def _board_section(board: Optional[Dict], depth: int) -> List[str]:
    if not board or not board.get("top"):
        return []
    rows = list(board["top"]) + list(board.get("more") or [])
    n = board["n_sessions"]
    head = (f"<b>MOMENTUM BOARD</b> <i>stocks in the lists in the last {n} sessions: {board['higher']:,} up, "
            f"{board['lower']:,} down today</i>")
    return [head] + _list_lines("Top", rows, min(depth, 8))


def _earnings_section(day: Optional[date], rows: Sequence[Dict], depth_factor: float = 1.0) -> List[str]:
    if day is None or not rows:
        return []
    n = len(rows)
    shown = rows[:EARNINGS_SHOWN]
    rest = rows[EARNINGS_SHOWN:]
    out = [f"<b>REPORTING {day:%a %d %b}</b> · {n:,} compan{'y' if n == 1 else 'ies'}",
           " · ".join(f"<b>{_link(r['symbol'])}</b>" for r in shown)]
    keep = int(len(rest) * depth_factor)
    if keep:
        out.append("<blockquote expandable>" + " · ".join(_link(r["symbol"]) for r in rest[:keep])
                   + (f" · +{len(rest) - keep:,} more" if keep < len(rest) else "") + "</blockquote>")
    elif rest:
        out.append(f"+{len(rest):,} more")
    return out


def report_caption(session: date, digest: Dict, hook: Optional[str]) -> str:
    """The market-card photo's caption: the notification line (the hook) and the counts. The report follows below it."""
    b, n = digest["counts"]["breakout"], digest["counts"]["near_breakout"]
    first = f"<b>{html.escape(hook, quote=False)}</b>" if hook else "<b>First Light</b>"
    return "\n".join([first, f"{session:%a %d %b} · {b:,} broke out · {n:,} within 3% of it", "Full report below."])


def format_report(session: date, digest: Dict, hook: Optional[str], *, sp500_pct: Optional[float] = None,
                  health_lines: Sequence[str] = (), top_gainers: Sequence[Dict] = (), board: Optional[Dict] = None,
                  earnings_day: Optional[date] = None, earnings_rows: Sequence[Dict] = (), universe_n: int = 0,
                  limit: int = REPORT_LIMIT) -> str:
    """The whole report as ONE message within `limit` parsed characters."""
    def build(depth: int, earn_factor: float) -> str:
        lines = [f"<b>{html.escape(hook, quote=False)}</b>" if hook else "<b>Market report</b>"]
        sp = f" · S&amp;P 500 {_arrow(sp500_pct)}" if sp500_pct is not None else ""
        lines.append(f"{session:%a %d %b}{sp}")
        lines += list(health_lines)
        lines.append(f"{digest['counts']['breakout']:,} broke out · {digest['counts']['near_breakout']:,} within 3% of it")
        stars = _star_section(digest)
        if stars:
            lines += [""] + stars
        for cat in LONG_CATEGORIES:
            lines += [""] + _group_section(cat, digest, depth)
        if top_gainers:
            scope = f"of {universe_n:,} liquid stocks" if universe_n else "all liquid stocks"
            lines += ["", f"<b>TOP GAINERS</b> <i>{scope}</i>"] + _list_lines("Top", top_gainers, depth)
        b = _board_section(board, depth)
        if b:
            lines += [""] + b
        e = _earnings_section(earnings_day, earnings_rows, earn_factor)
        if e:
            lines += [""] + e
        lines += ["", FOOTER]
        return "\n".join(lines)

    text = build(DEPTHS[0], 1.0)
    for earn_factor in (1.0, 0.5, 0.0):
        for depth in DEPTHS:
            text = build(depth, earn_factor)
            if text_length(text) <= limit:
                return text
    return text                                   # still too long at minimum depth: the caller refuses to send it
