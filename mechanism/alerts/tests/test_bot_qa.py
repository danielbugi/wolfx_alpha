"""QA scenario matrix for the First Light bot. Everything runs through the REAL dispatcher with a fake network (see qa_harness.py);
every reply is also checked against Telegram's HTML rules (tg_html.py).

Sections: A group chats (a pointer, never data) - B channels - C private flow - D hostile / odd input - E resilience - F isolation and
limits - G deep links and buttons - H the HTML checker itself - I public-string sweep - J the live-QA tool - K ACCESS (invite-only
authorisation, invitations, owner commands, leak and log tests).

Run:  python -m pytest mechanism/alerts/tests -q      (from the repo root)
"""
import itertools
import re
import socket

import pytest

import tg_html
from qa_harness import (BOT_USERNAME, OWNER_ID, SESSION, STOCKS, TODAY, MemoryStore, TOKEN, answers, channel_post, drive, keyboard_urls,
                        msg, sent, service, stock, tap, texts_edited, texts_sent)
from decimal import Decimal

from alerts import bot_service as bs  # noqa: E402
from alerts import access, deeplink, digest_builder as db_, digest_format as fmt, screens, texts  # noqa: E402
from aiogram.methods import AnswerCallbackQuery  # noqa: E402

LINK = lambda payload: f"https://t.me/{BOT_USERNAME}?start={payload}"          # noqa: E731
strip = lambda t: re.sub(r"<[^>]+>", "", t)                                       # noqa: E731


def silent(calls) -> bool:
    """The bot neither sent a message nor answered a button (aiogram's own getMe lookups do not count)."""
    return not sent(calls) and not answers(calls)


def acked(*uids, **kw):
    svc = service(**kw)
    for u in uids:
        svc.store.users[u] = {"ack": True}
    return svc


# ============================================================ A. group / supergroup chats: a pointer, never data
GROUP_COMMANDS = ["/levels MSTR", "/add AAPL", "/remove AAPL", "/portfolio", "/watchlist", "/today", "/stock MSTR", "/export", "/deleteme",
                  "/guide", "/privacy", "/agree", "/start", "/help", "/about"]


@pytest.mark.parametrize("chat_type", ["group", "supergroup"])
@pytest.mark.parametrize("command", GROUP_COMMANDS)
@pytest.mark.parametrize("who", ["owner", "member", "stranger"])
def test_A1_in_a_group_every_command_gets_only_the_pointer_and_nothing_is_stored(chat_type, command, who):
    """LEAK test: strategy levels, watchlists and help must never appear in a public group, whoever asks (owner included)."""
    uid = {"owner": OWNER_ID, "member": 5, "stranger": 6}[who]
    svc = acked(5)
    svc.store.set_access(5, "active")
    svc.store.upsert_tracked(5, "MSTR", "watch", Decimal("100"), "close", TODAY, None, Decimal("100"))
    _, calls = drive([msg(uid, command, chat_type=chat_type)], svc, enroll=False)
    [reply] = sent(calls)
    assert reply.text == texts.GROUP_POINTER and "135.22" not in reply.text and "MSTR" not in reply.text
    assert keyboard_urls(reply) == [LINK("help")]
    assert [r["symbol"] for r in svc.store.tracked(5)] == ["MSTR"] and 6 not in svc.store.users and 6 not in svc.store.access


@pytest.mark.parametrize("command", ["/invite Dana", "/approve 5", "/revoke 5", "/users", "/status"])
def test_A2_owner_commands_do_nothing_in_a_group(command):
    svc = service()
    _, calls = drive([msg(OWNER_ID, command, chat_type="supergroup")], svc, enroll=False)
    assert silent(calls) and not svc.store.invites and not svc.store.access and not svc.store.audit_log


def test_A3_mentions_only_the_addressed_bot_answers():
    _, calls = drive([msg(5, f"/levels@{BOT_USERNAME} MSTR", chat_type="supergroup")])
    assert texts_sent(calls) == [texts.GROUP_POINTER]
    _, calls = drive([msg(5, "/levels@some_other_bot MSTR", chat_type="supergroup")])
    assert silent(calls)                                                                   # another bot's command is none of our business


def test_A4_a_group_flood_is_dropped_silently_after_the_per_group_limit():
    from alerts.bot_service import RateLimiter
    updates = [msg(5, "/help", n, chat_type="supergroup", chat_id=-777) for n in range(1, 6)]
    _, calls = drive(updates, group_limit=RateLimiter(2, 60))
    assert len(sent(calls)) == 2                                                          # 3 more were dropped, no "too many" spam
    other_group = drive([msg(5, "/help", 9, chat_type="supergroup", chat_id=-888)], group_limit=RateLimiter(2, 60))[1]
    assert len(sent(other_group)) == 1                                                    # limits are per group


def test_A5_group_messages_without_a_user_are_ignored_not_crashed_on():
    upd = msg(5, "/help", chat_type="supergroup")
    del upd["message"]["from"]                                                             # anonymous admin / service message
    _, calls = drive([upd])
    assert silent(calls)


def test_A6_group_chatter_that_is_not_a_command_is_ignored():
    _, calls = drive([msg(5, "hello everyone", chat_type="supergroup"), msg(5, "MSTR looks strong", 2, chat_type="group")])
    assert silent(calls)


