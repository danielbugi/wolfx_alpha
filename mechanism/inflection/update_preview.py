#!/usr/bin/env python3
"""Fundamental Updates previews: render cards, captions and buttons TO FILES. Sends nothing, reads no database, calls no network.

    python mechanism/inflection/update_preview.py --fixture events.json [--news news.json] --out previews/ [--owner]

events.json: {"events": [event rows as read from fundamental_inflection_event, plus "accession"], "companies": {symbol: name},
              "ciks": {symbol: cik}, "profile": {symbol: {date, sector, industry, market_cap}}}
news.json:   [{"query_symbol", "published_at", "updated_at", "fetched_at"?, "headline", "source", "url", "symbols"}]

The cutoff for news is the start (00:00 UTC) of the event's first-detection date: conservative, earlier than any real send could be.
--owner keeps headlines that fail the advice-word guard (owner previews only); the default is the public rule set.
For every event the output folder gets card.png, caption.html, caption.txt, buttons.json, update.json (the public dict) and v1.txt (what the
current owner alert would say, for comparison), plus one index.html gallery.
"""
from __future__ import annotations

import argparse
import base64
import html
import json
import os
import sys
from datetime import date, datetime, timezone
from typing import Any, Dict, List

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from alerts.telegram_client import plain_text, text_length  # noqa: E402
from inflection import alerts as v1  # noqa: E402
from inflection import config as C  # noqa: E402
from inflection.update_card import render_update_card  # noqa: E402
from inflection.update_format import buttons, caption  # noqa: E402
from inflection.update_model import build_update  # noqa: E402
from inflection.update_news import select_news  # noqa: E402


def v1_text(ev: Dict[str, Any], company_name: str) -> str:
    """The current formatter's output for the same event (alerts._events shape, built from the row)."""
    ctx = ev.get("context") or {}
    shaped = {**{k: ev.get(k) for k in ("id", "symbol", "event_type", "severity", "fiscal_year", "fiscal_quarter", "data_quality", "method_version",
                                        "explanation", "availability_basis")},
              "company_name": company_name, "label": C.LABELS.get(ev["event_type"], ev["event_type"]), "strength": float(ev.get("strength") or 0),
              "score": float(ev.get("score") or 0), "quarter_end": str(ev["quarter_end"])[:10], "available_at": str(ev["available_at"])[:10],
              "key_metrics": v1.key_metrics_for(ev["event_type"], ev.get("metrics") or {}), "reaction_2d": (ctx.get("reaction") or {}).get("ret_2d"),
              "excess_vs_sector_since_publication": ctx.get("excess_vs_sector_since_publication"), "valuation": ctx.get("valuation_latest") or {}}
    return plain_text(v1.telegram_text(shaped))


def run(fixture: Dict[str, Any], news: List[Dict[str, Any]], out: str, public: bool = True) -> List[Dict[str, Any]]:
    os.makedirs(out, exist_ok=True)
    results = []
    for ev in fixture["events"]:
        sym = ev["symbol"]
        name = (fixture.get("companies") or {}).get(sym)
        detected = date.fromisoformat(str(ev["first_detected_on"])[:10])
        cutoff = datetime.combine(detected, datetime.min.time(), tzinfo=timezone.utc)
        sel = select_news([n for n in news if n.get("query_symbol") == sym], symbol=sym, company_name=name,
                          public_date=date.fromisoformat(str(ev["available_at"])[:10]), cutoff=cutoff, public=public)
        u, reason = build_update(ev, company_name=name, cik=(fixture.get("ciks") or {}).get(sym), accession=ev.get("accession"),
                                 profile=(fixture.get("profile") or {}).get(sym), news=sel["items"])
        row = {"symbol": sym, "event_type": ev["event_type"], "status": "ok" if u else f"not publishable: {reason}", "news_rejected": sel["rejected"],
               "news_kept": len(sel["items"]), "cutoff": cutoff.isoformat()}
        folder = os.path.join(out, f"{sym}_{ev['event_type']}")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "v1.txt"), "w", encoding="utf-8") as fh:
            fh.write(v1_text(ev, name or ""))
        if u:
            cap, dropped = caption(u)
            png = render_update_card(u)
            with open(os.path.join(folder, "card.png"), "wb") as fh:
                fh.write(png)
            for fn, body in (("caption.html", cap), ("caption.txt", plain_text(cap)), ("buttons.json", json.dumps(buttons(u), indent=1)),
                             ("update.json", json.dumps(u.to_public_dict(), indent=1, default=str))):
                with open(os.path.join(folder, fn), "w", encoding="utf-8") as fh:
                    fh.write(body)
            row.update(caption_chars=text_length(cap), dropped=dropped, card_bytes=len(png), folder=folder, key=u.key)
        results.append(row)
    _gallery(out, results)
    return results


