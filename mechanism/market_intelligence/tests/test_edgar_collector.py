"""EDGAR collector (dry run): verbatim facts, explicit UTC acceptance time, CIK identity, rejects instead of repairs, no writes, rate limiting."""
from datetime import date, datetime, timezone

import pytest

import mi_samples  # noqa: F401  (path setup)
from edgar_samples import CIK_A, CIK_B, NOW, UA, doc, r
from market_intelligence import edgar_collector as E
from market_intelligence import filing_contract as F

ACC1, ACC2, ACC3 = "0000320193-26-000101", "0000320193-26-000102", "0000320193-26-000103"


class Clock:
    def __init__(self):
        self.t, self.slept = 0.0, []

    def now(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


def run(docs, ciks, **kw):
    c = Clock()
    tr = E.FixtureTransport({E.submissions_url(k): d for k, d in docs.items()})
    rep = E.collect(E.EdgarConfig(UA), tr, ciks, now=NOW, clock=c.now, sleep=c.sleep, **kw)
    return rep, tr, c


# ---------------------------------------------------------------- configuration
@pytest.mark.parametrize("ua", ["", "ops@first-light.finance", "First Light", "First Light ops@example.com", "Your Name you@x.io",
                                "First Light noreply@first-light.finance", "<name> <a@b.co>", "First Light test@first-light.finance"])
def test_a_missing_or_placeholder_user_agent_is_refused(ua):
    with pytest.raises(E.EdgarError):
        E.EdgarConfig(ua)


def test_rate_must_respect_the_sec_limit_and_urls_are_canonical():
    with pytest.raises(E.EdgarError):
        E.EdgarConfig(UA, max_rps=11)
    assert E.submissions_url(320193) == "https://data.sec.gov/submissions/CIK0000320193.json"
    for bad in (0, -1, True, "320193"):
        with pytest.raises(E.EdgarError):
            E.submissions_url(bad)


# ---------------------------------------------------------------- facts are verbatim
def test_a_record_becomes_the_exact_fact_the_source_stated():
    rep, tr, _ = run({CIK_A: doc(CIK_A, [r(ACC1, period="2026-06-27")])}, [CIK_A])
    (cf,) = rep.facts
    f = cf.fact
    assert (f.accession, f.cik, f.form_type, f.filing_date, f.items, f.report_period, f.primary_document) == \
        (ACC1, CIK_A, "8-K", date(2026, 9, 18), ("2.02", "9.01"), date(2026, 6, 27), "a.htm")
    assert f.accepted_at == datetime(2026, 9, 18, 20, 5, 11, tzinfo=timezone.utc)                    # the SEC's own instant, not a date
    assert cf.draft.event_key == f"sec|{ACC1}" and cf.draft.payload["fact_hash"] == f.content_hash() and cf.draft.known_at_basis == "vendor_published"
    assert cf.symbol is None and cf.symbol_basis == "unmapped" and f.amends_accession is None
    assert rep.complete and rep.dry_run and rep.would_write == {"market_event": 1, "market_event_revision": 1, "catalyst_classification": 0}
    assert tr.calls[0][1]["User-Agent"] == UA


def test_the_source_record_hash_is_of_the_record_as_received():
    rep, _, _ = run({CIK_A: doc(CIK_A, [r(ACC1)])}, [CIK_A])
    assert rep.facts[0].source_record_hash == E.raw_hash(r(ACC1) | {})


def test_unlisted_forms_are_skipped_not_rejected_and_a_form_filter_can_widen_it():
    d = doc(CIK_A, [r(ACC1), r(ACC2, form="NT 10-Q")])
    rep, _, _ = run({CIK_A: d}, [CIK_A])
    assert len(rep.facts) == 1 and rep.counters["skipped_form"] == 1 and rep.rejected == []
    rep2, _, _ = run({CIK_A: d}, [CIK_A], forms=frozenset({"8-K", "NT 10-Q"}))
    assert {f.fact.form_type for f in rep2.facts} == {"8-K", "NT 10-Q"}


# ---------------------------------------------------------------- reject, never repair
@pytest.mark.parametrize("accepted", ["", None, "2026-09-18", "2026-09-18T20:05:11", "2026-09-18T20:05:11+00:00", "2026-09-18 20:05:11Z"])
def test_an_acceptance_time_that_is_not_an_explicit_utc_instant_is_rejected_not_assumed(accepted):
    rep, _, _ = run({CIK_A: doc(CIK_A, [r(ACC1, accepted=accepted)])}, [CIK_A])
    assert rep.facts == [] and len(rep.rejected) == 1 and "UTC instant" in rep.rejected[0].reason and rep.complete


@pytest.mark.parametrize("row", [r("bad-accession"), r(ACC1, filed=""), r(ACC1, filed="2026-13-45"), r(ACC1, items="2.02,x"),
                                 r(ACC1, accepted="2026-10-04T12:30:00.000Z")])
def test_malformed_records_are_rejected_with_a_reason_and_the_run_continues(row):
    rep, _, _ = run({CIK_A: doc(CIK_A, [row, r(ACC2)])}, [CIK_A])
    assert [f.fact.accession for f in rep.facts] == [ACC2] and len(rep.rejected) == 1 and rep.rejected[0].reason


def test_duplicates_collapse_when_identical_and_are_rejected_when_they_disagree():
    rep, _, _ = run({CIK_A: doc(CIK_A, [r(ACC1), r(ACC1)])}, [CIK_A])
    assert len(rep.facts) == 1 and rep.counters["duplicate_identical"] == 1
    rep2, _, _ = run({CIK_A: doc(CIK_A, [r(ACC1), r(ACC1, items="5.02")])}, [CIK_A])
    assert len(rep2.facts) == 1 and "twice with different content" in rep2.rejected[0].reason


def test_known_accessions_and_since_are_skipped_without_inventing_anything():
    d = doc(CIK_A, [r(ACC1), r(ACC2, filed="2026-08-01", accepted="2026-08-01T20:00:00.000Z"), r(ACC3)])
    rep, _, _ = run({CIK_A: d}, [CIK_A], known_accessions=frozenset({ACC1}), since=date(2026, 9, 1))
    assert [f.fact.accession for f in rep.facts] == [ACC3] and rep.counters["already_known"] == 1 and rep.counters["skipped_before_since"] == 1


# ---------------------------------------------------------------- identity
def test_a_document_for_another_cik_is_refused_as_a_whole_and_other_ciks_still_run():
    wrong = doc(CIK_B, [r(ACC1)])
    rep, _, _ = run({CIK_A: wrong, CIK_B: doc(CIK_B, [r("0000789019-26-000001")])}, [CIK_A, CIK_B])
    assert rep.facts[0].fact.cik == CIK_B and CIK_A in rep.fetch_errors and "refused as a whole" in rep.fetch_errors[CIK_A]
    assert rep.complete is False


def test_a_symbol_comes_only_from_supplied_links_and_carries_its_basis():
    links = [F.IdentifierLink(CIK_A, "AAPL", None, None, "reconstructed")]
    rep, _, _ = run({CIK_A: doc(CIK_A, [r(ACC1)])}, [CIK_A], links=links)
    cf = rep.facts[0]
    assert (cf.symbol, cf.symbol_basis, cf.draft.symbol) == ("AAPL", "reconstructed", "AAPL") and cf.fact.cik == CIK_A


# ---------------------------------------------------------------- classification stays outside the fact
def test_classification_pins_the_fact_and_leaves_it_unchanged():
    rep, _, _ = run({CIK_A: doc(CIK_A, [r(ACC1), r(ACC2, items="5.02")])}, [CIK_A])
    plain = [f.fact.content_hash() for f in rep.facts]
    rep2, _, _ = run({CIK_A: doc(CIK_A, [r(ACC1), r(ACC2, items="5.02")])}, [CIK_A], classify=True)
    assert [f.fact.content_hash() for f in rep2.facts] == plain                                   # classifying changes no fact
    c = rep2.facts[0].classification
    assert c.label == "earnings_release" and c.matches(rep2.facts[0].fact) and rep2.facts[1].classification is None
    assert rep2.would_write["catalyst_classification"] == 1


# ---------------------------------------------------------------- partial runs are visible
def test_a_transport_failure_is_recorded_and_the_run_is_marked_incomplete():
    rep, _, _ = run({CIK_A: doc(CIK_A, [r(ACC1)])}, [CIK_A, CIK_B])                              # CIK_B returns 404
    assert len(rep.facts) == 1 and not rep.complete and "LookupError" in rep.fetch_errors[CIK_B]
    assert rep.summary()["complete"] is False and rep.summary()["dry_run"] is True


def test_a_document_without_recent_filings_is_an_error_not_an_empty_success():
    rep, _, _ = run({CIK_A: {"cik": f"{CIK_A:010d}", "filings": {}}}, [CIK_A])
    assert not rep.complete and "filings.recent" in rep.fetch_errors[CIK_A]


def test_older_pages_are_counted_not_read():
    rep, _, _ = run({CIK_A: doc(CIK_A, [r(ACC1)], files=[{"name": "CIK0000320193-submissions-001.json"}])}, [CIK_A])
    assert rep.counters["older_pages_not_read"] == 1


# ---------------------------------------------------------------- politeness
def test_requests_are_spaced_by_the_injected_clock_and_duplicate_ciks_are_fetched_once():
    ciks = [CIK_A, CIK_B, CIK_A]
    docs = {CIK_A: doc(CIK_A, [r(ACC1)]), CIK_B: doc(CIK_B, [r("0000789019-26-000001")])}
    rep, tr, clock = run(docs, ciks)
    assert len(tr.calls) == 2 and rep.ciks_requested == (CIK_A, CIK_B)
    assert clock.slept == [pytest.approx(0.2)] and rep.throttle_waits == 1


def test_the_collector_has_no_write_path():
    import inspect
    src = inspect.getsource(E)
    assert "INSERT" not in src and "psycopg2" not in src and "open(" not in src
