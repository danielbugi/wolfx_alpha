"""The dev-chat reset tool (safety rules, sweep), the client's delete / pin methods, and the channel's fixed posts (Start here, promo caption)."""
import os
import re
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))
sys.path.insert(0, ROOT)

import tg_html  # noqa: E402
from alerts import channel_posts as cp  # noqa: E402
from alerts import dev_chat_reset as dr  # noqa: E402
from alerts import telegram_client as tc  # noqa: E402

BANNED = re.compile(r"\b(buy\w*|sell\w*|targets?|stops?|entry|entries|recommend\w*|signals?|opportunit\w*|picks?|should|profit\w*|"
                    r"winners?|guarantee\w*|alpha|returns?|beat\w*|outperform\w*|earn\w*|money)\b", re.I)


# ================================================================== the reset tool's safety rules
def test_the_reset_tool_refuses_production_a_missing_id_and_channels():
    dr.check_target("-1002222222222", "-1001111111111", "supergroup")                  # the normal case passes
    dr.check_target("-100222", None, "group")
    with pytest.raises(dr.ResetRefused, match="PRODUCTION"):
        dr.check_target("-1001111111111", "-1001111111111", "supergroup")               # dev id == prod id
    with pytest.raises(dr.ResetRefused, match="PRODUCTION"):
        dr.check_target(" -1001111111111 ", "-1001111111111", "supergroup")              # whitespace does not fool it
    for missing in (None, "", "   "):
        with pytest.raises(dr.ResetRefused, match="not set"):
            dr.check_target(missing, "-1001", "supergroup")
    for kind in ("channel", "private", "", "bot"):
        with pytest.raises(dr.ResetRefused, match="not a group"):
            dr.check_target("-100222", "-1001", kind)


class FakeChat:
    """Stands in for TelegramClient: records every delete; ids in `gone` no longer exist, ids in `old` are refused by Telegram."""

    def __init__(self, gone=(), old=(), outage_at=None):
        self.deleted, self.gone, self.old, self.outage_at = [], set(gone), set(old), outage_at

    def delete_message(self, mid):
        if self.outage_at == mid:
            raise tc.TelegramError("giving up after retries: network")
        if mid in self.gone:
            return "missing"
        if mid in self.old:
            return "refused"
        self.deleted.append(mid)
        return "deleted"


def test_the_sweep_tries_every_id_from_the_newest_down_and_reports_each_outcome():
    chat = FakeChat(gone={5, 6}, old={2})
    out = dr.sweep(chat, newest=10, floor=1)
    assert chat.deleted == [10, 9, 8, 7, 4, 3, 1]                                       # newest first, nothing skipped
    assert dict(out["counts"]) == {"deleted": 7, "missing": 2, "refused": 1} and out["refused"] == [2]
    assert dr.sweep(FakeChat(), newest=3, floor=3)["counts"]["deleted"] == 1              # the floor is respected


def test_the_sweep_gives_up_after_a_long_run_of_refusals_because_older_messages_are_out_of_reach():
    chat = FakeChat(old=set(range(1, 9000)))                                             # everything below #9000 is older than 48 h
    out = dr.sweep(chat, newest=9100, floor=1, give_up_after=50)
    assert chat.deleted == list(range(9100, 8999, -1)) and out["gave_up_at"] == 8950     # the recent messages (#9000..#9100) are deleted, then it stops
    assert len(out["refused"]) == 50 and out["counts"]["deleted"] == 101                 # ...instead of trying 9,000 more ids
    assert dr.sweep(FakeChat(old={5, 6}), newest=10, give_up_after=50)["gave_up_at"] is None   # a few refusals never stop a sweep


def test_an_outage_stops_the_sweep_loudly_instead_of_calling_everything_missing():
    with pytest.raises(tc.TelegramError):
        dr.sweep(FakeChat(outage_at=7), newest=10)


def test_the_sweep_reports_progress_every_hundred_ids():
    seen = []
    dr.sweep(FakeChat(), newest=250, floor=1, progress=lambda mid, c: seen.append(mid))
    assert seen == [151, 51]


