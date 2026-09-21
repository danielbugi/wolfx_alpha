"""The request-access flow (FUNNEL_PLAN.md sections 3-4): a stranger taps Request access -> the owner gets Approve / Decline -> the person is in.

Privacy contract under test: NOTHING is stored about a person until they tap Request access; then only their Telegram id and the time, until the
owner decides (declined rows only for the cool-down). The funnel numbers are counts only. Only the owner can decide. Everything runs through the
real dispatcher with a fake Telegram network; a real-Postgres round trip covers the SQL."""
import logging
import os
import re
import sys
from datetime import date, datetime, timezone

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))
sys.path.insert(0, ROOT)

from alerts import access as acc  # noqa: E402
from alerts import bot_service as bs  # noqa: E402
from alerts import texts  # noqa: E402

from qa_harness import OWNER_ID, answers, buttons, drive, edits, msg, sent, service, tap, texts_edited, texts_sent  # noqa: E402

strip = lambda t: re.sub(r"<[^>]+>", "", t)                                          # noqa: E731
BANNED = re.compile(r"\b(buy\w*|sell\w*|targets?|stops?|entry|entries|recommend\w*|signals?|opportunit\w*|picks?|should|profit\w*|"
                    r"winners?|guarantee\w*|alpha)\b", re.I)
REQUEST, INFO = ("Request access", "rq:new"), ("What is this?", "rq:info")


def to_owner(calls):
    return [c for c in sent(calls) if c.chat_id == OWNER_ID]


def to_user(calls, uid):
    return [c for c in sent(calls) if c.chat_id == uid]


def nothing_stored(store):
    return not store.requests and not store.users and not store.access and not store.tracked_rows


# ================================================================== what a stranger sees and what it costs them
def test_a_stranger_sees_the_request_screen_and_nothing_is_stored_by_looking():
    svc, calls = drive([msg(77, "/start"), msg(77, "/today", 2), msg(77, "hello", 3)], service(), enroll=False)
    first = sent(calls)[0]
    assert first.text == texts.NOT_INVITED.format(uid=77) and "free during the beta" in first.text and "Request access" in first.text
    assert buttons(first) == [REQUEST, INFO]
    assert all(t == first.text for t in texts_sent(calls)) and nothing_stored(svc.store) and svc.store.events == {}


def test_the_channel_button_is_counted_without_storing_who_pressed_it():
    svc = service()
    svc.store.set_access(5, "active")
    svc, calls = drive([msg(77, "/start ch"), msg(78, "/start ch", 2), msg(5, "/start ch", 3), msg(79, "/start", 4)], svc, enroll=False)
    assert svc.store.events == {"opened_channel": 3}                                    # three opens from the channel button, the plain /start is not one
    assert not svc.store.requests and 77 not in svc.store.users and 78 not in svc.store.users   # counted, never recorded per person


def test_what_is_this_explains_the_data_kept_and_goes_back_in_place():
    svc, calls = drive([tap(77, "rq:info"), tap(77, "rq:back", 2)], service(), enroll=False)
    info, back = edits(calls)
    assert "at most 14 days" in info.text and "Nothing is stored if you do not tap the button" in info.text and "not investment advice" in info.text
    assert buttons(info) == [("Request access", "rq:new"), ("‹ Back", "rq:back")]
    assert back.text == texts.NOT_INVITED.format(uid=77) and buttons(back) == [REQUEST, INFO]
    assert nothing_stored(svc.store)                                                    # even after reading everything


