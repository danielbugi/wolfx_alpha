"""Inflection alerts: message and email content from real-shaped events, severity thresholds, flood guard, exactly-once delivery (fake and real claims), retries, dry run and the
owner-only rules. Formatting and delivery logic run without a network; the claim/retry tests need PostgreSQL with migration 40 (skipped without)."""
import os
import sys
from datetime import date

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))
sys.path.insert(0, ROOT)

from inflection import alerts as A  # noqa: E402

TODAY = date(2026, 10, 31)


def ev(symbol="OSIS", kind="margin_expansion", strength=90.0, severity="high", **over):
    e = {"id": 7, "symbol": symbol, "company_name": "OSI Systems <Inc>", "event_type": kind, "label": "Margin expansion", "strength": strength, "severity": severity, "score": 72.0,
         "fiscal_year": 2026, "fiscal_quarter": 2, "quarter_end": "2026-06-30", "available_at": "2026-08-05", "availability_basis": "earnings_calendar", "data_quality": "ok", "method_version": "inflection_v1",
         "explanation": ["Operating margin rose to 18.0% from 3.9% a year earlier (+1406 basis points)."],
         "key_metrics": [{"label": "Operating margin", "current": 0.18, "previous": 0.039, "unit": "pct"}, {"label": "Change (bps)", "current": 1406.0, "previous": None, "unit": "bps"}],
         "reaction_2d": 0.042, "excess_vs_sector_since_publication": -0.011, "valuation": {"ps_ratio": 3.2, "ps_percentile_own_history": 0.35}}
    e.update(over)
    return e


class FakeDelivery(A.Delivery):
    def __init__(self):
        self.rows = {}

    def claim(self, key, channel, recipient_ref, event_id):
        if key in self.rows and self.rows[key]["status"] != "failed":
            return False
        if key in self.rows and self.rows[key]["attempts"] >= A.MAX_ATTEMPTS:
            return False
        row = self.rows.setdefault(key, {"status": "claimed", "attempts": 0, "channel": channel, "recipient": recipient_ref})
        row["status"], row["attempts"] = "claimed", row["attempts"] + 1
        return True

    def mark(self, key, ok, error=None):
        self.rows[key]["status"] = "sent" if ok else "failed"
        self.rows[key]["error"] = error


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ Telegram message
def test_the_telegram_message_has_everything_the_owner_needs_and_is_labelled_a_research_signal():
    t = A.telegram_text(ev())
    for needle in ("RESEARCH SIGNAL", "not an order recommendation", "OSIS", "Margin expansion", "high (strength 90, score 72)", "Operating margin: 18.0% (was 3.9%)", "Change (bps): +1406 bps",
                   "Why it may matter: Operating margin rose to 18.0%", "FY2026 Q2", "public 2026-08-05", "earnings report date", "+4.2% around the report", "-1.1% vs its sector since", "P/S 3.2 (35th percentile",
                   "https://dashboard.first-light.finance/stock/OSIS"):
        assert needle in t, needle
    assert "&lt;Inc&gt;" in t and "<Inc>" not in t                                   # company names are escaped for HTML mode
    assert len(t) < 1200 and "buy" not in t.lower().replace("buyback", "")


def test_an_assumed_availability_date_and_partial_data_are_stated_not_hidden():
    t = A.telegram_text(ev(availability_basis="assumed_lag_75d", data_quality="partial"))
    assert "assumed lag after quarter end (not a filing date)" in t and "partial data" in t


def test_missing_numbers_read_n_a_never_zero():
    t = A.telegram_text(ev(reaction_2d=None, excess_vs_sector_since_publication=None, valuation={}, key_metrics=[{"label": "Net income", "current": None, "previous": None, "unit": "money"}]))
    assert "Net income: n/a" in t and "n/a around the report" in t and "0.0%" not in t


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ exactly-once, thresholds, flood guard
def test_each_qualifying_event_is_sent_once_and_a_rerun_sends_nothing():
    d, sent = FakeDelivery(), []
    evs = [ev("A1", strength=90), ev("B2", strength=80), ev("C3", strength=60, severity="medium")]
    s1 = A.send_event_alerts(evs, d, sent.append, dry_run=False, log=lambda m: None)
    assert (s1["sent"], s1["skipped_severity"], s1["skipped_duplicate"]) == (2, 1, 0) and len(sent) == 2
    s2 = A.send_event_alerts(evs, d, sent.append, dry_run=False, log=lambda m: None)
    assert (s2["sent"], s2["skipped_duplicate"]) == (0, 2) and len(sent) == 2


def test_a_restated_event_does_not_alert_again_because_the_key_has_no_content_hash():
    d, sent = FakeDelivery(), []
    A.send_event_alerts([ev(strength=80)], d, sent.append, dry_run=False, log=lambda m: None)
    A.send_event_alerts([ev(strength=95, id=999)], d, sent.append, dry_run=False, log=lambda m: None)
    assert len(sent) == 1


