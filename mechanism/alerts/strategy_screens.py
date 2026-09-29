# mechanism/alerts/strategy_screens.py
"""
Strategy Intelligence screens for the private assistant (OWNER ONLY -- see strategy_intel.py). Pure functions: canonical results
from mechanism/strategy_analytics in, (Telegram HTML, buttons) out. Nothing here computes a statistic: every rate/average is shown by
its backend state -- 'no_data' is "—" (never 0% / 0R), 'preliminary' carries "Preliminary · N=…", 'not_available' names the release.

Callback data (all <= 64 bytes, stable ids only, no serialized state):
  si:z                        strategies list
  si:s|p|v|h:<sid>            summary | performance | directions (vs) | health
  si:l:<sid>:<tok>:<offset>   signal list page (tok = ListSpec.token(), 5 chars)
  si:d:<sid>:<id>:<tok>:<off> one signal (Back returns to that list page; tok 'x' = opened by symbol -> Back goes to the summary)
  si:b:<sid>                  "best signals" explanation
"""
from __future__ import annotations

import html
from datetime import date
from typing import Dict, List, Optional, Sequence

from alerts.screens import Btn, Screen, chunk
from alerts.strategy_intel import ListSpec, PAGE_SIZE
from strategy_analytics.definitions import DEFINITIONS, DEFINITIONS_VERSION, MIN_SAMPLE_SIZE

MINUS = "−"
NO_RESOLVED = "No resolved signals yet."
RELEASE_B = "not collected (Release B)"
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
STATUS = {"open": "OPEN", "stopped": "STOPPED", "target1": "TARGET 1", "target2": "TARGET 2", "target3": "TARGET 3", "expired": "EXPIRED"}
FLAG = {"split_suspect": "split suspect", "same_bar_stop_and_target": "ambiguous: stop and target on the same bar"}
PRICE_STATE = {"awaiting_first_session": "waiting for first forward session", "current": "price data current",
               "lagging": "price data lagging", "stale": "price data stale", "no_price_data": "no price data"}
EVAL_STATE = {"up_to_date": "evaluated through latest session", "pending": "evaluation pending",
              "invalid_price_blocked": "blocked by an invalid price bar"}
INVARIANT = {"duplicate_event_identities": "duplicate signal identities", "multiple_open_positions": "multiple open positions",
             "strategy_version_mismatch": "strategy version mismatches", "resolved_missing_outcome": "resolved without an outcome",
             "open_with_outcome": "open with an outcome", "resolution_flag_on_non_stop": "ambiguity flag on a non-stop",
             "evaluation_flag_on_resolved": "hold flag on a resolved signal"}
DEF_TITLE = {"winner": "What counts as a winner", "win_rate": "Win rate", "outcome_r": "R and outcome R", "held": "Held",
             "expired": "Expired", "ambiguous": "Ambiguous", "stopped": "Stopped", "holding_period": "Holding period",
             "resolved": "Resolved", "open": "Open", "mae_r": "MAE", "target_milestones": "Target milestones", "signal": "Signal"}


def esc(x) -> str:
    return html.escape(str(x), quote=False)


# ============================================================================ formatting (presentation only)
def session(d) -> str:
    """A market-session date as 'Sep 28, 2026' (a date or 'YYYY-MM-DD'); never shifted through a time zone."""
    if d is None:
        return "—"
    if isinstance(d, date):
        return f"{MONTHS[d.month - 1]} {d.day}, {d.year}"
    s = str(d)
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return f"{MONTHS[int(s[5:7]) - 1]} {int(s[8:10])}, {s[:4]}"
    return esc(s)


def short_session(d) -> str:
    full = session(d)
    return full.rsplit(",", 1)[0] if "," in full else full


def px(v) -> str:
    """A stored price, exact: 4 decimals trimmed to at least 2 (46.11, 43.7924)."""
    if v is None:
        return "—"
    s = f"{float(v):.4f}".rstrip("0")
    return s + "0" * max(0, 2 - len(s.split(".")[1])) if "." in s else s + ".00"


