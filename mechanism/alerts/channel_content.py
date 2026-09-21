# mechanism/alerts/channel_content.py
"""
The channel's extra posts (CHANNEL_CONTENT_REPORT_2026-09-21.md P1-P10): market health, sector rotation, macro strip, gaps and volume, near
52-week highs, aligned timeframes, base rates, weekly recap, and the rotating education / promotion posts.

Every builder is a PURE function: a `Ctx` of already-computed facts in, a `Post` (Telegram HTML text + optional PNG) out, or None when there
is nothing honest to say (a missing ingredient is never filled in). Loading the facts lives in send_channel_posts.py. Copy rules: educational
tone, facts only, no advice words, the disclaimer on every post, no performance claims - test_channel_content.py scans every text.
A channel post never imports the assistant's screens or tables (test_channel_tools.py enforces it).
"""
from __future__ import annotations

import html
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional, Sequence, Tuple

import pandas as pd

from alerts import channel_cards as cc
from alerts import market_stats as ms
from alerts.digest_format import INDENT, _arrow, _link
from alerts.market_card import SectorBar, Tile
from alerts.message_format import fmt_price
from alerts.texts import DEFINITIONS, DISCLAIMER_SHORT

CAPTION_LIMIT = 1024
KINDS = ("health", "sector", "macro", "gaps", "near_highs", "aligned", "base_rate", "recap", "promo", "news", "scoreboard")


@dataclass
class Post:
    kind: str
    text: str                                    # the caption when there is an image, else the whole message
    image: Optional[bytes] = None
    button: bool = False                         # attach the neutral "Private assistant" link button
    disable_preview: bool = True


@dataclass
class Ctx:
    session: date
    universe_n: int = 0
    up_n: int = 0
    down_n: int = 0
    counts: Dict[str, int] = field(default_factory=dict)          # digest counts: breakout / near_breakout
    health: Optional[ms.Health] = None
    sp500_pct: Optional[float] = None                             # S&P 500 change on the session, None when unknown
    table: Optional[pd.DataFrame] = None
    breakout_symbols: Sequence[str] = ()
    sector_bars: Optional[List[Tuple[str, float, int]]] = None
    sector_unclassified: int = 0
    sector_sessions: int = 20
    macro_tiles: Optional[List[Tile]] = None
    base: Optional[Dict] = None
    recap: Optional[Dict] = None
    news: Optional[Dict] = None
    scoreboard: Optional[Dict] = None
    week_number: int = 0


def _sign_arrow(pct: float) -> str:
    return f"{'▲' if pct > 0 else '▼' if pct < 0 else '■'}{abs(pct):.1f}%"


def _foot() -> str:
    return f"<i>{DISCLAIMER_SHORT}</i>"


def _rows_block(lines: List[str]) -> str:
    return "<blockquote>" + "\n".join(lines) + "</blockquote>"


# ------------------------------------------------------------------ P1 market health
def _index_vs_breadth(sp: Optional[float], up: int, down: int, universe_n: int) -> Optional[str]:
    total = up + down
    if sp is None or total <= 0 or universe_n <= 0:
        return None
    share = up / total * 100
    head = f"S&amp;P 500 {_sign_arrow(sp)} · {share:.0f}% of {universe_n:,} liquid stocks rose"
    if sp > 0 and share < 45:
        return f"Index up, most stocks down: {head}."
    if sp < 0 and share > 55:
        return f"Index down, most stocks up: {head}."
    return head + "."


def health_card_data(ctx: Ctx) -> Optional[cc.HealthCard]:
    h = ctx.health
    if h is None or len(h.above50) < 2:
        return None
    return cc.HealthCard(
        session=ctx.session, universe_n=ctx.universe_n, n50=h.n50, n200=h.n200, n_range=h.n_range,
        dates=[pd.Timestamp(x).date() for x in h.above50.index], above50=[float(v) for v in h.above50],
        above200=[float(v) for v in h.above200.reindex(h.above50.index)], ref_sessions=h.ref_sessions,
        new_highs=h.new_highs, new_lows=h.new_lows, breakouts=ctx.counts.get("breakout", 0),
        near_breakouts=ctx.counts.get("near_breakout", 0))