# ============================================================ B. channels
def test_B1_a_channel_post_never_triggers_a_reply():
    """The bot polls only for message + callback_query, and even if a channel_post reached the dispatcher nothing handles it: a bot
    reply in a channel would be published to every subscriber."""
    _, calls = drive([channel_post("/levels MSTR")], acked(5))
    assert silent(calls)


# ============================================================ C. private flow
def test_C1_full_happy_path_from_first_contact_to_a_tracked_stock():
    svc = service()
    svc.store.set_access(5, "active")
    updates = [msg(5, "/start"), tap(5, "ob:2", 2), tap(5, "ob:3", 3), tap(5, "ob:4", 4), tap(5, "ob:ack", 5), msg(5, "/levels mstr", 6),
               msg(5, "/add AAPL", 7), msg(5, "/watchlist", 8), msg(5, "/remove AAPL", 9), msg(5, "/help", 10), msg(5, "/about", 11)]
    svc, calls = drive(updates, svc, enroll=False)
    said, edited = texts_sent(calls), texts_edited(calls)
    assert said[0] == texts.ONBOARDING[1]                                                   # first contact = the guide, not a legal wall
    assert edited[:3] == [texts.ONBOARDING[2], texts.ONBOARDING[3], texts.ONBOARDING_LAST]
    assert texts.ONBOARDING_DONE in said and svc.store.is_acknowledged(5)
    assert any("MSTR</b> · ATR risk framework" in t for t in said)
    assert any(t.startswith("<i>Added: on your watchlist at $189.30") for t in said)
    assert any("<b>Watchlist</b>" in t and "AAPL" in t for t in said)
    assert "Removed AAPL from your lists." in said and svc.store.tracked(5) == []
    assert any(t.startswith("<b>Menu</b>") and "/add AAPL 140.5 10" in t for t in said) and texts.ABOUT in said
    assert len(answers(calls)) == 4


def test_C2_typed_agree_is_a_working_fallback_for_the_button():
    svc = service()
    svc.store.set_access(5, "active")
    svc, calls = drive([msg(5, "/agree"), msg(5, "/levels MSTR", 2)], svc, enroll=False)
    assert svc.store.is_acknowledged(5)
    assert texts.ONBOARDING_DONE in texts_sent(calls) and any("ATR risk framework" in t for t in texts_sent(calls))


def test_C3_a_stale_button_tap_is_still_recorded_and_still_answered():
    """Regression: a tap made while the bot was down arrives late; Telegram rejects the answer ('query is too old'). That used to abort
    the handler after the acknowledgement was stored -- the user saw nothing and thought the bot was broken. (An old button that still
    carries the retired 'ack:lv_MSTR' payload is accepted too, and no longer opens a stock.)"""
    svc = service()
    svc.store.set_access(5, "active")
    svc, calls = drive([tap(5, "ack:lv_MSTR")], svc, stale_callbacks=True, enroll=False)
    assert svc.store.is_acknowledged(5)
    assert texts.ONBOARDING_DONE in texts_sent(calls) and not any("</b> · ATR risk framework" in t for t in texts_sent(calls))


def test_C4_a_stale_popup_tap_does_not_raise():
    _, calls = drive([tap(9, "def:atr")], stale_callbacks=True)
    assert len(answers(calls)) == 1                                                        # attempted once, failure swallowed


def test_C5_popups_answer_every_definition_and_unknown_items():
    _, calls = drive([tap(9, f"def:{k}", i) for i, k in enumerate(["atr", "vol", "groups", "nope"], 1)])
    assert [a.text for a in answers(calls)] == [texts.DEFINITIONS["atr"], texts.DEFINITIONS["vol"], texts.DEFINITIONS["groups"], "Unknown item."]
    assert all(a.show_alert for a in answers(calls))


def test_C6_retired_deep_links_from_old_channel_posts_just_open_the_menu():
    """Old channel messages still carry '<TICKER> levels' / 'list' buttons. They must no longer open strategy content."""
    for payload in ("lv_COIN", "list", "help", "lv_", "", "lv_BRK_B"):
        _, calls = drive([msg(5, f"/start {payload}".rstrip())], acked(5))
        [reply] = texts_sent(calls)
        assert reply.startswith("Welcome back") and "ATR risk framework" not in reply and "Risk level" not in reply


def test_C7_unknown_commands_text_photos_and_other_bots_get_a_gentle_hint():
    updates = [msg(5, "/foo", 1), msg(5, "just chatting", 2), msg(5, "/levels@some_other_bot MSTR", 3)]
    photo = msg(5, "x", 4)
    del photo["message"]["text"]
    photo["message"]["photo"] = [{"file_id": "f", "file_unique_id": "u", "width": 1, "height": 1}]
    _, calls = drive(updates + [photo], acked(5))
    cmd_hint, text_hint = "Send /help to see what I can do, or use the menu below.", "Type a ticker such as AAPL, or use the menu below."
    assert texts_sent(calls) == [cmd_hint, text_hint, cmd_hint, cmd_hint]


def test_C8_unknown_callback_data_is_ignored():
    _, calls = drive([tap(5, "xyz:1"), tap(5, "")], acked(5))
    assert silent(calls)


# ============================================================ D. hostile / odd input
HOSTILE = ["", "   ", "123", "!!!", "<script>alert(1)</script>", "'; DROP TABLE bot_users; --", "A" * 500, "מניה", "MSTR\nCOIN",
           "$$$", "..", "MSTR." , "  mstr  ", "‮MSTR", "ＭＳＴＲ", "%s%s%s", "{cap}", "a b c d e f g h i j k l m n o p"]


