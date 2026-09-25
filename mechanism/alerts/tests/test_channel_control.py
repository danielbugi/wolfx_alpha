"""The Telegram Control Center: the message ledger (memory + real Postgres run the same contract), the client's edit / recording, and every rule of
channel_control.py (production lock, wrong-chat guard, 48 h delete window, limits, wording guard, audit, overview). No network, no Telegram."""
import json
import logging
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))
sys.path.insert(0, ROOT)

from alerts import channel_control as cc  # noqa: E402
from alerts import message_ledger as ml  # noqa: E402
from alerts import telegram_client as tc  # noqa: E402
from test_channel_tools import BANNED, Resp, client  # noqa: E402

UTC = timezone.utc
NOW = datetime(2026, 9, 21, 22, 30, tzinfo=UTC)          # = 01:30 on 22 Sep in Jerusalem (UTC+3)
KEYBOARD = {"inline_keyboard": [[{"text": "Private assistant", "url": "https://t.me/some_bot?start=ch"}]]}


# ================================================================== the ledger contract, memory and real Postgres
def _pg():
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(ROOT, ".env"))
        from shared import db
        db.execute_dict_query("SELECT 1 FROM telegram_messages LIMIT 1")
        db.execute_dict_query("SELECT 1 FROM telegram_control_audit LIMIT 1")
        return db
    except Exception as e:                                                          # noqa: BLE001
        pytest.skip(f"Postgres unreachable or the telegram tables are missing: {type(e).__name__}")


@pytest.fixture(params=["memory", "postgres"])
def ledger(request):
    """A ledger plus a unique tag: rows are recorded under a unique chat / kind, so the real database (which holds real posts) never interferes."""
    tag = "ctl" + uuid.uuid4().hex[:10]
    if request.param == "memory":
        yield ml.MemoryLedger(), tag
        return
    db = _pg()
    yield ml.PgLedger(db), tag
    db.execute_insert("DELETE FROM telegram_messages WHERE chat_id = %s", (tag,))
    db.execute_insert("DELETE FROM telegram_control_audit WHERE chat_id = %s", (tag,))


def put(lg, tag, mid, *, kind=None, text="hello", ctype="text", at=NOW, target="dev", markup=None):
    lg.record_sent(target=target, chat_id=tag, message_id=mid, kind=kind or tag, content_type=ctype, text=text, reply_markup=markup,
                   silent=True, disable_preview=True, sent_at=at)


def row_of(lg, tag, mid):
    rows, _ = lg.list(kind=tag, limit=200)
    return next(r for r in rows if r["message_id"] == mid)


def test_ledger_lists_newest_first_and_filters_by_status_day_and_text(ledger):
    lg, tag = ledger
    put(lg, tag, 1, text="first 100% sure_thing", at=NOW - timedelta(days=2))
    put(lg, tag, 2, text="second", at=NOW - timedelta(hours=5))
    put(lg, tag, 3, text="third", at=NOW)
    lg.record_edited(chat_id=tag, message_id=2, text="second, edited")
    lg.record_deleted(chat_id=tag, message_id=1)
    rows, total = lg.list(kind=tag)
    assert [r["message_id"] for r in rows] == [3, 2, 1] and total == 3                               # newest first
    assert [r["message_id"] for r in lg.list(kind=tag, status="edited")[0]] == [2]                    # 'edited' = still up and edited
    assert [r["message_id"] for r in lg.list(kind=tag, status="deleted")[0]] == [1]
    assert [r["message_id"] for r in lg.list(kind=tag, status="sent")[0]] == [3, 2]
    assert [r["message_id"] for r in lg.list(kind=tag, since=NOW - timedelta(hours=6), until=NOW)[0]] == [2]      # since inclusive, until exclusive
    assert [r["message_id"] for r in lg.list(kind=tag, q="SECOND")[0]] == [2]                         # case-insensitive
    assert [r["message_id"] for r in lg.list(kind=tag, q="100%")[0]] == [1]                           # % and _ are literal, not wildcards
    assert lg.list(kind=tag, q="_")[1] == 1 and lg.list(kind=tag, q="%")[1] == 1
    assert lg.list(kind=tag, target="prod")[1] == 0
    page, total = lg.list(kind=tag, limit=2, offset=2)
    assert [r["message_id"] for r in page] == [1] and total == 3


def test_ledger_edit_delete_pin_and_duplicates(ledger):
    lg, tag = ledger
    put(lg, tag, 7, text="original", markup=KEYBOARD)
    put(lg, tag, 7, text="a duplicate send report is ignored")
    r = row_of(lg, tag, 7)
    assert r["text"] == r["original_text"] == "original" and r["reply_markup"] == KEYBOARD and r["status"] == "sent" and r["edit_count"] == 0
    lg.record_edited(chat_id=tag, message_id=7, text="v2")
    lg.record_edited(chat_id=tag, message_id=7, text="v3")
    r = row_of(lg, tag, 7)
    assert (r["text"], r["original_text"], r["edit_count"]) == ("v3", "original", 2) and r["edited_at"] is not None       # the original is kept
    lg.record_pinned(chat_id=tag, message_id=7)
    assert row_of(lg, tag, 7)["pinned"] is True
    lg.record_deleted(chat_id=tag, message_id=7)
    first = row_of(lg, tag, 7)["deleted_at"]
    lg.record_deleted(chat_id=tag, message_id=7)
    r = row_of(lg, tag, 7)
    assert r["status"] == "deleted" and r["deleted_at"] == first and r["pinned"] is False                    # idempotent; a deleted post is not pinned
    lg.record_pinned(chat_id=tag, message_id=7)
    assert row_of(lg, tag, 7)["pinned"] is False
    lg.record_edited(chat_id=tag, message_id=999, text="unknown message")                                    # an unknown message is a no-op, not an error
    assert lg.get(r["id"])["message_id"] == 7 and lg.get(-5) is None


def test_ledger_counts_kinds_and_audit(ledger):
    lg, tag = ledger
    before = lg.counts("dev", NOW - timedelta(hours=1))
    put(lg, tag, 1, at=NOW - timedelta(days=3))
    put(lg, tag, 2, at=NOW)
    put(lg, tag, 3, at=NOW, kind=tag + "b")
    lg.record_edited(chat_id=tag, message_id=2, text="x")
    lg.record_deleted(chat_id=tag, message_id=3)
    after = lg.counts("dev", NOW - timedelta(hours=1))
    delta = {k: after[k] - before[k] for k in ("total", "since_count", "live", "edited", "deleted")}
    assert delta == {"total": 3, "since_count": 2, "live": 2, "edited": 1, "deleted": 1}
    assert after["last_sent"] >= NOW and after["first_sent"] <= NOW - timedelta(days=3)
    assert {k["kind"]: k["count"] for k in lg.kinds("dev") if k["kind"].startswith(tag)} == {tag: 2, tag + "b": 1}
    lg.audit_add(action="edit", target="dev", chat_id=tag, message_id=2, outcome="done", detail=None)
    lg.audit_add(action="delete", target="dev", chat_id=tag, message_id=2, outcome="failed", detail="x" * 500)
    trail = lg.audit_for(tag, 2)
    assert [(a["action"], a["outcome"]) for a in trail] == [("delete", "failed"), ("edit", "done")] and len(trail[0]["detail"]) == 200
    assert lg.audit_for(tag, 3) == []


def test_ledger_empty_state_is_null_not_zero_dates(ledger):
    lg, _ = ledger
    c = lg.counts("prod", datetime(2999, 1, 1, tzinfo=UTC))
    assert c["since_count"] == 0 and c["total"] >= 0
    if isinstance(lg, ml.MemoryLedger):
        assert c["last_sent"] is None and c["first_sent"] is None


def test_the_postgres_ledger_rejects_bad_values_at_the_database(ledger):
    lg, tag = ledger
    if not isinstance(lg, ml.PgLedger):
        pytest.skip("database constraints only exist in Postgres")
    with pytest.raises(Exception):
        put(lg, tag, 1, target="owner")                                                                     # the private chat is never a ledger target