def post_health(ctx: Ctx) -> Optional[Post]:
    d = health_card_data(ctx)
    if d is None:
        return None
    h = ctx.health
    a50, a200 = float(h.above50.iloc[-1]), float(h.above200.iloc[-1])
    b50, b200 = ms.value_ago(h.above50, h.ref_sessions), ms.value_ago(h.above200, h.ref_sessions)
    lines = [f"<b>Market health</b> · {ctx.session:%a %d %b}"]
    iv = _index_vs_breadth(ctx.sp500_pct, ctx.up_n, ctx.down_n, ctx.universe_n)
    if iv:
        lines.append(iv)
    lines.append(f"Above their 50-day average: {a50:.0f}%" + (f" ({b50:.0f}% {h.ref_sessions} sessions ago)" if b50 is not None else "")
                 + f" · above 200-day: {a200:.0f}%" + (f" ({b200:.0f}%)" if b200 is not None else "") + ".")
    lines.append(f"New 52-week highs {h.new_highs:,} · lows {h.new_lows:,}.")
    lines.append(f"Breakouts {ctx.counts.get('breakout', 0):,} · near breakouts {ctx.counts.get('near_breakout', 0):,}.")
    lines += ["", _foot()]
    return Post("health", "\n".join(lines), cc.render_health_card(d))


# ------------------------------------------------------------------ P2 sector rotation
def post_sector(ctx: Ctx) -> Optional[Post]:
    if not ctx.sector_bars:
        return None
    bars = ctx.sector_bars
    best, worst = bars[0], bars[-1]
    up_n = sum(1 for b in bars if b[1] > 0)
    lines = [f"<b>Sector rotation</b> · {ctx.session:%a %d %b}",
             f"Median {ctx.sector_sessions}-session change: strongest {html.escape(best[0])} {_sign_arrow(best[1])}, "
             f"weakest {html.escape(worst[0])} {_sign_arrow(worst[1])}.",
             ("No sector is higher than" if up_n == 0 else f"All {len(bars)} sectors are higher than" if up_n == len(bars)
              else f"{up_n} of {len(bars)} sectors are higher than") + f" {ctx.sector_sessions} sessions ago.", "", _foot()]
    png = cc.render_sector_card(ctx.session, ctx.sector_sessions, [SectorBar(n, v) for n, v, _ in bars], ctx.sector_unclassified, ctx.universe_n)
    return Post("sector", "\n".join(lines), png)


# ------------------------------------------------------------------ P3 macro strip
def post_macro(ctx: Ctx) -> Optional[Post]:
    tiles = ctx.macro_tiles
    if not tiles or all(t.value == "n/a" for t in tiles):
        return None
    parts = []
    for t in tiles:
        if t.value == "n/a":
            parts.append(f"{t.label} n/a")
        elif t.direction == 0:
            parts.append(f"{t.label} unchanged")
        else:
            parts.append(f"{t.label} {'▲' if t.direction > 0 else '▼'}{t.delta}")
    lines = [f"<b>Beyond stocks</b> · {ctx.session:%a %d %b}", " · ".join(parts) + ".", "", _foot()]
    return Post("macro", "\n".join(lines), cc.render_macro_card(ctx.session, tiles))


# ------------------------------------------------------------------ P4 gaps and volume
def _expiry_note(session: date) -> Optional[str]:
    if ms.is_quarterly_expiry(session):
        return "Quarterly options-expiry day: volume runs above normal across the board, so every vol × figure today is inflated."
    if ms.is_monthly_options_expiry(session):
        return "Monthly options-expiry day: volume runs above normal across the board, so vol × figures are inflated today."
    return None


def _gap_row(i: int, r: Dict) -> str:
    vol = f" · vol {r['rvol']:.1f}×" if r.get("rvol") is not None else ""
    return (f"{i}. <b>{_link(r['symbol'])}</b> {fmt_price(r['close'])} {_arrow(r['ret1_pct'])}\n"
            f"{INDENT}opened {r['gap_pct']:+.1f}%{vol}")


def post_gaps(ctx: Ctx) -> Optional[Post]:
    if ctx.table is None:
        return None
    g = ms.gap_lists(ctx.table)
    if not g["n_pool"] or not (g["ups"] or g["downs"]):
        return None
    lines = [f"<b>Gaps and volume</b> · {ctx.session:%a %d %b}",
             f"Of {g['n_pool']:,} liquid stocks, {g['n_up']} opened {g['threshold']:.0f}%+ above the prior close and {g['n_down']} opened "
             f"{g['threshold']:.0f}%+ below it."]
    if g["ups"]:
        lines += ["", "<b>Largest gap-ups</b>", _rows_block([_gap_row(i, r) for i, r in enumerate(g["ups"], 1)])]
    if g["downs"]:
        lines += ["", "<b>Largest gap-downs</b>", _rows_block([_gap_row(i, r) for i, r in enumerate(g["downs"], 1)])]
    note = _expiry_note(ctx.session)
    if note:
        lines += ["", f"<i>{note}</i>"]
    lines += ["", "<i>Gap = the open against the prior close. " + html.escape(DEFINITIONS["vol"], quote=False) + "</i>", "", _foot()]
    return Post("gaps", "\n".join(lines))


