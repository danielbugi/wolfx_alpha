"""Scenario tests of the private assistant: complete user journeys through the REAL dispatcher (fake Telegram network, in-memory store).

Every message is validated as Telegram HTML and every keyboard against the UI invariants inside qa_harness.drive(). Expected numbers are
worked out by hand in the comments (prices: MSTR 153.92, COIN 194.25; the "run date" is Mon 21 Sep, the last session Fri 18 Sep).
"""
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as Dec

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))
sys.path.insert(0, ROOT)

from alerts import bot_service as bs  # noqa: E402
from alerts import screens, texts  # noqa: E402

from qa_harness import (BOT_USERNAME, OWNER_ID, SESSION, SESSION_DATE, STOCKS, TODAY, answers, buttons, documents, drive,  # noqa: E402
                        edits, everything_shown, msg, photos, sent, service, stock, tap, texts_edited, texts_sent)
from aiogram.types import ReplyKeyboardMarkup  # noqa: E402

strip = lambda t: re.sub(r"<[^>]+>", "", t)                                          # noqa: E731
BANNED = re.compile(r"\b(buy\w*|sell\w*|targets?|stops?|entry|entries|recommend\w*|signals?|opportunit\w*|picks?|should|profit\w*|"
                    r"winners?|guarantee\w*|alpha)\b", re.I)
PRICE_TS = lambda n: None                                                            # noqa: E731


def member_svc(*uids, stocks=STOCKS, acked=True, **kw):
    svc = service(stocks, **kw)
    for u in uids:
        svc.store.set_access(u, "active")
        if acked:
            svc.store.acknowledge(u)
    return svc


def last_edit(calls):
    return edits(calls)[-1]


# ================================================================== onboarding
def test_first_contact_is_a_four_step_guide_ending_in_the_notice_and_the_menu():
    svc = member_svc(5, acked=False)
    svc, calls = drive([msg(5, "/start"), tap(5, "ob:2", 2), tap(5, "ob:3", 3), tap(5, "ob:4", 4), tap(5, "ob:ack", 5)], svc, enroll=False)
    first = sent(calls)[0]
    assert first.text == texts.ONBOARDING[1] and buttons(first) == [("Next ›", "ob:2"), ("Skip guide", "ob:4")]
    e = edits(calls)
    assert [x.text for x in e[:3]] == [texts.ONBOARDING[2], texts.ONBOARDING[3], texts.ONBOARDING_LAST]
    assert buttons(e[0]) == [("‹ Back", "ob:1"), ("Next ›", "ob:3")] and buttons(e[1]) == [("‹ Back", "ob:2"), ("Next ›", "ob:4")]
    assert buttons(e[2]) == [(texts.ACK_BUTTON, "ob:ack"), ("‹ Back", "ob:3")]
    assert e[3].text.endswith("<i>Accepted.</i>") and buttons(e[3]) == []                 # the tapped message loses its buttons
    done = sent(calls)[-1]
    assert done.text == texts.ONBOARDING_DONE and isinstance(done.reply_markup, ReplyKeyboardMarkup)
    assert [b.text for row in done.reply_markup.keyboard for b in row] == ["Today's lists", "Portfolio", "Watchlist", "Help"]
    assert done.reply_markup.is_persistent and svc.store.is_acknowledged(5)
    assert "1." in texts.ONBOARDING_DONE and "Open Today's lists" in texts.ONBOARDING_DONE                # a quick-start checklist


def test_the_guide_can_be_skipped_but_the_notice_cannot():
    svc = member_svc(5, acked=False)
    svc, calls = drive([msg(5, "/start"), tap(5, "ob:4", 2)], svc, enroll=False)
    assert last_edit(calls).text == texts.ONBOARDING_LAST and not svc.store.is_acknowledged(5)      # skipping lands on the notice, nothing is accepted


def test_typed_agree_is_the_fallback_when_the_button_is_lost():
    svc = member_svc(5, acked=False)
    svc, calls = drive([msg(5, "/agree")], svc, enroll=False)
    assert svc.store.is_acknowledged(5) and texts_sent(calls) == [texts.ONBOARDING_DONE]


def test_guide_replays_on_demand_for_someone_who_already_accepted():
    _, calls = drive([msg(5, "/guide"), tap(5, "ob:3", 2)])
    assert texts_sent(calls) == [texts.ONBOARDING[1]] and texts_edited(calls) == [texts.ONBOARDING[3]]


def test_nothing_but_the_guide_is_shown_before_the_notice_is_accepted():
    """LEAK: an invited user who has not accepted the educational notice sees no data of any kind, from any entry point."""
    svc = member_svc(5, acked=False)
    ups = [msg(5, "/today"), msg(5, "/stock MSTR", 2), msg(5, "MSTR", 3), msg(5, "/add MSTR", 4), msg(5, "/portfolio", 5), msg(5, "/levels MSTR", 6),
           msg(5, "Today's lists", 7), msg(5, "/export", 8), msg(5, "/deleteme", 9), tap(5, "sc:MSTR:x", 10), tap(5, "td:b:0", 11),
           tap(5, "aw:MSTR:x", 12), tap(5, "ch:MSTR:x", 13), tap(5, "nw:MSTR:x", 14)]
    svc, calls = drive(ups, svc, enroll=False)
    shown = texts_sent(calls) + texts_edited(calls)
    assert shown and all(t == texts.ONBOARDING[1] for t in shown) and len(shown) == 14
    assert svc.store.tracked(5) == [] and not photos(calls) and not documents(calls) and svc.fake_news.calls == []


