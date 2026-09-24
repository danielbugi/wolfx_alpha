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
from alerts import board as bd
from alerts import market_stats as ms
from alerts.digest_format import INDENT, _arrow, _link, headline
from alerts.market_card import SectorBar, Tile
from alerts.message_format import fmt_price
from alerts.texts import DEFINITIONS, DISCLAIMER_ONE_LINE

CAPTION_LIMIT = 1024
KINDS = ("board", "health", "sector", "macro", "gaps", "near_highs", "aligned", "base_rate", "recap", "promo", "news", "scoreboard", "disclaimer", "assistant", "earnings_today")


@dataclass
class Post:
    kind: str
    text: str                                    # the caption when there is an image, else the whole message
    image: Optional[bytes] = None
    button: bool = False                         # attach the neutral "Private assistant" link button
    disable_preview: bool = True

    def __post_init__(self):
        self.text = self.text.rstrip()


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
    board: Optional[Dict] = None                                  # board.compute() result (the momentum board), None when there is nothing to show
    week_number: int = 0
    notice_index: int = 0                                         # which variant of the rotating assistant post to show
    earnings_today: Optional[List[Dict]] = None                   # today's reporters in the covered universe: symbol, close, sector (any may be None)


def _sign_arrow(pct: float) -> str:
    return f"{'▲' if pct > 0 else '▼' if pct < 0 else '■'}{abs(pct):.1f}%"


def _rows_block(lines: List[str]) -> str:
    return "<blockquote>" + "\n".join(lines) + "</blockquote>"


# ------------------------------------------------------------------ P1 market health
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
    hook = headline(ctx.sp500_pct, ctx.up_n, ctx.down_n, ctx.universe_n)
    lines = [f"<b>{html.escape(hook, quote=False) if hook else 'Market health'}</b>"]
    sp = f" · S&amp;P 500 {_sign_arrow(ctx.sp500_pct)}" if ctx.sp500_pct is not None else ""
    lines.append(f"{ctx.session:%a %d %b}{sp}")
    ago = f"{h.ref_sessions} sessions ago"
    lines.append(f"Above 50-day average: {a50:.0f}%" + (f" ({ago}: {b50:.0f}%)" if b50 is not None else ""))
    lines.append(f"Above 200-day average: {a200:.0f}%" + (f" ({ago}: {b200:.0f}%)" if b200 is not None else ""))
    lines.append(f"52-week highs {h.new_highs:,} · lows {h.new_lows:,}")
    return Post("health", "\n".join(lines), cc.render_health_card(d))


# ------------------------------------------------------------------ P2 sector rotation
def post_sector(ctx: Ctx) -> Optional[Post]:
    if not ctx.sector_bars:
        return None
    bars = ctx.sector_bars
    best, worst = bars[0], bars[-1]
    up_n = sum(1 for b in bars if b[1] > 0)
    n = ctx.sector_sessions
    # the title and the two labels follow the facts: "rotation" only when money is not moving the same way everywhere
    if up_n == 0:
        title, hi, lo = f"Every sector is lower than {n} sessions ago.", "Smallest drop", "Largest drop"
    elif up_n == len(bars):
        title, hi, lo = f"Every sector is higher than {n} sessions ago.", "Largest gain", "Smallest gain"
    else:
        title, hi, lo = f"{up_n} of {len(bars)} sectors are higher than {n} sessions ago.", "Strongest", "Weakest"
    lines = [f"<b>{title}</b>", f"{ctx.session:%a %d %b} · median change of each sector's stocks",
             f"{hi}: {html.escape(best[0])} {_sign_arrow(best[1])}", f"{lo}: {html.escape(worst[0])} {_sign_arrow(worst[1])}"]
    png = cc.render_sector_card(ctx.session, ctx.sector_sessions, [SectorBar(n, v) for n, v, _ in bars], ctx.sector_unclassified, ctx.universe_n)
    return Post("sector", "\n".join(lines), png)


# ------------------------------------------------------------------ P3 macro strip
def post_macro(ctx: Ctx) -> Optional[Post]:
    tiles = [t for t in (ctx.macro_tiles or []) if t.value != "n/a"]          # a market with no value today is left out, never shown as "n/a"
    if not tiles:
        return None
    parts = [f"{t.label} unchanged" if t.direction == 0 else f"{t.label} {'▲' if t.direction > 0 else '▼'}{t.delta}" for t in tiles]
    lines = [f"<b>Beyond stocks</b> · {ctx.session:%a %d %b}", " · ".join(parts)]
    return Post("macro", "\n".join(lines), cc.render_macro_card(ctx.session, tiles))


