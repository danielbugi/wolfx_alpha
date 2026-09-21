# mechanism/alerts/insights.py
"""
Member features of the private assistant (CHANNEL_CONTENT_MILESTONES.md M4; report items D9, D10, D13, D15): full lists, the aligned-timeframes
list, a stock's past breakouts, the weekly summary of the user's own lists, and a CSV of the whole daily scan.

Pure functions (no Telegram, no database): every screen is unit-tested, validated as Telegram HTML and scanned by the wording guard, like
screens.py. Facts only; anything that cannot be computed is "n/a" with the reason. These are free during the beta; the `tier` column that a
later paid plan needs is not touched here.

Callback data: fl:<tab>:<order>:<page> (full list) | al:<page> (aligned) | hs:SYM:ctx (history). Card context for a full list: f<tab><order><page>.
"""
from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional, Sequence, Tuple

from alerts import performance as perf
from alerts.bot_service import GROUP_LABEL
from alerts.performance import D
from alerts.screens import (Btn, INDENT, Screen, TAB_CAT, TAB_HINT, TAB_TITLE, arrow_pct, asof, chunk, esc, rank_text)

FULL_PAGE_SIZE = 15
ORDERS = {"g": ("Gainers", "ret1_pct"), "a": ("ATR", "range_atr"), "v": ("Volume", "rvol")}
ORDER_HINT = {"g": "biggest 1-day % gain first", "a": "today's range vs its 14-day average, largest first",
              "v": "today's volume vs its 50-day median, largest first"}
NEAR_PCT = 3.0
MIN_BARS_WEEK, MIN_BARS_YEAR = 100, 200                    # a 20-week / 52-week range needs at least this many stored bars
HISTORY_SHOWN = 8


def _sort_key(order: str):
    field = ORDERS[order][1]
    return lambda r: (r.get(field) is None, -(r.get(field) or 0), r["symbol"])          # unknown values last, then the symbol


def _detail(r: Dict, order: str) -> str:
    bits = []
    if order == "a" and r.get("range_atr") is not None:
        bits.append(f"range {r['range_atr']:.1f}× ATR")
    if r.get("rvol") is not None:
        bits.append(f"vol {r['rvol']:.1f}×")
    if order != "a" and r.get("range_atr") is not None:
        bits.append(f"range {r['range_atr']:.1f}× ATR")
    if r.get("category") == "near_breakout" and r.get("below_high_pct") is not None:
        bits.append(f"{r['below_high_pct']:.1f}% below high")
    return INDENT + " · ".join(bits) if bits else ""