# ================================================================== the recorder (what TelegramClient calls)
def test_the_recorder_only_accepts_channel_targets_and_is_lazy():
    with pytest.raises(ValueError):
        ml.LedgerRecorder("owner")
    built = []
    rec = ml.LedgerRecorder("dev", ledger_factory=lambda: built.append(1) or ml.MemoryLedger())
    assert built == []                                                                                      # nothing is opened until the first event
    rec.sent(chat_id="c", message_id=1, kind=None, content_type="text", text="t")
    rec.edited(chat_id="c", message_id=1, text="t2")
    assert built == [1]                                                                                     # opened once, reused


def test_a_ledger_failure_never_breaks_a_send_and_the_log_holds_no_text_or_token(caplog):
    class Broken:
        def record_sent(self, **kw):
            raise RuntimeError("connection to secret-host with SECRET:TOKEN failed")
    c, http = client(Resp({"ok": True, "result": {"message_id": 5}}))
    c.recorder = ml.LedgerRecorder("dev", ledger_factory=Broken)
    with caplog.at_level(logging.WARNING):
        assert c.send_message("the body of the post") == [5]                                                # delivered, and no exception
    log = caplog.text
    assert "could not record send" in log and "RuntimeError" in log
    assert "SECRET:TOKEN" not in log and "secret-host" not in log and "the body of the post" not in log


def test_sends_are_recorded_with_kind_parts_keyboard_and_photo_caption():
    mem = ml.MemoryLedger()
    c, _ = client(*[Resp({"ok": True, "result": {"message_id": m}}) for m in (10, 11, 12)])
    c.recorder = ml.LedgerRecorder("dev", ledger_factory=lambda: mem)
    long_text = "\n".join(f"line {i:04d} " + "x" * 60 for i in range(70))                                    # ~4,970 parsed chars: split in two parts
    ids = c.send_message(long_text, silent=True, reply_markup=KEYBOARD, kind="digest_list", disable_preview=False)
    assert ids == [10, 11]
    a, b = mem.rows
    assert (a["kind"], a["content_type"], a["silent"], a["disable_preview"], a["target"], a["chat_id"]) == ("digest_list", "text", True, False, "dev", "-100222")
    assert a["reply_markup"] is None and b["reply_markup"] == KEYBOARD                                     # the keyboard is on the LAST part only
    assert a["text"] + "\n" + b["text"] == long_text
    c.send_photo(b"png", "<b>caption</b>", reply_markup=KEYBOARD, kind="digest_card")
    p = mem.rows[-1]
    assert (p["kind"], p["content_type"], p["text"], p["message_id"]) == ("digest_card", "photo", "<b>caption</b>", 12)
    c.recorder.sent(chat_id="c", message_id=1, kind=None, content_type="text", text="t")
    assert mem.rows[-1]["kind"] == "other"                                                                  # an unlabelled send is 'other'


def test_dry_runs_and_failed_sends_record_nothing():
    mem = ml.MemoryLedger()
    dry, _ = client(dry=True)
    dry.recorder = ml.LedgerRecorder("dev", ledger_factory=lambda: mem)
    dry.send_message("x"); dry.send_photo(b"p", "c"); dry.delete_message(1); dry.pin_message(1)
    assert mem.rows == []
    c, _ = client(Resp({"ok": False, "description": "Bad Request: chat not found"}, 400))
    c.recorder = ml.LedgerRecorder("dev", ledger_factory=lambda: mem)
    with pytest.raises(tc.TelegramError):
        c.send_message("x")
    assert mem.rows == []


def test_delete_pin_and_edit_update_the_ledger_only_when_telegram_says_so():
    mem = ml.MemoryLedger()
    for m in (1, 2, 3, 4):
        put(mem, "-100222", m, kind="x")
    c, _ = client(Resp({"ok": True, "result": True}),                                                        # delete 1 -> deleted
                  Resp({"ok": False, "description": "Bad Request: message to delete not found"}, 400),       # delete 2 -> missing (gone either way)
                  Resp({"ok": False, "description": "Bad Request: message can't be deleted"}, 400),          # delete 3 -> refused (still there)
                  Resp({"ok": True, "result": True}),                                                        # pin 4
                  Resp({"ok": True, "result": True}),                                                        # edit 4 -> edited
                  Resp({"ok": False, "description": "Bad Request: message is not modified"}, 400))           # edit 4 again -> unchanged
    c.recorder = ml.LedgerRecorder("dev", ledger_factory=lambda: mem)
    assert [c.delete_message(1), c.delete_message(2), c.delete_message(3)] == ["deleted", "missing", "refused"]
    assert c.pin_message(4) is True
    assert c.edit_message(4, "new") == "edited" and c.edit_message(4, "new") == "unchanged"
    by = {r["message_id"]: r for r in mem.rows}
    assert [by[m]["status"] for m in (1, 2, 3)] == ["deleted", "deleted", "sent"]
    assert by[4]["pinned"] is True and by[4]["text"] == "new" and by[4]["edit_count"] == 1                   # 'unchanged' is not an edit


def test_from_env_records_channels_only(monkeypatch):
    monkeypatch.setattr(tc, "_load_env", lambda: None)
    for key, val in (("TELEGRAM_BOT_TOKEN", "123456789:" + "A" * 35), ("TELEGRAM_DEV_CHAT_ID", "-100222"), ("TELEGRAM_CHAT_ID", "-100111"),
                     ("BOT_OWNER_ID", "4242"), (tc.PROD_SWITCH, "1")):
        monkeypatch.setenv(key, val)
    dev = tc.TelegramClient.from_env("dev", dry_run=False)
    prod = tc.TelegramClient.from_env("prod", dry_run=False)
    assert isinstance(dev.recorder, ml.LedgerRecorder) and dev.recorder.target == "dev" and dev.target == "dev"
    assert prod.recorder.target == "prod"
    assert tc.TelegramClient.from_env("owner", dry_run=False).recorder is None                              # the private chat is never recorded
    assert tc.TelegramClient.from_env("dev", dry_run=True).recorder is None and tc.TelegramClient.from_env("prod", dry_run=True).recorder is None


def test_every_channel_sender_labels_its_posts():
    """A post without a kind lands in the ledger as 'other' and cannot be filtered: each sender must pass one."""
    alerts = os.path.join(ROOT, "mechanism", "alerts")
    expect = {"send_daily_digest.py": ['kind="digest_card"', '"digest_header"', '"digest_list"'], "send_channel_posts.py": ["kind=post.kind"],
              "send_channel_notices.py": ["kind=p.kind"], "channel_posts.py": ['kind="start_here"', 'kind="promo"'],
              "send_daily_alerts.py": ['kind="alert_header"', 'kind="alert_card"']}
    for name, needles in expect.items():
        src = open(os.path.join(alerts, name), encoding="utf-8").read()
        for needle in needles:
            assert needle in src, (name, needle)
    assert {k for k in cc.KIND_LABELS} >= {"digest_card", "digest_header", "digest_list", "start_here", "promo", "alert_header", "alert_card", "other"}


# ================================================================== the client's edit
def test_edit_message_sends_text_or_caption_keeps_the_keyboard_and_maps_answers():
    c, http = client(Resp({"ok": True, "result": True}), Resp({"ok": True, "result": True}))
    assert c.edit_message(9, "<b>new</b>", reply_markup=KEYBOARD, disable_preview=False) == "edited"
    assert c.edit_message(10, "cap", caption=True, reply_markup=KEYBOARD) == "edited"
    (m1, p1), (m2, p2) = http.calls
    assert m1 == "editMessageText" and p1 == {"chat_id": "-100222", "message_id": 9, "parse_mode": "HTML", "text": "<b>new</b>",
                                              "disable_web_page_preview": False, "reply_markup": KEYBOARD}
    assert m2 == "editMessageCaption" and p2["caption"] == "cap" and "text" not in p2 and p2["reply_markup"] == KEYBOARD
    for description, expected in (("Bad Request: message is not modified: specified new message content and reply markup are exactly the same", "unchanged"),
                                  ("Bad Request: message to edit not found", "missing")):
        c, _ = client(Resp({"ok": False, "description": description}, 400))
        assert c.edit_message(1, "x") == expected
    c, _ = client(Resp({"ok": False, "description": "Bad Request: chat not found"}, 400))
    with pytest.raises(tc.TelegramError):
        c.edit_message(1, "x")                                                                              # "chat not found" is never read as "message gone"


