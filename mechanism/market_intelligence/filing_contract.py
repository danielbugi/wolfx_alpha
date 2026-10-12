"""SEC EDGAR filing-event CONTRACT -- a design with a small executable spine, not a parser, collector or client.

Pure: no database, no network, no clock. It fixes the chain

    filing -> timestamp -> company/identifier -> filing type -> source/provenance -> FACTUAL event record -> (optional, later) catalyst classification

and the rule that keeps the two halves apart:

  * A `FilingFact` is what the filing says about itself, copied verbatim from the source (accession, CIK, form type, items, the SEC's own
    acceptance timestamp). Nothing in it is interpreted, inferred or generated. A fact is immutable; an amendment is a NEW fact (new accession)
    that points at the one it amends. It never edits it.
  * A `CatalystClassification` is an interpretation of a fact. It is versioned (classifier name + version), carries the hash of the exact fact it
    read, and can be superseded by a later classification. It has no field in which to restate a timestamp, a form type, a value or an identity,
    so it cannot rewrite the fact it describes.
  * Anything a model or a heuristic produced is a classification. A model never produces a fact: `build_fact` takes only source-supplied values,
    refuses a missing timestamp rather than defaulting it, and has no "infer" path.

Every fact maps onto migration 25's provider-neutral event model (`events.EventDraft`, event_type `regulatory`), so no schema change is needed to
carry facts. Classifications need their own append-only table (a later migration; none is allocated here).
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from typing import Any, Dict, FrozenSet, Mapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from market_intelligence.events import EventDraft

SOURCE = "sec_edgar"
FACT_SCHEMA_VERSION = "filing_fact_v1"
NY = ZoneInfo("America/New_York")
ACCESSION_RE = re.compile(r"^\d{10}-\d{2}-\d{6}$")
FORM_RE = re.compile(r"^[A-Z0-9][A-Z0-9 /\-]{0,19}$")
ITEM_RE = re.compile(r"^\d{1,2}\.\d{2}$")

# Which forms are *families* we know how to describe. This is a lookup table, not a classifier: an unlisted form is still a valid fact
# (form_type is kept verbatim); it simply has no family. Amendments keep their own form_type ("8-K/A") and are linked, never merged.
FORM_FAMILY: Mapping[str, str] = {
    "8-K": "current_report", "8-K/A": "current_report", "6-K": "current_report_foreign",
    "10-Q": "periodic_report", "10-Q/A": "periodic_report", "10-K": "periodic_report", "10-K/A": "periodic_report",
    "20-F": "periodic_report_foreign", "40-F": "periodic_report_foreign",
    "S-1": "registration", "S-3": "registration", "424B5": "prospectus", "424B2": "prospectus",
    "SC 13D": "ownership", "SC 13G": "ownership", "SC 13D/A": "ownership", "SC 13G/A": "ownership",
    "3": "insider", "4": "insider", "5": "insider", "DEF 14A": "proxy",
}
AMENDMENT_SUFFIX = "/A"


class FilingContractError(ValueError):
    """The input breaks the contract. A missing or unprovable fact is refused, never defaulted."""


def _aware_utc(ts: datetime, what: str) -> datetime:
    if not isinstance(ts, datetime) or ts.tzinfo is None:
        raise FilingContractError(f"{what} must be a timezone-aware datetime supplied by the source")
    return ts.astimezone(timezone.utc)


@dataclass(frozen=True)
class FilingFact:
    """What a filing says about itself. Every field is source-supplied; none is computed except `content_hash()`."""
    accession: str                    # EDGAR accession number: the filing's identity and the fact's source_ref
    cik: int                          # the filer's SEC Central Index Key: the only identity a fact asserts
    form_type: str                    # verbatim, e.g. "8-K"
    filing_date: date                 # the date EDGAR assigns (a filing accepted after the cut-off carries the NEXT business day)
    accepted_at: datetime             # the SEC's acceptance timestamp, tz-aware (stored UTC). The one PIT-relevant instant
    items: Tuple[str, ...] = ()       # 8-K / 6-K item numbers exactly as filed ("2.02", "9.01"); empty for forms without items
    report_period: Optional[date] = None
    primary_document: Optional[str] = None
    amends_accession: Optional[str] = None
    source: str = SOURCE

    def __post_init__(self) -> None:
        if not ACCESSION_RE.match(self.accession or ""):
            raise FilingContractError(f"accession {self.accession!r} is not NNNNNNNNNN-YY-NNNNNN")
        if not isinstance(self.cik, int) or isinstance(self.cik, bool) or self.cik <= 0:
            raise FilingContractError(f"cik must be a positive integer, got {self.cik!r}")
        if not FORM_RE.match(self.form_type or ""):
            raise FilingContractError(f"form_type {self.form_type!r} is empty or malformed")
        if not isinstance(self.filing_date, date) or isinstance(self.filing_date, datetime):
            raise FilingContractError("filing_date must be a date")
        object.__setattr__(self, "accepted_at", _aware_utc(self.accepted_at, "accepted_at"))
        for it in self.items:
            if not ITEM_RE.match(it):
                raise FilingContractError(f"item {it!r} is not an 8-K style item number")
        if self.amends_accession is not None:
            if not ACCESSION_RE.match(self.amends_accession):
                raise FilingContractError(f"amends_accession {self.amends_accession!r} is malformed")
            if self.amends_accession == self.accession:
                raise FilingContractError("a filing cannot amend itself")
            if not self.form_type.endswith(AMENDMENT_SUFFIX):
                raise FilingContractError("amends_accession is only valid on an amendment form (form_type ending /A)")
        if self.source != SOURCE:
            raise FilingContractError(f"this contract describes {SOURCE!r} facts only")

    @property
    def form_family(self) -> Optional[str]:
        return FORM_FAMILY.get(self.form_type)

    def content(self) -> Dict[str, Any]:
        return {"schema": FACT_SCHEMA_VERSION, "source": self.source, "accession": self.accession, "cik": self.cik,
                "form_type": self.form_type, "filing_date": self.filing_date.isoformat(),
                "accepted_at": self.accepted_at.isoformat(), "items": list(self.items),
                "report_period": self.report_period.isoformat() if self.report_period else None,
                "primary_document": self.primary_document, "amends_accession": self.amends_accession}

    def content_hash(self) -> str:
        return hashlib.sha256(json.dumps(self.content(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def build_fact(raw: Mapping[str, Any]) -> FilingFact:
    """Turn ONE source record (the keys EDGAR's own submissions feed uses) into a fact. Every value is taken as given; a missing required
    value raises. There is no default for the timestamp, the form type or the identity, and nothing here estimates anything."""
    for key in ("accession", "cik", "form_type", "filing_date", "accepted_at"):
        if raw.get(key) in (None, ""):
            raise FilingContractError(f"source record has no {key!r}; a fact is not built from a partial record")
    items = raw.get("items") or ()
    if isinstance(items, str):
        items = tuple(i.strip() for i in items.split(",") if i.strip())
    return FilingFact(
        accession=str(raw["accession"]), cik=int(raw["cik"]), form_type=str(raw["form_type"]), filing_date=raw["filing_date"],
        accepted_at=raw["accepted_at"], items=tuple(items), report_period=raw.get("report_period"),
        primary_document=raw.get("primary_document"), amends_accession=raw.get("amends_accession"))


# ------------------------------------------------------------------ company / identifier
@dataclass(frozen=True)
class IdentifierLink:
    """CIK -> trading symbol, held OUTSIDE the fact. A symbol is reused, renamed and delisted, and the SEC's own ticker file describes only the
    present, so a link carries its validity window and how it is known. A fact without a link is still a fact; it simply has no symbol."""
    cik: int
    symbol: str
    valid_from: Optional[date]        # None = start unknown
    valid_to: Optional[date]          # None = still valid / end unknown
    basis: str                        # 'observed' (we captured the mapping on a date) | 'reconstructed' (today's mapping projected back)

    def __post_init__(self) -> None:
        if self.basis not in ("observed", "reconstructed"):
            raise FilingContractError("an identifier link is 'observed' or 'reconstructed'")
        if not self.symbol:
            raise FilingContractError("symbol is required")
        if self.valid_from and self.valid_to and self.valid_to < self.valid_from:
            raise FilingContractError("valid_to precedes valid_from")

    def covers(self, d: date) -> bool:
        return (self.valid_from is None or self.valid_from <= d) and (self.valid_to is None or d <= self.valid_to)


def resolve_symbol(fact: FilingFact, links: Sequence[IdentifierLink]) -> Tuple[Optional[str], str]:
    """The symbol for a fact, with the basis. Exactly one covering link -> its symbol; none -> (None, 'unmapped'); several with different symbols
    -> (None, 'ambiguous'). Reconstructed links are reported as such, never upgraded."""
    on = fact.filing_date
    hits = {(l.symbol, l.basis) for l in links if l.cik == fact.cik and l.covers(on)}
    syms = {s for s, _ in hits}
    if not hits:
        return None, "unmapped"
    if len(syms) > 1:
        return None, "ambiguous"
    bases = {b for _, b in hits}
    return syms.pop(), ("observed" if bases == {"observed"} else "reconstructed")


# ------------------------------------------------------------------ timestamp -> session timing (derived deterministically from the fact)
def filing_session_timing(accepted_at: datetime, trading_sessions: Optional[FrozenSet[date]] = None) -> str:
    """When in the US session the filing was accepted: BMO (before 09:30 ET), INTRADAY (09:30-16:00), AMC (16:00 or later).
    This is a property of the FILING, not of the event it announces (a release can precede its 8-K). A weekend, or a day outside a supplied
    session calendar, is 'UNKNOWN' -- never rolled forward silently. Early-close days are not modelled: pass no calendar and treat 'INTRADAY'
    near 13:00 ET as approximate, or supply the calendar and read the close from it in a later version."""
    local = _aware_utc(accepted_at, "accepted_at").astimezone(NY)
    if local.weekday() >= 5:
        return "UNKNOWN"
    if trading_sessions is not None and local.date() not in trading_sessions:
        return "UNKNOWN"
    t = local.time()
    if t < time(9, 30):
        return "BMO"
    if t < time(16, 0):
        return "INTRADAY"
    return "AMC"


def to_event_draft(fact: FilingFact, symbol: Optional[str] = None, symbol_basis: str = "unmapped",
                   trading_sessions: Optional[FrozenSet[date]] = None) -> EventDraft:
    """The fact as a migration-25 event: type `regulatory` (it asserts no meaning), known_at = the SEC acceptance time (vendor_published, grade A:
    the source proved the time and a filing is never edited, only superseded), revision 1.

    known_at is an UPPER bound on public availability that is at-or-after the true instant for an earnings release (the press release usually
    precedes its 8-K), so using it can only make a feature later than reality, never earlier. EDGAR may disseminate a filing some time after
    acceptance, so a feature builder must apply and record an availability lag; our own ingested_at is a hard ceiling once live capture exists."""
    return EventDraft(
        event_key=f"sec|{fact.accession}", symbol=symbol, event_type="regulatory", event_time=fact.accepted_at.astimezone(NY).date(),
        status="reported", source=SOURCE, source_ref=fact.accession, known_at_basis="vendor_published", pit_grade="A",
        published_at=fact.accepted_at, session_timing=filing_session_timing(fact.accepted_at, trading_sessions),
        payload={"schema": FACT_SCHEMA_VERSION, "fact_hash": fact.content_hash(), "cik": fact.cik, "form_type": fact.form_type,
                 "form_family": fact.form_family, "filing_date": fact.filing_date.isoformat(), "items": list(fact.items),
                 "amends_accession": fact.amends_accession, "symbol_basis": symbol_basis},
        provenance="observed")


# ------------------------------------------------------------------ interpretation (separate, versioned, supersedable)
CLASSIFICATION_METHODS = ("rule", "model", "human")
FACTUAL_KEYS = frozenset({"accession", "cik", "form_type", "filing_date", "accepted_at", "items", "report_period", "published_at",
                          "known_at", "symbol", "eps_actual", "revenue_actual", "eps_estimate", "revenue_estimate"})


@dataclass(frozen=True)
class CatalystClassification:
    """An interpretation of ONE fact. It has no field for a timestamp, form type, identity or financial value; `fact_hash` pins the exact fact it
    read, so a change in the fact is visible and a stale classification is detectable. A newer classification supersedes an older one; the older
    stays."""
    fact_accession: str
    fact_hash: str
    classifier: str                   # e.g. "items_rule"
    classifier_version: str           # a new version is a new row, never an edit
    method: str                       # rule | model | human
    label: str                        # e.g. "earnings_release"; the vocabulary belongs to the classifier version
    evidence: Mapping[str, Any] = field(default_factory=dict)   # citations of fact fields (e.g. {"items": ["2.02"]}); never new facts
    confidence: Optional[float] = None
    supersedes: Optional[int] = None  # id of the classification this replaces

    def __post_init__(self) -> None:
        if not ACCESSION_RE.match(self.fact_accession or ""):
            raise FilingContractError("fact_accession is malformed")
        if not re.fullmatch(r"[0-9a-f]{64}", self.fact_hash or ""):
            raise FilingContractError("fact_hash must be the 64-hex content hash of the fact that was read")
        if self.method not in CLASSIFICATION_METHODS:
            raise FilingContractError(f"method must be one of {CLASSIFICATION_METHODS}")
        if not self.classifier or not self.classifier_version or not self.label:
            raise FilingContractError("classifier, classifier_version and label are required")
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise FilingContractError("confidence must lie in [0, 1]")
        restated = FACTUAL_KEYS.intersection(self.evidence.keys()) - {"items", "form_type"}
        if restated:
            raise FilingContractError(f"evidence may cite items/form_type only; it may not restate {sorted(restated)}")
        if self.method == "rule" and self.confidence is not None:
            raise FilingContractError("a rule is deterministic: it has a label, not a confidence")

    def matches(self, fact: FilingFact) -> bool:
        """True only if this classification read exactly this fact (same accession, same content)."""
        return fact.accession == self.fact_accession and fact.content_hash() == self.fact_hash


def classify_by_items(fact: FilingFact) -> Optional[CatalystClassification]:
    """The one rule shipped as an example of the shape (deterministic, `items_rule` v1): an 8-K that lists Item 2.02 ('Results of Operations and
    Financial Condition') is labelled `earnings_release`. It reads only the fact's own `items`. No text is read, nothing is estimated."""
    if fact.form_type not in ("8-K", "8-K/A") or "2.02" not in fact.items:
        return None
    return CatalystClassification(
        fact_accession=fact.accession, fact_hash=fact.content_hash(), classifier="items_rule", classifier_version="v1",
        method="rule", label="earnings_release", evidence={"form_type": fact.form_type, "items": list(fact.items)})