def test_the_tool_never_names_a_chat_other_than_the_dev_id():
    src = open(os.path.join(ROOT, "mechanism", "alerts", "dev_chat_reset.py"), encoding="utf-8").read()
    assert 'from_env("dev"' in src and 'from_env("prod"' not in src and "TELEGRAM_CHAT_ID" in src.split("check_target")[0] + "TELEGRAM_CHAT_ID"
    assert "args.yes" in src                                                           # nothing is deleted without the flag


# ================================================================== the client's delete / pin
class Resp:
    def __init__(self, data, status=200):
        self._d, self.status_code = data, status

    def json(self):
        return self._d


class FakeHTTP:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def post(self, url, json=None, data=None, files=None, timeout=None):
        self.calls.append((url.rsplit("/", 1)[-1], json))
        return self.responses.pop(0)


def client(*responses, dry=False):
    http = FakeHTTP(*responses)
    return tc.TelegramClient("123456789:" + "A" * 35, "-100222", dry_run=dry, session=http, sleep=lambda s: None), http


@pytest.mark.parametrize("description,expected", [("Bad Request: message to delete not found", "missing"),
                                                  ("Bad Request: message can't be deleted", "refused"),
                                                  ("Bad Request: message can't be deleted for everyone", "refused")])
def test_delete_message_maps_telegrams_answers(description, expected):
    c, http = client(Resp({"ok": False, "description": description}, 400))
    assert c.delete_message(42) == expected and http.calls == [("deleteMessage", {"chat_id": "-100222", "message_id": 42})]


def test_delete_message_success_and_unexpected_errors():
    c, _ = client(Resp({"ok": True, "result": True}))
    assert c.delete_message(1) == "deleted"
    c, _ = client(Resp({"ok": False, "description": "Bad Request: chat not found"}, 400))
    with pytest.raises(tc.TelegramError):
        c.delete_message(1)                                                            # an unknown failure is never silently 'missing'
    c, http = client(dry=True)
    assert c.delete_message(1) == "dry-run" and http.calls == []


def test_pin_and_chat_info():
    c, http = client(Resp({"ok": True, "result": True}), Resp({"ok": True, "result": {"type": "supergroup", "title": "T"}}))
    assert c.pin_message(9) is True and c.chat_info()["type"] == "supergroup"
    assert http.calls[0] == ("pinChatMessage", {"chat_id": "-100222", "message_id": 9, "disable_notification": True})
    dry, _ = client(dry=True)
    assert dry.pin_message(9) is False
    with pytest.raises(tc.TelegramError):
        dry.chat_info()


# ================================================================== the fixed channel posts
def test_start_here_and_promo_caption_are_valid_telegram_html_within_limits():
    assert tg_html.problems(cp.START_HERE) == [] and len(cp.START_HERE) < 4096
    assert tg_html.problems(cp.PROMO_CAPTION, 1024) == [] and len(cp.PROMO_CAPTION) <= 1024


@pytest.mark.parametrize("text", [cp.START_HERE, cp.PROMO_CAPTION])
def test_channel_copy_has_no_advice_words_no_claims_and_the_disclaimer(text):
    plain = re.sub(r"<[^>]+>", "", text)
    assert BANNED.findall(plain) == [] and re.search(r"not investment advice", plain, re.I)
    assert "beta" in plain.lower() and "invitation" in plain.lower()                   # says exactly what access is today
    assert not re.search(r"\d+\s?%\s+(gain|return|profit|win)", plain, re.I)           # no performance claims


def test_start_here_explains_the_lists_the_row_symbols_and_the_assistant():
    plain = re.sub(r"<[^>]+>", "", cp.START_HERE)
    for needle in ("Breakout", "Near breakout", "20 sessions", "3% below", "top gainers", "top ATR", "top volume", "★", "vol", "Private assistant"):
        assert needle in plain, needle


