"""The dormant Fundamental Updates owner-chat publisher (update_publish): gates, the exactly-once guards and the refusal paths.
Pure: a fake database, ledger, delivery claim and sender; no network, no real database."""
import os
import sys
from datetime import date, datetime, timezone

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))
sys.path.insert(0, os.path.dirname(__file__))

from inflection import update_publish as P  # noqa: E402
from test_update_select import SESSION, ev  # noqa: E402

ON = {"FU_OWNER_SEND_ENABLED": "1", "INFLECTION_ALERTS_ENABLED": "0"}


IN_WINDOW = datetime(2026, 11, 6, 3, 45, tzinfo=timezone.utc)        # the 22:45 ET run for session 2026-11-05 (inside its window)


class FakeDb:
    """`live`: a FakeLedger whose rows ARE the publication table (cooldown and the session's picks read back from it, as in production)."""
    def __init__(self, events, ledger=True, posted=None, live=None):
        self.events, self.ledger, self.posted, self.calls, self.recent, self.live = events, ledger, posted or {}, [], [], live

    def execute_dict_query(self, q, params=None):
        self.calls.append(q)
        if "to_regclass" in q:
            return [{"ok": self.ledger}]
        if "e.id = ANY" in q:
            return [dict(e) for e in self.events if e["id"] in params[0]]
        if "FROM fundamental_inflection_event" in q:
            assert params[0] == "inflection_v2_sec_pit"
            return [dict(e) for e in self.events]
        if "DISTINCT ON (p.update_key)" in q:
            return list(self.recent)
        if "SELECT update_key, symbol FROM fundamental_update_publication" in q:
            assert params[0] < params[1]
            return [{"update_key": k, "symbol": u.symbol} for k, u in (self.live.rows.items() if self.live else ())]
        if "FROM fundamental_update_publication" in q:
            live = {u.symbol: IN_WINDOW.date() for u in self.live.rows.values()} if self.live else {}
            return [{"symbol": s, "last": d} for s, d in {**self.posted, **live}.items()]
        raise AssertionError(q)


class FakeLedger:
    def __init__(self):
        self.rows, self.versions = {}, {}

    def latest(self, key):
        v = self.versions.get(key) or [("initial", self.rows[key].to_public_dict())]
        return {"version": len(v), "kind": v[-1][0], "public_json": v[-1][1]}

    def revise(self, key, current, event_id, method_version):
        from inflection.update_model import classify_revision
        last = self.latest(key)
        kind, _ = classify_revision(last["public_json"], current)
        self.versions.setdefault(key, [("initial", self.rows[key].to_public_dict())]).append((kind, current.to_public_dict() if current else last["public_json"]))
        return kind

    def publish(self, u, event_id, method_version):
        assert method_version == "inflection_v2_sec_pit"
        if u.key in self.rows:
            return "exists"
        self.rows[u.key] = u
        return "published"


class FakeDelivery:
    def __init__(self):
        self.state = {}

    def claim(self, key, channel, recipient, event_id):
        assert channel == "fu_owner" and recipient == "owner"
        if self.state.get(key) in ("claimed", "sent"):
            return False
        self.state[key] = "claimed"
        return True

    def mark(self, key, ok, error=None):
        self.state[key] = "sent" if ok else "failed"


def night():
    return [ev("AAA", "profitability_turnaround", cap=5e9), ev("BBB", "margin_expansion", cap=4e9), ev("CCC", "revenue_acceleration", cap=3e9),
            ev("DDD", "fcf_turnaround", cap=2e9), ev("OLD", available_at="2026-10-01", quarter_end="2026-06-30")]


def go(events=None, *, record=True, send=True, env=ON, ledger=None, delivery=None, sender=None, db=None, now=IN_WINDOW):
    sent = []
    db = db or FakeDb(night() if events is None else events)
    ledger = ledger or FakeLedger()
    delivery = delivery or FakeDelivery()
    out = P.run(SESSION, db=db, ledger=ledger, delivery=delivery, sender=sender or sent.append, record=record, send=send, env=env, log=lambda s: None,
                now=now)
    return out, sent, ledger, delivery


