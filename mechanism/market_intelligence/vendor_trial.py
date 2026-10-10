"""Provider-neutral trial harness for an earnings-consensus / guidance source (pure). Implements EARNINGS_SOURCE_ASSESSMENT.md section 6.

A provider is evaluated BEFORE any purchase by an adapter that exposes three read calls. The harness never holds credentials (the adapter is
built by the caller), never names a vendor (a `provider_id` is an opaque label), and never writes anything: its output is a `TrialReport`
holding an observed PIT grade per field. Grade A needs checks 1-3 to PASS on enough evidence; unproven is X, never a benefit of the doubt.

  1 report time   vendor never later than the SEC acceptance time; its BMO/AMC/DMH label agrees with its own timestamp / the SEC bucket
  2 as-of         values differ across as-of dates (not one restated number) and equal our own first-seen snapshots where we hold them
  3 restatement   re-pulling the same historical quarter later changes nothing silently
  4 actuals       vendor actuals vs SEC XBRL, basis difference stated (gaap / adjusted / unknown)
  5 coverage      share of the sample with each field, revenue and guidance separately
  6 operational   observed rate/outage/pagination facts; a silent-empty outage is a failure (missing must never read as zero)
  7 licence       human-recorded answers; an unanswered question blocks adoption

Evidence is collected in two phases (`collect_snapshot` now, again >= 7 days later) so check 3 compares real, hashed, time-separated pulls.
Nothing here imports a database, reads the clock itself (callers inject one), or touches the network.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence, Tuple
from zoneinfo import ZoneInfo

from market_intelligence.first_seen import value_hash

POLICY_VERSION = "trial_policy_v1"
ESTIMATE_FIELDS = ("eps_consensus", "revenue_consensus")
GUIDANCE_FIELDS = ("guidance_eps", "guidance_revenue")
ACTUAL_FIELDS = ("eps_actual", "revenue_actual")
AS_OF_FIELDS = ESTIMATE_FIELDS + GUIDANCE_FIELDS
ALL_FIELDS = AS_OF_FIELDS + ACTUAL_FIELDS
TIMINGS = ("BMO", "AMC", "DMH")
BASES = ("gaap", "adjusted", "unknown")
TAGS = ("date_move", "split", "delisting", "adr")
LICENCE_QUESTIONS = ("store_history", "derive_signals", "publish_public_channel", "paid_product", "cancellation_data_fate")
LICENCE_ANSWERS = ("permitted", "prohibited", "needs_negotiation")
OUTAGE_BEHAVIOURS = ("error_codes", "silent_empty", "unknown")
PASS, FAIL, INCONCLUSIVE = "pass", "fail", "inconclusive"
_ET = ZoneInfo("America/New_York")
_PROVIDER_RE = re.compile(r"^[a-z][a-z0-9_]{1,39}$")

Key = Tuple[str, date, str]          # (symbol, fiscal period end, field)


class TrialError(ValueError):
    """The trial inputs break the harness contract."""


class AsOfUnsupported(Exception):
    """The provider cannot return a value as of a past date (its history is not as-of)."""


@dataclass(frozen=True)
class TrialPolicy:
    version: str = POLICY_VERSION
    min_symbol_quarters: int = 200
    min_distinct_quarters: int = 8
    max_later_than_edgar_rate: float = 0.0
    max_label_disagreement_rate: float = 0.05
    min_varying_share: float = 0.15
    min_reference_points: int = 20
    min_reference_match: float = 0.9
    min_restatement_overlap: int = 50
    min_restatement_gap: timedelta = timedelta(days=7)
    min_actual_match: float = 0.95
    min_coverage: float = 0.8
    rel_tol: float = 1e-3
    abs_tol: float = 0.005


@dataclass(frozen=True)
class Sample:
    symbol: str
    period: date
    as_of_dates: Tuple[datetime, ...] = ()
    tags: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ReportTime:
    at: Optional[datetime] = None
    timing: Optional[str] = None


@dataclass(frozen=True)
class Value:
    value: Optional[float]
    basis: str = "unknown"


class ProviderAdapter(Protocol):
    provider_id: str

    def report_time(self, symbol: str, period: date) -> Optional[ReportTime]: ...
    def current_value(self, symbol: str, period: date, field: str) -> Optional[Value]: ...
    def value_as_of(self, symbol: str, period: date, field: str, as_of: datetime) -> Optional[float]: ...


@dataclass(frozen=True)
class Reference:
    """What WE hold independently: SEC acceptance times, SEC XBRL (gaap) actuals, our own first-seen snapshots (observed_at, value)."""
    edgar_accepted: Mapping[Tuple[str, date], datetime] = field(default_factory=dict)
    xbrl_actuals: Mapping[Key, float] = field(default_factory=dict)
    own_first_seen: Mapping[Key, Sequence[Tuple[datetime, Optional[float]]]] = field(default_factory=dict)


@dataclass(frozen=True)
class Operational:
    """Observed by whoever ran the calls; the harness only judges it."""
    sustained_rps: Optional[float] = None
    universe_size: Optional[int] = None
    outage_behaviour: str = "unknown"
    pagination_ok: Optional[bool] = None
    auth_documented: Optional[bool] = None


@dataclass(frozen=True)
class Licence:
    """Written answers from the vendor's terms, recorded by a human with the document reference. Missing = unanswered = blocks."""
    answers: Mapping[str, str] = field(default_factory=dict)
    document_ref: Optional[str] = None
    recorded_by: Optional[str] = None


