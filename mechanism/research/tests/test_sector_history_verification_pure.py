"""Slice 11 (no database): the collector's read-only VERIFICATION of the forward sector history -- what satisfies a session's dependency and what
each failure looks like. The same judgement runs against a real database in `forward_collection/tests/test_sector_history_verify_db.py`."""
from datetime import date, datetime, timedelta, timezone

import pytest

from research.lab import dataset_readiness as READY
from research.lab import sector_history as SH
from research.lab import sector_history_verification as V
from test_sector_history_pure import at, chain, confirm

SRC = "yfinance_info"
SESSION = date(2026, 3, 6)                       # a Friday
GRACE = 1
DEADLINE = datetime(2026, 3, 8, tzinfo=timezone.utc)   # end of the session's UTC day + 1 grace day, as a 00:00 instant
SYMS = [f"S{i:03d}" for i in range(100)]


def act(symbol, state, n=1, when=None, reason=None):
    return {"symbol": symbol, "response_state": state, "failure_reason": reason, "polls": n, "last_attempted_at": when or at(SESSION, 21)}


def ev(universe=SYMS, activity=(), rows=(), **kw):
    return V.evaluate(session=SESSION, grace_days=GRACE, universe=universe, activity=list(activity), history_rows=list(rows), source=SRC,
                      cutoff=DEADLINE, **kw)


def full_refresh(state="sector", only=None):
    return [act(s, state) for s in (only or SYMS)]


def test_the_window_is_the_cadence_derived_gap_before_the_session_up_to_its_decision_deadline():
    lo, hi = V.window(SESSION, GRACE)
    assert lo == datetime(2026, 3, 2, tzinfo=timezone.utc) and hi == DEADLINE
    assert (SESSION - lo.date()).days == V.MAX_REFRESH_GAP_DAYS == 4


def test_the_only_threshold_is_the_existing_readiness_floor_not_a_new_one():
    assert V.MIN_ACCOUNTED_SHARE == READY.MIN_OBSERVED_COVERAGE == 0.90


def test_a_full_refresh_satisfies_the_dependency_whatever_each_symbols_sector_is():
    d = ev(activity=full_refresh())
    assert d["satisfied"] is True and d["reasons"] == [] and d["accounted"] == 100 and d["accounted_share"] == 1.0
    assert d["availability_gates_session"] is False and d["never_polled"] == 0 and d["failed_only"] == 0


def test_no_poll_at_all_is_refresh_absent_the_fundamentals_updater_did_not_run():
    d = ev(activity=[])
    assert d["reasons"] == [V.REFRESH_ABSENT] and d["satisfied"] is False and d["never_polled"] == 100 and d["accounted"] == 0
    assert d["meaning"][V.REFRESH_ABSENT]


def test_only_failed_polls_for_most_of_the_universe_is_vendor_failure_not_refresh_absent():
    d = ev(activity=full_refresh("request_failed"))
    assert d["reasons"] == [V.VENDOR_FAILURE] and d["failed_only"] == 100 and d["accounted"] == 0


def test_a_refresh_that_reached_only_part_of_the_universe_is_partial_refresh():
    d = ev(activity=full_refresh(only=SYMS[:60]))                  # 40 never polled, none failed
    assert d["reasons"] == [V.PARTIAL_REFRESH] and d["never_polled"] == 40 and d["accounted"] == 60


def test_failures_dominating_the_missing_share_read_as_vendor_failure_and_never_polled_dominating_as_partial():
    a = full_refresh(only=SYMS[:50]) + [act(s, "request_failed") for s in SYMS[50:90]]            # 40 failed, 10 never polled
    assert ev(activity=a)["reasons"] == [V.VENDOR_FAILURE]
    b = full_refresh(only=SYMS[:50]) + [act(s, "request_failed") for s in SYMS[50:60]]             # 10 failed, 40 never polled
    assert ev(activity=b)["reasons"] == [V.PARTIAL_REFRESH]


@pytest.mark.parametrize("accounted,ok", [(90, True), (89, False)])
def test_the_ninety_percent_boundary_is_inclusive(accounted, ok):
    d = ev(activity=full_refresh(only=SYMS[:accounted]))
    assert d["satisfied"] is ok and d["accounted"] == accounted


def test_a_legitimate_explicit_no_sector_symbol_is_accounted_so_it_never_fails_the_session_option_b():
    a = [act(s, "sector") for s in SYMS[:80]] + [act(s, "no_sector") for s in SYMS[80:]]          # 20 ETFs: vendor answered "no sector"
    d = ev(activity=a)
    assert d["satisfied"] is True and d["accounted"] == 100


def test_a_responded_but_unusable_answer_proves_the_vendor_answered_so_it_is_accounted():
    d = ev(activity=[act(s, "invalid_response") for s in SYMS])
    assert d["satisfied"] is True and d["failed_only"] == 0


def test_a_symbol_with_both_a_failed_and_a_good_poll_in_the_window_is_accounted():
    d = ev(activity=full_refresh() + [act("S000", "request_failed", 3)])
    assert d["failed_only"] == 0 and d["accounted"] == 100


