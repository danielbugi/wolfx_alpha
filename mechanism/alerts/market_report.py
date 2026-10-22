# mechanism/alerts/market_report.py
"""
The channel's ONE daily post: "First Light ⚡ Market Daily", a public market brief (owner decisions 2026-10-21/22).

It describes the US market of the session with market-wide facts only -- index moves, the fear gauge (VIX), the 10-year
yield, sectors, two rows of the day's breakout stocks (top 5 by % gain, top 5 by volume surge; names and numbers only), gold / oil / the dollar / Bitcoin, and the largest
companies reporting next session. It never shows anything about how First Light works: no breakout or list counts, no
universe size, no rankings of our own, no method words. Breadth is said in words ("most stocks rose"), never as a count.

The summary is written by RULES from the numbers (no AI, no invented facts): a headline picked from a fixed priority list,
then two or three sentences. One fixed emoji per section every day, green/red dots for direction -- a recognisable rhythm.

Pure formatting, no I/O: send_daily_digest.py --report gathers the numbers and passes them in. The channel's wording rules
apply (test_channel_content.py's banned words: e.g. never "sell-off", never "earnings").
"""
from __future__ import annotations

import html
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional, Sequence, Tuple

from alerts.digest_format import _link
from alerts.telegram_client import text_length

BRAND = "First Light ⚡ Market Daily"
REPORT_LIMIT = 3900
HASHTAGS = "#StockMarket #SP500 #Nasdaq #Stocks"
REPORTING_N = 8                       # largest companies reporting next session, named in the post
FOOTER = "<i>Educational data. Not advice. Read the pinned message.</i>"
UP, DOWN, FLAT = "🟢", "🔴", "⚪"

INDEX_ROWS = (("^GSPC", "S&amp;P 500"), ("^IXIC", "Nasdaq"), ("^DJI", "Dow"), ("^RUT", "Russell 2000"))   # HTML-ready labels
SHORT_SECTOR = {"Technology": "Tech", "Communication Services": "Communication", "Consumer Cyclical": "Consumer Cyclical",
                "Consumer Defensive": "Consumer Staples", "Financial Services": "Financials", "Basic Materials": "Materials"}


@dataclass
class Brief:
    session: date
    index_pct: Dict[str, Optional[float]] = field(default_factory=dict)     # ^GSPC/^IXIC/^DJI/^RUT -> % change (None = no bar)
    vix: Optional[Tuple[float, float]] = None                               # (level, % change)
    tnx: Optional[Tuple[float, float]] = None                               # (yield %, change in bps)
    macro_pct: Dict[str, Optional[float]] = field(default_factory=dict)     # gold / oil / dollar / btc -> % change
    up_share: Optional[float] = None                                        # % of US stocks that rose (said in words only)
    sectors: Sequence[Tuple[str, float]] = ()                               # (sector, avg % move) best -> worst
    gainers: Sequence[Tuple[str, float]] = ()                               # breakout stocks: (symbol, % change), biggest gain first
    volume: Sequence[Tuple[str, float, float]] = ()                         # breakout stocks: (symbol, % change, volume x usual), biggest surge first
    reporting_day: Optional[date] = None
    reporting: Sequence[str] = ()                                           # symbols, largest first


def _pct(x: float, nd: int = 1) -> str:
    if round(x, nd) == 0:                         # never "−0.0%": a move that rounds to zero is shown as flat
        return f"{0:.{nd}f}%"
    return f"{'+' if x > 0 else '−' if x < 0 else ''}{abs(x):.{nd}f}%"


def _dot(x: Optional[float]) -> str:
    if x is None or round(x, 2) == 0:
        return FLAT
    return UP if x > 0 else DOWN


def _sector(name: str) -> str:
    return html.escape(SHORT_SECTOR.get(name, name), quote=False)


def headline(b: Brief) -> Tuple[str, str]:
    """(emoji, words): the day's headline, the first rule that matches wins. Facts only, no forecast."""
    sp, nq, dj, rut = (b.index_pct.get(k) for k in ("^GSPC", "^IXIC", "^DJI", "^RUT"))
    vix_chg = b.vix[1] if b.vix else None
    top = b.sectors[0][0] if b.sectors else None
    if sp is None:
        return "📊", "US market close"
    if sp <= -1.0:
        return "🩸", "Heavy red day on Wall Street"
    if sp >= 1.0:
        return "🚀", "Big green day on Wall Street"
    if vix_chg is not None and vix_chg >= 10:
        return "😨", "Fear gauge jumps"
    if all(v is not None and v >= 0.3 for v in (sp, nq, dj, rut)) and (b.up_share or 0) >= 60:
        return "🟢", "Green across the board"
    if all(v is not None and v <= -0.3 for v in (sp, nq, dj, rut)) and b.up_share is not None and b.up_share <= 40:
        return "🔴", "Red across the board"
    if nq is not None and nq > 0 and nq - sp >= 0.4:
        return "🔥", "Nasdaq leads, tech rips" if top == "Technology" else "Nasdaq leads the way"
    if rut is not None and rut - sp >= 0.5:
        return "🐂", "Small caps run"
    if rut is not None and sp - rut >= 0.8:
        return "🧊", "Small caps lag"
    if vix_chg is not None and vix_chg <= -8 and sp > 0:
        return "😌", "Fear fades, stocks climb"
    if abs(sp) < 0.15:
        return "😐", "Flat close"
    return ("📈", "Stocks edge higher") if sp > 0 else ("📉", "Stocks slip")