# ================================================================== request -> owner decides -> in
def test_request_then_approve_end_to_end():
    svc, calls = drive([tap(77, "rq:new")], service(), enroll=False)
    assert svc.store.requests[77]["status"] == "pending" and svc.store.events == {"requested": 1}
    assert [a.text for a in answers(calls)] == [texts.REQUEST_POPUP_SENT] and texts_edited(calls) == [texts.REQUEST_SENT]
    [notice] = to_owner(calls)
    assert "77" in notice.text and buttons(notice) == [("Approve", "ra:77"), ("Decline", "rd:77")]
    svc, calls = drive([tap(OWNER_ID, "ra:77")], svc, enroll=False)
    assert texts_edited(calls) == ["Approved <code>77</code>."] and svc.store.access_status(77) == "active" and 77 not in svc.store.requests
    told = to_user(calls, 77)
    assert [m.text for m in told] == [texts.ACCESS_GRANTED_USER, texts.ONBOARDING[1]]     # they are told, and the guide starts
    assert (OWNER_ID, "approve", 77, "request") in svc.store.audit_log                       # the decision is audited (who / what / whom), never more
    svc, calls = drive([msg(77, "/agree"), msg(77, "/today", 2)], svc, enroll=False)      # and they can now use the bot
    assert texts.ONBOARDING_DONE in texts_sent(calls) and "<b>Today's lists</b>" in texts_sent(calls)[-1]


def test_a_double_tap_makes_one_request_and_one_owner_message():
    svc, calls = drive([tap(77, "rq:new"), tap(77, "rq:new", 2)], service(), enroll=False)
    assert len(svc.store.requests) == 1 and len(to_owner(calls)) == 1
    assert [a.text for a in answers(calls)] == [texts.REQUEST_POPUP_SENT, texts.REQUEST_POPUP_ALREADY]
    _, calls = drive([msg(77, "/start")], svc, enroll=False)
    assert texts_sent(calls) == [texts.REQUEST_PENDING] and buttons(sent(calls)[0]) == []   # while waiting, no button and no second ask


def test_decline_keeps_the_person_only_for_the_cool_down_then_they_may_ask_again():
    svc, _ = drive([tap(77, "rq:new")], service(), enroll=False)
    svc, calls = drive([tap(OWNER_ID, "rd:77")], svc, enroll=False)
    assert texts_edited(calls) == ["Declined <code>77</code>."] and svc.store.requests[77]["status"] == "declined" and svc.store.access_status(77) is None
    [told] = to_user(calls, 77)
    assert told.text.startswith("Your request was not approved this time. You can ask again after ")
    _, calls = drive([msg(77, "/start"), tap(77, "rq:new", 2)], svc, enroll=False)
    assert texts_sent(calls)[0].startswith("Your last request was not approved. You can ask again after ") and buttons(sent(calls)[0]) == []
    assert answers(calls)[0].text.startswith("Your last request was not approved") and answers(calls)[0].show_alert
    real = svc.store.clock
    svc.store.clock = lambda: real() + 7 * 86400 + 60                                   # the cool-down is over
    _, calls = drive([msg(77, "/start"), tap(77, "rq:new", 2)], svc, enroll=False)
    assert buttons(sent(calls)[0]) == [REQUEST, INFO] and svc.store.requests[77]["status"] == "pending"


def test_closed_mode_shows_no_request_button_and_stores_nothing():
    svc, calls = drive([msg(77, "/start"), tap(77, "rq:new", 2)], service(), enroll=False, access_mode="closed")
    assert texts_sent(calls) == [texts.NOT_INVITED_CLOSED.format(uid=77)] and buttons(sent(calls)[0]) == [INFO]
    assert [a.text for a in answers(calls)] == [texts.REQUEST_POPUP_CLOSED] and nothing_stored(svc.store) and not to_owner(calls)


def test_auto_mode_admits_up_to_the_cap_then_falls_back_to_the_owners_queue():
    svc = service()
    svc, calls = drive([tap(1001, "rq:new"), tap(1002, "rq:new", 2), tap(1003, "rq:new", 3)], svc, enroll=False, access_mode="auto", max_members=2)
    assert svc.store.access_status(1001) == svc.store.access_status(1002) == "active" and svc.store.access_status(1003) is None
    assert svc.store.requests[1003]["status"] == "pending" and 1001 not in svc.store.requests
    assert [m.text for m in to_user(calls, 1001)] == [texts.ONBOARDING[1]]               # the guide starts at once
    owner_msgs = to_owner(calls)
    assert sum("automatically" in m.text for m in owner_msgs) == 2 and sum(bool(buttons(m)) for m in owner_msgs) == 1   # 2 FYIs + 1 real request


