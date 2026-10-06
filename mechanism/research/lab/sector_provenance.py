"""Sector provenance and freshness contract (pure: no database, clock, file, network or environment).

A sector tag is a *classification attribute* fetched from a vendor (`daily_fundamentals.sector`, a yfinance `info` field). It is not a dated
observation of the world: the vendor reports today's classification, the row is rewritten in place on a re-fetch, and a row that exists for a
past date may have been written long after it. This module states what a sector tag is allowed to claim at one decision point, and fails closed.

Five evidence states (every sector tag is exactly one of them):

    observed_fresh   a live-captured sector whose source row is dated on or before t0 and at most SECTOR_MAX_AGE_DAYS before it.
                     The ONLY state from which a sector-relative measurement may be treated as point-in-time safe.
    observed_stale   live-captured, but its source row is older than SECTOR_MAX_AGE_DAYS: the sector may have changed since. Never safe.
    reconstructed    a classification projected backwards from a later map. Never safe.
    unavailable      no usable sector at the decision point: none exists, it is blank / the legacy 'Unknown' placeholder, it was stamped after
                     the decision deadline, or its source row is dated after t0.
    unknown          provenance cannot be established (unrecognised provenance, or an observed sector with no source / no source date). Fails
                     closed and is an integrity finding, never a quiet NULL.

`SECTOR_MAX_AGE_DAYS` is the recency window the live capture itself applies when it reads a sector (`research.repository.SECTOR_WINDOW_DAYS`);
a test pins the two together. A row dated exactly SECTOR_MAX_AGE_DAYS before t0 is fresh; one day older is stale.

What this contract deliberately does NOT claim: that a fresh sector is the vendor's *true* historical classification (the source cannot say),
or that a sector learned later was unknown earlier (that is a property of the stored, append-only observations, proved by the dataset tests).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Optional

SECTOR_MAX_AGE_DAYS = 30

OBSERVED_FRESH = "observed_fresh"
OBSERVED_STALE = "observed_stale"
RECONSTRUCTED = "reconstructed"
UNAVAILABLE = "unavailable"
UNKNOWN = "unknown"
EVIDENCE_STATES = (OBSERVED_FRESH, OBSERVED_STALE, RECONSTRUCTED, UNAVAILABLE, UNKNOWN)

PROVENANCES = ("observed", "reconstructed")

NO_SECTOR = "no_sector"
NAME_LATE = "name_late"
ASOF_AFTER_T0 = "asof_after_t0"
UNRECOGNISED_PROVENANCE = "unrecognised_provenance"
SOURCE_MISSING = "source_missing"
ASOF_MISSING = "asof_missing"
STALE = "stale"
FRESH = "fresh"
RECONSTRUCTED_REASON = "reconstructed"
# reasons only the append-only history cross-check produces (Slice 10); a candidate-level classification never yields them
HISTORY_ABSENT = "history_absent"            # the history holds no observation knowable at the decision point
IDENTITY_CONFLICT = "identity_conflict"      # the two sources name different sectors, or the history chain is broken: fail closed


def clean_sector(v: Any) -> Optional[str]:
    """NULL, blank and the legacy 'Unknown' placeholder all mean 'no sector': never a sector of their own."""
    if v is None:
        return None
    s = str(v).strip()
    return None if (not s or s.lower() == "unknown") else s


@dataclass(frozen=True)
class SectorEvidence:
    state: str
    reason: str
    sector: Optional[str]          # the cleaned sector name, kept only when one was present (whatever the state)
    age_days: Optional[int]        # t0 - source row date, when both exist

    @property
    def fresh(self) -> bool:
        return self.state == OBSERVED_FRESH


def classify_sector_evidence(*, sector: Any, source: Optional[str], asof: Optional[date], t0: date, provenance: Optional[str],
                             available: bool = True, max_age_days: int = SECTOR_MAX_AGE_DAYS) -> SectorEvidence:
    """Classify ONE sector tag at decision session `t0`. `available` is False when the tag was stamped after the decision deadline (it did
    not exist for the decision). `asof` is the date of the vendor row the sector came from."""
    if provenance not in PROVENANCES:
        return SectorEvidence(UNKNOWN, UNRECOGNISED_PROVENANCE, clean_sector(sector), None)
    name = clean_sector(sector)
    if name is None:
        return SectorEvidence(UNAVAILABLE, NO_SECTOR, None, None)
    if provenance == "reconstructed":
        return SectorEvidence(RECONSTRUCTED, RECONSTRUCTED_REASON, name, None if asof is None else (t0 - asof).days)
    if not available:
        return SectorEvidence(UNAVAILABLE, NAME_LATE, name, None if asof is None else (t0 - asof).days)
    if source is None or not str(source).strip():
        return SectorEvidence(UNKNOWN, SOURCE_MISSING, name, None)
    if asof is None:
        return SectorEvidence(UNKNOWN, ASOF_MISSING, name, None)
    age = (t0 - asof).days
    if age < 0:
        return SectorEvidence(UNAVAILABLE, ASOF_AFTER_T0, name, age)
    if age > max_age_days:
        return SectorEvidence(OBSERVED_STALE, STALE, name, age)
    return SectorEvidence(OBSERVED_FRESH, FRESH, name, age)
