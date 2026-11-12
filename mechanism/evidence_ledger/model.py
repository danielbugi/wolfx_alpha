"""Evidence Ledger v0, the pure model: what a discovery record holds, the price conventions, and the outcome of each horizon.

No database, no network, no wall clock (the capture time is passed in). docs/research/EVIDENCE_LEDGER.md.

Conventions (fixed; a change is a new LEDGER_VERSION):
  discovery    = the first time the ledger saw a company-quarter in Opportunity Intelligence, stamped with the ACTUAL capture time (captured_at).
                 'prospective' only when its first public day is the captured session or the session before it; otherwise 'late_first_capture'
                 (seen for the first time later than that, e.g. the backlog present when the ledger started). Late captures are reported
                 separately and never mixed into the prospective cohorts. Nothing is ever reconstructed backwards.
  delayed processing = a session the nightly capture did not run for (a missed or failed night) is processed on the ledger's next run, at most
                 MAX_DELAYED_SESSIONS sessions back, from the SAME point-in-time brief that night would have produced: every event in it was detected and
                 last changed by the system before the session's knowledge cutoff (database-stamped first_detected_at / updated_at), so the evidence is
                 what had been recorded at the time, not a reconstruction. Such a discovery is labelled 'delayed_processing' (never 'prospective'), keeps
                 its ACTUAL captured_at (never back-dated) next to evidence_cutoff and original_available_at, and its outcome clock still starts after
                 the actual capture. A session further back is recorded as 'missed': a genuine gap, with no observations manufactured for it.
  classification = the market response needs 3 sessions after the first public day, so a timely discovery is first captured with the market
                 dimension 'unavailable'. Its GROUP is set by the first later capture (a revision in the ledger) whose market window is complete;
                 until then it is 'awaiting_market_window'. The outcome clock starts after THAT capture, so the market response that defines the
                 group is never part of the outcome it is compared on. The discovery time and basis always come from the first capture.
  entry        = the close of the first session whose close (16:00 New York) is AFTER the classifying capture's captured_at. No information after the capture is used
                 to choose a company; nothing before the entry close is counted as outcome.
  horizon N    = N calendar days after the entry session; exit = the close of the last session on or before entry + N days. The window is
                 complete only once a session on or after entry + N days exists in the data.
  benchmarks   = the S&P 500 (^GSPC close) over the same entry -> exit; and, where the company's point-in-time sector is known, the MEDIAN
                 return of that sector's stocks with closes on both days (an equal-weight peer benchmark built from today's universe: it carries
                 survivorship of its own and is labelled so).
  every discovery stays in the denominator. A horizon is one of:
    pending        window not complete yet
    complete       entry and exit closes present, no distortion flag
    unreliable     a price discontinuity or a split-like (2x-20x) one-day move inside the window: counted, return not used
    ended_early    the stock has no close on or after the exit day although the window is complete (delisted, halted, or a data gap):
                   counted; return to its last close shown separately, never mixed into 'complete'
    no_entry       no close on the entry session (data gap): counted, no return
"""
from __future__ import annotations

import hashlib
import json
import statistics
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

LEDGER_VERSION = "evidence_ledger_v0"
NY = ZoneInfo("America/New_York")
HORIZONS_DAYS = (30, 90, 180)
SPLIT_FACTORS = (2.0, 3.0, 4.0, 5.0, 8.0, 10.0, 20.0)
SPLIT_TOLERANCE = 0.03

GROUP_POSITIVE = "business_improved_market_positive"
GROUP_NOT_POSITIVE = "business_improved_market_not_positive"
GROUP_UNREADABLE = "business_improved_market_unreadable"
GROUP_OTHER = "business_not_clearly_improved"
GROUP_AWAITING = "awaiting_market_window"
GROUPS = (GROUP_POSITIVE, GROUP_NOT_POSITIVE, GROUP_UNREADABLE, GROUP_OTHER, GROUP_AWAITING)
_CATEGORY_TO_GROUP = {"business_improved_market_confirmed": GROUP_POSITIVE, "business_improved_market_not_confirming": GROUP_NOT_POSITIVE,
                      "business_improved_market_unreadable": GROUP_UNREADABLE}