# ------------------------------------------------------------------ D9 full lists
def full_list(session: Optional[Dict], rows: Sequence[Dict], tab: str, order: str, page: int, tracked: Dict[str, str],
              today_date: Optional[date] = None) -> Screen:
    if not session:
        return Screen("There is no scan data yet. Try again after the next daily scan.")
    tab = tab if tab in TAB_CAT else "b"
    order = order if order in ORDERS else "g"
    items = sorted(rows, key=_sort_key(order))
    pages = max(1, -(-len(items) // FULL_PAGE_SIZE))
    page = min(max(page, 0), pages - 1)
    chosen = items[page * FULL_PAGE_SIZE:(page + 1) * FULL_PAGE_SIZE]
    lines = [f"<b>All {TAB_TITLE[tab].lower()}</b> · {asof(session['session_date'], today_date)}",
             f"{len(items)} stocks {TAB_HINT[tab]} · sorted by {ORDERS[order][0].lower()} ({ORDER_HINT[order]})", ""]
    if not items:
        lines.append("No stock in this group today.")
    for r in chosen:
        mark = " ✓" if r["symbol"] in tracked else ""
        lines.append(f"<b>{esc(r['symbol'])}</b>{mark}  {arrow_pct(r['ret1_pct'])}  {perf.fmt_price(D(r['close']))}")
        d = _detail(r, order)
        if d:
            lines.append(d)
    lines += ["", "✓ on your lists · tap a stock for its card"]
    ctx = f"f{tab}{order}{page}"
    kb = chunk([Btn(("✓ " if r["symbol"] in tracked else "") + r["symbol"], f"sc:{r['symbol']}:{ctx}") for r in chosen])
    kb.append([Btn(("● " if o == order else "") + ORDERS[o][0], f"fl:{tab}:{o}:0") for o in ORDERS])
    if pages > 1:
        kb.append([Btn("‹ Prev", f"fl:{tab}:{order}:{page - 1}") if page > 0 else Btn("·", "noop"), Btn(f"{page + 1}/{pages}", "noop"),
                   Btn("Next ›", f"fl:{tab}:{order}:{page + 1}") if page < pages - 1 else Btn("·", "noop")])
    kb.append([Btn("Aligned timeframes", "al:0"), Btn("‹ Today's lists", f"td:{tab}:0")])
    return Screen("\n".join(lines), kb)


# ------------------------------------------------------------------ D10 aligned timeframes
def alignment(row: Dict, highs: Optional[Dict]) -> Tuple[Optional[bool], Optional[bool]]:
    """(within 3% of the 20-week high, within 3% of the 52-week high) for one stock; None = not enough stored history to say."""
    if not highs:
        return None, None
    close = float(row["close"])
    week = year = None
    if highs.get("n", 0) >= MIN_BARS_WEEK and highs.get("hi100") is not None:
        week = (float(highs["hi100"]) - close) / close * 100 <= NEAR_PCT
    if highs.get("n", 0) >= MIN_BARS_YEAR and highs.get("hi252") is not None:
        year = (float(highs["hi252"]) - close) / close * 100 <= NEAR_PCT
    return week, year


def aligned_screen(session: Optional[Dict], rows: Sequence[Dict], highs: Dict[str, Dict], tracked: Dict[str, str], page: int = 0,
                   today_date: Optional[date] = None) -> Screen:
    if not session:
        return Screen("There is no scan data yet. Try again after the next daily scan.")
    scored = []
    short_w = short_y = 0
    for r in rows:
        w, y = alignment(r, highs.get(r["symbol"]))
        short_w += w is None
        short_y += y is None
        if w or y:
            scored.append((r, bool(w), bool(y)))
    scored.sort(key=lambda t: (-(t[1] and t[2]), -t[1], -t[2], -(t[0].get("ret1_pct") or 0), t[0]["symbol"]))
    pages = max(1, -(-len(scored) // FULL_PAGE_SIZE))
    page = min(max(page, 0), pages - 1)
    chosen = scored[page * FULL_PAGE_SIZE:(page + 1) * FULL_PAGE_SIZE]
    n_both = sum(1 for _, w, y in scored if w and y)
    lines = [f"<b>Breakouts on longer timeframes</b> · {asof(session['session_date'], today_date)}",
             f"{len(rows)} stocks closed above their 20-day high. {len(scored)} of them also closed within {NEAR_PCT:.0f}% of their 20-week high "
             f"or 52-week high ({n_both} both).", ""]
    if not scored:
        lines.append("None of today's breakouts is within 3% of a 20-week or 52-week high.")
    for r, w, y in chosen:
        near = " · ".join(x for x, ok in (("near 20-week high", w), ("near 52-week high", y)) if ok)
        mark = " ✓" if r["symbol"] in tracked else ""
        lines.append(f"<b>{esc(r['symbol'])}</b>{mark}  {arrow_pct(r['ret1_pct'])}  {perf.fmt_price(D(r['close']))}")
        lines.append(f"{INDENT}{near}")
    if short_w or short_y:
        lines += ["", f"<i>Not enough stored history to measure: {short_w} for the 20-week high, {short_y} for the 52-week high.</i>"]
    lines += ["", "<i>20-week high = highest high of the last 100 sessions; 52-week high = last 252 sessions. A description, not a forecast.</i>"]
    kb = chunk([Btn(("✓ " if r["symbol"] in tracked else "") + r["symbol"], f"sc:{r['symbol']}:fbg0") for r, _, _ in chosen])
    if pages > 1:
        kb.append([Btn("‹ Prev", f"al:{page - 1}") if page > 0 else Btn("·", "noop"), Btn(f"{page + 1}/{pages}", "noop"),
                   Btn("Next ›", f"al:{page + 1}") if page < pages - 1 else Btn("·", "noop")])
    kb.append([Btn("‹ All breakouts", "fl:b:g:0")])
    return Screen("\n".join(lines), kb)


# ------------------------------------------------------------------ D13 a stock's past breakouts
def history_screen(symbol: str, hist: Optional[Dict], ctx: str) -> Screen:
    e = esc(symbol)
    back = [Btn("‹ " + symbol, f"sc:{e}:{ctx}")]
    total = (hist or {}).get("n_total", 0)
    if not total:
        return Screen(f"<b>{e}</b> · past breakouts\nNo 20-day-high breakout is stored for this stock since 2018.\n\n<i>Educational data, not advice.</i>", [back])
    m = hist["n_matured"]
    lines = [f"<b>{e}</b> · past 20-day-high breakouts since {hist['first_year']}",
             f"{total} breakouts; {m} have been followed for 20 sessions."]
    if m:
        lines.append(f"Of those {m}: {hist['n_risk']} fell to a level 2× ATR below the breakout price at some point, and {hist['n_up3']} rose to a "
                     f"level 6× ATR above it before that happened.")
    if m < 10:
        lines.append("<i>Only a few cases, so these counts say little.</i>")
    lines += ["", "<b>Latest</b>"]
    for r in hist["rows"][:HISTORY_SHOWN]:
        if r["risk"]:
            what = "fell to the 2× ATR level"
        elif r["up3"]:
            what = "rose to the 6× ATR level first"
        else:
            what = "neither level within 20 sessions"
        lines.append(f"{r['date']:%d %b %Y} · {what}")
    lines += ["", "<i>Descriptive history from today's index members (stocks that dropped out are missing), not a forecast. "
                  "ATR = average true range.</i>"]
    return Screen("\n".join(lines), [back])


# ------------------------------------------------------------------ D15 the week on the user's own lists
WEEK_SESSIONS = 5


def week_rows(tracked: Sequence[Dict], closes: Dict[str, Sequence[Tuple[date, object]]], sessions: int = WEEK_SESSIONS) -> List[Dict]:
    """One row per tracked stock: change over the last `sessions` stored sessions. `closes` = {symbol: [(date, close)] oldest first}.
    A price series that jumps like a split inside the window is reported as adjusted, not as a move."""
    out = []
    for t in tracked:
        series = list(closes.get(t["symbol"], []))
        row = {"symbol": t["symbol"], "kind": t["kind"], "shares": t.get("shares"), "pct": None, "change": None, "note": None, "start": None, "end": None}
        if len(series) < sessions + 1:
            row["note"] = "no recent price is stored" if not series else "not enough stored days"
        else:
            window = series[-(sessions + 1):]
            prev = None
            adjusted = False
            for _, c in window:
                if prev is not None and prev > 0 and perf.is_split_like(D(c) / D(prev)):
                    adjusted = True
                prev = D(c)
            if adjusted:
                row["note"] = "the price series was adjusted (a split?)"
            else:
                a, b = D(window[0][1]), D(window[-1][1])
                if a and a > 0:
                    row.update(pct=perf.pct_change(b, a), start=window[0][0], end=window[-1][0])
                    if t["kind"] == "hold" and t.get("shares"):
                        row["change"] = (b - a) * D(t["shares"])
        out.append(row)
    return out


def week_screen(rows: Sequence[Dict], session: Optional[Dict], today_date: Optional[date] = None) -> Screen:
    if not session:
        return Screen("There is no scan data yet. Try again after the next daily scan.")
    if not rows:
        return Screen("You have no stocks on your lists yet. Add one with /add AAPL, then check back after a few sessions.")
    known = sorted([r for r in rows if r["pct"] is not None], key=lambda r: (-r["pct"], r["symbol"]))
    unknown = sorted([r for r in rows if r["pct"] is None], key=lambda r: r["symbol"])
    lines = [f"<b>Your lists over the last {WEEK_SESSIONS} sessions</b> · {asof(session['session_date'], today_date)}"]
    if known:
        lines.append(f"{known[0]['start']:%a %d %b} close to {known[0]['end']:%a %d %b} close", )
    lines.append("")
    for r in known:
        tag = "portfolio" if r["kind"] == "hold" else "watchlist"
        lines.append(f"<b>{esc(r['symbol'])}</b>  {perf.fmt_pct(r['pct'])}  · {tag}")
    for r in unknown:
        lines.append(f"<b>{esc(r['symbol'])}</b>  n/a - {r['note']}")
    changes = [r["change"] for r in known if r["change"] is not None]
    if changes:
        total = sum(changes, Decimal(0))
        lines += ["", f"Portfolio stocks with saved shares: {perf.fmt_money(perf.quantize(total))} change in value over the period"]
    lines += ["", "<i>Closing prices over the last five stored sessions. Educational data, not advice.</i>"]
    return Screen("\n".join(lines), [[Btn("Portfolio", "pf:0"), Btn("Watchlist", "wl:0")]])


# ------------------------------------------------------------------ D9 the whole scan as a file
CSV_HEADER = ["us_close_date", "symbol", "group", "close", "day_change_pct", "volume_x_50d_median", "range_x_atr", "below_20d_high_pct", "atr14"]


def scan_csv(session: Dict, rows: Sequence[Dict]) -> str:
    """Every stock of the daily scan (not only the listed ones) as CSV. Unknown values are left empty, never filled in."""
    def cell(v, nd):
        return "" if v is None else f"{float(v):.{nd}f}"
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(CSV_HEADER)
    for r in sorted(rows, key=lambda r: r["symbol"]):
        group = GROUP_LABEL.get(r.get("category"), "not in a long-side group")
        w.writerow([session["session_date"].isoformat(), r["symbol"], group, cell(r["close"], 4), cell(r["ret1_pct"], 2), cell(r["rvol"], 2),
                    cell(r["range_atr"], 2), cell(r["below_high_pct"], 2), cell(r.get("atr"), 4)])
    return buf.getvalue()


# ------------------------------------------------------------------ M5.3 custom screens: /screen breakout price>10 vol>3 sort=range top=10
import re as _re                                                               # noqa: E402

SCREEN_FIELDS = {"price": ("close", "price"), "day": ("ret1_pct", "day change %"), "vol": ("rvol", "volume ×"),
                 "range": ("range_atr", "range × ATR"), "below": ("below_high_pct", "% below 20-day high")}
SCREEN_OPS = {">": lambda a, b: a > b, "<": lambda a, b: a < b, ">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b}
SCREEN_GROUPS = {"breakout": ("breakout",), "near": ("near_breakout",), "all": ("breakout", "near_breakout"), "scan": (None, "breakout", "near_breakout")}
SCREEN_MAX_FILTERS, SCREEN_MAX_TOP, SCREEN_DEFAULT_TOP = 6, 30, 15
_FILTER_RE = _re.compile(r"^(price|day|vol|range|below)(>=|<=|>|<)(-?\d{1,7}(?:\.\d{1,4})?)$")
SCREEN_USAGE = ("Filter today's scan. Examples:\n<code>/screen breakout vol&gt;3</code>\n<code>/screen near price&gt;10 below&lt;1.5 sort=vol top=10</code>\n\n"
                "Groups: breakout, near, all (both), scan (every stock). Filters: price, day (1-day %), vol (volume ×), range (× ATR), below (% below the "
                "20-day high), with &gt; &lt; &gt;= &lt;= and a number. sort= day, vol, range or price; top= up to 30. Up to 6 filters.")


def parse_screen(text: Optional[str]) -> Tuple[Optional[Dict], Optional[str]]:
    """(spec, None) or (None, reason). Only the fixed grammar above is accepted; nothing typed is ever evaluated."""
    spec = {"group": "breakout", "filters": [], "sort": "day", "top": SCREEN_DEFAULT_TOP}
    for tok in (text or "").lower().replace(" ", " ").split():
        if tok in SCREEN_GROUPS:
            spec["group"] = tok
        elif tok.startswith("sort="):
            key = tok[5:]
            if key not in SCREEN_FIELDS:
                return None, f"I cannot sort by “{key[:20]}”."
            spec["sort"] = key
        elif tok.startswith("top="):
            if not tok[4:].isdigit() or not 1 <= int(tok[4:]) <= SCREEN_MAX_TOP:
                return None, f"top= must be a whole number from 1 to {SCREEN_MAX_TOP}."
            spec["top"] = int(tok[4:])
        else:
            m = _FILTER_RE.match(tok)
            if not m:
                return None, f"I could not read “{tok[:24]}”."
            spec["filters"].append((m.group(1), m.group(2), float(m.group(3))))
    if len(spec["filters"]) > SCREEN_MAX_FILTERS:
        return None, f"Use at most {SCREEN_MAX_FILTERS} filters."
    return spec, None


def run_screen(spec: Dict, rows: Sequence[Dict]) -> List[Dict]:
    """Rows that pass every filter (a stock with an unknown value never passes a filter on it), sorted, best first, unknown last."""
    cats = SCREEN_GROUPS[spec["group"]]
    out = []
    for r in rows:
        if r.get("category") not in cats:
            continue
        ok = True
        for name, op, val in spec["filters"]:
            v = r.get(SCREEN_FIELDS[name][0])
            if v is None or not SCREEN_OPS[op](float(v), val):
                ok = False
                break
        if ok:
            out.append(r)
    field = SCREEN_FIELDS[spec["sort"]][0]
    return sorted(out, key=lambda r: (r.get(field) is None, -(float(r.get(field)) if r.get(field) is not None else 0), r["symbol"]))


def screen_screen(session: Optional[Dict], spec: Dict, matches: Sequence[Dict], tracked: Dict[str, str], today_date: Optional[date] = None) -> Screen:
    if not session:
        return Screen("There is no scan data yet. Try again after the next daily scan.")
    shown = list(matches[:spec["top"]])
    group = {"breakout": "Breakout", "near": "Near breakout", "all": "Breakout + Near breakout", "scan": "every stock in the scan"}[spec["group"]]
    rules = " · ".join(f"{SCREEN_FIELDS[n][1]} {o} {v:g}" for n, o, v in spec["filters"]) or "no filters"
    lines = [f"<b>Your screen</b> · {asof(session['session_date'], today_date)}",
             f"{esc(group)} · {esc(rules)} · sorted by {esc(SCREEN_FIELDS[spec['sort']][1])}",
             f"{len(matches)} stocks match" + (f", showing the first {len(shown)}" if len(matches) > len(shown) else ""), ""]
    if not matches:
        lines.append("No stock matches. Loosen a filter.")
    for r in shown:
        mark = " ✓" if r["symbol"] in tracked else ""
        cat = GROUP_LABEL.get(r.get("category"), "no group")
        lines.append(f"<b>{esc(r['symbol'])}</b>{mark}  {arrow_pct(r['ret1_pct'])}  {perf.fmt_price(D(r['close']))}")
        bits = [cat] + ([f"vol {r['rvol']:.1f}×"] if r.get("rvol") is not None else []) + ([f"range {r['range_atr']:.1f}× ATR"] if r.get("range_atr") is not None else [])
        lines.append(f"{INDENT}{' · '.join(bits)}")
    lines += ["", "<i>Filters describe today's stored facts; they are not a forecast. Educational data, not advice.</i>"]
    return Screen("\n".join(lines), chunk([Btn(("✓ " if r["symbol"] in tracked else "") + r["symbol"], f"sc:{r['symbol']}:x") for r in shown]))