# ------------------------------------------------------------------ P5 near 52-week highs with volume
def post_near_highs(ctx: Ctx) -> Optional[Post]:
    if ctx.table is None:
        return None
    n = ms.near_high_list(ctx.table)
    if not n["rows"]:
        return None
    body = []
    for i, r in enumerate(n["rows"], 1):
        below = f"{r['below_hi252_pct']:.1f}% below its 52-week high"
        body.append(f"{i}. <b>{_link(r['symbol'])}</b> {fmt_price(r['close'])} {_arrow(r['ret1_pct'])}\n{INDENT}{below} · vol {r['rvol']:.1f}×")
    lines = [f"<b>Near 52-week highs, on heavy volume</b> · {ctx.session:%a %d %b}",
             f"{n['n']} liquid stocks closed within {n['within_pct']:.0f}% of their 52-week high with volume at least {n['min_rvol']:.0f}× normal. "
             "Top 5 by volume:", "", _rows_block(body)]
    note = _expiry_note(ctx.session)
    if note:
        lines += ["", f"<i>{note}</i>"]
    lines += ["", "<i>" + html.escape(DEFINITIONS["vol"], quote=False) + "</i>", "", _foot()]
    return Post("near_highs", "\n".join(lines))


# ------------------------------------------------------------------ P6 aligned timeframes (counts only)
def post_aligned(ctx: Ctx) -> Optional[Post]:
    if ctx.table is None or not ctx.breakout_symbols:
        return None
    a = ms.aligned_breakouts(ctx.table, ctx.breakout_symbols)
    if a["n_priced"] == 0:
        return None
    lines = [f"<b>Breakouts on longer timeframes</b> · {ctx.session:%a %d %b}",
             f"{a['n_breakouts']} stocks closed above their 20-day high today. Of them, {a['n_week']} also closed within {a['within_pct']:.0f}% of their "
             f"20-week high and {a['n_year']} within {a['within_pct']:.0f}% of their 52-week high ({a['n_both']} both)."]
    if a["n_short_week"] or a["n_short_year"]:
        lines.append(f"Stocks without enough history for a measure are not counted in it ({a['n_short_week']} for 20 weeks, {a['n_short_year']} for 52 weeks).")
    lines += ["", "Members of the private assistant (free beta, by invitation) can see which stocks they are.", "",
              "<i>20-week high = highest high of the last 100 sessions; 52-week high = last 252 sessions.</i>", "", _foot()]
    return Post("aligned", "\n".join(lines), button=True)


# ------------------------------------------------------------------ P9 base rates
def post_base_rate(ctx: Ctx) -> Optional[Post]:
    b = ctx.base
    if not b or b.get("n", 0) < 1000:
        return None
    years = [y for y in b["by_year"] if y["n"] >= 1000]
    lines = [f"<b>Base rates</b> · what happened after a 20-day-high breakout",
             f"All long breakouts in liquid US stocks since {b['first_year']}: {b['n']:,} cases, followed for 20 sessions each.",
             f"• In {b['stopped'] * 100:.0f}% price fell to a level 2× ATR below the breakout price at some point.",
             f"• In {b['tp3'] * 100:.0f}% price rose to a level 6× ATR above it before that happened."]
    if len(years) >= 2:
        lo, hi = min(years, key=lambda y: y["stopped"]), max(years, key=lambda y: y["stopped"])
        lines.append(f"• By calendar year the first figure ranged from {lo['stopped'] * 100:.0f}% ({lo['year']}) to {hi['stopped'] * 100:.0f}% ({hi['year']}).")
    lines += ["", "Most breakouts do not run far, a minority do, and how often changes with the market.", "",
              "<i>ATR = average true range. Uses today's index members, so stocks that dropped out are missing (survivor bias). "
              "Descriptive history, not a forecast.</i>", "", _foot()]
    return Post("base_rate", "\n".join(lines))