def test_edit_message_validates_before_any_request():
    c, http = client()
    with pytest.raises(tc.TelegramError, match="4096"):
        c.edit_message(1, "x" * 4097)
    with pytest.raises(tc.TelegramError, match="1024"):
        c.edit_message(1, "x" * 1025, caption=True)
    with pytest.raises(tc.TelegramError, match="empty"):
        c.edit_message(1, "  \n ")
    assert http.calls == []
    dry, dry_http = client(dry=True)
    assert dry.edit_message(1, "ok") == "dry-run" and dry_http.calls == []


# ================================================================== the rules
class FakeClient:
    """Stands in for the Telegram client of the control layer (edit/delete/pin/unpin/photo-replace of an EXISTING row): records calls,
    answers as configured."""

    def __init__(self, edit="edited", delete="deleted", unpin="unpinned", raises=None):
        self.edit_result, self.delete_result, self.unpin_result, self.raises, self.calls, self.recorder = edit, delete, unpin, raises, [], "unset"

    def edit_message(self, message_id, text, **kw):
        self.calls.append(("edit", message_id, text, kw))
        if self.raises:
            raise self.raises
        return self.edit_result

    def delete_message(self, message_id):
        self.calls.append(("delete", message_id))
        if self.raises:
            raise self.raises
        return self.delete_result

    def pin_message(self, message_id, silent=True):
        self.calls.append(("pin", message_id))
        if self.raises:
            raise self.raises
        return True

    def unpin_message(self, message_id):
        self.calls.append(("unpin", message_id))
        if self.raises:
            raise self.raises
        return self.unpin_result

    def replace_photo(self, message_id, png, caption, reply_markup=None):
        self.calls.append(("replace_photo", message_id, png, caption, reply_markup))
        if self.raises:
            raise self.raises
        return self.edit_result


class FakeSendClient:
    """Stands in for a WHOLE real TelegramClient + its attached LedgerRecorder (compose's `_send_factory`, never `_factory`): its
    send_message/send_photo write straight into the same ledger channel_control was given, exactly as the real recorder would."""

    def __init__(self, ledger, target, chat_id, raises=None, start_id=500):
        self.ledger, self.target, self.chat_id, self.raises, self.calls = ledger, target, chat_id, raises, []
        self._next = start_id

    def send_message(self, text, disable_preview=True, silent=False, reply_markup=None, kind=None):
        self.calls.append(("send_message", text, kind, silent, disable_preview))
        if self.raises:
            raise self.raises
        mid = self._next
        self._next += 1
        self.ledger.record_sent(target=self.target, chat_id=self.chat_id, message_id=mid, kind=kind or "manual", content_type="text",
                                text=text, reply_markup=reply_markup, silent=silent, disable_preview=disable_preview)
        return [mid]

    def send_photo(self, png, caption="", silent=False, reply_markup=None, kind=None):
        self.calls.append(("send_photo", caption, kind, silent))
        if self.raises:
            raise self.raises
        mid = self._next
        self._next += 1
        self.ledger.record_sent(target=self.target, chat_id=self.chat_id, message_id=mid, kind=kind or "manual", content_type="photo",
                                text=caption, reply_markup=reply_markup, silent=silent, disable_preview=True)
        return mid


ENV = {"TELEGRAM_BOT_TOKEN": "123456789:" + "S" * 35, "TELEGRAM_DEV_CHAT_ID": "-1002222222222", "TELEGRAM_CHAT_ID": "-1001111111111",
       "PROD_SENDING_ENABLED": "0", "TELEGRAM_CONTROL_TOKEN": "control-secret-value", "ALERTS_TIMEZONE": "Asia/Jerusalem", "BOT_OWNER_ID": "4242"}


def make(env=None, fake=None, now=NOW, send_fake=None):
    ledger, fake = ml.MemoryLedger(clock=lambda: now), fake or FakeClient()
    built, send_built = [], []

    def factory(target):
        built.append(target)
        return fake

    def send_factory(target):
        send_built.append(target)
        return send_fake
    control = cc.ChannelControl(ledger, client_factory=factory, env={**ENV, **(env or {})}, now=lambda: now, send_client_factory=send_factory)
    control.send_built = send_built          # exposed for assertions, not part of the real class's API
    return control, ledger, fake, built


def seed(ledger, mid=1, *, target="dev", text="Hello <b>world</b>", ctype="text", age=timedelta(hours=1), chat=None, markup=KEYBOARD,
         kind="board"):
    chat = chat or ENV["TELEGRAM_DEV_CHAT_ID" if target == "dev" else "TELEGRAM_CHAT_ID"]
    ledger.record_sent(target=target, chat_id=chat, message_id=mid, kind=kind, content_type=ctype, text=text, reply_markup=markup,
                       silent=True, disable_preview=False, sent_at=NOW - age)
    return ledger.rows[-1]["id"]


def refused(fn, *args, **kw):
    with pytest.raises(cc.ControlError) as e:
        fn(*args, **kw)
    return e.value


def test_production_is_view_only_while_locked_and_nothing_reaches_telegram():
    control, ledger, fake, built = make()
    pid = seed(ledger, target="prod")
    assert control.list_messages(target="prod")["total"] == 1                                                # listing is fine
    for action in (lambda: control.edit(pid, "changed"), lambda: control.delete(pid)):
        e = refused(action)
        assert (e.code, e.http) == ("locked", 423)
    assert built == [] and fake.calls == [] and ledger.audit == []                                          # not even a client was built
    m = control.get_message(pid)
    assert m["can_edit"]["code"] == m["can_delete"]["code"] == "locked" and "view only" in m["can_edit"]["reason"]
    control, ledger, fake, built = make(env={"PROD_SENDING_ENABLED": "1"})
    pid = seed(ledger, target="prod")
    assert control.edit(pid, "changed")["result"] == "edited" and built == ["prod"]                         # launched: allowed


@pytest.mark.parametrize("value", ["", "0", "true", "yes", " 2 "])
def test_only_exactly_one_opens_production(value):
    control, ledger, *_ = make(env={"PROD_SENDING_ENABLED": value})
    assert control.prod_locked() is True and control.can_edit(ledger.get(seed(ledger, target="prod"))).code == "locked"


def test_a_row_of_a_replaced_channel_is_never_acted_on():
    """Message ids are per chat: after the channel is replaced, the old row's id would name a DIFFERENT message in the new chat."""
    control, ledger, fake, built = make()
    rid = seed(ledger, chat="-1009999999999")                                                                # posted to a chat that is no longer the dev channel
    for action in (lambda: control.edit(rid, "x"), lambda: control.delete(rid)):
        e = refused(action)
        assert (e.code, e.http) == ("wrong_chat", 409) and "no longer the configured" in e.message
    assert built == []
    control, ledger, *_ = make(env={"TELEGRAM_DEV_CHAT_ID": ""})
    assert refused(control.delete, seed(ledger)).code == "not_configured"


def test_a_deleted_message_cannot_be_edited_or_deleted_again():
    control, ledger, fake, built = make()
    rid = seed(ledger)
    control.delete(rid)
    assert refused(control.edit, rid, "again").code == "deleted" and refused(control.delete, rid).code == "deleted"
    assert len([c for c in fake.calls if c[0] == "delete"]) == 1


def test_delete_window_is_48_hours():
    control, ledger, fake, built = make()
    inside = seed(ledger, 1, age=timedelta(hours=47, minutes=59))
    edge = seed(ledger, 2, age=timedelta(hours=48))
    old = seed(ledger, 3, age=timedelta(days=9))
    assert control.get_message(inside)["can_delete"]["ok"] is True
    for rid in (edge, old):
        e = refused(control.delete, rid)
        assert (e.code, e.http) == ("too_old", 409) and "by hand" in e.message
        assert control.get_message(rid)["can_edit"]["ok"] is True                                          # editing has no such window
    assert control.delete(inside)["result"] == "deleted"
    assert [c[1] for c in fake.calls] == [1]


