"""Acceptance-time reconciliation (pure): the SEC header record is Eastern wall clock; the submissions feed was found 4.00 h later than it for 66% of a real sample, never earlier.
Equal -> kept; feed later by 0 < d <= 6 h -> the header time is used and both are recorded; anything else is refused."""
from datetime import date, datetime, timedelta, timezone

import pytest

import mi_samples  # noqa: F401  (path setup)
from edgar_samples import CIK_A, NOW, doc, r
from market_intelligence import acceptance_check as A
from market_intelligence import catalyst_rules as R
from market_intelligence import edgar_collector as E

UTC = timezone.utc
ACC = "0000320193-26-000101"


def collected(accepted="2026-10-15T20:47:20.000Z", filed="2026-10-15", form="8-K", items="2.02"):
    return E.parse_submissions(doc(CIK_A, [r(ACC, form=form, items=items, filed=filed, accepted=accepted)]), CIK_A, now=NOW.replace(year=2027), forms=R.PHASE1A_FORMS)[0][0]


def header(digits):
    return f"<SEC-HEADER>0000320193-26-000101.hdr.sgml : 20261015\n<ACCEPTANCE-DATETIME>{digits}\nACCESSION NUMBER:\t0000320193-26-000101\n"


def test_the_header_is_eastern_wall_clock_and_daylight_saving_is_applied():
    assert A.parse_header_acceptance(header("20261015164720")) == datetime(2026, 10, 15, 20, 47, 20, tzinfo=UTC)           # EDT = UTC-4
    assert A.parse_header_acceptance(header("20260115164720")) == datetime(2026, 1, 15, 21, 47, 20, tzinfo=UTC)           # EST = UTC-5


@pytest.mark.parametrize("text", ["", "no header", "<ACCEPTANCE-DATETIME>2026", "<ACCEPTANCE-DATETIME>20261340250000", "<ACCEPTANCE-DATETIME>20260230100000"])
def test_a_missing_or_malformed_header_is_refused(text):
    with pytest.raises(A.AcceptanceError):
        A.parse_header_acceptance(text)


def test_wall_times_that_do_not_exist_or_occur_twice_are_refused():
    with pytest.raises(A.AcceptanceError, match="ambiguous or non-existent"):
        A.parse_header_acceptance(header("20261101013000"))                      # the repeated hour when daylight saving ends
    with pytest.raises(A.AcceptanceError, match="ambiguous or non-existent"):
        A.parse_header_acceptance(header("20260308023000"))                      # the skipped hour when it starts


def test_when_the_two_records_agree_the_fact_is_unchanged_and_the_audit_says_so():
    c = collected("2026-10-15T20:47:20.000Z")
    out = A.reconcile(c, datetime(2026, 10, 15, 20, 47, 20, tzinfo=UTC))
    assert out.fact == c.fact and out.fact.content_hash() == c.fact.content_hash()
    a = out.draft.payload["acceptance"]
    assert a["source"] == "agree" and a["used"] == "feed" and a["feed_minus_header_hours"] == 0


def test_a_feed_that_is_four_hours_late_is_replaced_by_the_header_instant_and_both_are_recorded():
    c = collected("2026-10-16T00:47:20.000Z")                                      # what the feed said
    out = A.reconcile(c, datetime(2026, 10, 15, 20, 47, 20, tzinfo=UTC))           # what the SEC header says
    assert out.fact.accepted_at == datetime(2026, 10, 15, 20, 47, 20, tzinfo=UTC) and out.fact.content_hash() != c.fact.content_hash()
    assert out.draft.published_at == out.fact.accepted_at and out.draft.event_time == date(2026, 10, 15) and out.draft.session_timing == "AMC"
    assert out.draft.payload["fact_hash"] == out.fact.content_hash()
    a = out.draft.payload["acceptance"]
    assert (a["source"], a["used"], a["feed"], a["header"], a["feed_minus_header_hours"]) == ("feed_late", "header", "2026-10-16T00:47:20+00:00", "2026-10-15T20:47:20+00:00", 4.0)
    assert out.fact.accession == c.fact.accession and out.fact.items == c.fact.items and out.fact.form_type == c.fact.form_type


def test_the_four_hour_shift_turned_a_before_open_filing_into_an_intraday_one_and_the_header_restores_it():
    c = collected("2026-10-15T15:02:04.000Z")                                      # feed: 11:02 ET -> INTRADAY
    assert c.draft.session_timing == "INTRADAY"
    out = A.reconcile(c, datetime(2026, 10, 15, 11, 2, 4, tzinfo=UTC))             # header: 07:02:04 ET -> BMO
    assert out.draft.session_timing == "BMO" and out.draft.payload["acceptance"]["feed_minus_header_hours"] == 4.0


@pytest.mark.parametrize("header_utc", [datetime(2026, 10, 15, 20, 47, 21, tzinfo=UTC),                     # header LATER than the feed: the feed claims an earlier instant than the record -> refuse
                                        datetime(2026, 10, 15, 12, 0, 0, tzinfo=UTC),                       # feed later by 12.8 h: outside the documented range
                                        datetime(2026, 10, 14, 20, 47, 20, tzinfo=UTC)])                    # a whole day apart
def test_an_unexplained_disagreement_is_refused_not_repaired(header_utc):
    with pytest.raises(A.AcceptanceError, match="differ by"):
        A.reconcile(collected("2026-10-15T20:47:20.000Z"), header_utc)