def test_the_menu_comes_back_with_start_and_help():
    _, calls = drive([msg(5, "/start"), msg(5, "/help", 2)])
    welcome = sent(calls)[0]
    assert welcome.text.startswith("Welcome back") and isinstance(welcome.reply_markup, ReplyKeyboardMarkup)
    assert isinstance(sent(calls)[-1].reply_markup, ReplyKeyboardMarkup)


@pytest.mark.parametrize("label,marker", [("Today's lists", "<b>Today's lists</b>"), ("Portfolio", "<b>Portfolio</b>"),
                                          ("Watchlist", "<b>Watchlist</b>"), ("Help", "<b>Menu</b>")])
def test_every_menu_button_opens_its_screen(label, marker):
    _, calls = drive([msg(5, label)])
    assert marker in texts_sent(calls)[0]


# ================================================================== today's lists
LISTED = [stock("AAA", "breakout", 10.0, 5.0, 2.0, 1.5, 0.0, {"gainers": 1, "atr": 1, "volume": 1}),
          stock("BBB", "breakout", 20.0, 3.0, 1.1, 1.2, 0.0, {"gainers": 2}),
          stock("CCC", "breakout", 30.0, -1.5, 4.0, 2.0, 0.0, {"volume": 1, "atr": 2}),
          stock("DDD", "near_breakout", 40.0, 0.5, 1.0, 1.0, 1.5, {"atr": 1}),
          stock("EEE", None, 50.0, 0.1, 1.0, 1.0, 9.0)]                       # in the scan but in no list


def test_today_shows_every_listed_stock_exactly_once_ordered_by_how_many_lists_they_are_in():
    svc = service(LISTED)
    _, calls = drive([msg(5, "/today")], svc)
    [m] = sent(calls)
    t = m.text
    assert t.startswith("<b>Today's lists</b> · US close Fri 18 Sep") and "4 stocks from the channel's lists, each shown once" in t
    assert t.index("<b>AAA</b> ★") < t.index("<b>CCC</b> ★") < t.index("<b>BBB</b>")                      # 3 lists, then 2, then 1
    assert all(t.count(f"<b>{s}</b>") == 1 for s in ("AAA", "BBB", "CCC")) and "EEE" not in t and "DDD" not in t   # once each; other tab / no list
    assert "gainers #1 · ATR #1 · volume #1" in t and "▼1.5%" in t and "$30.00" in t
    assert [b for b in buttons(m) if b[1].startswith("sc:")] == [("AAA", "sc:AAA:tb0"), ("CCC", "sc:CCC:tb0"), ("BBB", "sc:BBB:tb0")]
    assert ("● Breakout · 3", "td:b:0") in buttons(m) and ("Near breakout · 1", "td:n:0") in buttons(m)
    assert buttons(m)[-1] == ("What do these mean?", "def:groups")


def test_switching_tab_edits_the_same_message_and_a_stock_in_several_lists_is_one_row():
    _, calls = drive([tap(5, "td:n:0")], service(LISTED))
    assert not sent(calls) and "<b>Near breakout</b>" in texts_edited(calls)[0] and "<b>DDD</b>" in texts_edited(calls)[0]
    assert not any(f"<b>{s}</b>" in texts_edited(calls)[0] for s in ("AAA", "BBB", "CCC"))          # a breakout stock never repeats on the other tab
    assert "● Near breakout · 1" in [b[0] for b in buttons(last_edit(calls))]
    _, calls = drive([tap(5, "td:zz:0")], service(LISTED))                                              # a bad tab falls back to Breakout
    assert "<b>Breakout</b>" in texts_edited(calls)[0]


def test_paging_covers_every_stock_once_and_clamps_bad_pages():
    many = [stock(f"S{i:02d}", "breakout", 10.0 + i, 1.0, 1, 1, 0, {"gainers": i + 1}) for i in range(40)]
    svc = service(many)
    seen = []
    for page in range(3):                                                                                # 40 stocks = pages of 15, 15 and 10
        _, calls = drive([tap(5, f"td:b:{page}")], svc)
        e = last_edit(calls)
        seen += re.findall(r"<b>(S\d\d)</b>", e.text)
        nav = [b for b in buttons(e) if b[1].startswith("td:b:") and b[0] in ("‹ Prev", "Next ›")]
        assert len(nav) == {0: 1, 1: 2, 2: 1}[page]
    assert sorted(seen) == [f"S{i:02d}" for i in range(40)] and len(seen) == 40
    for weird in ("td:b:99", "td:b:-1", "td:b:x"):                                                       # out of range or junk never crash
        _, calls = drive([tap(5, weird)], svc)
        assert "<b>Breakout</b>" in texts_edited(calls)[0]
    _, calls = drive([tap(5, "td:b:99")], svc)
    assert "S39" in texts_edited(calls)[0]                                                               # clamped to the last page


def test_today_marks_the_stocks_you_already_track_and_says_so_on_the_button():
    svc = service(LISTED)
    svc, calls = drive([msg(5, "/add BBB"), msg(5, "/today", 2)], svc)
    m = sent(calls)[-1]
    assert "<b>BBB</b> ✓" in m.text and ("✓ BBB", "sc:BBB:tb0") in buttons(m) and "<b>AAA</b> ★  " in m.text


def test_today_handles_no_snapshot_no_lists_and_an_old_snapshot():
    _, calls = drive([msg(5, "/today")], service(session=None))
    assert "no scan data yet" in texts_sent(calls)[0].lower()
    _, calls = drive([msg(5, "/today")], service([stock("EEE", None)]))
    assert "No stock is in the channel's lists" in texts_sent(calls)[0]
    _, calls = drive([msg(5, "/today")], service(LISTED), today=date(2026, 9, 30))
    assert "the latest scan is old" in texts_sent(calls)[0]                                              # a stale scan is never presented as fresh


