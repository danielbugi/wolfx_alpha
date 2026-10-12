"""First-seen observation architecture (pure): what First Light KNEW, when it first knew it, who said so, and every later revision.

Mutable external inputs -- an earnings date a company moves, a consensus estimate that is revised, a guidance figure restated -- are poison for
research if only their latest value is kept: a backtest then "knows" the final date and the final consensus on a day when neither existed.
This module is the contract for the append-only log (migration 27, `source_observation`) that prevents it.

  * An observation is one (series, value). A SERIES is (source, dataset, subject, period): "Provider X's eps_estimate for AAPL, fiscal period P".
  * The log stores only CHANGES. Seeing the same value again writes nothing (that fact is recorded by `source_poll`, so "unchanged" is provable
    without a row per poll). A -> B -> A is three rows: a reversion is information.
  * Each row carries `prev_value_hash`, the hash of its predecessor's value, so a series is a hash chain. A deleted or reordered middle row is
    detectable by `verify_chain`; the database also refuses a row whose predecessor does not match.
  * AVAILABILITY is the database-stamped `observed_at` (pit grade B, `ingested`). `source_asof` is the provider's own claim and is kept for audit
    only -- it never becomes availability, because nothing here can prove it. There is no backfill path: nothing can be back-dated.
  * Values are stored verbatim. Derived quantities (surprise, revision trend) are computed at read time from the chain, never stored as facts.
  * "Missing is not zero": an unknown value is None. NaN / infinity are refused rather than written (a number that is not a number is missing).

Nothing here touches a database, a clock or the network.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

SCHEMA_VERSION = "first_seen_v1"
SUBJECT_TYPES = ("symbol", "cik")
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_COMPONENT_RE = re.compile(r"^[^|\x00-\x1f]+$")


class FirstSeenError(ValueError):
    """The input breaks the first-seen contract."""


def _plain(v: Any, path: str = "value") -> Any:
    """JSON-safe, verbatim copy: Decimal/numpy -> number, date -> ISO, NaN/inf refused, containers recursed."""
    if v is None or isinstance(v, (bool, str)):
        return v
    if isinstance(v, Decimal):
        v = float(v)
    elif hasattr(v, "item") and not isinstance(v, (bytes, dict, list, tuple)):
        v = v.item()
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        if not math.isfinite(v):
            raise FirstSeenError(f"{path} is NaN/infinite; an unknown value must be None, not a non-number")
        return v
    if isinstance(v, datetime):
        if v.tzinfo is None:
            raise FirstSeenError(f"{path} is a naive datetime")
        return v.astimezone(timezone.utc).isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, Mapping):
        return {str(k): _plain(x, f"{path}.{k}") for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_plain(x, f"{path}[]") for x in v]
    raise FirstSeenError(f"{path}: type {type(v).__name__} is not storable")


def canonical_value(value: Mapping[str, Any]) -> str:
    return json.dumps(_plain(dict(value)), sort_keys=True, separators=(",", ":"), allow_nan=False)


def value_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_value(value).encode()).hexdigest()


def series_key(source: str, dataset: str, subject_type: str, subject_id: str, period_key: Optional[str] = None) -> str:
    """The canonical identity of a series. Components cannot contain '|' (the separator), so two different series never collide."""
    for name, comp in (("source", source), ("dataset", dataset), ("subject_id", subject_id)):
        if not isinstance(comp, str) or not _COMPONENT_RE.match(comp):
            raise FirstSeenError(f"{name} {comp!r} is empty or contains a reserved character")
    if subject_type not in SUBJECT_TYPES:
        raise FirstSeenError(f"subject_type must be one of {SUBJECT_TYPES}")
    if period_key is not None and not _COMPONENT_RE.match(period_key):
        raise FirstSeenError(f"period_key {period_key!r} is empty or contains a reserved character")
    return "|".join([source, dataset, f"{subject_type}:{subject_id}", period_key if period_key is not None else "-"])


@dataclass(frozen=True)
class Observation:
    """One value a source reported for one series. The writer neither derives nor corrects it."""
    source: str
    dataset: str
    subject_type: str
    subject_id: str
    value: Mapping[str, Any]
    period_key: Optional[str] = None
    source_asof: Optional[datetime] = None      # the provider's own claimed time: audit only

    def __post_init__(self) -> None:
        if not isinstance(self.value, Mapping) or not self.value:
            raise FirstSeenError("value must be a non-empty mapping (a source answer with fields)")
        object.__setattr__(self, "value", _plain(dict(self.value)))
        if self.source_asof is not None:
            if not isinstance(self.source_asof, datetime) or self.source_asof.tzinfo is None:
                raise FirstSeenError("source_asof must be a timezone-aware datetime")
            object.__setattr__(self, "source_asof", self.source_asof.astimezone(timezone.utc))
        self.key  # validates the identity components

    @property
    def key(self) -> str:
        return series_key(self.source, self.dataset, self.subject_type, self.subject_id, self.period_key)

    @property
    def hash(self) -> str:
        return value_hash(self.value)


@dataclass(frozen=True)
class ChainHead:
    """The latest stored position of one series (read from the log)."""
    series_key: str
    seq: int
    value_hash: str


@dataclass(frozen=True)
class PlannedAppend:
    observation: Observation
    seq: int
    prev_value_hash: Optional[str]
    value_hash: str

    @property
    def series_key(self) -> str:
        return self.observation.key


@dataclass(frozen=True)
class AppendPlan:
    appends: Tuple[PlannedAppend, ...]
    unchanged: int
    duplicates_in_batch: int


def plan_appends(observations: Iterable[Observation], heads: Mapping[str, ChainHead]) -> AppendPlan:
    """What a run must write, given the current chain heads. Only a CHANGED value produces a row. One run reports one value per series: two
    different values for the same series inside one batch are refused (which one was 'seen first' would be a coin toss); identical repeats collapse."""
    seen: Dict[str, Observation] = {}
    dup = 0
    for o in observations:
        k = o.key
        if k in seen:
            if seen[k].hash != o.hash:
                raise FirstSeenError(f"series {k} appears twice in one batch with different values")
            dup += 1
            continue
        seen[k] = o
    out: List[PlannedAppend] = []
    unchanged = 0
    for k, o in seen.items():
        head = heads.get(k)
        if head is None:
            out.append(PlannedAppend(o, 1, None, o.hash))
        elif head.value_hash == o.hash:
            unchanged += 1
        else:
            out.append(PlannedAppend(o, head.seq + 1, head.value_hash, o.hash))
    return AppendPlan(tuple(out), unchanged, dup)


# ------------------------------------------------------------------ verification and as-of reads (over rows read from the log)
def verify_chain(rows: Sequence[Mapping[str, Any]]) -> List[str]:
    """Problems found in ONE series' rows (any order). Empty list = intact. Recomputes every value hash from the stored value, so a row whose
    value was altered after the fact is caught even though the database forbids it."""
    problems: List[str] = []
    ordered = sorted(rows, key=lambda r: r["seq"])
    keys = {r["series_key"] for r in ordered}
    if len(keys) > 1:
        return [f"rows belong to {len(keys)} different series"]
    prev: Optional[Mapping[str, Any]] = None
    for i, r in enumerate(ordered, start=1):
        if r["seq"] != i:
            problems.append(f"seq gap: expected {i}, found {r['seq']}")
        if value_hash(r["value"]) != r["value_hash"]:
            problems.append(f"seq {r['seq']}: stored value does not hash to value_hash")
        expected_prev = prev["value_hash"] if prev else None
        if r.get("prev_value_hash") != expected_prev:
            problems.append(f"seq {r['seq']}: prev_value_hash does not match the predecessor")
        if prev and prev["value_hash"] == r["value_hash"]:
            problems.append(f"seq {r['seq']}: repeats its predecessor's value (only changes are stored)")
        if prev and r["observed_at"] < prev["observed_at"]:
            problems.append(f"seq {r['seq']}: observed_at precedes its predecessor's")
        prev = r
    return problems


def as_of(rows: Iterable[Mapping[str, Any]], cutoff: datetime) -> Dict[str, Mapping[str, Any]]:
    """The value of every series as First Light knew it at `cutoff`: the row with the highest seq whose observed_at <= cutoff. A series first
    seen after the cutoff is ABSENT (unknown then), never filled with a later value. Never a latest-value join."""
    if cutoff.tzinfo is None:
        raise FirstSeenError("cutoff must be timezone-aware")
    best: Dict[str, Mapping[str, Any]] = {}
    for r in rows:
        if r["observed_at"] > cutoff:
            continue
        cur = best.get(r["series_key"])
        if cur is None or r["seq"] > cur["seq"]:
            best[r["series_key"]] = r
    return best


# ------------------------------------------------------------------ adapter: the existing yfinance-backed earnings_calendar table
CALENDAR_SOURCE = "earnings_calendar_table"
CALENDAR_ROW_DATASET = "earnings_row"
CALENDAR_UPCOMING_DATASET = "earnings_upcoming_dates"


def observations_from_earnings_calendar(rows: Sequence[Mapping[str, Any]], as_of_date: date) -> List[Observation]:
    """Observations from `earnings_calendar` rows (symbol, report_date, eps_estimate, eps_actual, surprise_pct), as of `as_of_date` (the day the
    caller read the table; supplied, never taken from a clock here).

    Two datasets per symbol:
      * `earnings_row`, period_key = report_date: the row's fields verbatim -- a late eps_actual or a revised estimate is a new link in its chain;
      * `earnings_upcoming_dates`: the sorted list of report dates on or after `as_of_date` -- a company moving its date changes this list.
    The source is a scraper: these are grade B (first seen by us), never an as-of history of the provider. NaN values become None."""
    by_symbol: Dict[str, List[Mapping[str, Any]]] = {}
    out: List[Observation] = []
    for r in rows:
        sym = r.get("symbol")
        rd = r.get("report_date")
        if not sym or not isinstance(rd, date) or isinstance(rd, datetime):
            raise FirstSeenError(f"earnings_calendar row without a symbol / report_date: {dict(r)!r}")
        by_symbol.setdefault(sym, []).append(r)

        def num(x: Any) -> Optional[float]:
            if x is None:
                return None
            f = float(x)
            return f if math.isfinite(f) else None
        out.append(Observation(
            source=CALENDAR_SOURCE, dataset=CALENDAR_ROW_DATASET, subject_type="symbol", subject_id=sym, period_key=rd.isoformat(),
            value={"eps_estimate": num(r.get("eps_estimate")), "eps_actual": num(r.get("eps_actual")),
                   "surprise_pct": num(r.get("surprise_pct"))}))
    for sym, rs in by_symbol.items():
        upcoming = sorted({r["report_date"].isoformat() for r in rs if r["report_date"] >= as_of_date})
        out.append(Observation(source=CALENDAR_SOURCE, dataset=CALENDAR_UPCOMING_DATASET, subject_type="symbol", subject_id=sym,
                               value={"dates": upcoming}))
    return out


def surprise_as_known(chain: Sequence[Mapping[str, Any]], cutoff: datetime) -> Optional[Dict[str, Optional[float]]]:
    """Read-time derivation from an `earnings_row` chain: the estimate and actual as known at `cutoff`, and the surprise computed HERE from
    those two (not the provider's stored `surprise_pct`). None when nothing was known yet; a field is None when it was not yet known."""
    seen = as_of(chain, cutoff)
    if not seen:
        return None
    row = next(iter(seen.values()))["value"]
    est, act = row.get("eps_estimate"), row.get("eps_actual")
    surprise = None
    if est is not None and act is not None and est != 0:
        surprise = (act - est) / abs(est) * 100.0
    return {"eps_estimate": est, "eps_actual": act, "surprise_pct": surprise}
