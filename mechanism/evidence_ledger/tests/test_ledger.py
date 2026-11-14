"""Evidence Ledger v0, pure: capture records, the price conventions, every outcome status, denominators, the pre-release consensus rule and the
append-only store. No database."""
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from evidence_ledger import consensus as Cn  # noqa: E402
from evidence_ledger import model as M  # noqa: E402
from evidence_ledger.store import LedgerCorrupt, Store  # noqa: E402

UTC = timezone.utc


def weekdays(start, end):
    d, out = start, []
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


SESSIONS = weekdays(date(2026, 11, 2), date(2027, 6, 30))


def brief(sym, category="business_improved_market_confirmed", first_public="2026-11-11", excess=4.2):
    return {"version": "brief_v1", "symbol": sym, "quarter_end": "2026-09-30", "first_public": first_public,
            "filing": {"form": "10-Q", "accession": "0000000000-26-000009", "accepted_at": "2026-11-11T21:00:00+00:00", "source": "SEC 10-Q x"},
            "assessment": {"category": category, "dimensions": {"business": "supports", "expectations": "unverified", "market": "positive"},
                           "business_improved": ["revenue", "operating"], "business_worsened": []},
            "evidence": {"market": {"figures": [{"label": "Move relative to the S&P 500", "value": excess}]}},
            "contradictions": [], "what_changed": [{"event_type": "margin_expansion"}]}


CAPTURED = datetime(2026, 11, 12, 10, 20, tzinfo=UTC)          # 05:20 New York on Thursday 11-12


def obs(sym="AAA", **kw):
    return M.observation_record(brief(sym, **kw), date(2026, 11, 11), CAPTURED, SESSIONS, {"sector": "Technology"})


# ------------------------------------------------------------- discovery records
def test_groups_and_basis():
    assert obs()["group"] == M.GROUP_POSITIVE and obs()["basis"] == M.PROSPECTIVE
    assert obs(category="business_improved_market_not_confirming")["group"] == M.GROUP_NOT_POSITIVE
    assert obs(category="business_mixed")["group"] == M.GROUP_OTHER                        # the whole universe is recorded
    assert obs(first_public="2026-11-10")["basis"] == M.PROSPECTIVE                        # the session before the captured one
    assert obs(first_public="2026-11-03")["basis"] == M.LATE                               # backlog: never mixed into the prospective cohort


def test_the_capture_time_is_the_actual_time_and_the_evidence_is_hashed():
    o = obs()
    assert o["captured_at"] == "2026-11-12T10:20:00+00:00" and o["evidence_hash"] == M.sha(o["evidence"])
    assert obs(excess=4.3)["evidence_hash"] != o["evidence_hash"]


# ------------------------------------------------------------- price conventions
def test_entry_is_the_first_close_after_the_capture():
    assert M.entry_session(CAPTURED, SESSIONS) == date(2026, 11, 12)                       # captured before the open: same-day close
    assert M.entry_session(datetime(2026, 11, 12, 21, 30, tzinfo=UTC), SESSIONS) == date(2026, 11, 13)   # after the close: next session


def test_exit_is_the_last_session_on_or_before_entry_plus_n_days_and_only_when_the_data_reaches_it():
    assert M.exit_session(date(2026, 11, 12), 30, SESSIONS) == date(2026, 12, 11)          # 12-12 is a Saturday
    assert M.exit_session(date(2026, 11, 12), 30, [d for d in SESSIONS if d < date(2026, 12, 12)]) is None


def _bars(syms_end=None, start=100.0, end=110.0, until=date(2027, 6, 30)):
    return {d: (end if d > date(2026, 11, 12) else start) for d in SESSIONS if d <= until}


BENCH = {d: (5150.0 if d > date(2026, 11, 12) else 5000.0) for d in SESSIONS}


