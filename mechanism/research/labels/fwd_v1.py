"""fwd_v1: what happened after T0. Pure functions -- no database, no clock, no environment.

Given one observation, an explicit trading-session calendar, the stored daily bars and an explicit `as_of_session`
(the latest COMPLETED session the caller vouches for), `compute()` returns exactly one of:

    Pending(reason)  nothing may be written yet (horizon not reached, or inputs not usable yet and the grace period
                     has not elapsed). A pending horizon has no row anywhere, so a partial label cannot be consumed.
    Label(final)     the horizon session has completed and every input needed for the return was usable.
    Label(void)      terminal: the grace period passed and the inputs were still unusable. No outcome is invented.

Semantics (docs/research/FWD_V1_FORWARD_OUTCOME_ENGINE.md is the contract; this module is its implementation):

  * T0 is the observation's session; the reference price is the T0 close AS STORED when the label is computed.
  * Horizon N is the Nth session AFTER T0 in the supplied calendar (sessions, never calendar days).
  * A label is determinable only when horizon_session <= as_of_session. Bars dated after the horizon session are
    ignored even when present, so the value and the input hash cannot depend on data from beyond the horizon.
  * raw_return = close(horizon) / close(T0) - 1;  directional_return = direction * raw_return (+1 long, -1 short).
  * benchmark_return uses the same two dates on the benchmark series; excess_return = raw - benchmark;
    directional_excess_return = direction * excess_return.
  * MFE/MAE: over sessions T0+1 .. horizon (T0 itself excluded), highs taken as max(high, close) and lows as
    min(low, close); long: MFE = max(0, max_high/ref - 1), MAE = min(0, min_low/ref - 1);
    short: MFE = max(0, 1 - min_low/ref), MAE = min(0, 1 - max_high/ref). A missing session in the path makes both NULL.
  * A split-like jump between consecutive stored closes inside [T0, horizon] means the stored series mixes price bases:
    the label is unusable (pending, then void) -- never silently accepted.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from alerts.price_guard import is_split_like

LABEL_VERSION = "fwd_v1"
METHODOLOGY_VERSION = "fwd_v1.m1"
HORIZONS: Tuple[int, ...] = (1, 3, 5, 10, 20, 60)
VOID_GRACE_SESSIONS = 3          # sessions after the horizon during which unusable inputs may still be repaired by the data pipeline
BASIS_TOLERANCE = 1e-4           # |entry_close / stored_T0_close - 1| above this => data_quality 'basis_adjusted'
PRICE_SOURCE = "stock_prices"
PRICE_BASIS = "provider_adjusted_as_stored"
CODE_REF = "mechanism/research/labels/fwd_v1.py@" + METHODOLOGY_VERSION

MAX_CALENDAR_GAP_DAYS = 4        # Friday -> Tuesday (Monday holiday) and Thursday -> Monday (Good Friday) are the longest normal gaps

# void reasons, in priority order
REASON_MISSING_REFERENCE = "missing_reference_bar"
REASON_MISSING_HORIZON = "missing_horizon_bar"
REASON_INVALID_BAR = "invalid_bar"
REASON_DISCONTINUITY = "split_like_discontinuity"
VOID_REASONS = (REASON_MISSING_REFERENCE, REASON_MISSING_HORIZON, REASON_INVALID_BAR, REASON_DISCONTINUITY)


class CalendarError(ValueError):
    """The supplied trading-session calendar cannot be trusted. Fail closed: never label on a doubtful calendar."""


@dataclass(frozen=True)
class Bar:
    session: date
    close: float
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None


@dataclass(frozen=True)
class Observation:
    id: int
    symbol: str
    direction: int
    t0_session: date
    entry_close: float


@dataclass(frozen=True)
class Pending:
    reason: str


@dataclass(frozen=True)
class Label:
    observation_id: int
    horizon_sessions: int
    label_status: str                       # final | void
    void_reason: Optional[str]
    symbol: str
    direction: int
    t0_session: date
    horizon_session: date
    computed_as_of_session: date
    reference_close: Optional[float]
    entry_close_captured: Optional[float]
    basis_ratio: Optional[float]
    horizon_close: Optional[float]
    raw_return: Optional[float]
    directional_return: Optional[float]
    benchmark_symbol: str
    benchmark_state: str                    # ok | unavailable | not_evaluated
    benchmark_return: Optional[float]
    excess_return: Optional[float]
    directional_excess_return: Optional[float]
    path_state: str                         # complete | incomplete | not_evaluated
    mfe: Optional[float]
    mae: Optional[float]
    bars_expected: int
    bars_observed: Optional[int]
    data_quality: str                       # ok | basis_adjusted | not_evaluated
    dq_details: Dict[str, Any]
    input_hash: str
    calendar_source: str
    label_version: str = LABEL_VERSION
    methodology_version: str = METHODOLOGY_VERSION
    price_source: str = PRICE_SOURCE
    price_basis: str = PRICE_BASIS
    code_ref: str = CODE_REF

    @property
    def is_final(self) -> bool:
        return self.label_status == "final"


# ---------------------------------------------------------------------------------------------- calendar
def validate_calendar(sessions: Sequence[date]) -> Tuple[date, ...]:
    """Strictly increasing weekday dates with no gap longer than MAX_CALENDAR_GAP_DAYS. Anything else raises: a calendar that
    silently drops or invents a session shifts every horizon after it."""
    out = tuple(sessions)
    if not out:
        raise CalendarError("empty trading-session calendar")
    for d in out:
        if not isinstance(d, date):
            raise CalendarError(f"calendar entry is not a date: {d!r}")
        if d.weekday() >= 5:
            raise CalendarError(f"calendar contains a weekend date: {d}")
    for a, b in zip(out, out[1:]):
        if b <= a:
            raise CalendarError(f"calendar is not strictly increasing at {a} -> {b}")
        if (b - a).days > MAX_CALENDAR_GAP_DAYS:
            raise CalendarError(f"calendar gap of {(b - a).days} days between {a} and {b}: a session is probably missing")
    return out


def cross_check_calendar(sessions: Sequence[date], authoritative: Mapping[date, Any]) -> None:
    """Where an authoritative calendar (e.g. Alpaca's) overlaps the supplied one, they must agree exactly."""
    if not authoritative:
        return
    lo, hi = min(authoritative), max(authoritative)
    ours = {d for d in sessions if lo <= d <= hi}
    theirs = set(authoritative)
    if ours != theirs:
        raise CalendarError(f"calendar disagrees with the authoritative one on {sorted(ours ^ theirs)[:5]}")


def horizon_session(sessions: Sequence[date], t0: date, n: int) -> Optional[date]:
    """The Nth session strictly after t0, or None when t0 is not a session or the calendar does not reach it."""
    try:
        i = list(sessions).index(t0)
    except ValueError:
        return None
    return sessions[i + n] if i + n < len(sessions) else None


# ---------------------------------------------------------------------------------------------- helpers
def _ok_price(x: Any) -> bool:
    return x is not None and isinstance(x, (int, float)) and math.isfinite(x) and x > 0


def _bar_problem(b: Bar) -> Optional[str]:
    if not _ok_price(b.close):
        return "close"
    for name in ("open", "high", "low"):
        v = getattr(b, name)
        if v is not None and not _ok_price(v):
            return name
    if b.high is not None and b.low is not None and b.high < b.low:
        return "high<low"
    return None


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False,
                      default=lambda o: o.isoformat() if isinstance(o, date) else float(o))