def test_a_full_waiting_list_refuses_politely_and_stores_nothing():
    ups = [tap(2000 + i, "rq:new", i + 1) for i in range(6)]
    svc, calls = drive(ups, service(), enroll=False, max_pending=4)
    assert len(svc.store.requests) == 4 and len(to_owner(calls)) == 4
    assert [a.text for a in answers(calls)].count(texts.REQUEST_POPUP_FULL) == 2 and 2005 not in svc.store.requests


def test_a_revoked_person_is_told_and_cannot_ask_again():
    svc = service()
    svc.store.set_access(77, "revoked")
    svc, calls = drive([msg(77, "/start"), tap(77, "rq:new", 2)], svc, enroll=False)
    assert texts_sent(calls) == [texts.INVITE_REVOKED] and [a.text for a in answers(calls)] == [texts.REQUEST_POPUP_BLOCKED]
    assert not svc.store.requests and not to_owner(calls)


def test_a_member_tapping_an_old_request_button_is_told_they_are_in():
    svc = service()
    svc.store.set_access(5, "active")
    svc, calls = drive([tap(5, "rq:new")], svc, enroll=False)
    assert [a.text for a in answers(calls)] == [texts.REQUEST_POPUP_MEMBER] and not svc.store.requests


def test_an_invalid_invitation_link_offers_a_request_instead_of_a_dead_end():
    svc, calls = drive([msg(77, "/start inv_abcdefghijkl")], service(), enroll=False)
    [m] = sent(calls)
    assert m.text == texts.INVITE_INVALID.format(uid=77) and buttons(m) == [REQUEST, INFO] and nothing_stored(svc.store)


# ================================================================== only the owner decides
def test_only_the_owner_can_approve_or_decline_and_bad_data_does_nothing():
    svc, _ = drive([tap(77, "rq:new")], service(), enroll=False)
    svc.store.set_access(5, "active")
    svc, calls = drive([tap(5, "ra:77"), tap(78, "rd:77", 2), tap(77, "ra:77", 3)], svc, enroll=False)   # a member, a stranger, the requester himself
    assert [a.text for a in answers(calls)] == ["Only the owner can do this."] * 3
    assert svc.store.access_status(77) is None and svc.store.requests[77]["status"] == "pending"
    _, calls = drive([tap(OWNER_ID, "ra:abc"), tap(OWNER_ID, "ra:", 2), tap(OWNER_ID, "ra:-5", 3), tap(OWNER_ID, "rz:77", 4), tap(OWNER_ID, "ra:" + "9" * 30, 5)],
                     svc, enroll=False)
    assert not answers(calls) and not edits(calls) and svc.store.requests[77]["status"] == "pending"     # no handler, no effect


def test_deciding_twice_or_on_someone_who_never_asked_is_reported():
    svc, _ = drive([tap(77, "rq:new")], service(), enroll=False)
    svc, calls = drive([tap(OWNER_ID, "ra:77"), tap(OWNER_ID, "ra:77", 2), tap(OWNER_ID, "rd:88", 3), tap(OWNER_ID, f"ra:{OWNER_ID}", 4)], svc, enroll=False)
    said = [a.text for a in answers(calls)]
    assert said[0] == "Approved" and said[1:] == [texts.OWNER_NO_REQUEST.format(uid=77), texts.OWNER_NO_REQUEST.format(uid=88),
                                                  texts.OWNER_NO_REQUEST.format(uid=OWNER_ID)]
    assert svc.store.access_status(OWNER_ID) is None                                    # the owner is never written into the access table


