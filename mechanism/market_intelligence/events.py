"""Earnings / catalyst EVENT MODEL and the source interface -- a foundation only.

No collector, no vendor, nothing scheduled. This module defines what a future source must hand over and the rules that keep the
event history point-in-time (PIT) honest. Pure: no database, no network, no clock.

Rules (mirrored by CHECK constraints in migration 25):
  * known_at is when the information was available. Basis `ingested` (the database stamps it), `vendor_published` (the source gave a
    publication time) or `unknown` -- in which case known_at is NULL. It is never guessed, back-dated or defaulted.
  * grade A/C <-> vendor_published, B <-> ingested, X <-> unknown. Grade X is never PIT-eligible.
  * Estimates and actuals are separate fields; an actual needs a reported/revised status. Surprise is derived at read time.
  * Revisions are appended, never edited. The version of an event visible at a cutoff is the latest one whose known_at <= cutoff.
  * NO MODEL may consume an event without PIT provenance: `ml_view` returns only observed, graded A/B rows with a known_at <= cutoff.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence, runtime_checkable

EVENT_TYPES = ("earnings_scheduled", "earnings_reported", "estimate_eps", "estimate_revenue", "guidance", "analyst_action",
               "price_target", "corporate_event", "regulatory", "mna", "dividend", "split", "news")
STATUSES = ("scheduled", "confirmed", "reported", "revised", "cancelled")
TIMINGS = ("BMO", "AMC", "INTRADAY", "UNKNOWN")
BASES = ("ingested", "vendor_published", "unknown")
GRADES = ("A", "B", "C", "X")
GRADE_FOR_BASIS = {"vendor_published": ("A", "C"), "ingested": ("B",), "unknown": ("X",)}
ML_GRADES = ("A", "B")


class EventValidationError(ValueError):
    """The draft contradicts a PIT rule. A source that cannot prove a basis must say 'unknown', not invent one."""


@dataclass(frozen=True)
class RawEvent:
    """Whatever a source returned, untouched. `source_ref` is the source's own record id."""
    source: str
    source_ref: str
    data: Mapping[str, Any]


