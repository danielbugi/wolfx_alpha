"""Phase 1A scope (8-K, 10-K, 10-Q, their amendments, NT 10-K / NT 10-Q) and the deterministic classification rules (pure)."""
from datetime import date, datetime, timezone

import pytest

import mi_samples  # noqa: F401  (path setup)
from edgar_samples import CIK_A, NOW, UA, doc, r
from market_intelligence import catalyst_rules as R
from market_intelligence import edgar_collector as E
from market_intelligence import filing_contract as F

UTC = timezone.utc


def fact(form="8-K", items="", acc="0000320193-26-000101"):
    return F.build_fact(dict(accession=acc, cik=CIK_A, form_type=form, filing_date=date(2026, 9, 18), accepted_at=datetime(2026, 9, 18, 20, 5, 11, tzinfo=UTC), items=items))


def test_the_scope_is_exactly_the_approved_eight_forms():
    assert R.PHASE1A_FORMS == frozenset({"8-K", "8-K/A", "10-K", "10-K/A", "10-Q", "10-Q/A", "NT 10-K", "NT 10-Q"})
    assert all(F.FORM_FAMILY.get(f) for f in R.PHASE1A_FORMS)


def test_out_of_scope_forms_are_skipped_by_the_collector_not_stored():
    rows = [r("0000320193-26-000101", form="8-K"), r("0000320193-26-000102", form="4", items=""), r("0000320193-26-000103", form="NT 10-K", items=""),
            r("0000320193-26-000104", form="SC 13G", items=""), r("0000320193-26-000105", form="10-Q/A", items="")]
    facts, rejected, n = E.parse_submissions(doc(CIK_A, rows), CIK_A, now=NOW, forms=R.PHASE1A_FORMS)
    assert [f.fact.form_type for f in facts] == ["8-K", "NT 10-K", "10-Q/A"] and n["skipped_form"] == 2 and not rejected


@pytest.mark.parametrize("form,label", [("NT 10-K", "late_filing_notice"), ("NT 10-Q", "late_filing_notice"), ("10-K", "periodic_report"), ("10-K/A", "periodic_report"),
                                        ("10-Q", "periodic_report"), ("10-Q/A", "periodic_report")])
def test_form_rule_v1_labels_notices_and_periodic_reports(form, label):
    c = R.classify_by_form(fact(form))
    assert (c.classifier, c.classifier_version, c.method, c.label, c.confidence) == ("form_rule", "v1", "rule", label, None)
    assert c.evidence == {"form_type": form} and c.matches(fact(form))


def test_an_8k_is_classified_only_by_the_existing_items_rule_and_only_for_item_2_02():
    assert [(c.classifier, c.label) for c in R.classify_all(fact("8-K", "2.02,9.01"))] == [("items_rule", "earnings_release")]
    assert R.classify_all(fact("8-K", "5.02")) == [] and R.classify_all(fact("8-K", "1.03,3.01,4.02")) == []        # carried in the fact, not interpreted in Phase 1A
    assert [c.label for c in R.classify_all(fact("8-K/A", "2.02"))] == ["earnings_release"]


def test_a_periodic_report_gets_exactly_one_label_and_each_label_names_its_rule_identity():
    cs = R.classify_all(fact("10-K"))
    assert len(cs) == 1 and set(R.RULE_IDENTITY) == {"items_rule", "form_rule"} and R.RULE_IDENTITY["form_rule"] == {"rule_id": "form_rule.v1"}


def test_a_rule_pins_the_exact_fact_it_read_and_a_changed_fact_no_longer_matches():
    f = fact("NT 10-K")
    c = R.classify_by_form(f)
    changed = F.build_fact(dict(accession=f.accession, cik=f.cik, form_type="NT 10-K", filing_date=f.filing_date, accepted_at=f.accepted_at.replace(second=12), items=""))
    assert c.matches(f) and not c.matches(changed)


def test_the_existing_items_rule_is_unchanged():
    c = F.classify_by_items(fact("8-K", "2.02,9.01"))
    assert (c.classifier, c.classifier_version, c.label) == ("items_rule", "v1", "earnings_release")
