"""The one-line disclaimer, the twice-daily notices (disclaimer + assistant post), the pinned Start-here copy and the board's candlesticks.
No database, no network."""
import io
import os
import re
import sys
from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))
sys.path.insert(0, ROOT)

import tg_html  # noqa: E402
from PIL import Image  # noqa: E402
from alerts import board as bd  # noqa: E402
from alerts import channel_cards as cc  # noqa: E402
from alerts import channel_content as cx  # noqa: E402
from alerts import channel_posts as cp  # noqa: E402
from alerts import digest_builder as db_  # noqa: E402
from alerts import digest_format as fmt  # noqa: E402
from alerts import send_channel_notices as sn  # noqa: E402
from alerts import texts  # noqa: E402
from alerts.market_stats import Wide  # noqa: E402
from alerts.telegram_client import text_length  # noqa: E402
from test_board import BANNED, DATES, PRIOR, SESSION, listed, make_wide  # noqa: E402
from test_channel_content import ALL, _ctx  # noqa: E402

ONE_LINE = "* Not investment advice."


def _plain(text):
    return re.sub(r"<[^>]+>", "", text)


# ================================================================== the one-line disclaimer
def test_the_one_line_disclaimer_is_the_documented_text():
    assert texts.DISCLAIMER_ONE_LINE == ONE_LINE


@pytest.mark.parametrize("kind", [k for k in ALL if k != "disclaimer"])
def test_no_channel_post_carries_a_disclaimer_of_its_own(kind):
    """The disclaimers moved to the pinned post. Only the dedicated notice (kind 'disclaimer') repeats one."""
    plain = _plain(cx.build_post(kind, _ctx()).text)
    for phrase in ("Not investment advice", "investment advice", "suggestion to trade", "own decisions", "survivor bias", "not a forecast",
                   "say nothing about", "not independent tests", "We do not write or edit"):
        assert phrase not in plain, (kind, phrase)


def test_the_board_the_digest_caption_and_the_digest_header_carry_no_disclaimer_but_point_to_the_pinned_message():
    board = bd.compute(listed("UP1", sessions=PRIOR), make_wide({"UP1": 5.0}), SESSION)
    assert "advice" not in cx.post_board(cx.Ctx(session=SESSION, board=board)).text
    d = db_.build_digest([], top_n=5)
    caption = fmt.format_caption(date(2026, 9, 18), d)
    assert "\n" not in caption and "advice" not in caption
    header = fmt.format_header(date(2026, 9, 18), datetime(2026, 9, 19, 6, 0), d, 3000, {"up": 1, "down": 1})
    assert "New here? Read the pinned message." in header and "advice" not in header and "suggestion to trade" not in header


def test_no_channel_image_carries_a_disclaimer_or_one_of_the_moved_notes():
    """Structural: the picture code has no footer line and none of the caveats that now live in the pinned post."""
    from alerts import market_card as mcard
    assert not hasattr(mcard, "DISCLAIMER_LINE") and not hasattr(cc, "DISCLAIMER_LINE")
    for name in ("market_card.py", "channel_cards.py"):
        src = open(os.path.join(ROOT, "mechanism", "alerts", name), encoding="utf-8").read()
        for phrase in ("advice", "Educational information", "Sector tags are today", "front-month futures", "say nothing about later ones",
                       "too short a history"):
            assert phrase not in src, (name, phrase)


# ================================================================== the disclaimer notice
def test_the_disclaimer_notice_is_short_valid_and_points_to_the_pinned_message():
    p = cx.build_post("disclaimer", _ctx())
    assert p.kind == "disclaimer" and p.image is None and p.button is False
    assert tg_html.problems(p.text) == [] and BANNED.findall(_plain(p.text)) == []
    plain = _plain(p.text)
    assert plain.count("Not investment advice") == 1 and plain.startswith("Educational data. Not investment advice.")
    assert "Read the pinned message" in plain and "not what happens next" in plain
    assert len(plain) < 250                                                             # a notice, not a wall of text