def test_a_complete_horizon_against_the_sp500_and_the_sector_median():
    peers = {"P1": {date(2026, 11, 12): 50.0, date(2026, 12, 11): 52.0}, "P2": {date(2026, 11, 12): 10.0, date(2026, 12, 11): 10.4},
             "P3": {date(2026, 11, 12): 20.0}}                                             # P3 has no exit close: not in the median
    o = M.outcome(obs(), 30, SESSIONS, _bars(), BENCH, sector_bars=peers)
    assert o.status == M.COMPLETE and o.entry == "2026-11-12" and o.exit == "2026-12-11"
    assert round(o.stock_pct, 6) == 10.0 and round(o.sp500_pct, 6) == 3.0 and round(o.excess_vs_sp500_pp, 6) == 7.0
    assert o.sector_peers == 2 and round(o.sector_median_pct, 6) == 4.0 and round(o.excess_vs_sector_pp, 6) == 6.0


def test_pending_until_the_window_is_complete():
    short = [d for d in SESSIONS if d <= date(2027, 1, 15)]
    rows = {h: M.outcome(obs(), h, short, _bars(), BENCH).status for h in M.HORIZONS_DAYS}
    assert rows == {30: M.COMPLETE, 90: M.PENDING, 180: M.PENDING}


def test_a_split_or_discontinuity_makes_the_horizon_unreliable_but_counted():
    b = _bars()
    for d in SESSIONS:
        if d >= date(2026, 11, 20):
            b[d] = b[d] / 2                                                                  # unadjusted 2:1 split on 11-20
    assert M.outcome(obs(), 30, SESSIONS, b, BENCH).status == M.UNRELIABLE
    assert M.outcome(obs(), 30, SESSIONS, _bars(), BENCH, discontinuities=[date(2026, 11, 25)]).status == M.UNRELIABLE


def test_a_delisting_or_data_gap_is_ended_early_with_the_return_to_the_last_close():
    o = M.outcome(obs(), 30, SESSIONS, _bars(until=date(2026, 12, 1)), BENCH)
    assert o.status == M.ENDED_EARLY and round(o.stock_pct, 6) == 10.0 and "2026-12-01" in o.note and o.excess_vs_sp500_pp is None


def test_no_entry_close_is_counted_as_no_entry():
    b = _bars()
    del b[date(2026, 11, 12)]
    assert M.outcome(obs(), 30, SESSIONS, b, BENCH).status == M.NO_ENTRY


def test_every_discovery_stays_in_the_denominator_and_groups_are_separate():
    o1, o2, o3 = obs("AAA"), obs("BBB"), obs("CCC", category="business_improved_market_not_confirming")
    gone = _bars(until=date(2026, 12, 1))
    outs = {o1["key"]: [M.outcome(o1, h, SESSIONS, _bars(), BENCH) for h in M.HORIZONS_DAYS],
            o2["key"]: [M.outcome(o2, h, SESSIONS, gone, BENCH) for h in M.HORIZONS_DAYS],
            o3["key"]: [M.outcome(o3, h, SESSIONS, _bars(end=95.0), BENCH) for h in M.HORIZONS_DAYS]}
    s = M.summarize([o1, o2, o3], outs)
    pos = s[M.GROUP_POSITIVE]["horizons"]["30"]
    assert s[M.GROUP_POSITIVE]["discoveries"] == 2 and pos["observed"] == 2 and pos[M.COMPLETE] == 1 and pos[M.ENDED_EARLY] == 1
    assert round(pos["median_excess_vs_sp500_pp"], 6) == 7.0                              # the median uses complete horizons, the denominator shows both
    neg = s[M.GROUP_NOT_POSITIVE]["horizons"]["30"]
    assert neg["observed"] == 1 and round(neg["median_excess_vs_sp500_pp"], 6) == -8.0 and neg["share_above_sp500"] == 0
    assert M.summarize([o1, o2, o3], outs, M.LATE)[M.GROUP_POSITIVE]["discoveries"] == 0


# ------------------------------------------------------------- pre-release consensus stream
def test_only_genuine_pre_release_estimates_are_captured():
    row = {"symbol": "AAA", "report_date": date(2026, 11, 20), "eps_estimate": 1.23, "fetched_at": datetime(2026, 11, 11, 22, 0, tzinfo=UTC)}
    rec = Cn.consensus_record(row, CAPTURED)
    assert rec["stream"] == Cn.STREAM and rec["captured_at"] == "2026-11-12T10:20:00+00:00" and rec["vendor_fetched_at"] == "2026-11-11T22:00:00+00:00"
    assert Cn.consensus_record(dict(row, report_date=date(2026, 11, 12)), CAPTURED) is None    # release day already begun: never verified retroactively
    assert Cn.consensus_record(dict(row, fetched_at=datetime(2026, 11, 20, 6, 0, tzinfo=UTC)), CAPTURED) is None
    assert Cn.consensus_record(dict(row, eps_estimate=None), CAPTURED) is None