@dataclass(frozen=True)
class Snapshot:
    provider_id: str
    collected_at: datetime
    report_times: Mapping[Tuple[str, date], Optional[ReportTime]]
    current: Mapping[Key, Optional[Value]]
    as_of: Mapping[Key, Tuple[Tuple[datetime, Optional[float]], ...]]
    as_of_unsupported: Tuple[Key, ...]
    errors: Mapping[str, int]
    calls: int
    max_latency_s: float

    def digest(self) -> str:
        return value_hash({
            "provider": self.provider_id, "collected_at": self.collected_at,
            "report_times": {f"{s}|{p}": (None if r is None else {"at": r.at, "timing": r.timing}) for (s, p), r in sorted(self.report_times.items())},
            "current": {f"{s}|{p}|{f}": (None if v is None else {"value": v.value, "basis": v.basis}) for (s, p, f), v in sorted(self.current.items())},
            "as_of": {f"{s}|{p}|{f}": [[t, v] for t, v in series] for (s, p, f), series in sorted(self.as_of.items())},
            "as_of_unsupported": [f"{s}|{p}|{f}" for s, p, f in sorted(self.as_of_unsupported)],
        })


@dataclass(frozen=True)
class CheckResult:
    check: str
    status: str
    n: int
    detail: Dict[str, Any]


@dataclass(frozen=True)
class FieldGrade:
    field: str
    grade: str                        # A | C | X
    reasons: Tuple[str, ...]


@dataclass(frozen=True)
class TrialReport:
    provider_id: str
    policy_version: str
    snapshot_digests: Tuple[str, ...]
    checks: Tuple[CheckResult, ...]
    grades: Tuple[FieldGrade, ...]
    blockers: Tuple[str, ...]
    adoptable_for_research: bool
    adoptable_for_publication: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider_id": self.provider_id, "policy_version": self.policy_version, "snapshot_digests": list(self.snapshot_digests),
            "checks": [{"check": c.check, "status": c.status, "n": c.n, "detail": c.detail} for c in self.checks],
            "grades": [{"field": g.field, "grade": g.grade, "reasons": list(g.reasons)} for g in self.grades],
            "blockers": list(self.blockers), "adoptable_for_research": self.adoptable_for_research,
            "adoptable_for_publication": self.adoptable_for_publication,
        }

    def report_hash(self) -> str:
        return value_hash(self.to_dict())

    def grade(self, f: str) -> str:
        return next(g.grade for g in self.grades if g.field == f)

    def check(self, name: str) -> CheckResult:
        return next(c for c in self.checks if c.check == name)


# ------------------------------------------------------------------ samples
def _utc(dt: datetime, what: str) -> datetime:
    if not isinstance(dt, datetime) or dt.tzinfo is None:
        raise TrialError(f"{what} must be a timezone-aware datetime")
    return dt.astimezone(timezone.utc)


def validate_samples(samples: Sequence[Sample], policy: TrialPolicy = TrialPolicy()) -> List[str]:
    """Shortfalls against the section 6 sample requirements. Duplicates and malformed rows raise; shortfalls are returned, never hidden."""
    seen = set()
    for s in samples:
        if not isinstance(s.period, date) or not s.symbol:
            raise TrialError("a sample needs a symbol and a fiscal period date")
        if (s.symbol, s.period) in seen:
            raise TrialError(f"duplicate sample {s.symbol} {s.period}")
        seen.add((s.symbol, s.period))
        if any(t not in TAGS for t in s.tags):
            raise TrialError(f"unknown sample tag in {s.tags}")
        for a in s.as_of_dates:
            _utc(a, "as_of")
    out = []
    if len(samples) < policy.min_symbol_quarters:
        out.append(f"only {len(samples)} symbol-quarters; need {policy.min_symbol_quarters}")
    quarters = {s.period for s in samples}
    if len(quarters) < policy.min_distinct_quarters:
        out.append(f"only {len(quarters)} distinct quarters; need {policy.min_distinct_quarters}")
    for t in TAGS:
        if not any(t in s.tags for s in samples):
            out.append(f"no sample tagged '{t}'")
    return out


