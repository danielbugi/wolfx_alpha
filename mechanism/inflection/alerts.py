#!/usr/bin/env python3
"""Owner alerts for the Fundamental Inflection Engine: a private Telegram message per qualifying new event, a daily email digest and a weekly research report.

Safety rules (all enforced here and tested):
  * RESEARCH SIGNAL, never an order recommendation: every message says so.
  * OWNER ONLY: Telegram goes to the private chat with the bot (target 'owner', BOT_OWNER_ID); never to a channel. The email recipient is configured (INFLECTION_EMAIL_TO: an address, or the word
    'owner' for the owner's own login address); with nothing configured no email is sent and a log line says so. No address is ever invented.
  * OFF until switched on: INFLECTION_ALERTS_ENABLED=1 is required for any real send; otherwise the run prepares and logs what it WOULD send (dry run).
  * EXACTLY ONCE: every alert first claims a row in inflection_alert_delivery (UNIQUE dedupe_key) and sends only if it won the claim; a failed send is retried on a later run (at most 3
    attempts). Re-running, restating a quarter or processing the same event twice never alerts twice.
  * Flood guard: at most INFLECTION_TELEGRAM_MAX_PER_RUN (10) event messages per run; the rest are summarised in one message.

Usage:  python mechanism/inflection/alerts.py [--send] [--telegram] [--daily-digest] [--weekly] [--weekly-on-friday] [--asof YYYY-MM-DD]
        (without --send nothing is sent, whatever the environment says)
"""
from __future__ import annotations

import html
import json
import os
import smtplib
import sys
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from inflection import config as C  # noqa: E402

BASE_URL_DEFAULT = "https://dashboard.first-light.finance"
SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2}
MAX_ATTEMPTS = 3
DISCLAIMER = "RESEARCH SIGNAL, not an order recommendation. Rule-based and explainable; not shown to predict returns."


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ formatting (pure)
def _m(v: Optional[float], unit: str) -> str:
    if v is None:
        return "n/a"
    if unit == "pct":
        return f"{v * 100:.1f}%"
    if unit == "money":
        a = abs(v)
        s = f"{a / 1e9:.2f}B" if a >= 1e9 else f"{a / 1e6:.1f}M" if a >= 1e6 else f"{a / 1e3:.0f}K"
        return f"-${s}" if v < 0 else f"${s}"
    if unit == "bps":
        return f"{v:+.0f} bps"
    if unit == "ratio":
        return f"{v:.1f}x"
    return f"{v:.0f}"


def _pct(v: Optional[float]) -> str:
    return "n/a" if v is None else f"{v * 100:+.1f}%"


def key_metric_lines(ev: Mapping[str, Any]) -> List[str]:
    out = []
    for km in (ev.get("key_metrics") or [])[:3]:
        cur, prev = _m(km.get("current"), km["unit"]), km.get("previous")
        out.append(f"{km['label']}: {cur}" + (f" (was {_m(prev, km['unit'])})" if prev is not None else ""))
    return out


def basis_text(basis: str) -> str:
    if basis == "earnings_calendar":
        return "earnings report date"
    if basis == "first_seen":
        return "first seen by First Light"
    if basis.startswith("assumed_lag"):
        return "an assumed lag after quarter end (not a filing date)"
    if basis.startswith("repricing_window_end"):
        return "end of the repricing window"
    if basis.startswith("sec_"):
        return "SEC filing date"
    return basis.replace("_", " ")


def stock_url(base: str, symbol: str) -> str:
    return f"{base.rstrip('/')}/stock/{symbol}"