def input_hash(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical(payload).encode()).hexdigest()


def _h(x: Any) -> Any:
    """JSON-safe value for hashing: a non-finite float (NaN/inf from a bad feed) is hashed as its text, never raised on."""
    return repr(x) if isinstance(x, float) and not math.isfinite(x) else x


def _hash_payload(obs: Observation, horizon: int, h_session: date, window: Sequence[date], bars: Mapping[date, Bar],
                  bench: Optional[Mapping[date, float]], benchmark_symbol: str) -> Dict[str, Any]:
    b_rows = []
    for d in window:
        b = bars.get(d)
        b_rows.append([d, None if b is None else [_h(b.open), _h(b.high), _h(b.low), _h(b.close)]])
    bench_rows = [[d, None if bench is None else _h(bench.get(d))] for d in (window[0], window[-1])]
    return {"label_version": LABEL_VERSION, "methodology": METHODOLOGY_VERSION, "observation_id": obs.id,
            "symbol": obs.symbol, "direction": obs.direction, "t0": obs.t0_session, "entry_close": obs.entry_close,
            "horizon": horizon, "horizon_session": h_session, "window": list(window), "bars": b_rows,
            "benchmark_symbol": benchmark_symbol, "benchmark": bench_rows}


def _void(obs: Observation, horizon: int, h_session: date, as_of: date, reason: str, details: Dict[str, Any],
          ihash: str, benchmark_symbol: str, calendar_source: str, bars_observed: Optional[int]) -> Label:
    return Label(observation_id=obs.id, horizon_sessions=horizon, label_status="void", void_reason=reason, symbol=obs.symbol,
                 direction=obs.direction, t0_session=obs.t0_session, horizon_session=h_session, computed_as_of_session=as_of,
                 reference_close=None, entry_close_captured=None, basis_ratio=None, horizon_close=None, raw_return=None,
                 directional_return=None, benchmark_symbol=benchmark_symbol, benchmark_state="not_evaluated",
                 benchmark_return=None, excess_return=None, directional_excess_return=None, path_state="not_evaluated",
                 mfe=None, mae=None, bars_expected=horizon, bars_observed=bars_observed, data_quality="not_evaluated",
                 dq_details=details, input_hash=ihash, calendar_source=calendar_source)