def test_telegram_refusing_a_delete_is_reported_audited_and_the_row_stays_up():
    control, ledger, fake, _ = make(fake=FakeClient(delete="refused"))
    rid = seed(ledger)
    e = refused(control.delete, rid)
    assert e.code == "too_old"
    assert ledger.get(rid)["status"] == "sent" and [(a["action"], a["outcome"]) for a in ledger.audit] == [("delete", "refused")]


def test_delete_success_and_already_gone_are_both_recorded_as_deleted():
    for answer, outcome in (("deleted", "done"), ("missing", "gone")):
        control, ledger, fake, _ = make(fake=FakeClient(delete=answer))
        rid = seed(ledger)
        out = control.delete(rid)
        assert out["result"] == answer and out["message"]["status"] == "deleted" and out["message"]["deleted_at"] is not None
        assert ledger.audit[-1]["outcome"] == outcome and fake.calls == [("delete", 1)]


def test_edit_replaces_the_text_keeps_the_keyboard_and_the_original():
    control, ledger, fake, _ = make()
    rid = seed(ledger, 5, text="Hello <b>world</b>")
    out = control.edit(rid, "Hello <b>everyone</b>")
    (kind, mid, text, kw), = fake.calls
    assert (kind, mid, text) == ("edit", 5, "Hello <b>everyone</b>")
    assert kw == {"caption": False, "reply_markup": KEYBOARD, "disable_preview": False}                     # the buttons are sent again, the preview flag kept
    m = out["message"]
    assert out["result"] == "edited" and m["text"] == "Hello <b>everyone</b>" and m["original_text"] == "Hello <b>world</b>" and m["edit_count"] == 1
    assert m["edited_at"] is not None and m["status"] == "sent"
    assert [(a["action"], a["outcome"]) for a in m["audit"]] == [("edit", "done")]
    control.edit(rid, "Hello <b>world</b>")                                                                # restoring the original is just another edit
    assert ledger.get(rid)["text"] == ledger.get(rid)["original_text"] and ledger.get(rid)["edit_count"] == 2


def test_editing_a_photo_edits_its_caption():
    control, ledger, fake, _ = make()
    control.edit(seed(ledger, ctype="photo", text="caption"), "new caption")
    assert fake.calls[0][3]["caption"] is True


def test_edit_limits_count_parsed_characters_and_reject_empty_or_unchanged():
    control, ledger, fake, _ = make()
    rid = seed(ledger, text="a")
    pid = seed(ledger, 2, text="a", ctype="photo")
    assert refused(control.edit, rid, "   ").code == "empty"
    e = refused(control.edit, rid, "x" * 4097)
    assert (e.code, e.http) == ("too_long", 422) and "4097" in e.message and "4096" in e.message
    assert refused(control.edit, pid, "x" * 1025).code == "too_long"                                        # a caption's limit is 1,024
    assert refused(control.edit, rid, "a").code == "unchanged"
    assert fake.calls == []
    link = '<a href="https://example.com/' + "p" * 300 + '">' + "x" * 4090 + "</a>"                         # 4,090 parsed characters: the URL does not count
    assert control.edit(rid, link)["result"] == "edited"


def test_the_wording_guard_blocks_new_advice_words_unless_acknowledged():
    control, ledger, fake, _ = make()
    rid = seed(ledger, text="Stocks that moved today")
    e = refused(control.edit, rid, "Stocks to <b>buy</b> today, a great pick")
    assert (e.code, e.http, e.warnings) == ("wording", 422, ["buy", "pick"]) and fake.calls == []
    assert control.edit(rid, "Stocks to buy today", acknowledge_wording=True)["result"] == "edited"
    assert "wording acknowledged: buy" in ledger.audit[-1]["detail"]
    control.edit(rid, "Stocks to buy today, edited again")                                                    # 'buy' was already there: not a NEW word
    assert ledger.get(rid)["text"].endswith("edited again")


def test_the_wording_guard_is_the_same_pattern_the_channel_copy_tests_enforce():
    assert cc.ADVICE_WORDS.pattern == BANNED.pattern and cc.ADVICE_WORDS.flags == BANNED.flags
    assert cc.wording_hits("<b>Buy</b> the STOP &amp; sell") == ["buy", "sell", "stop"] and cc.wording_hits("nothing here") == []


def test_a_telegram_failure_is_reported_audited_and_changes_nothing_and_leaks_no_token():
    control, ledger, fake, _ = make(fake=FakeClient(raises=tc.TelegramError("Telegram rejected the request: can't parse entities <token>")))
    rid = seed(ledger, text="before")
    e = refused(control.edit, rid, "<b>after")
    assert (e.code, e.http) == ("telegram", 502) and "parse entities" in e.message
    assert ledger.get(rid)["text"] == "before" and ledger.get(rid)["edit_count"] == 0
    assert (ledger.audit[-1]["action"], ledger.audit[-1]["outcome"]) == ("edit", "failed") and ENV["TELEGRAM_BOT_TOKEN"] not in json.dumps(ledger.audit, default=str)
    e = refused(control.delete, rid)
    assert e.code == "telegram" and ledger.audit[-1]["action"] == "delete" and ledger.get(rid)["status"] == "sent"


def test_telegram_saying_not_modified_or_gone_is_recorded_honestly():
    control, ledger, fake, _ = make(fake=FakeClient(edit="unchanged"))
    rid = seed(ledger, text="a")
    out = control.edit(rid, "b")
    # "not modified" = Telegram ALREADY shows "b" (e.g. a retried attempt of this same edit had applied it): the record follows Telegram, not the old text
    assert out["result"] == "unchanged" and ledger.get(rid)["text"] == "b" and out["message"]["text"] == "b"
    assert ledger.audit[-1]["outcome"] == "unchanged" and "brought in line" in ledger.audit[-1]["detail"]
    control, ledger, fake, _ = make(fake=FakeClient(edit="missing"))
    rid = seed(ledger, text="a")
    assert control.edit(rid, "b")["message"]["status"] == "deleted" and ledger.audit[-1]["outcome"] == "gone"


def test_the_control_layer_never_lets_the_client_record_on_its_own():
    """One writer per action: the real client comes from from_env (the production lock) with its recorder switched off."""
    import inspect
    src = inspect.getsource(cc.ChannelControl._real_client)
    assert "TelegramClient.from_env(target, dry_run=False)" in src and "client.recorder = None" in src


def test_unknown_message_and_bad_filters_are_refused():
    control, *_ = make()
    assert (refused(control.get_message, 404).code, refused(control.edit, 404, "x").http) == ("not_found", 404)
    for bad in ({"target": "owner"}, {"status": "weird"}, {"day": "21/09/2026"}):
        e = refused(control.list_messages, **bad)
        assert (e.code, e.http) == ("bad_filter", 422)                                                      # a malformed filter is not a missing resource


def test_list_uses_the_local_day_and_never_exposes_the_chat_id():
    control, ledger, *_ = make()
    seed(ledger, 1, age=timedelta(hours=2, minutes=29))               # 20:01 UTC = 23:01 on 21 Sep in Jerusalem
    seed(ledger, 2, age=timedelta(minutes=10))                        # 22:20 UTC = 01:20 on 22 Sep in Jerusalem
    assert [m["message_id"] for m in control.list_messages(day="2026-09-21")["items"]] == [1]
    assert [m["message_id"] for m in control.list_messages(day="2026-09-22")["items"]] == [2]
    out = control.list_messages()
    assert out["timezone"] == "Asia/Jerusalem" and out["total"] == 2
    blob = json.dumps(out)
    assert ENV["TELEGRAM_DEV_CHAT_ID"] not in blob and "chat_id" not in blob and "original_text" not in blob        # the list is light; the detail has the text
    d = control.get_message(1)
    assert d["text"] == "Hello <b>world</b>" and d["buttons"] == [[{"text": "Private assistant", "url": "https://t.me/some_bot?start=ch"}]]
    assert d["limit"] == 4096 and "chat_id" not in json.dumps(d)