def summary(b: Brief) -> List[str]:
    """Two or three plain sentences from the numbers."""
    out: List[str] = []
    named = [(label, b.index_pct.get(sym)) for sym, label in INDEX_ROWS if b.index_pct.get(sym) is not None]
    if named:
        best = max(named, key=lambda kv: kv[1])
        worst = min(named, key=lambda kv: kv[1])
        if best[0] != worst[0] and best[1] - worst[1] >= 0.3:
            out.append(f"{best[0]} led ({_pct(best[1])}) while {worst[0]} trailed ({_pct(worst[1])}).")
        else:
            out.append(f"The major indices moved together, the S&amp;P 500 {_pct(b.index_pct.get('^GSPC') or 0.0)}.")
    if b.up_share is not None:
        if b.up_share >= 60:
            out.append("Most US stocks rose.")
        elif b.up_share <= 40:
            out.append("Most US stocks fell.")
        else:
            out.append("Stocks were split, roughly half up and half down.")
    if len(b.sectors) >= 2:
        lead, lag = b.sectors[0], b.sectors[-1]
        if lead[1] > 0 > lag[1]:
            out.append(f"{_sector(lead[0])} led ({_pct(lead[1])}); {_sector(lag[0])} lagged ({_pct(lag[1])}).")
        elif lead[1] > 0:
            out.append(f"Every sector closed higher, {_sector(lead[0])} the strongest ({_pct(lead[1])}).")
        else:
            out.append(f"Every sector closed lower, {_sector(lag[0])} the weakest ({_pct(lag[1])}).")
    return out


def _movers(rows: Sequence[Tuple[str, float]]) -> str:
    return " · ".join(f"<b>{_link(s)}</b> {_pct(p)}" for s, p in rows)


def format_report(b: Brief, limit: int = REPORT_LIMIT) -> str:
    emoji, words = headline(b)
    lines = [f"{emoji} <b>{html.escape(words.upper(), quote=False)}</b>", f"{b.session:%a %d %b} · US close", ""]
    lines += [" ".join(summary(b)), ""]

    idx = [(label, b.index_pct.get(sym)) for sym, label in INDEX_ROWS]
    if any(v is not None for _, v in idx):
        lines.append("📊 <b>INDICES</b>")
        lines += [f"{_dot(v)} {label} {_pct(v, 2)}" for label, v in idx if v is not None]
        extra = []
        if b.vix:
            extra.append(f"😨 VIX {b.vix[0]:.2f} ({_pct(b.vix[1])})")
        if b.tnx:
            extra.append(f"🏦 10Y {b.tnx[0]:.2f}% ({'+' if b.tnx[1] >= 0 else '−'}{abs(b.tnx[1]):.1f} bps)")
        if extra:
            lines.append(" · ".join(extra))
        lines.append("")

    if b.sectors:
        ups = [s for s in b.sectors if s[1] > 0][:3]
        downs = [s for s in reversed(b.sectors) if s[1] < 0][:3]
        lines.append("🏭 <b>SECTORS</b>")
        if ups:
            lines.append(f"{UP} " + " · ".join(f"{_sector(n)} {_pct(p)}" for n, p in ups))
        if downs:
            lines.append(f"{DOWN} " + " · ".join(f"{_sector(n)} {_pct(p)}" for n, p in downs))
        lines.append("")

    if b.gainers:
        lines += ["🚀 <b>TOP GAINERS</b>", _movers(b.gainers), ""]
    if b.volume:
        lines += ["🔊 <b>VOLUME SURGE</b>",
                  " · ".join(f"<b>{_link(s)}</b> {x:.1f}× ({_pct(p)})" for s, p, x in b.volume)]
        lines.append("")

    labels = (("oil", "🛢 Oil"), ("gold", "🥇 Gold"), ("dollar", "💵 Dollar"), ("btc", "₿ Bitcoin"))
    macro = [f"{lab} {_pct(b.macro_pct[k])}" for k, lab in labels if b.macro_pct.get(k) is not None]
    if macro:
        lines += ["🌍 <b>MACRO &amp; CRYPTO</b>", " · ".join(macro), ""]

    if b.reporting_day and b.reporting:
        lines += [f"📅 <b>REPORTING {b.reporting_day:%a %d %b}</b>",
                  " · ".join(f"<b>{_link(s)}</b>" for s in b.reporting), ""]

    lines += [f"<b>{html.escape(BRAND, quote=False)}</b>", FOOTER, HASHTAGS]
    text = "\n".join(lines)
    if text_length(text) > limit and len(b.reporting) > 3:          # far too long is not expected; trim the optional row first
        return format_report(Brief(**{**b.__dict__, "reporting": list(b.reporting)[:-1]}), limit)
    return text


def caption(b: Brief) -> str:
    """The card photo's caption: the headline (what the notification shows) and the two main indices."""
    emoji, words = headline(b)
    sp, nq = b.index_pct.get("^GSPC"), b.index_pct.get("^IXIC")
    parts = [f"{b.session:%a %d %b}"]
    if sp is not None:
        parts.append(f"S&amp;P 500 {_pct(sp, 2)}")
    if nq is not None:
        parts.append(f"Nasdaq {_pct(nq, 2)}")
    return "\n".join([f"{emoji} <b>{html.escape(words, quote=False)}</b>", " · ".join(parts), "Full brief below 👇"])


def card_headline(b: Brief) -> str:
    """The headline for the card image (the card's font has no colour emoji, so words only)."""
    return headline(b)[1]
