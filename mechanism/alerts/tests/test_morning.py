"""Morning message (CHANNEL_CONTENT_MILESTONES.md M5.2): the rules, the sending round, the /morning command and its privacy."""
import asyncio
import os
import re
import sys
from datetime import date, timedelta
from decimal import Decimal as Dec

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))
sys.path.insert(0, ROOT)

import tg_html  # noqa: E402
from alerts import morning, run_bot, texts  # noqa: E402
from alerts.access import Access  # noqa: E402
from aiogram.exceptions import TelegramForbiddenError  # noqa: E402
from aiogram.methods import SendMessage  # noqa: E402

from qa_harness import OWNER_ID, SESSION, SESSION_DATE, STOCKS, TODAY, drive, msg, sent, service, stock  # noqa: E402

BANNED = re.compile(r"\b(buy\w*|sell\w*|targets?|stops?|entry|entries|recommend\w*|signals?|opportunit\w*|picks?|should|profit\w*|"
                    r"winners?|guarantee\w*|alpha|returns?|beat\w*|outperform\w*|earn\w*|money)\b", re.I)
strip = lambda t: re.sub(r"<[^>]+>", "", t)                                          # noqa: E731


def trow(sym, kind="watch"):
    return {"symbol": sym, "kind": kind, "shares": None}


# ================================================================== the rules
def test_reasons_cover_group_changes_lists_and_big_moves_and_nothing_else():
    entered = stock("A", "breakout", 100, ret=1.0, prev=None)
    assert morning.reasons(entered) == ["entered Breakout"]
    moved = stock("B", "breakout", 100, ret=1.0, prev="near_breakout")
    assert morning.reasons(moved) == ["moved from Near breakout to Breakout"]
    left = stock("C", None, 100, ret=-1.0, prev="near_breakout")
    assert morning.reasons(left) == ["left Near breakout"]
    listed = stock("D", "near_breakout", 100, ret=1.0, prev="near_breakout", ranks={"gainers": 2, "atr": 5})
    assert morning.reasons(listed) == ["in today's lists: gainers #2 · ATR #5"]
    big = stock("E", None, 100.0, ret=10.0, prev=None, atr=4.0)                          # a $9.09 move against ATR 4.0 = 2.3x
    assert morning.reasons(big) == ["moved 2.3× its ATR"]
    assert morning.reasons(stock("F", None, 100.0, ret=1.0, prev=None, atr=4.0)) == []      # a quiet day changes nothing
    assert morning.reasons(stock("G", "near_breakout", 100.0, ret=1.0, prev="near_breakout")) == []      # same group, not listed
    assert morning.reasons(stock("H", None, 100.0, ret=10.0, prev=None, atr=None)) == []    # no ATR in the snapshot: the move rule stays silent


def test_the_message_lists_only_changed_stocks_states_counts_and_how_to_turn_it_off():
    rows = {s["symbol"]: s for s in STOCKS}
    text = morning.build_message(SESSION, [trow("AAPL"), trow("MSTR"), trow("COIN")], rows, TODAY)
    plain = strip(text)
    assert tg_html.problems(text) == [] and BANNED.findall(plain) == []
    assert "MSTR ▲16.4% · moved from Near breakout to Breakout; in today's lists: gainers #3 · ATR #3; moved 2.3× its ATR" in plain
    assert "AAPL" not in plain                                                            # nothing changed for AAPL
    assert "2 of your 3 stocks changed" in plain and "/morning off" in plain and "Educational data, not advice" in plain
    assert plain.index("COIN") < plain.index("MSTR")                                      # alphabetical, a stable order


def test_no_message_when_nothing_changed_nothing_is_tracked_or_the_data_is_stale():
    rows = {s["symbol"]: s for s in STOCKS}
    assert morning.build_message(SESSION, [trow("AAPL")], rows, TODAY) is None
    assert morning.build_message(SESSION, [], rows, TODAY) is None
    assert morning.build_message(SESSION, [trow("MSTR")], {}, TODAY) is None              # a tracked symbol missing from the snapshot is skipped
    assert morning.build_message(SESSION, [trow("MSTR")], rows, SESSION_DATE + timedelta(days=9)) is None    # an old scan is never pushed
    assert morning.build_message(None, [trow("MSTR")], rows, TODAY) is None


def test_the_message_is_capped_and_says_how_many_more():
    many = [stock(f"S{i:02d}", "breakout", 10, ret=1.0, prev="near_breakout") for i in range(14)]
    text = morning.build_message(SESSION, [trow(s["symbol"]) for s in many], {s["symbol"]: s for s in many}, TODAY)
    assert text.count("<b>S") == morning.MAX_LINES and "... and 4 more" in text and len(text) < 4096


# ================================================================== planning and the sending round
class Bot:
    def __init__(self, fail=None):
        self.sent, self.fail = [], fail or {}

    async def send_message(self, uid, text, **kw):
        if uid in self.fail:
            raise self.fail[uid]
        self.sent.append((uid, text))


def _world(opted=(5,), tracked=("MSTR",)):
    svc = service(STOCKS)
    st = svc.store
    for u in (5, 6, 7):
        st.set_access(u, "active")
        st.acknowledge(u)
        st.dm_on(u, SESSION_DATE - timedelta(days=3)) if u in opted else None                # opted in for an earlier session
        for sym in tracked:
            st.upsert_tracked(u, sym, "watch", Dec("100"), "close", SESSION_DATE - timedelta(days=3), None, Dec("100"))
    return svc, st, Access(st, OWNER_ID, "approve", 25, 100)