@pytest.mark.parametrize("arg", HOSTILE)
@pytest.mark.parametrize("command", ["/levels", "/add", "/remove", "/stock"])
def test_D1_hostile_arguments_never_crash_leak_or_produce_invalid_html(command, arg):
    svc = acked(5)
    _, calls = drive([msg(5, f"{command} {arg}".rstrip(), chat_type="private")], svc)
    said = texts_sent(calls)
    assert len(said) == 1 and len(said[0]) < 4096                                          # drive() also validated the HTML
    assert "<script" not in said[0] and "DROP TABLE" not in said[0] and "Traceback" not in said[0]


def test_D2_a_trailing_dot_or_dollar_sign_still_finds_the_stock():
    assert "MSTR</b> · ATR risk framework" in texts_sent(drive([msg(5, "/levels MSTR.")], acked(5))[1])[0]
    assert "MSTR</b> · ATR risk framework" in texts_sent(drive([msg(5, "/levels $mstr")], acked(5))[1])[0]


def test_D3_symbols_are_escaped_in_replies():
    from alerts import tracker
    assert tracker.parse_add_args("<b>X</b>").error == "bad_number"                           # not a valid symbol shape -> rejected
    assert tg_html.problems(service().render_levels("ZZ&Z")) == []
    card = screens.stock_card("A<B&C", None, SESSION, None, "x")
    assert "A&lt;B&amp;C" in card.text and tg_html.problems(card.text) == []
    assert tg_html.problems(screens.hold_prompt("A<B", None, "x").text) == []


def test_D4_a_full_watchlist_and_portfolio_page_fit_one_message_with_valid_html():
    many = [stock(f"S{i:02d}", "breakout" if i % 2 else None, 10 + i, i - 12, 1.5, 1.1, 2.0, {"gainers": 1, "atr": 2, "volume": 3}, atr=0.5) for i in range(30)]
    svc = service(many)
    from alerts.tracker import TrackerService
    tr = TrackerService(svc.store, today=lambda: TODAY)
    for i in range(30):
        tr.add_watch(1, f"S{i:02d}")
        tr.add_hold(1, f"S{i:02d}", Decimal("9.5"), Decimal("12.25"))
    kinds = [r['kind'] for r in svc.store.tracked(1)]
    assert kinds.count('hold') == 25 and kinds.count('watch') == 5                               # caps are per list: the 26th move is refused, it stays watched
    for kind in ("hold", "watch"):
        views, totals = tr.views(1, kind)
        for page in range(0, 4):
            text = screens.tracked_list(kind, views, totals, SESSION, page).text
            assert len(text) < 4096 and tg_html.problems(text) == []


@pytest.mark.parametrize("rvol,rng,below,ranks,atr,cat", list(itertools.product([None, 0.0, 250.0], [None, 0.0, 9.9], [None, 0.0, 2.9],
                                                                                  [None, {"volume": 1}], [None, 0.4, 3.0, 9.9], [None, "breakout", "near_breakout"])))
def test_D5_levels_and_the_stock_card_survive_every_combination_of_missing_facts(rvol, rng, below, ranks, atr, cat):
    svc = service([stock("XYZ", cat, 10.0, -3.2, rvol, rng, below, ranks, atr=atr)])
    row = svc.store.stocks(None, ["XYZ"])["XYZ"]
    for text in (svc.render_levels("XYZ"), screens.stock_card("XYZ", row, SESSION, None, "x").text):
        assert tg_html.problems(text) == [] and "None" not in strip(text) and "nan" not in strip(text).lower()


# ============================================================ E. resilience
def test_E1_a_database_outage_gets_a_friendly_reply_and_the_bot_keeps_serving():
    svc = service(broken=True)
    _, calls = drive([msg(5, "/levels MSTR", 1), msg(5, "/about", 2)], svc)
    assert texts_sent(calls) == [texts.ERROR_REPLY, texts.ERROR_REPLY]                    # no traceback, no silence, and FAIL CLOSED:
    svc.store.broken = False                                                               # nobody is served data while access can't be checked
    _, calls = drive([msg(5, "/about", 3)], svc)
    assert texts_sent(calls) == [texts.ABOUT]                                              # the bot recovers by itself; it never crashed


def test_E5_the_owner_is_never_locked_out_by_a_database_outage_and_popups_need_no_database():
    svc = service(broken=True)
    _, calls = drive([msg(OWNER_ID, "/about", 1), tap(9, "def:atr", 2)], svc, enroll=False)
    assert texts_sent(calls) == [texts.ABOUT]                                              # the owner id lives in .env, not in the database
    assert [a.text for a in answers(calls)] == [texts.DEFINITIONS["atr"]]


def test_E2_a_database_outage_during_a_button_tap_answers_with_an_alert():
    _, calls = drive([tap(5, "ack:")], service(broken=True))
    assert any(a.text == texts.ERROR_REPLY and a.show_alert for a in answers(calls))


def test_E3_a_second_bot_instance_refuses_to_start():
    from alerts.run_bot import acquire_single_instance
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    first = acquire_single_instance(port)
    try:
        with pytest.raises(SystemExit) as exc:
            acquire_single_instance(port)
        assert "already running" in str(exc.value)
    finally:
        first.close()
    acquire_single_instance(port).close()                                                  # the lock is released with the process