def r_value(v, digits: int = 2) -> str:
    v = float(v)
    sign = "+" if v > 0 else MINUS if v < 0 else ""
    return f"{sign}{abs(v):.{digits}f}R"


def metric(m: Dict, kind: str) -> str:
    """'62.1% · N=87' | '33.3% · Preliminary · N=3' | '—' (no data) | '— (not available, Release B)'."""
    state = m.get("state")
    if state == "not_available" or (state != "no_data" and m.get("value") is None):
        return f"— (not available{', Release ' + m['release'] if m.get('release') else ''})"
    if state == "no_data":
        return "—"
    v = m["value"]
    text = f"{v * 100:.1f}%" if kind == "rate" else r_value(v) if kind == "r" else f"{abs(v):.2f}R" if kind == "mag" else f"{v:.1f}"
    return f"{text} · Preliminary · N={m['n']}" if state == "preliminary" else f"{text} · N={m['n']}"


def cell(m: Dict, kind: str) -> str:
    """A narrow table cell: value, '~value' when preliminary, '—' otherwise."""
    if m.get("state") not in ("ok", "preliminary") or m.get("value") is None:
        return "—"
    v = m["value"]
    text = f"{v * 100:.1f}%" if kind == "rate" else r_value(v) if kind == "r" else f"{v:.1f}"
    return ("~" if m["state"] == "preliminary" else "") + text


def title(strategy: Dict) -> str:
    return f"<b>{esc(strategy['display_name'].upper())} · {esc(strategy['version'])}</b>"


def _rows(pairs: Sequence, width: int = 14) -> str:
    return "\n".join(f"{esc(k):<{width}}{esc(v):>{max(1, 26 - width)}}" if k else "" for k, v in pairs)


def nav_summary(sid: int) -> Btn:
    return Btn("‹ Summary", f"si:s:{sid}")


# ============================================================================ screens
def strategies_screen(strategies: Sequence[Dict]) -> Screen:
    if not strategies:
        return Screen("<b>Strategies</b>\n\nNo strategy is registered yet.")
    lines = ["<b>Strategies</b>"]
    for s in strategies:
        t = s["tracking"]
        live = "LIVE" if t["tracking_status"] == "tracking" else "no signals yet"
        lines.append(f"\n<b>{esc(s['display_name'])}</b>\n{esc(s['version'])} · {live}\n"
                     f"{t['total_signals']:,} signals · {t['open']:,} open · {t['resolved']:,} resolved")
    return Screen("\n".join(lines), chunk([Btn(f"{s['display_name'][:28]} {s['version']}", f"si:s:{s['id']}") for s in strategies]))


def choose_strategy(strategies: Sequence[Dict]) -> Screen:
    s = strategies_screen(strategies)
    return Screen(s.text + "\n\n<i>More than one strategy is tracked — pick one.</i>", s.rows)


def summary_screen(sm: Dict, many_strategies: bool = False) -> Screen:
    st, t, p = sm["strategy"], sm["tracking"], sm["performance"]
    sid = st["id"]
    live = "● LIVE" if t["tracking_status"] == "tracking" else "no signals yet"
    table = _rows([("Signals", f"{t['total_signals']:,}"), ("Open", f"{t['open']:,}"), ("Resolved", f"{t['resolved']:,}"),
                   ("Held", f"{t['held']:,}"), ("", ""), ("Bullish", f"{t['bullish']:,}"), ("Bearish", f"{t['bearish']:,}"), ("", ""),
                   ("Win rate", cell(p["win_rate"], "rate")), ("Average R", cell(p["average_r"], "r")),
                   ("Median R", cell(p["median_r"], "r"))])
    lines = [f"{title(st)} {live}",
             f"Tracking since {session(t['first_tracked_session'])} · latest market session {session(sm['reference_session'])}",
             f"<pre>{table}</pre>"]
    if t["resolved"] == 0:
        lines.append(f"<i>{NO_RESOLVED}</i>")
    else:
        lines.append(f"Win rate {metric(p['win_rate'], 'rate')}")
        if p["win_rate"]["state"] == "preliminary":
            lines.append(f"<i>~ preliminary: fewer than {MIN_SAMPLE_SIZE} resolved signals.</i>")
    rows = [[Btn("Performance", f"si:p:{sid}"), Btn("Bull vs bear", f"si:v:{sid}"), Btn("Data health", f"si:h:{sid}")],
            [Btn("All signals", f"si:l:{sid}:-----:0"), Btn("Open", f"si:l:{sid}:-a--n:0"), Btn("Resolved", f"si:l:{sid}:-r--n:0")]]
    if many_strategies:
        rows.append([Btn("‹ Strategies", "si:z")])
    return Screen("\n".join(lines), rows)