# ------------------------------------------------------------- the store
def test_first_capture_wins_and_a_change_is_a_revision(tmp_path):
    st = Store(str(tmp_path / "l.jsonl"))
    o = obs()
    assert st.append("observation", o["key"], o, compare="evidence_hash") == "added"
    later = dict(o, captured_at="2026-11-13T10:20:00+00:00")
    assert st.append("observation", o["key"], later, compare="evidence_hash") == "unchanged"   # same evidence again keeps the first time
    changed = obs(excess=-3.0)
    assert st.append("observation", o["key"], changed, compare="evidence_hash") == "revision"
    re = Store(str(tmp_path / "l.jsonl"))
    assert [r["captured_at"] for r in re.records("observation")] == ["2026-11-12T10:20:00+00:00"]
    assert [l["kind"] for l in re.lines] == ["observation", "observation_revision"] and re.lines[1]["record"]["revises_seq"] == 1


def test_an_edited_or_reordered_file_is_refused(tmp_path):
    p = tmp_path / "l.jsonl"
    st = Store(str(p))
    st.append("observation", "A", obs("AAA"), compare="evidence_hash")
    st.append("observation", "B", obs("BBB"), compare="evidence_hash")
    lines = p.read_text(encoding="utf-8").splitlines()
    p.write_text(lines[0].replace("AAA", "ZZZ") + "\n" + lines[1] + "\n", encoding="utf-8")
    with pytest.raises(LedgerCorrupt):
        Store(str(p))
    p.write_text(lines[1] + "\n" + lines[0] + "\n", encoding="utf-8")
    with pytest.raises(LedgerCorrupt):
        Store(str(p))


def test_the_ledger_never_writes_the_database_or_sends():
    root = os.path.dirname(M.__file__)
    for name in os.listdir(root):
        if name.endswith(".py"):
            src = open(os.path.join(root, name), encoding="utf-8").read()
            for forbidden in ("INSERT ", "UPDATE ", "DELETE ", "telegram", "TelegramClient", "research.labels"):
                assert forbidden not in src, (name, forbidden)


# ------------------------------------------------------------- classification after the market window
def _unclassified():
    o = obs(category="business_improved_market_unreadable")
    o["evidence"]["dimensions"]["market"] = "unavailable"
    return o


def test_a_timely_discovery_awaits_its_market_window_then_is_classified_by_the_later_capture():
    first = _unclassified()
    assert M.classified([first])["group"] == M.GROUP_AWAITING
    later = obs(category="business_improved_market_confirmed")
    later["captured_at"] = "2026-11-17T09:00:00+00:00"
    c = M.classified([first, later])
    assert c["group"] == M.GROUP_POSITIVE and c["basis"] == M.PROSPECTIVE
    assert c["discovered_at"] == first["captured_at"] and c["captured_at"] == "2026-11-17T09:00:00+00:00"
    o = M.outcome(c, 30, SESSIONS, _bars(), BENCH)
    assert o.entry == "2026-11-17"                                                           # the outcome clock starts after the classifying capture


def test_the_store_keeps_the_discovery_and_the_classification_as_a_revision(tmp_path):
    st = Store(str(tmp_path / "l.jsonl"))
    first = _unclassified()
    st.append("observation", first["key"], first, compare="evidence_hash")
    later = obs(category="business_improved_market_not_confirming")
    later["captured_at"] = "2026-11-17T09:00:00+00:00"
    later["evidence_hash"] = M.sha(dict(later["evidence"], market="flat"))
    assert st.append("observation", first["key"], later, compare="evidence_hash") == "revision"
    c = M.classified(Store(str(tmp_path / "l.jsonl")).history("observation")[first["key"]])
    assert c["group"] == M.GROUP_NOT_POSITIVE and c["discovered_at"] == first["captured_at"]