def test_overview_counts_today_in_local_time_and_says_when_history_starts():
    control, ledger, *_ = make()
    o = control.overview()
    assert o["tracking_since"] is None and all(t["recorded"] == 0 and t["last_sent_at"] is None for t in o["targets"])       # empty = null, not a date
    seed(ledger, 1, age=timedelta(hours=6))                           # 16:30 UTC 21 Sep = 19:30 local: NOT today (today is 22 Sep locally)
    seed(ledger, 2, age=timedelta(minutes=30))                        # 22:00 UTC = 01:00 local 22 Sep: today
    ledger.record_deleted(chat_id=ENV["TELEGRAM_DEV_CHAT_ID"], message_id=1)
    seed(ledger, 3, target="prod")
    o = control.overview()
    dev, prod = o["targets"]
    assert (dev["target"], dev["sent_today"], dev["recorded"], dev["live"], dev["deleted"], dev["edited"]) == ("dev", 1, 2, 1, 1, 0)
    assert (prod["locked"], prod["recorded"]) == (True, 1) and dev["locked"] is False
    assert o["tracking_since"] == (NOW - timedelta(hours=6)).isoformat() and o["timezone"] == "Asia/Jerusalem"
    # seed()'s default kind="board" is the pre-2026-09-25 Post.kind string -- this assertion is now also an
    # implicit backward-compat regression check: a historical ledger row still aggregates/labels correctly
    # without any database rewrite (channel_content.py's Post.kind values were renamed to "momentum_board"/
    # "market_health" that day; KIND_LABELS deliberately keeps both old and new strings mapped to one label).
    assert {k["kind"]: k["count"] for k in o["kinds"]} == {"board": 3} and o["kinds"][0]["label"] == "Momentum board"


def test_overview_labels_the_new_canonical_kind_string_the_same_as_the_historical_one():
    """The 2026-09-25 rename's other half: a NEW row using "momentum_board" (what every sender writes going
    forward) must aggregate/label identically to an old "board" row -- proven side by side in one ledger."""
    control, ledger, *_ = make()
    seed(ledger, 1, kind="board")
    seed(ledger, 2, kind="momentum_board")
    seed(ledger, 3, kind="market_health")
    o = control.overview()
    kinds = {k["kind"]: (k["count"], k["label"]) for k in o["kinds"]}
    assert kinds["board"] == (1, "Momentum board")
    assert kinds["momentum_board"] == (1, "Momentum board")
    assert kinds["market_health"] == (1, "Market health")


def test_overview_config_and_warnings_never_reveal_secrets():
    control, *_ = make()
    o = control.overview()
    blob = json.dumps(o)
    for secret in (ENV["TELEGRAM_BOT_TOKEN"], ENV["TELEGRAM_CONTROL_TOKEN"], ENV["TELEGRAM_DEV_CHAT_ID"], ENV["TELEGRAM_CHAT_ID"], ENV["BOT_OWNER_ID"]):
        assert secret not in blob
    cfg = {i["key"]: i["value"] for i in o["config"]}
    assert cfg["PROD_SENDING_ENABLED"] == "Locked" and cfg["TELEGRAM_CONTROL_TOKEN"] == "Enabled" and cfg["TELEGRAM_BOT_TOKEN"] == "Set"
    assert cfg["CHANNEL_BOARD_ENABLED"] == "On" and cfg["CHANNEL_NEWS_ENABLED"] == "Off" and cfg["BOT_ACCESS_MODE"] == "approve"
    assert {t["target"]: t["chat_hint"] for t in o["targets"]} == {"dev": "…2222", "prod": "…1111"} and o["warnings"] == []
    bare, *_ = make(env={"TELEGRAM_CONTROL_TOKEN": "", "TELEGRAM_BOT_TOKEN": "your_token_here", "TELEGRAM_CHAT_ID": "", "CHANNEL_BOARD_ENABLED": "0",
                         "CHANNEL_NEWS_ENABLED": "1"})
    o = bare.overview()
    assert o["controls_enabled"] is False and len(o["warnings"]) == 3
    assert any("TELEGRAM_CONTROL_TOKEN" in w for w in o["warnings"]) and any("Production channel is not configured" in w for w in o["warnings"])
    cfg = {i["key"]: i["value"] for i in o["config"]}
    assert cfg["CHANNEL_BOARD_ENABLED"] == "Off" and cfg["CHANNEL_NEWS_ENABLED"] == "On" and cfg["TELEGRAM_BOT_TOKEN"] == "Missing"


# ================================================================== phase 2: pin / unpin
def test_pin_and_unpin_a_live_message():
    control, ledger, fake, _ = make()
    rid = seed(ledger)
    out = control.pin(rid)
    assert out["result"] == "pinned" and out["message"]["pinned"] is True and fake.calls == [("pin", 1)]
    assert ledger.audit[-1] == {"at": NOW, "action": "pin", "target": "dev", "chat_id": ENV["TELEGRAM_DEV_CHAT_ID"], "message_id": 1, "outcome": "done", "detail": None}
    out = control.unpin(rid)
    assert out["result"] == "unpinned" and out["message"]["pinned"] is False and fake.calls[-1] == ("unpin", 1)
    assert ledger.audit[-1]["action"] == "unpin" and ledger.audit[-1]["outcome"] == "done"


def test_pinning_twice_or_unpinning_an_unpinned_message_is_refused():
    control, ledger, fake, _ = make()
    rid = seed(ledger)
    assert refused(control.unpin, rid).code == "unchanged"           # never pinned yet
    control.pin(rid)
    e = refused(control.pin, rid)
    assert e.code == "unchanged" and fake.calls == [("pin", 1)]       # the second pin never reaches Telegram


def test_pin_unpin_obey_the_same_lock_and_wrong_chat_rules_as_edit():
    control, ledger, fake, _ = make()
    rid = seed(ledger, target="prod")
    assert refused(control.pin, rid).code == "locked" and fake.calls == []
    control, ledger, fake, _ = make()
    rid = seed(ledger, chat="-1009999999999")
    assert refused(control.pin, rid).code == "wrong_chat"
    control, ledger, fake, _ = make()
    rid = seed(ledger)
    control.delete(rid)
    assert refused(control.pin, rid).code == "deleted"


def test_pin_maps_telegrams_gone_answer_and_a_failure_is_audited():
    control, ledger, fake, _ = make(fake=FakeClient(raises=tc.TelegramError("Bad Request: message to pin not found")))
    rid = seed(ledger)
    out = control.pin(rid)
    assert out["result"] == "gone" and ledger.get(rid)["status"] == "deleted" and ledger.audit[-1]["outcome"] == "gone"
    control, ledger, fake, _ = make(fake=FakeClient(raises=tc.TelegramError("giving up after retries: network")))
    rid = seed(ledger)
    e = refused(control.pin, rid)
    assert e.code == "telegram" and ledger.audit[-1]["outcome"] == "failed" and ledger.get(rid)["pinned"] is False
    e = refused(control.unpin, rid)                                   # the row still isn't pinned: unpin is refused before Telegram is ever asked
    assert e.code == "unchanged"


def test_unpin_reports_and_audits_a_telegram_failure_without_changing_the_row():
    control, ledger, fake, _ = make(fake=FakeClient(raises=tc.TelegramError("giving up after retries: network")))
    rid = seed(ledger)
    ledger.record_pinned(chat_id=ENV["TELEGRAM_DEV_CHAT_ID"], message_id=1)      # set up "pinned" directly: control.pin() would also raise here
    e = refused(control.unpin, rid)
    assert e.code == "telegram" and "network" in e.message
    assert ledger.audit[-1] == {"at": NOW, "action": "unpin", "target": "dev", "chat_id": ENV["TELEGRAM_DEV_CHAT_ID"], "message_id": 1, "outcome": "failed", "detail": "giving up after retries: network"}
    assert ledger.get(rid)["pinned"] is True                          # unchanged: the row still says pinned, matching reality


