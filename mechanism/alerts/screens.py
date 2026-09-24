# mechanism/alerts/screens.py
"""
Every screen of the private assistant as (HTML text, button layout). Pure functions: no Telegram, no database, so each screen is
unit-tested, validated as Telegram HTML and scanned by the wording guard (BOT_DESIGN_REPORT.md section 3).

UX rules applied here (SKILLS/telegram-bot-ui-design):
  * mobile first: at most 3 buttons per row, a Back button on every drill-down, text on every button;
  * navigation edits the message in place (run_bot.py), so the chat never fills with old screens;
  * every screen prints the data date ("US close Fri 18 Sep") and warns when it is older than STALE_AFTER_DAYS;
  * each stock appears once per view; anything unknown is "n/a" with the reason, never a guess.

Callback data (<= 64 bytes): ob:N ob:ack | td:<tab>:<page> | sc:SYM:ctx | nw:SYM:ctx | ch:SYM:ctx | lv:SYM:ctx | aw:SYM:ctx |
ah:SYM:ctx | hc:SYM:ctx | rm:SYM:ctx | ry:SYM:ctx | pf:<page> | wl:<page> | dy | cx | noop | def:<key>.
`ctx` says where a card was opened from so Back returns there: t<tab><page> (today), p<page>, w<page>, x (typed ticker).
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Dict, List, Optional, Sequence, Tuple

from alerts import performance as perf
from alerts import texts
from alerts.bot_service import GROUP_LABEL, LIST_LABEL, STALE_AFTER_DAYS
from alerts.performance import D, PositionView, Totals
from alerts.star import is_starred
from alerts.tracker import CAP_HOLD, CAP_WATCH, AddResult

TODAY_PAGE_SIZE = 15                                       # both of today's tabs (~10 and ~13 stocks) fit one screen; <= 8 keyboard rows
TRACKED_PAGE_SIZE = 8
INDENT = "   "
SOURCE_WORD = {"entered": "your price", "close": "last close when added"}
TAB_CAT = {"b": "breakout", "n": "near_breakout"}
TAB_TITLE = {"b": "Breakout", "n": "Near breakout"}
TAB_HINT = {"b": "closed above the prior 20-day high", "n": "within 3% below the prior 20-day high"}


def esc(x) -> str:
    return html.escape(str(x), quote=False)


@dataclass
class Btn:
    text: str
    data: Optional[str] = None
    url: Optional[str] = None


@dataclass
class Screen:
    text: str
    rows: List[List[Btn]] = field(default_factory=list)


def chunk(buttons: Sequence[Btn], n: int = 3) -> List[List[Btn]]:
    return [list(buttons[i:i + n]) for i in range(0, len(buttons), n)]


# ------------------------------------------------------------------ small formatters
def asof(session_date: date, today: Optional[date] = None) -> str:
    line = f"US close {session_date:%a %d %b}"
    if (today or date.today()) - session_date > timedelta(days=STALE_AFTER_DAYS):
        line += " - the latest scan is old, so this may be out of date"
    return line


def arrow_pct(x) -> str:
    return perf.fmt_pct(None if x is None else D(x))


def days_word(n: int) -> str:
    return f"{n} day" if n == 1 else f"{n} days"


def signed_money(x: Decimal) -> str:
    q = perf.quantize(x)
    return f"+{perf.fmt_money(q)}" if q > 0 else perf.fmt_money(q)


def rank_text(ranks: Optional[Dict]) -> str:
    if not ranks:
        return ""
    return " · ".join(f"{LIST_LABEL[k]} #{ranks[k]}" for k in LIST_LABEL if k in ranks)          # fixed order: JSONB loses key order


def since_line(v: PositionView) -> str:
    if v.note == perf.NOTE_ADJUSTED:
        return "n/a - the price series was adjusted after you added it (a split?). Check your broker."
    if v.note == perf.NOTE_NO_PRICE:
        return "n/a - no recent price is stored for this symbol."
    return f"{perf.fmt_pct(v.since_pct)} since {v.ref_date:%a %d %b}" + (f" · {days_word(v.days)}" if v.days else "")


def parse_ctx(ctx: str) -> Tuple[str, str, int]:
    """'tb0' -> ('t', 'b', 0); 'p2' -> ('p', '', 2); 'x' -> ('x', '', 0). Never raises."""
    m = re.fullmatch(r"(?:([tpw])([bn]?)|f([bn][gav]))(\d{0,3})", ctx or "")
    if not m:
        return "x", "", 0
    if m.group(3):                                                    # a full list: 'fbg2' -> ('f', 'bg', 2) = group b, order g, page 2
        return "f", m.group(3), int(m.group(4) or 0)
    if m.group(1) == "t" and not m.group(2):
        return "x", "", 0
    return m.group(1), m.group(2), int(m.group(4) or 0)


def back_button(ctx: str) -> Optional[Btn]:
    kind, tab, page = parse_ctx(ctx)
    if kind == "f":
        return Btn("‹ All " + TAB_TITLE[tab[0]].lower(), f"fl:{tab[0]}:{tab[1]}:{page}")
    if kind == "t":
        return Btn("‹ Today's lists", f"td:{tab}:{page}")
    if kind == "p":
        return Btn("‹ Portfolio", f"pf:{page}")
    if kind == "w":
        return Btn("‹ Watchlist", f"wl:{page}")
    return None


# ------------------------------------------------------------------ onboarding
def onboarding(page: int) -> Screen:
    page = min(max(page, 1), 4)
    if page == 4:
        return Screen(texts.ONBOARDING_LAST, [[Btn(texts.ACK_BUTTON, "ob:ack")], [Btn("‹ " + texts.BACK_BTN, "ob:3")]])
    nav = {1: [Btn(texts.NEXT + " ›", "ob:2"), Btn(texts.SKIP_GUIDE, "ob:4")],
           2: [Btn("‹ " + texts.BACK_BTN, "ob:1"), Btn(texts.NEXT + " ›", "ob:3")],
           3: [Btn("‹ " + texts.BACK_BTN, "ob:2"), Btn(texts.NEXT + " ›", "ob:4")]}[page]
    return Screen(texts.ONBOARDING[page], [nav])


def onboarding_done() -> Screen:
    return Screen(texts.ONBOARDING_DONE, [[Btn("Open today's lists", "td:b:0")]])


# ------------------------------------------------------------------ today's lists
def order_rows(rows: Sequence[Dict]) -> List[Dict]:
    """Starred stocks (top 5 of 2+ lists) first, then stocks in more lists, then the best rank, then the symbol - a stable, explainable order."""
    def key(r):
        ranks = r.get("list_ranks") or {}
        return (not is_starred(ranks), -len(ranks), min(ranks.values()) if ranks else 99, r["symbol"])
    return sorted(rows, key=key)


def today(session: Optional[Dict], rows: Sequence[Dict], tab: str, page: int, tracked: Dict[str, str],
          today_date: Optional[date] = None) -> Screen:
    if not session:
        return Screen("There is no scan data yet. Try again after the next daily scan.")
    tab = tab if tab in TAB_CAT else "b"
    listed = [r for r in rows if r.get("list_ranks")]
    by_tab = {t: order_rows([r for r in listed if r["category"] == c]) for t, c in TAB_CAT.items()}
    total = sum(len(v) for v in by_tab.values())
    head = [f"<b>Today's lists</b> · {asof(session['session_date'], today_date)}"]
    if not total:
        return Screen("\n".join(head + ["", "No stock is in the channel's lists for this session."]),
                      [[Btn("Open help", "hp")]])
    head.append(f"{total} stocks from the channel's lists, each shown once")
    items = by_tab[tab]
    pages = max(1, -(-len(items) // TODAY_PAGE_SIZE))
    page = min(max(page, 0), pages - 1)
    chosen = items[page * TODAY_PAGE_SIZE:(page + 1) * TODAY_PAGE_SIZE]
    lines = head + ["", f"<b>{TAB_TITLE[tab]}</b> - {TAB_HINT[tab]} ({len(items)})"]
    if not items:
        lines.append("No stock in this group today.")
    for r in chosen:
        ranks = r.get("list_ranks") or {}
        marks = (" ★" if is_starred(ranks) else "") + (" ✓" if r["symbol"] in tracked else "")
        lines.append(f"<b>{esc(r['symbol'])}</b>{marks}  {arrow_pct(r['ret1_pct'])}  {perf.fmt_price(D(r['close']))}")
        lines.append(f"{INDENT}{rank_text(ranks)}")
    lines += ["", "★ top 5 of 2+ lists · ✓ on your lists · tap a stock for its card"]
    ctx = f"t{tab}{page}"
    buttons = [Btn(("✓ " if r["symbol"] in tracked else "") + r["symbol"], f"sc:{r['symbol']}:{ctx}") for r in chosen]
    kb = chunk(buttons)
    kb.append([Btn(("● " if t == tab else "") + f"{TAB_TITLE[t]} · {len(by_tab[t])}", f"td:{t}:0") for t in TAB_CAT])
    if pages > 1:
        kb.append([Btn("‹ Prev", f"td:{tab}:{page - 1}") if page > 0 else Btn("·", "noop"), Btn(f"{page + 1}/{pages}", "noop"),
                   Btn("Next ›", f"td:{tab}:{page + 1}") if page < pages - 1 else Btn("·", "noop")])
    total_in_group = ((session.get("counts") or {}).get(TAB_CAT[tab]))
    kb.append([Btn(f"All {total_in_group}" if total_in_group else "All stocks", f"fl:{tab}:g:0"), Btn("What do these mean?", "def:groups")])
    return Screen("\n".join(lines), kb)


# ------------------------------------------------------------------ stock card
def tracked_block(v: PositionView) -> List[str]:
    src = SOURCE_WORD[v.ref_source]
    if v.kind == "watch":
        lines = [f"<b>On your watchlist</b> since {v.ref_date:%a %d %b} at {perf.fmt_price(v.ref_price)} ({src})"]
    else:
        sh = f"{perf.fmt_shares(v.shares)} sh at " if v.shares else "at "
        lines = [f"<b>In your portfolio</b> since {v.ref_date:%a %d %b} · {sh}{perf.fmt_price(v.ref_price)} ({src})"]
    lines.append(since_line(v))
    if v.value is not None and v.change is not None:
        lines.append(f"Value {perf.fmt_money(v.value)} ({signed_money(v.change)})")
    elif v.kind == "hold" and not v.shares and v.note is None:
        lines.append("No shares saved, so this is not in your portfolio totals. Add them with /add " + esc(v.symbol) + " price shares")
    return lines


def stock_card(symbol: str, row: Optional[Dict], session: Optional[Dict], view: Optional[PositionView], ctx: str,
               today_date: Optional[date] = None, notice: Optional[str] = None) -> Screen:
    if not session:
        return Screen("There is no scan data yet. Try again after the next daily scan.")
    if not row:
        return Screen(f"<b>{esc(symbol)}</b> is not in the daily scan (liquid US stocks only).", [[b] for b in [back_button(ctx)] if b])
    lines = [f"<i>{esc(notice)}</i>", ""] if notice else []
    group = GROUP_LABEL.get(row.get("category"), "not in a long-side group")
    lines.append(f"<b>{esc(symbol)}</b> · {group}")
    lines.append(f"Close {perf.fmt_price(D(row['close']))} · day {arrow_pct(row['ret1_pct'])} · {asof(session['session_date'], today_date)}")
    facts = []
    if row.get("rvol") is not None:
        facts.append(f"volume {row['rvol']:.1f}× its 50-day median")
    if row.get("range_atr") is not None:
        facts.append(f"range {row['range_atr']:.1f}× ATR")
    if row.get("below_high_pct") is not None and row.get("category") != "breakout":
        facts.append(f"{row['below_high_pct']:.1f}% below the 20-day high")
    if facts:
        lines.append(facts[0][0].upper() + facts[0][1:] + "".join(" · " + f for f in facts[1:]))
    ranks = rank_text(row.get("list_ranks"))
    lines.append(f"In today's lists: {ranks}" if ranks else "Not in today's channel lists.")
    if view:
        lines += ["", *tracked_block(view)]
    lines += ["", "<i>Educational data, not advice.</i>"]
    e = esc(symbol)
    kb = [[Btn("News", f"nw:{e}:{ctx}"), Btn("Chart", f"ch:{e}:{ctx}"), Btn("ATR levels", f"lv:{e}:{ctx}")]]
    if view is None:
        kb.append([Btn("Add to watchlist", f"aw:{e}:{ctx}"), Btn("Add to portfolio", f"ah:{e}:{ctx}")])
    elif view.kind == "watch":
        kb.append([Btn("Move to portfolio", f"ah:{e}:{ctx}"), Btn("Remove", f"rm:{e}:{ctx}")])
    else:
        kb.append([Btn("Edit price / shares", f"ah:{e}:{ctx}"), Btn("Remove", f"rm:{e}:{ctx}")])
    kb.append([Btn("Past breakouts", f"hs:{e}:{ctx}")])
    back = back_button(ctx)
    if back:
        kb.append([back])
    return Screen("\n".join(lines), kb)


def levels_screen(symbol: str, text: str, ctx: str) -> Screen:
    e = esc(symbol)
    return Screen(text, [[Btn("‹ " + symbol, f"sc:{e}:{ctx}"), Btn("Chart", f"ch:{e}:{ctx}")]])


# ------------------------------------------------------------------ news
def _ago(when: datetime, now: datetime) -> str:
    secs = max(0, int((now - when).total_seconds()))
    if secs < 3600:
        return f"{max(1, secs // 60)}m"
    if secs < 86400:
        return f"{secs // 3600}h"
    return f"{secs // 86400}d"


def news_screen(symbol: str, result, ctx: str, now: Optional[datetime] = None) -> Screen:
    now = now or datetime.now(timezone.utc)
    lines = [f"<b>{esc(symbol)}</b> · news"]
    if result is None or (result.status == "unavailable" and not result.items):
        lines += ["", "News is not available right now. Try again in a few minutes."]
    elif result.status == "empty":
        lines += ["", "No recent news was found for this symbol."]
    else:
        if result.status == "unavailable":
            lines += ["", "<i>The news provider is not answering; these are earlier headlines.</i>"]
        for it in result.items:
            title = it["headline"] if len(it["headline"]) <= 110 else it["headline"][:107].rstrip() + "..."
            src = f"{esc(it['source'])} · " if it.get("source") else ""
            lines += ["", f"• <a href=\"{html.escape(it['url'], quote=True)}\">{esc(title)}</a>",
                      f"{INDENT}{src}{_ago(it['published_at'], now)} ago"]
    lines += ["", "<i>Headlines link to the publisher. Educational data, not advice.</i>"]
    e = esc(symbol)
    return Screen("\n".join(lines), [[Btn("‹ " + symbol, f"sc:{e}:{ctx}"), Btn("Chart", f"ch:{e}:{ctx}")]])


# ------------------------------------------------------------------ chart caption (text alternative of the image, <= 1024 chars)
def chart_caption(symbol: str, close, day_pct, quote_date: Optional[date], view: Optional[PositionView], ctx: str,
                  adjusted_note: bool = False) -> Tuple[str, List[List[Btn]]]:
    lines = [f"<b>{esc(symbol)}</b> · daily chart, about 6 months"]
    if close is not None:
        lines.append(f"Close {perf.fmt_price(D(close))} · day {arrow_pct(day_pct)}" + (f" · US close {quote_date:%a %d %b}" if quote_date else ""))
    lines.append("Dashed line: the prior 20-day high (the breakout level).")
    if view and view.note is None:
        lines.append(f"Your price {perf.fmt_price(view.ref_price)} since {view.ref_date:%a %d %b}: {perf.fmt_pct(view.since_pct)}")
    if adjusted_note:
        lines.append("Note: the stored prices were adjusted inside this window (a split?), so older bars may not match.")
    e = esc(symbol)
    return "\n".join(lines), [[Btn("‹ " + symbol, f"sc:{e}:{ctx}"), Btn("News", f"nw:{e}:{ctx}")]]


# ------------------------------------------------------------------ watchlist / portfolio
def _tracked_row(v: PositionView, kind: str) -> List[str]:
    head = f"<b>{esc(v.symbol)}</b> "
    if v.note == perf.NOTE_ADJUSTED:
        return [head + "n/a - price series adjusted after you added it (a split?)",
                f"{INDENT}now {perf.fmt_price(v.close)} · check your broker"]
    if v.note == perf.NOTE_NO_PRICE:
        return [head + "n/a - no recent price stored"]
    head += f"{perf.fmt_pct(v.since_pct)} since {v.ref_date:%a %d %b}" + (f" ({days_word(v.days)})" if v.days else "")
    if kind == "hold":
        bits = ([f"{perf.fmt_shares(v.shares)} sh"] if v.shares else ["no shares saved"])
        bits += [f"your price {perf.fmt_price(v.ref_price)}", f"now {perf.fmt_price(v.close)}"]
        if v.value is not None:
            bits.append(perf.fmt_money(v.value))
        if v.weight_pct is not None:
            bits.append(f"{perf.quantize(v.weight_pct, Decimal('1')):.0f}%")
    else:
        bits = [f"added at {perf.fmt_price(v.ref_price)} ({SOURCE_WORD[v.ref_source]})", f"now {perf.fmt_price(v.close)}",
                f"day {perf.fmt_pct(v.day_pct)}"]
    return [head, INDENT + " · ".join(bits)]


def tracked_list(kind: str, views: Sequence[PositionView], totals: Optional[Totals], session: Optional[Dict], page: int,
                 today_date: Optional[date] = None) -> Screen:
    title = "Portfolio" if kind == "hold" else "Watchlist"
    letter = "p" if kind == "hold" else "w"
    cap = CAP_HOLD if kind == "hold" else CAP_WATCH
    if not views:
        how = ("Open Today's lists, tap a stock and choose Add to portfolio - or type /add AAPL 140.5 10 (price, then shares)."
               if kind == "hold" else
               "Open Today's lists, tap a stock and choose Add to watchlist - or type /add AAPL. The bot remembers the price and "
               "the day, then shows how the stock changed since.")
        return Screen(f"<b>{title}</b>\n\nYour {title.lower()} is empty.\n{how}", [[Btn("Open today's lists", "td:b:0")]])
    pages = max(1, -(-len(views) // TRACKED_PAGE_SIZE))
    page = min(max(page, 0), pages - 1)
    chosen = views[page * TRACKED_PAGE_SIZE:(page + 1) * TRACKED_PAGE_SIZE]
    when = asof(session["session_date"], today_date) if session else "no scan data yet"
    lines = [f"<b>{title}</b> · {len(views)}/{cap} · {when}"]
    if kind == "hold":
        lines.append("Values at the last close, in USD, without fees.")
        if totals:
            pct = perf.fmt_pct(totals.change_pct)
            lines += [f"<b>Total</b> cost {perf.fmt_money(totals.cost)} · value {perf.fmt_money(totals.value)} · {pct} ({signed_money(totals.change)})"]
            if totals.largest and totals.largest_weight_pct is not None and totals.counted > 1:
                lines.append(f"Largest position: {esc(totals.largest)} {perf.quantize(totals.largest_weight_pct, Decimal('1')):.0f}% of the portfolio")
            if totals.left_out:
                lines.append(f"{totals.left_out} holding(s) without shares or a usable price are not in the totals.")
        else:
            lines.append("No holding has shares and a usable price yet, so there are no totals. Add shares with /add SYMBOL price shares.")
    for v in chosen:
        lines.append("")
        lines += _tracked_row(v, kind)
    ctx = f"{letter}{page}"
    kb = chunk([Btn(v.symbol, f"sc:{esc(v.symbol)}:{ctx}") for v in chosen])
    if pages > 1:
        cb = "pf" if kind == "hold" else "wl"
        kb.append([Btn("‹ Prev", f"{cb}:{page - 1}") if page > 0 else Btn("·", "noop"), Btn(f"{page + 1}/{pages}", "noop"),
                   Btn("Next ›", f"{cb}:{page + 1}") if page < pages - 1 else Btn("·", "noop")])
    kb.append([Btn("Today's lists", "td:b:0"), Btn("Export", "ex")])
    lines.append("")
    lines.append("<i>Educational data, not advice.</i>")
    return Screen("\n".join(lines), kb)


# ------------------------------------------------------------------ adding / removing
def add_notice(res: AddResult) -> str:
    """One plain sentence for the card (never HTML: the card escapes it)."""
    sym = res.symbol
    src = SOURCE_WORD.get(res.ref_source, "")
    if res.code in ("ok", "updated"):
        price = perf.fmt_price(res.ref_price)
        if res.kind == "watch":
            return f"{'Updated' if res.code == 'updated' else 'Added'}: on your watchlist at {price} ({src})."
        sh = f"{perf.fmt_shares(res.shares)} sh at " if res.shares else "at "
        verb = "Moved to your portfolio" if res.moved_from_watch else ("Updated in your portfolio" if res.code == "updated" else "Added to your portfolio")
        tail = "" if res.shares else " Add your shares with /add " + sym + " price shares to include it in the totals."
        return f"{verb}: {sh}{price} ({src}).{tail}"
    if res.code == "already":
        return f"Already on your {'watchlist' if res.kind == 'watch' else 'portfolio'} at {perf.fmt_price(res.ref_price)} ({src})."
    if res.code == "already_held":
        return "Already in your portfolio, so it was left as it is."
    if res.code == "cap_full":
        cap = CAP_WATCH if res.kind == "watch" else CAP_HOLD
        return f"Your {'watchlist' if res.kind == 'watch' else 'portfolio'} is full ({cap} stocks). Remove one first."
    if res.code == "unknown_symbol":
        return f"{sym} is not in the daily scan (liquid US stocks only)."
    if res.code == "no_price":
        return f"No price is stored for {sym} yet, so send your own price: /add {sym} 140.5"
    if res.code == "bad_number":
        return "I could not use that price or number."
    return "There is no scan data yet. Try again after the next daily scan."


def multi_add_summary(results: Sequence[AddResult]) -> str:
    groups: Dict[str, List[str]] = {}
    for r in results:
        key = {"ok": "Added to your watchlist at the last close", "updated": "Updated", "already": "Already on your watchlist",
               "already_held": "Already in your portfolio", "unknown_symbol": "Not in the daily scan (liquid US stocks only)",
               "cap_full": "Watchlist is full", "no_price": "No price stored yet"}.get(r.code, "Could not add")
        groups.setdefault(key, []).append(r.symbol)
    lines = [f"{esc(k)}: {esc(', '.join(v))}" for k, v in groups.items()]
    lines.append("See them with /watchlist")
    return "\n".join(lines)


def hold_prompt(symbol: str, last_close: Optional[Decimal], ctx: str, existing: Optional[PositionView] = None) -> Screen:
    e = esc(symbol)
    lines = [f"<b>{e} · {'update this holding' if existing and existing.kind == 'hold' else 'add to portfolio'}</b>"]
    if last_close is not None:
        lines.append(f"Latest close {perf.fmt_price(last_close)}")
    lines += ["", "Send your price and your shares, for example <code>140.5 10</code>.",
              "Shares are optional: <code>140.5</code> alone works too, but then this holding is left out of the totals."]
    kb = []
    if last_close is not None:
        kb.append([Btn(f"Use last close {perf.fmt_price(last_close)}", f"hc:{e}:{ctx}")])
    kb.append([Btn("Cancel", f"sc:{e}:{ctx}")])
    return Screen("\n".join(lines), kb)


def remove_confirm(symbol: str, kind: str, ctx: str) -> Screen:
    e = esc(symbol)
    where = "portfolio" if kind == "hold" else "watchlist"
    return Screen(f"Remove <b>{e}</b> from your {where}? Its saved price and date are erased.",
                  [[Btn("Yes, remove", f"ry:{e}:{ctx}"), Btn("Keep it", f"sc:{e}:{ctx}")]])


def erase_confirm() -> Screen:
    return Screen(texts.DELETE_CONFIRM, [[Btn("Yes, erase everything", "dy"), Btn("Cancel", "cx")]])


def help_screen(is_owner: bool, hours: int = 72, days: int = 30) -> Screen:
    text = texts.HELP + (texts.OWNER_HELP.format(hours=hours, days=days) if is_owner else "")
    return Screen(text, [[Btn("Open today's lists", "td:b:0"), Btn("Quick tour", "ob:1")]])


# ------------------------------------------------------------------ access: what a person without access sees (FUNNEL_PLAN.md section 3)
def _day(dt) -> str:
    return f"{dt:%d %b}" if dt else "later"


def not_invited(uid: int, state: Dict, invalid_invite: bool = False) -> Screen:
    """The single reply a person without access gets. Only a person who CAN request sees the Request button; nothing is stored by
    showing this screen."""
    reason = state.get("reason")
    if reason == "blocked":
        return Screen(texts.INVITE_REVOKED)
    if reason == "pending":
        return Screen(texts.REQUEST_PENDING)
    if reason == "cooldown":
        return Screen(texts.REQUEST_COOLDOWN.format(date=_day(state.get("until"))))
    if reason == "closed":
        return Screen(texts.NOT_INVITED_CLOSED.format(uid=uid), [[Btn(texts.WHAT_BUTTON, "rq:info")]])
    text = texts.INVITE_INVALID if invalid_invite else texts.NOT_INVITED
    return Screen(text.format(uid=uid), [[Btn(texts.REQUEST_BUTTON, "rq:new"), Btn(texts.WHAT_BUTTON, "rq:info")]])


def what_is_this(can_request: bool) -> Screen:
    rows = [[Btn(texts.REQUEST_BUTTON, "rq:new")]] if can_request else []
    rows.append([Btn("‹ " + texts.BACK_TO_ACCESS, "rq:back")])
    return Screen(texts.WHAT_IS_THIS, rows)


def owner_request(uid: int) -> Screen:
    return Screen(texts.OWNER_REQUEST_NOTICE.format(uid=uid), [[Btn("Approve", f"ra:{uid}"), Btn("Decline", f"rd:{uid}")]])


def requests_list(count: int, rows: Sequence[Dict]) -> Screen:
    if not count:
        return Screen("<b>Requests</b>\nNobody is waiting for access.")
    lines = [f"<b>Requests</b> · {count} waiting"]
    kb = []
    for r in rows:
        when = r["requested_at"]
        stamp = f"{when:%d %b %H:%M} UTC" if hasattr(when, "strftime") else ""
        lines.append(f"<code>{r['telegram_user_id']}</code> · asked {stamp}")
        kb.append([Btn(f"Approve {r['telegram_user_id']}", f"ra:{r['telegram_user_id']}"), Btn("Decline", f"rd:{r['telegram_user_id']}")])
    if count > len(rows):
        lines.append(f"...and {count - len(rows)} more (the oldest are shown first).")
    return Screen("\n".join(lines), kb)


def funnel_screen(m7: Dict, m30: Dict) -> Screen:
    def line(label, key):
        return f"{label}: {m7[key]} (7 days) · {m30[key]} (30 days)"
    lines = ["<b>Funnel</b> · counts only, never who", "",
             line("Opened from the channel", "opened_channel"), line("Requested access", "requested"), line("Approved", "approved"),
             line("Finished the guide", "finished_guide"), line("Tracked a first stock within 24 h", "activated"), "",
             f"Active in the last 7 days: {m7['active_7d']} · waiting now: {m7['waiting']} · cooling down: {m7['declined_cooling']}"]
    return Screen("\n".join(lines))