# ------------------------------------------------------------------ P4 gaps and volume
def _expiry_note(session: date) -> Optional[str]:
    if ms.is_quarterly_expiry(session):
        return "Quarterly options-expiry day: volume runs high everywhere."
    if ms.is_monthly_options_expiry(session):
        return "Monthly options-expiry day: volume runs high everywhere."
    return None


def _gap_row(i: int, r: Dict) -> str:
    vol = f" · vol {r['rvol']:.1f}×" if r.get("rvol") is not None else ""
    return (f"{i}. <b>{_link(r['symbol'])}</b> {fmt_price(r['close'])} {_arrow(r['ret1_pct'])}\n"
            f"{INDENT}opened {_sign_arrow(r['gap_pct'])}{vol}")


def post_gaps(ctx: Ctx) -> Optional[Post]:
    if ctx.table is None:
        return None
    g = ms.gap_lists(ctx.table)
    if not g["n_pool"] or not (g["ups"] or g["downs"]):
        return None
    lines = [f"<b>{g['n_up']} gapped up {g['threshold']:.0f}%+. {g['n_down']} gapped down.</b>",
             f"{ctx.session:%a %d %b} · of {g['n_pool']:,} liquid stocks"]
    if g["ups"]:
        lines += ["", "<b>Largest gap-ups</b>", _rows_block([_gap_row(i, r) for i, r in enumerate(g["ups"], 1)])]
    if g["downs"]:
        lines += ["", "<b>Largest gap-downs</b>", _rows_block([_gap_row(i, r) for i, r in enumerate(g["downs"], 1)])]
    note = _expiry_note(ctx.session)
    if note:
        lines += ["", f"<i>{note}</i>"]
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
    lines = [f"<b>{n['n']} within {n['within_pct']:.0f}% of a 52-week high, on {n['min_rvol']:.0f}×+ volume.</b>",
             f"{ctx.session:%a %d %b} · top 5 by volume", "", _rows_block(body)]
    note = _expiry_note(ctx.session)
    if note:
        lines += ["", f"<i>{note}</i>"]
    return Post("near_highs", "\n".join(lines))


# ------------------------------------------------------------------ P6 aligned timeframes (counts only)
def post_aligned(ctx: Ctx) -> Optional[Post]:
    if ctx.table is None or not ctx.breakout_symbols:
        return None
    a = ms.aligned_breakouts(ctx.table, ctx.breakout_symbols)
    if a["n_priced"] == 0:
        return None
    w = a["within_pct"]
    lines = [f"<b>{a['n_breakouts']} broke out. {a['n_both']} are also near their 20-week and 52-week highs.</b>",
             f"{ctx.session:%a %d %b} · {a['n_week']} within {w:.0f}% of the 20-week high · {a['n_year']} within {w:.0f}% of the 52-week high",
             "", "The names are in the assistant."]
    return Post("aligned", "\n".join(lines), button=True)


# ------------------------------------------------------------------ earnings-today (own 11:00 Israel schedule -- see DATA_ML_MILESTONES.md M3)
def _earnings_row(i: int, r: Dict) -> str:
    bits = []
    if r.get("sector"):
        bits.append(html.escape(r["sector"]))
    if r.get("close") is not None:
        bits.append(fmt_price(r["close"]))
    if r.get("eps_estimate") is not None:
        bits.append(f"EPS est. {r['eps_estimate']:.2f}")
    detail = " · ".join(bits) if bits else "no additional facts on file"
    return f"{i}. <b>{_link(r['symbol'])}</b>\n{INDENT}{detail}"