# ================================================================== the assistant post
@pytest.mark.parametrize("i", range(len(cx.ASSISTANT_POSTS)))
def test_every_assistant_variant_is_clean_says_what_access_is_and_has_the_button(i):
    p = cx.build_post("assistant", cx.Ctx(session=date(2026, 9, 21), notice_index=i))
    plain = _plain(p.text)
    assert p.button is True and p.image is None and tg_html.problems(p.text) == []
    assert BANNED.findall(plain) == [] and "advice" not in plain                            # no disclaimer of its own: the pinned post has it
    assert "Access on request" in plain and "seats limited" in plain and "beta" not in plain.lower()
    assert not re.search(r"\$\d|free forever|sign ?up|subscribe", plain, re.I)                # no price before a paid tier exists
    assert not re.search(r"\d+\s?%\s+(gain|return|profit|win)", plain, re.I)
    assert text_length(p.text) < 700


def test_the_assistant_variants_differ_and_consecutive_notices_never_repeat_one():
    texts_ = {cx.build_post("assistant", cx.Ctx(session=date(2026, 9, 21), notice_index=i)).text for i in range(len(cx.ASSISTANT_POSTS))}
    assert len(texts_) == len(cx.ASSISTANT_POSTS)
    seq = [sn.variant_index(date(2026, 9, 21).replace(day=d), s) % len(cx.ASSISTANT_POSTS) for d in range(21, 30) for s in (1, 2)]
    assert all(a != b for a, b in zip(seq, seq[1:])) and set(seq) == set(range(len(cx.ASSISTANT_POSTS)))


# ================================================================== the sender
def test_build_posts_gives_the_disclaimer_first_then_the_assistant_post():
    posts = sn.build_posts(date(2026, 9, 22), 1)
    assert [p.kind for p in posts] == ["disclaimer", "assistant"]


def test_the_disclaimer_notice_goes_out_once_a_day_and_the_assistant_post_in_every_slot():
    assert [p.kind for p in sn.build_posts(date(2026, 9, 22), 2)] == ["assistant"]
    assert [p.kind for p in sn.build_posts(date(2026, 9, 22), 1)][-1] == "assistant"


def test_a_slot_is_sent_once_per_target_and_day(tmp_path):
    f = tmp_path / "state.json"
    d = date(2026, 9, 22)
    assert not sn.already_sent("dev", d, 1, f)
    sn.mark_sent("dev", d, 1, f)
    assert sn.already_sent("dev", d, 1, f)
    assert not sn.already_sent("dev", d, 2, f) and not sn.already_sent("prod", d, 1, f) and not sn.already_sent("dev", date(2026, 9, 23), 1, f)


def test_old_state_is_pruned_and_a_corrupt_state_file_is_treated_as_empty(tmp_path):
    f = tmp_path / "state.json"
    sn.mark_sent("dev", date(2026, 8, 1), 1, f)
    sn.mark_sent("dev", date(2026, 9, 22), 2, f)
    assert not sn.already_sent("dev", date(2026, 8, 1), 1, f) and sn.already_sent("dev", date(2026, 9, 22), 2, f)
    f.write_text("{not json", encoding="utf-8")
    assert not sn.already_sent("dev", date(2026, 9, 22), 2, f)
    sn.mark_sent("dev", date(2026, 9, 22), 1, f)                                        # and it recovers by rewriting the file
    assert sn.already_sent("dev", date(2026, 9, 22), 1, f)


def test_the_sender_is_dry_run_by_default_goes_through_the_production_lock_and_skips_a_repeat():
    src = open(os.path.join(ROOT, "mechanism", "alerts", "send_channel_notices.py"), encoding="utf-8").read()
    assert "TelegramClient.from_env(args.to, dry_run=False)" in src                    # the lock lives inside from_env
    assert "if not args.send:" in src and "DRY RUN" in src
    assert "if already_sent(args.to, day, args.slot) and not args.force:" in src
    assert src.index("if already_sent(") < src.index("TelegramClient.from_env(")       # the repeat check comes before any network call
    assert "silent=True" in src