@dataclass(frozen=True)
class EventDraft:
    """A normalised event ready to be appended. It carries NO ingested_at and (for basis `ingested`) no known_at: the database stamps both."""
    event_key: str
    symbol: Optional[str]                       # None = market-wide
    event_type: str
    event_time: date
    status: str
    source: str
    source_ref: str
    known_at_basis: str
    pit_grade: str
    published_at: Optional[datetime] = None
    session_timing: str = "UNKNOWN"
    fiscal_period: Optional[str] = None
    eps_estimate: Optional[float] = None
    eps_actual: Optional[float] = None
    revenue_estimate: Optional[float] = None
    revenue_actual: Optional[float] = None
    payload: Mapping[str, Any] = field(default_factory=dict)
    provenance: str = "observed"
    reconstruction_basis: Optional[str] = None

    def content(self) -> Dict[str, Any]:
        """The fields that define the event's state. Two drafts with equal content are the same revision (idempotent re-ingest)."""
        return {
            "event_key": self.event_key, "symbol": self.symbol, "event_type": self.event_type,
            "event_time": self.event_time.isoformat(), "status": self.status, "session_timing": self.session_timing,
            "fiscal_period": self.fiscal_period, "eps_estimate": self.eps_estimate, "eps_actual": self.eps_actual,
            "revenue_estimate": self.revenue_estimate, "revenue_actual": self.revenue_actual,
            "published_at": self.published_at.isoformat() if self.published_at else None, "payload": dict(self.payload),
        }

    def payload_hash(self) -> str:
        return hashlib.sha256(json.dumps(self.content(), sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()


@runtime_checkable
class CatalystSource(Protocol):
    """What a future collector must implement. Declares up front what it can prove about WHEN it learned things."""
    name: str
    known_at_basis: str      # the strongest basis this source can prove: 'vendor_published' | 'ingested' | 'unknown'

    def fetch(self, since: date, until: date) -> Iterable[RawEvent]: ...

    def normalise(self, raw: RawEvent) -> EventDraft: ...


def validate(draft: EventDraft, declared_basis: Optional[str] = None) -> EventDraft:
    """Raise EventValidationError if the draft breaks a PIT rule; otherwise return it unchanged."""
    d = draft
    if d.event_type not in EVENT_TYPES:
        raise EventValidationError(f"event_type {d.event_type!r} is not one of {EVENT_TYPES}")
    if d.status not in STATUSES:
        raise EventValidationError(f"status {d.status!r} is not one of {STATUSES}")
    if d.session_timing not in TIMINGS:
        raise EventValidationError(f"session_timing {d.session_timing!r} is not one of {TIMINGS}")
    if not d.event_key or not d.source or not d.source_ref:
        raise EventValidationError("event_key, source and source_ref are required")
    if d.known_at_basis not in BASES:
        raise EventValidationError(f"known_at_basis {d.known_at_basis!r} is not one of {BASES}")
    if d.pit_grade not in GRADE_FOR_BASIS[d.known_at_basis]:
        raise EventValidationError(f"grade {d.pit_grade!r} is not valid for basis {d.known_at_basis!r} "
                                   f"(allowed {GRADE_FOR_BASIS[d.known_at_basis]})")
    if declared_basis is not None:
        order = {"unknown": 0, "ingested": 1, "vendor_published": 2}
        if order[d.known_at_basis] > order[declared_basis]:
            raise EventValidationError(f"the source declared basis {declared_basis!r} but the draft claims the stronger {d.known_at_basis!r}")
    if d.known_at_basis == "vendor_published":
        if d.published_at is None or d.published_at.tzinfo is None:
            raise EventValidationError("basis vendor_published requires a timezone-aware published_at")
    if (d.eps_actual is not None or d.revenue_actual is not None) and d.status not in ("reported", "revised"):
        raise EventValidationError("an actual may only be set on a reported/revised event")
    if d.provenance not in ("observed", "reconstructed"):
        raise EventValidationError(f"provenance {d.provenance!r} must be observed or reconstructed")
    if (d.provenance == "reconstructed") != (d.reconstruction_basis is not None):
        raise EventValidationError("reconstruction_basis is required for, and only for, a reconstructed event")
    return d


def normalise_checked(source: CatalystSource, raw: RawEvent) -> EventDraft:
    """The only sanctioned way to turn a RawEvent into a draft: the source's own normaliser, then the PIT rules, with the source's declared
    basis as a ceiling. A source cannot claim a better known_at than it said it could prove."""
    return validate(source.normalise(raw), declared_basis=source.known_at_basis)


# ------------------------------------------------------------------ reading: PIT visibility and the ML gate
def _val(row: Any, key: str) -> Any:
    return row[key] if isinstance(row, Mapping) else getattr(row, key)


def _require_aware(cutoff: datetime) -> None:
    if cutoff.tzinfo is None:
        raise ValueError("cutoff must be timezone-aware")


def visible_as_of(revisions: Iterable[Any], cutoff: datetime) -> List[Any]:
    """The state of every event as it could have been known at `cutoff`: per event_key, the highest revision whose known_at <= cutoff.
    A revision with no known_at is never visible (we cannot say when it became known). Rows may be mappings or objects."""
    _require_aware(cutoff)
    best: Dict[str, Any] = {}
    for r in revisions:
        k = _val(r, "known_at")
        if k is None or _val(r, "known_at_basis") == "unknown" or k > cutoff:
            continue
        key = _val(r, "event_key")
        if key not in best or _val(r, "revision") > _val(best[key], "revision"):
            best[key] = r
    return sorted(best.values(), key=lambda r: (_val(r, "event_key")))


@dataclass
class MLView:
    rows: List[Any]
    excluded: Dict[str, int]          # reason -> count, so a thin result is explained, not silent


def ml_view(revisions: Iterable[Any], cutoff: datetime) -> MLView:
    """The ONLY event view a model may use: visible as of cutoff, observed, graded A/B. Everything else is counted and dropped.
    There is no parameter to relax this."""
    _require_aware(cutoff)
    visible = visible_as_of(revisions, cutoff)
    out, excluded = [], {"reconstructed": 0, "grade_not_ml_eligible": 0}
    for r in visible:
        if _val(r, "provenance") != "observed":
            excluded["reconstructed"] += 1
        elif _val(r, "pit_grade") not in ML_GRADES:
            excluded["grade_not_ml_eligible"] += 1
        else:
            out.append(r)
    return MLView(out, excluded)


def assert_ml_eligible(row: Any) -> None:
    """Guard for any code path that is handed a single event row to use as a model input."""
    if _val(row, "known_at") is None or _val(row, "known_at_basis") == "unknown" or _val(row, "pit_grade") == "X":
        raise EventValidationError("event has no point-in-time provenance (unknown known_at) and cannot be a model input")
    if _val(row, "provenance") != "observed" or _val(row, "pit_grade") not in ML_GRADES:
        raise EventValidationError("only observed, grade A/B events may be model inputs")


def surprise(actual: Optional[float], estimate: Optional[float]) -> Dict[str, Optional[float]]:
    """Derived at read time, never stored. Percentage surprise is undefined (None) when the estimate is missing or zero."""
    if actual is None or estimate is None:
        return {"abs": None, "pct": None}
    ab = float(actual) - float(estimate)
    pct = ab / abs(float(estimate)) * 100.0 if float(estimate) != 0 else None
    return {"abs": ab, "pct": pct}