def telegram_text(ev: Mapping[str, Any], base_url: str = BASE_URL_DEFAULT) -> str:
    """One event as a private Telegram message (HTML parse mode, everything dynamic escaped)."""
    e = html.escape
    v = ev.get("valuation") or {}
    price = f"Price: {_pct(ev.get('reaction_2d'))} around the report, {_pct(ev.get('excess_vs_sector_since_publication'))} vs its sector since"
    if v.get("ps_ratio") is not None:
        price += f" · P/S {v['ps_ratio']:.1f}"
        if v.get("ps_percentile_own_history") is not None:
            price += f" ({v['ps_percentile_own_history'] * 100:.0f}th percentile of its history)"
    lines = [
        "<b>RESEARCH SIGNAL</b> · not an order recommendation",
        f"<b>{e(ev['symbol'])}</b> {e(ev.get('company_name') or '')}".rstrip(),
        f"{e(ev['label'])} · {e(ev['severity'])} (strength {ev['strength']:.0f}, score {ev['score']:.0f})",
    ]
    lines += [f"• {e(l)}" for l in key_metric_lines(ev)]
    if ev.get("explanation"):
        lines.append(f"Why it may matter: {e(ev['explanation'][0])}")
    q = f"FY{ev['fiscal_year']} Q{ev['fiscal_quarter']}" if ev.get("fiscal_year") and ev.get("fiscal_quarter") else f"quarter ending {ev['quarter_end']}"
    lines.append(f"{e(q)} · public {e(str(ev['available_at']))}, date from {e(basis_text(ev['availability_basis']))}")
    lines.append(e(price))
    if ev.get("data_quality") == "partial":
        lines.append("Note: some inputs were missing (partial data).")
    lines.append(e(stock_url(base_url, ev["symbol"])))
    return "\n".join(lines)


def digest_overflow_text(extra: int, base_url: str = BASE_URL_DEFAULT) -> str:
    return f"<b>RESEARCH SIGNAL</b> · {extra} more fundamental inflection event(s) were detected and not sent individually. {html.escape(base_url.rstrip('/'))}/inflection"


_CSS = "font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#1e293b;line-height:1.45"


def _card_html(ev: Mapping[str, Any], base_url: str) -> str:
    e = html.escape
    color = {"high": "#047857", "medium": "#b45309", "low": "#64748b"}.get(ev["severity"], "#64748b")
    v = ev.get("valuation") or {}
    km = "".join(f"<li>{e(l)}</li>" for l in key_metric_lines(ev))
    why = "".join(f"<li>{e(l)}</li>" for l in (ev.get("explanation") or [])[:3])
    price = f"{_pct(ev.get('reaction_2d'))} around the report · {_pct(ev.get('excess_vs_sector_since_publication'))} vs sector since" + (f" · P/S {v['ps_ratio']:.1f}" if v.get("ps_ratio") is not None else "")
    return (f"<tr><td style=\"padding:12px 0;border-top:1px solid #e2e8f0\">"
            f"<div><a href=\"{e(stock_url(base_url, ev['symbol']))}\" style=\"font-size:16px;font-weight:700;color:#0f172a;text-decoration:none\">{e(ev['symbol'])}</a> "
            f"<span style=\"color:#64748b\">{e(ev.get('company_name') or '')}</span></div>"
            f"<div style=\"font-size:13px\"><b style=\"color:{color}\">{e(ev['label'])}</b> · {e(ev['severity'])} · strength {ev['strength']:.0f}, score {ev['score']:.0f}</div>"
            f"<ul style=\"margin:6px 0;padding-left:18px;font-size:13px\">{km}{why}</ul>"
            f"<div style=\"font-size:12px;color:#64748b\">{e(price)}<br>Public {e(str(ev['available_at']))} (from {e(basis_text(ev['availability_basis']))})"
            f"{' · partial data' if ev.get('data_quality') == 'partial' else ''}</div></td></tr>")


