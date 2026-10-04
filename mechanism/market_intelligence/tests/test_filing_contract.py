"""SEC EDGAR filing-event contract: fact vs interpretation, PIT time, identity, amendments. Pure -- no database, no network, no vendor client."""
from datetime import date, datetime, timedelta, timezone

import pytest

import mi_samples  # noqa: F401  (path setup)
from market_intelligence import events as E
from market_intelligence import filing_contract as F

UTC = timezone.utc
ACC = "0001234567-26-000123"
ACC2 = "0001234567-26-000456"


def raw(**kw):
    base = dict(accession=ACC, cik=1234567, form_type="8-K", filing_date=date(2026, 10, 20),
                accepted_at=datetime(2026, 10, 20, 20, 5, 11, tzinfo=UTC), items="2.02,9.01",
                primary_document="a8k.htm")
    base.update(kw)
    return base


def fact(**kw):
    return F.build_fact(raw(**kw))


# ---------------------------------------------------------------- facts are copied, never invented
@pytest.mark.parametrize("missing", ["accession", "cik", "form_type", "filing_date", "accepted_at"])
def test_a_partial_source_record_is_refused_not_completed(missing):
    r = raw()
    r.pop(missing)
    with pytest.raises(F.FilingContractError, match=missing):
        F.build_fact(r)


def test_the_acceptance_time_must_be_timezone_aware_and_is_never_defaulted():
    with pytest.raises(F.FilingContractError, match="timezone-aware"):
        fact(accepted_at=datetime(2026, 10, 20, 16, 5))
    with pytest.raises(F.FilingContractError):
        fact(accepted_at="2026-10-20T16:05:00Z")


def test_acceptance_time_is_stored_as_utc_whatever_zone_the_source_used():
    ny = datetime(2026, 10, 20, 16, 5, 11, tzinfo=F.NY)
    assert fact(accepted_at=ny).accepted_at == datetime(2026, 10, 20, 20, 5, 11, tzinfo=UTC)


@pytest.mark.parametrize("bad", ["123", "0001234567-26-12", "0001234567/26/000123", "", "000123456726000123"])
def test_a_malformed_accession_is_refused(bad):
    with pytest.raises(F.FilingContractError):
        fact(accession=bad)


@pytest.mark.parametrize("bad", [0, -5, True, "12", 1.5])
def test_cik_must_be_a_positive_integer(bad):
    with pytest.raises(F.FilingContractError):
        F.FilingFact(accession=ACC, cik=bad, form_type="8-K", filing_date=date(2026, 10, 20),
                     accepted_at=datetime(2026, 10, 20, 20, 0, tzinfo=UTC))


def test_items_are_kept_verbatim_and_a_bad_item_is_refused():
    assert fact(items="2.02, 9.01").items == ("2.02", "9.01")
    assert fact(items="").items == ()
    with pytest.raises(F.FilingContractError, match="item"):
        fact(items="2.02,earnings")


def test_an_unlisted_form_is_a_valid_fact_with_no_family():
    f = fact(form_type="NT 10-K")
    assert f.form_type == "NT 10-K" and f.form_family is None
    assert fact().form_family == "current_report"


def test_the_fact_is_immutable_and_its_hash_is_stable():
    f = fact()
    with pytest.raises(Exception):
        f.form_type = "10-K"
    assert fact().content_hash() == f.content_hash()
    assert fact(items="2.02").content_hash() != f.content_hash()
    assert fact(accepted_at=f.accepted_at + timedelta(seconds=1)).content_hash() != f.content_hash()


def test_only_sec_edgar_facts_are_describable():
    with pytest.raises(F.FilingContractError):
        F.FilingFact(accession=ACC, cik=1, form_type="8-K", filing_date=date(2026, 10, 20),
                     accepted_at=datetime(2026, 10, 20, 20, 0, tzinfo=UTC), source="vendor_x")


# ---------------------------------------------------------------- amendments are new facts, not edits
def test_an_amendment_is_a_new_accession_that_points_at_the_original():
    a = fact(accession=ACC2, form_type="8-K/A", amends_accession=ACC)
    assert a.amends_accession == ACC and a.accession != ACC
    assert a.content_hash() != fact().content_hash()


