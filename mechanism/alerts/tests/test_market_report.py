"""The ONE daily market report (owner decision 2026-10-21) and the switches that pause the other recurring channel posts."""
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
from alerts import digest_builder as dbld  # noqa: E402
from alerts import market_report as mr  # noqa: E402
from alerts import publish_post_market as ppm  # noqa: E402
from alerts.telegram_client import plain_text, text_length  # noqa: E402
from test_channel_content import BANNED  # noqa: E402
import tg_html  # noqa: E402

SESSION = date(2026, 10, 20)


def _row(sym, cat, ret, atr, vol, dv=3e7):
    return {"symbol": sym, "cat": cat, "dv20": dv, "ret1_pct": ret, "range_atr": atr, "rvol": vol, "close": 20.0 + ret,
            "new": cat == "near_breakout", "prev_cat": None, "below_high_pct": 1.0 if cat == "near_breakout" else None}


def _digest(n=40):
    rows = [_row(f"B{i:02d}", "breakout", 50 - i, 10 - i * 0.2, 30 - i * 0.5) for i in range(n)]
    rows += [_row(f"N{i:02d}", "near_breakout", 14 - i * 0.3, 4 - i * 0.05, 5 - i * 0.1) for i in range(n)]
    return dbld.build_digest(rows, top_n=15)


def _reporters(n):
    return [{"symbol": f"E{i:03d}", "close": 10.0, "sector": "Tech", "eps_estimate": 1.0} for i in range(n)]


def _board():
    top = [{"symbol": f"M{i}", "close": 10.0, "ret1_pct": 9 - i, "listed_sessions": 1} for i in range(3)]
    more = [{"symbol": f"M{i}", "close": 10.0, "ret1_pct": 5 - i * 0.5} for i in range(3, 8)]
    return {"session": SESSION, "n_sessions": 5, "n_measured": 269, "higher": 205, "lower": 59, "at_day_high": 46, "top": top, "more": more}


def _report(**kw):
    base = dict(sp500_pct=0.3, health_lines=["Above 50-day average: 35% (10 sessions ago: 29%)", "Above 200-day average: 48%",
                                             "52-week highs 85 · lows 86"],
                top_gainers=[{"symbol": f"G{i}", "close": 5.0, "ret1_pct": 30 - i} for i in range(10)], board=_board(),
                earnings_day=date(2026, 10, 21), earnings_rows=_reporters(116), universe_n=2897)
    base.update(kw)
    return mr.format_report(SESSION, _digest(), "Broad rally. 77% of stocks up.", **base)


def _plain(t):
    return plain_text(t)                          # what Telegram shows: tags removed, entities unescaped


def test_report_carries_every_section_in_one_valid_message():
    text = _report()
    assert tg_html.problems(text, 4096) == [] and text_length(text) <= mr.REPORT_LIMIT
    p = _plain(text)
    for part in ("Broad rally. 77% of stocks up.", "Tue 20 Oct · S&P 500 ▲0.3%", "Above 50-day average: 35%", "52-week highs 85",
                 "40 broke out · 40 within 3% of it", "★ Top 5 in 2+ lists", "BREAKOUT · 40", "NEAR BREAKOUT · 40",
                 "Gainers:", "Range:", "Volume:", "TOP GAINERS of 2,897 liquid stocks", "MOMENTUM BOARD", "REPORTING Wed 21 Oct · 116 companies",
                 "Educational data. Not advice. Read the pinned message.", "Access on request · seats limited"):
        assert part in p, part
    # three names inline per list, the rest collapsed
    assert "Gainers: B00 ▲50.0% · B01 ▲49.0% · B02 ▲48.0%" in p
    assert "<blockquote expandable>4. <b>" in text


def test_report_wording_follows_the_channel_rules():
    p = _plain(_report())
    assert BANNED.findall(p) == [], BANNED.findall(p)                  # e.g. never "earnings": the section says REPORTING
    assert not re.search(r"investment advice|not a forecast|say nothing about", p, re.I)


def test_report_shortens_only_the_collapsed_parts_when_it_would_not_fit():
    full = _report()
    tight = _report(earnings_rows=_reporters(900))
    assert text_length(tight) <= mr.REPORT_LIMIT
    first_lines = lambda t: [ln for ln in _plain(t).split("\n") if ln.startswith(("Gainers:", "Range:", "Volume:"))]
    assert first_lines(full) == first_lines(tight)                     # the visible names never change
    assert "more" in _plain(tight)                                     # the reporters list says how many it left out


def test_report_omits_a_missing_ingredient_instead_of_inventing_it():
    p = _plain(_report(board=None, earnings_rows=[], top_gainers=[], sp500_pct=None, health_lines=[]))
    assert "MOMENTUM BOARD" not in p and "REPORTING" not in p and "TOP GAINERS" not in p and "S&P 500" not in p
    assert "BREAKOUT · 40" in p


def test_caption_is_short_and_points_to_the_report():
    c = mr.report_caption(SESSION, _digest(), "Broad rally. 77% of stocks up.")
    assert _plain(c) == "Broad rally. 77% of stocks up.\nTue 20 Oct · 40 broke out · 40 within 3% of it\nFull report below."
    assert text_length(c) <= 1024


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