def _shell(title: str, intro: str, body: str, base_url: str) -> str:
    e = html.escape
    return (f"<!doctype html><html><body style=\"margin:0;background:#f1f5f9;{_CSS}\"><table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\"><tr><td align=\"center\" style=\"padding:16px\">"
            f"<table role=\"presentation\" width=\"600\" style=\"max-width:600px;width:100%;background:#fff;border-radius:12px;padding:20px\" cellpadding=\"0\" cellspacing=\"0\">"
            f"<tr><td><div style=\"font-size:12px;letter-spacing:.06em;color:#64748b\">FIRST LIGHT</div><h1 style=\"margin:4px 0 6px;font-size:20px\">{e(title)}</h1>"
            f"<p style=\"margin:0 0 12px;font-size:13px;color:#475569\">{e(intro)}</p>"
            f"<p style=\"margin:0 0 8px;font-size:12px;font-weight:700;color:#0f172a\">{e(DISCLAIMER)}</p></td></tr>{body}"
            f"<tr><td style=\"padding-top:14px;border-top:1px solid #e2e8f0;font-size:12px;color:#64748b\"><a href=\"{e(base_url.rstrip('/'))}/inflection\">Open the inflection screener</a> · "
            f"<a href=\"{e(base_url.rstrip('/'))}/lab\">Open the Quant Lab</a></td></tr></table></td></tr></table></body></html>")


def quality_lines(q: Mapping[str, Any]) -> List[str]:
    out = []
    if q.get("last_run"):
        r = q["last_run"]
        out.append(f"Last engine run: {r['status']} (as of {r['asof']}, {r['symbols_processed']} symbols, {r['errors']} errors).")
    else:
        out.append("The engine has no recorded run yet.")
    if q.get("features"):
        out.append(f"{q['partial_features']} of {q['features']} analysed company-quarters have partial data (no prior-year quarter or missing fields); they cannot produce year-over-year events.")
    if q.get("assumed_share") is not None:
        out.append(f"{q['assumed_share'] * 100:.0f}% of recent events use an assumed availability date instead of a known report date.")
    if q.get("stale_days") is not None and q["stale_days"] > 3:
        out.append(f"The newest event is {q['stale_days']} days old.")
    return out


def daily_digest(events: Sequence[Mapping[str, Any]], q: Mapping[str, Any], day: date, base_url: str = BASE_URL_DEFAULT) -> Tuple[str, str, str]:
    """(subject, html, text) for the daily digest of new events."""
    n_high = sum(1 for e in events if e["severity"] == "high")
    subject = f"First Light: {len(events)} new fundamental inflection event(s), {n_high} high · {day.isoformat()}"
    shown = list(events)[:15]
    body = "".join(_card_html(e, base_url) for e in shown) or "<tr><td style=\"padding:12px 0;font-size:13px\">No new events since the last digest.</td></tr>"
    more = f"<tr><td style=\"font-size:12px;color:#64748b\">+ {len(events) - len(shown)} more in the screener.</td></tr>" if len(events) > len(shown) else ""
    dq = "".join(f"<li>{html.escape(l)}</li>" for l in quality_lines(q))
    body += more + f"<tr><td style=\"padding-top:12px\"><div style=\"font-size:13px;font-weight:700\">Data quality</div><ul style=\"margin:4px 0;padding-left:18px;font-size:12px;color:#475569\">{dq}</ul></td></tr>"
    text = [subject, DISCLAIMER, ""]
    for e in shown:
        text += [f"{e['symbol']} {e.get('company_name') or ''} - {e['label']} ({e['severity']}, strength {e['strength']:.0f}, score {e['score']:.0f})"] + [f"  - {l}" for l in key_metric_lines(e)] + [
            f"  public {e['available_at']} (from {basis_text(e['availability_basis'])}) {stock_url(base_url, e['symbol'])}", ""]
    text += ["Data quality:"] + [f"  - {l}" for l in quality_lines(q)] + ["", f"{base_url.rstrip('/')}/inflection"]
    return subject, _shell("Fundamental inflection digest", f"Events detected since the last digest, as of {day.isoformat()}.", body, base_url), "\n".join(text)


