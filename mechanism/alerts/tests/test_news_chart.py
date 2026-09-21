"""News service (parsing, cost controls, failure handling, concurrency) and the real chart renderer. No network: the provider is injected."""
import os
import random
import struct
import sys
import threading
import time
from datetime import date, datetime, timedelta, timezone

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))
sys.path.insert(0, ROOT)

from alerts import news_service as ns  # noqa: E402

from qa_harness import FakeNews, MemoryStore  # noqa: E402

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)


def item(n=1, sym="MSTR", **over):
    d = {"headline": f"Headline {n}", "created_at": "2026-09-21T07:28:00Z", "source": "benzinga", "url": f"https://news.test/{n}", "symbols": [sym, "BTCUSD"]}
    d.update(over)
    return d


# ================================================================== parsing
def test_alpaca_payload_is_parsed_into_headline_link_source_and_time():
    got = ns.parse_alpaca({"news": [item(1), item(2)]}, "MSTR")
    assert [g["headline"] for g in got] == ["Headline 1", "Headline 2"]
    g = got[0]
    assert g["published_at"] == datetime(2026, 9, 21, 7, 28, tzinfo=timezone.utc) and g["source"] == "benzinga" and g["url"] == "https://news.test/1"
    assert g["url_hash"] == ns.url_hash("https://news.test/1") and len(g["url_hash"]) == 64
    assert set(g) == {"published_at", "headline", "source", "url", "url_hash"}                          # no article body is ever kept


@pytest.mark.parametrize("bad", [item(url="javascript:alert(1)"), item(url="ftp://x.test/1"), item(url="data:text/html,x"), item(url=""),
                                 item(symbols=["AAPL"]), item(symbols=None), item(created_at="not a date"), item(created_at="2026-09-21T07:28:00"),
                                 item(headline=""), item(headline="   "), {"headline": "no url"}, {}, None, 5, "x"])
def test_malformed_or_unsafe_items_are_skipped_never_raised(bad):
    assert ns.parse_alpaca({"news": [bad, item(9)]}, "MSTR")[-1]["headline"] == "Headline 9"           # the good one survives
    assert all(g["url"].startswith("https://news.test/") for g in ns.parse_alpaca({"news": [bad, item(9)]}, "MSTR"))


def test_headlines_are_cleaned_capped_and_deduplicated_by_link():
    got = ns.parse_alpaca({"news": [item(1, headline="  Big \n  move\t today "), item(2, headline="x" * 900), item(1, headline="same link again")]}, "MSTR")
    assert got[0]["headline"] == "Big move today" and len(got[1]["headline"]) == ns.HEADLINE_MAX and len(got) == 2
    assert ns.parse_alpaca(None, "MSTR") == [] and ns.parse_alpaca({}, "MSTR") == [] and ns.parse_alpaca({"news": None}, "MSTR") == []


def test_the_real_provider_call_refuses_to_run_without_keys(monkeypatch):
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET", raising=False)
    with pytest.raises(RuntimeError):
        ns.alpaca_fetch("MSTR")                                                                        # fails BEFORE any network call


# ================================================================== cost controls and failure handling
def svc_with(fetch, clock):
    store = MemoryStore()
    return ns.NewsService(store, fetch=fetch, now=lambda: clock[0]), store


def test_a_symbol_is_fetched_once_per_ttl_and_again_only_after_it():
    clock, fake = [NOW], FakeNews()
    s, store = svc_with(fake, clock)
    assert s.get("MSTR").status == "ok" and fake.calls == ["MSTR"]
    clock[0] = NOW + timedelta(hours=ns.NEWS_TTL_HOURS) - timedelta(seconds=1)
    s.get("MSTR")
    assert fake.calls == ["MSTR"]                                                                      # still fresh
    clock[0] = NOW + timedelta(hours=ns.NEWS_TTL_HOURS, seconds=1)
    s.get("MSTR")
    assert fake.calls == ["MSTR", "MSTR"]                                                              # stale: one more call
    s.get("COIN")
    assert fake.calls == ["MSTR", "MSTR", "COIN"] and len(store.news_recent("MSTR", 50)) == 2         # the same headlines are not stored twice


def test_no_headlines_is_cached_too_and_reported_as_empty():
    clock, fake = [NOW], FakeNews()
    fake.empty = True
    s, _ = svc_with(fake, clock)
    r = s.get("MSTR")
    s.get("MSTR")
    assert (r.status, r.items, r.stale) == ("empty", [], False) and fake.calls == ["MSTR"]


def test_a_failed_fetch_is_remembered_for_a_short_backoff_then_retried():
    clock, fake = [NOW], FakeNews()
    fake.fail = True
    s, _ = svc_with(fake, clock)
    assert s.get("MSTR").status == "unavailable" and s.get("MSTR").status == "unavailable" and fake.calls == ["MSTR"]     # no hammering
    clock[0] = NOW + timedelta(minutes=ns.RETRY_MINUTES, seconds=1)
    fake.fail = False
    r = s.get("MSTR")
    assert r.status == "ok" and fake.calls == ["MSTR", "MSTR"] and len(r.items) == 2                   # the provider recovered


