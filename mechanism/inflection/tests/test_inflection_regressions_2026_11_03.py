"""Regressions from the first full SEC run review (2026-11-03). Real cases from production, checked against SEC 10-Q / 10-K acceptance dates:

  * availability: a quarter whose own earnings-report row had no EPS claimed the NEXT quarter's report (window up to +130 days). NSSC Q 2026-06-30 was dated 2026-11-02 (10-K accepted
    2026-08-24); OSIS 06-30 -> 10-29 (10-K 08-21); ARXS 03-31 -> 07-29 (10-Q 05-28); FLXS 06-30 -> 10-26 (10-K 08-19).
  * alerts: the re-dated NSSC events (first detected 2026-10-31) fell into the 3-day freshness window and were sent to the owner as new on 2026-11-03.
  * duplicates and dating: the overflow and digest keys followed the wall clock, any method version could re-alert the same company-quarter-event, SEC PIT is not alertable.
No database and no network, except the last test (PostgreSQL with migration 40, skipped without).
"""
import os
import sys
import types
from datetime import date, datetime, timedelta, timezone

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))
sys.path.insert(0, ROOT)

from inflection import alerts as A  # noqa: E402
from inflection import config as C  # noqa: E402
from inflection import features as F  # noqa: E402
from inflection import pipeline as P  # noqa: E402
from inflection.tests.test_inflection_alerts import FakeDelivery, ev  # noqa: E402

D = date.fromisoformat


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ availability attribution
@pytest.mark.parametrize("symbol, quarter_end, confirmed_reports, sec_accepted", [
    ("NSSC", "2026-06-30", ["2026-11-02"], "2026-08-24"),                    # the 2026-08-24 row had no EPS
    ("OSIS", "2026-06-30", ["2026-05-04", "2026-10-29"], "2026-08-21"),      # the 2026-08-20 row had no EPS
    ("ARXS", "2026-03-31", ["2026-07-29", "2026-10-28"], "2026-05-28"),      # no report row for the quarter at all
    ("FLXS", "2026-06-30", ["2026-04-20", "2026-10-26"], "2026-08-19"),      # the 2026-08-17 row had no EPS
])
def test_the_next_quarters_report_is_never_attributed_to_the_previous_quarter(symbol, quarter_end, confirmed_reports, sec_accepted):
    qe = D(quarter_end)
    d, basis = P.resolve_availability(qe, [D(x) for x in confirmed_reports], "2026-10-14 01:15:35")
    assert basis == "assumed_lag_75d" and d == qe + timedelta(days=C.ASSUMED_LAG_DAYS), (symbol, d, basis)
    assert d not in [D(x) for x in confirmed_reports]
    # the fallback is conservative for these cases: never EARLIER than the SEC filing that first made the quarter public
    assert d >= D(sec_accepted)


@pytest.mark.parametrize("quarter_end, reports, expected", [
    ("2026-09-30", ["2026-11-02"], "2026-11-02"),          # NSSC's next quarter keeps its own report
    ("2026-06-30", ["2026-07-29", "2026-10-28"], "2026-07-29"),   # ARXS Q2 (10-Q accepted 2026-07-30)
    ("2026-09-30", ["2026-10-19", "2026-10-26"], "2026-10-19"),   # the first report in the window wins
])
def test_a_quarters_own_report_is_still_used(quarter_end, reports, expected):
    assert F.availability(D(quarter_end), [D(x) for x in reports]) == (D(expected), "earnings_calendar")


def test_the_window_closes_after_104_days_and_keeps_late_filers_up_to_it():
    qe = D("2025-12-31")
    assert F.availability(qe, [qe + timedelta(days=100)]) == (qe + timedelta(days=100), "earnings_calendar")      # a late 10-K (seen on production, within 7 days of the filing)
    assert F.availability(qe, [qe + timedelta(days=104)])[1] == "earnings_calendar"
    assert F.availability(qe, [qe + timedelta(days=105)])[1].startswith("assumed")


def test_the_engine_no_longer_emits_nssc_dated_2026_11_02():
    rows = [{"quarter": D(e), "fiscal_year": None, "fiscal_quarter": None, "revenue": r, "operating_income": oi, "net_income": ni, "operating_cash_flow": None, "free_cash_flow": None,
             "updated_at": "2026-10-14 01:15:35"}
            for e, r, oi, ni in [("2025-03-31", 44e6, 9e6, 8e6), ("2025-06-30", 50.7e6, 12e6, 11e6), ("2025-09-30", 49.2e6, 12e6, 11e6), ("2025-12-31", 48.2e6, 11e6, 10e6),
                                 ("2026-03-31", 49.2e6, 12e6, 11e6), ("2026-06-30", 55.8e6, 20e6, 17e6)]]
    feats, evs = P.build_symbol("NSSC", rows, [D("2026-11-02")], "Technology", None, None, None, D("2026-11-02"))
    june = [f for f in feats if f["quarter_end"] == D("2026-06-30")][0]
    assert june["available_at"] == D("2026-09-13") and june["availability_basis"] == "assumed_lag_75d"
    assert all(e["available_at"] != D("2026-11-02") for e in evs if e["quarter_end"] == D("2026-06-30"))


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ alert freshness
NSSC_REDATED = dict(symbol="NSSC", quarter_end="2026-06-30", available_at="2026-11-02", first_detected_on="2026-10-31")