def weekly_report(week_events: Sequence[Mapping[str, Any]], by_type: Mapping[str, int], research: Mapping[str, Any], q: Mapping[str, Any], week: str, base_url: str = BASE_URL_DEFAULT) -> Tuple[str, str, str]:
    """(subject, html, text): the week's events, the research experiments and what they found (including the negative findings), new hypotheses, and data-quality issues."""
    e = html.escape
    subject = f"First Light weekly research report · {week}"
    top = sorted(week_events, key=lambda x: -x["strength"])[:10]
    types = ", ".join(f"{k.replace('_', ' ')}: {v}" for k, v in sorted(by_type.items())) or "none"
    exps = research.get("experiments") or []
    exp_rows = "".join(f"<li><b>{e(x['key'])} {e(x['version'])}</b>: {e(x['verdict'].replace('_', ' ').lower())}" + (f" (test net R {x['test_expectancy_r']:+.3f})" if x.get("test_expectancy_r") is not None else "") + "</li>" for x in exps)
    fails = "".join(f"<li>{e(p['pattern'])}: {e(p['evidence'])}</li>" for p in (research.get("failure_patterns") or [])[:6])
    hyp = research.get("derived_hypothesis")
    steps = "".join(f"<li>[{e(s['status'])}] {e(s['statement'])}</li>" for s in (research.get("next_steps") or [])[:5])
    sections = [
        f"<tr><td style=\"padding:10px 0;font-size:13px\"><b>This week</b>: {len(week_events)} new events ({e(types)}).</td></tr>",
        "".join(_card_html(x, base_url) for x in top) or "",
        (f"<tr><td style=\"padding-top:12px;font-size:13px\"><b>Strategy experiments</b><ul style=\"padding-left:18px\">{exp_rows}</ul></td></tr>" if exps else
         "<tr><td style=\"padding-top:12px;font-size:13px\"><b>Strategy experiments</b>: none completed this week.</td></tr>"),
        (f"<tr><td style=\"font-size:13px\"><b>Negative findings and failure patterns</b><ul style=\"padding-left:18px\">{fails}</ul></td></tr>" if fails else ""),
        (f"<tr><td style=\"font-size:13px\"><b>New hypothesis registered</b><p style=\"margin:4px 0\">{e(hyp['statement'])}</p></td></tr>" if hyp else ""),
        (f"<tr><td style=\"font-size:13px\"><b>Next</b><ul style=\"padding-left:18px\">{steps}</ul></td></tr>" if steps else ""),
        f"<tr><td style=\"padding-top:8px;font-size:13px\"><b>Data quality and coverage</b><ul style=\"padding-left:18px;color:#475569\">{''.join(f'<li>{e(l)}</li>' for l in quality_lines(q))}</ul></td></tr>",
    ]
    text = [subject, DISCLAIMER, "", f"This week: {len(week_events)} new events ({types})", ""]
    text += [f"{x['symbol']} - {x['label']} ({x['severity']}, strength {x['strength']:.0f}) {stock_url(base_url, x['symbol'])}" for x in top]
    text += ["", "Strategy experiments:"] + [f"  {x['key']} {x['version']}: {x['verdict']}" for x in exps]
    text += ["", "Failure patterns:"] + [f"  {p['pattern']}: {p['evidence']}" for p in (research.get("failure_patterns") or [])[:6]]
    if hyp:
        text += ["", "New hypothesis: " + hyp["statement"]]
    text += ["", "Data quality:"] + [f"  {l}" for l in quality_lines(q)]
    return subject, _shell("Weekly research report", f"Week {week}: what fundamentals did, what the research found and what is missing.", "".join(sections), base_url), "\n".join(text)


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ delivery with exactly-once claims
class Delivery:
    """A small interface so the logic is testable without a database or a network: claim / mark."""

    def claim(self, dedupe_key: str, channel: str, recipient_ref: str, event_id: Optional[int]) -> bool: ...
    def mark(self, dedupe_key: str, ok: bool, error: Optional[str] = None) -> None: ...


