"""Unit tests for mechanism/alerts (no database, no network).

Run:  python -m pytest mechanism/alerts/tests -q      (from the repo root)
"""
import os
import sys
import warnings
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest
import requests

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))
sys.path.insert(0, ROOT)
warnings.filterwarnings("ignore")

from alerts import alert_builder as ab  # noqa: E402
from alerts import message_format as mf  # noqa: E402
from alerts import news_links as nl  # noqa: E402
from alerts.telegram_client import TelegramClient, TelegramError, split_message  # noqa: E402

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)


# ------------------------------------------------------------------ news
def test_normalize_handles_current_and_legacy_yfinance_shapes():
    new = {"id": "1", "content": {"title": "Big deal", "pubDate": "2026-09-20T09:00:00Z",
                                  "provider": {"displayName": "Reuters"}, "canonicalUrl": {"url": "https://x.com/a"}}}
    old = {"title": "Old shape", "link": "https://y.com/b", "publisher": "Zacks", "providerPublishTime": int(NOW.timestamp()) - 7200}
    assert nl.normalize(new)["publisher"] == "Reuters" and nl.normalize(new)["url"] == "https://x.com/a"
    assert nl.normalize(old)["title"] == "Old shape" and nl.normalize(old)["published"] == NOW - timedelta(hours=2)
    assert nl.normalize({"title": "no url", "pubDate": "2026-09-20T09:00:00Z"}) is None


def test_unsafe_urls_are_rejected():
    bad = {"title": "x", "link": "javascript:alert(1)", "publisher": "p", "providerPublishTime": int(NOW.timestamp())}
    assert nl.normalize(bad) is None


def test_pick_articles_dedupes_prefers_variety_and_drops_stale():
    def item(t, pub, hours):
        return {"title": t, "link": f"https://e.com/{abs(hash(t)) % 999}", "publisher": pub,
                "providerPublishTime": int((NOW - timedelta(hours=hours)).timestamp())}
    items = [item("Stock jumps on deal", "Reuters", 3), item("STOCK JUMPS ON DEAL!", "Benzinga", 4),      # duplicate headline
             item("Analyst raises target", "Reuters", 5), item("Something else", "CNBC", 6),
             item("Ancient news", "AP", 24 * 30)]
    out = nl.pick_articles(items, 3, now=NOW)
    titles = [a["title"] for a in out]
    assert "Ancient news" not in titles and len([t for t in titles if "jumps" in t.lower()]) == 1
    assert [a["publisher"] for a in out][:2] == ["Reuters", "CNBC"]      # distinct publishers first


def test_shorten_title_and_publisher_and_age():
    assert nl.shorten_title("Short") == "Short"
    t = nl.shorten_title("The quick brown fox jumps over the lazy dog again and again", 30)
    assert len(t) <= 30 and t.endswith("…") and not t[:-1].endswith(" ")
    assert nl.short_publisher("The Motley Fool") == "Motley Fool"
    assert nl.age_label(NOW - timedelta(minutes=5), NOW) == "5m" and nl.age_label(NOW - timedelta(hours=7), NOW) == "7h" \
        and nl.age_label(NOW - timedelta(days=2, hours=3), NOW) == "2d"


def test_link_html_escapes_text_and_attribute():
    a = {"title": 'Fed <b>says</b> "yes" & more', "url": "https://e.com/x?a=1&b=2", "publisher": "A&B", "published": NOW - timedelta(hours=1)}
    h = nl.link_html(a, NOW)
    assert "<b>" not in h and "&amp;b=2" in h and "A&amp;B" in h and h.endswith("(1h)")