def test_E4_no_snapshot_yet_is_reported_in_every_entry_point():
    svc = service(session=None)
    svc.store.users[5] = {"ack": True}
    _, calls = drive([msg(5, "/levels MSTR"), msg(5, "/today", 2), msg(5, "/stock MSTR", 3), msg(5, "/add AAPL", 4)], svc)
    assert all("no scan data yet" in t.lower() for t in texts_sent(calls))


# ============================================================ F. isolation and limits
def test_F1_200_users_never_see_or_change_each_others_data():
    updates, n = [], 0
    for uid in range(1000, 1200):
        for text in ("/agree", f"/add {['AAPL', 'MSTR', 'COIN', 'MSFT'][uid % 4]}"):
            n += 1
            updates.append(msg(uid, text, n))
    svc, calls = drive(updates)
    assert all(svc.store.is_acknowledged(u) for u in range(1000, 1200))
    assert all([r['symbol'] for r in svc.store.tracked(u)] == [['AAPL', 'MSTR', 'COIN', 'MSFT'][u % 4]] for u in range(1000, 1200))
    assert svc.store.tracked(999) == []


def test_F2_rate_limits_are_per_user_and_popups_have_their_own_budget():
    updates = [msg(5, "/about", n) for n in range(1, 5)] + [msg(6, "/about", 5)]
    _, calls = drive(updates, acked(5, 6), msg_limit=bs.RateLimiter(3, 3600))
    said = texts_sent(calls)
    assert said.count("Too many requests - please try again in a while.") == 1 and len(said) == 5
    _, calls = drive([tap(9, "def:atr", n) for n in range(1, 5)], popup_limit=bs.RateLimiter(2, 3600))
    assert sum(1 for a in answers(calls) if "Too many" in (a.text or "")) == 2 and len(answers(calls)) == 4


# ============================================================ G. deep links and buttons
def test_G1_payloads_only_use_characters_telegram_allows():
    assert deeplink.link("bot", "help") == "https://t.me/bot?start=help"
    assert deeplink.link("bot", "inv_AbC-_123") == "https://t.me/bot?start=inv_AbC-_123"
    for bad in ("", "a b", "<x>", "ÅB", "x" * 65, "a.b"):
        with pytest.raises(ValueError):
            deeplink.link("bot", bad)
    assert deeplink.is_invite("inv_abc") and not deeplink.is_invite("help") and not deeplink.is_invite(None) and not deeplink.is_invite("")


def _digest():
    rows = []
    for k, (sym, cat) in enumerate((("AAA", "breakout"), ("BBB", "breakout"), ("CCC", "breakout"), ("DDD", "breakout"), ("EEE", "breakout"),
                                    ("FFF", "breakout"), ("GGG", "breakout"), ("BRK.B", "near_breakout"), ("HHH", "near_breakout"))):
        rows.append({"symbol": sym, "cat": cat, "prev_cat": None, "new": True, "close": 10.0 + k, "ret1_pct": 9.0 - k, "dv20": 5e7,
                     "rvol": 1.0 + k * 0.5, "range_atr": 3.0 - k * 0.2, "below_high_pct": 1.0, "atr": 0.5})
    return db_.build_digest(rows, top_n=5, min_dv=0)


def test_G2_the_channel_carries_three_popups_and_one_neutral_assistant_link_and_nothing_else():
    """Decision D2: no per-ticker / strategy buttons in the public channel."""
    kb = fmt.header_keyboard(BOT_USERNAME)["inline_keyboard"]
    buttons = [b for row in kb for b in row]
    assert sorted(b["callback_data"] for b in buttons if "callback_data" in b) == ["def:atr", "def:groups", "def:vol"]
    urls = [b for b in buttons if "url" in b]
    assert [(b["text"], b["url"]) for b in urls] == [(fmt.PRIVATE_ASSISTANT_BUTTON, LINK("ch"))]   # "ch" = counted, never per person
    assert not any("levels" in b["text"].lower() or "lv_" in b.get("url", "") for b in buttons)


def test_G2b_the_per_ticker_channel_buttons_stay_removed():
    import inspect
    from alerts import send_daily_digest
    assert not hasattr(fmt, "group_keyboard") and not hasattr(fmt, "keyboard_symbols") and not hasattr(deeplink, "levels_payload")
    assert "group_keyboard" not in inspect.getsource(send_daily_digest) and "keyboard_symbols" not in inspect.getsource(send_daily_digest)


def test_G3_every_button_url_and_callback_fits_telegrams_limits():
    for row in fmt.header_keyboard(BOT_USERNAME)["inline_keyboard"]:
        assert 1 <= len(row) <= 3                                                         # UI skill: at most 3 columns
        for b in row:
            assert b["text"].strip() and len(b["text"]) <= 40
            assert ("url" in b) != ("callback_data" in b)
            assert len(b.get("callback_data", "").encode()) <= 64 and len(b.get("url", "")) <= 2048