def test_a_typed_approve_also_clears_the_waiting_request_and_tells_the_person():
    svc, _ = drive([tap(77, "rq:new")], service(), enroll=False)
    svc, calls = drive([msg(OWNER_ID, "/approve 77"), msg(OWNER_ID, "/approve 88", 2)], svc, enroll=False)
    assert svc.store.access_status(77) == "active" and 77 not in svc.store.requests and svc.store.access_status(88) == "active"
    assert [m.text for m in to_user(calls, 77)] == [texts.ACCESS_GRANTED_USER, texts.ONBOARDING[1]] and to_user(calls, 88) == []   # only a waiting person is pinged


def test_when_the_owner_cannot_be_reached_the_request_still_waits():
    svc, calls = drive([tap(77, "rq:new")], service(), enroll=False, owner_id=None)
    assert svc.store.requests[77]["status"] == "pending" and texts_edited(calls) == [texts.REQUEST_SENT]


# ================================================================== /requests and /funnel
def test_requests_lists_the_oldest_first_with_buttons_and_is_owner_only():
    svc, _ = drive([msg(OWNER_ID, "/requests")], service(), enroll=False)
    svc, calls = drive([msg(OWNER_ID, "/requests")], svc, enroll=False)
    assert texts_sent(calls) == ["<b>Requests</b>\nNobody is waiting for access."]
    svc, _ = drive([tap(3000 + i, "rq:new", i + 1) for i in range(6)], svc, enroll=False)
    _, calls = drive([msg(OWNER_ID, "/requests")], svc, enroll=False)
    [m] = sent(calls)
    assert "6 waiting" in m.text and "and 2 more" in m.text and m.text.count("<code>") == 4
    assert buttons(m)[0] == ("Approve 3000", "ra:3000") and buttons(m)[1] == ("Decline", "rd:3000") and len(buttons(m)) == 8
    svc.store.set_access(5, "active")
    _, calls = drive([msg(5, "/requests"), msg(5, "/funnel", 2)], svc, enroll=False)
    assert texts_sent(calls) == ["Send /help to see what I can do, or use the menu below."] * 2      # a member never learns these commands exist


def test_the_funnel_counts_the_whole_journey_and_never_shows_who():
    svc = service()
    ups = [msg(77, "/start ch"), msg(78, "/start ch", 2), msg(79, "/start ch", 3), tap(77, "rq:new", 4), tap(78, "rq:new", 5)]
    svc, _ = drive(ups, svc, enroll=False)
    svc, _ = drive([tap(OWNER_ID, "ra:77")], svc, enroll=False)
    svc, _ = drive([msg(77, "/agree"), msg(77, "/add MSTR", 2)], svc, enroll=False)      # finished the guide and tracked a first stock
    _, calls = drive([msg(OWNER_ID, "/funnel")], svc, enroll=False)
    t = strip(texts_sent(calls)[0])
    assert "Opened from the channel: 3 (7 days) · 3 (30 days)" in t and "Requested access: 2" in t and "Approved: 1" in t
    assert "Finished the guide: 1" in t and "Tracked a first stock within 24 h: 1" in t and "waiting now: 1" in t
    assert not re.search(r"\b(77|78|79|MSTR)\b", t) and "never who" in t                    # counts only: no id, no symbol


# ================================================================== housekeeping, privacy, robustness
def test_stale_requests_and_finished_cool_downs_are_purged_but_recent_ones_stay():
    svc, _ = drive([tap(1, "rq:new"), tap(2, "rq:new", 2), tap(3, "rq:new", 3)], service(), enroll=False)
    drive([tap(OWNER_ID, "rd:2")], svc, enroll=False)
    a = acc.Access(svc.store, OWNER_ID)
    a.purge()
    assert set(svc.store.requests) == {1, 2, 3}                                          # nothing is old yet
    real = svc.store.clock
    svc.store.clock = lambda: real() + 8 * 86400
    a.purge()
    assert set(svc.store.requests) == {1, 3}                                            # the declined one's 7-day cool-down is over: forgotten
    svc.store.clock = lambda: real() + 15 * 86400
    a.purge()
    assert svc.store.requests == {}                                                     # an unanswered request is deleted after 14 days


