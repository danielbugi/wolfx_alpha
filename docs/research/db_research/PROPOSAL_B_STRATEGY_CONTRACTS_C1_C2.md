# Proposal B — Versioned strategy-rule contracts c1 / c2 (with evidence and uncertainty)

Status: **proposal for owner review**, 2026-10-22. Read-only research. The general design is in [STRATEGY_VERSION_CONTRACT.md](STRATEGY_VERSION_CONTRACT.md). This document records the evidence for the two contracts that already ran, and how confidently each historical cohort can be assigned.

## Evidence (read-only, 2026-10-22)

| Evidence | What it shows | Durability |
|---|---|---|
| `/opt/donchian/env/.env`: `GUARDS_EFFECTIVE_FROM=2026-10-02` | The configured boundary | **Mutable** file; current value only |
| Env backups: `.env.bak.20261002T172757Z` (taken before an edit) **lacks** the setting; `.env.bak.pre-S10-20261003T094542Z` (mtime 2026-10-02 17:35:34Z) **has** it | The setting was added on 2026-10-02 between 17:27:57Z and 17:35:34Z, before that session's nightly run (23:15Z) | Mutable files; mtimes can change |
| Pipeline journal: `Universe guards mode: ACTIVE (session 2026-10-02 >= GUARDS_EFFECTIVE_FROM=2026-10-02)` at 2026-10-02 23:15:48Z, then one line per session (13 lines up to 10-21) | The guards actually ran from session 2026-10-02 | **Rotating** journal (retention currently from 2026-09-24): it will be lost |
| Pipeline runs for 09-28 and 10-01: no guards line at all in about 200k journal lines each | No guard evaluation happened for those cohorts | Same rotating journal |
| `signal_ledger.model_version = 'unknown'` on 165/169 (10-02), 124/126 (10-05) and 98/99 (10-06) rows | A side effect that started exactly with the new screener behaviour (documented defect) and stopped after the provenance fix | Immutable-in-practice ledger rows; indirect evidence |
| `candidate_observation.passed_guard / guard_reasons` from session 2026-10-09 (with `research_capture_activation.effective_from_session = 2026-10-09`) | Each guard decision recorded per candidate | **Immutable** (append-only research tables) |

## Cohort assignment and its certainty

| Cohort (signal_date) | Rows | Contract | Certainty | Basis |
|---|---|---|---|---|
| 2026-09-28, 2026-10-01 | 853 | **c1**: no universe guards; writer not strict | High | Boundary date and no guards log line (journal) |
| 2026-10-02, 10-05, 10-06 | 394 | **c2**: guards active | High | Journal ACTIVE lines plus the `'unknown'` marker on 387 of 394 rows |
| 2026-10-07, 10-08 | 258 | **c2** | Medium-high | Journal ACTIVE lines only (no marker in the rows themselves) |
| 2026-10-09 → | ~1,468 (growing) | **c2** | High | Journal plus immutable per-candidate guard decisions |

What cannot be recovered without reconstruction: **which c1 signals c2 would have rejected.**
- Recomputing the guard rules (illiquid dollar volume, price discontinuity, insufficient history) for 09-28 and 10-01 from prices is possible, but it is **reconstructed**. It must be labelled as such and never written into the ledger.

## Smallest forward-only fix

1. **Preserve the evidence before the journal rotates.** *(Done 2026-10-22: `docs/research/evidence/strategy_contract_c1_c2_2026-10-22/`, with sha256 sums.)*
   - Export the 13 `Universe guards mode` journal lines, the env-backup names with their mtimes, and the per-cohort `'unknown'` counts into a committed file, `docs/research/evidence/strategy_contract_c1_c2_2026-10-22/`, with sha256 sums.
   - This is a read-only export plus a docs commit. No secret values: only the one setting's name and value.
2. **Code registry (no migration):**
   - `mechanism/shared/strategy_contracts.py` holds two immutable canonical-JSON contracts with hashes:
     - c1: `effective_from_session 2026-09-28`, `guards: inert`.
     - c2: `effective_from_session 2026-10-02`, `guards: universe_guards`, `supersedes: c1`, `evidence: <path + sha256>`.
   - `contract_for(strategy, signal_date)` assigns by date. A contract also carries a `certainty` note for cohorts whose assignment rests on non-immutable evidence (10-07, 10-08).
3. **Drift tests.**
   - The production `GUARDS_EFFECTIVE_FROM` value used by c2 must equal the contract.
   - The guards code path must exist.
   - `trade_plan` constants must equal c2.
   - Any behaviour change needs a c3 with a new `effective_from`.
4. **Reporting.** Strategy Intelligence and `/api/strategies/*` gain `contract_version` + hash per number, and refuse to mix c1 and c2 in one aggregate unless asked, in which case the split is shown.
5. **Later (separate approval):** an append-only `strategy_contract` table and an optional forward-only `signal_ledger.contract_hash` column. Neither is needed for steps 1–4.

## Not proposed

- Renaming or re-versioning `donchian_breakout v1`.
- Editing any ledger row.
- Changing guards or trading behaviour.
- Treating reconstructed guard decisions as recorded ones.