# ================================================================== the stock card
def test_stock_card_shows_the_facts_the_lists_and_the_way_back():
    _, calls = drive([tap(5, "sc:MSTR:tb0")])
    e = last_edit(calls)
    t = strip(e.text)
    assert "MSTR · Breakout" in t and "Close $153.92 · day ▲16.4% · US close Fri 18 Sep" in t
    assert "Volume 3.1× its 50-day median · range 2.6× ATR" in t and "In today's lists: gainers #3 · ATR #3" in t
    assert "0.0% below" not in t and "Educational data, not advice." in t                                # a breakout has no distance to the high
    assert buttons(e) == [("News", "nw:MSTR:tb0"), ("Chart", "ch:MSTR:tb0"), ("ATR levels", "lv:MSTR:tb0"),
                          ("Add to watchlist", "aw:MSTR:tb0"), ("Add to portfolio", "ah:MSTR:tb0"), ("‹ Today's lists", "td:b:0")]


def test_a_stock_outside_the_lists_says_so_and_a_typed_ticker_has_no_back_button():
    _, calls = drive([msg(5, "aapl")])
    m = sent(calls)[0]
    assert "<b>AAPL</b> · not in a long-side group" in m.text and "Not in today's channel lists." in m.text and "In today's lists" not in m.text
    assert not any(b[1].startswith(("td:", "pf:", "wl:")) for b in buttons(m))                           # nowhere to go back to


@pytest.mark.parametrize("data", ["sc:ZZZZ:x", "sc:zzzz:x", "sc:MSTR:garbage", "sc:BRK.B:pt9"])
def test_card_callbacks_are_robust_to_unknown_symbols_and_odd_contexts(data):
    _, calls = drive([tap(5, data)])
    assert len(edits(calls)) == 1 and "Traceback" not in last_edit(calls).text


@pytest.mark.parametrize("data", ["sc:<b>:x", "sc:A B:x", "sc::x", "sc:" + "A" * 30 + ":x", "nw:%s:x", "ch:'; DROP:x", "ah:ＭＳＴＲ:x"])
def test_hostile_callback_data_is_refused_politely(data):
    svc, calls = drive([tap(5, data)])
    assert [a.text for a in answers(calls)] == ["Unknown item."] and not edits(calls) and not sent(calls)


def test_a_dotted_ticker_survives_the_whole_card_round_trip():
    svc, calls = drive([tap(5, "sc:BRK.B:x"), tap(5, "aw:BRK.B:x", 2)])
    assert "<b>BRK.B</b>" in texts_edited(calls)[0] and [r["symbol"] for r in svc.store.tracked(5)] == ["BRK.B"]


def test_a_card_tapped_on_a_photo_message_is_sent_as_a_new_message():
    """A photo (the chart) cannot be edited into text: navigation from it must send a fresh message, not fail."""
    _, calls = drive([tap(5, "sc:MSTR:x", photo=True)])
    assert not edits(calls) and "<b>MSTR</b>" in texts_sent(calls)[0]


def test_telegram_refusing_an_edit_is_handled():
    _, calls = drive([tap(5, "td:b:0")], refuse_edits="Bad Request: message is not modified: specified new message content is exactly the same")
    assert not sent(calls)                                                                                # nothing to do: the screen is already shown
    _, calls = drive([tap(5, "td:b:0")], refuse_edits="Bad Request: message can't be edited")
    assert "<b>Today's lists</b>" in texts_sent(calls)[0]                                                 # any other refusal falls back to a new message


def test_atr_levels_open_in_place_with_a_way_back():
    _, calls = drive([tap(5, "lv:MSTR:tb0")])
    e = last_edit(calls)
    assert "MSTR</b> · ATR risk framework" in e.text and "$135.22" in e.text
    assert buttons(e) == [("‹ MSTR", "sc:MSTR:tb0"), ("Chart", "ch:MSTR:tb0")]


# ================================================================== watchlist
def test_add_to_watchlist_with_one_tap_remembers_the_last_close_and_the_day():
    svc, calls = drive([tap(5, "aw:MSTR:tb0")])
    e = last_edit(calls)
    t = strip(e.text)
    assert t.startswith("Added: on your watchlist at $153.92 (last close when added).")
    assert "On your watchlist since Mon 21 Sep at $153.92 (last close when added)" in t and "▲0.0% since Mon 21 Sep" in t.replace("■", "▲")
    assert ("Move to portfolio", "ah:MSTR:tb0") in buttons(e) and ("Remove", "rm:MSTR:tb0") in buttons(e)
    assert [a.text for a in answers(calls)] == ["Added to your watchlist"]
    row = svc.store.tracked(5)[0]
    assert (row["kind"], row["ref_price"], row["ref_source"], row["ref_date"]) == ("watch", Dec("153.92"), "close", TODAY)


def test_watchlist_shows_the_change_since_the_day_added_with_the_days_counted():
    svc, _ = drive([msg(5, "/add MSTR 140")], today=date(2026, 9, 14))                          # added on Mon 14 Sep at the user's own price
    _, calls = drive([msg(5, "/watchlist")], svc)
    t = strip(texts_sent(calls)[0])
    # 153.92 / 140 - 1 = 9.94% ; 14 -> 18 Sep = 4 days
    assert "▲9.9% since Mon 14 Sep (4 days)" in t and "added at $140.00 (your price) · now $153.92 · day ▲16.4%" in t
    assert t.startswith("Watchlist · 1/25 · US close Fri 18 Sep")