# ------------------------------------------------------------------ P8 weekly recap
def post_recap(ctx: Ctx) -> Optional[Post]:
    r = ctx.recap
    if not r or len(r.get("days", [])) < 3:
        return None
    days = r["days"]
    first, last = days[0]["date"], days[-1]["date"]
    lines = [f"<b>Week in review</b> · {first:%d %b} – {last:%d %b}",
             "Breakouts by day: " + " · ".join(f"{d['date']:%a} {d['breakout']}" for d in days),
             "Near breakouts: " + " · ".join(f"{d['date']:%a} {d['near']}" for d in days)]
    if all(d.get("up_pct") is not None for d in days):
        lines.append("Share of stocks that rose: " + " · ".join(f"{d['date']:%a} {d['up_pct']:.0f}%" for d in days))
    if r.get("sectors"):
        best, worst = r["sectors"][0], r["sectors"][-1]
        lines.append(f"Median {r['sector_sessions']}-session sector change: strongest {html.escape(best[0])} {_sign_arrow(best[1])}, "
                     f"weakest {html.escape(worst[0])} {_sign_arrow(worst[1])}.")
    if r.get("persistent"):
        names = ", ".join(f"<b>{_link(s)}</b>" for s in r["persistent"][:12])
        lines.append(f"In the breakout group on {r['persist_min']}+ of {len(days)} sessions ({len(r['persistent'])}): {names}")
    lines += ["", _foot()]
    return Post("recap", "\n".join(lines))


# ------------------------------------------------------------------ P10 education and promotion (rotating)
PRIVATE = "Free during the beta, by invitation — tap <b>Private assistant</b> under the daily post to request access."
PROMOS: List[Dict] = [
    {"key": "atr", "assistant": False, "text":
        "<b>What is ATR?</b>\nATR (average true range) is a stock's average daily price range over 14 days. Two stocks can both move $1 in a day, "
        "yet for one that is a quiet day and for the other an unusual one. \"Range 3.0× ATR\" in the lists means today's range was three times "
        "that stock's own average — a way to compare movement across stocks of any price."},
    {"key": "volx", "assistant": False, "text":
        "<b>How to read vol ×</b>\n\"vol 3.1×\" is today's trading volume divided by the median daily volume of the previous 50 sessions. "
        "3.1× means about three times the usual number of shares changed hands. On options-expiry Fridays nearly every stock shows a high "
        "figure, so we say so in the post."},
    {"key": "groups", "assistant": False, "text":
        "<b>The two groups</b>\n<b>Breakout</b> — closed above the highest high of the prior 20 sessions.\n<b>Near breakout</b> — within 3% below "
        "that high, not through it yet.\nA ★ means the stock is in more than one of the day's lists (top gainers, top ATR, top volume). "
        "The lists describe what happened; they do not say what happens next."},
    {"key": "card", "assistant": False, "text":
        "<b>Reading the market card</b>\nThe six tiles are the S&amp;P 500, Nasdaq, Russell 2000, Dow, the VIX and the 10-year yield, each with "
        "its last 60 sessions. The bar shows how many of the liquid stocks rose and fell, and the sector bars are the average 1-day change of "
        "the stocks in each sector. A triangle and a number always go with the colour."},
    {"key": "breadth", "assistant": False, "text":
        "<b>What is market breadth?</b>\nAn index can rise while most stocks fall, because a few very large companies weigh heavily. Breadth "
        "counts stocks instead: how many rose, how many trade above their 50-day and 200-day averages, how many made new 52-week highs or lows. "
        "Our market-health post shows these."},
    {"key": "track", "assistant": True, "text":
        "<b>Follow a stock from the day you add it</b>\nIn the First Light private assistant, add a stock to your watchlist or portfolio and it "
        "shows the change since that day, with the price you gave (or the last close), the day you added it and the number of sessions "
        "since. Nothing is sent to anyone else."},
    {"key": "newschart", "assistant": True, "text":
        "<b>News and a chart for every stock</b>\nOpen any stock from today's lists in the private assistant and see its facts, the lists it is "
        "in, recent headlines with links, and a candle chart with volume and the prior 20-day high."},
]


def promo_for(week_number: int) -> Dict:
    return PROMOS[week_number % len(PROMOS)]


def post_promo(ctx: Ctx) -> Optional[Post]:
    p = promo_for(ctx.week_number)
    lines = [p["text"]]
    if p["assistant"]:
        lines += ["", PRIVATE]
    lines += ["", _foot()]
    return Post("promo", "\n".join(lines), button=p["assistant"])


# ------------------------------------------------------------------ P7 news on the movers
NEWS_TEXT_LIMIT = 3800                           # Telegram allows 4096; keep a margin for the footer