def test_the_notice_script_blocks_production_before_it_runs_anything():
    ps1 = open(os.path.join(ROOT, "run_first_light_notice.ps1"), encoding="utf-8").read()
    assert ps1.index("PROD_SENDING_ENABLED") < ps1.index("$pyArgs = @('mechanism/alerts/send_channel_notices.py'") and "exit 4" in ps1
    assert "if ($Send) { $pyArgs += '--send' }" in ps1                                  # preview unless -Send is given


# ================================================================== the pinned Start-here post
def test_start_here_is_plain_language_and_holds_the_general_disclaimer():
    plain = _plain(cp.START_HERE)
    for needle in ("not investment advice", "not a suggestion to trade any security", "risk", "what already happened", "does not say what happens next",
                   "delays or mistakes", "Everyone makes their own decisions", "Nothing here is tailored to you"):
        assert needle in plain, needle
    assert plain.rstrip().endswith(ONE_LINE)
    for jargon in ("Donchian", "Bollinger", "MACD", "SMA", "XGBoost", "RSI", "stop loss"):
        assert jargon not in plain
    assert text_length(cp.START_HERE) <= 4000 and tg_html.problems(cp.START_HERE) == []      # parsed length with a margin under Telegram's 4096


def test_start_here_explains_that_the_data_is_end_of_day_and_not_live():
    plain = _plain(cp.START_HERE)
    assert "End of day" in plain and "official US market close" in plain and "Nothing is live" in plain


# ================================================================== every service has its disclaimer in the pinned post
def test_every_kind_of_channel_post_has_a_note_in_the_pinned_post():
    """A post kind without a note would go out with no disclaimer anywhere. The digest's own lists count as the kind 'digest'."""
    data_kinds = {k for k in cx.KINDS if k not in ("promo", "disclaimer")} | {"digest"}
    assert data_kinds <= set(cp.NOTE_FOR_KIND), sorted(data_kinds - set(cp.NOTE_FOR_KIND))
    keys = {key for key, _t, _n in cp.SERVICE_NOTES}
    assert set(cp.NOTE_FOR_KIND.values()) <= keys and len(keys) == len(cp.SERVICE_NOTES)
    for key, title, note in cp.SERVICE_NOTES:
        assert title in _plain(cp.START_HERE) and note.split(" ")[0] in _plain(cp.START_HERE), key


def test_the_service_notes_are_in_a_collapsed_quote_and_are_clean():
    assert "<blockquote expandable><b>Daily lists" in cp.START_HERE
    notes = " ".join(n for _k, _t, n in cp.SERVICE_NOTES)
    assert BANNED.findall(notes) == [] and len(cp.SERVICE_NOTES) >= 11


@pytest.mark.parametrize("key, phrase", [
    ("lists", "not a forecast"), ("board", "Earlier lists say nothing about later ones"), ("health", "does not predict tomorrow"),
    ("sector", "Sector tags are today's"), ("macro", "front-month futures"), ("gaps", "options-expiry"),
    ("aligned", "not counted"), ("base_rate", "survivor bias"), ("recap", "not independent tests"), ("news", "do not write or edit"),
    ("assistant", "educational reference")])
def test_each_caveat_that_used_to_sit_in_a_post_is_in_its_service_note(key, phrase):
    note = next(n for k, _t, n in cp.SERVICE_NOTES if k == key)
    assert phrase in note


# ================================================================== the board's candlesticks
def _wide_with_open(moves):
    w = make_wide(moves)
    w.open.loc[:, :] = w.close.shift(1).fillna(100.0) * 1.001                             # opens a hair above the previous close: real bodies
    w.high.loc[:, :] = np.maximum(w.high.to_numpy(), w.open.to_numpy())                    # a consistent bar: its range contains its own open
    w.low.loc[:, :] = np.minimum(w.low.to_numpy(), w.open.to_numpy())
    return w