class DbDelivery(Delivery):
    def __init__(self, db) -> None:
        self.db = db

    def claim(self, dedupe_key: str, channel: str, recipient_ref: str, event_id: Optional[int]) -> bool:
        with self.db.get_sync_connection() as conn:
            cur = conn.cursor()
            cur.execute("INSERT INTO inflection_alert_delivery (event_id, channel, recipient_ref, dedupe_key, attempts) VALUES (%s, %s, %s, %s, 1) ON CONFLICT (dedupe_key) DO NOTHING RETURNING id",
                        (event_id, channel, recipient_ref, dedupe_key))
            won = cur.fetchone() is not None
            if not won:      # a previous attempt that FAILED may be retried (bounded); a claimed/sent one never is
                cur.execute("UPDATE inflection_alert_delivery SET status = 'claimed', attempts = attempts + 1, claimed_at = NOW() WHERE dedupe_key = %s AND status = 'failed' AND attempts < %s RETURNING id",
                            (dedupe_key, MAX_ATTEMPTS))
                won = cur.fetchone() is not None
            conn.commit()
            return won

    def mark(self, dedupe_key: str, ok: bool, error: Optional[str] = None) -> None:
        self.db.execute_insert("UPDATE inflection_alert_delivery SET status = %s, sent_at = CASE WHEN %s THEN NOW() ELSE NULL END, last_error = %s WHERE dedupe_key = %s",
                               ("sent" if ok else "failed", ok, None if ok else (error or "")[:500], dedupe_key))


def event_key(ev: Mapping[str, Any]) -> str:
    return f"telegram_owner:{ev['symbol']}:{ev['quarter_end']}:{ev['event_type']}:{ev.get('method_version') or C.METHOD_VERSION}"


def send_event_alerts(events: Sequence[Mapping[str, Any]], delivery: Delivery, send_telegram: Callable[[str], None], *, min_severity: str = "high", max_per_run: int = 10,
                      base_url: str = BASE_URL_DEFAULT, dry_run: bool = True, log: Callable[[str], None] = print) -> Dict[str, int]:
    """Telegram alerts for qualifying events, once each. Returns counts: sent, failed, skipped_duplicate, skipped_severity, overflow."""
    stats = {"sent": 0, "failed": 0, "skipped_duplicate": 0, "skipped_severity": 0, "overflow": 0, "would_send": 0}
    qualifying = [e for e in events if SEVERITY_RANK.get(e["severity"], 0) >= SEVERITY_RANK[min_severity]]
    stats["skipped_severity"] = len(events) - len(qualifying)
    ordered = sorted(qualifying, key=lambda e: (-e["strength"], e["symbol"]))
    for i, ev in enumerate(ordered):
        if i >= max_per_run:
            stats["overflow"] = len(ordered) - max_per_run
            break
        key = event_key(ev)
        if dry_run:
            stats["would_send"] += 1
            log(f"INFLECTION_ALERT dry-run would send {key}")
            continue
        if not delivery.claim(key, "telegram_owner", "owner", ev.get("id")):
            stats["skipped_duplicate"] += 1
            continue
        try:
            send_telegram(telegram_text(ev, base_url))
            delivery.mark(key, True)
            stats["sent"] += 1
        except Exception as exc:  # noqa: BLE001 - one failed send must not stop the rest; it is retried on a later run
            delivery.mark(key, False, f"{type(exc).__name__}: {exc}")
            stats["failed"] += 1
            log(f"INFLECTION_ALERT send failed for {key}: {type(exc).__name__}: {exc}")
    if stats["overflow"] and not dry_run:
        okey = f"telegram_owner:overflow:{date.today().isoformat()}"
        if delivery.claim(okey, "telegram_owner", "owner", None):
            try:
                send_telegram(digest_overflow_text(stats["overflow"], base_url))
                delivery.mark(okey, True)
            except Exception as exc:  # noqa: BLE001
                delivery.mark(okey, False, str(exc))
    return stats


