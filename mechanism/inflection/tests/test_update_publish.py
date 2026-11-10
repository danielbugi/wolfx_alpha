"""The dormant Fundamental Updates owner-chat publisher (update_publish): gates, the exactly-once guards and the refusal paths.
Pure: a fake database, ledger, delivery claim and sender; no network, no real database."""
import os
import sys
from datetime import date

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))
sys.path.insert(0, os.path.dirname(__file__))

from inflection import update_publish as P  # noqa: E402
from test_update_select import SESSION, ev  # noqa: E402

ON = {"FU_OWNER_SEND_ENABLED": "1", "INFLECTION_ALERTS_ENABLED": "0"}


class FakeDb:
    def __init__(self, events, ledger=True, posted=None):
        self.events, self.ledger, self.posted, self.calls = events, ledger, posted or {}, []

    def execute_dict_query(self, q, params=None):
        self.calls.append(q)
        if "to_regclass" in q:
            return [{"ok": self.ledger}]
        if "FROM fundamental_inflection_event" in q:
            assert params[0] == "inflection_v2_sec_pit"
            return [dict(e) for e in self.events]
        if "FROM fundamental_update_publication" in q:
            return [{"symbol": s, "last": d} for s, d in self.posted.items()]
        raise AssertionError(q)


class FakeLedger:
    def __init__(self):
        self.rows = {}

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


def go(events=None, *, record=True, send=True, env=ON, ledger=None, delivery=None, sender=None, db=None):
    sent = []
    db = db or FakeDb(night() if events is None else events)
    ledger = ledger or FakeLedger()
    delivery = delivery or FakeDelivery()
    out = P.run(SESSION, db=db, ledger=ledger, delivery=delivery, sender=sender or sent.append, record=record, send=send, env=env, log=lambda s: None)
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
    for p in ("automation_pipeline.sh",) + tuple(f"deploy/vps/{n}" for n in os.listdir(os.path.join(ROOT, "deploy", "vps"))):
        assert "update_publish" not in open(os.path.join(ROOT, p), encoding="utf-8", errors="ignore").read(), p   # nothing runs it yet


def test_the_event_query_matches_the_reviewed_simulation_export():
    sim = open(os.path.join(ROOT, "docs", "product", "fu_selection_simulation.sql"), encoding="utf-8").read()
    for col in ("f.quality_notes AS feature_notes", "p.date AS profile_date", "sc.cik", "co.company_name", "(e.first_detected_at AT TIME ZONE 'UTC')::date AS first_detected_on",
                "d.date <= e.available_at"):
        assert col in sim and col in P.EVENTS_SQL, col