@pytest.mark.parametrize("session", [D("2026-11-02"), D("2026-11-03")])
def test_a_re_dated_event_is_not_fresh(session):
    assert A.is_fresh(ev(**NSSC_REDATED), session, 3) is False


def test_freshness_needs_a_recent_public_date_and_a_detection_on_or_after_it():
    s = D("2026-11-02")
    assert A.is_fresh(ev(available_at="2026-11-02", first_detected_on="2026-11-02"), s, 3)          # public and detected the same evening
    assert A.is_fresh(ev(available_at="2026-10-30", first_detected_on="2026-11-02"), s, 3)          # a late data source, still within the window
    assert not A.is_fresh(ev(available_at="2026-10-28", first_detected_on="2026-11-02"), s, 3)      # public too long ago (a backfill)
    assert not A.is_fresh(ev(available_at="2026-11-03", first_detected_on="2026-11-03"), s, 3)      # after the session
    assert not A.is_fresh(ev(available_at="2026-11-02", first_detected_on=None), s, 3)              # unknown detection: not alerted


class _FakeDb:
    def execute_dict_query(self, sql, params=()):
        return [{"t": "inflection_alert_delivery"}]


def test_the_run_drops_re_dated_events_before_sending(monkeypatch):
    sent, fake = [], FakeDelivery()

    class _Client:
        @staticmethod
        def from_env(target, dry_run):
            assert target == "owner"
            return types.SimpleNamespace(send_message=lambda t, kind: sent.append(t))
    monkeypatch.setitem(sys.modules, "alerts.telegram_client", types.SimpleNamespace(TelegramClient=_Client))
    monkeypatch.setitem(sys.modules, "shared", types.SimpleNamespace(db=_FakeDb()))
    monkeypatch.setattr(A, "DbDelivery", lambda db: fake)
    monkeypatch.setattr(A, "_events", lambda db, where, params, limit=200: [ev(id=861, **NSSC_REDATED), ev("NEWCO", id=900, available_at="2026-11-02", first_detected_on="2026-11-02")])
    monkeypatch.setenv("INFLECTION_ALERTS_ENABLED", "1")
    logs = []
    out = A.run(D("2026-11-02"), send=True, telegram=True, log=logs.append)
    assert out["telegram"]["sent"] == 1 and len(sent) == 1 and "NEWCO" in sent[0] and "NSSC" not in sent[0]
    assert any("re-dated" in m for m in logs)


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ duplicates and dating
class LedgerDelivery(FakeDelivery):
    """FakeDelivery with the cross-version check the database implements."""

    def alerted_under_another_version(self, e):
        key = A.event_key(e)
        prefix = key.rsplit(":", 1)[0] + ":"
        return any(k.startswith(prefix) and k != key and r["status"] in ("claimed", "sent") for k, r in self.rows.items())


def test_the_same_company_quarter_event_alerts_once_across_method_versions():
    d, sent = LedgerDelivery(), []
    A.send_event_alerts([ev(method_version="inflection_v1")], d, sent.append, dry_run=False, log=lambda m: None)
    s = A.send_event_alerts([ev(method_version="inflection_v2_sec_pit", id=5)], d, sent.append, dry_run=False, log=lambda m: None)
    assert len(sent) == 1 and s["skipped_duplicate"] == 1


def test_the_overflow_note_is_keyed_on_the_session_not_the_wall_clock():
    d, sent = FakeDelivery(), []
    evs = [ev(f"S{i:02d}", strength=100 - i) for i in range(12)]
    A.send_event_alerts(evs, d, sent.append, max_per_run=10, dry_run=False, log=lambda m: None, day=D("2026-11-02"))
    assert "telegram_owner:overflow:2026-11-02" in d.rows
    A.send_event_alerts(evs + [ev("S99", strength=1)], d, sent.append, max_per_run=10, dry_run=False, log=lambda m: None, day=D("2026-11-02"))
    assert sum(1 for t in sent if "more fundamental inflection" in t) == 1