def send_email_once(delivery: Delivery, dedupe_key: str, channel: str, subject: str, html_body: str, text_body: str, send: Callable[[str, str, str], None], *, dry_run: bool = True,
                    log: Callable[[str], None] = print) -> str:
    """'sent' | 'duplicate' | 'failed' | 'dry-run'."""
    if dry_run:
        log(f"INFLECTION_EMAIL dry-run would send {dedupe_key}: {subject}")
        return "dry-run"
    if not delivery.claim(dedupe_key, channel, "owner", None):
        return "duplicate"
    try:
        send(subject, html_body, text_body)
        delivery.mark(dedupe_key, True)
        return "sent"
    except Exception as exc:  # noqa: BLE001
        delivery.mark(dedupe_key, False, f"{type(exc).__name__}: {exc}")
        log(f"INFLECTION_EMAIL send failed for {dedupe_key}: {type(exc).__name__}: {exc}")
        return "failed"


def smtp_sender(to: str) -> Callable[[str, str, str], None]:
    """Plain SMTP like the login-code mailer (SMTP_HOST/PORT/USER/PASSWORD/FROM). Raises on any refusal so the delivery is marked failed and retried."""
    host = os.environ.get("SMTP_HOST")
    if not host:
        raise RuntimeError("SMTP_HOST is not set")

    def send(subject: str, html_body: str, text_body: str) -> None:
        user, password = os.environ.get("SMTP_USER"), os.environ.get("SMTP_PASSWORD")
        msg = EmailMessage()
        msg["Subject"], msg["From"], msg["To"] = subject, os.environ.get("SMTP_FROM", user or ""), to
        msg.set_content(text_body)
        msg.add_alternative(html_body, subtype="html")
        with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587")), timeout=20) as smtp:
            smtp.starttls()
            if user and password:
                smtp.login(user, password)
            refused = smtp.send_message(msg)
            if refused:
                raise RuntimeError(f"server refused the recipient: {refused}")
    return send


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ data access and the run
def _events(db, where: str, params: Sequence[Any], limit: int = 200) -> List[Dict[str, Any]]:
    """Events shaped like the API (reusing its service would import the backend; this is the same SELECT, narrower)."""
    rows = db.execute_dict_query(f"""SELECT e.id, e.symbol, co.company_name, e.event_type, e.strength, e.severity, e.score, e.fiscal_year, e.fiscal_quarter, e.quarter_end, e.available_at, e.availability_basis,
               e.data_quality, e.method_version, e.explanation, e.metrics, e.context
        FROM fundamental_inflection_event e LEFT JOIN LATERAL (SELECT c.company_name FROM companies c WHERE c.symbol = e.symbol LIMIT 1) co ON true
        WHERE e.method_version = %s AND {where} ORDER BY e.strength DESC, e.symbol LIMIT %s""", [C.METHOD_VERSION] + list(params) + [limit])
    out = []
    for r in rows:
        ctx = r.get("context") or {}
        val = ctx.get("valuation_latest") or {}
        out.append({**{k: r[k] for k in ("id", "symbol", "company_name", "event_type", "severity", "fiscal_year", "fiscal_quarter", "data_quality", "method_version", "explanation", "availability_basis")},
                    "label": C.LABELS.get(r["event_type"], r["event_type"]), "strength": float(r["strength"]), "score": float(r["score"]), "quarter_end": r["quarter_end"].isoformat(),
                    "available_at": r["available_at"].isoformat(), "key_metrics": key_metrics_for(r["event_type"], r["metrics"] or {}), "reaction_2d": (ctx.get("reaction") or {}).get("ret_2d"),
                    "excess_vs_sector_since_publication": ctx.get("excess_vs_sector_since_publication"), "valuation": val})
    return out


def key_metrics_for(kind: str, m: Mapping[str, Any]) -> List[Dict[str, Any]]:
    def it(label, cur, prev, unit):
        return {"label": label, "current": cur, "previous": prev, "unit": unit}
    return {
        C.REVENUE_ACCELERATION: [it("Revenue growth YoY", m.get("rev_yoy"), m.get("rev_yoy_prev"), "pct")],
        C.MARGIN_EXPANSION: [it("Operating margin", m.get("op_margin"), m.get("op_margin_py"), "pct")],
        C.OPERATING_LEVERAGE: [it("Operating leverage", m.get("op_leverage"), None, "ratio"), it("Revenue growth YoY", m.get("rev_yoy"), None, "pct")],
        C.PROFITABILITY_TURNAROUND: [it("Net income", m.get("net_income"), m.get("net_income_py"), "money")],
        C.FCF_TURNAROUND: [it("Free cash flow", m.get("free_cash_flow"), m.get("free_cash_flow_py"), "money")],
        C.LIMITED_REPRICING: [it("Inflection score", m.get("inflection_score"), None, "score"), it("Return vs sector", m.get("excess_return_window"), None, "pct")],
    }.get(kind, [])