def test_a_dry_run_selects_but_writes_and_sends_nothing():
    out, sent, ledger, delivery = go(record=False, send=False)
    assert [x["symbol"] for x in out["selected"]] == ["AAA", "BBB", "CCC"] and out["excluded"] == 1 and out["outranked"] == 1
    assert sent == [] and ledger.rows == {} and delivery.state == {}


def test_record_and_send_publishes_then_sends_each_pick_once_to_the_owner():
    out, sent, ledger, delivery = go()
    assert out["published"] == 3 and out["sent"] == 3 and [u.symbol for u in sent] == ["AAA", "BBB", "CCC"]
    assert set(delivery.state) == {P.delivery_key(u.key) for u in sent} and set(delivery.state.values()) == {"sent"}


def test_a_rerun_the_retry_or_a_second_process_never_sends_twice():
    out1, sent1, ledger, delivery = go()
    out2, sent2, _, _ = go(ledger=ledger, delivery=delivery)
    assert len(sent1) == 3 and sent2 == [] and out2["exists"] == 3 and out2["skipped_already_claimed"] == 3


def test_a_failed_send_is_recorded_reported_and_retried_once_on_the_next_run():
    def boom(u):
        raise RuntimeError("https://api.telegram.org/bot123:SECRET/sendPhoto")
    out, _, ledger, delivery = go(sender=boom)
    assert out["send_failed"] == 3 and set(delivery.state.values()) == {"failed"}
    out2, sent2, _, _ = go(ledger=ledger, delivery=delivery)
    assert out2["sent"] == 3 and len(sent2) == 3                                      # failed claims are retryable; the ledger already holds them ('exists')


def test_the_company_cooldown_comes_from_the_ledger():
    out, sent, _, _ = go(db=FakeDb(night(), posted={"AAA": date(2026, 10, 20)}))
    assert [u.symbol for u in sent] == ["BBB", "CCC", "DDD"] and out["cooldown"] == 1


@pytest.mark.parametrize("record,send,env,ledger_ok,needle", [
    (False, True, ON, True, "--send needs --record"),
    (True, False, ON, False, "migration 41 is not applied"),
    (True, True, {"INFLECTION_ALERTS_ENABLED": "0"}, True, "FU_OWNER_SEND_ENABLED is not 1"),
    (True, True, {"FU_OWNER_SEND_ENABLED": "1", "INFLECTION_ALERTS_ENABLED": "1"}, True, "legacy owner alerts are still on"),
])
def test_every_gate_refuses_before_reading_events_or_writing(record, send, env, ledger_ok, needle):
    db = FakeDb(night(), ledger=ledger_ok)
    out, sent, ledger, delivery = go(record=record, send=send, env=env, db=db)
    assert needle in out["refused"] and sent == [] and ledger.rows == {} and delivery.state == {}
    assert not any("fundamental_inflection_event" in q for q in db.calls)


def test_record_only_never_claims_or_sends():
    out, sent, ledger, delivery = go(send=False)
    assert out["published"] == 3 and sent == [] and delivery.state == {}


def test_nothing_fresh_means_an_empty_night_not_a_backlog_flood():
    stale = [ev(f"H{i}", available_at="2026-09-15", quarter_end="2026-06-30") for i in range(20)]
    out, sent, ledger, _ = go(stale)
    assert out["selected"] == [] and sent == [] and ledger.rows == {} and out["excluded"] == 20


def test_the_publisher_is_owner_only_and_dormant():
    src = open(P.__file__, encoding="utf-8").read()
    assert 'TelegramClient.from_env("owner", dry_run=False)' in src
    for forbidden in ('"prod"', '"dev"', "TELEGRAM_CHAT_ID", "PROD_SENDING_ENABLED=1", "post_delivery"):
        assert forbidden not in src, forbidden
    for p in tuple(f"deploy/vps/{n}" for n in os.listdir(os.path.join(ROOT, "deploy", "vps"))):
        assert "update_publish" not in open(os.path.join(ROOT, p), encoding="utf-8", errors="ignore").read(), p   # no unit or wrapper runs it
    # The pipeline runs it only from step 8f, which is off unless FU_PUBLISH_MODE is set (behaviour: test_pipeline_fu_step.py).
    pipe = open(os.path.join(ROOT, "automation_pipeline.sh"), encoding="utf-8").read()
    gated = pipe[pipe.index('FU_MODE="${FU_PUBLISH_MODE:-off}"'):]
    gated = gated[:gated.index("\nfi") + 3]
    assert pipe.count("update_publish") == gated.count("update_publish") == 2