def test_earlier_headlines_are_kept_and_flagged_when_a_refresh_fails():
    clock, fake = [NOW], FakeNews()
    s, _ = svc_with(fake, clock)
    s.get("MSTR")
    clock[0] = NOW + timedelta(hours=ns.NEWS_TTL_HOURS + 1)
    fake.fail = True
    r = s.get("MSTR")
    assert r.status == "unavailable" and r.stale is True and len(r.items) == 2


def test_the_show_limit_is_respected_and_newest_comes_first():
    clock = [NOW]
    many = lambda sym: [{"published_at": NOW - timedelta(hours=i), "headline": f"H{i}", "source": None, "url": f"https://n.test/{i}",   # noqa: E731
                         "url_hash": ns.url_hash(f"https://n.test/{i}")} for i in range(12)]
    s, _ = svc_with(many, clock)
    r = s.get("MSTR")
    assert [i["headline"] for i in r.items] == ["H0", "H1", "H2", "H3", "H4"]


def test_the_symbol_is_never_written_to_the_log_when_the_provider_fails(caplog):
    import logging
    caplog.set_level(logging.DEBUG)
    fake = FakeNews()
    fake.fail = True
    svc_with(fake, [NOW])[0].get("SECRETSYM")
    assert "SECRETSYM" not in caplog.text and "RuntimeError" in caplog.text


def test_simultaneous_requests_for_one_symbol_make_one_provider_call():
    calls, gate = [], threading.Barrier(8)

    def slow(symbol):
        calls.append(symbol)
        time.sleep(0.15)                                                                               # a slow provider: the others must wait, not repeat
        return FakeNews()(symbol)

    s, _ = svc_with(slow, [NOW])
    results = []

    def go():
        gate.wait()
        results.append(s.get("MSTR"))

    threads = [threading.Thread(target=go) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert calls == ["MSTR"] and len(results) == 8 and all(r.status == "ok" and len(r.items) == 2 for r in results)


# ================================================================== the real chart renderer
chart = pytest.importorskip("alerts.chart")


def bars(n=126, start=100.0, seed=7, flat=False, split_at=None, volume=2_000_000):
    rnd, out, price = random.Random(seed), [], start
    for i in range(n):
        if not flat:
            price *= 1 + rnd.uniform(-0.03, 0.035)
        if split_at is not None and i == split_at:
            price /= 2
        o, c = price * (1 + rnd.uniform(-0.01, 0.01)), price
        out.append({"date": date(2026, 4, 1) + timedelta(days=i), "open": o, "high": max(o, c) * 1.01, "low": min(o, c) * 0.99, "close": c,
                    "volume": volume * (0.5 + rnd.random())})
    return out


def png_size(png):
    assert png[:8] == chart.PNG_MAGIC
    return struct.unpack(">II", png[16:24])


def test_a_real_chart_is_a_valid_png_of_the_expected_size():
    png = chart.render_chart("MSTR", bars())
    assert png_size(png) == (1080, 640) and 15_000 < len(png) < 400_000


def test_the_personal_chart_with_the_users_price_line_renders_and_differs():
    plain = chart.render_chart("MSTR", bars())
    mine = chart.render_chart("MSTR", bars(), ref_price=105.0, ref_date=date(2026, 6, 1), ref_label="your price $105.00")
    assert png_size(mine) == (1080, 640) and mine != plain
    late = chart.render_chart("MSTR", bars(), ref_price=105.0, ref_date=date(2027, 1, 1))              # a reference after the last bar: no marker, no crash
    assert png_size(late) == (1080, 640)


@pytest.mark.parametrize("kw", [dict(n=5), dict(n=12), dict(n=30), dict(flat=True), dict(split_at=60), dict(volume=0), dict(start=0.02, seed=3), dict(start=5000.0)])
def test_the_chart_survives_short_flat_split_zero_volume_and_penny_or_huge_prices(kw):
    assert png_size(chart.render_chart("X", bars(**kw)))[0] == 1080


def test_too_little_history_is_refused_clearly():
    for n in (0, 1, 4):
        with pytest.raises(ValueError):
            chart.render_chart("X", bars(n=n) if n else [])


def test_rendering_from_several_threads_at_once_is_safe():
    out, errs = [], []

    def go(seed):
        try:
            out.append(png_size(chart.render_chart(f"S{seed}", bars(seed=seed))))
        except Exception as e:                                                                         # noqa: BLE001
            errs.append(e)

    threads = [threading.Thread(target=go, args=(i,)) for i in range(5)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errs and out == [(1080, 640)] * 5


def test_the_chart_uses_the_validated_midnight_dawn_palette():
    """The colours must stay the ones validated for the market card (dataviz skill: colour-blind dE 14.5, contrast 5.9/5.1:1)."""
    from alerts import market_card
    assert (chart.UP, chart.DOWN, chart.SURFACE) == (market_card.UP, market_card.DOWN, market_card.SURFACE) == ("#1ea5c4", "#e5626b", "#0a1a3c")