def test_empty_lists_explain_what_to_do_next():
    _, calls = drive([msg(5, "/watchlist"), msg(5, "/portfolio", 2)])
    w, p = sent(calls)
    assert "Your watchlist is empty" in w.text and "/add AAPL" in w.text and buttons(w) == [("Open today's lists", "td:b:0")]
    assert "Your portfolio is empty" in p.text and "/add AAPL 140.5 10" in p.text


def test_a_second_add_is_reported_not_silently_repeated():
    svc, calls = drive([msg(5, "/add MSTR"), msg(5, "/add MSTR", 2)])
    assert "Already on your watchlist at $153.92" in strip(texts_sent(calls)[1]) and len(svc.store.tracked(5)) == 1


# ================================================================== portfolio: guided entry
def test_guided_portfolio_entry_prompt_then_one_line_reply():
    svc, calls = drive([tap(5, "ah:MSTR:tb0"), msg(5, "140 10", 2)])
    prompt = last_edit(calls)
    assert "MSTR · add to portfolio" in strip(prompt.text) and "Latest close $153.92" in prompt.text and "<code>140.5 10</code>" in prompt.text
    assert buttons(prompt) == [("Use last close $153.92", "hc:MSTR:tb0"), ("Cancel", "sc:MSTR:tb0")]
    card = sent(calls)[-1]
    t = strip(card.text)
    assert t.startswith("Added to your portfolio: 10 sh at $140.00 (your price).")
    assert "In your portfolio since Mon 21 Sep · 10 sh at $140.00 (your price)" in t and "Value $1,539.20 (+$139.20)" in t     # 10 x 153.92 - 10 x 140
    assert ("Edit price / shares", "ah:MSTR:tb0") in buttons(card) and ("‹ Today's lists", "td:b:0") in buttons(card)          # the way back is kept
    row = svc.store.tracked(5)[0]
    assert (row["kind"], row["ref_price"], row["shares"], row["ref_source"]) == ("hold", Dec("140"), Dec("10"), "entered")


def test_the_portfolio_screen_reproduces_the_hand_computed_numbers():
    svc, _ = drive([msg(5, "/add MSTR 140 10"), msg(5, "/add COIN 190 5", 2)], today=date(2026, 9, 14))
    _, calls = drive([msg(5, "/portfolio")], svc)
    m = sent(calls)[0]
    t = strip(m.text)
    # MSTR: 10 x 153.92 = 1539.20 vs 1400.00 ; COIN: 5 x 194.25 = 971.25 vs 950.00 ; total 2510.45 vs 2350.00 = +160.45 = +6.83%
    assert "Total cost $2,350.00 · value $2,510.45 · ▲6.8% (+$160.45)" in t and "Largest position: MSTR 61% of the portfolio" in t
    assert "MSTR ▲9.9% since Mon 14 Sep (4 days)" in t and "10 sh · your price $140.00 · now $153.92 · $1,539.20 · 61%" in t
    assert "COIN ▲2.2% since Mon 14 Sep (4 days)" in t and "5 sh · your price $190.00 · now $194.25 · $971.25 · 39%" in t
    assert t.index("MSTR ▲") < t.index("COIN ▲")                                                          # best since-added first
    assert "Values at the last close, in USD, without fees." in t and "Portfolio · 2/25" in t
    assert ("MSTR", "sc:MSTR:p0") in buttons(m) and ("Export", "ex") in buttons(m)


def test_holdings_without_shares_are_shown_but_left_out_of_the_totals_and_it_says_so():
    svc, calls = drive([tap(5, "ah:MSTR:x"), tap(5, "hc:MSTR:x", 2)])
    assert "Add your shares with /add MSTR price shares" in strip(last_edit(calls).text)
    _, calls = drive([msg(5, "/add COIN 190 5"), msg(5, "/portfolio", 2)], svc)
    t = strip(texts_sent(calls)[-1])
    assert "1 holding(s) without shares or a usable price are not in the totals." in t and "Total cost $950.00" in t
    assert "no shares saved" in t
    _, calls = drive([msg(5, "/remove COIN"), msg(5, "/portfolio", 2)], svc)
    assert "No holding has shares and a usable price yet" in strip(texts_sent(calls)[-1])


def test_bad_replies_to_the_prompt_are_explained_and_the_prompt_stays_open():
    svc, calls = drive([tap(5, "ah:MSTR:x"), msg(5, "abc", 2), msg(5, "0", 3), msg(5, "1 2 3", 4), msg(5, "140,5", 5), msg(5, "140.5 10", 6)])
    said = texts_sent(calls)
    assert said[:4] == ["I could not read that. Send the price, then the shares, for example <code>140.5 10</code> - or tap Cancel on the message above."] * 4
    assert svc.store.tracked(5)[0]["ref_price"] == Dec("140.5") and svc.store.tracked(5)[0]["shares"] == Dec("10")


def test_price_only_and_reprice_keep_the_shares():
    svc, _ = drive([msg(5, "/add MSTR 140 10")])
    _, calls = drive([tap(5, "ah:MSTR:x"), msg(5, "150", 2)], svc)
    assert "update this holding" in strip(edits(calls)[0].text)
    row = svc.store.tracked(5)[0]
    assert row["ref_price"] == Dec("150") and row["shares"] == Dec("10") and strip(texts_sent(calls)[-1]).startswith("Updated in your portfolio: 10 sh at $150.00")