# ============================================================ H. the HTML checker itself
@pytest.mark.parametrize("bad,expect", [("a & b", "raw '&'"), ("x < y", "raw '<'"), ("<b>open", "unclosed"), ("<b>x</i>", "unexpected"),
                                        ("<div>x</div>", "unsupported tag"), ("<a>x</a>", "href"), ("&nbsp;", "unsupported entity"),
                                        ("<blockquote><blockquote>x</blockquote></blockquote>", "nested"), ("<pre><b>x</b></pre>", "inside code"),
                                        ("<br/>", "self-closing"), ("", "empty"), ("<blockquote color='x'>x</blockquote>", "attributes")])
def test_H1_the_checker_catches_what_telegram_rejects(bad, expect):
    assert any(expect in p for p in tg_html.problems(bad)), tg_html.problems(bad)


def test_H2_the_checker_accepts_what_our_messages_use():
    ok = ('<b>Title</b> · <i>x</i> &amp; <a href="https://finviz.com/quote.ashx?t=A&amp;p=d">A</a> &lt;3 &quot;q&quot; &#39;s&#39;\n'
          "<blockquote expandable>a\nb</blockquote>\n<b><a href=\"https://t.me/x?start=list\">y</a></b> ★ ▲ ▼")
    assert tg_html.problems(ok) == []
    assert tg_html.problems("x" * 4097) and tg_html.problems("x" * 4096) == []


# ============================================================ I. public-string sweep
BANNED = re.compile(r"\b(buy\w*|sell\w*|targets?|stops?|entry|entries|recommend\w*|signals?|opportunit\w*|picks?|should|profit\w*|"
                    r"winners?|guarantee\w*|alpha)\b", re.I)


def test_I1_every_public_string_is_valid_html_and_free_of_advice_words():
    d = _digest()
    header = fmt.format_header(__import__("datetime").date(2026, 9, 18), __import__("datetime").datetime(2026, 9, 20, 3, 0), d, 2915,
                               {"up": 1, "down": 2}, ["S&P 500 +0.2%", "VIX (volatility index) 14.8"])
    everything = [header, fmt.format_caption(__import__("datetime").date(2026, 9, 18), d), *(fmt.format_group(c, d) for c in db_.LONG_CATEGORIES),
                  texts.DISCLAIMER_SHORT, texts.DISCLAIMER_LONG + "\n\n" + texts.ACK_HINT, texts.HELP, texts.ABOUT,
                  texts.NOT_INVITED.format(uid=123456789), texts.INVITE_INVALID.format(uid=123456789), texts.INVITE_REVOKED,
                  texts.WELCOME_INVITED, texts.GROUP_POINTER, texts.OWNER_HELP.format(hours=72, days=30),
                  texts.ERROR_REPLY, texts.LEVELS_NOTE, texts.LEVELS_HOW,
                  *texts.DEFINITIONS.values(), texts.ACK_BUTTON,
                  *(service().render_levels(s) for s in ("MSTR", "COIN", "AAPL", "MSFT", "WILD", "BRK.B", "NOPE"))]
    for text in everything:
        limit = 1024 if text is everything[1] else 4096
        assert tg_html.problems(text, limit) == [], (tg_html.problems(text, limit), text[:80])
        hits = BANNED.findall(strip(text))
        assert not hits, (hits, text[:80])
    assert all(len(v) <= 200 for v in texts.DEFINITIONS.values())


# ============================================================ J. the live-QA tool itself
def test_J1_qa_live_labels_are_escaped_so_telegram_accepts_them():
    """qa_live.py once posted the label "'<TICKER> levels'" verbatim and real Telegram rejected it (can't parse entities)."""
    from alerts import qa_live
    body = qa_live.qa_body(3, 14, "channel: '<TICKER>' & (2xATR >= price)", "<b>ok</b>")
    assert tg_html.problems(body) == [] and "&lt;TICKER&gt;" in body and "&amp;" in body
    labels_and_texts = [(label, text) for label, text, _ in _live_replies_without_db()]
    for label, text in labels_and_texts:
        assert tg_html.problems(qa_live.qa_body(1, 1, label, text)) == [], label


def _live_replies_without_db():
    """The static part of qa_live.replies(): everything that does not need the snapshot."""
    from alerts import texts as t
    return [("ack", t.DISCLAIMER_LONG + "\n\n" + t.ACK_HINT, None), ("/help", t.HELP, None), ("/about", t.ABOUT, None),
            ("private: not invited", t.NOT_INVITED.format(uid=123456789), None),
            ("private: invitation not valid", t.INVITE_INVALID.format(uid=123456789), None),
            ("group: pointer", t.GROUP_POINTER, None), ("error", t.ERROR_REPLY, None),
            ("  header keyboard", "Header buttons (popups need the bot running):", None)]


# ============================================================ K. ACCESS: invite-only authorisation (PRIVATE_ASSISTANT_PLAN.md 7.0)
PRIVATE_COMMANDS = ["/start", "/agree", "/guide", "/today", "/stock MSTR", "/add AAPL", "/add AAPL 140.5 10", "/remove AAPL", "/portfolio",
                    "/watchlist", "/export", "/deleteme", "/privacy", "/levels MSTR", "/help", "/about", "/foo", "just chatting", "MSTR", "Today's lists",
                    "/invite Dana", "/approve 5", "/revoke 5", "/users", "/status"]
STRATEGY_MARKERS = ("ATR risk framework", "Risk level", "Reference levels", "135.22", "153.92", "<b>Watchlist</b>", "<b>Portfolio</b>", "<b>Menu</b>", "Today's lists ·")