def test_nothing_about_a_requester_reaches_the_logs(caplog):
    caplog.set_level(logging.DEBUG)
    svc, _ = drive([msg(555123, "/start ch"), tap(555123, "rq:new", 2)], service(), enroll=False)
    drive([tap(OWNER_ID, "ra:555123")], svc, enroll=False)
    assert "555123" not in caplog.text


def test_a_database_outage_during_a_request_gives_an_error_and_stores_nothing():
    svc = service(broken=True)
    svc, calls = drive([tap(77, "rq:new")], svc, enroll=False)
    assert [a.text for a in answers(calls)] == [texts.ERROR_REPLY]
    svc.store.broken = False
    assert nothing_stored(svc.store)


def test_the_request_button_is_rate_limited_per_person():
    svc, calls = drive([tap(77, "rq:info", i) for i in range(1, 6)], service(), enroll=False, popup_limit=bs.RateLimiter(3, 3600))
    assert [a.text for a in answers(calls)].count("Too many requests - please try again in a while.") == 2


def test_a_group_never_gets_the_request_screen():
    svc, calls = drive([msg(77, "/start", chat_type="supergroup")], service(), enroll=False)
    assert texts_sent(calls) == [texts.GROUP_POINTER] and buttons(sent(calls)[0]) == [("Open the private chat", f"https://t.me/test_first_light_bot?start=help")]


def test_an_invalid_access_mode_is_refused_at_construction():
    with pytest.raises(ValueError):
        acc.Access(service().store, 1, mode="open")
    assert acc.MODES == ("approve", "auto", "closed")


def test_all_new_owner_and_stranger_copy_is_free_of_advice_words():
    from alerts import screens
    strings = [screens.not_invited(1, {"reason": r, "until": datetime(2026, 9, 28, tzinfo=timezone.utc)}, inv).text
               for r in (None, "closed", "blocked", "pending", "cooldown") for inv in (False, True)]
    strings += [screens.what_is_this(True).text, screens.owner_request(5).text, screens.requests_list(2, [{"telegram_user_id": 1, "requested_at": datetime(2026, 9, 21, tzinfo=timezone.utc)}]).text,
                screens.funnel_screen({k: 1 for k in ("opened_channel", "requested", "approved", "finished_guide", "activated", "active_7d", "waiting", "declined_cooling")},
                                      {k: 2 for k in ("opened_channel", "requested", "approved", "finished_guide", "activated", "active_7d", "waiting", "declined_cooling")}).text]
    strings += [v.replace("{uid}", "1").replace("{date}", "28 Sep") for k, v in vars(texts).items() if isinstance(v, str) and k.startswith(("REQUEST", "OWNER", "ACCESS", "WHAT", "NOT_INVITED"))]
    hits = [(m.group(0), t[:50]) for t in strings for m in BANNED.finditer(strip(t))]
    assert not hits, hits


# ================================================================== Postgres (real SQL)
def _pg():
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(ROOT, ".env"))
        from shared import db
        db.execute_dict_query("SELECT 1 FROM bot_requests LIMIT 1")
        return db
    except Exception as e:                                                          # noqa: BLE001
        pytest.skip(f"Postgres unreachable or access-flow tables missing: {type(e).__name__}")