# ------------------------------------------------------------------ collection (the only place the adapter is called)
def collect_snapshot(adapter: ProviderAdapter, samples: Sequence[Sample], clock: Callable[[], datetime],
                     perf_counter: Callable[[], float]) -> Snapshot:
    pid = getattr(adapter, "provider_id", None)
    if not isinstance(pid, str) or not _PROVIDER_RE.match(pid):
        raise TrialError("adapter.provider_id must be a short lowercase label")
    validate_samples(samples)
    collected_at = _utc(clock(), "clock()")
    report_times: Dict[Tuple[str, date], Optional[ReportTime]] = {}
    current: Dict[Key, Optional[Value]] = {}
    as_of: Dict[Key, Tuple[Tuple[datetime, Optional[float]], ...]] = {}
    unsupported: List[Key] = []
    errors: Dict[str, int] = {}
    calls, worst = 0, 0.0

    def call(fn, *a):
        nonlocal calls, worst
        calls += 1
        t0 = perf_counter()
        try:
            return fn(*a)
        except AsOfUnsupported:
            raise
        except Exception as exc:                      # recorded, never turned into "no data"
            errors[type(exc).__name__] = errors.get(type(exc).__name__, 0) + 1
            return _ERR
        finally:
            worst = max(worst, perf_counter() - t0)

    for s in samples:
        rt = call(adapter.report_time, s.symbol, s.period)
        report_times[(s.symbol, s.period)] = None if rt is _ERR else rt
        for f in ALL_FIELDS:
            v = call(adapter.current_value, s.symbol, s.period, f)
            if v is not _ERR:
                current[(s.symbol, s.period, f)] = v
        for f in AS_OF_FIELDS:
            key, series = (s.symbol, s.period, f), []
            try:
                for a in s.as_of_dates:
                    got = call(adapter.value_as_of, s.symbol, s.period, f, _utc(a, "as_of"))
                    if got is not _ERR:
                        series.append((_utc(a, "as_of"), got))
            except AsOfUnsupported:
                unsupported.append(key)
                continue
            if series:
                as_of[key] = tuple(series)
    return Snapshot(pid, collected_at, report_times, current, as_of, tuple(unsupported), dict(errors), calls, worst)


_ERR = object()


# ------------------------------------------------------------------ the checks
def _bucket(at: datetime) -> str:
    et = at.astimezone(_ET)
    mins = et.hour * 60 + et.minute
    return "BMO" if mins < 9 * 60 + 30 else ("AMC" if mins >= 16 * 60 else "DMH")


def _close(a: float, b: float, p: TrialPolicy) -> bool:
    return abs(a - b) <= max(p.abs_tol, p.rel_tol * max(abs(a), abs(b)))


def check_report_time(snap: Snapshot, ref: Reference, p: TrialPolicy, shortfalls: Sequence[str]) -> CheckResult:
    later = label_bad = compared = no_vendor = no_edgar = 0
    for (sym, per), rt in snap.report_times.items():
        edgar = ref.edgar_accepted.get((sym, per))
        if edgar is None:
            no_edgar += 1
            continue
        if rt is None or (rt.at is None and rt.timing is None):
            no_vendor += 1
            continue
        compared += 1
        e = _utc(edgar, "edgar acceptance")
        if rt.at is not None and _utc(rt.at, "vendor report time") > e:
            later += 1
        if rt.timing is not None:
            expect = _bucket(_utc(rt.at, "vendor report time")) if rt.at is not None else _bucket(e)
            label_bad += rt.timing != expect
    d = {"compared": compared, "vendor_later_than_edgar": later, "label_disagreements": label_bad, "no_vendor_time": no_vendor,
         "no_edgar_reference": no_edgar}
    if compared == 0 or shortfalls:
        return CheckResult("report_time", INCONCLUSIVE, compared, {**d, "shortfalls": list(shortfalls)})
    bad = later / compared > p.max_later_than_edgar_rate or label_bad / compared > p.max_label_disagreement_rate
    return CheckResult("report_time", FAIL if bad else PASS, compared, d)