# ---------------------------------------------------------------------------------------------- the computation
def compute(obs: Observation, horizon: int, sessions: Sequence[date], bars: Mapping[date, Bar],
            bench: Optional[Mapping[date, float]], as_of_session: date, *, benchmark_symbol: str = "^GSPC",
            calendar_source: str = "explicit") -> "Pending | Label":
    if horizon not in HORIZONS:
        raise ValueError(f"horizon {horizon} is not one of {HORIZONS}")
    if obs.direction not in (1, -1):
        raise ValueError("direction must be +1 or -1")
    cal = validate_calendar(sessions)
    if as_of_session not in cal:
        raise CalendarError(f"as_of_session {as_of_session} is not a session of the calendar")
    if obs.t0_session not in cal:
        return Pending("t0_not_in_calendar")
    i0 = cal.index(obs.t0_session)
    if i0 + horizon >= len(cal):
        return Pending("horizon_not_reached")           # as_of is a calendar session, so a horizon past the calendar is past as_of
    h_session = cal[i0 + horizon]
    if h_session > as_of_session:
        return Pending("horizon_not_reached")
    window = cal[i0:i0 + horizon + 1]                       # T0 .. horizon, nothing after
    grace_idx = i0 + horizon + VOID_GRACE_SESSIONS
    grace_elapsed = grace_idx < len(cal) and cal[grace_idx] <= as_of_session

    in_win = {d: bars[d] for d in window if d in bars}
    ihash = input_hash(_hash_payload(obs, horizon, h_session, window, bars, bench, benchmark_symbol))

    # ---- inputs that make the return itself undefined or untrustworthy
    problems: List[Tuple[str, Dict[str, Any]]] = []
    if obs.t0_session not in in_win:
        problems.append((REASON_MISSING_REFERENCE, {"missing": obs.t0_session.isoformat()}))
    if h_session not in in_win:
        problems.append((REASON_MISSING_HORIZON, {"missing": h_session.isoformat()}))
    bad = {d.isoformat(): _bar_problem(b) for d, b in in_win.items() if _bar_problem(b)}
    if bad:
        problems.append((REASON_INVALID_BAR, {"bars": bad}))
    ordered = [in_win[d] for d in window if d in in_win and not _bar_problem(in_win[d])]
    jumps = [{"from": a.session.isoformat(), "to": b.session.isoformat(), "ratio": b.close / a.close}
             for a, b in zip(ordered, ordered[1:]) if is_split_like(b.close / a.close)]
    if jumps:
        problems.append((REASON_DISCONTINUITY, {"jumps": jumps}))
    if problems:
        problems.sort(key=lambda p: VOID_REASONS.index(p[0]))
        reason, details = problems[0]
        if not grace_elapsed:
            return Pending("awaiting_inputs:" + reason)
        details = dict(details, all_reasons=[p[0] for p in problems],
                       last_bar=max((d.isoformat() for d in in_win), default=None))
        return _void(obs, horizon, h_session, as_of_session, reason, details, ihash, benchmark_symbol, calendar_source,
                     sum(1 for d in window[1:] if d in in_win))

    # ---- benchmark: a gap is not fatal, but a lagging benchmark feed must not be frozen in as "unavailable"
    b0 = None if bench is None else bench.get(window[0])
    bn = None if bench is None else bench.get(h_session)
    bench_ok = _ok_price(b0) and _ok_price(bn)
    if not bench_ok and not grace_elapsed:
        return Pending("awaiting_benchmark")

    ref = in_win[obs.t0_session].close
    hz = in_win[h_session].close
    raw = hz / ref - 1.0
    directional = obs.direction * raw
    basis_ratio = obs.entry_close / ref
    basis_adjusted = abs(basis_ratio - 1.0) > BASIS_TOLERANCE

    benchmark_return = excess = d_excess = None
    if bench_ok:
        benchmark_return = bn / b0 - 1.0
        excess = raw - benchmark_return
        d_excess = obs.direction * excess

    post = [d for d in window[1:]]
    present = [in_win[d] for d in post if d in in_win]
    complete = len(present) == horizon and all(b.high is not None and b.low is not None for b in present)
    mfe = mae = None
    if complete:
        hi = max(max(b.high, b.close) for b in present)
        lo = min(min(b.low, b.close) for b in present)
        if obs.direction == 1:
            mfe, mae = max(0.0, hi / ref - 1.0), min(0.0, lo / ref - 1.0)
        else:
            mfe, mae = max(0.0, 1.0 - lo / ref), min(0.0, 1.0 - hi / ref)

    details: Dict[str, Any] = {"missing_path_sessions": [d.isoformat() for d in post if d not in in_win]}
    if basis_adjusted:
        details["basis_note"] = ("stored T0 close differs from the close captured at T0: the stored history was restated "
                                 "(split/dividend adjustment); every return here uses the stored basis consistently")
    if not bench_ok:
        details["benchmark_missing"] = [d.isoformat() for d, v in ((window[0], b0), (h_session, bn)) if not _ok_price(v)]
    return Label(observation_id=obs.id, horizon_sessions=horizon, label_status="final", void_reason=None, symbol=obs.symbol,
                 direction=obs.direction, t0_session=obs.t0_session, horizon_session=h_session,
                 computed_as_of_session=as_of_session, reference_close=ref, entry_close_captured=obs.entry_close,
                 basis_ratio=basis_ratio, horizon_close=hz, raw_return=raw, directional_return=directional,
                 benchmark_symbol=benchmark_symbol, benchmark_state="ok" if bench_ok else "unavailable",
                 benchmark_return=benchmark_return, excess_return=excess, directional_excess_return=d_excess,
                 path_state="complete" if complete else "incomplete", mfe=mfe, mae=mae, bars_expected=horizon,
                 bars_observed=len(present), data_quality="basis_adjusted" if basis_adjusted else "ok",
                 dq_details=details, input_hash=ihash, calendar_source=calendar_source)