# ------------------------------------------------------------------ plan
def test_make_plan_math_and_guards():
    t = ab.PlanTemplate()
    p = ab.make_plan(100.0, 2.0, 96.5, t)
    assert p["stop"] == 96.0 and p["risk_pct"] == pytest.approx(4.0)
    assert [round(x["price"], 2) for x in p["tps"]] == [108.0, 120.0, 140.0]      # +2R/+5R/+10R with R = 4
    assert p["runner_frac"] == pytest.approx(2 / 3, abs=1e-3) and p["trail_pct"] == 40.0
    assert p["alt_stop_10d_low"] == 96.5 and p["alt_stop_risk_pct"] == pytest.approx(3.5)
    assert ab.make_plan(100.0, None, None) is None and ab.make_plan(100.0, float("nan"), None) is None
    assert ab.make_plan(10.0, 4.0, None) is None                                     # 2 ATR stop = 80% of price


def test_templates_and_env_selection(monkeypatch):
    assert ab.TEMPLATES["runner"].tp_fractions == (0.0, 0.0, 0.0)
    assert sum(ab.TEMPLATES["ladder"].tp_fractions) == pytest.approx(0.75)
    monkeypatch.setenv("ALERTS_PLAN", "runner")
    assert ab.template_from_env() is ab.TEMPLATES["runner"]
    with pytest.raises(ValueError):
        ab.template_from_env("yolo")


# ------------------------------------------------------------------ multi-timeframe
def _weekly_frame(sweep: bool, close_last: float):
    dates = pd.bdate_range(end="2026-09-18", periods=35)          # ends on a Friday, 7 weeks
    df = pd.DataFrame({"date": dates, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1e6})
    last_week = df["date"] >= pd.Timestamp("2026-09-14")
    df.loc[last_week, "high"] = 102.0
    if sweep:
        df.loc[df["date"] == pd.Timestamp("2026-09-14"), "low"] = 95.0
    df.loc[df["date"] == pd.Timestamp("2026-09-18"), "close"] = close_last
    return df


def test_weekly_false_breakdown_detected_and_negatives():
    w = ab.mtf_context(_weekly_frame(True, 101.5))["weekly"]
    assert w["spring"] and w["swept"] and w["reclaimed"] and w["complete"]
    assert w["tail_pct"] == pytest.approx((99 - 95) / 99 * 100) and w["loc"] == pytest.approx((101.5 - 95) / 7)
    assert not ab.mtf_context(_weekly_frame(False, 101.5))["weekly"]["spring"]                   # no sweep
    weak = ab.mtf_context(_weekly_frame(True, 96.0))["weekly"]
    assert weak["swept"] and not weak["spring"]                                                   # closed weak
    reclaim_fail = ab.mtf_context(_weekly_frame(True, 98.0))["weekly"]
    assert reclaim_fail["swept"] and not reclaim_fail["reclaimed"] and not reclaim_fail["spring"]


def test_week_in_progress_flag():
    f = _weekly_frame(True, 101.5)
    f = f[f["date"] <= pd.Timestamp("2026-09-16")]                                                # Wednesday
    assert ab.mtf_context(f)["weekly"]["complete"] is False


# ------------------------------------------------------------------ candidates & enrichment
def test_rank_top_movers_filters_illiquid_and_gaps():
    days = pd.bdate_range(end="2026-09-18", periods=30)
    rows = []
    for sym, last_ret, vol in (("BIG", 0.30, 5e6), ("MID", 0.10, 5e6), ("ILLIQ", 0.90, 100), ("DOWN", -0.10, 5e6)):
        for i, d in enumerate(days):
            close = 10.0 * (1 + last_ret) if i == len(days) - 1 else 10.0
            rows.append({"symbol": sym, "date": d, "close": close, "volume": vol})
    top = ab.rank_top_movers(pd.DataFrame(rows), days[-1].date(), pool=10)
    assert list(top["symbol"]) == ["BIG", "MID", "DOWN"]                                          # ILLIQ dropped by the $ volume floor


def _history(n=300, seed=0):
    rng = np.random.default_rng(seed)
    c = 50 * np.exp(np.cumsum(rng.normal(0.0003, 0.015, n)))
    return pd.DataFrame({"date": pd.bdate_range(end="2026-09-18", periods=n), "open": c * 0.998, "high": c * 1.01,
                         "low": c * 0.99, "close": c, "volume": rng.integers(1_000_000, 3_000_000, n).astype(float)})


