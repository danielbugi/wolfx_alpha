"""The budget guard. A paid call happens only after a reservation of its WORST-CASE cost fits the monthly cap, the mission's cap and the
per-call cap; the reservation and the check are one transaction under a Postgres advisory lock, so two concurrent calls cannot both pass
at the edge. After the call the reservation is settled with the actual cost. A refusal is recorded and no request is sent.

The decision itself (`decide`) and the price arithmetic are pure and unit-tested; `MemoryLedger` mirrors `DbLedger` for tests.
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from research_missions import config as C

LOCK_KEY = 7302026110541           # pg_advisory_xact_lock key for the research budget


class BudgetExceeded(Exception):
    """A reservation would exceed a limit; no request was sent."""


@dataclass(frozen=True)
class Limits:
    monthly_usd: float = C.MONTHLY_BUDGET_USD
    mission_usd: float = C.MISSION_BUDGET_USD
    call_usd: float = C.MAX_CALL_USD


def limits_from_env(env: Optional[Dict[str, str]] = None) -> Limits:
    """The environment may LOWER a limit, never raise it above the reviewed default (a typo cannot lift the cap)."""
    e = os.environ if env is None else env

    def lower(name: str, default: float) -> float:
        raw = e.get(name)
        if raw in (None, ""):
            return default
        try:
            v = float(raw)
        except ValueError:
            return default
        return max(0.0, min(v, default))
    return Limits(lower("RESEARCH_MONTHLY_BUDGET_USD", C.MONTHLY_BUDGET_USD), lower("RESEARCH_MISSION_BUDGET_USD", C.MISSION_BUDGET_USD),
                  lower("RESEARCH_MAX_CALL_USD", C.MAX_CALL_USD))


# ------------------------------------------------------------------------------------------------------------------------------------------------ prices
def price_of(model: Optional[str]):
    return C.PRICES.get(model or "", C.CEILING_PRICE)


def worst_case_usd(model: str, input_chars: int, max_tokens: int, fallback: bool = C.USE_FALLBACKS) -> float:
    """Upper bound of one call: estimated input tokens (characters / 3, an over-estimate) at the input price plus max_tokens at the output
    price; with a server-side fallback, a second full call at the ceiling price is added (the API may run both)."""
    est_in = input_chars / C.CHARS_PER_TOKEN_ESTIMATE
    p_in, p_out, _, _ = price_of(model)
    cost = (est_in * p_in + max_tokens * p_out) / 1e6
    if fallback:
        c_in, c_out, _, _ = C.CEILING_PRICE
        cost += (est_in * c_in + max_tokens * c_out) / 1e6
    return round(cost, 6)


def usage_cost_usd(served_model: Optional[str], usage: Any) -> float:
    """Actual cost from a response's usage. If a fallback model ran (usage.iterations has a fallback_message), everything is priced at the
    ceiling, because the per-iteration model is not something we want to guess."""
    iters = getattr(usage, "iterations", None) or []
    fell_back = any(getattr(it, "type", None) == "fallback_message" for it in iters)
    p_in, p_out, p_cr, p_cw = C.CEILING_PRICE if fell_back else price_of(served_model)
    g = lambda k: int(getattr(usage, k, 0) or 0)  # noqa: E731
    return round((g("input_tokens") * p_in + g("output_tokens") * p_out + g("cache_read_input_tokens") * p_cr + g("cache_creation_input_tokens") * p_cw) / 1e6, 6)


def decide(month_committed: float, mission_committed: float, amount: float, limits: Limits, mission_budget: Optional[float] = None) -> Optional[str]:
    """None when the reservation fits every limit, else the reason. 'Committed' = settled + failed costs + open reservations."""
    mission_cap = limits.mission_usd if mission_budget is None else min(limits.mission_usd, float(mission_budget))
    if amount > limits.call_usd:
        return f"call reservation ${amount:.4f} exceeds the per-call cap ${limits.call_usd:.2f}"
    if month_committed + amount > limits.monthly_usd:
        return f"monthly budget: ${month_committed:.4f} committed + ${amount:.4f} > ${limits.monthly_usd:.2f}"
    if mission_committed + amount > mission_cap:
        return f"mission budget: ${mission_committed:.4f} committed + ${amount:.4f} > ${mission_cap:.2f}"
    return None


# ------------------------------------------------------------------------------------------------------------------------------------------------ ledgers
class MemoryLedger:
    """In-memory ledger with the same contract as DbLedger (tests, dry runs)."""

    def __init__(self) -> None:
        self.rows: List[Dict[str, Any]] = []
        self._lock = threading.Lock()

    def _committed(self, mission_id: Optional[int] = None) -> float:
        tot = 0.0
        for r in self.rows:
            if mission_id is not None and r["mission_id"] != mission_id:
                continue
            tot += r["reserved_usd"] if r["status"] == "reserved" else r["cost_usd"] if r["status"] in ("settled", "failed") else 0.0
        return tot

    def reserve(self, mission_id: Optional[int], role: str, model: str, amount: float, limits: Limits, mission_budget: Optional[float] = None) -> int:
        with self._lock:
            reason = decide(self._committed(), self._committed(mission_id) if mission_id is not None else 0.0, amount, limits, mission_budget)
            row = {"id": len(self.rows) + 1, "mission_id": mission_id, "role": role, "model": model, "reserved_usd": amount, "cost_usd": 0.0,
                   "status": "refused" if reason else "reserved", "note": reason}
            self.rows.append(row)
            if reason:
                raise BudgetExceeded(reason)
            return row["id"]

    def settle(self, row_id: int, cost_usd: float, tokens: Dict[str, int], served_model: Optional[str]) -> None:
        r = self.rows[row_id - 1]
        assert r["status"] == "reserved"
        r.update(status="settled", cost_usd=cost_usd, served_model=served_model, **tokens)

    def fail(self, row_id: int, cost_usd: float, note: str) -> None:
        r = self.rows[row_id - 1]
        assert r["status"] == "reserved"
        r.update(status="failed", cost_usd=cost_usd, note=note)

    def status(self, limits: Limits) -> Dict[str, Any]:
        spent = sum(r["cost_usd"] for r in self.rows if r["status"] in ("settled", "failed"))
        reserved = sum(r["reserved_usd"] for r in self.rows if r["status"] == "reserved")
        return {"cap_usd": limits.monthly_usd, "spent_usd": round(spent, 6), "reserved_usd": round(reserved, 6), "remaining_usd": round(limits.monthly_usd - spent - reserved, 6)}


_MONTH_START = "date_trunc('month', NOW() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'"
_COMMITTED = "COALESCE(SUM(CASE WHEN status = 'reserved' THEN reserved_usd WHEN status IN ('settled', 'failed') THEN cost_usd ELSE 0 END), 0)"


class DbLedger:
    """research_llm_usage. The check and the reservation run in ONE transaction holding a transaction-scoped advisory lock."""

    def __init__(self, db) -> None:
        self.db = db

    def reserve(self, mission_id: Optional[int], role: str, model: str, amount: float, limits: Limits, mission_budget: Optional[float] = None) -> int:
        with self.db.get_sync_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_KEY,))
                cur.execute(f"SELECT {_COMMITTED} FROM research_llm_usage WHERE created_at >= {_MONTH_START}")
                month = float(cur.fetchone()[0])
                mission = 0.0
                if mission_id is not None:
                    cur.execute(f"SELECT {_COMMITTED} FROM research_llm_usage WHERE mission_id = %s", (mission_id,))
                    mission = float(cur.fetchone()[0])
                reason = decide(month, mission, amount, limits, mission_budget)
                cur.execute("INSERT INTO research_llm_usage (mission_id, role, model, status, reserved_usd, note) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                            (mission_id, role, model, "refused" if reason else "reserved", amount, reason))
                row_id = cur.fetchone()[0]
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        if reason:
            raise BudgetExceeded(reason)
        return int(row_id)

    def settle(self, row_id: int, cost_usd: float, tokens: Dict[str, int], served_model: Optional[str]) -> None:
        self.db.execute_insert("""UPDATE research_llm_usage SET status = 'settled', cost_usd = %s, input_tokens = %s, output_tokens = %s, cache_read_tokens = %s,
                                  cache_write_tokens = %s, served_model = %s, settled_at = NOW() WHERE id = %s AND status = 'reserved'""",
                               (cost_usd, tokens.get("input_tokens", 0), tokens.get("output_tokens", 0), tokens.get("cache_read_tokens", 0),
                                tokens.get("cache_write_tokens", 0), served_model, row_id))

    def fail(self, row_id: int, cost_usd: float, note: str) -> None:
        self.db.execute_insert("UPDATE research_llm_usage SET status = 'failed', cost_usd = %s, note = %s, settled_at = NOW() WHERE id = %s AND status = 'reserved'",
                               (cost_usd, note[:500], row_id))

    def status(self, limits: Limits) -> Dict[str, Any]:
        r = self.db.execute_dict_query(f"""SELECT COALESCE(SUM(cost_usd) FILTER (WHERE status IN ('settled', 'failed')), 0) AS spent,
                                                  COALESCE(SUM(reserved_usd) FILTER (WHERE status = 'reserved'), 0) AS reserved
                                           FROM research_llm_usage WHERE created_at >= {_MONTH_START}""")[0]
        spent, reserved = float(r["spent"]), float(r["reserved"])
        return {"month": datetime.now(timezone.utc).strftime("%Y-%m"), "cap_usd": limits.monthly_usd, "spent_usd": round(spent, 6), "reserved_usd": round(reserved, 6),
                "remaining_usd": round(limits.monthly_usd - spent - reserved, 6)}
