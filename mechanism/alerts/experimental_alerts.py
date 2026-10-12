"""Experimental alert CONTRACT (catalysts and research breakouts). PURE: no network, no database, no Telegram call, no environment. Nothing here sends anything.

An experimental alert is evidence about an event, never a trading instruction. It is kept apart from the production channel and from the production signal path:
  * its destination is always `experimental` (a private/experimental chat); `PROD_SENDING_ENABLED` and the production channel are not reachable from this module;
  * every alert carries the source, the publication and ingestion timestamps, the extracted facts, a verification status, a confidence (or an explicit absence of one) and market context;
  * a regulatory / clinical catalyst can only be built from a VERIFIED source document: it is refused when the only evidence is a headline (a headline never establishes an approval,
    a clinical-trial result or a regulatory decision);
  * vendor-reported figures are labelled as such and can never be presented as verified filings.
The text uses a stricter advice-word guard than the channel copy (no buy/sell/recommend/target/stop/entry/should...) but, unlike the channel guard, may say "earnings" and "return".
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Tuple

DESTINATION = "experimental"
CLASSES = ("verified_financial_event", "regulatory_clinical_catalyst", "technical_breakout", "vendor_reported_event")
VERIFICATION = ("verified_source_document", "vendor_reported_unverified", "headline_only", "computed_from_prices")
# classes that may only ever rest on a verified source document
VERIFIED_ONLY = ("verified_financial_event", "regulatory_clinical_catalyst")
ADVICE = re.compile(r"\b(buy\w*|sell\w*|recommend\w*|targets?|stops?|entry|entries|should|must|guarantee\w*|opportunit\w*|picks?|winners?|outperform\w*|profit\w*)\b", re.I)
SOURCE_REF = re.compile(r"^(https://\S+|\d{10}-\d{2}-\d{6})$")          # a URL or an SEC accession number
NOTICE = "Experimental research alert. Not a trading signal and not advice."


class AlertContractError(ValueError):
    pass


@dataclass(frozen=True)
class Fact:
    name: str
    value: str
    unit: Optional[str] = None


@dataclass(frozen=True)
class ExperimentalAlert:
    alert_class: str
    symbol: str
    company_id: Optional[str]                    # e.g. the CIK; None only when the source has none
    source: str                                  # sec_edgar | vendor_earnings | fda | clinical_registry | price_data ...
    source_ref: str                              # URL or accession; required for every class except a computed technical alert
    published_at: datetime                       # when the SOURCE published it
    ingested_at: datetime                        # when WE stored it
    facts: Tuple[Fact, ...]
    verification: str
    confidence: Optional[float]                  # 0..1, or None when no calibrated confidence exists (say so rather than inventing one)
    market_context: Mapping[str, Any] = field(default_factory=dict)
    destination: str = DESTINATION


def _aware(dt: datetime, name: str) -> datetime:
    if not isinstance(dt, datetime) or dt.tzinfo is None or dt.utcoffset() is None:
        raise AlertContractError(f"{name} must be a timezone-aware datetime")
    return dt.astimezone(timezone.utc)


def validate(a: ExperimentalAlert, *, now: Optional[datetime] = None) -> ExperimentalAlert:
    """Raises AlertContractError on the first violation; returns the alert unchanged when it is valid."""
    if a.destination != DESTINATION:
        raise AlertContractError("experimental alerts may only target the experimental destination, never the production channel")
    if a.alert_class not in CLASSES:
        raise AlertContractError(f"unknown alert class {a.alert_class!r}")
    if a.verification not in VERIFICATION:
        raise AlertContractError(f"unknown verification status {a.verification!r}")
    if a.alert_class in VERIFIED_ONLY and a.verification != "verified_source_document":
        raise AlertContractError(f"{a.alert_class} requires a verified source document (a headline or a vendor figure never establishes it)")
    if a.alert_class == "vendor_reported_event" and a.verification != "vendor_reported_unverified":
        raise AlertContractError("a vendor-reported event must be labelled vendor_reported_unverified")
    if a.alert_class == "technical_breakout" and a.verification != "computed_from_prices":
        raise AlertContractError("a technical breakout is computed from prices")
    if not re.match(r"^[A-Z][A-Z0-9./-]{0,9}$", a.symbol or ""):
        raise AlertContractError("symbol is required and must be an upper-case ticker")
    if a.alert_class in VERIFIED_ONLY and not a.company_id:
        raise AlertContractError("a verified filing event needs the company identifier (CIK) from the source")
    if a.alert_class != "technical_breakout" and not SOURCE_REF.match(a.source_ref or ""):
        raise AlertContractError("source_ref must be an https URL or an SEC accession number")
    pub, ing = _aware(a.published_at, "published_at"), _aware(a.ingested_at, "ingested_at")
    if ing < pub:
        raise AlertContractError("ingested_at cannot precede published_at")
    if pub > (now or datetime.now(timezone.utc)):
        raise AlertContractError("published_at is in the future")
    if not a.facts or any(not f.name or f.value in (None, "") for f in a.facts):
        raise AlertContractError("at least one extracted fact (name and value) is required; every fact needs both")
    if a.confidence is not None and not (0.0 <= a.confidence <= 1.0):
        raise AlertContractError("confidence must be within 0..1 or None")
    if not isinstance(a.market_context, Mapping) or not a.market_context:
        raise AlertContractError("market_context is required (liquidity / reaction so far / regime)")
    return a


def render(a: ExperimentalAlert, *, now: Optional[datetime] = None) -> str:
    """Plain text for an experimental destination. Validates first; refuses text that contains advice wording."""
    validate(a, now=now)
    facts = "; ".join(f"{f.name}: {f.value}{(' ' + f.unit) if f.unit else ''}" for f in a.facts)
    ctx = "; ".join(f"{k}: {v}" for k, v in sorted(a.market_context.items()))
    conf = "not calibrated" if a.confidence is None else f"{a.confidence:.2f}"
    lines = [NOTICE, f"{a.symbol} · {a.alert_class.replace('_', ' ')}", f"Facts: {facts}", f"Verification: {a.verification.replace('_', ' ')} (confidence {conf})",
             f"Source: {a.source} {a.source_ref}" if a.alert_class != "technical_breakout" else f"Source: {a.source}",
             f"Published: {_aware(a.published_at, 'p').strftime('%Y-%m-%d %H:%M UTC')} · ingested: {_aware(a.ingested_at, 'i').strftime('%Y-%m-%d %H:%M UTC')}", f"Context: {ctx}"]
    text = "\n".join(lines)
    body = text.replace(NOTICE, "")
    if ADVICE.search(body):
        raise AlertContractError(f"advice wording in alert text: {ADVICE.findall(body)}")
    return text