def test_enrich_returns_flags_and_skips_discontinuities():
    e = ab.enrich(_history())
    assert len(e["flags"]) == ab.N_FLAGS and e["flag_count"] == sum(e["flags"])
    assert e["facts"]["atr"] > 0 and e["mtf"]["weekly"] is not None
    bad = _history()
    bad.loc[200:, ["open", "high", "low", "close"]] *= 0.1                                        # unadjusted reverse split
    with pytest.raises(ab.Skip):
        ab.enrich(bad)
    with pytest.raises(ab.Skip):
        ab.enrich(_history(60))                                                                   # too little history
    jump = _history()
    jump.loc[jump.index[-1], ["open", "high", "low", "close"]] = jump["close"].iloc[-2] * 4       # +300% on the session bar
    assert any("verify" in w for w in ab.enrich(jump)["warnings"])                                 # kept, but flagged


def test_build_alerts_sorts_by_flags_and_survives_bad_symbols():
    movers = pd.DataFrame({"symbol": ["AAA", "BAD", "CCC"], "ret1": [0.3, 0.2, 0.1]})
    hist = {"AAA": _history(seed=1), "CCC": _history(seed=2)}

    def loader(sym):
        if sym == "BAD":
            raise RuntimeError("db exploded")
        return hist[sym]
    alerts, skipped = ab.build_alerts(movers, loader, top_n=5)
    assert {a["symbol"] for a in alerts} == {"AAA", "CCC"} and skipped[0][0] == "BAD"
    counts = [a["flag_count"] for a in alerts]
    assert counts == sorted(counts, reverse=True)


# ------------------------------------------------------------------ formatting
def _alert():
    a = ab.build_alerts(pd.DataFrame({"symbol": ["AAA"], "ret1": [0.2]}), lambda s: _history(), lambda s: {"sector": "Tech & <Co>", "market_cap": 2.5e9})[0][0]
    a["news"] = [{"title": "Big <deal> for AAA", "url": "https://e.com/x?a=1&b=2", "publisher": "Reuters", "published": NOW - timedelta(hours=2)}]
    return a


def test_card_contents_escaping_and_length():
    card = mf.format_card(1, _alert(), NOW)
    for token in ("<b>1. AAA</b>", "🛑 Stop", "🎯 TP1", "🎯 TP2", "🎯 TP3", "🏃 Runner", "📰", "finviz.com/quote.ashx?t=AAA&amp;p=w"):
        assert token in card
    assert "Tech &amp; &lt;Co&gt;" in card and "&lt;deal&gt;" in card and "<deal>" not in card
    assert len(card) < 4000 and card.count("<a ") == card.count("</a>")


def test_header_mentions_plan_and_disclaimer():
    h = mf.format_header(datetime(2026, 9, 18).date(), datetime(2026, 9, 20, 6, 0), 15, 3070, ab.TEMPLATES["balanced"], ["stale data note"])
    assert "3,070" in h and "not a validated signal" in h and "stop to breakeven" in h and "stale data note" in h


# ------------------------------------------------------------------ telegram client
class FakeResp:
    def __init__(self, status=200, body=None):
        self.status_code, self._body = status, body if body is not None else {"ok": True, "result": {"message_id": 7}}

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def post(self, url, json=None, timeout=None):
        self.calls.append((url, json))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def test_dry_run_never_touches_the_network():
    s = FakeSession([])
    assert TelegramClient("t", "c", dry_run=True, session=s).send_message("hi") == [None] and s.calls == []


def test_placeholders_rejected_when_sending():
    for tok, chat in ((None, "1"), ("", "1"), ("123:abc", ""), ("123:abc", None)):
        with pytest.raises(TelegramError):
            TelegramClient(tok, chat, dry_run=False)