def performance_screen(sm: Dict) -> Screen:
    st, p, o = sm["strategy"], sm["performance"], sm["outcomes"]
    sid = st["id"]
    lines = [f"{title(st)}\n<b>Performance</b> · resolved signals only", f"Resolved: {p['resolved_n']:,}"]
    if p["resolved_n"] == 0:
        lines.append(f"\n<i>{NO_RESOLVED}</i> Rates, R and outcomes appear once signals reach a stop, a target or the 20-session time exit.")
    else:
        lines += ["", f"Win rate\n{metric(p['win_rate'], 'rate')}", f"Average R\n{metric(p['average_r'], 'r')}",
                  f"Median R\n{metric(p['median_r'], 'r')}", f"Average hold (trading sessions)\n{metric(p['average_holding_bars'], 'sessions')}",
                  f"Median hold (trading sessions)\n{metric(p['median_holding_bars'], 'sessions')}",
                  f"Average MAE\n{metric(p['average_mae_r'], 'mag')}"]
        term, exp = o["terminal"], o["terminal"]["expired"]
        amb = f"  (incl. {o['ambiguous']['count']} ambiguous)" if o["ambiguous"]["count"] else ""
        lines.append("<pre>" + _rows([("Target 1", term["target1"]["count"]), ("Target 2", term["target2"]["count"]),
                                       ("Target 3", term["target3"]["count"]), ("Stopped", term["stopped"]["count"]),
                                       ("Expired", exp["count"]), ("  positive", exp["positive_count"]),
                                       ("  negative", exp["negative_count"]), ("  flat", exp["flat_count"])]) + "</pre>")
        if amb:
            lines.append(esc(amb.strip()))
        lines.append("<i>A win is a target reached before the stop; an expiry is never a win, but its R counts in average R.</i>")
    lines.append(f"Average MFE: {metric(p['average_mfe_r'], 'mag')}")
    return Screen("\n".join(lines), [[nav_summary(sid), Btn("Bull vs bear", f"si:v:{sid}")]])


DIRECTION_ROWS = [("Signals", "signals", None), ("Open", "normally_open", None), ("Held", "held", None), ("Resolved", "resolved", None),
                  ("Win rate", "win_rate", "rate"), ("Avg R", "average_r", "r"), ("Median R", "median_r", "r"),
                  ("Avg hold", "average_holding_bars", "sessions"), ("Stop rate", "stop_rate", "rate")]


def directions_screen(sm: Dict) -> Screen:
    st, bull, bear = sm["strategy"], sm["directions"]["bullish"], sm["directions"]["bearish"]
    head = f"{'':<10}{'Bull':>8}{'Bear':>8}"
    body = []
    for label, key, kind in DIRECTION_ROWS:
        a, b = (f"{bull[key]:,}", f"{bear[key]:,}") if kind is None else (cell(bull[key], kind), cell(bear[key], kind))
        body.append(f"{label:<10}{a:>8}{b:>8}")
    lines = [f"{title(st)}\n<b>BULLISH vs BEARISH</b>", "<pre>" + esc("\n".join([head] + body)) + "</pre>"]
    notes = []
    if bull["resolved"] == 0 and bear["resolved"] == 0:
        notes.append(f"{NO_RESOLVED} Counts describe what the screener found, not which side performs better.")
    else:
        notes.append(f"~ preliminary: fewer than {MIN_SAMPLE_SIZE} resolved in that direction. "
                     f"Resolved N: bullish {bull['resolved']}, bearish {bear['resolved']}. Avg hold in trading sessions.")
    lines.append("<i>" + esc(" ".join(notes)) + "</i>")
    return Screen("\n".join(lines), [[nav_summary(st["id"]), Btn("Performance", f"si:p:{st['id']}")]])


