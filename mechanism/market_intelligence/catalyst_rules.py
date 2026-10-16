"""Phase 1A scope and the deterministic classification rules applied to it. PURE: stdlib + filing_contract.

Scope (owner-approved): 8-K and 8-K/A, 10-K and 10-K/A, 10-Q and 10-Q/A, NT 10-K and NT 10-Q. Anything else the source lists is skipped, not stored.

Rules (interpretation only; each is a separate, versioned row in `catalyst_classification`, never a field of the fact):
  items_rule  v1  (existing, unchanged)  8-K / 8-K/A listing Item 2.02                    -> earnings_release
  form_rule   v1  (new)                  NT 10-K / NT 10-Q                                -> late_filing_notice
                                         10-K, 10-K/A, 10-Q, 10-Q/A                       -> periodic_report
A rule reads only the fact's own `form_type` / `items`; it reads no filing text and estimates nothing. 8-K items other than 2.02 are NOT classified in
Phase 1A: they travel with the fact (payload.items) for researchers, unlabelled.
"""
from __future__ import annotations

from typing import FrozenSet, List, Mapping, Optional

from market_intelligence import filing_contract as F

PHASE1A_FORMS: FrozenSet[str] = frozenset({"8-K", "8-K/A", "10-K", "10-K/A", "10-Q", "10-Q/A", "NT 10-K", "NT 10-Q"})
RULE_IDENTITY: Mapping[str, Mapping[str, str]] = {
    "items_rule": {"rule_id": "items_rule.v1"},
    "form_rule": {"rule_id": "form_rule.v1"},
}
_PERIODIC = frozenset({"10-K", "10-K/A", "10-Q", "10-Q/A"})
_LATE = frozenset({"NT 10-K", "NT 10-Q"})


def classify_by_form(fact: F.FilingFact) -> Optional[F.CatalystClassification]:
    """`form_rule` v1: a deterministic lookup on the form type."""
    if fact.form_type in _LATE:
        label = "late_filing_notice"
    elif fact.form_type in _PERIODIC:
        label = "periodic_report"
    else:
        return None
    return F.CatalystClassification(fact_accession=fact.accession, fact_hash=fact.content_hash(), classifier="form_rule", classifier_version="v1",
                                    method="rule", label=label, evidence={"form_type": fact.form_type})


def classify_all(fact: F.FilingFact) -> List[F.CatalystClassification]:
    """Every Phase 1A rule that applies to this fact (zero, one or two classifications)."""
    return [c for c in (F.classify_by_items(fact), classify_by_form(fact)) if c is not None]