def test_amendment_rules():
    with pytest.raises(F.FilingContractError, match="amendment form"):
        fact(accession=ACC2, form_type="8-K", amends_accession=ACC)
    with pytest.raises(F.FilingContractError, match="itself"):
        fact(form_type="8-K/A", amends_accession=ACC)
    with pytest.raises(F.FilingContractError, match="malformed"):
        fact(accession=ACC2, form_type="8-K/A", amends_accession="x")


# ---------------------------------------------------------------- identifier is outside the fact
def link(**kw):
    base = dict(cik=1234567, symbol="AAA", valid_from=date(2020, 1, 1), valid_to=None, basis="observed")
    base.update(kw)
    return F.IdentifierLink(**base)


def test_the_fact_asserts_a_cik_and_no_symbol():
    assert "symbol" not in fact().content()


def test_symbol_resolution_reports_unmapped_ambiguous_and_the_basis():
    f = fact()
    assert F.resolve_symbol(f, []) == (None, "unmapped")
    assert F.resolve_symbol(f, [link()]) == ("AAA", "observed")
    assert F.resolve_symbol(f, [link(basis="reconstructed")]) == ("AAA", "reconstructed")
    assert F.resolve_symbol(f, [link(), link(basis="reconstructed")]) == ("AAA", "reconstructed")
    assert F.resolve_symbol(f, [link(), link(symbol="BBB")]) == (None, "ambiguous")
    assert F.resolve_symbol(f, [link(valid_to=date(2026, 1, 1))]) == (None, "unmapped")
    assert F.resolve_symbol(f, [link(cik=999)]) == (None, "unmapped")


def test_a_symbol_reused_by_another_company_resolves_by_the_filing_date():
    links = [link(symbol="AAA", valid_to=date(2024, 12, 31)), link(cik=777, symbol="AAA", valid_from=date(2025, 1, 1))]
    assert F.resolve_symbol(fact(cik=777), links) == ("AAA", "observed")
    assert F.resolve_symbol(fact(), links) == (None, "unmapped")


def test_a_link_cannot_claim_a_basis_it_does_not_have():
    with pytest.raises(F.FilingContractError):
        link(basis="vendor")
    with pytest.raises(F.FilingContractError):
        link(valid_from=date(2026, 2, 1), valid_to=date(2026, 1, 1))


# ---------------------------------------------------------------- timestamp -> timing, derived and never rolled forward
@pytest.mark.parametrize("ny_hm,expected", [((9, 29), "BMO"), ((7, 0), "BMO"), ((9, 30), "INTRADAY"), ((15, 59), "INTRADAY"),
                                             ((16, 0), "AMC"), ((20, 15), "AMC")])
def test_filing_session_timing_buckets(ny_hm, expected):
    ts = datetime(2026, 10, 20, *ny_hm, tzinfo=F.NY)
    assert F.filing_session_timing(ts) == expected


def test_a_weekend_or_a_day_outside_the_calendar_is_unknown_not_rolled_forward():
    assert F.filing_session_timing(datetime(2026, 10, 17, 10, 0, tzinfo=F.NY)) == "UNKNOWN"      # Saturday
    ts = datetime(2026, 10, 20, 10, 0, tzinfo=F.NY)
    assert F.filing_session_timing(ts, frozenset({date(2026, 10, 21)})) == "UNKNOWN"
    assert F.filing_session_timing(ts, frozenset({date(2026, 10, 20)})) == "INTRADAY"


def test_timing_follows_new_york_dst_not_a_fixed_utc_offset():
    summer = datetime(2026, 7, 15, 13, 45, tzinfo=UTC)       # 09:45 EDT
    winter = datetime(2026, 12, 15, 13, 45, tzinfo=UTC)      # 08:45 EST
    assert F.filing_session_timing(summer) == "INTRADAY"
    assert F.filing_session_timing(winter) == "BMO"


# ---------------------------------------------------------------- the fact is a valid, PIT-graded migration-25 event
def test_a_fact_maps_to_a_valid_event_draft_with_the_sec_time_as_known_at():
    f = fact()
    d = F.to_event_draft(f, "AAA", "observed")
    E.validate(d, declared_basis="vendor_published")
    assert (d.event_type, d.status, d.known_at_basis, d.pit_grade, d.provenance) == ("regulatory", "reported", "vendor_published", "A", "observed")
    assert d.published_at == f.accepted_at and d.source == "sec_edgar" and d.source_ref == ACC
    assert d.event_key == f"sec|{ACC}" and d.session_timing == "AMC"
    assert d.event_time == date(2026, 10, 20)
    assert d.payload["fact_hash"] == f.content_hash() and d.payload["items"] == ["2.02", "9.01"]