def test_only_the_v1_namespace_is_alertable():
    assert A.ALERT_METHOD_VERSIONS == (C.METHOD_VERSION,) == ("inflection_v1",)
    assert C.METHOD_VERSION_SEC_PIT not in A.ALERT_METHOD_VERSIONS and C.METHOD_VERSION_SEC not in A.ALERT_METHOD_VERSIONS


def test_the_digest_counts_but_does_not_list_backfilled_events():
    subj, h, t = A.daily_digest([ev()], {}, D("2026-11-02"), older=2116)
    assert subj.startswith("First Light: 1 new") and "2116 newly computed event(s) for quarters public more than 30 days ago" in t and "2116 newly computed" in h


def test_the_digest_window_starts_at_the_previous_digest(monkeypatch):
    seen = {}
    last = datetime(2026, 11, 3, 0, 8, 53, tzinfo=timezone.utc)

    class Ledger(FakeDelivery):
        def last_sent_at(self, channel):
            return last if channel == "email_digest" else None

    def fake_events(db, where, params, limit=200):
        seen["where"], seen["params"] = where, params
        return [ev("FRESH", available_at="2026-11-03"), ev("OLD", available_at="2025-05-01")]
    monkeypatch.setitem(sys.modules, "shared", types.SimpleNamespace(db=_FakeDb()))
    monkeypatch.setattr(A, "DbDelivery", lambda db: Ledger())
    monkeypatch.setattr(A, "_events", fake_events)
    monkeypatch.setattr(A, "resolve_email_to", lambda db: "ops@example.invalid")
    monkeypatch.setattr(A, "quality_snapshot", lambda db, asof: {})
    logs = []
    out = A.run(D("2026-11-03"), daily=True, log=logs.append)                       # dry run: nothing claimed or sent
    assert seen["params"] == (last,) and "first_detected_at > %s" in seen["where"] and out["daily"] == "dry-run"
    assert any("email_digest:2026-11-03" in m for m in logs)


def test_the_pipeline_dates_every_inflection_step_by_the_gate_session():
    src = open(os.path.join(ROOT, "automation_pipeline.sh"), encoding="utf-8").read()
    assert 'ASOF_ARGS=(--asof "$SESSION_DATE")' in src
    for step in ('mechanism/inflection/updater.py "${ASOF_ARGS[@]}"', 'mechanism/inflection/alerts.py --send --telegram --daily-digest --weekly-on-friday "${ASOF_ARGS[@]}"',
                 'mechanism/inflection/updater.py --source sec_pit "${ASOF_ARGS[@]}"'):
        assert step in src, step


def test_the_sec_pit_window_is_anchored_on_the_session(monkeypatch):
    from inflection import updater as U
    got = {}
    monkeypatch.setattr(U.InflectionUpdater, "__init__", lambda self, logger=None: None)
    monkeypatch.setattr(U.InflectionUpdater, "run", lambda self, **k: got.update(k) or {"errors": 0})
    monkeypatch.setattr(sys, "argv", ["updater.py", "--source", "sec_pit", "--asof", "2026-11-02"])
    assert U.main() == 0
    assert got["asof"] == D("2026-11-02") and got["since"] == D("2026-11-02") - timedelta(days=200)


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ the real claim table
def _db():
    try:
        from shared import db as _d
        _d.execute_dict_query("SELECT 1 FROM inflection_alert_delivery LIMIT 1")
        return _d
    except Exception as e:  # pragma: no cover
        pytest.skip(f"database with migration 40 not reachable: {e}")


def test_the_database_finds_an_alert_under_another_version_and_the_last_digest():
    d = _db()
    d.execute_insert("DELETE FROM inflection_alert_delivery WHERE dedupe_key LIKE 'telegram_owner:ZZT\\_X:%%' OR dedupe_key = 'email_digest:1999-01-01'")
    try:
        dl = A.DbDelivery(d)
        e1 = ev("ZZT_X", method_version="inflection_v1")
        assert dl.alerted_under_another_version(e1) is False
        assert dl.claim(A.event_key(e1), "telegram_owner", "owner", None)
        dl.mark(A.event_key(e1), True)
        assert dl.alerted_under_another_version(ev("ZZT_X", method_version="inflection_v2_sec_pit")) is True
        assert dl.alerted_under_another_version(e1) is False                                    # its own key is not "another version"
        assert dl.alerted_under_another_version(ev("ZZTAX", method_version="inflection_v2_sec_pit")) is False   # '_' is not a wildcard
        assert dl.claim("email_digest:1999-01-01", "email_digest", "owner", None)
        dl.mark("email_digest:1999-01-01", True)
        assert dl.last_sent_at("email_digest") is not None
    finally:
        d.execute_insert("DELETE FROM inflection_alert_delivery WHERE dedupe_key LIKE 'telegram_owner:ZZT\\_X:%%' OR dedupe_key = 'email_digest:1999-01-01'")