def test_a_failed_send_is_retried_on_a_later_run_but_only_three_times():
    d, calls = FakeDelivery(), []

    def boom(t):
        calls.append(t)
        raise RuntimeError("network down")
    for _ in range(5):
        A.send_event_alerts([ev()], d, boom, dry_run=False, log=lambda m: None)
    assert len(calls) == A.MAX_ATTEMPTS and list(d.rows.values())[0]["status"] == "failed"
    ok = []
    d2 = FakeDelivery()
    A.send_event_alerts([ev()], d2, boom, dry_run=False, log=lambda m: None)
    s = A.send_event_alerts([ev()], d2, ok.append, dry_run=False, log=lambda m: None)
    assert s["sent"] == 1 and list(d2.rows.values())[0]["status"] == "sent"


def test_one_failing_send_does_not_stop_the_others():
    d, sent = FakeDelivery(), []

    def flaky(t):
        if "A1" in t:
            raise RuntimeError("boom")
        sent.append(t)
    s = A.send_event_alerts([ev("A1", strength=95), ev("B2", strength=85)], d, flaky, dry_run=False, log=lambda m: None)
    assert (s["failed"], s["sent"]) == (1, 1) and "B2" in sent[0]


def test_flood_guard_sends_the_strongest_first_and_summarises_the_rest_once_per_day():
    d, sent = FakeDelivery(), []
    evs = [ev(f"S{i:02d}", strength=100 - i) for i in range(14)]
    s = A.send_event_alerts(evs, d, sent.append, max_per_run=10, dry_run=False, log=lambda m: None)
    assert s["sent"] == 10 and s["overflow"] == 4 and len(sent) == 11 and "4 more" in sent[-1] and "S00" in sent[0]
    A.send_event_alerts(evs, d, sent.append, max_per_run=10, dry_run=False, log=lambda m: None)
    assert len(sent) == 11                                                            # neither the events nor the overflow note repeat


def test_dry_run_sends_and_claims_nothing():
    d, sent = FakeDelivery(), []
    s = A.send_event_alerts([ev()], d, sent.append, dry_run=True, log=lambda m: None)
    assert s["would_send"] == 1 and not sent and not d.rows


def test_a_lower_severity_floor_is_honoured():
    d, sent = FakeDelivery(), []
    s = A.send_event_alerts([ev(severity="medium", strength=60), ev("Z", severity="low", strength=30)], d, sent.append, min_severity="medium", dry_run=False, log=lambda m: None)
    assert s["sent"] == 1 and s["skipped_severity"] == 1


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ email
QUALITY = {"features": 15896, "partial_features": 13067, "assumed_share": 0.08, "stale_days": 6, "last_run": {"status": "ok", "asof": "2026-10-30", "symbols_processed": 3001, "errors": 0}}


def test_the_daily_digest_lists_events_with_links_and_reports_data_quality():
    subj, html_body, text = A.daily_digest([ev("OSIS"), ev("AA", severity="medium", strength=60)], QUALITY, TODAY)
    assert subj.startswith("First Light: 2 new fundamental inflection event(s), 1 high") and "2026-10-31" in subj
    for needle in ("RESEARCH SIGNAL, not an order recommendation", "https://dashboard.first-light.finance/stock/OSIS", "/inflection", "Data quality", "13067 of 15896", "assumed availability date", "6 days old",
                   "Last engine run: ok"):
        assert needle in html_body or needle in text, needle
    assert "&lt;Inc&gt;" in html_body and "<Inc>" not in html_body and "<table" in html_body and "width=\"600\"" in html_body


def test_an_empty_digest_says_so():
    _, h, t = A.daily_digest([], QUALITY, TODAY)
    assert "No new events" in h


RESEARCH = {"experiments": [{"key": "earnings_drift", "version": "v1", "verdict": "NO_EDGE_DEMONSTRATED", "test_expectancy_r": 0.023},
                            {"key": "fundamental_inflection", "version": "v1", "verdict": "INSUFFICIENT_DATA", "test_expectancy_r": None}],
            "failure_patterns": [{"pattern": "the pool is indistinguishable from zero", "evidence": "mean -0.003 R, n 5024"}],
            "derived_hypothesis": {"statement": "Within parent signals, a falling 20-session trend keeps drifting."},
            "next_steps": [{"status": "blocked on data", "statement": "SEC acceptance times need the operator contact."}]}