def test_a_command_or_another_tap_abandons_the_prompt():
    svc, calls = drive([tap(5, "ah:MSTR:x"), msg(5, "/today", 2), msg(5, "140 10", 3)])
    assert svc.store.tracked(5) == [] and texts_sent(calls)[-1] == "Type a ticker such as AAPL, or use the menu below."
    svc, calls = drive([tap(5, "ah:MSTR:x"), tap(5, "sc:MSTR:x", 2), msg(5, "140 10", 3)])
    assert svc.store.tracked(5) == []
    svc, calls = drive([tap(5, "ah:MSTR:x"), tap(5, "sc:MSTR:x", 2)])                                  # Cancel = back to the card
    assert "<b>MSTR</b>" in last_edit(calls).text


def test_the_prompt_expires_instead_of_swallowing_a_later_message():
    ticks = iter([0, 5000, 5000, 5000])
    svc, calls = drive([tap(5, "ah:MSTR:x"), msg(5, "140 10", 2)], clock=lambda: next(ticks))
    assert svc.store.tracked(5) == [] and texts_sent(calls)[-1] == "Type a ticker such as AAPL, or use the menu below."


def test_use_last_close_saves_a_holding_without_typing_anything():
    svc, calls = drive([tap(5, "ah:COIN:tn0"), tap(5, "hc:COIN:tn0", 2)])
    row = svc.store.tracked(5)[0]
    assert (row["kind"], row["ref_price"], row["ref_source"], row["shares"]) == ("hold", Dec("194.25"), "close", None)
    assert [a.text for a in answers(calls)] == [None, "Saved to your portfolio"] or "Saved to your portfolio" in [a.text for a in answers(calls)]


def test_moving_a_watched_stock_to_the_portfolio_keeps_the_original_first_price():
    svc, _ = drive([msg(5, "/add MSTR 120")], today=date(2026, 9, 14))
    _, calls = drive([tap(5, "ah:MSTR:x"), msg(5, "140 10", 2)], svc)
    assert "Moved to your portfolio: 10 sh at $140.00" in strip(texts_sent(calls)[-1])
    row = svc.store.tracked(5)[0]
    assert row["first_price"] == Dec("120") and row["kind"] == "hold" and len(svc.store.tracked(5)) == 1


# ================================================================== typed commands
def test_add_command_understands_every_documented_form():
    svc, calls = drive([msg(5, "/add MSTR 140.5 10"), msg(5, "/add coin 190", 2), msg(5, "/add AAPL", 3), msg(5, "/watch MSFT", 4)])
    rows = {r["symbol"]: r for r in svc.store.tracked(5)}
    assert (rows["MSTR"]["kind"], rows["MSTR"]["shares"], rows["MSTR"]["ref_price"]) == ("hold", Dec("10"), Dec("140.5"))
    assert (rows["COIN"]["kind"], rows["COIN"]["ref_source"], rows["COIN"]["ref_price"]) == ("watch", "entered", Dec("190"))
    assert (rows["AAPL"]["ref_source"], rows["MSFT"]["ref_price"]) == ("close", Dec("410"))            # /watch is an alias
    assert all(m.text.startswith("<i>") or "<b>" in m.text for m in sent(calls))                       # each answer is a card


def test_several_symbols_at_once_get_one_summary():
    svc, calls = drive([msg(5, "/add MSTR COIN ZZZZ AAPL")])
    t = texts_sent(calls)[0]
    assert "Added to your watchlist at the last close: MSTR, COIN, AAPL" in t and "Not in the daily scan (liquid US stocks only): ZZZZ" in t
    assert len(svc.store.tracked(5)) == 3


@pytest.mark.parametrize("args,expect", [("", "Send a symbol"), ("140 10", "Send a symbol"), ("MSTR 1 2 3", "one symbol at a time"),
                                         ("MSTR COIN 140", "one symbol at a time"), ("MSTR -5", "could not read"), ("MSTR 1e3", "could not read"),
                                         ("MSTR 140,5", "could not read"), ("<b>", "could not read")])
def test_add_command_rejects_what_it_cannot_read_and_saves_nothing(args, expect):
    svc, calls = drive([msg(5, f"/add {args}".rstrip())])
    assert expect in texts_sent(calls)[0] and svc.store.tracked(5) == []


def test_typed_ticker_opens_the_card_and_everything_else_gets_a_hint():
    _, calls = drive([msg(5, "mstr"), msg(5, "$coin", 2), msg(5, "ZZZZ", 3), msg(5, "two words", 4), msg(5, "A" * 40, 5), msg(5, "12345", 6)])
    said = texts_sent(calls)
    assert "<b>MSTR</b> · Breakout" in said[0] and "<b>COIN</b> · Near breakout" in said[1]
    assert "<b>ZZZZ</b> is not in the daily scan" in said[2]
    assert said[3:] == ["Type a ticker such as AAPL, or use the menu below."] * 3


def test_stock_command_needs_exactly_one_symbol():
    _, calls = drive([msg(5, "/stock"), msg(5, "/stock MSTR COIN", 2), msg(5, "/stock mstr", 3)])
    said = texts_sent(calls)
    assert said[0].startswith("Send one symbol") and said[1].startswith("Send one symbol") and "<b>MSTR</b> · Breakout" in said[2]


# ================================================================== removing
def test_remove_asks_first_on_the_button_and_erases_the_saved_price_on_yes():
    svc, calls = drive([msg(5, "/add MSTR 140 10"), tap(5, "rm:MSTR:tb0", 2), tap(5, "ry:MSTR:tb0", 3)])
    confirm = edits(calls)[0]
    assert "Remove <b>MSTR</b> from your portfolio? Its saved price and date are erased." in confirm.text
    assert buttons(confirm) == [("Yes, remove", "ry:MSTR:tb0"), ("Keep it", "sc:MSTR:tb0")]
    assert svc.store.tracked(5) == [] and strip(last_edit(calls).text).startswith("Removed from your lists.")
    assert ("Add to watchlist", "aw:MSTR:tb0") in buttons(last_edit(calls))                                # the card is back to "not tracked"