def test_the_event_query_matches_the_reviewed_simulation_export():
    sim = open(os.path.join(ROOT, "docs", "product", "fu_selection_simulation.sql"), encoding="utf-8").read()
    for col in ("f.quality_notes AS feature_notes", "f.metrics AS feature_metrics", "p.date AS profile_date", "sc.cik", "co.company_name", "(e.first_detected_at AT TIME ZONE 'UTC')::date AS first_detected_on",
                "d.date <= e.available_at"):
        assert col in sim and col in P.EVENTS_SQL, col


# ------------------------------------------------------------------ corrections (--revise): record-only, never sent
def published_world(n=3):
    """A night that was recorded and sent; returns the db (with its 'recent' ledger view), ledger, delivery and the events by symbol."""
    out, sent, ledger, delivery = go()
    events = {e["symbol"]: e for e in night()}
    db = FakeDb(list(events.values()))
    db.recent = [{"update_key": u.key, "event_id": events[u.symbol]["id"], "version": 1, "kind": "initial"} for u in sent[:n]]
    return db, ledger, delivery, events, sent


def test_unchanged_updates_record_nothing():
    db, ledger, _, _, _ = published_world()
    out = P.revise(SESSION, db=db, ledger=ledger, env={"FU_CORRECTIONS_ENABLED": "1"}, log=lambda s: None)
    assert out["checked"] == 3 and out["would_change"] == {} and out["recorded"] == {} and ledger.versions == {}


def test_a_restated_figure_is_recorded_as_a_new_version_and_never_sent():
    db, ledger, delivery, events, sent = published_world()
    before = dict(delivery.state)
    a = events["AAA"]
    a["metrics"] = dict(a["metrics"], net_income=12.3e6)                       # the company restated the quarter
    db.events = list(events.values())
    out = P.revise(SESSION, db=db, ledger=ledger, env={"FU_CORRECTIONS_ENABLED": "1"}, log=lambda s: None)
    assert out["would_change"] == {"restated": 1} and out["recorded"] == {"restated": 1}
    assert [k for k, _ in ledger.versions[sent[0].key]] == ["initial", "restated"]
    assert delivery.state == before                                           # no claim, no send for a correction


def test_a_vanished_event_is_withdrawn_not_deleted():
    db, ledger, _, events, sent = published_world()
    db.events = [e for s_, e in events.items() if s_ != "BBB"]
    out = P.revise(SESSION, db=db, ledger=ledger, env={"FU_CORRECTIONS_ENABLED": "1"}, log=lambda s: None)
    assert out["recorded"] == {"withdrawn": 1} and [k for k, _ in ledger.versions[sent[1].key]] == ["initial", "withdrawn"]


def test_without_the_switch_corrections_are_reported_but_not_recorded():
    db, ledger, _, events, _ = published_world()
    db.events = []
    out = P.revise(SESSION, db=db, ledger=ledger, env={}, log=lambda s: None)
    assert out["would_change"] == {"withdrawn": 3} and out["recorded"] == {} and "FU_CORRECTIONS_ENABLED is not 1" in out["refused"] and ledger.versions == {}


def test_a_mass_change_records_nothing(monkeypatch):
    db, ledger, _, _, _ = published_world()
    db.events = []
    monkeypatch.setattr(P, "MAX_REVISIONS_PER_RUN", 2)
    out = P.revise(SESSION, db=db, ledger=ledger, env={"FU_CORRECTIONS_ENABLED": "1"}, log=lambda s: None)
    assert "would change in one run" in out["refused"] and out["recorded"] == {} and ledger.versions == {}