def stranger_svc(status=None):
    """A service where user 77 has the given access status (None = never seen) and 5 is a normal member with a watchlist."""
    svc = acked(5)
    svc.store.set_access(5, "active")
    svc.store.upsert_tracked(5, "MSTR", "watch", Decimal("100"), "close", TODAY, None, Decimal("100"))
    if status:
        svc.store.set_access(77, status)
    return svc


def invite_code(calls):
    """The invitation payload ('inv_<code>') inside the link the owner was sent."""
    m = re.search(r"start=(inv_[A-Za-z0-9_-]+)", " ".join(texts_sent(calls)))
    assert m, texts_sent(calls)
    return m.group(1)


def snapshot_of(store):
    return (dict(store.users), {k: sorted(v) for k, v in store.tracked_rows.items()}, dict(store.invites), list(store.audit_log))


@pytest.mark.parametrize("status", [None, "pending", "revoked"])
@pytest.mark.parametrize("command", PRIVATE_COMMANDS)
def test_K1_anyone_not_let_in_gets_one_refusal_and_nothing_else(status, command):
    """LEAK + PRIVACY test: no strategy / list / help content reaches a stranger, a pending or a revoked user, and nothing is stored."""
    svc = stranger_svc(status)
    before = snapshot_of(svc.store)
    _, calls = drive([msg(77, command)], svc, enroll=False)
    [reply] = texts_sent(calls)
    assert reply == (texts.INVITE_REVOKED if status == "revoked" else texts.NOT_INVITED.format(uid=77))   # a blocked person is told so; nobody sees data
    assert not any(m in reply for m in STRATEGY_MARKERS)
    assert snapshot_of(svc.store) == before
    assert svc.store.access_status(77) == status                                          # the request changed nothing


def test_K2_buttons_need_access_except_the_three_public_popups():
    svc = stranger_svc()
    taps = [tap(77, "ack:"), tap(77, "ack:lv_MSTR", 2), tap(77, "ob:ack", 3), tap(77, "sc:MSTR:x", 4), tap(77, "aw:MSTR:x", 5), tap(77, "td:b:0", 6),
            tap(77, "pf:0", 7), tap(77, "nw:MSTR:x", 8), tap(77, "ch:MSTR:x", 9), tap(77, "dy", 10), tap(77, "def:atr", 11), tap(77, "def:groups", 12)]
    _, calls = drive(taps, svc, enroll=False)
    assert [a.text for a in answers(calls)] == [texts.NOT_INVITED_POPUP] * 10 + [texts.DEFINITIONS["atr"], texts.DEFINITIONS["groups"]]
    assert not svc.store.is_acknowledged(77) and 77 not in svc.store.users and not sent(calls)
    assert all(len(a.text) <= 200 for a in answers(calls))


def test_K3_the_owner_needs_no_database_row_and_sees_the_owner_commands():
    svc = service()
    _, calls = drive([msg(OWNER_ID, "/start"), tap(OWNER_ID, "ob:ack", 2), msg(OWNER_ID, "/help", 3), msg(OWNER_ID, "/levels MSTR", 4)], svc, enroll=False)
    said = texts_sent(calls)
    assert OWNER_ID not in svc.store.access                                                # never stored as a member
    assert said[0] == texts.ONBOARDING[1]                                                  # the owner also goes through the guide once
    assert any("/invite" in t and "/approve" in t and "/revoke" in t for t in said)        # the help after the notice lists owner commands
    assert any("MSTR</b> · ATR risk framework" in t for t in said)


def test_K4_members_never_see_or_run_the_owner_commands():
    svc = acked(5)
    _, calls = drive([msg(5, "/help"), msg(5, "/invite Dana", 2), msg(5, "/approve 99", 3), msg(5, "/revoke 5", 4), msg(5, "/users", 5), msg(5, "/status", 6)], svc)
    said = texts_sent(calls)
    assert "/invite" not in said[0] and "Owner" not in said[0]
    assert said[2:] == ["Send /help to see what I can do, or use the menu below."] * 5                             # as if the commands did not exist
    assert not svc.store.invites and svc.store.access_status(99) is None and svc.store.access_status(5) == "active" and not svc.store.audit_log


def test_K5_with_no_owner_configured_nobody_can_be_invited_and_owner_commands_are_dead():
    svc = service()
    _, calls = drive([msg(1, "/invite x"), msg(2, "/approve 3", 2), msg(-1, "/users", 3)], svc, owner_id=None, enroll=False)
    assert all(t == texts.NOT_INVITED.format(uid=u) for t, u in zip(texts_sent(calls), (1, 2, -1)))
    assert not svc.store.invites and not svc.store.access


