"""Forward paper-trade tracker (research only): parallel PAPER observations of the registered strategy variants. PURE: stdlib only; no database, broker, clock, network or environment.

A paper observation is bookkeeping about what a variant's exit rule WOULD have done after a parent-strategy signal. It opens no order, touches no ledger row, and changes no live signal.
Design rules:
  * ONE exit implementation: every position is an `exit_policies.OpenPosition` stepped by `exit_policies.advance`, the same code the batch experiment runner (`simulate`) loops over, so a forward
    observation cannot disagree with the historical experiment (tests replay real histories day by day and compare to the batch results);
  * independent state: each (variant, symbol, direction) observation owns its position state; nothing is shared between variants, so a variant's results never depend on which others are tracked;
  * the live ledger's rule, per variant: at most one open (or pending) observation per (variant, symbol, direction); a signal on or before the session its previous observation closed is skipped;
  * sessions are EXPLICIT dates passed by the caller and must increase; replaying a session that was already processed is a no-op. Nothing here reads a wall clock;
  * point-in-time: a signal is registered at the close of session S with the channel window of bars <= S supplied by the caller; the entry is the OPEN of the symbol's next bar; later sessions only
    ever move the position forward;
  * the whole book serialises to a plain dict (`to_state`) and restores exactly (`from_state`), so a future persistence layer needs no tracker changes.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from research import exit_policies as X
from research.experiment_runner import Variant

PENDING, OPEN, CLOSED = "pending_entry", "open", "closed"


class TrackerError(RuntimeError):
    pass


@dataclass
class Observation:
    variant_key: str
    definition_hash: str
    policy_name: str
    symbol: str
    direction: int
    signal_date: date
    atr: float
    hold_cap: int
    registered_cap: bool
    pending_hist_h: List[float]
    pending_hist_l: List[float]
    status: str = PENDING
    position: Optional[X.OpenPosition] = None
    entry_date: Optional[date] = None
    exit_date: Optional[date] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    trade: Optional[X.Trade] = None
    stale_sessions: int = 0                     # sessions in which this symbol had no bar while the observation was open (the position did not move)


def _d(x: Optional[date]) -> Optional[str]:
    return x.isoformat() if x else None


class PaperBook:
    """All paper observations of a set of variants, advanced one explicit session at a time."""

    def __init__(self, variants: Sequence[Variant], *, hold_cap: Optional[int] = None):
        if not variants:
            raise TrackerError("no variants")
        self.variants: Dict[str, Variant] = {v.key: v for v in variants}
        if len(self.variants) != len(variants):
            raise TrackerError("duplicate variant key")
        self.hold_cap_override = hold_cap
        self.last_session: Optional[date] = None
        self._active: Dict[Tuple[str, str, int], Observation] = {}
        self._closed: List[Observation] = []
        self._last_exit: Dict[Tuple[str, str, int], date] = {}
        self._session_no = 0

    # ---------------------------------------------------------------------------------------------------------------------------------------- inputs
    def register_signal(self, symbol: str, direction: int, signal_date: date, atr: float, hist_h: Sequence[float], hist_l: Sequence[float]) -> Dict[str, str]:
        """A parent-strategy signal at the close of `signal_date` (which must be the latest processed session). One paper observation is created for EVERY tracked variant unless that variant already
        has an open/pending observation for (symbol, direction) or closed one on/after `signal_date`. Returns variant_key -> 'registered' | 'skipped_open_position'."""
        if self.last_session is not None and signal_date != self.last_session:
            raise TrackerError(f"a signal for {signal_date} must be registered right after session {self.last_session} is processed, not {signal_date}")
        d = 1 if direction > 0 else -1
        out: Dict[str, str] = {}
        for key, v in self.variants.items():
            k = (key, symbol, d)
            if k in self._active or self._last_exit.get(k, date.min) >= signal_date:
                out[key] = "skipped_open_position"
                continue
            cap = self.hold_cap_override or v.registered_max_hold
            self._active[k] = Observation(key, v.definition_hash, v.policy_name, symbol, d, signal_date, float(atr), int(cap), cap == v.registered_max_hold,
                                          [float(x) for x in hist_h], [float(x) for x in hist_l])
            out[key] = "registered"
        return out

    def on_session(self, session_date: date, bars: Mapping[str, Tuple[float, float, float, float]]) -> List[Observation]:
        """Advance every observation by one session. `bars` maps symbol -> (open, high, low, close) for the symbols that traded. Pending observations enter at the open of their symbol's next bar; open
        ones process the bar. Returns the observations that closed in this session. Re-sending the last processed session changes nothing; an earlier one is an error."""
        if self.last_session is not None:
            if session_date == self.last_session:
                return []
            if session_date < self.last_session:
                raise TrackerError(f"session {session_date} is before the last processed session {self.last_session}")
        self.last_session = session_date
        self._session_no += 1
        closed_now: List[Observation] = []
        for k in sorted(self._active):                     # a fixed order: the outcome never depends on dict ordering
            ob = self._active[k]
            bar = bars.get(ob.symbol)
            if bar is None:
                if ob.status == OPEN:
                    ob.stale_sessions += 1
                continue
            o, h, l, c = (float(x) for x in bar)
            if ob.status == PENDING:
                pos = X.open_position(ob.policy_name, ob.direction, o, ob.atr, ob.pending_hist_h, ob.pending_hist_l, max_hold=ob.hold_cap)
                if pos is None:
                    self._close(ob, session_date, None, "not_enterable")
                    closed_now.append(ob)
                    continue
                ob.position, ob.status, ob.entry_date = pos, OPEN, session_date
                ob.pending_hist_h, ob.pending_hist_l = [], []
            ex = X.advance(ob.position, o, h, l, c)
            if ex is not None:
                self._close(ob, session_date, ex, ex.reason)
                closed_now.append(ob)
        for ob in closed_now:
            self._active.pop((ob.variant_key, ob.symbol, ob.direction), None)
        return closed_now

    def _close(self, ob: Observation, session_date: date, ex: Optional[X.Exit], reason: str) -> None:
        ob.status, ob.exit_date, ob.exit_reason = CLOSED, session_date, reason
        if ex is not None:
            ob.exit_price = ex.price
            ob.trade = X.make_trade(ob.position, ex, 0, 0)
            self._last_exit[(ob.variant_key, ob.symbol, ob.direction)] = session_date      # a signal that could not be entered never blocked later ones (same as the batch runner)
        self._closed.append(ob)

    # ---------------------------------------------------------------------------------------------------------------------------------------- views
    def open_observations(self) -> List[Observation]:
        return [self._active[k] for k in sorted(self._active)]

    def closed_observations(self) -> List[Observation]:
        return list(self._closed)

    def summary(self) -> Dict[str, Any]:
        by: Dict[str, Dict[str, int]] = {k: {"pending": 0, "open": 0, "closed": 0} for k in self.variants}
        for ob in self._active.values():
            by[ob.variant_key]["pending" if ob.status == PENDING else "open"] += 1
        for ob in self._closed:
            by[ob.variant_key]["closed"] += 1
        return {"last_session": _d(self.last_session), "sessions_processed": self._session_no, "by_variant": by}

    # ---------------------------------------------------------------------------------------------------------------------------------------- persistence
    def to_state(self) -> Dict[str, Any]:
        def pos(p: Optional[X.OpenPosition]) -> Optional[Dict[str, Any]]:
            return dataclasses.asdict(p) if p else None

        def ob(o: Observation) -> Dict[str, Any]:
            return {"variant_key": o.variant_key, "definition_hash": o.definition_hash, "policy_name": o.policy_name, "symbol": o.symbol, "direction": o.direction,
                    "signal_date": _d(o.signal_date), "atr": o.atr, "hold_cap": o.hold_cap, "registered_cap": o.registered_cap, "pending_hist_h": o.pending_hist_h, "pending_hist_l": o.pending_hist_l,
                    "status": o.status, "position": pos(o.position), "entry_date": _d(o.entry_date), "exit_date": _d(o.exit_date), "exit_price": o.exit_price, "exit_reason": o.exit_reason,
                    "trade": dataclasses.asdict(o.trade) if o.trade else None, "stale_sessions": o.stale_sessions}
        return {"schema": "paper_book_state_v1", "last_session": _d(self.last_session), "session_no": self._session_no, "hold_cap_override": self.hold_cap_override,
                "variants": [{"key": v.key, "version": v.version, "definition_hash": v.definition_hash, "policy_name": v.policy_name, "registered_max_hold": v.registered_max_hold} for v in self.variants.values()],
                "active": [ob(self._active[k]) for k in sorted(self._active)], "closed": [ob(o) for o in self._closed],
                "last_exit": {"|".join([k[0], k[1], str(k[2])]): _d(v) for k, v in sorted(self._last_exit.items())}}

    def state_hash(self) -> str:
        return hashlib.sha256(json.dumps(self.to_state(), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()

    @classmethod
    def from_state(cls, state: Mapping[str, Any]) -> "PaperBook":
        if state.get("schema") != "paper_book_state_v1":
            raise TrackerError("unknown state schema")
        book = cls([Variant(v["key"], v["version"], v["definition_hash"], v["policy_name"], int(v["registered_max_hold"])) for v in state["variants"]], hold_cap=state["hold_cap_override"])
        book.last_session = date.fromisoformat(state["last_session"]) if state["last_session"] else None
        book._session_no = int(state["session_no"])

        def to_obs(s: Mapping[str, Any]) -> Observation:
            pos = X.OpenPosition(**s["position"]) if s["position"] else None
            return Observation(s["variant_key"], s["definition_hash"], s["policy_name"], s["symbol"], int(s["direction"]), date.fromisoformat(s["signal_date"]), float(s["atr"]), int(s["hold_cap"]),
                               bool(s["registered_cap"]), list(s["pending_hist_h"]), list(s["pending_hist_l"]), s["status"], pos,
                               date.fromisoformat(s["entry_date"]) if s["entry_date"] else None, date.fromisoformat(s["exit_date"]) if s["exit_date"] else None, s["exit_price"], s["exit_reason"],
                               X.Trade(**s["trade"]) if s["trade"] else None, int(s["stale_sessions"]))
        for s in state["active"]:
            o = to_obs(s)
            book._active[(o.variant_key, o.symbol, o.direction)] = o
        book._closed = [to_obs(s) for s in state["closed"]]
        for k, v in state["last_exit"].items():
            key, sym, d = k.split("|")
            book._last_exit[(key, sym, int(d))] = date.fromisoformat(v)
        return book