def post_earnings_today(ctx: Ctx) -> Optional[Post]:
    """Facts only: no before/after-market timing -- the stored calendar only has the report DATE (see
    DATA_ML_MILESTONES.md M2's schema note), never a reason to trade. Silent (returns None) on a day
    with no reporters in the covered universe -- the trading-day gate that keeps this from firing on a
    weekend/holiday at all lives in the sender (send_earnings_today.py), not here; a real trading day
    can still legitimately have zero reporters, and that is not itself worth a post."""
    rows = ctx.earnings_today or []
    if not rows:
        return None
    n = len(rows)
    head = f"<b>{n} compan{'y' if n == 1 else 'ies'} in the covered universe report{'s' if n == 1 else ''} today.</b>"
    lines = [head, f"{ctx.session:%a %d %b}", ""]
    shown, rest = rows[:25], rows[25:]
    lines.append(_rows_block([_earnings_row(i, r) for i, r in enumerate(shown, 1)]))
    if rest:
        more_rows = [f"{25 + i}. <b>{_link(r['symbol'])}</b>" for i, r in enumerate(rest, 1)]
        lines += ["", f"<i>More: {26}–{n} · tap to expand</i>",
                  "<blockquote expandable>" + "\n".join(more_rows) + "</blockquote>"]
    return Post("earnings_today", "\n".join(lines))


# ------------------------------------------------------------------ P9 base rates
def post_base_rate(ctx: Ctx) -> Optional[Post]:
    b = ctx.base
    if not b or b.get("n", 0) < 1000:
        return None
    years = [y for y in b["by_year"] if y["n"] >= 1000]
    lines = [f"<b>{b['n']:,} breakouts since {b['first_year']}.</b>",
             f"{b['stopped'] * 100:.0f}% fell 2× ATR below the breakout price at some point.",
             f"{b['tp3'] * 100:.0f}% rose 6× ATR above it before that."]
    if len(years) >= 2:
        lo, hi = min(years, key=lambda y: y["stopped"]), max(years, key=lambda y: y["stopped"])
        lines.append(f"By year, the first figure ran from {lo['stopped'] * 100:.0f}% ({lo['year']}) to {hi['stopped'] * 100:.0f}% ({hi['year']}).")
    lines += ["", "The question is not what broke out. It is which ones, and why."]
    return Post("base_rate", "\n".join(lines))


# ------------------------------------------------------------------ P8 weekly recap
def post_recap(ctx: Ctx) -> Optional[Post]:
    r = ctx.recap
    if not r or len(r.get("days", [])) < 3:
        return None
    days = r["days"]
    first, last = days[0]["date"], days[-1]["date"]
    lines = [f"<b>Week in review</b> · {first:%d %b} – {last:%d %b}",
             "Breakouts: " + " · ".join(f"{d['date']:%a} {d['breakout']}" for d in days),
             "Near breakouts: " + " · ".join(f"{d['date']:%a} {d['near']}" for d in days)]
    if all(d.get("up_pct") is not None for d in days):
        lines.append("Stocks that rose: " + " · ".join(f"{d['date']:%a} {d['up_pct']:.0f}%" for d in days))
    if r.get("sectors"):
        best, worst = r["sectors"][0], r["sectors"][-1]
        lines.append(f"Sectors, {r['sector_sessions']} sessions: {html.escape(best[0])} {_sign_arrow(best[1])} led, "
                     f"{html.escape(worst[0])} {_sign_arrow(worst[1])} trailed.")
    if r.get("persistent"):
        names, seen = [], set()
        for s in r["persistent"]:                        # one company can be stored under two tickers (BRK.B / BRK/B): show it once
            key = s.replace("/", ".").replace("-", ".").upper()
            if key not in seen:
                seen.add(key)
                names.append(s)
        shown = ", ".join(f"<b>{_link(s)}</b>" for s in names[:10])
        more = f" +{len(names) - 10}" if len(names) > 10 else ""
        lines.append(f"Breakout list on {r['persist_min']}+ of {len(days)} days ({len(names)}): {shown}{more}")
    return Post("recap", "\n".join(lines))


# ------------------------------------------------------------------ P10 education and promotion (rotating)
PRIVATE = "Access on request · seats limited. Tap <b>Request access</b> under the daily post."
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
    return Post("promo", "\n".join(lines), button=p["assistant"])


# ------------------------------------------------------------------ P7 news on the movers
NEWS_TEXT_LIMIT = 3800                           # Telegram allows 4096; keep a margin for the footer


def post_news(ctx: Ctx) -> Optional[Post]:
    n = ctx.news
    if not n or not n.get("items"):
        return None
    lines = [f"<b>News on today's movers</b> · {ctx.session:%a %d %b}",
             "Headlines for stocks in today's lists, as published. We show the headline and its link only.", ""]
    foot = ["", "<i>Headlines via Alpaca's news feed; each link goes to its publisher.</i>"]
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
    lines += ["", "<i>A stock-day is one stock on one session's lists.</i>"]
    return Post("scoreboard", "\n".join(lines))