def check_as_of(snap: Snapshot, other_ref: Reference, p: TrialPolicy, f: str, shortfalls: Sequence[str]) -> CheckResult:
    keys = [k for k in snap.as_of if k[2] == f]
    unsupported = [k for k in snap.as_of_unsupported if k[2] == f]
    with_values = varying = ref_n = ref_ok = 0
    for k in keys:
        vals = [v for _, v in snap.as_of[k] if v is not None]
        if len(vals) < 2:
            continue
        with_values += 1
        varying += len(set(vals)) > 1
        for at, v in snap.as_of[k]:
            own = [(t, x) for t, x in other_ref.own_first_seen.get(k, ()) if _utc(t, "own observed_at") <= at]
            if v is None or not own:
                continue
            x = max(own, key=lambda tv: _utc(tv[0], "own observed_at"))[1]
            if x is not None:
                ref_n += 1
                ref_ok += _close(v, x, p)
    d = {"unsupported_keys": len(unsupported), "keys_with_two_values": with_values, "varying_keys": varying,
         "reference_points": ref_n, "reference_matches": ref_ok}
    if with_values == 0 and unsupported:
        return CheckResult(f"as_of:{f}", FAIL, 0, {**d, "why": "provider cannot answer as-of: history is restated or absent"})
    if with_values == 0 or shortfalls:
        return CheckResult(f"as_of:{f}", INCONCLUSIVE, with_values, {**d, "shortfalls": list(shortfalls)})
    if varying / with_values < p.min_varying_share:
        return CheckResult(f"as_of:{f}", FAIL, with_values, {**d, "why": "same value for (almost) every as-of date: restated"})
    if ref_n < p.min_reference_points:
        return CheckResult(f"as_of:{f}", INCONCLUSIVE, with_values, {**d, "why": "too few own first-seen points to confirm as-of"})
    ok = ref_ok / ref_n >= p.min_reference_match
    return CheckResult(f"as_of:{f}", PASS if ok else FAIL, with_values, d)


def check_restatement(first: Snapshot, later: Optional[Snapshot], p: TrialPolicy, f: str) -> CheckResult:
    if later is None:
        return CheckResult(f"restatement:{f}", INCONCLUSIVE, 0, {"why": "no second pull"})
    if later.provider_id != first.provider_id:
        raise TrialError("restatement compares two pulls of the same provider")
    gap = _utc(later.collected_at, "later.collected_at") - _utc(first.collected_at, "first.collected_at")
    both = [k for k in first.current if k[2] == f and k in later.current]
    changed = []
    for k in both:
        a, b = first.current[k], later.current[k]
        av, bv = (None if a is None else a.value), (None if b is None else b.value)
        if (av is None) != (bv is None) or (av is not None and not _close(av, bv, p)):
            changed.append(k)
    d = {"gap_days": gap.total_seconds() / 86400, "keys_compared": len(both), "silently_changed": len(changed),
         "examples": [f"{s}|{pe}" for s, pe, _ in sorted(changed)[:5]]}
    if gap < p.min_restatement_gap or len(both) < p.min_restatement_overlap:
        return CheckResult(f"restatement:{f}", FAIL if changed else INCONCLUSIVE, len(both), {**d, "why": "gap or sample too small to pass"})
    return CheckResult(f"restatement:{f}", FAIL if changed else PASS, len(both), d)


def check_actuals(snap: Snapshot, ref: Reference, p: TrialPolicy) -> CheckResult:
    by_basis: Dict[str, Dict[str, int]] = {}
    for (sym, per, f), v in snap.current.items():
        if f not in ACTUAL_FIELDS or v is None or v.value is None:
            continue
        x = ref.xbrl_actuals.get((sym, per, f))
        if x is None:
            continue
        b = by_basis.setdefault(v.basis if v.basis in BASES else "unknown", {"n": 0, "match": 0})
        b["n"] += 1
        b["match"] += _close(v.value, x, p)
    gaap = by_basis.get("gaap", {"n": 0, "match": 0})
    d = {"by_basis": by_basis, "reference": "sec_xbrl_gaap"}
    if gaap["n"] == 0:
        return CheckResult("actuals", INCONCLUSIVE, 0, {**d, "why": "no vendor value stated as gaap to reconcile against xbrl"})
    return CheckResult("actuals", PASS if gaap["match"] / gaap["n"] >= p.min_actual_match else FAIL, gaap["n"], d)