@pytest.mark.parametrize("header,feed_extra_h,ok", [
    (datetime(2026, 10, 15, 20, 47, 20, tzinfo=UTC), 4, True),            # daylight time: the Eastern offset is 4 h
    (datetime(2026, 10, 15, 20, 47, 20, tzinfo=UTC), 5, False),           # 5 h in daylight time is NOT explained
    (datetime(2026, 10, 15, 20, 47, 20, tzinfo=UTC), 3, False),
    (datetime(2026, 1, 15, 21, 47, 20, tzinfo=UTC), 5, True),             # standard time: 5 h
    (datetime(2026, 1, 15, 21, 47, 20, tzinfo=UTC), 4, False),            # 4 h in standard time is NOT explained
    (datetime(2026, 3, 8, 6, 0, 0, tzinfo=UTC), 5, True),                 # 01:00 EST on the day daylight time starts (02:00 -> 03:00): still standard time
    (datetime(2026, 3, 8, 6, 0, 0, tzinfo=UTC), 4, False),
    (datetime(2026, 3, 8, 11, 0, 0, tzinfo=UTC), 4, True),                # 07:00 EDT the same day
    (datetime(2026, 3, 8, 11, 0, 0, tzinfo=UTC), 5, False),
    (datetime(2026, 11, 1, 5, 30, 0, tzinfo=UTC), 4, True),               # 01:30 EDT, before the repeated hour on the day daylight time ends
    (datetime(2026, 11, 1, 12, 0, 0, tzinfo=UTC), 5, True),               # 07:00 EST the same day
    (datetime(2026, 11, 1, 12, 0, 0, tzinfo=UTC), 4, False),
])
def test_only_the_eastern_offset_in_force_at_the_acceptance_instant_is_an_explained_gap_across_the_dst_changes(header, feed_extra_h, ok):
    feed = header + timedelta(hours=feed_extra_h)
    c = collected(feed.strftime("%Y-%m-%dT%H:%M:%S.000Z"), filed=header.date().isoformat())
    if ok:
        out = A.reconcile(c, header)
        assert out.fact.accepted_at == header and out.draft.payload["acceptance"]["feed_minus_header_hours"] == float(feed_extra_h)
    else:
        with pytest.raises(A.AcceptanceError, match="only 0 or exactly the Eastern UTC offset"):
            A.reconcile(c, header)


def test_eastern_offset_is_four_hours_in_daylight_time_and_five_in_standard_time():
    assert A.eastern_offset(datetime(2026, 7, 1, 12, tzinfo=UTC)) == timedelta(hours=4) and A.eastern_offset(datetime(2026, 12, 1, 12, tzinfo=UTC)) == timedelta(hours=5)
    assert A.eastern_offset(datetime(2026, 3, 8, 6, 59, 59, tzinfo=UTC)) == timedelta(hours=5) and A.eastern_offset(datetime(2026, 3, 8, 7, 0, 0, tzinfo=UTC)) == timedelta(hours=4)


def test_a_naive_header_time_is_refused():
    with pytest.raises(A.AcceptanceError):
        A.reconcile(collected(), datetime(2026, 10, 15, 20, 47, 20))


def test_the_header_url_is_the_filings_own_archive_path():
    assert A.header_url(320193, ACC) == "https://www.sec.gov/Archives/edgar/data/320193/000032019326000101/0000320193-26-000101.hdr.sgml"


def test_a_fresh_filing_with_a_late_feed_value_reaches_the_reconciliation_instead_of_being_refused_as_future():
    """Found while the corrected dry run saw no fresh filings: the collector rejected a feed value 4 h ahead of its clock before the header could correct it."""
    now = datetime(2026, 10, 16, 12, 30, tzinfo=UTC)                         # header: accepted 12:25 UTC; the feed says 16:25
    rows = [r(ACC, filed="2026-10-16", accepted="2026-10-16T16:25:00.000Z")]
    plain = E.parse_submissions(doc(CIK_A, rows), CIK_A, now=now, forms=R.PHASE1A_FORMS)
    assert plain[0] == [] and "future" in plain[1][0].reason                  # without the allowance the filing is refused and waits ~4 h
    facts = E.parse_submissions(doc(CIK_A, rows), CIK_A, now=A.collector_now(now), forms=R.PHASE1A_FORMS)[0]
    assert len(facts) == 1
    out = A.reconcile(facts[0], datetime(2026, 10, 16, 12, 25, tzinfo=UTC))
    assert out.fact.accepted_at == datetime(2026, 10, 16, 12, 25, tzinfo=UTC) and out.fact.accepted_at <= now


def test_a_far_future_feed_value_still_cannot_get_through_even_with_the_allowance():
    now = datetime(2026, 10, 16, 12, 30, tzinfo=UTC)
    facts = E.parse_submissions(doc(CIK_A, [r(ACC, filed="2026-10-16", accepted="2026-10-17T12:25:00.000Z")]), CIK_A, now=A.collector_now(now), forms=R.PHASE1A_FORMS)
    assert facts[0] == [] and "future" in facts[1][0].reason                  # a day ahead is beyond the tolerance and is refused by the collector itself
    with pytest.raises(A.AcceptanceError):
        A.collector_now(datetime(2026, 10, 16, 12, 30))