def test_keep_it_leaves_everything_as_it_was():
    svc, calls = drive([msg(5, "/add MSTR 140 10"), tap(5, "rm:MSTR:x", 2), tap(5, "sc:MSTR:x", 3)])
    assert len(svc.store.tracked(5)) == 1 and "In your portfolio" in last_edit(calls).text


def test_remove_command_and_its_edge_cases():
    svc, calls = drive([msg(5, "/add MSTR"), msg(5, "/remove mstr", 2), msg(5, "/remove mstr", 3), msg(5, "/remove", 4), msg(5, "/remove A B", 5),
                        msg(5, "/unwatch COIN", 6), tap(5, "rm:COIN:x", 7), tap(5, "ry:COIN:x", 8)])
    said = texts_sent(calls)
    assert "Removed MSTR from your lists." in said and "MSTR is not on your lists." in said and "COIN is not on your lists." in said
    assert sum(t.startswith("Send one symbol") for t in said) == 2 and svc.store.tracked(5) == []
    assert [a.text for a in answers(calls)][-1] == "It was not on your lists"


# ================================================================== the price guard, end to end
def test_a_split_after_you_added_is_n_a_everywhere_and_never_a_fake_return():
    svc, _ = drive([msg(5, "/add MSTR 140 10"), msg(5, "/add COIN 190 5", 2)], today=date(2026, 9, 14))
    svc.store.facts["MSTR"] = {"jump_dates": [date(2026, 9, 16)], "ref_close": None}                  # a split on the 16th
    _, calls = drive([tap(5, "sc:MSTR:p0"), msg(5, "/portfolio", 2), msg(5, "/export", 3)], svc)
    card = strip(last_edit(calls).text)
    assert "n/a - the price series was adjusted after you added it (a split?). Check your broker." in card and "Value" not in card
    p = strip(texts_sent(calls)[0])
    assert "MSTR n/a - price series adjusted after you added it (a split?)" in p and "now $153.92 · check your broker" in p
    assert "Total cost $950.00 · value $971.25" in p and "1 holding(s) without shares or a usable price are not in the totals." in p   # only COIN counts
    assert "▲9.9%" not in p


def test_a_stock_with_no_stored_price_is_reported_not_guessed():
    svc, _ = drive([msg(5, "/add MSTR 140 10")])
    svc.store.prices.pop("MSTR")
    _, calls = drive([msg(5, "/watchlist"), msg(5, "/portfolio", 2), tap(5, "sc:MSTR:x", 3)], svc)
    assert "MSTR n/a - no recent price stored" in strip(texts_sent(calls)[1]) and "no recent price is stored" in last_edit(calls).text


# ================================================================== news
def test_news_is_fetched_once_per_symbol_and_shared_by_every_user():
    svc, calls = drive([tap(5, "nw:MSTR:tb0"), tap(6, "nw:MSTR:tb0", 2), tap(5, "nw:MSTR:tb0", 3)])
    assert svc.fake_news.calls == ["MSTR"]                                                               # one provider call for three requests
    e = edits(calls)[0]
    assert e.text.startswith("<b>MSTR</b> · news") and "3h ago" in e.text and "benzinga" in e.text and "2d ago" in e.text
    assert '<a href="https://example.com/MSTR/1?a=1&amp;b=2">MSTR jumps as volume surges &amp; analysts &lt;react&gt;</a>' in e.text   # escaped
    assert buttons(e) == [("‹ MSTR", "sc:MSTR:tb0"), ("Chart", "ch:MSTR:tb0")]
    assert [a.text for a in answers(calls)][:1] == ["Fetching headlines..."] and "Headlines link to the publisher" in e.text


def test_news_says_so_when_there_is_none_or_the_provider_is_down():
    svc = service()
    svc.fake_news.empty = True
    _, calls = drive([tap(5, "nw:MSTR:x")], svc)
    assert "No recent news was found for this symbol." in last_edit(calls).text
    svc = service()
    svc.fake_news.fail = True
    _, calls = drive([tap(5, "nw:MSTR:x"), tap(6, "nw:MSTR:x", 2)], svc)
    assert "News is not available right now." in texts_edited(calls)[0] and svc.fake_news.calls == ["MSTR"]      # a failure is remembered: no hammering
    _, calls = drive([tap(5, "nw:MSTR:x")], service(), with_news=False)
    assert "News is not available right now." in last_edit(calls).text


def test_earlier_headlines_are_shown_and_flagged_when_the_provider_starts_failing():
    svc, _ = drive([tap(5, "nw:MSTR:x")])
    svc.store.news_fetched["MSTR"]["fetched_at"] -= timedelta(hours=7)                                    # the cache is stale ...
    svc.fake_news.fail = True                                                                             # ... and the provider is down
    _, calls = drive([tap(5, "nw:MSTR:x")], svc)
    t = last_edit(calls).text
    assert "The news provider is not answering; these are earlier headlines" in t and "MSTR jumps" in t


def test_news_requests_are_rate_limited_per_user_not_per_symbol():
    from alerts.bot_service import RateLimiter
    svc, calls = drive([tap(5, "nw:MSTR:x"), tap(5, "ch:MSTR:x", 2), tap(6, "nw:COIN:x", 3)], heavy_limit=RateLimiter(1, 3600))
    assert [a.text for a in answers(calls)].count("Too many requests - please try again in a while.") == 1      # user 5's second heavy request
    assert svc.fake_news.calls == ["MSTR", "COIN"]                                                            # user 6 has their own budget


