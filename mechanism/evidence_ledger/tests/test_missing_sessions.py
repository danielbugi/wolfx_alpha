"""Missed nights: delayed processing of evidence the system had already recorded (labelled, never back-dated, never 'prospective') versus genuinely
missing sessions (recorded as gaps, no observations manufactured). No database."""
import os
import sys
from datetime import date, datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from evidence_ledger import cli as C  # noqa: E402
from evidence_ledger import model as M  # noqa: E402
from evidence_ledger.store import Store  # noqa: E402

UTC = timezone.utc


def weekdays(start, end):
    d, out = start, []
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


SESSIONS = weekdays(date(2026, 11, 2), date(2027, 6, 30))


def brief(sym="AAA", first_public="2026-11-11", market="positive"):
    return {"version": "brief_v1", "symbol": sym, "quarter_end": "2026-09-30", "first_public": first_public,
            "filing": {"form": "10-Q", "accession": "0000000000-26-000009", "accepted_at": "2026-11-11T21:00:00+00:00", "source": "SEC 10-Q x"},
            "assessment": {"category": "business_improved_market_confirmed", "dimensions": {"business": "supports", "expectations": "unverified", "market": market},
                           "business_improved": ["revenue"], "business_worsened": []},
            "evidence": {"market": {"figures": [{"label": "Move relative to the S&P 500", "value": 3.0}]}},
            "contradictions": [], "what_changed": [{"event_type": "margin_expansion"}]}


LATE_RUN = datetime(2026, 11, 17, 10, 30, tzinfo=UTC)          # the ledger was down 11-12 .. 11-16; it runs again on Tuesday 11-17
CUTOFF = "2026-11-12T12:00:00+00:00"                            # session 11-11's knowledge cutoff


# ---------------------------------------------------------------- the record
def test_a_delayed_record_is_never_prospective_and_keeps_its_actual_capture_time():
    r = M.observation_record(brief(), date(2026, 11, 11), LATE_RUN, SESSIONS, None, delayed=True, evidence_cutoff=CUTOFF)
    assert r["basis"] == M.DELAYED and r["processing"] == M.RUN_DELAYED
    assert r["captured_at"] == "2026-11-17T10:30:00+00:00"                                   # actual, never back-dated to the session
    assert r["evidence_cutoff"] == CUTOFF and r["original_available_at"] == "2026-11-11T21:00:00+00:00"
    on_time = M.observation_record(brief(), date(2026, 11, 11), datetime(2026, 11, 12, 5, 30, tzinfo=UTC), SESSIONS, None, evidence_cutoff=CUTOFF)
    assert on_time["basis"] == M.PROSPECTIVE and on_time["processing"] == M.RUN_ON_TIME
    assert r["evidence_hash"] == on_time["evidence_hash"]                                   # the same evidence, only the label and time differ


def test_a_backlog_item_stays_late_whether_processed_on_time_or_delayed():
    for delayed in (False, True):
        assert M.observation_record(brief(first_public="2026-11-03"), date(2026, 11, 11), LATE_RUN, SESSIONS, None, delayed=delayed)["basis"] == M.LATE


def test_the_outcome_clock_of_a_delayed_record_starts_after_the_actual_capture():
    r = M.classified([M.observation_record(brief(), date(2026, 11, 11), LATE_RUN, SESSIONS, None, delayed=True, evidence_cutoff=CUTOFF)])
    bars = {d: 100.0 for d in SESSIONS}
    o = M.outcome(r, 30, SESSIONS, bars, {d: 5000.0 for d in SESSIONS})
    assert o.entry == "2026-11-17"                                                           # not 11-11 or 11-12: no back-dating of the entry
    assert r["processing"] == M.RUN_DELAYED and r["basis"] == M.DELAYED


def test_the_label_survives_a_later_classifying_revision():
    first = M.observation_record(brief(market="unavailable"), date(2026, 11, 11), LATE_RUN, SESSIONS, None, delayed=True, evidence_cutoff=CUTOFF)
    later = M.observation_record(brief(market="positive"), date(2026, 11, 17), LATE_RUN + timedelta(days=2), SESSIONS, None)
    c = M.classified([first, later])
    assert c["basis"] == M.DELAYED and c["processing"] == M.RUN_DELAYED and c["discovered_at"] == first["captured_at"]


# ---------------------------------------------------------------- the plan
D = date


def test_an_empty_ledger_plans_no_gap():
    assert M.plan_sessions(SESSIONS, D(2026, 11, 16), []) == ([], [])


def test_a_short_gap_is_processed_late_and_a_long_one_beyond_the_cap_is_missed():
    delayed, missed = M.plan_sessions(SESSIONS, D(2026, 11, 17), [D(2026, 11, 11)])
    assert delayed == [D(2026, 11, 12), D(2026, 11, 13), D(2026, 11, 16)] and missed == []
    delayed, missed = M.plan_sessions(SESSIONS, D(2026, 11, 24), [D(2026, 11, 11)])          # 8 sessions missed, cap 5
    assert delayed == [D(2026, 11, 17), D(2026, 11, 18), D(2026, 11, 19), D(2026, 11, 20), D(2026, 11, 23)]
    assert missed == [D(2026, 11, 12), D(2026, 11, 13), D(2026, 11, 16)]


def test_no_gap_on_consecutive_nights_and_holidays_are_not_gaps():
    assert M.plan_sessions(SESSIONS, D(2026, 11, 12), [D(2026, 11, 11)]) == ([], [])
    holiday_calendar = [d for d in SESSIONS if d != D(2026, 11, 26)]                         # Thanksgiving is not a session
    assert M.plan_sessions(holiday_calendar, D(2026, 11, 27), [D(2026, 11, 25)]) == ([], [])