def post_news(ctx: Ctx) -> Optional[Post]:
    n = ctx.news
    if not n or not n.get("items"):
        return None
    lines = [f"<b>News on today's movers</b> · {ctx.session:%a %d %b}",
             "Headlines for stocks in today's lists, as published. We show the headline and its link only.", ""]
    foot = ["", "<i>Headlines come from Alpaca's news feed and link to their publishers. We do not write or edit them; headlines that read as "
            "ratings or price calls are left out.</i>", "", _foot()]
    size = sum(len(x) + 1 for x in lines + foot)
    shown = 0
    for m in n["movers"]:
        heads = n["items"].get(m["symbol"])
        if not heads:
            continue
        block = [f"<b>{html.escape(m['symbol'])}</b> {_arrow(m['ret1_pct'])} · {html.escape(m['group'])}"]
        for h in heads:
            src = f"{h['source']} · " if h.get("source") else ""
            block.append(f"• <a href=\"{html.escape(h['url'], quote=True)}\">{html.escape(h['headline'], quote=False)}</a> ({html.escape(src, quote=False)}{h['published_at']:%d %b})")
        text = "\n".join(block)
        if size + len(text) + 2 > NEWS_TEXT_LIMIT:
            break
        lines += [text, ""]
        size += len(text) + 2
        shown += 1
    if not shown:
        return None
    return Post("news", "\n".join(lines + foot[1:]))                 # `lines` already ends with a blank line, so foot[0] (blank) is skipped


# ------------------------------------------------------------------ D12 list scoreboard
def _signed(x: float) -> str:
    """+0.4 / -1.3 / 0.0 (a value that rounds to zero has no sign, so there is never a "-0.0")."""
    return "0.0" if round(x, 1) == 0 else f"{x:+.1f}"


def post_scoreboard(ctx: Ctx) -> Optional[Post]:
    s = ctx.scoreboard
    if not s or not any(s["groups"].get(g) for g in ("breakout", "near_breakout")):
        return None
    lines = ["<b>List scoreboard</b> · what happened after the lists",
             f"{s['sessions']} sessions of lists, {s['first']:%d %b} – {s['last']:%d %b}. A stock counts once per session (a stock-day)."]
    for g, title in (("breakout", "Breakout lists"), ("near_breakout", "Near breakout lists")):
        horizons = s["groups"].get(g) or {}
        if not horizons:
            continue
        lines += ["", f"<b>{title}</b>"]
        for k in sorted(horizons):
            h = horizons[k]
            u = h["universe"]
            lines.append(f"• {k} session{'s' if k > 1 else ''} later: {h['up_share']:.0f}% higher, median {_signed(h['median'])}% ({h['n']:,} stock-days). "
                         f"All liquid stocks over the same sessions: {u['up_share']:.0f}% higher, median {_signed(u['median'])}%.")
    lines += ["", "<i>Consecutive days of the same stock overlap, so these are descriptive counts, not independent tests. Lists that came earlier say "
              "nothing about the next ones. Uses today's index members and today's adjusted prices.</i>", "", _foot()]
    return Post("scoreboard", "\n".join(lines))


# ------------------------------------------------------------------ registry
BUILDERS = {"health": post_health, "sector": post_sector, "macro": post_macro, "gaps": post_gaps, "near_highs": post_near_highs,
            "aligned": post_aligned, "base_rate": post_base_rate, "recap": post_recap, "promo": post_promo, "news": post_news,
            "scoreboard": post_scoreboard}


def build_post(kind: str, ctx: Ctx) -> Optional[Post]:
    fn = BUILDERS.get(kind)
    return fn(ctx) if fn else None


# the weekday rotation (0 = Monday). Two options per day alternate by ISO week; the first is the default when the second is unavailable.
ROTATION = {0: ("sector", "macro"), 1: ("gaps", "news"), 2: ("health", "health"), 3: ("near_highs", "aligned"), 4: ("promo", "promo")}


def pick_kinds(session: date, news_enabled: bool = False, scoreboard_enabled: bool = False) -> List[str]:
    """Kinds to try for a session, best first (the first that yields a post is sent). One extra post per session at most."""
    d = pd.Timestamp(session)
    week = d.isocalendar().week
    first_of_month = d.day <= 7
    options = ROTATION.get(d.weekday())
    if options is None:                                       # Saturday / Sunday: only the recap runs, on request
        return ["recap"]
    chosen = options[week % 2]
    if chosen == "news" and not news_enabled:
        chosen = options[0]
    order: List[str] = []
    if first_of_month and d.weekday() == 3:                   # the first Thursday of the month is the base-rate card
        order.append("base_rate")
    if first_of_month and d.weekday() == 4 and scoreboard_enabled:   # the first Friday: the list scoreboard (opt-in: its numbers may be unflattering)
        order.append("scoreboard")
    order += [chosen, "health", "macro", "promo"]
    seen, out = set(), []
    for k in order:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out