def health_screen(h: Dict) -> Screen:
    st, led, md, ev = h["strategy"], h["ledger"], h["market_data"], h["evaluation"]
    sid = st["id"]
    violations = {k: n for k, n in h["invariants"].items() if n}
    ps = {k: n for k, n in md["open_signals_by_price_data_state"].items() if n}
    es = {k: n for k, n in ev["open_signals_by_evaluation_state"].items() if n}
    lines = [f"{title(st)}\n<b>STRATEGY DATA HEALTH</b>"]
    if h["status"] == "healthy":
        lines.append("Status: <b>HEALTHY</b>")
    else:
        lines.append("⚠ <b>" + ("INTEGRITY VIOLATION" if h["status"] == "violation" else "DATA HEALTH NEEDS ATTENTION") + "</b>")
        if led["held"]:
            lines.append(f"• {led['held']} signal{'s' if led['held'] != 1 else ''} held for review")
        for key, word in (("stale", "stale symbol"), ("lagging", "lagging symbol")):
            if md["open_signals_by_price_data_state"].get(key):
                n = md["open_signals_by_price_data_state"][key]
                lines.append(f"• {n} {word}{'s' if n != 1 else ''}")
        if ev["open_signals_by_evaluation_state"].get("invalid_price_blocked"):
            lines.append(f"• {ev['open_signals_by_evaluation_state']['invalid_price_blocked']} blocked by an invalid price bar")
        for k, n in violations.items():
            lines.append(f"• {n} {INVARIANT.get(k, k)}")
    total_violations = sum(h["invariants"].values())
    lines.append("<pre>" + _rows([("Ledger", f"{led['total_rows']:,}"), ("Open", f"{led['normally_open']:,}"), ("Held", f"{led['held']:,}"),
                                   ("Split suspect", f"{led['held_by_flag'].get('split_suspect', 0):,}"),
                                   ("Ambiguous", f"{led['ambiguous_resolutions']:,}"), ("Integrity", f"{total_violations:,}")], 16) + "</pre>")
    if ps:
        lines.append("Price state (open):\n" + "\n".join(f"{n:,} {PRICE_STATE.get(k, k)}" for k, n in ps.items()))
    if es and set(es) != {"up_to_date"}:
        lines.append("Evaluation (open):\n" + "\n".join(f"{n:,} {EVAL_STATE.get(k, k)}" for k, n in es.items()))
    attention = h["attention"]["open_signals"][:5]
    if attention:
        lines.append("Needs attention: " + ", ".join(f"{esc(a['symbol'])} ({a['lag_sessions']}{'+' if a['lag_capped'] else ''} behind)"
                                                     if a["evaluation_state"] != "invalid_price_blocked" else f"{esc(a['symbol'])} (invalid bar)"
                                                     for a in attention))
    held = h["attention"]["held_signals"][:5]
    if held:
        lines.append("Held: " + ", ".join(f"{esc(x['symbol'])} since {short_session(x['held_since'])}" for x in held))
    lines.append(f"Latest market session: {session(md['reference_session'])}\nEvaluator history: not persisted")
    rows = [[nav_summary(sid)]]
    if led["held"]:
        rows[0].append(Btn("Held signals", f"si:l:{sid}:-h--n:0"))
    return Screen("\n".join(lines), rows)