# ------------------------------------------------------------------ recurring notices (twice a day, sent by send_channel_notices.py)
def post_disclaimer(ctx: Ctx) -> Optional[Post]:
    """The general disclaimer notice. The full plain-language version lives in the pinned "Start here" post; this is the short reminder that points to it."""
    lines = ["<b>Educational data. Not investment advice.</b>",
             "It describes what already happened, not what happens next. Read the pinned message: how this channel works and what to keep in mind."]
    return Post("disclaimer", "\n".join(lines))


ASSISTANT_ACCESS = "Access on request · seats limited."
ASSISTANT_POSTS: List[str] = [
    "<b>The channel shows what moved. The assistant shows what moved for you.</b>\nYour stocks, followed from the day you add them.",
    "<b>Find any stock that fits your rules.</b>\nOne line: <code>/screen breakout vol&gt;3</code>. Every stock in the daily scan, filtered.",
    "<b>One message when the scan is ready.</b>\nThe stocks you follow, what changed, nothing else.",
    "<b>Type a ticker. Get the card.</b>\nThe facts, the lists it is in, headlines and a chart, in one tap.",
]


def post_assistant(ctx: Ctx) -> Optional[Post]:
    """The commercial post for the private assistant (four rotating variants, chosen by ctx.notice_index). Says exactly what access is today."""
    body = ASSISTANT_POSTS[ctx.notice_index % len(ASSISTANT_POSTS)]
    return Post("assistant", "\n".join([body, "", ASSISTANT_ACCESS]), button=True)


# ------------------------------------------------------------------ momentum board (daily)
PLACE_WORD = {1: "1st", 2: "2nd", 3: "3rd"}


def _board_detail(r: Dict, n_sessions: int) -> str:
    bits = []
    if r.get("at_day_high"):
        bits.append("closed at the top of its range")
    elif r.get("above_20d_high"):
        bits.append("closed above its prior 20-day high")
    bits.append(f"in the lists {r['listed_sessions']} of {n_sessions} sessions")
    return " · ".join(bits)


def post_board(ctx: Ctx) -> Optional[Post]:
    b = ctx.board
    if not b or not b.get("top"):
        return None
    n = b["n_sessions"]
    flat = max(0, b["n_measured"] - b["higher"] - b["lower"])          # the three numbers always add up to the group
    lines = [f"<b>Momentum board</b> · {b['session']:%a %d %b}",
             f"The {b['n_measured']:,} stocks the lists carried in the last {n} sessions: {b['higher']:,} up, {b['lower']:,} down"
             + (f", {flat:,} flat" if flat else "") + f" today; {b['at_day_high']:,} closed at the top of their day's range.", ""]
    for place, r in enumerate(b["top"], 1):
        lines.append(f"{PLACE_WORD[place]} <b>{_link(r['symbol'])}</b> {fmt_price(r['close'])} {_arrow(r['ret1_pct'])}")
        lines.append(f"{INDENT}{_board_detail(r, n)}")
    if b.get("more"):
        first = len(b["top"]) + 1
        rows = [f"{first + i}. <b>{_link(r['symbol'])}</b> {fmt_price(r['close'])} {_arrow(r['ret1_pct'])}" for i, r in enumerate(b["more"])]
        lines += ["", f"<i>More: ranks {first}–{first + len(rows) - 1} · tap to expand</i>", "<blockquote expandable>" + "\n".join(rows) + "</blockquote>"]
    lines += ["", "<i>Ranked by today's % change among the stocks that closed higher.</i>"]
    return Post("board", "\n".join(lines), cc.render_board_card(b))


# ------------------------------------------------------------------ registry
BUILDERS = {"disclaimer": post_disclaimer, "assistant": post_assistant, "board": post_board, "health": post_health, "sector": post_sector, "macro": post_macro, "gaps": post_gaps, "near_highs": post_near_highs,
            "aligned": post_aligned, "base_rate": post_base_rate, "recap": post_recap, "promo": post_promo, "news": post_news,
            "scoreboard": post_scoreboard, "earnings_today": post_earnings_today}


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