def check_coverage(snap: Snapshot, samples: Sequence[Sample], p: TrialPolicy) -> CheckResult:
    n = len(samples)
    share = {}
    for f in ALL_FIELDS:
        have = sum(1 for s in samples if (v := snap.current.get((s.symbol, s.period, f))) is not None and v.value is not None)
        share[f] = (have / n) if n else 0.0
    low = sorted(f for f, v in share.items() if v < p.min_coverage)
    return CheckResult("coverage", PASS if not low else FAIL, n, {"share": share, "below_policy": low})


def check_operational(op: Operational, snap: Snapshot) -> CheckResult:
    d = {"sustained_rps": op.sustained_rps, "universe_size": op.universe_size, "outage_behaviour": op.outage_behaviour,
         "pagination_ok": op.pagination_ok, "auth_documented": op.auth_documented, "errors_seen": dict(snap.errors),
         "max_latency_s": snap.max_latency_s, "calls": snap.calls}
    if op.outage_behaviour not in OUTAGE_BEHAVIOURS:
        raise TrialError(f"outage_behaviour must be one of {OUTAGE_BEHAVIOURS}")
    if op.outage_behaviour == "silent_empty" or op.pagination_ok is False:
        return CheckResult("operational", FAIL, snap.calls, d)
    if None in (op.sustained_rps, op.universe_size, op.pagination_ok, op.auth_documented) or op.outage_behaviour == "unknown":
        return CheckResult("operational", INCONCLUSIVE, snap.calls, d)
    return CheckResult("operational", PASS, snap.calls, d)


def check_licence(lic: Licence) -> CheckResult:
    bad = [k for k in lic.answers if k not in LICENCE_QUESTIONS or lic.answers[k] not in LICENCE_ANSWERS]
    if bad:
        raise TrialError(f"unknown licence question/answer: {bad}")
    unanswered = [q for q in LICENCE_QUESTIONS if q not in lic.answers]
    d = {"answers": dict(lic.answers), "unanswered": unanswered, "document_ref": lic.document_ref, "recorded_by": lic.recorded_by}
    if unanswered or not lic.document_ref or not lic.recorded_by:
        return CheckResult("licence", INCONCLUSIVE, len(lic.answers), d)
    return CheckResult("licence", PASS if all(v == "permitted" for v in lic.answers.values()) else FAIL, len(lic.answers), d)


# ------------------------------------------------------------------ verdict
def evaluate(first: Snapshot, later: Optional[Snapshot], samples: Sequence[Sample], ref: Reference,
             operational: Operational, licence: Licence, policy: TrialPolicy = TrialPolicy()) -> TrialReport:
    samples = list(samples)
    shortfalls = validate_samples(samples, policy)
    size_short = [s for s in shortfalls if s.startswith("only")]
    rt = check_report_time(first, ref, policy, shortfalls)
    as_of = {f: check_as_of(first, ref, policy, f, size_short) for f in AS_OF_FIELDS}
    restate = {f: check_restatement(first, later, policy, f) for f in ALL_FIELDS}
    checks = [rt, *as_of.values(), *restate.values(), check_actuals(first, ref, policy), check_coverage(first, samples, policy),
              check_operational(operational, first), check_licence(licence)]
    grades = []
    for f in ALL_FIELDS:
        needed = [rt, restate[f]] + ([as_of[f]] if f in AS_OF_FIELDS else [])
        reasons = tuple(f"{c.check}:{c.status}" for c in needed if c.status != PASS)
        if rt.status == FAIL:
            g = "X"
        elif any(c.status == FAIL for c in needed):
            g = "C"
        elif any(c.status == INCONCLUSIVE for c in needed):
            g = "X"
        else:
            g = "A"
        grades.append(FieldGrade(f, g, reasons))
    by = {c.check: c for c in checks}
    blockers = list(shortfalls)
    blockers += [f"licence unanswered: {q}" for q in by["licence"].detail["unanswered"]]
    if by["licence"].status == INCONCLUSIVE and not by["licence"].detail["unanswered"]:
        blockers.append("licence answers lack a document reference / recorder")
    if by["operational"].status != PASS:
        blockers.append(f"operational {by['operational'].status}")
    any_a = any(g.grade == "A" for g in grades)
    ans = licence.answers
    research = any_a and not blockers and ans.get("store_history") == "permitted" and ans.get("derive_signals") == "permitted"
    publish = research and ans.get("publish_public_channel") == "permitted"
    return TrialReport(first.provider_id, policy.version, tuple(x.digest() for x in (first, later) if x is not None), tuple(checks),
                       tuple(grades), tuple(blockers), bool(research), bool(publish))
