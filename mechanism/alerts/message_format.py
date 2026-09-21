# mechanism/alerts/message_format.py
"""Telegram HTML rendering of the daily shortlist: one header message + one compact card per stock."""
from __future__ import annotations

import html
import math
from datetime import date, datetime
from typing import Dict, List, Optional
from urllib.parse import quote

from alerts.alert_builder import FLAG_LABELS, N_FLAGS, PlanTemplate
from alerts.news_links import link_html


def fmt_price(x: float) -> str:
    return f"${x:,.4f}" if x < 1 else f"${x:,.2f}"


def fmt_cap(x) -> str:
    if not x:
        return ""
    x = float(x)
    for div, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if x >= div:
            return f"${x / div:.1f}{suf}"
    return f"${x:,.0f}"


def dots(k: int) -> str:
    return "●" * k + "○" * (N_FLAGS - k)


def _tf_line(label: str, ctx: Optional[Dict], prior_label: str) -> str:
    if not ctx:
        return f"{label}: n/a (not enough history)"
    state = "complete" if ctx.get("complete") else f"day {ctx['days']}, in progress"
    loc = ctx["loc"]
    pos = "flat range" if not (loc == loc) else f"close in top {max(1, round((1 - loc) * 100))}% of range"
    if ctx["spring"]:
        sweep = f"✅ false-breakdown (wick {ctx['tail_pct']:.1f}% below prior {prior_label} low, closed back above)"
    elif ctx["swept"]:
        sweep = f"swept prior {prior_label} low by {ctx['tail_pct']:.1f}% - no clean reclaim"
    else:
        sweep = f"no sweep of prior {prior_label} lows"
    return f"{label} ({state}): {pos} · {sweep}"


def format_card(rank: int, a: Dict, now_utc: Optional[datetime] = None, template: Optional[PlanTemplate] = None) -> str:
    f, p, sym = a["facts"], a["plan"], a["symbol"]
    esc = html.escape
    head = f"<b>{rank}. {esc(sym)}</b> · {esc(a.get('sector') or 'sector n/a')} · {fmt_price(f['close'])} <b>{f['ret1_pct']:+.1f}%</b>"
    if a.get("market_cap"):
        head += f" · MC {fmt_cap(a['market_cap'])}"
    fh = f["from_52w_high_pct"]
    hi_txt = "n/a" if not (fh == fh) else ("at 52w high" if fh > -1 else f"{abs(fh):.0f}% below 52w high")
    lines = [head,
             f"{dots(a['flag_count'])} {a['flag_count']}/{N_FLAGS} · Vol {f['rvol']:.1f}× · ATR {f['atr_pct']:.1f}% · {hi_txt}",
             _tf_line("W", a["mtf"]["weekly"], "4w"),
             _tf_line("M", a["mtf"]["monthly"], "3m"),
             "",
             f"<b>Plan</b> (ref = last close {fmt_price(p['entry'])})"]
    stop = f"🛑 Stop {fmt_price(p['stop'])} (−{p['risk_pct']:.1f}%) = 1R"
    if p.get("alt_stop_10d_low"):
        stop += f" · alt: 10-day low {fmt_price(p['alt_stop_10d_low'])} (−{p['alt_stop_risk_pct']:.1f}%)"
    lines.append(stop)
    for i, tp in enumerate(p["tps"], 1):
        if tp["sell_frac"] > 0:
            act = f"sell {tp['sell_frac'] * 100:.0f}%" + (", then stop to breakeven" if i == 1 and p["breakeven_after_tp1"] else "")
        else:
            act = "review"
        lines.append(f"🎯 TP{i} {fmt_price(tp['price'])} (+{tp['gain_pct']:.0f}%, +{tp['r']:g}R) - {act}")
    lines.append(f"🏃 Runner {p['runner_frac'] * 100:.0f}%: exit on a close {p['trail_pct']:.0f}% below the highest close since entry")
    for w in a.get("warnings", []):
        lines.append(f"⚠️ {esc(w)}")
    now = now_utc or datetime.utcnow()
    news = a.get("news") or []
    lines.append("📰 " + (" · ".join(link_html(n, now) for n in news) if news else "no recent articles found"))
    q = quote(sym, safe="")
    lines.append(f'📈 <a href="https://finviz.com/quote.ashx?t={q}&amp;p=d">D</a> · '
                 f'<a href="https://finviz.com/quote.ashx?t={q}&amp;p=w">W</a> · '
                 f'<a href="https://finviz.com/quote.ashx?t={q}&amp;p=m">M</a> · '
                 f'<a href="https://finance.yahoo.com/quote/{q}/news">Yahoo news</a>')
    return "\n".join(lines)


def plan_summary(t: PlanTemplate) -> str:
    sells = [(r, fr) for r, fr in zip(t.tp_r, t.tp_fractions) if fr > 0]
    parts = [f"1R = {t.stop_atr:g}×ATR stop"]
    if sells:
        parts.append("; ".join(f"+{r:g}R sell {fr * 100:.0f}%" for r, fr in sells) + (" (then stop to breakeven)" if t.breakeven_after_tp1 else ""))
    else:
        parts.append("no forced sales")
    parts.append(f"rest runs on a {t.trail_pct * 100:.0f}% trail from the highest close")
    return " · ".join(parts)


def format_header(session: date, now_local: datetime, n_alerts: int, universe_n: int, template: PlanTemplate,
                  notes: Optional[List[str]] = None) -> str:
    lines = [f"📊 <b>Momentum shortlist</b> · US session {session:%a %d %b}",
             f"{n_alerts} top gainers from {universe_n:,} liquid US stocks (≥$1M/day), sorted by number of context flags met (●).",
             "Flags: " + " · ".join(FLAG_LABELS),
             "ℹ️ Flags are a descriptive checklist for chart reading, not a validated signal. Most alerts lose; the plan limits each loss to 1R.",
             f"Plan: {html.escape(plan_summary(template))}",
             f"Sent {now_local:%a %d %b %H:%M} Jerusalem"]
    for n in notes or []:
        lines.append(f"⚠️ {html.escape(n)}")
    return "\n".join(lines)