def test_K6_invitation_lifecycle_link_to_access_owner_notice_and_single_use():
    svc = service()
    _, calls = drive([msg(OWNER_ID, "/invite Dana <b>&")], svc, enroll=False)
    link_text = texts_sent(calls)[0]
    code = invite_code(calls)
    assert f"https://t.me/{BOT_USERNAME}?start={code}" in link_text and "72 hours" in link_text
    assert code[len("inv_"):] not in " ".join(list(svc.store.invites) + [str(v) for v in svc.store.invites.values()])   # only a hash is stored
    assert access.hash_code(code[len("inv_"):]) in svc.store.invites
    # a stranger opens it
    _, calls = drive([msg(77, f"/start {code}", 1), tap(77, "ack:", 2), msg(77, "/levels MSTR", 3)], svc, enroll=False)
    said = [c for c in sent(calls) if c.chat_id == 77]
    assert said[0].text == texts.WELCOME_INVITED and said[1].text == texts.ONBOARDING[1]
    assert any(t.text == texts.ONBOARDING_DONE for t in said) and any("MSTR</b> · ATR risk framework" in t.text for t in said)
    assert svc.store.access_status(77) == "active" and svc.store.is_acknowledged(77)
    [notice] = [c for c in sent(calls) if c.chat_id == OWNER_ID]
    assert "77" in notice.text and "Dana &lt;b&gt;&amp;" in notice.text                       # the owner learns the id; the note is escaped
    assert [a[1] for a in svc.store.audit_log] == ["invite", "redeem"]
    # single use: the next person with the same link is refused
    _, calls = drive([msg(78, f"/start {code}")], svc, enroll=False)
    assert texts_sent(calls) == [texts.INVITE_INVALID.format(uid=78)] and svc.store.access_status(78) is None


def test_K7_an_invitation_expires():
    svc = service()
    _, calls = drive([msg(OWNER_ID, "/invite")], svc, enroll=False)
    code = invite_code(calls)
    real = svc.store.clock
    svc.store.clock = lambda: real() + 73 * 3600
    _, calls = drive([msg(77, f"/start {code}")], svc, enroll=False)
    assert texts_sent(calls) == [texts.INVITE_INVALID.format(uid=77)] and svc.store.access_status(77) is None


def test_K8_a_member_opening_someone_elses_link_does_not_burn_it():
    svc = acked(5)
    svc.store.set_access(5, "active")
    _, calls = drive([msg(OWNER_ID, "/invite")], svc, enroll=False)
    code = invite_code(calls)
    _, calls = drive([msg(5, f"/start {code}")], svc, enroll=False)
    assert texts_sent(calls)[0].startswith("Welcome back")
    assert next(iter(svc.store.invites.values()))["uses"] == 0


@pytest.mark.parametrize("payload", ["inv_", "inv_x", "inv_' OR 1=1 --", "inv_" + "A" * 500, "inv_%s%s", "inv_<b>", "inv_ÅÅÅÅÅÅÅÅÅÅ", "INV_abcdefghijkl",
                                     "inv_abcdefgh\nrevoke", "lv_MSTR", "list", "help", "x"])
def test_K9_hostile_invitation_payloads_are_refused_without_a_crash(payload):
    svc = service()
    _, calls = drive([msg(77, f"/start {payload}")], svc, enroll=False)
    [reply] = texts_sent(calls)
    assert reply in (texts.NOT_INVITED.format(uid=77), texts.INVITE_INVALID.format(uid=77))
    assert svc.store.access_status(77) is None and not svc.store.users


def test_K10_revoke_blocks_at_once_and_a_new_link_cannot_bring_them_back():
    svc = service()
    _, calls = drive([msg(OWNER_ID, "/invite")], svc, enroll=False)
    code = invite_code(calls)
    drive([msg(77, f"/start {code}"), tap(77, "ack:", 2)], svc, enroll=False)
    assert svc.store.access_status(77) == "active"
    _, calls = drive([msg(OWNER_ID, "/revoke 77")], svc, enroll=False)
    assert "revoked for 77" in texts_sent(calls)[0]
    _, calls = drive([msg(77, "/levels MSTR")], svc, enroll=False)
    assert texts_sent(calls) == [texts.INVITE_REVOKED]                                      # blocked on the very next message
    _, calls = drive([msg(OWNER_ID, "/invite again")], svc, enroll=False)
    code2 = invite_code(calls)
    _, calls = drive([msg(77, f"/start {code2}")], svc, enroll=False)
    assert texts_sent(calls) == [texts.INVITE_REVOKED] and svc.store.access_status(77) == "revoked"
    assert next(v for v in svc.store.invites.values() if v["note"] == "again")["uses"] == 0   # the refused link was not consumed
    _, calls = drive([msg(OWNER_ID, "/approve 77")], svc, enroll=False)                     # only the owner can re-admit, explicitly
    assert "Access granted to 77" in texts_sent(calls)[0]
    _, calls = drive([msg(77, "/help")], svc, enroll=False)
    assert texts_sent(calls)[0] != texts.NOT_INVITED.format(uid=77)


@pytest.mark.parametrize("arg", ["", "abc", "-5", "0", "12.5", "1e9", "９９", "99999999999999999999", "'; DROP TABLE bot_access;--"])
@pytest.mark.parametrize("command", ["/approve", "/revoke"])
def test_K11_owner_commands_validate_the_id(command, arg):
    svc = service()
    _, calls = drive([msg(OWNER_ID, f"{command} {arg}".rstrip())], svc, enroll=False)
    [reply] = texts_sent(calls)
    assert reply.startswith("Send the numeric Telegram id") and not svc.store.access and not svc.store.audit_log


def test_K12_the_owner_cannot_be_revoked_or_locked_out_and_unknown_ids_are_reported():
    svc = service()
    _, calls = drive([msg(OWNER_ID, f"/revoke {OWNER_ID}"), msg(OWNER_ID, f"/approve {OWNER_ID}", 2), msg(OWNER_ID, "/revoke 123", 3)], svc, enroll=False)
    said = texts_sent(calls)
    assert "cannot revoke the owner" in said[0] and "own id" in said[1] and "no access to revoke" in said[2]
    assert not svc.store.access and svc.store.access_status(OWNER_ID) is None