def test_send_success_retry_and_token_redaction():
    slept = []
    s = FakeSession([FakeResp(429, {"parameters": {"retry_after": 3}}), FakeResp()])
    c = TelegramClient("SECRET:TOKEN", "-100", dry_run=False, session=s, sleep=slept.append)
    assert c.send_message("hello") == [7] and 3 in slept and s.calls[0][1]["parse_mode"] == "HTML"
    boom = requests.ConnectionError("HTTPSConnectionPool: url /botSECRET:TOKEN/sendMessage failed")
    c2 = TelegramClient("SECRET:TOKEN", "-100", dry_run=False, session=FakeSession([boom] * 4), sleep=lambda *_: None)
    with pytest.raises(TelegramError) as ei:
        c2.send_message("x")
    assert "SECRET" not in str(ei.value)
    c3 = TelegramClient("SECRET:TOKEN", "-100", dry_run=False, session=FakeSession([FakeResp(400, {"ok": False, "description": "chat not found SECRET:TOKEN"})]),
                        sleep=lambda *_: None)
    with pytest.raises(TelegramError) as ei:
        c3.send_message("x")
    assert "chat not found" in str(ei.value) and "SECRET" not in str(ei.value)


def test_split_message_respects_limit_and_keeps_lines():
    text = "\n".join(f"line {i} " + "x" * 50 for i in range(200))
    parts = split_message(text, 500)
    assert all(len(p) <= 500 for p in parts) and "\n".join(parts) == text
    assert split_message("short") == ["short"]


# ------------------------------------------------------------------ the production launch lock
def _no_dotenv(monkeypatch):
    from alerts import telegram_client as tc
    monkeypatch.setattr(tc, "_load_env", lambda: None)                                   # the real .env must not influence these tests
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456789:" + "A" * 35)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-1001111111111")
    monkeypatch.setenv("TELEGRAM_DEV_CHAT_ID", "-1002222222222")
    return tc


def test_production_is_locked_until_the_launch_switch_is_set(monkeypatch):
    tc = _no_dotenv(monkeypatch)
    for value in (None, "", "0", "true", "yes", "2", " "):
        if value is None:
            monkeypatch.delenv(tc.PROD_SWITCH, raising=False)
        else:
            monkeypatch.setenv(tc.PROD_SWITCH, value)
        assert tc.prod_sending_enabled() is False
        with pytest.raises(tc.TelegramError, match="switched OFF"):
            tc.TelegramClient.from_env("prod", dry_run=False)                               # a real send to the public channel: refused
    monkeypatch.setenv(tc.PROD_SWITCH, "1")
    assert tc.prod_sending_enabled() is True
    assert tc.TelegramClient.from_env("prod", dry_run=False).chat_id == "-1001111111111"    # launched


def test_the_lock_never_blocks_dev_or_a_dry_run(monkeypatch):
    tc = _no_dotenv(monkeypatch)
    monkeypatch.delenv(tc.PROD_SWITCH, raising=False)
    assert tc.TelegramClient.from_env("dev", dry_run=False).chat_id == "-1002222222222"     # the test bed is always open
    assert tc.TelegramClient.from_env("prod", dry_run=True).dry_run is True                  # a preview touches nothing, so it is allowed


def test_no_script_can_bypass_the_lock_by_building_a_client_directly():
    """Every sender must go through TelegramClient.from_env (where the lock lives); nothing else may construct a client."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[2]
    offenders = []
    for path in list(root.rglob("*.py")):
        if "tests" in path.parts or path.name == "telegram_client.py":
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "TelegramClient(" in text:
            offenders.append(str(path.relative_to(root)))
    assert offenders == [], offenders
    for sender in ("alerts/send_daily_digest.py", "alerts/send_daily_alerts.py"):
        assert "TelegramClient.from_env(args.to, dry_run=False)" in (root / sender).read_text(encoding="utf-8")