def test_start_here_is_pinned_and_a_pin_failure_does_not_lose_the_post():
    class Fake:
        def __init__(self, fail=False):
            self.sent, self.pinned, self.fail = [], [], fail

        def send_message(self, text, **kw):
            self.sent.append(text)
            return [77]

        def pin_message(self, mid, silent=True):
            if self.fail:
                raise tc.TelegramError("no pin right")
            self.pinned.append(mid)

    f = Fake()
    assert cp.post_start_here(f) == [77] and f.sent == [cp.START_HERE] and f.pinned == [77]
    f = Fake(fail=True)
    assert cp.post_start_here(f) == [77] and f.sent == [cp.START_HERE] and f.pinned == []           # posted, just not pinned
    f = Fake()
    cp.post_start_here(f, pin=False)
    assert f.pinned == []


def test_a_dry_run_client_sends_nothing_for_the_channel_posts():
    dry = tc.TelegramClient(None, None, dry_run=True)
    assert cp.post_start_here(dry) == [None]
    assert cp.post_promo(dry, b"png") is None


def test_the_channel_posts_cannot_reach_production_while_it_is_locked(monkeypatch):
    monkeypatch.setattr(tc, "_load_env", lambda: None)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456789:" + "A" * 35)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-1001111111111")
    monkeypatch.delenv(tc.PROD_SWITCH, raising=False)
    with pytest.raises(tc.TelegramError, match="switched OFF"):
        tc.TelegramClient.from_env("prod", dry_run=False)
    src = open(os.path.join(ROOT, "mechanism", "alerts", "channel_posts.py"), encoding="utf-8").read()
    assert "TelegramClient.from_env(args.to, dry_run=False)" in src                    # goes through the lock like every other sender


# ================================================================== channel = data, promotion, news, information. The assistant lives in the private chat.
ALERTS = os.path.join(ROOT, "mechanism", "alerts")


def _read(name):
    return open(os.path.join(ALERTS, name), encoding="utf-8").read()


def test_the_owner_target_is_the_private_chat_and_is_not_held_by_the_production_lock(monkeypatch):
    monkeypatch.setattr(tc, "_load_env", lambda: None)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456789:" + "A" * 35)
    monkeypatch.setenv("BOT_OWNER_ID", "4242")
    monkeypatch.delenv(tc.PROD_SWITCH, raising=False)
    assert tc.TelegramClient.from_env("owner", dry_run=False).chat_id == "4242"          # a private chat: id = the owner's user id


def test_the_assistant_tour_can_only_go_to_the_owners_private_chat_never_a_channel():
    src = _read("qa_live.py")
    assert 'from_env("owner"' in src and 'from_env("dev"' not in src and 'from_env("prod"' not in src
    ps1 = open(os.path.join(ROOT, "replay_dev_channel.ps1"), encoding="utf-8").read()
    assert "--send-to-dev" not in ps1 and "--send-to-owner" in ps1                        # the replay never sends the tour to the channel
    assert "-Tour" in ps1 and "if ($Tour)" in ps1                                         # ... and even the private tour is opt-in


def _imports(source):
    import ast
    names = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
            names.update(f"{node.module}.{a.name}" for a in node.names)
    return names


@pytest.mark.parametrize("sender", ["send_daily_digest.py", "channel_posts.py", "send_daily_alerts.py", "digest_format.py", "market_card.py"])
def test_channel_senders_do_not_use_the_assistants_screens_or_data(sender):
    """Structural guard: what a channel receives is built from public daily data only. Nothing of the assistant (its screens, the user's
    lists, the access flow, the tracker, the bot process) may be IMPORTED by a module that posts to a channel, and none of them may query the
    assistant's personal tables. (Comments may mention the bot; imports and SQL may not.)"""
    src = _read(sender)
    imported = _imports(src)
    forbidden = {"alerts.screens", "alerts.tracker", "alerts.access", "alerts.run_bot", "alerts.performance", "alerts.news_service", "alerts.chart"}
    assert not (imported & forbidden), (sender, sorted(imported & forbidden))
    assert not ({"screens", "tracker", "access", "run_bot"} & {n.split(".")[-1] for n in imported if n.startswith("alerts")}), sender
    for table in ("bot_tracked", "bot_access", "bot_requests", "bot_invites"):
        assert table not in src, (sender, table)


def test_the_channel_posts_are_the_daily_data_the_promotion_and_the_start_here_post_only():
    from alerts import channel_posts
    assert {n for n in dir(channel_posts) if n.startswith("post_")} == {"post_start_here", "post_promo"}
