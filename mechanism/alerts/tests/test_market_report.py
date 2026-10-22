"""The channel's ONE daily post -- the public market brief "First Light ⚡ Market Daily" (owner decisions 2026-10-21/22) -- and
the switches that pause the other recurring channel posts."""
import re
import sys
from datetime import date
from pathlib import Path

import pytest

MECH = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(MECH))
sys.path.insert(0, str(MECH.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from alerts import channel_switches  # noqa: E402
from alerts import market_report as mr  # noqa: E402
from alerts import publish_post_market as ppm  # noqa: E402
from alerts.telegram_client import plain_text, text_length  # noqa: E402
from test_channel_content import BANNED  # noqa: E402
import tg_html  # noqa: E402

SESSION = date(2026, 10, 21)


def _brief(**kw):
    base = dict(session=SESSION, index_pct={"^GSPC": 0.58, "^IXIC": 1.05, "^DJI": 0.69, "^RUT": 0.27}, vix=(16.68, -3.64),
                tnx=(5.146, 2.9), macro_pct={"gold": 0.1, "oil": -2.0, "dollar": 0.2, "btc": 3.0}, up_share=50.4,
                sectors=[("Technology", 1.81), ("Industrials", 1.10), ("Energy", 0.58), ("Healthcare", -0.25),
                         ("Consumer Cyclical", -1.24), ("Consumer Defensive", -1.25)],
                gainers=[("NUAI", 17.7), ("ASST", 13.6), ("ENVX", 12.9)], losers=[("XYZ", -12.0), ("ABC", -8.1)],
                reporting_day=date(2026, 10, 22), reporting=["INTC", "PG", "UNP", "SCCO", "WDC", "BX", "LMT", "VLO"])
    base.update(kw)
    return mr.Brief(**base)


def test_brief_is_one_valid_message_with_every_section():
    text = mr.format_report(_brief())
    assert tg_html.problems(text, 4096) == [] and text_length(text) <= mr.REPORT_LIMIT
    p = plain_text(text)
    for part in ("🔥 NASDAQ LEADS, TECH RIPS", "Wed 21 Oct · US close", "Nasdaq led (+1.1%) while Russell 2000 trailed (+0.3%).",
                 "Stocks were split, roughly half up and half down.", "Tech led (+1.8%); Consumer Staples lagged (−1.2%).",
                 "📊 INDICES", "🟢 S&P 500 +0.58%", "😨 VIX 16.68 (−3.6%)", "🏦 10Y 5.15% (+2.9 bps)",
                 "🏭 SECTORS", "🟢 Tech +1.8% · Industrials +1.1% · Energy +0.6%", "🔴 Consumer Staples −1.2% · Consumer Cyclical −1.2%",
                 "🚀 TOP MOVERS", "🟢 NUAI +17.7%", "🔴 XYZ −12.0%", "🌍 MACRO & CRYPTO", "🛢 Oil −2.0% · 🥇 Gold +0.1%",
                 "₿ Bitcoin +3.0%", "📅 REPORTING Thu 22 Oct", "INTC · PG · UNP", "\nFirst Light ⚡ Market Daily\n",
                 "Educational data. Not advice. Read the pinned message.", "#StockMarket"):
        assert part in p, part


def test_brief_reveals_nothing_about_the_system():
    p = plain_text(mr.format_report(_brief())) + plain_text(mr.caption(_brief()))
    assert not re.search(r"breakout|break out|broke out|near break|list|★|momentum board|liquid|universe|scan|ranked|stocks? (up|down)\b \d", p, re.I)
    assert not re.search(r"\b\d{1,3},\d{3}\b", p)                     # no stock counts (index levels are not printed in the text)
    assert "%" not in p.split("Stocks were split")[1].split(".")[0]    # breadth is said in words, never as a number


def test_brief_wording_follows_the_channel_rules():
    for kw in ({}, {"index_pct": {"^GSPC": -1.4, "^IXIC": -1.9, "^DJI": -1.1, "^RUT": -2.0}, "up_share": 20.0}):
        p = plain_text(mr.format_report(_brief(**kw)))
        assert BANNED.findall(p) == [], BANNED.findall(p)            # never "sell-off", never "earnings"
        assert not re.search(r"investment advice|not a forecast", p, re.I)


@pytest.mark.parametrize("kw,expected", [
    ({"index_pct": {"^GSPC": 1.3, "^IXIC": 1.5, "^DJI": 1.0, "^RUT": 1.2}}, ("🚀", "Big green day on Wall Street")),
    ({"index_pct": {"^GSPC": -1.2, "^IXIC": -1.5, "^DJI": -1.0, "^RUT": -1.2}}, ("🩸", "Heavy red day on Wall Street")),
    ({"index_pct": {"^GSPC": -0.2, "^IXIC": -0.3, "^DJI": -0.1, "^RUT": -0.4}, "vix": (21.0, 12.0)}, ("😨", "Fear gauge jumps")),
    ({"index_pct": {"^GSPC": 0.5, "^IXIC": 0.6, "^DJI": 0.4, "^RUT": 0.7}, "up_share": 70.0}, ("🟢", "Green across the board")),
    ({"index_pct": {"^GSPC": 0.2, "^IXIC": 0.1, "^DJI": 0.2, "^RUT": 0.9}}, ("🐂", "Small caps run")),
    ({"index_pct": {"^GSPC": 0.05, "^IXIC": 0.0, "^DJI": 0.1, "^RUT": 0.0}, "vix": (16.0, 1.0)}, ("😐", "Flat close")),
    ({"index_pct": {"^GSPC": -0.4, "^IXIC": -0.5, "^DJI": -0.3, "^RUT": -0.6}, "up_share": 45.0, "vix": (16.0, 2.0)}, ("📉", "Stocks slip")),
    ({"index_pct": {}}, ("📊", "US market close")),
])
def test_headline_rules(kw, expected):
    assert mr.headline(_brief(**kw)) == expected


def test_missing_ingredients_are_left_out_never_invented():
    p = plain_text(mr.format_report(_brief(vix=None, tnx=None, macro_pct={}, gainers=[], losers=[], reporting=[], sectors=[], up_share=None)))
    for gone in ("VIX", "10Y", "MACRO", "TOP MOVERS", "REPORTING", "SECTORS", "Most US stocks", "split"):
        assert gone not in p, gone
    assert "📊 INDICES" in p and "🟢 S&P 500 +0.58%" in p


def test_caption_is_the_headline_and_the_two_main_indices():
    c = mr.caption(_brief())
    assert plain_text(c) == "🔥 Nasdaq leads, tech rips\nWed 21 Oct · S&P 500 +0.58% · Nasdaq +1.05%\nFull brief below 👇"
    assert text_length(c) <= 1024
    assert mr.card_headline(_brief()) == "Nasdaq leads, tech rips"


# ------------------------------------------------------------------ publishing format and switches
def test_post_market_format_defaults_to_the_one_report(monkeypatch):
    monkeypatch.delenv("POST_MARKET_FORMAT", raising=False)
    assert ppm.post_market_format() == "report" and list(ppm.kinds_for("report")) == ["market_report"]
    monkeypatch.setenv("POST_MARKET_FORMAT", "package")
    assert list(ppm.kinds_for(ppm.post_market_format())) == ["daily_digest", "momentum_board", "top_gainers", "market_health"]
    monkeypatch.setenv("POST_MARKET_FORMAT", "nonsense")
    assert ppm.post_market_format() == "report"


def test_switch_defaults_keep_only_the_report_and_one_assistant_post(monkeypatch):
    for k in ("CHANNEL_NOTICE_SLOTS", "CHANNEL_EARNINGS_POST_ENABLED", "CHANNEL_EXTRA_POSTS_ENABLED"):
        monkeypatch.delenv(k, raising=False)
    assert channel_switches.notice_slots() == {2}
    assert not channel_switches.earnings_post_enabled() and not channel_switches.extra_posts_enabled()
    monkeypatch.setenv("CHANNEL_NOTICE_SLOTS", "1, 2")
    monkeypatch.setenv("CHANNEL_EARNINGS_POST_ENABLED", "1")
    monkeypatch.setenv("CHANNEL_EXTRA_POSTS_ENABLED", "1")
    assert channel_switches.notice_slots() == {1, 2}
    assert channel_switches.earnings_post_enabled() and channel_switches.extra_posts_enabled()


def test_publish_dry_run_in_report_mode_attempts_only_the_report(monkeypatch, capsys):
    monkeypatch.delenv("POST_MARKET_FORMAT", raising=False)
    monkeypatch.setattr(ppm.post_delivery, "already_sent", lambda db, s, kind, to: False)
    assert ppm.publish(SESSION, "prod", send=False, image=True, buttons=True, force=False) == 0
    out = capsys.readouterr().out
    assert "market_report: would attempt" in out and "daily_digest" not in out


@pytest.mark.parametrize("script,argv,env", [
    ("send_earnings_today", ["--send", "--to", "dev"], {}),
    ("send_channel_notices", ["--slot", "1", "--send", "--to", "dev"], {}),
    ("send_channel_posts", ["--send", "--to", "dev"], {}),
])
def test_paused_posts_send_nothing_and_never_touch_telegram(script, argv, env, monkeypatch, capsys):
    import importlib
    mod = importlib.import_module(f"alerts.{script}")
    for k in ("CHANNEL_NOTICE_SLOTS", "CHANNEL_EARNINGS_POST_ENABLED", "CHANNEL_EXTRA_POSTS_ENABLED"):
        monkeypatch.delenv(k, raising=False)

    def boom(*a, **k):
        raise AssertionError("a paused post must not create a Telegram client")
    monkeypatch.setattr(mod.TelegramClient, "from_env", classmethod(lambda cls, *a, **k: boom()))
    if script == "send_channel_notices":
        monkeypatch.setattr(mod, "already_sent", lambda *a, **k: False)
    monkeypatch.setattr(sys, "argv", [script, *argv])
    assert mod.main() == 0
    assert "paused" in capsys.readouterr().out


def test_a_move_that_rounds_to_zero_is_flat_never_minus_zero():
    p = plain_text(mr.format_report(_brief(macro_pct={"dollar": -0.004})))
    assert "💵 Dollar 0.0%" in p and "−0.0%" not in p


# ------------------------------------------------------------------ the public brand's pinned post and assistant posts
def test_public_pinned_post_keeps_the_disclaimers_and_reveals_nothing_about_the_system():
    from alerts import channel_posts as cp
    t = cp.START_HERE_PUBLIC
    assert tg_html.problems(t, 4096) == [] and text_length(t) <= 4000
    p = plain_text(t)
    assert re.search(r"not investment advice", p, re.I) and "First Light · Market Daily" in p
    assert not re.search(r"breakout|near.high|\blists?\b|★|momentum board|scan|2,9\d\d|ranked by a formula", p, re.I)   # "watchlist" is fine
    assert BANNED.findall(re.sub(r"not investment advice|Not investment advice", "", p)) == []


def test_assistant_posts_reveal_nothing_about_the_system():
    from alerts import channel_content as cx
    for body in cx.ASSISTANT_POSTS:
        p = plain_text(body)
        assert not re.search(r"breakout|scan|\blists?\b|/screen|vol\s*>", p, re.I), p
        assert BANNED.findall(p) == [], p


def test_assistant_post_carries_its_picture_and_the_artwork_copy_is_clean():
    from alerts import brand_assets as ba
    from alerts import channel_content as cx
    for i in range(len(cx.ASSISTANT_POSTS)):
        post = cx.post_assistant(cx.Ctx(session=SESSION, notice_index=i))
        assert post.image and post.image[:8] == b"\x89PNG\r\n\x1a\n" and post.button
        assert text_length(post.text) <= cx.CAPTION_LIMIT
    words = " ".join([ba.BRAND_COPY["brand"], ba.BRAND_COPY["pill"], ba.BRAND_COPY["cta"], ba.BRAND_COPY["foot"], *ba.BRAND_COPY["mock"]]
                     + [v for p in ba.BRAND_COPY["posts"] for v in p.values()])
    assert BANNED.findall(words) == [], BANNED.findall(words)
    assert not re.search(r"breakout|\bscan\b|\blists?\b|israel|jerusalem", words, re.I)
    assert len(ba.BRAND_COPY["posts"]) == len(cx.ASSISTANT_POSTS)
    for png in (ba.render_channel_avatar(), ba.render_bot_avatar()):
        assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_movers_are_the_momentum_boards_breakout_list_in_the_boards_order():
    from alerts import digest_builder as dbld
    from alerts.send_daily_digest import board_breakouts

    def r(sym, ret, atr, vol, cat="breakout"):
        return {"symbol": sym, "cat": cat, "dv20": 3e7, "ret1_pct": ret, "range_atr": atr, "rvol": vol, "close": 10.0, "new": False,
                "prev_cat": None, "below_high_pct": None}
    rows = [r("BIG", 9.0, 1.0, 1.0), r("STAR", 3.0, 9.0, 9.0), r("MID", 5.0, 2.0, 2.0), r("NEAR", 20.0, 9.5, 9.5, "near_breakout")]
    # fillers that each make only ONE list, so STAR is the only stock in the top 5 of two or more lists
    rows += [r(f"A{i}", -0.5, 5.0, 0.1) for i in range(6)] + [r(f"V{i}", -0.5, 0.1, 5.0) for i in range(6)]
    rows += [r(f"G{i}", 1.0, 0.1, 0.1) for i in range(3)]
    d = dbld.build_digest(rows, top_n=15)
    got = [x["symbol"] for x in board_breakouts(d, 3)]
    assert got == ["STAR", "BIG", "MID"]          # top 5 in 2+ lists first, then the 1-day gain; a near breakout never appears