def quality_snapshot(db, asof: date) -> Dict[str, Any]:
    r = db.execute_dict_query("""SELECT (SELECT COUNT(*) FROM fundamental_feature WHERE method_version = %s) AS features,
            (SELECT COUNT(*) FROM fundamental_feature WHERE method_version = %s AND data_quality <> 'ok') AS partial_features,
            (SELECT COUNT(*) FROM fundamental_inflection_event WHERE method_version = %s AND available_at >= %s::date - 90) AS recent,
            (SELECT COUNT(*) FROM fundamental_inflection_event WHERE method_version = %s AND available_at >= %s::date - 90 AND availability_basis LIKE 'assumed%%') AS recent_assumed,
            (SELECT MAX(available_at) FROM fundamental_inflection_event WHERE method_version = %s) AS newest""", (C.METHOD_VERSION, C.METHOD_VERSION, C.METHOD_VERSION, asof, C.METHOD_VERSION, asof, C.METHOD_VERSION))[0]
    run = db.execute_dict_query("SELECT status, asof, symbols_processed, errors FROM inflection_run_log WHERE method_version = %s ORDER BY id DESC LIMIT 1", (C.METHOD_VERSION,))
    return {"features": r["features"], "partial_features": r["partial_features"], "assumed_share": (r["recent_assumed"] / r["recent"]) if r["recent"] else None,
            "stale_days": (asof - r["newest"]).days if r["newest"] else None,
            "last_run": ({"status": run[0]["status"], "asof": run[0]["asof"].isoformat(), "symbols_processed": run[0]["symbols_processed"], "errors": run[0]["errors"]} if run else None)}


def load_research(lab_dir: Optional[str]) -> Dict[str, Any]:
    """The Lab's published research summary (experiments, failure patterns, derived hypothesis, next steps) when its report files are present."""
    out: Dict[str, Any] = {"experiments": [], "failure_patterns": [], "derived_hypothesis": None, "next_steps": []}
    if not lab_dir or not os.path.isdir(lab_dir):
        return out
    for name in ("strategy_lab_loop.json", "fundamental_inflection_research.json"):
        p = os.path.join(lab_dir, name)
        if not os.path.exists(p):
            continue
        try:
            d = json.load(open(p, encoding="utf-8"))["data"]
        except (OSError, ValueError, KeyError):
            continue
        out["experiments"] += d.get("experiments", [])
        out["failure_patterns"] += (d.get("analysis") or {}).get("failure_patterns", [])
        out["derived_hypothesis"] = d.get("derived_hypothesis") or out["derived_hypothesis"]
        out["next_steps"] += d.get("next_steps", [])
    return out


def resolve_email_to(db) -> Optional[str]:
    to = os.environ.get("INFLECTION_EMAIL_TO", "").strip()
    if not to:
        return None
    if to.lower() == "owner":
        rows = db.execute_dict_query("SELECT email FROM dashboard_users WHERE role = 'owner' AND is_active ORDER BY id LIMIT 1")
        return rows[0]["email"] if rows else None
    return to if "@" in to else None


