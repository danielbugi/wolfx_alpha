# mechanism/alerts/morning.py
"""
Morning message for opted-in members (CHANNEL_CONTENT_MILESTONES.md M5.2, report item D8): after each daily scan, a short private message saying
which of the member's OWN stocks changed - and nothing when nothing changed. Pure functions (no Telegram, no database access of their own beyond
the store passed in), so every rule is unit-tested; the sending loop lives in run_bot.py.

Rules: opt-in (/morning on), default off; at most one message per session per member; only stocks the member added; only facts that are in
the stored snapshot (group change, being in the channel's lists, a day move of at least 2x ATR); stale data is never sent; no advice wording.
"""
from __future__ import annotations

import html
from datetime import date
from typing import Dict, List, Optional, Sequence, Tuple

from alerts.bot_service import GROUP_LABEL, STALE_AFTER_DAYS
from alerts.screens import arrow_pct, rank_text

ATR_MOVE = 2.0                                  # a day move of at least this many ATR is worth a line
MAX_LINES = 10
LONG = ("breakout", "near_breakout")


def reasons(row: Dict) -> List[str]:
    """Why a stock is in the message; [] = nothing changed for it."""
    out: List[str] = []
    cat, prev = row.get("category"), row.get("prev_category")
    if cat in LONG and cat != prev:
        out.append(f"moved from {GROUP_LABEL[prev]} to {GROUP_LABEL[cat]}" if prev in LONG else f"entered {GROUP_LABEL[cat]}")
    elif prev in LONG and cat not in LONG:
        out.append(f"left {GROUP_LABEL[prev]}")
    ranks = rank_text(row.get("list_ranks"))
    if ranks:
        out.append(f"in today's lists: {ranks}")
    atr, close, ret = row.get("atr"), row.get("close"), row.get("ret1_pct")
    if atr and close and ret is not None and ret > -100:
        move = abs(float(close) - float(close) / (1 + float(ret) / 100))
        if float(atr) > 0 and move >= ATR_MOVE * float(atr):
            out.append(f"moved {move / float(atr):.1f}× its ATR")
    return out


def build_message(session: Dict, tracked: Sequence[Dict], rows: Dict[str, Dict], today: Optional[date] = None) -> Optional[str]:
    """The message for one member, or None when nothing changed / nothing to compare / the data is stale."""
    if not session or not tracked:
        return None
    if ((today or date.today()) - session["session_date"]).days > STALE_AFTER_DAYS:
        return None
    changed: List[Tuple[Dict, List[str]]] = []
    for t in sorted(tracked, key=lambda t: t["symbol"]):
        row = rows.get(t["symbol"])
        if row:
            why = reasons(row)
            if why:
                changed.append((row, why))
    if not changed:
        return None
    # the count leads: the first line is what the phone's notification shows
    lines = [f"<b>{len(changed)} of your {len(tracked)} stocks changed</b> · US close {session['session_date']:%a %d %b}"]
    for row, why in changed[:MAX_LINES]:
        lines.append(f"<b>{html.escape(row['symbol'], quote=False)}</b> {arrow_pct(row['ret1_pct'])} · {'; '.join(why)}")
    if len(changed) > MAX_LINES:
        lines.append(f"... and {len(changed) - MAX_LINES} more (open /watchlist or /portfolio)")
    lines += ["", "Turn this off with /morning off.", "<i>Educational data, not advice.</i>"]
    return "\n".join(lines)


def plan_round(store, today: Optional[date] = None) -> List[Tuple[int, Optional[str], date]]:
    """[(user id, message or None, session date)] for every opted-in member not yet handled for the latest session. None = nothing to send but
    the session is still marked, so the member is not re-evaluated every few minutes."""
    session = store.latest_session()
    if not session:
        return []
    sd = session["session_date"]
    if ((today or date.today()) - sd).days > STALE_AFTER_DAYS:
        return []                                                  # an old scan is never pushed to anyone
    plan = []
    for uid in store.dm_pending(sd):
        tracked = store.tracked(uid)
        rows = store.stocks(sd, [t["symbol"] for t in tracked]) if tracked else {}
        plan.append((uid, build_message(session, tracked, rows, today), sd))
    return plan