def _list_title(spec: ListSpec, ref) -> str:
    parts = []
    if spec.lifecycle:
        parts.append({"a": "OPEN", "o": "OPEN (not held)", "h": "HELD", "r": "RESOLVED"}[spec.lifecycle])
    if spec.direction:
        parts.append(spec.direction.upper())
    if spec.grade:
        parts.append(f"GRADE {spec.grade}")
    head = " · ".join(parts + ["SIGNALS"])
    if spec.today:
        head += f" · {short_session(ref)}" if ref else " · latest session"
    if spec.sort == "grade":
        head += " (by recorded grade)"
    return head


def _signal_line(i: int, s: Dict) -> str:
    arrow = "▲ Bullish" if s["direction"] == "bullish" else "▼ Bearish"
    head = f"{i}. <b>{esc(s['symbol'])}</b> {arrow} · Grade {esc(s['quality_grade'] or '—')} · {short_session(s['signal_date'])}"
    plan = f"   Entry {px(s['entry_price'])} · Stop {px(s['stop_price'])} · T1 {px(s['target1_price'])}"
    if s["lifecycle"] == "held":
        state = f"   HELD ({FLAG.get(s['evaluation_flag'], s['evaluation_flag'])})"
    elif s["lifecycle"] == "resolved":
        amb = " (ambiguous)" if s["resolution_flag"] else ""
        state = f"   {STATUS[s['status']]}{amb} · {r_value(s['outcome_r'])} · {s['bars_held']} sessions"
    else:
        state = "   OPEN"
    return "\n".join([head, plan, state])


def list_screen(strategy: Dict, page: Dict, spec: ListSpec, ref=None) -> Screen:
    sid, tok = strategy["id"], spec.token()
    total, offset, items = page["total"], page["offset"], page["items"]
    head = [f"{title(strategy)}", f"<b>{esc(_list_title(spec, ref))}</b>", f"{total:,} found"]
    if not items:
        empty = NO_RESOLVED if spec.lifecycle == "r" and total == 0 else "No signals match."
        return Screen("\n".join(head + ["", empty]), [[nav_summary(sid)]])
    head.append(f"Showing {offset + 1}–{offset + len(items)} of {total:,}")
    body = [_signal_line(offset + i + 1, s) for i, s in enumerate(items)]
    rows = chunk([Btn(s["symbol"], f"si:d:{sid}:{s['id']}:{tok}:{offset}") for s in items])
    nav = []
    if offset > 0:
        nav.append(Btn("◀ Previous", f"si:l:{sid}:{tok}:{max(0, offset - PAGE_SIZE)}"))
    if page["has_more"]:
        nav.append(Btn("Next ▶", f"si:l:{sid}:{tok}:{offset + PAGE_SIZE}"))
    if nav:
        rows.append(nav)
    rows.append([nav_summary(sid)])
    return Screen("\n".join(head) + "\n\n" + "\n\n".join(body) + "\n\n<i>Tap a symbol for its full record.</i>", rows)