# ---------------------------------------------------------------- the nightly run
@pytest.fixture
def fake_capture(monkeypatch):
    calls = []
    absent = set()

    def fake(connect, d, store, captured_at, days=14, delayed=False):
        if d in absent:
            raise C.NotReady(f"session {d} not loaded yet")
        calls.append((d, delayed))
        store.append("capture_run", d.isoformat(), M.capture_run_record(d, captured_at, M.RUN_DELAYED if delayed else M.RUN_ON_TIME, 1, CUTOFF),
                     compare="session")
        return {"session": d.isoformat(), "briefs": 1, "observations": {}}

    monkeypatch.setattr(C, "capture", fake)
    return calls, absent


def test_nightly_processes_the_gap_late_then_the_latest_on_time(tmp_path, fake_capture):
    calls, _ = fake_capture
    store = Store(str(tmp_path / "l.jsonl"))
    store.append("capture_run", "2026-11-11", M.capture_run_record(D(2026, 11, 11), LATE_RUN - timedelta(days=5), M.RUN_ON_TIME, 1, CUTOFF), compare="session")
    res = C.nightly(None, store, LATE_RUN, D(2026, 11, 17), SESSIONS)
    assert calls == [(D(2026, 11, 12), True), (D(2026, 11, 13), True), (D(2026, 11, 16), True), (D(2026, 11, 17), False)]
    assert res["missed"] == [] and [x["session"] for x in res["delayed"]] == ["2026-11-12", "2026-11-13", "2026-11-16"]
    calls.clear()
    C.nightly(None, store, LATE_RUN + timedelta(hours=3), D(2026, 11, 17), SESSIONS)          # the second fire: nothing left to plan
    assert calls == [(D(2026, 11, 17), False)]


def test_beyond_the_cap_and_absent_data_are_recorded_as_missed_with_no_observations(tmp_path, fake_capture):
    calls, absent = fake_capture
    absent.add(D(2026, 11, 20))
    store = Store(str(tmp_path / "l.jsonl"))
    store.append("capture_run", "2026-11-11", M.capture_run_record(D(2026, 11, 11), LATE_RUN, M.RUN_ON_TIME, 1, CUTOFF), compare="session")
    res = C.nightly(None, store, datetime(2026, 11, 25, 10, 30, tzinfo=UTC), D(2026, 11, 24), SESSIONS)
    assert res["missed"] == ["2026-11-12", "2026-11-13", "2026-11-16", "2026-11-20"]
    runs = {k: v[0]["mode"] for k, v in store.history("capture_run").items()}
    assert [k for k, m in runs.items() if m == M.RUN_MISSED] == ["2026-11-12", "2026-11-13", "2026-11-16", "2026-11-20"]
    assert "absent" in store.history("capture_run")["2026-11-20"][0]["reason"]
    assert list(store.records("observation")) == []                                         # nothing manufactured for a gap
    assert (D(2026, 11, 20), True) not in calls and calls[-1] == (D(2026, 11, 24), False)


def test_the_first_manual_capture_counts_as_dealt_with(tmp_path, fake_capture):
    calls, _ = fake_capture
    store = Store(str(tmp_path / "l.jsonl"))
    legacy = M.observation_record(brief(), D(2026, 11, 11), LATE_RUN, SESSIONS, None)
    legacy.pop("processing")                                                                 # a line written before capture_run existed
    store.append("observation", legacy["key"], legacy, compare="evidence_hash")
    C.nightly(None, store, LATE_RUN, D(2026, 11, 13), SESSIONS)
    assert calls == [(D(2026, 11, 12), True), (D(2026, 11, 13), False)]


# ---------------------------------------------------------------- coverage and summaries
def test_coverage_separates_on_time_delayed_missed_and_no_record():
    runs = {"2026-11-12": {"mode": M.RUN_DELAYED}, "2026-11-13": {"mode": M.RUN_MISSED}, "2026-11-16": {"mode": M.RUN_ON_TIME}}
    cov = M.coverage(SESSIONS[:15], runs, {D(2026, 11, 11)})
    assert cov[M.RUN_ON_TIME] == 2 and cov[M.RUN_DELAYED] == 1 and cov[M.RUN_MISSED] == 1
    assert cov["missed_sessions"] == ["2026-11-13"] and cov["delayed_sessions"] == ["2026-11-12"] and cov["no_record_sessions"][0] == "2026-11-17"


def test_delayed_discoveries_are_summarised_apart_from_prospective_ones():
    on = M.classified([M.observation_record(brief("AAA"), D(2026, 11, 11), datetime(2026, 11, 12, 5, 30, tzinfo=UTC), SESSIONS, None)])
    late = M.classified([M.observation_record(brief("BBB"), D(2026, 11, 11), LATE_RUN, SESSIONS, None, delayed=True)])
    outcomes = {o["key"]: [M.outcome(o, h, SESSIONS[:5], {}, {}) for h in M.HORIZONS_DAYS] for o in (on, late)}
    p, d = M.summarize([on, late], outcomes, M.PROSPECTIVE), M.summarize([on, late], outcomes, M.DELAYED)
    assert p[M.GROUP_POSITIVE]["discoveries"] == 1 and d[M.GROUP_POSITIVE]["discoveries"] == 1
    assert on["key"] != late["key"]


def test_an_explicit_future_session_is_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(C, "resolve_latest_completed", lambda now: (D(2026, 11, 12), SESSIONS[:9]))
    assert C.main(["capture", "--session", "2026-11-13", "--store", str(tmp_path / "l.jsonl")]) == C.EXIT_REFUSED