def test_unpin_maps_telegrams_missing_answer_without_deleting_the_row():
    control, ledger, fake, _ = make(fake=FakeClient(unpin="missing"))
    rid = seed(ledger)
    control.pin(rid)
    out = control.unpin(rid)
    assert out["result"] == "missing" and ledger.get(rid)["pinned"] is False and ledger.get(rid)["status"] == "sent"     # unlike edit/delete, "missing" here just means "wasn't pinned"; the message itself is still live
    assert ledger.audit[-1]["outcome"] == "gone"


def test_the_public_row_exposes_can_pin_matching_can_edit():
    control, ledger, *_ = make()
    rid = seed(ledger, target="prod")
    m = control.get_message(rid)
    assert m["can_pin"] == m["can_edit"] == {"ok": False, "code": "locked", "reason": m["can_edit"]["reason"]}


# ================================================================== phase 2: photo replace
def test_replace_photo_sends_the_new_image_and_caption_and_keeps_the_keyboard():
    control, ledger, fake, _ = make()
    rid = seed(ledger, text="old caption", ctype="photo")
    out = control.replace_photo(rid, b"\x89PNG-new-bytes", "new caption")
    (kind, mid, png, caption, markup), = fake.calls
    assert (kind, mid, png, caption, markup) == ("replace_photo", 1, b"\x89PNG-new-bytes", "new caption", KEYBOARD)
    m = out["message"]
    assert out["result"] == "edited" and m["text"] == "new caption" and m["original_text"] == "old caption" and m["edit_count"] == 1
    assert ledger.audit[-1]["detail"] == "photo replaced"


def test_replace_photo_reports_and_audits_a_telegram_failure_without_changing_the_row():
    control, ledger, fake, _ = make(fake=FakeClient(raises=tc.TelegramError("boom")))
    rid = seed(ledger, text="old caption", ctype="photo")
    e = refused(control.replace_photo, rid, b"png", "new caption")
    assert e.code == "telegram" and "boom" in e.message
    assert ledger.audit[-1] == {"at": NOW, "action": "edit", "target": "dev", "chat_id": ENV["TELEGRAM_DEV_CHAT_ID"], "message_id": 1, "outcome": "failed", "detail": "boom"}
    assert ledger.get(rid)["text"] == "old caption" and ledger.get(rid)["edit_count"] == 0


def test_replace_photo_is_refused_on_a_text_message():
    control, ledger, fake, _ = make()
    rid = seed(ledger, ctype="text")
    e = refused(control.replace_photo, rid, b"png", "caption")
    assert e.code == "not_photo" and fake.calls == []


def test_replace_photo_enforces_caption_length_and_upload_size():
    control, ledger, fake, _ = make()
    rid = seed(ledger, ctype="photo")
    e = refused(control.replace_photo, rid, b"x", "c" * 1025)
    assert e.code == "too_long" and "1025" in e.message
    e = refused(control.replace_photo, rid, b"x" * (cc.MAX_UPLOAD_BYTES + 1), "caption")
    assert e.code == "too_long" and "MB" in e.message
    assert fake.calls == []


def test_replace_photo_applies_the_wording_guard_and_records_the_acknowledgement():
    control, ledger, fake, _ = make()
    rid = seed(ledger, text="old caption", ctype="photo")
    e = refused(control.replace_photo, rid, b"png", "buy this now")
    assert e.code == "wording" and e.warnings == ["buy"]
    out = control.replace_photo(rid, b"png", "buy this now", acknowledge_wording=True)
    assert out["result"] == "edited" and "wording acknowledged: buy" in ledger.audit[-1]["detail"]


def test_replace_photo_maps_missing_and_unchanged_and_obeys_the_lock():
    control, ledger, fake, _ = make(fake=FakeClient(edit="missing"))
    rid = seed(ledger, ctype="photo")
    out = control.replace_photo(rid, b"png", "c")
    assert out["result"] == "missing" and ledger.get(rid)["status"] == "deleted"
    control, ledger, fake, _ = make(fake=FakeClient(edit="unchanged"))
    rid = seed(ledger, text="c", ctype="photo")
    out = control.replace_photo(rid, b"png", "c")
    assert out["result"] == "unchanged" and ledger.get(rid)["edit_count"] == 1 and "brought in line" in ledger.audit[-1]["detail"]
    control, ledger, fake, _ = make()
    rid = seed(ledger, target="prod", ctype="photo")
    assert refused(control.replace_photo, rid, b"png", "c").code == "locked"


# ================================================================== phase 2: compose
def test_compose_sends_a_text_message_through_the_real_send_path_and_returns_the_ledger_row():
    control, ledger, fake, _ = make()
    control.send_built.clear()
    fake_send = FakeSendClient(ledger, "dev", ENV["TELEGRAM_DEV_CHAT_ID"])
    control._send_factory = lambda target: fake_send
    out = control.compose("dev", "Hello <b>channel</b>", kind="manual", silent=True)
    assert out["result"] == "sent" and out["target"] == "dev" and out["message_id"] == 500
    assert out["message"]["kind"] == "manual" and out["message"]["text"] == "Hello <b>channel</b>" and out["message"]["status"] == "sent"
    assert fake_send.calls == [("send_message", "Hello <b>channel</b>", "manual", True, True)]
    assert ledger.get_by_message(ENV["TELEGRAM_DEV_CHAT_ID"], 500) is not None            # it really is the same ledger, same row


def test_compose_defaults_kind_to_manual_and_strips_no_text():
    control, ledger, *_ = make()
    control._send_factory = lambda target: FakeSendClient(ledger, "dev", ENV["TELEGRAM_DEV_CHAT_ID"])
    out = control.compose("dev", "no kind given")
    assert out["message"]["kind"] == "manual" and out["message"]["kind_label"] == "Manual post"


def test_compose_refuses_empty_or_over_long_text_before_touching_telegram():
    control, ledger, *_ = make()
    send = FakeSendClient(ledger, "dev", ENV["TELEGRAM_DEV_CHAT_ID"])
    control._send_factory = lambda target: send
    assert refused(control.compose, "dev", "   ").code == "empty"
    e = refused(control.compose, "dev", "x" * (tc.MAX_LEN + 1))
    assert e.code == "too_long" and str(tc.MAX_LEN) in e.message
    assert send.calls == []                                                              # never split into multiple messages by mistake


def test_compose_wording_guard_has_no_baseline_so_any_advice_word_needs_acknowledgement():
    control, ledger, *_ = make()
    send = FakeSendClient(ledger, "dev", ENV["TELEGRAM_DEV_CHAT_ID"])
    control._send_factory = lambda target: send
    e = refused(control.compose, "dev", "A great pick to buy")
    assert e.code == "wording" and set(e.warnings) == {"pick", "buy"} and send.calls == []
    out = control.compose("dev", "A great pick to buy", acknowledge_wording=True)
    assert out["result"] == "sent"


def test_compose_is_refused_to_a_locked_or_unconfigured_target_before_building_a_send_client():
    control, ledger, *_ = make()
    control._send_factory = lambda target: (_ for _ in ()).throw(AssertionError("should never be built"))
    assert refused(control.compose, "prod", "hello").code == "locked"
    assert refused(control.compose, "owner", "hello").code == "bad_filter"
    control, ledger, *_ = make(env={"TELEGRAM_DEV_CHAT_ID": ""})
    control._send_factory = lambda target: (_ for _ in ()).throw(AssertionError("should never be built"))
    assert refused(control.compose, "dev", "hello").code == "not_configured"


def test_compose_a_telegram_failure_is_reported_as_a_refused_action():
    control, ledger, *_ = make()
    control._send_factory = lambda target: FakeSendClient(ledger, "dev", ENV["TELEGRAM_DEV_CHAT_ID"], raises=tc.TelegramError("boom"))
    e = refused(control.compose, "dev", "hello")
    assert e.code == "telegram" and "boom" in e.message and ledger.rows == []