def run(asof: Optional[date] = None, *, send: bool = False, telegram: bool = False, daily: bool = False, weekly: bool = False, weekly_on_friday: bool = False, log: Callable[[str], None] = print) -> Dict[str, Any]:
    from shared import db
    asof = asof or date.today()
    enabled = os.environ.get("INFLECTION_ALERTS_ENABLED", "0") == "1"
    dry_run = not (send and enabled)
    if send and not enabled:
        log("INFLECTION_ALERT INFLECTION_ALERTS_ENABLED is not 1: nothing is sent (dry run)")
    if not db.execute_dict_query("SELECT to_regclass('inflection_alert_delivery') AS t")[0]["t"]:
        log("INFLECTION_ALERT skipped: migration 40 is not applied")
        return {"skipped": True}
    base = os.environ.get("INFLECTION_BASE_URL", BASE_URL_DEFAULT)
    delivery = DbDelivery(db)
    out: Dict[str, Any] = {"dry_run": dry_run}
    if telegram:
        fresh = int(os.environ.get("INFLECTION_FRESH_DAYS", "3"))
        evs = _events(db, "e.available_at >= %s::date - %s AND e.available_at <= %s", (asof, fresh, asof))
        sender: Callable[[str], None]
        if dry_run:
            sender = lambda t: None  # noqa: E731
        else:
            from alerts.telegram_client import TelegramClient
            client = TelegramClient.from_env("owner", dry_run=False)
            sender = lambda t: client.send_message(t, kind="inflection_alert")  # noqa: E731
        out["telegram"] = send_event_alerts(evs, delivery, sender, min_severity=os.environ.get("INFLECTION_TELEGRAM_MIN_SEVERITY", "high"),
                                            max_per_run=int(os.environ.get("INFLECTION_TELEGRAM_MAX_PER_RUN", "10")), base_url=base, dry_run=dry_run, log=log)
    want_weekly = weekly or (weekly_on_friday and asof.weekday() == 4)
    if daily or want_weekly:
        to = resolve_email_to(db)
        if to is None:
            log("INFLECTION_EMAIL no recipient configured (INFLECTION_EMAIL_TO): no email is prepared or sent")
        else:
            send_fn = (lambda s, h, t: None) if dry_run else smtp_sender(to)
            q = quality_snapshot(db, asof)
            if daily:
                evs = _events(db, "e.first_detected_at >= %s::timestamptz - interval '26 hours' AND e.severity IN ('high','medium')", (datetime.combine(asof, datetime.max.time(), tzinfo=timezone.utc),))
                subj, h, t = daily_digest(evs, q, asof, base)
                out["daily"] = send_email_once(delivery, f"email_digest:{asof.isoformat()}", "email_digest", subj, h, t, send_fn, dry_run=dry_run, log=log)
            if want_weekly:
                iso = asof.isocalendar()
                week = f"{iso[0]}-W{iso[1]:02d}"
                wk = _events(db, "e.first_detected_at >= %s::date - 7", (asof,), limit=500)
                by_type: Dict[str, int] = {}
                for e in wk:
                    by_type[e["event_type"]] = by_type.get(e["event_type"], 0) + 1
                subj, h, t = weekly_report(wk, by_type, load_research(os.environ.get("LAB_REPORTS_DIR")), q, week, base)
                out["weekly"] = send_email_once(delivery, f"email_weekly:{week}", "email_weekly", subj, h, t, send_fn, dry_run=dry_run, log=log)
    log("INFLECTION_ALERTS " + json.dumps(out, default=str, sort_keys=True))
    return out


def main() -> int:
    import argparse
    p = argparse.ArgumentParser(description="Fundamental inflection owner alerts")
    p.add_argument("--asof"); p.add_argument("--send", action="store_true", help="really send (also needs INFLECTION_ALERTS_ENABLED=1)")
    p.add_argument("--telegram", action="store_true"); p.add_argument("--daily-digest", action="store_true"); p.add_argument("--weekly", action="store_true")
    p.add_argument("--weekly-on-friday", action="store_true")
    a = p.parse_args()
    run(date.fromisoformat(a.asof) if a.asof else None, send=a.send, telegram=a.telegram, daily=a.daily_digest, weekly=a.weekly, weekly_on_friday=a.weekly_on_friday)
    return 0


if __name__ == "__main__":
    sys.exit(main())