def test_a_fact_asserts_no_meaning_and_no_financial_value():
    d = F.to_event_draft(fact(), None)
    assert d.event_type == "regulatory"
    assert (d.eps_actual, d.revenue_actual, d.eps_estimate, d.revenue_estimate) == (None, None, None, None)
    assert d.symbol is None and d.payload["symbol_basis"] == "unmapped"


def test_the_event_date_is_the_new_york_date_not_the_utc_date():
    late = fact(accepted_at=datetime(2026, 10, 21, 1, 30, tzinfo=UTC))        # 21:30 EDT on the 20th
    assert F.to_event_draft(late).event_time == date(2026, 10, 20)


def test_the_draft_is_ml_eligible_only_through_the_existing_gate():
    d = F.to_event_draft(fact(), "AAA", "observed")
    row = {**d.content(), "known_at": d.published_at, "known_at_basis": d.known_at_basis, "pit_grade": d.pit_grade,
           "provenance": d.provenance, "revision": 1}
    E.assert_ml_eligible(row)
    assert E.ml_view([row], d.published_at - timedelta(seconds=1)).rows == []
    assert E.ml_view([row], d.published_at).rows == [row]


# ---------------------------------------------------------------- interpretation is separate, versioned and cannot restate a fact
def test_a_rule_classification_reads_only_the_facts_own_items():
    c = F.classify_by_items(fact())
    assert (c.label, c.method, c.classifier, c.classifier_version) == ("earnings_release", "rule", "items_rule", "v1")
    assert c.matches(fact()) and c.confidence is None
    assert F.classify_by_items(fact(items="5.02")) is None
    assert F.classify_by_items(fact(form_type="10-Q", items="")) is None


def test_a_classification_goes_stale_when_the_fact_it_read_changes():
    c = F.classify_by_items(fact())
    assert not c.matches(fact(items="2.02"))                                   # different content, same accession
    assert not c.matches(fact(accession=ACC2))


def test_a_classification_has_no_field_to_rewrite_a_fact():
    names = set(F.CatalystClassification.__dataclass_fields__)
    assert not names & {"accepted_at", "filing_date", "cik", "form_type", "items", "accession", "symbol", "eps_actual", "revenue_actual"}


def test_a_classification_cannot_smuggle_a_fact_through_evidence():
    h = fact().content_hash()
    base = dict(fact_accession=ACC, fact_hash=h, classifier="m", classifier_version="1", method="model", label="guidance_cut")
    F.CatalystClassification(**base, evidence={"items": ["2.02"], "form_type": "8-K"}, confidence=0.7)
    for key in ("accepted_at", "known_at", "cik", "eps_actual", "revenue_actual", "symbol", "filing_date"):
        with pytest.raises(F.FilingContractError, match="restate"):
            F.CatalystClassification(**base, evidence={key: "x"}, confidence=0.7)


def test_classification_validation():
    h = fact().content_hash()
    base = dict(fact_accession=ACC, fact_hash=h, classifier="m", classifier_version="1", method="model", label="x")
    with pytest.raises(F.FilingContractError):
        F.CatalystClassification(**{**base, "method": "guess"})
    with pytest.raises(F.FilingContractError):
        F.CatalystClassification(**{**base, "fact_hash": "abc"})
    with pytest.raises(F.FilingContractError):
        F.CatalystClassification(**{**base, "classifier_version": ""})
    with pytest.raises(F.FilingContractError):
        F.CatalystClassification(**base, confidence=1.5)
    with pytest.raises(F.FilingContractError, match="deterministic"):
        F.CatalystClassification(**{**base, "method": "rule"}, confidence=0.9)


def test_a_new_classification_supersedes_by_reference_and_the_old_one_is_untouched():
    f = fact()
    old = F.classify_by_items(f)
    new = F.CatalystClassification(fact_accession=ACC, fact_hash=f.content_hash(), classifier="items_rule", classifier_version="v2",
                                   method="rule", label="earnings_release", supersedes=7)
    assert new.supersedes == 7 and old.classifier_version == "v1" and old.supersedes is None
    with pytest.raises(Exception):
        old.label = "other"