def detail_screen(d: Dict, back_tok: str = "x", back_offset: int = 0, history: Sequence[Dict] = ()) -> Screen:
    st, ident, plan, ctx, lc, prov = d["strategy"], d["identity"], d["trade_plan"], d["context"], d["lifecycle"], d["provenance"]
    sid = st["id"]
    arrow = "▲ Bullish" if ident["direction"] == "bullish" else "▼ Bearish"
    status = "HELD" if lc["state"] == "held" else STATUS[lc["status"]]
    lines = [f"<b>{esc(ident['symbol'])}</b>", f"{esc(st['display_name'].upper())} · {esc(ident['strategy_version'])}",
             f"{arrow} · signal {session(ident['signal_date'])}", f"Status: <b>{status}</b>"
             + (f" ({FLAG.get(lc['evaluation_flag'], lc['evaluation_flag'])})" if lc["evaluation_flag"] else "")]
    risk = f"{px(plan['risk_per_share'])}" + (f" ({plan['risk_pct_of_entry'] * 100:.1f}% of entry)" if plan["risk_pct_of_entry"] else "")
    lines.append("<pre>" + _rows([("Entry", px(plan["entry_price"])), ("Stop (−1R)", px(plan["stop_price"])),
                                  ("Target 1 (+1R)", px(plan["target1_price"])), ("Target 2 (+2R)", px(plan["target2_price"])),
                                  ("Target 3 (+3R)", px(plan["target3_price"])), ("ATR", px(plan["atr"]))], 15) + "</pre>")
    lines.append(f"Risk per share (1R): {esc(risk)}")
    if lc["state"] == "resolved":
        amb = " — ambiguous: stop and target on the same bar (counted as the stop)" if lc["resolution_flag"] else ""
        lines.append(f"Outcome: <b>{r_value(lc['outcome_r'])}</b> · resolved {session(lc['resolved_date'])} · "
                     f"{lc['bars_held']} trading sessions{amb}")
    lines.append(f"Trading sessions since signal: {lc['forward_bars_available']}")
    if lc["mae_r"] is not None:
        lines.append(f"Max adverse excursion: {float(lc['mae_r']):.2f}R")
    lines.append(f"Last evaluated: {session(lc['last_evaluated_date'])}")
    lines.append(f"Grade {esc(ctx['quality_grade'] or '—')} · {esc(ctx['sector'] or 'sector not recorded')} · "
                 f"model: {esc(ctx['model_version']) if ctx['model_version'] else 'not scored (no validated model)'}")
    lineage = "present" if prov["release_b_lineage"] else RELEASE_B
    lines.append(f"Feature snapshot / observation: {lineage} · ledger #{d['id']}")
    others = [h for h in history if h["id"] != d["id"]]
    rows: List[List[Btn]] = []
    if others:
        lines.append(f"\nOther {esc(ident['symbol'])} signals: {len(others)}")
        rows += chunk([Btn(f"{short_session(h['signal_date'])} {'▲' if h['direction'] == 'bullish' else '▼'}", f"si:d:{sid}:{h['id']}:x:0")
                       for h in others[:3]])
    rows.append([Btn("‹ Back to list", f"si:l:{sid}:{back_tok}:{back_offset}") if back_tok != "x" else nav_summary(sid)])
    return Screen("\n".join(lines), rows)


def not_found(symbol: str) -> Screen:
    return Screen(f"No tracked signal found for {esc(symbol)}.")


def best_screen(strategy: Dict) -> Screen:
    return Screen("There is no canonical “best signal” score, so I will not rank signals by conviction.\n\n"
                  "I can sort by the strategy's <b>recorded grade</b> (A→F, set by the screener at signal time). That is the "
                  "screener's own label, not a forecast.",
                  [[Btn("Signals by grade", f"si:l:{strategy['id']}:----g:0"), nav_summary(strategy["id"])]])


def definition_screen(term: str) -> Optional[Screen]:
    text = DEFINITIONS.get(term)
    if text is None:
        return None
    return Screen(f"<b>{esc(DEF_TITLE.get(term, term))}</b>\n{esc(text)}\n\n<i>Canonical definition {esc(DEFINITIONS_VERSION)} — "
                  "the same one the dashboard uses.</i>")


def usage(bad_word: Optional[str] = None) -> Screen:
    head = f"I did not understand “{esc(bad_word)}”.\n\n" if bad_word else ""
    return Screen(head + SI_HELP)


SI_HELP = ("<b>Strategy Intelligence</b> (owner only, read only)\n"
           "/strategies — tracked strategies\n"
           "/strategy — summary of the tracked strategy\n"
           "/performance — win rate, R, outcomes\n"
           "/directions — bullish vs bearish\n"
           "/signals — list; add words: <code>bullish</code> <code>bearish</code> <code>open</code> <code>held</code> "
           "<code>resolved</code> <code>today</code> <code>grade A</code> <code>by grade</code>\n"
           "/signal SYMBOL — one symbol's record\n"
           "/open · /resolved — shortcuts\n"
           "/health — strategy data health\n"
           "Or just ask: “how is Donchian doing?”, “show bearish signals”, “what happened to VLGEA?”, “what counts as a win?”")
