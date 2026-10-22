# mechanism/strategy_analytics/contracts.py
"""Versioned, immutable strategy-rule contracts (Proposal B, docs/research/db_research/PROPOSAL_B_STRATEGY_CONTRACTS_C1_C2.md).

`donchian_breakout v1` ran under two rule sets without a version change: universe guards switched on from session
2026-10-02 through an environment variable (GUARDS_EFFECTIVE_FROM). A contract documents one such rule set exactly;
it never renames the strategy, rewrites a ledger row or changes live behaviour. Every reported number can cite the
contract (version + sha256 of its canonical JSON) that produced it.

Rules:
  * A contract is immutable. Any change to its content is a NEW contract_version with a later effective session;
    tests pin each contract's hash so an in-place edit fails CI.
  * Assignment is by signal session: the latest contract whose effective_from_session <= signal_date.
  * Historical assignment carries evidence and certainty: per-signal configuration was not recorded in the database
    before 2026-10-09, so the cohort certainty says what the assignment rests on.
Pure: no database, no clock, no I/O.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Any, Dict, List, Optional

SCHEMA = "strategy_contract_v1"
EVIDENCE_DIR = "docs/research/evidence/strategy_contract_c1_c2_2026-10-22"

_COMMON = {
    "signal": {"rule": "donchian_close_breakout", "channel_bars": 20, "price": "daily_close", "directions": ["long", "short"]},
    "entry": {"price_basis": "signal_close", "fill_model": "at_price", "known_after": "session_close"},
    "risk": {"atr": {"source": "technical_indicators.atr_14", "method": "wilder", "period": 14},
             "initial_stop": {"atr_mult": 2.0}, "r_unit": "abs(entry - initial_stop)"},
    "targets": {"atr_mults": [2.0, 4.0, 6.0], "r_levels": [1, 2, 3],
                "resolution": "first_bar_touching_stop_or_any_target; highest_target_touched_on_that_bar"},
    "gaps": {"stop_fill": "at_level", "target_fill": "at_level"},
    "same_bar_tie": {"rule": "stop_first", "flag": "same_bar_stop_and_target"},
    "expiry": {"bars": 20, "basis": "trading_bars_after_signal", "exit_price": "close_of_last_bar"},
    "costs": {"round_trip_bps": 0, "reported_sensitivities_bps": [10, 25]},
    "outcome": {"r_definition": "target_k=+k; stopped=-1; expired=direction*(close_bar20-entry)/r_unit",
                "mae": "max adverse excursion in R over evaluated bars"},
    "evaluation": {"calendar": "stock_prices sessions", "invalid_bar": "halt_row_unchanged", "split_suspect": "hold_until_reviewed"},
    "portfolio_rule": {"name": "independent_1R", "risk_per_signal_r": 1, "concurrency": "unlimited"},
    "code_refs": {"evaluator": "mechanism/screeners/evaluate_signal_ledger.py", "levels": "mechanism/shared/trade_plan.py",
                  "writer": "mechanism/screeners/signal_ledger_writer.py", "guards": "mechanism/screeners/universe_guards.py"},
}

CONTRACTS: List[Dict[str, Any]] = [
    {"schema": SCHEMA, "strategy_key": "donchian_breakout", "strategy_version": "v1", "contract_version": "c1",
     "effective_from_session": "2026-09-28", "supersedes": None, **_COMMON,
     "universe": {"guards": "none", "guards_active": False, "eligibility": ["real_atr_required", "one_open_slot_per_symbol_direction"],
                  "writer_strict": False},
     "assignment": [
         {"from": "2026-09-28", "to": "2026-10-01", "certainty": "high",
          "basis": ["first ledger session 2026-09-28", "pipeline journal shows no universe-guards line for the 2026-09-28 and 2026-10-01 runs",
                    "GUARDS_EFFECTIVE_FROM was added on 2026-10-02 between 17:27:57Z and 17:35:34Z (env backups)"],
          "evidence": EVIDENCE_DIR, "durability": "operational (rotating journal, mutable file mtimes); preserved copy with sha256"},
     ]},
    {"schema": SCHEMA, "strategy_key": "donchian_breakout", "strategy_version": "v1", "contract_version": "c2",
     "effective_from_session": "2026-10-02", "supersedes": "c1", **_COMMON,
     "universe": {"guards": "universe_guards", "guards_active": True, "boundary_setting": "GUARDS_EFFECTIVE_FROM=2026-10-02",
                  "eligibility": ["real_atr_required", "one_open_slot_per_symbol_direction", "universe_guards_passed"],
                  "writer_strict": True},
     "assignment": [
         {"from": "2026-10-02", "to": "2026-10-06", "certainty": "high",
          "basis": ["pipeline journal 'Universe guards mode: ACTIVE' line for each session",
                    "model_version='unknown' side-effect marker on 387 of 394 rows (began with the new screener behaviour)"],
          "evidence": EVIDENCE_DIR, "durability": "operational + ledger rows"},
         {"from": "2026-10-07", "to": "2026-10-08", "certainty": "medium_high",
          "basis": ["pipeline journal 'Universe guards mode: ACTIVE' line for each session (no marker in the rows themselves)"],
          "evidence": EVIDENCE_DIR, "durability": "operational only"},
         {"from": "2026-10-09", "to": None, "certainty": "high",
          "basis": ["pipeline journal ACTIVE lines", "immutable per-candidate guard decisions in candidate_observation (capture active from 2026-10-09)"],
          "evidence": EVIDENCE_DIR, "durability": "immutable research rows"},
     ]},
]


def canonical(contract: Dict[str, Any]) -> str:
    return json.dumps(contract, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def contract_hash(contract: Dict[str, Any]) -> str:
    return hashlib.sha256(canonical(contract).encode("ascii")).hexdigest()


def contracts_for(strategy_key: str, strategy_version: str) -> List[Dict[str, Any]]:
    return sorted((c for c in CONTRACTS if c["strategy_key"] == strategy_key and c["strategy_version"] == strategy_version),
                  key=lambda c: c["effective_from_session"])


def contract_for(strategy_key: str, strategy_version: str, signal_date: date) -> Optional[Dict[str, Any]]:
    """The contract in force on `signal_date` (latest effective_from_session <= signal_date), or None before the first."""
    found = None
    for c in contracts_for(strategy_key, strategy_version):
        if date.fromisoformat(c["effective_from_session"]) <= signal_date:
            found = c
    return found


def assignment(strategy_key: str, strategy_version: str, signal_date: date) -> Dict[str, Any]:
    """Contract + certainty for one signal session. Never guesses: no contract -> state 'not_available'."""
    c = contract_for(strategy_key, strategy_version, signal_date)
    if c is None:
        return {"state": "not_available", "reason": "no contract is declared for this strategy version on that session"}
    cohort = next((a for a in c["assignment"]
                   if date.fromisoformat(a["from"]) <= signal_date and (a["to"] is None or signal_date <= date.fromisoformat(a["to"]))), None)
    return {"state": "assigned", "contract_version": c["contract_version"], "contract_hash": contract_hash(c),
            "effective_from_session": c["effective_from_session"],
            "certainty": cohort["certainty"] if cohort else "unknown", "basis": cohort["basis"] if cohort else [],
            "evidence": cohort["evidence"] if cohort else None}


def public_view(strategy_key: str, strategy_version: str) -> Dict[str, Any]:
    """What the read-only API serves: every contract with its hash."""
    cs = contracts_for(strategy_key, strategy_version)
    return {"schema": SCHEMA, "contracts": [{**c, "contract_hash": contract_hash(c)} for c in cs],
            "state": "declared" if cs else "not_available"}