def test_the_weekly_report_has_events_experiments_negative_findings_hypotheses_and_gaps():
    wk = [ev("A1", strength=95), ev("B2", strength=70, severity="medium")]
    subj, h, t = A.weekly_report(wk, {"margin_expansion": 2}, RESEARCH, QUALITY, "2026-W44")
    assert subj == "First Light weekly research report · 2026-W44"
    for needle in ("2 new events", "earnings_drift v1", "no edge demonstrated", "insufficient data", "Negative findings and failure patterns", "indistinguishable from zero", "New hypothesis registered",
                   "falling 20-session trend", "blocked on data", "Data quality and coverage"):
        assert needle in h, needle
    assert "No experiments" not in h and "RESEARCH SIGNAL" in t
    _, h2, _ = A.weekly_report([], {}, {"experiments": []}, QUALITY, "2026-W45")
    assert "none completed this week" in h2


def test_email_is_sent_once_per_key_and_never_when_dry():
    d, sent = FakeDelivery(), []
    f = lambda s, h, t: sent.append(s)   # noqa: E731
    assert A.send_email_once(d, "email_digest:2026-10-31", "email_digest", "s", "h", "t", f, dry_run=True, log=lambda m: None) == "dry-run" and not sent
    assert A.send_email_once(d, "email_digest:2026-10-31", "email_digest", "s", "h", "t", f, dry_run=False, log=lambda m: None) == "sent"
    assert A.send_email_once(d, "email_digest:2026-10-31", "email_digest", "s", "h", "t", f, dry_run=False, log=lambda m: None) == "duplicate" and len(sent) == 1
    def bad(s, h, t): raise RuntimeError("smtp down")
    assert A.send_email_once(d, "email_digest:2026-11-01", "email_digest", "s", "h", "t", bad, dry_run=False, log=lambda m: None) == "failed"
    assert d.rows["email_digest:2026-11-01"]["status"] == "failed"
    assert A.send_email_once(d, "email_digest:2026-11-01", "email_digest", "s", "h", "t", f, dry_run=False, log=lambda m: None) == "sent"


def test_no_recipient_is_ever_invented(monkeypatch):
    class NoDb:
        def execute_dict_query(self, *a, **k): return []
    monkeypatch.delenv("INFLECTION_EMAIL_TO", raising=False)
    assert A.resolve_email_to(NoDb()) is None
    monkeypatch.setenv("INFLECTION_EMAIL_TO", "not-an-address")
    assert A.resolve_email_to(NoDb()) is None
    monkeypatch.setenv("INFLECTION_EMAIL_TO", "owner")
    assert A.resolve_email_to(NoDb()) is None                                        # 'owner' with no owner account on file
    monkeypatch.setenv("INFLECTION_EMAIL_TO", "ops@example.invalid")
    assert A.resolve_email_to(NoDb()) == "ops@example.invalid"


def test_telegram_alerts_use_the_owners_private_chat_never_a_channel():
    src = open(os.path.join(ROOT, "mechanism", "inflection", "alerts.py"), encoding="utf-8").read()
    assert 'TelegramClient.from_env("owner", dry_run=False)' in src
    assert '"prod"' not in src.split("def run(")[1] and '"dev"' not in src.split("def run(")[1]


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ the real claim table
def _db():
    try:
        from shared import db as _d
        _d.execute_dict_query("SELECT 1 FROM inflection_alert_delivery LIMIT 1")
        return _d
    except Exception as e:  # pragma: no cover
        pytest.skip(f"database with migration 40 not reachable: {e}")


def test_the_database_claim_is_atomic_exactly_once_and_retries_failures_up_to_three_times():
    d = _db()
    d.execute_insert("DELETE FROM inflection_alert_delivery WHERE dedupe_key LIKE 'test:%%'")
    try:
        dl = A.DbDelivery(d)
        assert dl.claim("test:a", "telegram_owner", "owner", None) is True
        assert dl.claim("test:a", "telegram_owner", "owner", None) is False                 # a claimed (in flight) alert is never re-claimed
        dl.mark("test:a", True)
        assert dl.claim("test:a", "telegram_owner", "owner", None) is False                 # sent: never again
        assert dl.claim("test:b", "email_digest", "owner", None) is True
        dl.mark("test:b", False, "smtp down")
        assert dl.claim("test:b", "email_digest", "owner", None) is True                    # failed: attempt 2
        dl.mark("test:b", False, "smtp down")
        assert dl.claim("test:b", "email_digest", "owner", None) is True                    # attempt 3
        dl.mark("test:b", False, "smtp down")
        assert dl.claim("test:b", "email_digest", "owner", None) is False                   # give up: visible as failed
        row = d.execute_dict_query("SELECT status, attempts, last_error, recipient_ref FROM inflection_alert_delivery WHERE dedupe_key = 'test:b'")[0]
        assert (row["status"], row["attempts"], row["last_error"], row["recipient_ref"]) == ("failed", 3, "smtp down", "owner")
    finally:
        d.execute_insert("DELETE FROM inflection_alert_delivery WHERE dedupe_key LIKE 'test:%%'")