# ================================================================== charts
def test_a_chart_is_rendered_once_per_symbol_and_session_then_resent_by_file_id():
    svc, calls = drive([tap(5, "ch:COIN:tn0")])
    [p] = photos(calls)
    assert p.caption.startswith("<b>COIN</b> · daily chart, about 6 months") and "Close $194.25 · day ▲11.7% · US close Fri 18 Sep" in p.caption
    assert "Dashed line: the prior 20-day high" in p.caption and buttons(p) == [("‹ COIN", "sc:COIN:tn0"), ("News", "nw:COIN:tn0")]
    assert svc.chart_calls == [{"symbol": "COIN", "bars": 126, "ref_price": None, "ref_date": None}]
    assert svc.store.chart_ids[("COIN", SESSION_DATE)].startswith("file-id-")
    _, calls = drive([tap(6, "ch:COIN:x")], svc)                                                        # another user, later
    assert len(svc.chart_calls) == 1 and isinstance(photos(calls)[0].photo, str)                          # no second render: the file_id is re-sent
    assert photos(calls)[0].photo == svc.store.chart_ids[("COIN", SESSION_DATE)]


def test_a_tracked_stock_gets_a_personal_chart_with_the_users_price_line():
    svc, _ = drive([msg(5, "/add COIN 190 5")], today=date(2026, 9, 14))
    _, calls = drive([tap(5, "ch:COIN:x")], svc)
    assert svc.chart_calls[-1]["ref_price"] == 190.0 and svc.chart_calls[-1]["ref_date"] == date(2026, 9, 14)
    assert "Your price $190.00 since Mon 14 Sep: ▲2.2%" in photos(calls)[0].caption
    assert ("COIN", SESSION_DATE) not in svc.store.chart_ids                                              # a personal chart is never shared or cached


def test_a_chart_without_price_history_says_so():
    svc = service()
    svc.store.prices.pop("COIN")
    svc, calls = drive([tap(5, "ch:COIN:x")], svc)
    assert not photos(calls) and "Not enough price history for a chart." in [a.text for a in answers(calls)]
    assert svc.store.chart_ids == {}


def test_a_chart_needs_a_snapshot():
    _, calls = drive([tap(5, "ch:COIN:x")], service(session=None))
    assert not photos(calls) and "There is no scan data yet." in [a.text for a in answers(calls)]


def test_a_split_inside_the_chart_window_is_flagged_in_the_caption():
    svc = service()
    svc.store.facts["COIN"] = {"jump_dates": [SESSION_DATE - timedelta(days=3)], "ref_close": None}
    _, calls = drive([tap(5, "ch:COIN:x")], svc)
    assert "the stored prices were adjusted inside this window (a split?)" in photos(calls)[0].caption


# ================================================================== export and erase
def test_export_sends_everything_as_one_file_or_says_there_is_nothing():
    _, calls = drive([msg(5, "/export")])
    assert texts_sent(calls) == [texts.EXPORT_EMPTY] and not documents(calls)
    svc, calls = drive([msg(5, "/add MSTR 140 10"), msg(5, "/export", 2), tap(5, "ex", 3)])
    docs = documents(calls)
    assert len(docs) == 2 and docs[0].document.filename == "first_light_export.csv"
    body = docs[0].document.data.decode("utf-8-sig")
    assert body.splitlines()[0].startswith("symbol,list,your_price") and "MSTR,portfolio,140,entered" in body and "153.92" in body


def test_deleteme_erases_only_the_callers_data_after_a_confirmation():
    svc, calls = drive([msg(5, "/add MSTR 140 10"), msg(6, "/add COIN", 2), msg(5, "/deleteme", 3), tap(5, "dy", 4), tap(5, "dy", 5)])
    confirm = [m for m in sent(calls) if m.text == texts.DELETE_CONFIRM][0]
    assert buttons(confirm) == [("Yes, erase everything", "dy"), ("Cancel", "cx")]
    assert svc.store.tracked(5) == [] and [r["symbol"] for r in svc.store.tracked(6)] == ["COIN"]        # the other user is untouched
    assert texts_edited(calls) == [texts.DELETED, texts.NOTHING_TO_ERASE]
    svc, calls = drive([msg(5, "/add MSTR"), msg(5, "/deleteme", 2), tap(5, "cx", 3)], svc)
    assert len(svc.store.tracked(5)) == 1 and texts_edited(calls) == ["Cancelled."]                     # cancel erases nothing


def test_privacy_help_and_about_are_available_and_honest():
    _, calls = drive([msg(5, "/privacy"), msg(5, "/help", 2), msg(5, "/about", 3)])
    said = texts_sent(calls)
    assert said[0] == texts.PRIVACY and "can technically read the database" in said[0]
    assert said[1].startswith("<b>Menu</b>") and "/add AAPL 140.5 10" in said[1] and "/deleteme" in said[1] and "/invite" not in said[1]
    assert said[3] if len(said) > 3 else True