def test_access_flow_sql_round_trip_with_real_postgres():
    """Request / decline / cool-down / approve / purge / counters / funnel metrics against the real tables, with throw-away ids and a private
    event name (the real funnel counters are not touched). Everything is deleted afterwards."""
    db = _pg()
    store, owner = bs.PgStore(db), 990_000_000_300
    u1, u2, u3 = 990_000_000_301, 990_000_000_302, 990_000_000_303
    try:
        assert store.request_status(u1) is None
        assert store.add_request(u1) is True and store.add_request(u1) is False              # one pending request per person
        st = store.request_status(u1)
        assert st["status"] == "pending" and st["cooldown_active"] is False
        assert store.add_request(u2) is True
        n, rows = store.pending_requests(10)
        ids = [r["telegram_user_id"] for r in rows]
        assert n >= 2 and ids.index(u1) < ids.index(u2)                                      # oldest first
        assert store.decide_request(u1, approve=False) == "declined" and store.decide_request(u1, approve=False) is None
        st = store.request_status(u1)
        assert st["status"] == "declined" and st["cooldown_active"] is True and st["cooldown_until"] > datetime.now(timezone.utc)
        assert store.add_request(u1) is False                                                # still cooling down: refused, nothing changes
        db.execute_insert("UPDATE bot_requests SET decided_at = NOW() - INTERVAL '8 days' WHERE telegram_user_id = %s", (u1,))
        assert store.request_status(u1)["cooldown_active"] is False and store.add_request(u1) is True   # cool-down over: may ask again
        assert store.decide_request(u2, approve=True) == "approved" and store.request_status(u2) is None   # approve deletes the row
        db.execute_insert("UPDATE bot_requests SET requested_at = NOW() - INTERVAL '15 days' WHERE telegram_user_id = %s", (u1,))
        assert store.purge_requests(14, 7) >= 1 and store.request_status(u1) is None          # an unanswered request is purged after 14 days
        # aggregate counters: a private event name, no user id anywhere
        store.count_event("zz_test")
        store.count_event("zz_test")
        assert db.execute_dict_query("SELECT n FROM funnel_events WHERE day = CURRENT_DATE AND event = 'zz_test'")[0]["n"] == 2
        # funnel metrics: a throw-away member who accepted the notice and tracked a stock straight away
        before = store.funnel_metrics(7)
        store.set_access(u3, "active", owner)
        store.acknowledge(u3)
        store.upsert_tracked(u3, "AAPL", "watch", 100, "close", date.today(), None, 100)
        after = store.funnel_metrics(7)
        assert after["approved"] == before["approved"] + 1 and after["finished_guide"] == before["finished_guide"] + 1
        assert after["activated"] == before["activated"] + 1 and after["active_7d"] == before["active_7d"] + 1
        assert set(after) == {"opened_channel", "requested", "approved", "finished_guide", "activated", "active_7d", "waiting", "declined_cooling"}
    finally:
        db.execute_insert("DELETE FROM bot_requests WHERE telegram_user_id IN (%s, %s, %s)", (u1, u2, u3))
        db.execute_insert("DELETE FROM funnel_events WHERE event = 'zz_test'")
        db.execute_insert("DELETE FROM bot_access WHERE telegram_user_id = %s", (u3,))
        db.execute_insert("DELETE FROM bot_users WHERE telegram_user_id = %s", (u3,))


# ================================================================== setting up a new channel: forward a post to the bot, get its chat id
def _forwarded(uid, chat_id=-1005551234, title="First Light DEV <x>", kind="channel", text="a post", n=1):
    upd = msg(uid, text, n)
    upd["message"]["forward_origin"] = {"type": "channel", "date": 1758000000, "message_id": 9,
                                        "chat": {"id": chat_id, "type": kind, "title": title}}
    return upd


def test_the_owner_gets_the_chat_id_of_a_forwarded_channel_post():
    svc = service()
    svc, calls = drive([_forwarded(OWNER_ID)], svc, enroll=False)
    [reply] = texts_sent(calls)
    assert "Chat id: <code>-1005551234</code>" in reply and "First Light DEV &lt;x&gt;" in reply and "(channel)" in reply     # title is escaped
    assert "TELEGRAM_DEV_CHAT_ID" in reply and nothing_stored(svc.store)


def test_nobody_else_learns_anything_from_a_forwarded_post():
    svc = service()
    svc.store.set_access(5, "active")
    svc, calls = drive([_forwarded(5), _forwarded(77, n=2)], svc, enroll=False)
    said = texts_sent(calls)
    assert "-1005551234" not in " ".join(said)                                          # a member and a stranger get their usual replies only
    assert said[1] == texts.NOT_INVITED.format(uid=77)
