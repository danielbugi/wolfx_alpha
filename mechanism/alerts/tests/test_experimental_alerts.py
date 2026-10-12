"""Experimental alert contract: verified-only classes, no inference from headlines, separation from the production channel, mandatory provenance, advice-word guard."""
import ast
import os
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))
sys.path.insert(0, ROOT)

from alerts import experimental_alerts as X  # noqa: E402

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
PUB = NOW - timedelta(hours=3)
ING = NOW - timedelta(hours=2)


def alert(**kw):
    base = dict(alert_class="verified_financial_event", symbol="ABCD", company_id="0000123456", source="sec_edgar", source_ref="0000123456-26-000123",
                published_at=PUB, ingested_at=ING, facts=(X.Fact("revenue_yoy_growth", "41.2", "%"), X.Fact("prior_quarter_revenue_yoy_growth", "18.0", "%")),
                verification="verified_source_document", confidence=None, market_context={"avg_dollar_volume_20d": "12.4M USD", "reaction_so_far": "+3.1% (market-adjusted)"})
    base.update(kw)
    return X.ExperimentalAlert(**base)


def test_a_complete_verified_alert_validates_and_renders_with_every_required_element():
    t = X.render(alert(), now=NOW)
    for needle in ("Experimental research alert", "ABCD", "revenue_yoy_growth: 41.2 %", "Verification: verified source document", "not calibrated", "0000123456-26-000123",
                   "Published: 2026-10-12 09:00 UTC", "ingested: 2026-10-12 10:00 UTC", "Context:"):
        assert needle in t, needle


@pytest.mark.parametrize("bad_verification", ["headline_only", "vendor_reported_unverified", "computed_from_prices"])
def test_a_regulatory_or_clinical_catalyst_is_refused_unless_the_source_document_is_verified(bad_verification):
    with pytest.raises(X.AlertContractError, match="verified source document"):
        X.validate(alert(alert_class="regulatory_clinical_catalyst", source="fda", source_ref="https://www.fda.gov/x", verification=bad_verification), now=NOW)


def test_a_verified_regulatory_catalyst_with_a_document_url_is_accepted():
    a = alert(alert_class="regulatory_clinical_catalyst", source="fda", source_ref="https://www.accessdata.fda.gov/doc.pdf", facts=(X.Fact("decision_document", "approval letter dated 2026-10-09"),))
    assert X.validate(a, now=NOW) is a


def test_a_financial_event_from_a_vendor_cannot_pose_as_verified_and_must_say_so():
    with pytest.raises(X.AlertContractError):
        X.validate(alert(alert_class="verified_financial_event", verification="vendor_reported_unverified"), now=NOW)
    ok = alert(alert_class="vendor_reported_event", source="vendor_earnings", source_ref="https://example.org/earnings/ABCD", verification="vendor_reported_unverified", company_id=None)
    assert "vendor reported unverified" in X.render(ok, now=NOW)
    with pytest.raises(X.AlertContractError):
        X.validate(replace(ok, verification="verified_source_document"), now=NOW)


def test_an_experimental_alert_can_never_target_the_production_channel():
    for dest in ("prod", "production", "channel", "dev"):
        with pytest.raises(X.AlertContractError, match="experimental destination"):
            X.validate(alert(destination=dest), now=NOW)


@pytest.mark.parametrize("kw,match", [
    ({"published_at": datetime(2026, 10, 12, 9, 0)}, "timezone-aware"),
    ({"ingested_at": PUB - timedelta(minutes=1)}, "cannot precede"),
    ({"published_at": NOW + timedelta(hours=1), "ingested_at": NOW + timedelta(hours=2)}, "future"),
    ({"facts": ()}, "fact"),
    ({"facts": (X.Fact("revenue", ""),)}, "fact"),
    ({"market_context": {}}, "market_context"),
    ({"confidence": 1.5}, "confidence"),
    ({"source_ref": "see headline"}, "source_ref"),
    ({"company_id": None}, "company identifier"),
    ({"symbol": "abcd"}, "ticker"),
    ({"alert_class": "alpha_pick"}, "unknown alert class"),
    ({"verification": "trust_me"}, "unknown verification"),
])
def test_missing_or_invalid_provenance_is_refused(kw, match):
    with pytest.raises(X.AlertContractError, match=match):
        X.validate(alert(**kw), now=NOW)


def test_a_technical_breakout_is_computed_from_prices_and_needs_no_filing_reference():
    a = alert(alert_class="technical_breakout", company_id=None, source="price_data", source_ref="", verification="computed_from_prices",
              facts=(X.Fact("channel", "20-day high crossed at close"), X.Fact("close", "52.10", "USD")), market_context={"index_trend": "above 200-day average", "breadth": "58% above 50-day"})
    assert "Source: price_data" in X.render(a, now=NOW)
    with pytest.raises(X.AlertContractError):
        X.validate(replace(a, verification="verified_source_document"), now=NOW)


@pytest.mark.parametrize("word", ["buy", "sell now", "target 55", "stop at 49", "entry 51", "you should", "recommend", "guaranteed", "opportunity", "profit"])
def test_advice_wording_in_any_field_is_refused(word):
    with pytest.raises(X.AlertContractError, match="advice wording"):
        X.render(alert(facts=(X.Fact("note", f"analyst says {word}"),)), now=NOW)


def test_the_module_is_pure_and_cannot_send():
    src = Path(X.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported = {n.names[0].name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import)} | {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert imported <= {"__future__", "re", "dataclasses", "datetime", "typing"}, imported
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id in {"open", "requests", "psycopg2", "os", "environ", "send"}]