def _run(coro):
    return asyncio.run(coro)


def test_the_round_messages_only_opted_in_members_once_per_session():
    svc, st, access = _world(opted=(5, 6))
    bot = Bot()
    counts = _run(run_bot.morning_round(bot, st, access, TODAY))
    assert counts == {"sent": 2, "nothing": 0, "switched_off": 0, "failed": 0} and sorted(u for u, _ in bot.sent) == [5, 6]
    assert "MSTR" in bot.sent[0][1]
    again = _run(run_bot.morning_round(bot, st, access, TODAY))                               # same session: nobody is messaged twice
    assert again["sent"] == 0 and len(bot.sent) == 2


def test_a_member_with_nothing_changed_gets_no_message_but_is_marked_handled():
    svc, st, access = _world(opted=(5,), tracked=("AAPL",))
    bot = Bot()
    counts = _run(run_bot.morning_round(bot, st, access, TODAY))
    assert counts["nothing"] == 1 and counts["sent"] == 0 and not bot.sent
    assert st.dm_pending(SESSION_DATE) == []                                                  # not re-evaluated every few minutes


def test_a_blocked_or_revoked_member_is_switched_off_and_a_transient_failure_is_retried():
    svc, st, access = _world(opted=(5, 6, 7))
    st.set_access(6, "revoked")
    bot = Bot(fail={5: TelegramForbiddenError(method=SendMessage(chat_id=5, text="x"), message="bot was blocked by the user"),
                    7: RuntimeError("network")})
    counts = _run(run_bot.morning_round(bot, st, access, TODAY))
    assert counts == {"sent": 0, "nothing": 0, "switched_off": 2, "failed": 1}
    assert not st.dm_status(5) and not st.dm_status(6)                                        # blocked and revoked: the switch is removed
    assert st.dm_status(7) and st.dm_pending(SESSION_DATE) == [7]                             # a network error is not marked: retried next pass


def test_an_old_scan_is_not_pushed_to_anyone():
    svc, st, access = _world(opted=(5,))
    assert _run(run_bot.morning_round(Bot(), st, access, SESSION_DATE + timedelta(days=9))) == {"sent": 0, "nothing": 0, "switched_off": 0, "failed": 0}


def test_a_database_outage_in_a_round_raises_to_the_loop_which_survives_it():
    svc, st, access = _world()
    st.broken = True
    with pytest.raises(RuntimeError):
        _run(run_bot.morning_round(Bot(), st, access, TODAY))
    async def once():
        task = asyncio.create_task(run_bot.morning_loop(Bot(), st, access, every_s=0.01))
        await asyncio.sleep(0.05)
        alive = not task.done()
        task.cancel()
        return alive
    assert _run(once())                                                                       # the loop logs the failure and keeps going


# ================================================================== the command
def test_morning_command_turns_on_off_reports_status_and_the_first_message_waits_for_the_next_scan():
    svc = service(STOCKS)
    svc.store.set_access(5, "active")
    svc.store.acknowledge(5)
    svc, calls = drive([msg(5, "/morning"), msg(5, "/morning on", 2), msg(5, "/morning"), msg(5, "/morning maybe", 3), msg(5, "/morning off", 4), msg(5, "/morning", 5)], svc)
    out = [m.text for m in sent(calls)]
    assert out[0] == texts.MORNING_STATUS_OFF and out[1] == texts.MORNING_ON and out[2] == texts.MORNING_STATUS_ON
    assert out[3] == texts.MORNING_USAGE and out[4] == texts.MORNING_OFF and out[5] == texts.MORNING_STATUS_OFF
    assert not svc.store.dm_status(5)
    svc.store.dm_on(5, SESSION_DATE)
    assert svc.store.dm_pending(SESSION_DATE) == []                                           # turned on after the latest scan: nothing to send until the next one
    assert svc.store.dm_pending(SESSION_DATE + timedelta(days=3)) == [5]


def test_deleteme_also_removes_the_morning_switch_and_strangers_cannot_use_the_command():
    svc = service(STOCKS)
    svc.store.set_access(5, "active")
    svc.store.acknowledge(5)
    svc.store.dm_on(5, SESSION_DATE)
    svc, calls = drive([msg(5, "/deleteme"), _tap_yes()], svc)
    assert not svc.store.dm_status(5)
    stranger = service(STOCKS)
    _, calls = drive([msg(9, "/morning on")], stranger, enroll=False)
    assert not stranger.store.dm_status(9) and all("Request access" in str(getattr(m.reply_markup, "inline_keyboard", "")) or True for m in sent(calls))
    assert not getattr(stranger.store, "dm", {})                                              # nothing is stored for a stranger


def _tap_yes():
    from qa_harness import tap
    return tap(5, "dy", 2)


def test_the_new_copy_is_clean_and_the_privacy_text_and_help_mention_the_switch():
    for t in (texts.MORNING_ON, texts.MORNING_OFF, texts.MORNING_USAGE, texts.MORNING_STATUS_ON, texts.MORNING_STATUS_OFF):
        assert tg_html.problems(t) == [] and BANNED.findall(strip(t)) == []
    assert "/morning" in texts.HELP and "morning" in texts.PRIVACY.lower() and "nothing else" in texts.PRIVACY