def test_stale_or_missing_per_symbol_history_never_gates_it_is_only_reported():
    d = ev(activity=full_refresh())                                # polls, but no observation rows read at all
    assert d["satisfied"] is True and d["history_by_kind"] == {SH.K_NO_HISTORY: 100} and d["observed_by_evidence_state"] == {}


def test_observed_sectors_are_reported_by_their_evidence_state_and_explicit_no_sector_by_kind():
    rows = []
    for s in SYMS[:3]:
        c = chain(("Technology", at(date(2026, 3, 3))), symbol=s)
        rows += c
    c = chain((None, at(date(2026, 3, 3))), symbol="S010")
    d = ev(activity=full_refresh(), rows=rows + c)
    assert d["history_by_kind"][SH.K_OBSERVED] == 3 and d["history_by_kind"][SH.K_EXPLICIT_NO_SECTOR] == 1
    assert sum(d["observed_by_evidence_state"].values()) == 3


def test_a_same_value_confirmation_poll_inside_the_window_keeps_a_head_current_in_the_report():
    head = chain(("Technology", at(date(2026, 1, 5))), symbol="S000")[0]
    stale = ev(activity=full_refresh(), rows=[head])
    fresh = ev(activity=full_refresh(), rows=[head, confirm(head, at(date(2026, 3, 5)))])
    assert stale["observed_by_evidence_state"] != fresh["observed_by_evidence_state"]
    assert fresh["observed_by_evidence_state"] == {"observed_fresh": 1}


def test_a_broken_chain_is_reported_even_when_the_refresh_itself_was_complete():
    c = chain(("Technology", at(date(2026, 3, 2))), ("Energy", at(date(2026, 3, 3))), symbol="S000")
    c[1] = {**c[1], "sector": "Utilities"}                         # rewritten after the fact: the hash no longer matches
    d = ev(activity=full_refresh(), rows=c)
    assert d["reasons"] == [V.CHAIN_BROKEN] and d["chain_broken_symbols"] == ["S000"] and d["chain_broken_count"] == 1
    assert V.CHAIN_BROKEN in V.PERMANENT_ALWAYS


def test_an_incomplete_refresh_and_a_broken_chain_are_both_reported_not_merged():
    c = chain(("Technology", at(date(2026, 3, 2))), ("Energy", at(date(2026, 3, 3))), symbol="S000")
    c[1] = {**c[1], "sector": "Utilities"}
    d = ev(activity=[], rows=c)
    assert d["reasons"] == [V.REFRESH_ABSENT, V.CHAIN_BROKEN] and set(d["meaning"]) == set(d["reasons"])


def test_a_session_without_bars_has_no_universe_to_verify():
    d = ev(universe=[], activity=[])
    assert d["reasons"] == [V.NO_UNIVERSE] and d["universe"] == 0 and d["accounted_share"] == 0.0


def test_the_document_is_deterministic_and_contains_no_symbol_beyond_the_bounded_examples():
    a = ev(activity=full_refresh(only=SYMS[:10]))
    b = ev(activity=list(reversed(full_refresh(only=SYMS[:10]))))
    assert a == b and len(a["never_polled_examples"]) == V.EXAMPLES == 5


def test_polls_are_counted_and_a_symbol_duplicated_in_the_universe_is_counted_once():
    d = ev(universe=SYMS + SYMS[:5], activity=[act(s, "sector", 2) for s in SYMS])
    assert d["universe"] == 100 and d["polls_in_window"] == 200


def test_every_reason_code_has_a_meaning_and_only_chain_broken_is_permanent_before_the_deadline():
    assert set(V.MEANING) == set(V.REASONS) and V.PERMANENT_ALWAYS == (V.CHAIN_BROKEN,)


SPARSE = ("invalid_response", "response_too_sparse")


def test_a_vendor_answering_near_empty_bodies_for_everything_is_a_vendor_failure_not_a_healthy_refresh():
    d = ev(activity=[act(s, *SPARSE[:1], reason=SPARSE[1]) for s in SYMS])
    assert d["reasons"] == [V.VENDOR_FAILURE] and d["accounted"] == 0 and d["symbols_with_a_sparse_response"] == 100


def test_a_handful_of_unknown_symbols_with_sparse_bodies_is_absorbed_by_the_existing_tolerance():
    a = [act(s, "sector") for s in SYMS[:97]] + [act(s, SPARSE[0], reason=SPARSE[1]) for s in SYMS[97:]]
    d = ev(activity=a)
    assert d["satisfied"] is True and d["accounted"] == 97 and d["symbols_with_a_sparse_response"] == 3


def test_responded_but_unusable_answers_with_a_real_body_still_count_as_accounted_option_b():
    a = ([act(s, "sector") for s in SYMS[:70]] + [act(s, "no_sector") for s in SYMS[70:80]]
         + [act(s, "invalid_response", reason="operating_company_sector_absent") for s in SYMS[80:]])
    d = ev(activity=a)
    assert d["satisfied"] is True and d["accounted"] == 100 and d["symbols_with_a_sparse_response"] == 0


def test_a_symbol_with_one_sparse_poll_and_one_real_answer_in_the_window_is_accounted():
    a = [act(s, "sector") for s in SYMS] + [act(SYMS[0], SPARSE[0], reason=SPARSE[1])]
    assert ev(activity=a)["accounted"] == 100