def test_compose_repairs_a_ledger_write_that_silently_failed_via_its_own_fallback_insert():
    """The recorder never raises and can silently fail to write; compose repairs that itself (record_sent's own ON CONFLICT DO NOTHING makes a
    second attempt safe) so a post that reached Telegram is not left permanently invisible to this page because of an unlucky write."""
    control, ledger, *_ = make()

    class SilentlyUnrecordedSend:
        chat_id = ENV["TELEGRAM_DEV_CHAT_ID"]

        def send_message(self, text, **kw):
            return [999]                                              # "sent" to Telegram, but the recorder's own write silently failed
    control._send_factory = lambda target: SilentlyUnrecordedSend()
    out = control.compose("dev", "hello", kind="manual")
    assert out["result"] == "sent" and out["message_id"] == 999
    assert out["message"] is not None and out["message"]["text"] == "hello" and out["message"]["kind"] == "manual"
    assert ledger.get_by_message(ENV["TELEGRAM_DEV_CHAT_ID"], 999) is not None                # the fallback insert really landed


def test_compose_never_lies_about_the_send_even_if_the_fallback_insert_also_fails():
    control, ledger, *_ = make()

    class SilentlyUnrecordedSend:
        chat_id = ENV["TELEGRAM_DEV_CHAT_ID"]

        def send_message(self, text, **kw):
            return [998]
    control._send_factory = lambda target: SilentlyUnrecordedSend()
    ledger.record_sent = lambda **kw: (_ for _ in ()).throw(RuntimeError("db down"))          # the fallback insert ALSO fails
    out = control.compose("dev", "hello")
    assert out == {"result": "sent", "target": "dev", "message_id": 998, "message": None}     # honest: never claims failure, never guesses success


def test_the_real_send_client_keeps_the_recorder_but_the_real_row_based_client_strips_it(monkeypatch):
    """Pins down the DEFAULT factories themselves (not a fake), since compose's tests all inject a FakeSendClient that never goes through
    `_real_send_client` at all - without this, a regression there (e.g. stripping the recorder like `_real_client` does) would go unnoticed."""
    monkeypatch.setattr(tc, "_load_env", lambda: None)
    for key, val in ENV.items():
        monkeypatch.setenv(key, val)
    monkeypatch.setenv(tc.PROD_SWITCH, "1")
    send_client = cc.ChannelControl._real_send_client("dev")
    assert send_client.recorder is not None and send_client.recorder.target == "dev"          # compose: the one, proven ledger-write path
    row_client = cc.ChannelControl._real_client("dev")
    assert row_client.recorder is None                                                       # edit/delete/pin: channel_control writes the ledger itself


def test_compose_photo_sends_and_validates_like_compose_but_for_a_caption():
    control, ledger, *_ = make()
    send = FakeSendClient(ledger, "dev", ENV["TELEGRAM_DEV_CHAT_ID"])
    control._send_factory = lambda target: send
    out = control.compose_photo("dev", b"png-bytes", caption="a caption")
    assert out["result"] == "sent" and out["message"]["content_type"] == "photo" and out["message"]["text"] == "a caption"
    assert send.calls == [("send_photo", "a caption", "manual", True)]
    e = refused(control.compose_photo, "dev", b"x" * (cc.MAX_UPLOAD_BYTES + 1), caption="c")
    assert e.code == "too_long" and "MB" in e.message
    e = refused(control.compose_photo, "dev", b"png", caption="buy now")
    assert e.code == "wording" and e.warnings == ["buy"]
    assert refused(control.compose_photo, "prod", b"png", caption="c").code == "locked"


# ================================================================== phase 2: today's schedule
def test_schedule_rejects_an_unknown_target_and_otherwise_delegates_to_the_schedule_module(monkeypatch):
    control, *_ = make()
    assert refused(control.schedule, "owner").code == "bad_filter"
    called = {}

    def fake_today_schedule(target, tz, now):
        called.update(target=target, tz=tz.key, now=now)
        return [{"key": "digest", "label": "Daily digest", "local_time": "06:00", "status": "done", "reason": "ok"}]
    monkeypatch.setattr(cc.sched, "today_schedule", fake_today_schedule)
    out = control.schedule("dev")
    assert out == {"target": "dev", "timezone": "Asia/Jerusalem", "jobs": [{"key": "digest", "label": "Daily digest", "local_time": "06:00", "status": "done", "reason": "ok"}]}
    assert called == {"target": "dev", "tz": "Asia/Jerusalem", "now": NOW}


def test_schedule_key_formats_match_the_senders_that_own_them():
    """schedule.py copies these key formats rather than importing the senders (see its own docstring); this pins both sides so a change on
    either one is caught, not silently drifted."""
    notices_src = open(os.path.join(ROOT, "mechanism", "alerts", "send_channel_notices.py"), encoding="utf-8").read()
    earnings_src = open(os.path.join(ROOT, "mechanism", "alerts", "send_earnings_today.py"), encoding="utf-8").read()
    assert 'return f"{target}:{day.isoformat()}:{slot}"' in notices_src
    assert 'return f"{target}:{day.isoformat()}"' in earnings_src
    assert 'datetime.now(tz).astimezone(ZoneInfo("America/New_York")).date()' in earnings_src     # earnings keys by the NY date, not local
    schedule_src = open(os.path.join(ROOT, "mechanism", "alerts", "schedule.py"), encoding="utf-8").read()
    assert '{target}:{today_local.isoformat()}:{slot}' in schedule_src
    assert '{target}:{ny_today.isoformat()}' in schedule_src


def test_earnings_schedule_status_keys_by_the_ny_date_not_the_owner_local_date(monkeypatch, tmp_path):
    """Behavioural, not just a source-string check: constructs a `now` where Israel's calendar date and NY's genuinely differ, and proves the
    lookup uses NY's - a mutant that swapped in the local date would report 'pending'/'overdue' here although the real sender already ran."""
    tz = ZoneInfo("Asia/Jerusalem")
    now = datetime(2026, 9, 22, 3, 0, tzinfo=UTC)              # = 06:00 Israel on 22 Sep, but still 21 Sep 23:00 in NY (UTC-4 in September)
    assert now.astimezone(tz).date().isoformat() == "2026-09-22"
    assert now.astimezone(cc.sched.NY).date().isoformat() == "2026-09-21"
    state = tmp_path / "earnings_today_state.json"
    state.write_text(json.dumps({"dev:2026-09-21": "2026-09-21T11:00:00"}), encoding="utf-8")     # keyed by the NY date
    monkeypatch.setattr(cc.sched, "EARNINGS_STATE", state)
    monkeypatch.setattr(cc.sched.mc, "is_trading_day", lambda day: True)
    rows = {r["key"]: r for r in cc.sched.today_schedule("dev", tz, now)}
    assert rows["earnings"]["status"] == "done"                                                   # found it under the NY date
    monkeypatch.setattr(cc.sched, "EARNINGS_STATE", tmp_path / "different_file.json")              # a key under Israel's date would miss
    rows = {r["key"]: r for r in cc.sched.today_schedule("dev", tz, now)}
    assert rows["earnings"]["status"] in ("pending", "overdue")


# ================================================================== phase 2: dev == prod (the channel promotion)
def test_overview_warns_when_dev_and_prod_are_the_same_chat():
    same = {"TELEGRAM_CHAT_ID": ENV["TELEGRAM_DEV_CHAT_ID"], "PROD_SENDING_ENABLED": "1"}
    control, *_ = make(env=same)
    warnings = control.overview()["warnings"]
    assert any("same channel" in w and "--to dev" in w for w in warnings)
    control, *_ = make()                                              # the normal test setup: distinct chats
    assert not any("same channel" in w for w in control.overview()["warnings"])
    control, *_ = make(env={"TELEGRAM_DEV_CHAT_ID": ""})              # no dev configured at all: never a false "same channel" warning
    assert not any("same channel" in w for w in control.overview()["warnings"])


# ================================================================== phase 2: opt-in read-auth toggle (enforced in the API layer, not here)
def test_the_read_auth_toggle_is_a_visible_config_item_off_by_default():
    control, *_ = make()
    cfg = {i["key"]: i["value"] for i in control.overview()["config"]}
    assert cfg["TELEGRAM_READ_REQUIRES_TOKEN"] == "Off"
    control, *_ = make(env={"TELEGRAM_READ_REQUIRES_TOKEN": "1"})
    assert control.overview()["config"] and {i["key"]: i["value"] for i in control.overview()["config"]}["TELEGRAM_READ_REQUIRES_TOKEN"] == "On"