def test_a_withdrawn_update_is_never_revived():
    db, ledger, _, _, _ = published_world(1)
    db.recent[0]["kind"] = "withdrawn"
    out = P.revise(SESSION, db=db, ledger=ledger, env={"FU_CORRECTIONS_ENABLED": "1"}, log=lambda s: None)
    assert out["would_change"] == {} and ledger.versions == {}


def test_revise_refuses_without_the_ledger_and_cannot_be_combined_with_sending():
    out = P.revise(SESSION, db=FakeDb([], ledger=False), ledger=FakeLedger(), env={"FU_CORRECTIONS_ENABLED": "1"}, log=lambda s: None)
    assert out["refused"] == "migration 41 is not applied"
    assert P.main(["--session", "2026-11-05", "--revise", "--send"]) == P.EXIT_REFUSED


# ------------------------------------------------------------------ the per-session cap holds across runs (night-1 finding 2026-11-13)
def live(events=None):
    ledger = FakeLedger()
    return FakeDb(night() if events is None else events, live=ledger), ledger


def test_a_rerun_of_the_same_session_reproduces_its_picks_instead_of_choosing_the_next_companies():
    db, ledger = live()
    out1, sent1, _, delivery = go(db=db, ledger=ledger)
    out2, sent2, _, _ = go(db=db, ledger=ledger, delivery=delivery)              # the 00:15 retry after a failure later in the pipeline
    assert out1["published"] == 3 and [u.symbol for u in sent1] == ["AAA", "BBB", "CCC"]
    assert out2["published"] == 0 and out2["exists"] == 3 and out2["cooldown"] == 0 and sent2 == []
    assert sorted(u.symbol for u in ledger.rows.values()) == ["AAA", "BBB", "CCC"]  # DDD is never recorded for this session


def test_a_rerun_never_records_more_than_the_cap_even_if_a_new_company_now_outranks_one():
    db, ledger = live()
    go(db=db, ledger=ledger, send=False)
    db.events = night() + [ev("EEE", "profitability_turnaround", cap=9e9)]          # detected between the two runs, ranks first
    out2, _, _, _ = go(db=db, ledger=ledger, send=False)
    assert out2["published"] == 0 and out2["session_full"] >= 1 and len(ledger.rows) == 3 and "EEE" not in {u.symbol for u in ledger.rows.values()}


def test_a_failed_send_is_still_retried_by_the_same_sessions_rerun():
    def boom(u):
        raise RuntimeError("x")
    db, ledger = live()
    _, _, _, delivery = go(db=db, ledger=ledger, sender=boom)
    out2, sent2, _, _ = go(db=db, ledger=ledger, delivery=delivery)
    assert out2["sent"] == 3 and [u.symbol for u in sent2] == ["AAA", "BBB", "CCC"] and len(ledger.rows) == 3


def test_the_next_session_is_not_capped_by_this_sessions_picks():
    db, ledger = live()
    go(db=db, ledger=ledger, send=False)
    start, end = P.session_window(SESSION)
    nxt_start, _ = P.session_window(date(2026, 11, 6))
    assert end == nxt_start and start < end


@pytest.mark.parametrize("now", [datetime(2026, 11, 5, 20, 59, tzinfo=timezone.utc),       # before the session's close (15:59 New York)
                                 datetime(2026, 11, 6, 21, 0, tzinfo=timezone.utc)])       # at the next session's close
def test_record_outside_the_sessions_window_is_refused(now):
    out, sent, ledger, delivery = go(now=now)
    assert "only allowed inside its window" in out["refused"] and ledger.rows == {} and sent == []


def test_a_dry_run_is_allowed_at_any_time():
    out, _, _, _ = go(record=False, send=False, now=datetime(2026, 12, 1, tzinfo=timezone.utc))
    assert out["refused"] is None and len(out["selected"]) == 3


def test_the_window_runs_from_the_close_to_the_next_weekdays_close():
    s, e = P.session_window(date(2026, 11, 13))                                          # a Friday: the window spans the weekend
    assert s.astimezone(timezone.utc) == datetime(2026, 11, 13, 21, 0, tzinfo=timezone.utc)
    assert e.astimezone(timezone.utc) == datetime(2026, 11, 16, 21, 0, tzinfo=timezone.utc)
