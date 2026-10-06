"""Point-in-time sector history: chain verification, the "what was known at T" selector and the dual-source cross-check (pure).

No database, clock, file, network or environment. The rows come from `sector_observation` / `sector_poll` (migration 31) as plain dicts with
exactly the columns of `HISTORY_COLUMNS`; the database trigger stamps and chains them, this module re-derives and re-checks all of it.

Three DIFFERENT times, never merged:
  vendor-claimed validity   `source_asof`. Stored only when the vendor really supplied one (yfinance's current-classification field supplies none,
                            so it is NULL). It is NEVER used for selection or freshness: it is the vendor's claim, not our knowledge.
  when our system knew it   `stamp` = `sector_observation.captured_at` (the database stamps it; a writer cannot set it). Confirmation polls carry
                            their own `attempted_at`. This is what freshness and eligibility are built from.
  which session may use it  `effective_session` = the UTC date of `captured_at`, stamped by the database. An observation is eligible for a decision at
                            session t0 only if `effective_session <= t0` AND `dataset_contract.is_known(...)` holds. The second condition alone is NOT
                            enough: with the manifest's grace of one day it admits an observation captured the day AFTER t0, which would let
                            "learn later" rewrite a fact that was knowable yesterday. The date test closes that.

Selection at (symbol, t0): the latest ELIGIBLE observation in the symbol's chain wins; the chain prefix up to it must verify (gapless, linked,
every row hash recomputed, monotone stamps, observed-forward provenance) or the symbol FAILS CLOSED. Rows after the head never matter, so a later
observation can never change an earlier decision.

Currency (freshness) is derived at READ time: max(head observation time, latest eligible same-value `confirmed_head` poll). A same-value refresh
therefore extends freshness; a failed or ambiguous poll does not (the age keeps growing). `SECTOR_MAX_AGE_DAYS` (30) stays the operational constant
of `sector_provenance`: it is NOT empirically validated and is stored nowhere in the observations.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from research.lab import dataset_contract as C
from research.lab import sector_provenance as SP

HASH_SCHEMA = "sector_obs_v1"
FORWARD_PROVENANCE = "observed_forward"

OBSERVATION, CONFIRMATION = "observation", "confirmation"
# The columns of one history read live in the contract (`C.HISTORY_COLUMNS`): observations and same-value confirmation polls in one list, told
# apart by `row_kind`. For a confirmation row `stamp` is the poll's attempted_at and `value_hash` / `seq` / `sector` are those of the head it confirmed.
HISTORY_COLUMNS = C.HISTORY_COLUMNS

# selection kinds
K_OBSERVED, K_EXPLICIT_NO_SECTOR, K_NO_HISTORY, K_BROKEN = "observed", "explicit_no_sector", "no_history", "broken"

# cross-check relations
R_AGREE, R_AGREE_UNUSABLE = "agree", "agree_unusable"
R_HISTORY_TIGHTENED, R_HISTORY_AHEAD = "history_tightened", "history_ahead"
R_IDENTITY_CONFLICT, R_CHAIN_BROKEN = "identity_conflict", "chain_broken"
RELATIONS = (R_AGREE, R_AGREE_UNUSABLE, R_HISTORY_TIGHTENED, R_HISTORY_AHEAD, R_IDENTITY_CONFLICT, R_CHAIN_BROKEN)

# how unsafe an evidence state is: the cross-check keeps the HIGHER one (the safer source wins), ties keep the candidate's own evidence
_RANK = {SP.OBSERVED_FRESH: 0, SP.OBSERVED_STALE: 1, SP.UNAVAILABLE: 2, SP.RECONSTRUCTED: 3, SP.UNKNOWN: 4}


# ------------------------------------------------------------------ the row hash (mirror of research_sector_row_hash in migration 31)
def _enc(v: Optional[str]) -> str:
    return "-;" if v is None else f"{len(v)}:{v};"


def _ts(v: datetime) -> str:
    if v.tzinfo is None:
        raise ValueError("a naive timestamp cannot be hashed")
    return v.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")


def chain_hash(*, symbol: str, source: str, seq: int, sector: Optional[str], no_sector_reason: Optional[str], sector_raw: Optional[str],
               captured_at: datetime, provenance: str, raw_payload_hash: str, prev_value_hash: Optional[str]) -> str:
    """The value_hash the database computes for an observation: sha256 over the length-prefixed canonical fields (identity, value, provenance,
    capture time, payload hash and the previous link), so rewriting ANY of them is detectable."""
    body = (HASH_SCHEMA + ";" + _enc(symbol) + _enc(source) + _enc(str(seq)) + _enc(sector) + _enc(no_sector_reason) + _enc(sector_raw)
            + _enc(_ts(captured_at)) + _enc(provenance) + _enc(raw_payload_hash) + _enc(prev_value_hash))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def row_hash(r: Mapping[str, Any]) -> str:
    return chain_hash(symbol=r["symbol"], source=r["source"], seq=r["seq"], sector=r["sector"], no_sector_reason=r["no_sector_reason"],
                      sector_raw=r["sector_raw"], captured_at=r["stamp"], provenance=r["provenance"], raw_payload_hash=r["raw_payload_hash"],
                      prev_value_hash=r["prev_value_hash"])


def _utc_date(v: datetime) -> date:
    return v.astimezone(timezone.utc).date()


# ------------------------------------------------------------------ chain verification
def verify_chain(chain: Sequence[Mapping[str, Any]]) -> List[str]:
    """Problems (stable codes) of ONE (symbol, source) chain given in seq order, starting at seq 1. An empty list means the chain verifies:
    first-row anchor, gapless, each row linked to the previous one's hash, every hash recomputed, monotone capture times, effective_session equal to
    the capture's UTC date, observed-forward provenance, a consistent value/no-sector split and a change_kind that matches the transition (a
    same-value row is a contradiction: a refresh is a poll, not an observation)."""
    out: List[str] = []
    prev: Optional[Mapping[str, Any]] = None
    for i, r in enumerate(chain):
        if r["row_kind"] != OBSERVATION:
            out.append("not_an_observation")
            continue
        if r["seq"] != i + 1:
            out.append("seq_gap_or_reorder")
        if (r["seq"] == 1) != (r["prev_value_hash"] is None):
            out.append("anchor_mismatch")
        elif prev is not None and r["prev_value_hash"] != prev["value_hash"]:
            out.append("broken_link")
        if row_hash(r) != r["value_hash"]:
            out.append("hash_mismatch")
        if r["provenance"] != FORWARD_PROVENANCE:
            out.append("provenance_not_observed_forward")
        if r["effective_session"] != _utc_date(r["stamp"]):
            out.append("effective_session_mismatch")
        if (r["sector"] is None) == (r["no_sector_reason"] is None) or (r["sector"] is not None and SP.clean_sector(r["sector"]) != r["sector"]):
            out.append("value_shape_invalid")
        if prev is not None:
            if r["stamp"] < prev["stamp"]:
                out.append("time_not_monotone")
            if r["sector"] == prev["sector"]:
                out.append("same_value_observation")
            want = "became_none" if r["sector"] is None else ("became_set" if prev["sector"] is None else "changed")
            if r["change_kind"] != want:
                out.append("change_kind_mismatch")
        elif r["change_kind"] != "first":
            out.append("change_kind_mismatch")
        prev = r
    return sorted(set(out))


# ------------------------------------------------------------------ the point-in-time selector
@dataclass(frozen=True)
class HistorySelection:
    kind: str                                  # observed | explicit_no_sector | no_history | broken
    evidence: SP.SectorEvidence                # derived at read time; UNAVAILABLE for no_history / explicit no-sector, see `kind` for which
    sector: Optional[str]
    head_seq: Optional[int]
    head_captured_at: Optional[datetime]
    currency_at: Optional[datetime]            # max(head capture, latest eligible confirmation of that head)
    confirmations_used: int
    problems: Tuple[str, ...]


def _eligible(stamp: Optional[datetime], effective_session: Optional[date], t0: date, grace: int, cutoff: datetime) -> bool:
    return (effective_session is not None and effective_session <= t0 and C.is_known(stamp, t0, grace, cutoff))


def select(obs: Sequence[Mapping[str, Any]], confirmations: Sequence[Mapping[str, Any]], *, t0: date, grace_days: int, cutoff: datetime,
           max_age_days: int = SP.SECTOR_MAX_AGE_DAYS) -> HistorySelection:
    """What the append-only history says about ONE (symbol, source) at decision session `t0`. `obs` is every observation row of the chain
    (any order), `confirmations` every confirmation row of it. Later observations are never consulted for the verdict."""
    chain = sorted(obs, key=lambda r: r["seq"])
    eligible = [r for r in chain if _eligible(r["stamp"], r["effective_session"], t0, grace_days, cutoff)]
    if not eligible:
        return HistorySelection(K_NO_HISTORY, _ev(SP.UNAVAILABLE, SP.HISTORY_ABSENT, None), None, None, None, None, 0, ())
    problems = list(verify_chain(eligible))
    if [r["seq"] for r in eligible] != list(range(1, len(eligible) + 1)):
        problems.append("eligible_rows_not_a_prefix")
    head = eligible[-1]
    if problems:
        return HistorySelection(K_BROKEN, _ev(SP.UNAVAILABLE, SP.IDENTITY_CONFLICT, head["sector"]), head["sector"], head["seq"],
                                head["stamp"], None, 0, tuple(sorted(set(problems))))
    confs = [c for c in confirmations if c["value_hash"] == head["value_hash"] and c["symbol"] == head["symbol"] and c["source"] == head["source"]
             and _eligible(c["stamp"], c["effective_session"], t0, grace_days, cutoff)]
    if any(c["stamp"] < head["stamp"] for c in confs):
        return HistorySelection(K_BROKEN, _ev(SP.UNAVAILABLE, SP.IDENTITY_CONFLICT, head["sector"]), head["sector"], head["seq"],
                                head["stamp"], None, 0, ("confirmation_before_head",))
    currency = max([head["stamp"]] + [c["stamp"] for c in confs])
    if head["sector"] is None:
        return HistorySelection(K_EXPLICIT_NO_SECTOR, _ev(SP.UNAVAILABLE, SP.NO_SECTOR, None), None, head["seq"], head["stamp"],
                                currency, len(confs), ())
    ev = SP.classify_sector_evidence(sector=head["sector"], source=head["source"], asof=_utc_date(currency), t0=t0, provenance="observed",
                                     available=True, max_age_days=max_age_days)
    return HistorySelection(K_OBSERVED, ev, head["sector"], head["seq"], head["stamp"], currency, len(confs), ())


def _ev(state: str, reason: str, sector: Optional[str]) -> SP.SectorEvidence:
    return SP.SectorEvidence(state, reason, sector, None)


def orphan_confirmations(obs: Sequence[Mapping[str, Any]], confirmations: Sequence[Mapping[str, Any]]) -> int:
    """Confirmation rows that point at no observation of their chain (never expected: the database foreign key forbids it)."""
    known = {(o["symbol"], o["source"], o["value_hash"]) for o in obs}
    return sum(1 for c in confirmations if (c["symbol"], c["source"], c["value_hash"]) not in known)


# ------------------------------------------------------------------ dual-source cross-check
@dataclass(frozen=True)
class CrossCheck:
    effective: SP.SectorEvidence               # the evidence the dataset uses (never safer than the candidate's own)
    relation: str
    candidate_state: str
    history_kind: str
    history_state: str
    problems: Tuple[str, ...]


def crosscheck(cand: SP.SectorEvidence, hist: HistorySelection) -> CrossCheck:
    """Combine the candidate-bounded evidence (Slice 8, unchanged) with the append-only history for ONE decision point.

    * a broken history chain, or two FRESH observed sources naming different sectors  -> identity conflict, fail closed;
    * otherwise the SAFER source wins (higher `_RANK`; ties keep the candidate's own evidence), so history can only tighten a cell;
    * the history being ahead of weaker candidate evidence changes nothing (recorded as `history_ahead`), and reconstructed / unknown provenance in
      either source can never be loosened by the other;
    * a later observation never repairs an earlier row: both sides are evaluated AT t0 only."""
    h = hist.evidence
    if hist.kind == K_BROKEN:
        return CrossCheck(_ev(SP.UNAVAILABLE, SP.IDENTITY_CONFLICT, cand.sector), R_CHAIN_BROKEN, cand.state, hist.kind, h.state,
                          hist.problems)
    if cand.state == SP.OBSERVED_FRESH and h.state == SP.OBSERVED_FRESH:
        if cand.sector == h.sector:
            return CrossCheck(cand, R_AGREE, cand.state, hist.kind, h.state, ())
        return CrossCheck(_ev(SP.UNAVAILABLE, SP.IDENTITY_CONFLICT, cand.sector), R_IDENTITY_CONFLICT, cand.state, hist.kind, h.state, ())
    rc, rh = _RANK[cand.state], _RANK[h.state]
    if rh > rc:
        return CrossCheck(h, R_HISTORY_TIGHTENED, cand.state, hist.kind, h.state, ())
    if rh < rc:
        return CrossCheck(cand, R_HISTORY_AHEAD, cand.state, hist.kind, h.state, ())
    return CrossCheck(cand, R_AGREE_UNUSABLE, cand.state, hist.kind, h.state, ())


def group_history(rows: Sequence[Mapping[str, Any]], source: str) -> Tuple[Dict[str, List[Mapping[str, Any]]], Dict[str, List[Mapping[str, Any]]]]:
    """Split one history read into per-symbol observation / confirmation lists for ONE source (other sources are ignored)."""
    obs: Dict[str, List[Mapping[str, Any]]] = {}
    conf: Dict[str, List[Mapping[str, Any]]] = {}
    for r in rows:
        if r["source"] != source:
            continue
        (obs if r["row_kind"] == OBSERVATION else conf).setdefault(r["symbol"], []).append(r)
    return obs, conf


def summarise(records: Sequence[Tuple[str, "CrossCheck"]], *, source: str, examples: int = 5) -> Dict[str, Any]:
    """Deterministic, JSON-able diagnostics of the cross-check over (observation_key, CrossCheck) pairs: counts by relation / history kind / chain
    problem, and the first few examples of every non-agreeing relation. Additive: it describes the dataset, it is never a dataset cell."""
    by_rel: Dict[str, int] = {}
    by_kind: Dict[str, int] = {}
    problems: Dict[str, int] = {}
    ex: Dict[str, List[Dict[str, Any]]] = {}
    for okey, cc in sorted(records, key=lambda x: x[0]):
        by_rel[cc.relation] = by_rel.get(cc.relation, 0) + 1
        by_kind[cc.history_kind] = by_kind.get(cc.history_kind, 0) + 1
        for p in cc.problems:
            problems[p] = problems.get(p, 0) + 1
        if cc.relation not in (R_AGREE, R_AGREE_UNUSABLE) and len(ex.setdefault(cc.relation, [])) < examples:
            ex[cc.relation].append({"observation_key": okey, "candidate_state": cc.candidate_state, "history_kind": cc.history_kind,
                                    "history_state": cc.history_state})
    return {"source": source, "candidates_checked": len(records), "by_relation": dict(sorted(by_rel.items())),
            "by_history_kind": dict(sorted(by_kind.items())), "chain_problems": dict(sorted(problems.items())),
            "examples": {k: ex[k] for k in sorted(ex)}}