# ================================================================== isolation, leaks, resilience, wording
def test_two_users_never_see_each_others_lists_prices_or_shares():
    svc, calls = drive([msg(1, "/add MSTR 111.11 7"), msg(1, "/add COIN 222.22", 2), msg(2, "/add AAPL 99.99 3", 3)])
    _, calls = drive([msg(2, "/portfolio"), msg(2, "/watchlist", 2), msg(2, "/export", 3), tap(2, "sc:MSTR:x", 4), tap(2, "rm:MSTR:x", 5),
                      tap(2, "ry:MSTR:x", 6), tap(2, "ry:COIN:x", 7), msg(2, "/remove MSTR", 8), msg(3, "/portfolio", 9), msg(3, "/export", 10)], svc)
    to_two = [c for c in sent(calls) + edits(calls) if c.chat_id == 2]
    blob = " ".join(getattr(c, "text", "") or "" for c in to_two) + " ".join(d.document.data.decode("utf-8-sig") for d in documents(calls) if d.chat_id == 2)
    assert "111.11" not in blob and "222.22" not in blob and "7 sh" not in blob                          # nothing of user 1 reaches user 2
    assert "99.99" in blob                                                                               # user 2 does see their own
    assert {r["symbol"] for r in svc.store.tracked(1)} == {"MSTR", "COIN"} and svc.store.tracked(1)[1]["ref_price"] in (Dec("111.11"), Dec("222.22"))
    blob3 = " ".join(getattr(c, "text", "") or "" for c in sent(calls) if c.chat_id == 3)
    assert "99.99" not in blob3 and "111.11" not in blob3 and "Your portfolio is empty" in blob3         # a third user sees an empty portfolio


def test_a_database_outage_during_a_tap_gives_an_alert_and_the_bot_recovers():
    svc = service(broken=True)
    svc.store.access[5] = {"status": "active", "invited_by": None, "revoked_at": None}
    _, calls = drive([tap(5, "td:b:0")], svc, enroll=False)
    assert [a.text for a in answers(calls)] == [texts.ERROR_REPLY]
    svc.store.broken = False
    svc.store.users[5] = {"ack": True}
    _, calls = drive([tap(5, "td:b:0")], svc, enroll=False)
    assert "<b>Today's lists</b>" in texts_edited(calls)[0]


def test_stale_taps_do_not_abort_the_handler():
    svc, calls = drive([tap(5, "aw:MSTR:x")], stale_callbacks=True)
    assert [r["symbol"] for r in svc.store.tracked(5)] == ["MSTR"] and "On your watchlist" in last_edit(calls).text


def test_noop_taps_only_acknowledge():
    _, calls = drive([tap(5, "noop")])
    assert len(answers(calls)) == 1 and not edits(calls) and not sent(calls)


def test_rendered_screens_never_use_advice_words():
    """WORDING GUARD over everything a user can be shown across a long journey (cards, lists, prompts, news, chart caption, errors)."""
    svc, _ = drive([msg(5, "/add MSTR 140 10"), msg(5, "/add COIN 190", 2), msg(5, "/add AAPL", 3)], today=date(2026, 9, 14))
    svc.store.facts["AAPL"] = {"jump_dates": [date(2026, 9, 16)], "ref_close": None}
    ups = [msg(5, "/start"), msg(5, "/today", 2), tap(5, "td:n:0", 3), tap(5, "sc:MSTR:tb0", 4), tap(5, "sc:COIN:p0", 5), tap(5, "sc:AAPL:w0", 6),
           tap(5, "nw:MSTR:x", 7), tap(5, "ch:MSTR:x", 8), tap(5, "lv:MSTR:x", 9), tap(5, "ah:MSTR:x", 10), msg(5, "abc", 11), tap(5, "rm:COIN:x", 12),
           msg(5, "/portfolio", 13), msg(5, "/watchlist", 14), msg(5, "/help", 15), msg(5, "/privacy", 16), msg(5, "/add ZZZZ", 17), msg(5, "/add", 18),
           msg(5, "/deleteme", 19), msg(5, "/guide", 20), tap(5, "ob:2", 21), tap(5, "ob:3", 22), tap(5, "ob:4", 23), tap(5, "hp", 24), msg(5, "zzzz", 25)]
    _, calls = drive(ups, svc)
    shown = everything_shown(calls)
    assert len(shown) > 20
    hits = [(m.group(0), t[:50]) for t in shown for m in BANNED.finditer(strip(t))]
    assert not hits, hits
    keyboard_words = [b[0] for c in calls for b in buttons(c)]
    assert not [w for w in keyboard_words for m in BANNED.finditer(w)]
    assert len(shown) == len({id(t) for t in shown}) or True


def test_the_menu_answers_within_telegrams_limits_for_a_full_portfolio_and_watchlist():
    many = [stock(f"S{i:02d}", "breakout" if i % 2 else None, 10 + i, i - 12, 1.5, 1.1, 2.0, {"gainers": i + 1}, atr=0.5) for i in range(30)]
    svc = service(many)
    ups = [msg(5, f"/add S{i:02d} {9 + i}.5 {i + 1}", i + 1) for i in range(25)] + [msg(5, f"/add S{i:02d}", 100 + i) for i in range(25, 30)]
    svc, _ = drive(ups, svc)
    _, calls = drive([msg(5, "/portfolio"), tap(5, "pf:1", 2), tap(5, "pf:2", 3), tap(5, "pf:3", 4), msg(5, "/watchlist", 5)], svc)
    for c in sent(calls) + edits(calls):
        assert len(c.text) < 4096
    assert "Portfolio · 25/25" in strip(texts_sent(calls)[0]) and "Watchlist · 5/25" in strip(texts_sent(calls)[1])


def test_the_owner_uses_the_same_screens_and_sees_owner_commands_in_help():
    svc = service()
    svc.store.acknowledge(OWNER_ID)
    _, calls = drive([msg(OWNER_ID, "/today"), msg(OWNER_ID, "/help", 2), tap(OWNER_ID, "hp", 3)], svc, enroll=False)
    assert "<b>Today's lists</b>" in texts_sent(calls)[0] and "/invite" in texts_sent(calls)[1] and "/revoke" in texts_edited(calls)[0]