def test_K13_users_and_status_show_counts_only_never_ids_or_holdings():
    svc = service()
    for uid in (11, 12, 13):
        svc.store.set_access(uid, "active")
    svc.store.set_access(14, "revoked")
    svc.store.upsert_tracked(11, "MSTR", "watch", Decimal("100"), "close", TODAY, None, Decimal("100"))
    _, calls = drive([msg(OWNER_ID, "/invite x"), msg(OWNER_ID, "/users", 2), msg(OWNER_ID, "/status", 3)], svc, enroll=False)
    users, status = texts_sent(calls)[1:]
    assert "3 active" in users and "1 revoked" in users and "1 open invitation" in users
    assert all(str(i) not in users + status for i in (11, 12, 13, 14)) and "MSTR" not in users + status
    assert "Fri 18 Sep" in status and "2,915 stocks" in status


def test_K14_strangers_are_rate_limited_before_any_database_work():
    svc = service()
    _, calls = drive([msg(77, "/levels MSTR", n) for n in range(1, 6)], svc, msg_limit=bs.RateLimiter(2, 3600), enroll=False)
    said = texts_sent(calls)
    assert said.count(texts.NOT_INVITED.format(uid=77)) == 2 and said.count("Too many requests - please try again in a while.") == 3


def test_K15_purge_deletes_revoked_users_data_after_the_retention_window_but_keeps_the_block():
    svc = service()
    svc.store.set_access(77, "active")
    svc.store.touch_user(77)
    svc.store.upsert_tracked(77, "AAPL", "watch", Decimal("100"), "close", TODAY, None, Decimal("100"))
    svc.store.set_access(78, "active")
    svc.store.touch_user(78)
    acc = access.Access(svc.store, OWNER_ID)
    acc.revoke(OWNER_ID, 77)
    assert acc.purge() == 0 and [r["symbol"] for r in svc.store.tracked(77)] == ["AAPL"]                        # inside the 30-day window nothing is deleted
    real = svc.store.clock
    svc.store.clock = lambda: real() + 31 * 86400
    assert acc.purge() == 1
    assert svc.store.tracked(77) == [] and 77 not in svc.store.users and svc.store.access_status(77) == "revoked"
    assert 78 in svc.store.users                                                           # active users untouched
    assert acc.purge() == 0                                                                # idempotent


def test_K16_nothing_sensitive_is_ever_logged(caplog):
    """PRIVACY test: run the whole invitation + watchlist flow at DEBUG level; no invitation code, symbol or note reaches a log line."""
    import logging
    caplog.set_level(logging.DEBUG)
    svc = service()
    _, calls = drive([msg(OWNER_ID, "/invite SecretNote")], svc, enroll=False)
    code = invite_code(calls)
    drive([msg(77, f"/start {code}"), tap(77, "ack:", 2), msg(77, "/add AAPL COIN", 3), msg(77, "/levels MSTR", 4), msg(77, "/watchlist", 5)],
          svc, enroll=False)
    for secret in (code, code[len("inv_"):], "SecretNote", "AAPL", "COIN", "MSTR", "135.22"):
        assert secret not in caplog.text, secret


def test_K17_a_database_error_never_admits_anyone():
    svc = stranger_svc()
    svc.store.broken = True
    _, calls = drive([msg(77, "/levels MSTR"), msg(5, "/levels MSTR", 2), tap(77, "ack:", 3)], svc, enroll=False)
    assert texts_sent(calls) == [texts.ERROR_REPLY, texts.ERROR_REPLY]                      # fail closed: an error, never data
    assert [a.text for a in answers(calls)] == [texts.ERROR_REPLY]


def test_K18_two_members_never_see_each_others_data_and_a_stranger_sees_neither():
    svc = service()
    svc.store.set_access(1, "active")
    svc.store.set_access(2, "active")
    drive([msg(1, "/agree"), msg(1, "/add MSTR 140 5", 2), msg(2, "/agree", 3), msg(2, "/add COIN", 4)], svc, enroll=False)
    _, calls = drive([msg(1, "/portfolio"), msg(2, "/watchlist", 2), msg(77, "/portfolio", 3)], svc, enroll=False)
    one, two, stranger = texts_sent(calls)
    assert "MSTR" in one and "COIN" not in one and "COIN" in two and "MSTR" not in two
    assert stranger == texts.NOT_INVITED.format(uid=77)


def test_K19_parsers_and_hashes():
    assert access.parse_user_id("123456789") == 123456789 and access.parse_user_id(" 42  extra") == 42
    for bad in (None, "", "abc", "-1", "0", "1.5", "９９", "1" * 16):
        assert access.parse_user_id(bad) is None, bad
    assert access.clean_note("  a   b  ") == "a b" and access.clean_note("x" * 200) == "x" * access.NOTE_MAX and access.clean_note("   ") is None
    assert access.hash_code("abc") == access.hash_code("abc") != access.hash_code("abd") and len(access.hash_code("abc")) == 64
    acc = access.Access(MemoryStore(), 1)
    codes = {acc.create_invite(1, None) for _ in range(200)}
    assert len(codes) == 200 and all(re.fullmatch(r"inv_[A-Za-z0-9_-]{12}", c) for c in codes)   # random, URL-safe, fits a start payload