def _gallery(out: str, results: List[Dict[str, Any]]) -> None:
    e = html.escape
    cards = []
    for r in results:
        folder = os.path.join(out, f"{r['symbol']}_{r['event_type']}")
        v1t = open(os.path.join(folder, "v1.txt"), encoding="utf-8").read()
        if r["status"] != "ok":
            cards.append(f"<section><h2>{e(r['symbol'])} · {e(r['event_type'])}</h2><p class=muted>{e(r['status'])}</p><h3>Current owner alert (v1)</h3><pre>{e(v1t)}</pre></section>")
            continue
        img = base64.b64encode(open(os.path.join(folder, "card.png"), "rb").read()).decode()
        cap = open(os.path.join(folder, "caption.html"), encoding="utf-8").read().replace("\n", "<br>")
        kb = json.load(open(os.path.join(folder, "buttons.json"), encoding="utf-8"))["inline_keyboard"]
        btns = "".join("<div class=row>" + "".join(f"<a class=btn href=\"{e(b['url'], quote=True)}\">{e(b['text'])}</a>" for b in row) + "</div>" for row in kb)
        cards.append(f"<section><h2>{e(r['symbol'])} · {e(r['event_type'])}</h2><div class=msg><img alt=\"card\" src=\"data:image/png;base64,{img}\"><div class=cap>{cap}</div></div>"
                     f"<div class=kb>{btns}</div><p class=muted>caption {r['caption_chars']}/1024 characters · news kept {r['news_kept']}, rejected {e(json.dumps(r['news_rejected']))}"
                     f" · cutoff {e(r['cutoff'])}</p><details><summary>Current owner alert (v1), for comparison</summary><pre>{e(v1t)}</pre></details></section>")
    page = ("<!doctype html><meta charset=utf-8><meta name=viewport content=\"width=device-width,initial-scale=1\"><title>Fundamental Updates previews</title><style>"
            "body{margin:0;padding:16px;background:#0f1416;color:#e8eef0;font:15px/1.45 -apple-system,Segoe UI,Roboto,sans-serif}section{max-width:560px;margin:0 auto 40px}"
            ".msg{background:#1c2a30;border-radius:14px;overflow:hidden}.msg img{width:100%;display:block}.cap{padding:10px 12px}.cap a{color:#6cb6ff}"
            ".kb .row{display:flex;gap:6px;margin-top:6px}.btn{flex:1;text-align:center;background:#24363d;color:#e8eef0;border-radius:10px;padding:9px;text-decoration:none}"
            ".muted{color:#93a9a5;font-size:13px}pre{white-space:pre-wrap;background:#161f22;padding:10px;border-radius:8px;font-size:13px}h2{font-size:15px;color:#93a9a5}</style>"
            "<h1 style=\"max-width:560px;margin:0 auto 24px;font-size:20px\">Fundamental Updates: previews (nothing sent)</h1>" + "".join(cards))
    with open(os.path.join(out, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(page)


def main() -> int:
    p = argparse.ArgumentParser(description="Render Fundamental Updates previews to files (sends nothing)")
    p.add_argument("--fixture", required=True)
    p.add_argument("--news")
    p.add_argument("--out", required=True)
    p.add_argument("--owner", action="store_true", help="owner rules: keep headlines that fail the advice-word guard")
    a = p.parse_args()
    fixture = json.load(open(a.fixture, encoding="utf-8"))
    news = json.load(open(a.news, encoding="utf-8")) if a.news else []
    for r in run(fixture, news, a.out, public=not a.owner):
        print(json.dumps(r, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