PROSPECTIVE, DELAYED, LATE = "prospective", "delayed_processing", "late_first_capture"
BASES = (PROSPECTIVE, DELAYED, LATE)
RUN_ON_TIME, RUN_DELAYED, RUN_MISSED = "on_time", "delayed_processing", "missed"
MAX_DELAYED_SESSIONS = 5
PENDING, COMPLETE, UNRELIABLE, ENDED_EARLY, NO_ENTRY = "pending", "complete", "unreliable", "ended_early", "no_entry"


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def sha(obj: Any) -> str:
    return hashlib.sha256(canonical(obj).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- discovery records
def observation_key(brief: Mapping[str, Any]) -> str:
    return f"{brief['symbol']}:{brief['quarter_end']}:{brief['filing'].get('accession') or 'na'}"


def evidence_payload(brief: Mapping[str, Any]) -> Dict[str, Any]:
    """The evidence AS SEEN at capture: compact, versioned and hashed (the full brief is reproducible from the same data, the hash proves which)."""
    a = brief["assessment"]
    mk = brief["evidence"]["market"]
    excess = next((f["value"] for f in mk.get("figures", []) if f["label"] == "Move relative to the S&P 500"), None)
    return {"brief_version": brief["version"], "symbol": brief["symbol"], "quarter_end": brief["quarter_end"], "filing": brief["filing"],
            "first_public": brief["first_public"], "category": a["category"], "dimensions": a["dimensions"],
            "business_improved": a["business_improved"], "business_worsened": a["business_worsened"], "market_excess_pp": excess,
            "contradictions": [c["text"] for c in brief["contradictions"]], "event_types": sorted(w["event_type"] for w in brief["what_changed"])}


def capture_basis(first_public: date, session: date, sessions: Sequence[date], delayed: bool = False) -> str:
    prior = [d for d in sessions if d < session]
    earliest = prior[-1] if prior else session
    if earliest <= first_public <= session:
        return DELAYED if delayed else PROSPECTIVE
    return LATE


def observation_record(brief: Mapping[str, Any], session: date, captured_at: datetime, sessions: Sequence[date], sector: Optional[Mapping[str, Any]],
                       delayed: bool = False, evidence_cutoff: Optional[str] = None) -> Dict[str, Any]:
    """captured_at is always the ACTUAL time of this capture. evidence_cutoff = the brief's knowledge cutoff (every event in it was detected and last changed
    by then); original_available_at = when the filing became public. A delayed record differs only in its label and these fields, never in its time."""
    ev = evidence_payload(brief)
    return {"key": observation_key(brief), "ledger_version": LEDGER_VERSION, "captured_at": captured_at.astimezone(timezone.utc).isoformat(),
            "session": session.isoformat(), "symbol": brief["symbol"], "group": _CATEGORY_TO_GROUP.get(ev["category"], GROUP_OTHER),
            "basis": capture_basis(date.fromisoformat(brief["first_public"]), session, sessions, delayed),
            "processing": RUN_DELAYED if delayed else RUN_ON_TIME, "evidence_cutoff": evidence_cutoff,
            "original_available_at": (brief.get("filing") or {}).get("accepted_at"),
            "sector": dict(sector) if sector else None, "evidence": ev, "evidence_hash": sha(ev)}


def capture_run_record(session: date, captured_at: datetime, mode: str, briefs: Optional[int] = None, evidence_cutoff: Optional[str] = None,
                       reason: Optional[str] = None) -> Dict[str, Any]:
    """One per session the ledger has dealt with: on_time | delayed_processing | missed (a genuine gap: no observations exist for it)."""
    assert mode in (RUN_ON_TIME, RUN_DELAYED, RUN_MISSED)
    return {"session": session.isoformat(), "mode": mode, "captured_at": captured_at.astimezone(timezone.utc).isoformat(), "briefs": briefs,
            "evidence_cutoff": evidence_cutoff, "reason": reason, "ledger_version": LEDGER_VERSION}


def plan_sessions(calendar: Sequence[date], latest: date, dealt_with: Iterable[date], max_delayed: int = MAX_DELAYED_SESSIONS):
    """Sessions after the newest one the ledger has dealt with and before `latest`: the newest `max_delayed` are processed late (delayed), older
    ones are recorded as missed. An empty ledger plans nothing (the ledger's start is not a gap)."""
    done = set(dealt_with)
    if not done:
        return [], []
    gap = sorted(d for d in calendar if max(done) < d < latest and d not in done)
    cut = max(len(gap) - max_delayed, 0)
    return gap[cut:], gap[:cut]


def coverage(calendar: Sequence[date], runs: Mapping[str, Mapping[str, Any]], legacy: Iterable[date]) -> Dict[str, Any]:
    """Per session from the ledger's first session: on_time / delayed_processing / missed / no_record (never dealt with yet)."""
    legacy = set(legacy)
    known = [date.fromisoformat(s) for s in runs] + list(legacy)
    if not known:
        return {"sessions": 0}
    out: Dict[str, List[str]] = {RUN_ON_TIME: [], RUN_DELAYED: [], RUN_MISSED: [], "no_record": []}
    for d in sorted(x for x in calendar if x >= min(known)):
        r = runs.get(d.isoformat())
        out[r["mode"] if r else (RUN_ON_TIME if d in legacy else "no_record")].append(d.isoformat())
    return {"sessions": sum(len(v) for v in out.values()), **{k: len(v) for k, v in out.items()},
            "missed_sessions": out[RUN_MISSED], "delayed_sessions": out[RUN_DELAYED], "no_record_sessions": out["no_record"]}


def classified(history: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """The record outcomes are measured from. `history` = the first capture and its revisions, in ledger order. The discovery (time, basis,
    session) is always the first capture; group, evidence and the outcome clock come from the first capture whose market window was complete."""
    first = history[0]
    for rec in history:
        if rec["evidence"]["dimensions"].get("market") != "unavailable":
            return dict(rec, basis=first["basis"], processing=first.get("processing", RUN_ON_TIME), discovered_at=first["captured_at"],
                        discovery_session=first["session"])
    return dict(first, group=GROUP_AWAITING if first["evidence"]["dimensions"]["business"] == "supports" else first["group"],
                processing=first.get("processing", RUN_ON_TIME), discovered_at=first["captured_at"], discovery_session=first["session"])


# ---------------------------------------------------------------- price conventions
def close_time(d: date) -> datetime:
    return datetime.combine(d, time(16, 0), tzinfo=NY)


def entry_session(captured_at: datetime, sessions: Sequence[date]) -> Optional[date]:
    return next((d for d in sessions if close_time(d) > captured_at), None)


def exit_session(entry: date, days: int, sessions: Sequence[date]) -> Optional[date]:
    """The last session on or before entry + days, provided the data already reaches that day (else None: window not complete)."""
    target = entry + timedelta(days=days)
    if not sessions or sessions[-1] < target:
        return None
    return max(d for d in sessions if d <= target)


def _split_like(ratio: float) -> bool:
    return any(abs(ratio - x) / x <= SPLIT_TOLERANCE for f in SPLIT_FACTORS for x in (f, 1 / f))


def _ret(a: Optional[float], b: Optional[float]) -> Optional[float]:
    return None if not a or b is None else (b / a - 1) * 100


@dataclass
class Outcome:
    horizon_days: int
    status: str
    entry: Optional[str] = None
    exit: Optional[str] = None
    stock_pct: Optional[float] = None
    sp500_pct: Optional[float] = None
    excess_vs_sp500_pp: Optional[float] = None
    sector_median_pct: Optional[float] = None
    excess_vs_sector_pp: Optional[float] = None
    sector_peers: int = 0
    note: str = ""


def outcome(obs: Mapping[str, Any], horizon: int, sessions: Sequence[date], bars: Mapping[date, float], bench: Mapping[date, float],
            discontinuities: Iterable[date] = (), sector_bars: Optional[Mapping[str, Mapping[date, float]]] = None) -> Outcome:
    captured = datetime.fromisoformat(obs["captured_at"])
    entry = entry_session(captured, sessions)
    if entry is None:
        return Outcome(horizon, PENDING, note="entry session has not closed yet")
    ex = exit_session(entry, horizon, sessions)
    if ex is None:
        return Outcome(horizon, PENDING, entry.isoformat(), note=f"window ends {(entry + timedelta(days=horizon)).isoformat()}")
    if not bars.get(entry):
        return Outcome(horizon, NO_ENTRY, entry.isoformat(), ex.isoformat(), note="no close on the entry session")
    window = [d for d in sessions if entry <= d <= ex]
    have = [d for d in window if bars.get(d)]
    if not bars.get(ex):
        last = have[-1]
        return Outcome(horizon, ENDED_EARLY, entry.isoformat(), ex.isoformat(), stock_pct=_ret(bars[entry], bars[last]),
                       note=f"no close on or after {ex.isoformat()}; last close {last.isoformat()} (delisted, halted or a data gap)")
    flags = [d.isoformat() for d in discontinuities if entry < d <= ex]
    flags += [cur.isoformat() for prev, cur in zip(have, have[1:]) if _split_like(bars[cur] / bars[prev])]
    s, i = _ret(bars[entry], bars[ex]), _ret(bench.get(entry), bench.get(ex))
    o = Outcome(horizon, COMPLETE, entry.isoformat(), ex.isoformat(), s, i, None if s is None or i is None else s - i)
    if sector_bars:
        rs = [r for r in (_ret(p.get(entry), p.get(ex)) for p in sector_bars.values()) if r is not None]
        if rs:
            o.sector_median_pct, o.sector_peers = statistics.median(rs), len(rs)
            o.excess_vs_sector_pp = None if s is None else s - o.sector_median_pct
    if flags:
        o.status, o.note = UNRELIABLE, "possible split or price discontinuity on " + ", ".join(sorted(set(flags)))
    elif i is None:
        o.note = "no S&P 500 close on the entry or exit day"
    return o


# ---------------------------------------------------------------- summary: every discovery in the denominator
def summarize(observations: Sequence[Mapping[str, Any]], outcomes: Mapping[str, Sequence[Outcome]], basis: str = PROSPECTIVE) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for g in GROUPS:
        members = [o for o in observations if o["group"] == g and o["basis"] == basis]
        per_h = {}
        for h in HORIZONS_DAYS:
            rows = [next(x for x in outcomes[o["key"]] if x.horizon_days == h) for o in members]
            done = [r for r in rows if r.status == COMPLETE and r.excess_vs_sp500_pp is not None]
            ex = [r.excess_vs_sp500_pp for r in done]
            sec = [r.excess_vs_sector_pp for r in done if r.excess_vs_sector_pp is not None]
            per_h[str(h)] = {"observed": len(rows), **{st: sum(r.status == st for r in rows) for st in (PENDING, COMPLETE, UNRELIABLE, ENDED_EARLY, NO_ENTRY)},
                             "median_excess_vs_sp500_pp": statistics.median(ex) if ex else None,
                             "share_above_sp500": (sum(x > 0 for x in ex) / len(ex)) if ex else None,
                             "median_excess_vs_sector_pp": statistics.median(sec) if sec else None, "with_sector_benchmark": len(sec)}
        out[g] = {"discoveries": len(members), "horizons": per_h}
    return out
