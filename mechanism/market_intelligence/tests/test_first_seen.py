"""first_seen (pure): the change-only hash-chained observation contract. No database, clock or network."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

import mi_samples  # noqa: F401  (path setup)
from market_intelligence import first_seen as fs

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def obs(value, subject="AAPL", period="2026-10-30", dataset="earnings_row", **kw):
    return fs.Observation("prov_a", dataset, "symbol", subject, value, period_key=period, **kw)


def row(o, seq, prev, at):
    return {"series_key": o.key, "seq": seq, "value": o.value, "value_hash": o.hash, "prev_value_hash": prev, "observed_at": at}


# ---------------------------------------------------------------- identity and hashing
def test_value_hash_is_stable_across_key_order_and_numeric_types():
    a = fs.value_hash({"x": 1.5, "y": {"b": 1, "a": 2}})
    assert a == fs.value_hash({"y": {"a": 2, "b": 1}, "x": Decimal("1.5")})
    assert a != fs.value_hash({"x": 1.6, "y": {"b": 1, "a": 2}})


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
def test_non_numbers_are_refused_not_stored_as_zero_or_null(bad):
    with pytest.raises(fs.FirstSeenError):
        obs({"eps_estimate": bad})


def test_unknown_is_none_and_survives_verbatim():
    o = obs({"eps_estimate": None, "eps_actual": 1.2})
    assert o.value == {"eps_estimate": None, "eps_actual": 1.2}


def test_value_must_be_a_non_empty_mapping_and_naive_times_are_refused():
    for bad in ({}, None, [1], "x"):
        with pytest.raises(fs.FirstSeenError):
            obs(bad)
    with pytest.raises(fs.FirstSeenError):
        obs({"a": 1}, source_asof=datetime(2026, 10, 1))
    assert obs({"a": 1}, source_asof=T0.astimezone(timezone(timedelta(hours=3)))).source_asof == T0


def test_series_key_is_unambiguous_and_refuses_the_separator():
    assert fs.series_key("s", "d", "symbol", "AAPL", "2026Q3") == "s|d|symbol:AAPL|2026Q3"
    assert fs.series_key("s", "d", "symbol", "AAPL") == "s|d|symbol:AAPL|-"
    for args in (("s|x", "d", "symbol", "A"), ("s", "d", "symbol", ""), ("s", "d", "ticker", "A"), ("s", "d", "symbol", "A", "a|b")):
        with pytest.raises(fs.FirstSeenError):
            fs.series_key(*args)
    assert fs.series_key("s", "d", "symbol", "A") != fs.series_key("s", "d", "cik", "A")


# ---------------------------------------------------------------- change-only planning
def test_first_sight_is_seq_1_with_no_predecessor():
    p = fs.plan_appends([obs({"eps_estimate": 1.0})], {})
    assert [(a.seq, a.prev_value_hash) for a in p.appends] == [(1, None)] and p.unchanged == 0


def test_an_unchanged_value_writes_nothing_and_a_change_chains_to_the_head():
    o1, o2 = obs({"eps_estimate": 1.0}), obs({"eps_estimate": 1.1})
    head = fs.ChainHead(o1.key, 1, o1.hash)
    assert fs.plan_appends([o1], {o1.key: head}).appends == () and fs.plan_appends([o1], {o1.key: head}).unchanged == 1
    (a,) = fs.plan_appends([o2], {o1.key: head}).appends
    assert (a.seq, a.prev_value_hash, a.value_hash) == (2, o1.hash, o2.hash)


def test_a_reversion_is_a_new_row_because_it_is_information():
    a, b = obs({"eps_estimate": 1.0}), obs({"eps_estimate": 1.1})
    head = fs.ChainHead(a.key, 2, b.hash)             # A -> B stored; now A is seen again
    (p,) = fs.plan_appends([a], {a.key: head}).appends
    assert p.seq == 3 and p.prev_value_hash == b.hash


def test_one_batch_cannot_report_two_values_for_a_series_but_identical_repeats_collapse():
    p = fs.plan_appends([obs({"a": 1}), obs({"a": 1})], {})
    assert len(p.appends) == 1 and p.duplicates_in_batch == 1
    with pytest.raises(fs.FirstSeenError):
        fs.plan_appends([obs({"a": 1}), obs({"a": 2})], {})


def test_different_periods_and_subjects_are_different_series():
    p = fs.plan_appends([obs({"a": 1}), obs({"a": 1}, period="2027-01-30"), obs({"a": 1}, subject="MSFT")], {})
    assert len(p.appends) == 3


# ---------------------------------------------------------------- chain verification
def _chain():
    vals = [{"eps_estimate": 1.0}, {"eps_estimate": 1.1}, {"eps_estimate": 0.9}]
    os_ = [obs(v) for v in vals]
    rows, prev = [], None
    for i, o in enumerate(os_, start=1):
        rows.append(row(o, i, prev, T0 + timedelta(days=i)))
        prev = o.hash
    return rows


def test_an_intact_chain_verifies_whatever_the_row_order():
    r = _chain()
    assert fs.verify_chain(r) == [] and fs.verify_chain(list(reversed(r))) == []


def test_a_deleted_middle_row_a_tampered_value_and_a_reorder_are_all_caught():
    r = _chain()
    assert any("seq gap" in p for p in fs.verify_chain([r[0], r[2]]))
    tampered = [dict(x) for x in r]
    tampered[1]["value"] = {"eps_estimate": 5.0}
    assert any("does not hash" in p for p in fs.verify_chain(tampered))
    relinked = [dict(x) for x in r]
    relinked[2]["prev_value_hash"] = r[0]["value_hash"]
    assert any("predecessor" in p for p in fs.verify_chain(relinked))
    back = [dict(x) for x in r]
    back[2]["observed_at"] = T0
    assert any("observed_at" in p for p in fs.verify_chain(back))


def test_a_repeat_of_the_predecessor_and_mixed_series_are_flagged():
    r = _chain()
    dup = [dict(x) for x in r[:2]] + [dict(r[1], seq=3, prev_value_hash=r[1]["value_hash"])]
    assert any("repeats" in p for p in fs.verify_chain(dup))
    other = dict(r[0], series_key="x|y|symbol:Z|-")
    assert "different series" in fs.verify_chain([r[0], other])[0]


# ---------------------------------------------------------------- as-of reads
def test_as_of_returns_what_was_known_then_and_omits_what_was_not_yet_seen():
    r = _chain()
    assert fs.as_of(r, T0) == {}                                                       # nothing known yet
    k = r[0]["series_key"]
    assert fs.as_of(r, T0 + timedelta(days=1))[k]["seq"] == 1
    assert fs.as_of(r, T0 + timedelta(days=2, hours=1))[k]["seq"] == 2                 # the revision is invisible before it was observed
    assert fs.as_of(r, T0 + timedelta(days=30))[k]["seq"] == 3
    with pytest.raises(fs.FirstSeenError):
        fs.as_of(r, datetime(2026, 10, 3))


# ---------------------------------------------------------------- earnings_calendar adapter
def _cal(sym, d, est, act=None, sur=None):
    return {"symbol": sym, "report_date": d, "eps_estimate": est, "eps_actual": act, "surprise_pct": sur}


def test_calendar_adapter_emits_one_row_series_per_report_and_one_upcoming_list_per_symbol():
    rows = [_cal("AAPL", date(2026, 7, 30), 1.0, 1.1, 10.0), _cal("AAPL", date(2026, 10, 30), 1.2), _cal("MSFT", date(2026, 10, 28), None)]
    out = fs.observations_from_earnings_calendar(rows, date(2026, 10, 4))
    by = {(o.dataset, o.subject_id, o.period_key): o.value for o in out}
    assert by[("earnings_row", "AAPL", "2026-07-30")] == {"eps_estimate": 1.0, "eps_actual": 1.1, "surprise_pct": 10.0}
    assert by[("earnings_row", "MSFT", "2026-10-28")]["eps_estimate"] is None             # unknown stays unknown, not 0
    assert by[("earnings_upcoming_dates", "AAPL", None)] == {"dates": ["2026-10-30"]}     # the past report is not "upcoming"
    assert len(out) == 5


def test_calendar_adapter_turns_nan_into_none_and_refuses_rows_it_cannot_key():
    (o, _) = fs.observations_from_earnings_calendar([_cal("AAPL", date(2026, 10, 30), float("nan"))], date(2026, 10, 4))
    assert o.value["eps_estimate"] is None
    for bad in (_cal("", date(2026, 10, 30), 1.0), _cal("AAPL", None, 1.0), _cal("AAPL", datetime(2026, 10, 30), 1.0)):
        with pytest.raises(fs.FirstSeenError):
            fs.observations_from_earnings_calendar([bad], date(2026, 10, 4))


def test_a_moved_report_date_is_a_changed_upcoming_list_and_so_a_new_chain_link():
    before = fs.observations_from_earnings_calendar([_cal("AAPL", date(2026, 10, 30), 1.0)], date(2026, 10, 4))
    after = fs.observations_from_earnings_calendar([_cal("AAPL", date(2026, 11, 5), 1.0)], date(2026, 10, 5))
    up_b = next(o for o in before if o.dataset == "earnings_upcoming_dates")
    up_a = next(o for o in after if o.dataset == "earnings_upcoming_dates")
    assert up_b.key == up_a.key and up_b.hash != up_a.hash


# ---------------------------------------------------------------- derived values are read-time only
def test_surprise_is_derived_from_what_was_known_at_the_cutoff_not_from_the_providers_stored_field():
    est, act = obs({"eps_estimate": 1.0, "eps_actual": None, "surprise_pct": None}), obs({"eps_estimate": 1.0, "eps_actual": 1.1, "surprise_pct": 99.0})
    r = [row(est, 1, None, T0), row(act, 2, est.hash, T0 + timedelta(days=5))]
    early = fs.surprise_as_known(r, T0 + timedelta(days=1))
    late = fs.surprise_as_known(r, T0 + timedelta(days=6))
    assert early == {"eps_estimate": 1.0, "eps_actual": None, "surprise_pct": None}
    assert late["surprise_pct"] == pytest.approx(10.0)                                    # recomputed, not the stored 99.0
    assert fs.surprise_as_known(r, T0 - timedelta(days=1)) is None
    zero = [row(obs({"eps_estimate": 0.0, "eps_actual": 0.1}), 1, None, T0)]
    assert fs.surprise_as_known(zero, T0)["surprise_pct"] is None                         # no division by zero, no invented number