def test_board_rows_carry_one_ohlc_bar_per_chart_session():
    b = bd.compute(listed("UP1", sessions=PRIOR), _wide_with_open({"UP1": 5.0}), SESSION)
    bars = b["top"][0]["ohlc"]
    assert len(bars) == bd.CHART_SESSIONS and all(x is not None for x in bars)
    o, h, l, c = bars[-1]
    assert c == pytest.approx(105.0) and h >= max(o, c) and l <= min(o, c)


def test_an_inconsistent_or_missing_bar_is_left_out_never_repaired():
    assert bd._bar(100, 101, 99, 100.5) == (100.0, 101.0, 99.0, 100.5)
    assert bd._bar(100, 99, 98, 100.5) is None                                           # high below the close
    assert bd._bar(100, 101, 100.2, 100.5) is None                                       # low above the open
    assert bd._bar(np.nan, 101, 99, 100) is None and bd._bar(100, 101, 99, None) is None and bd._bar(0, 1, 0, 1) is None


def _count(img, rgb, tol=28):
    a = np.asarray(img.convert("RGB")).astype(int)
    return int((np.abs(a - np.array(rgb)).sum(axis=2) <= tol).sum())


def test_the_leader_chart_is_candlesticks_aqua_for_up_and_coral_for_down():
    from alerts.market_card import DOWN, UP
    hexrgb = lambda h: tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))  # noqa: E731
    bars = [(100, 103, 99, 102), (102, 104, 100, 101), (101, 102, 97, 98), (98, 101, 97, 100), (100, 106, 99, 105),
            (105, 108, 104, 107), (107, 109, 103, 104), (104, 108, 103, 107), (107, 111, 106, 110), (110, 115, 109, 114)]
    r = {"symbol": "AAA", "close": 114.0, "ret1_pct": 3.6, "listed_sessions": 2, "first_listed": DATES[-4].date(), "rvol": 3.0,
         "at_day_high": True, "above_20d_high": True, "closes": [b[3] for b in bars], "dates": [d.date() for d in DATES[-10:]], "ohlc": bars}
    b = {"session": SESSION, "n_sessions": 5, "n_pool": 10, "n_measured": 10, "n_excluded": 0, "higher": 6, "lower": 4, "flat": 0,
         "at_day_high": 2, "above_20d_high": 1, "top": [r], "more": []}
    img = Image.open(io.BytesIO(cc.render_board_card(b)))
    chart = img.crop((520, 620, 1040, 980))                                               # the leader's chart area
    assert _count(chart, hexrgb(UP)) > 400 and _count(chart, hexrgb(DOWN)) > 250        # both colours are drawn as solid bodies
    all_up = [(o, h, l, c) if c >= o else (c, h, l, o) for o, h, l, c in bars]              # same bars, every candle up: no coral left
    r2 = {**r, "ohlc": all_up}
    img2 = Image.open(io.BytesIO(cc.render_board_card({**b, "top": [r2]}))).crop((520, 620, 1040, 980))
    assert _count(img2, hexrgb(DOWN)) < 40


def test_missing_bars_leave_gaps_and_a_chart_with_fewer_than_two_bars_draws_nothing():
    b = bd.compute(listed("UP1", "UP2", sessions=PRIOR), _wide_with_open({"UP1": 5.0, "UP2": 3.0}), SESSION)
    b["top"][0]["ohlc"][3] = None                                                        # one bar missing
    assert Image.open(io.BytesIO(cc.render_board_card(b))).size[0] == 1080
    b["top"][0]["ohlc"] = [None] * (bd.CHART_SESSIONS - 1) + [b["top"][0]["ohlc"][-1]]     # a single bar: no chart, and no crash
    b["top"][1]["ohlc"] = [None] * bd.CHART_SESSIONS
    assert Image.open(io.BytesIO(cc.render_board_card(b))).size[0] == 1080