def _router():
    backend_dir = os.path.join(ROOT, "backend")
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    from routers import telegram_control as router_mod
    return router_mod


def test_require_read_access_only_checks_the_token_when_the_toggle_is_on(monkeypatch):
    router = _router()
    monkeypatch.setattr(router, "control_token", lambda: "the-real-secret")
    monkeypatch.delenv("TELEGRAM_READ_REQUIRES_TOKEN", raising=False)
    router.require_read_access(x_control_token=None)                                      # toggle off: no token needed to read, at all
    router.require_read_access(x_control_token="wrong")
    monkeypatch.setenv("TELEGRAM_READ_REQUIRES_TOKEN", "1")
    with pytest.raises(router.HTTPException) as e:
        router.require_read_access(x_control_token=None)
    assert e.value.status_code == 401
    with pytest.raises(router.HTTPException):
        router.require_read_access(x_control_token="wrong")
    router.require_read_access(x_control_token="the-real-secret")                          # toggle on, right token: allowed through
    monkeypatch.setenv("TELEGRAM_READ_REQUIRES_TOKEN", "0")
    router.require_read_access(x_control_token=None)                                       # explicitly 0 behaves the same as unset


# ================================================================== structure: the control layer belongs to the channel side
def test_control_modules_stay_on_the_channel_side_and_reach_telegram_only_through_from_env():
    import ast
    for name in ("channel_control.py", "message_ledger.py", "schedule.py"):
        src = open(os.path.join(ROOT, "mechanism", "alerts", name), encoding="utf-8").read()
        imported = set()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.update([node.module or ""] + [f"{node.module}.{a.name}" for a in node.names])
        assert not (imported & {"alerts.screens", "alerts.tracker", "alerts.access", "alerts.run_bot", "alerts.performance", "alerts.news_service", "alerts.chart"}), name
        for table in ("bot_tracked", "bot_access", "bot_requests", "bot_invites", "bot_users", "bot_user_settings"):
            assert table not in src, (name, table)
        assert "TelegramClient(" not in src, name


# ================================================================== fixes from the code review
class NoClient:
    """A factory that cannot build a client (missing / placeholder token or chat id, or the production lock inside from_env)."""

    def __call__(self, target):
        raise tc.TelegramError("TELEGRAM_BOT_TOKEN is empty/placeholder in .env")


def test_a_client_that_cannot_be_built_is_a_refused_action_not_an_unhandled_error():
    control = cc.ChannelControl(ml.MemoryLedger(clock=lambda: NOW), client_factory=NoClient(), env=dict(ENV), now=lambda: NOW)
    rid = seed(control.ledger)
    for call, action in ((lambda: control.edit(rid, "changed"), "edit"), (lambda: control.delete(rid), "delete")):
        e = refused(call)
        assert (e.code, e.http) == ("telegram", 502) and "Could not reach Telegram" in e.message
        assert control.ledger.audit[-1]["action"] == action and control.ledger.audit[-1]["outcome"] == "failed"
    assert control.ledger.get(rid)["text"] == "Hello <b>world</b>" and control.ledger.get(rid)["status"] == "sent"


def test_a_placeholder_control_token_is_no_token_anywhere():
    """The API's check and the overview share ONE rule: "changeme" must not unlock what the page says is switched off."""
    for value in (None, "", "   ", "changeme", "your_token_here", "xxxx"):
        env = {**ENV, "TELEGRAM_CONTROL_TOKEN": value or ""}
        assert cc.control_token(env) is None
        assert cc.ChannelControl(ml.MemoryLedger(), env=env).controls_enabled() is False
    assert cc.control_token({**ENV, "TELEGRAM_CONTROL_TOKEN": "  real-secret-9  "}) == "real-secret-9"
    src = open(os.path.join(ROOT, "backend", "routers", "telegram_control.py"), encoding="utf-8").read()
    assert "expected = control_token()" in src and 'os.getenv("TELEGRAM_CONTROL_TOKEN"' not in src             # the router has no rule of its own


def test_the_configuration_panel_uses_the_same_rules_as_the_senders():
    """The panel must never say Off while a post still goes out (or the reverse): its switch rules are the senders' rules."""
    sender = open(os.path.join(ROOT, "mechanism", "alerts", "send_channel_posts.py"), encoding="utf-8").read()
    assert 'os.getenv("CHANNEL_BOARD_ENABLED", "1").strip() != "0"' in sender                                      # board: on unless exactly 0
    assert 'os.getenv("CHANNEL_NEWS_ENABLED", "").strip() == "1"' in sender                                        # news: on only when exactly 1
    assert 'os.getenv("CHANNEL_SCOREBOARD_ENABLED", "").strip() == "1"' in sender

    def cfg(**flags):
        control = cc.ChannelControl(ml.MemoryLedger(), env={**ENV, **flags})
        return {i["key"]: i["value"] for i in control.config_items()}
    assert cfg(CHANNEL_BOARD_ENABLED="false")["CHANNEL_BOARD_ENABLED"] == "On"                                     # "false" does not switch the board off
    assert cfg(CHANNEL_BOARD_ENABLED="0")["CHANNEL_BOARD_ENABLED"] == "Off" and cfg(CHANNEL_BOARD_ENABLED="")["CHANNEL_BOARD_ENABLED"] == "On"
    assert cfg(CHANNEL_NEWS_ENABLED="true")["CHANNEL_NEWS_ENABLED"] == "Off" and cfg(CHANNEL_NEWS_ENABLED="1")["CHANNEL_NEWS_ENABLED"] == "On"
    assert cfg(CHANNEL_SCOREBOARD_ENABLED="yes")["CHANNEL_SCOREBOARD_ENABLED"] == "Off"


def test_re_locking_production_in_env_takes_effect_in_a_running_api_without_a_restart(tmp_path):
    """The API loads its environment once. If PROD_SENDING_ENABLED goes back to 0 in .env, edit / delete of the public channel must stop at once."""
    env_file = tmp_path / ".env"
    opened = {**ENV, "PROD_SENDING_ENABLED": "1"}

    def control(file_text):
        if file_text is not None:
            env_file.write_text(file_text, encoding="utf-8")
        return cc.ChannelControl(ml.MemoryLedger(), env=dict(opened), env_file=env_file if file_text is not None else tmp_path / "missing.env")
    assert control("PROD_SENDING_ENABLED=1\n").prod_locked() is False                                              # process and file agree: open
    assert control("PROD_SENDING_ENABLED=0\n").prod_locked() is True                                               # re-locked in the file: locked NOW
    assert control("PROD_SENDING_ENABLED=\n").prod_locked() is True
    assert control("SOMETHING_ELSE=1\n").prod_locked() is False                                                    # the file does not mention it: the environment decides
    assert control(None).prod_locked() is False                                                                    # no .env file at all: the environment decides
    stale = cc.ChannelControl(ml.MemoryLedger(), env={**ENV, "PROD_SENDING_ENABLED": "0"}, env_file=env_file)
    env_file.write_text("PROD_SENDING_ENABLED=1\n", encoding="utf-8")
    assert stale.prod_locked() is True                                                                             # opening needs a restart: never trusted at runtime
    ledger = ml.MemoryLedger(clock=lambda: NOW)
    fake = FakeClient()
    relocked = cc.ChannelControl(ledger, client_factory=lambda t: fake, env=dict(opened), env_file=env_file, now=lambda: NOW)
    rid = seed(ledger, target="prod")
    assert relocked.edit(rid, "changed")["result"] == "edited"
    env_file.write_text("PROD_SENDING_ENABLED=0\n", encoding="utf-8")
    assert refused(relocked.delete, rid).code == "locked" and len(fake.calls) == 1


def test_the_channel_control_reuses_the_clients_plain_text_rule():
    assert cc.tc.plain_text is tc.plain_text and tc.text_length("<b>a &amp; b</b>") == 5 and tc.plain_text("<a href=\"x\">y</a> &lt;") == "y <"
    assert not hasattr(cc, "_TAG") and not hasattr(cc, "plain_text")                                               # no second copy of the rule to drift
