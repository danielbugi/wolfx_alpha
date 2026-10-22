# Strategy version contract — proposal

Status: **proposal for owner review**, 2026-10-22. No code, schema, identity or live-behaviour change.

## 1. Audit: where a strategy's rules live today

| Element | Where it is defined | Persisted / versioned? |
|---|---|---|
| Identity | `strategies` table: `id, strategy_key, strategy_version, description, created_at`. One row: `1 donchian_breakout v1`. | Persisted; **no rules attached** |
| Signal rule | `mechanism/screeners/multi_timeframe_screener.py` (Donchian-20 breakout on the daily close; alignment/grade) | Code only |
| Universe guards | `screeners/universe_guards.py`; switched on by the **environment variable** `GUARDS_EFFECTIVE_FROM` (`guards_boundary.py`). Release records give 2026-10-02 in production. | **Not in the database.** Entry behaviour changed under the same `v1`. |
| Ledger eligibility | `signal_ledger_writer.py`: refuses defaulted ATR; one open slot per (symbol, strategy, direction); strict only after the guards boundary | Code only |
| Entry / ATR | Writer: `entry_price = current_price` (signal-day **close**), `atr = technical_indicators.atr_14` (Wilder 14) | Values per row; the convention is code only |
| Stop / targets | `shared/trade_plan.py`: stop 2×ATR = 1R; targets 2/4/6×ATR = 1R/2R/3R; `PLAN_HORIZON_BARS = 20` | Levels per row; constants in code |
| Resolution | `evaluate_signal_ledger._resolve`: first bar touching the stop or any target; highest target touched that bar; stop first on a tie (flagged `same_bar_stop_and_target`); **fills at level, no gap model, no cost**; expiry = close of bar 20; invalid-bar halt; split-suspect hold | Code only |
| Declared rules (API) | `strategy_analytics/performance.EXIT_RULES[("donchian_breakout","v1")]`, drift-tested against `trade_plan.py` | Code constant; omits entry, gaps, costs and resolution semantics |
| Backtest rule | `ml_training/features/price_features.plan_outcomes`: close entry, 3 tranches, **`r_single` = 3R target**, `plan_r` = tranche mean, no gaps, no cost | Code only. A **different rule** from the live ledger. |
| Exit variants (paper tracker) | `strategy_variant` (migration 35): immutable, `definition` JSON + `definition_hash`, `supersedes_variant_id`; 5 rows. Engine `research/exit_policies.py`: **next-open entry, gap-aware fills, costs 0/10/25 bps, actual initial risk**. | **Persisted and immutable**, but `variant_kind` is limited to `exit_policy` by a CHECK constraint |

**Consequences:**
- The same name, "donchian_breakout v1", currently means three different execution rule sets: the ledger, the backtest and the paper variants.
- One unrecorded behaviour change (guards) sits inside v1.
- No reported number carries a machine-checkable reference to the exact rules that produced it.

## 2. The contract

A **contract** is an immutable, canonical-JSON document with a sha256 hash and an **effective-from session**. It belongs to one strategy identity. A change to any field is a new contract version that supersedes the previous one from a stated session. Existing identities and rows are never rewritten.

```json
{
  "schema": "strategy_contract_v1",
  "strategy_key": "donchian_breakout",
  "strategy_version": "v1",
  "contract_version": "c2",
  "effective_from_session": "2026-10-02",
  "supersedes": "c1",
  "signal": {"rule": "donchian_close_breakout", "channel_bars": 20, "price": "daily_close", "directions": ["long", "short"]},
  "universe": {"guards": "universe_guards", "guards_active": true, "eligibility": ["real_atr_required", "one_open_slot_per_symbol_direction"]},
  "entry": {"price_basis": "signal_close", "fill_model": "at_price", "known_after": "session_close"},
  "risk": {"atr": {"source": "technical_indicators.atr_14", "method": "wilder", "period": 14},
           "initial_stop": {"atr_mult": 2.0}, "r_unit": "abs(entry - initial_stop)"},
  "targets": {"atr_mults": [2.0, 4.0, 6.0], "r_levels": [1, 2, 3],
              "resolution": "first_bar_touching_stop_or_any_target; highest_target_touched_on_that_bar"},
  "gaps": {"stop_fill": "at_level", "target_fill": "at_level"},
  "same_bar_tie": {"rule": "stop_first", "flag": "same_bar_stop_and_target"},
  "expiry": {"bars": 20, "basis": "trading_bars_after_signal", "exit_price": "close_of_last_bar"},
  "costs": {"round_trip_bps": 0, "reported_sensitivities_bps": [10, 25]},
  "outcome": {"r_definition": "target_k=+k, stopped=-1, expired=direction*(close_bar20-entry)/r_unit",
              "mae": "max adverse excursion in R over evaluated bars", "mfe": "max favourable excursion in R over evaluated bars"},
  "evaluation": {"calendar": "stock_prices sessions", "invalid_bar": "halt_row_unchanged", "split_suspect": "hold_until_reviewed"},
  "portfolio_rule": {"name": "independent_1R", "risk_per_signal_r": 1, "concurrency": "unlimited"},
  "code_refs": {"evaluator": "mechanism/screeners/evaluate_signal_ledger.py", "levels": "mechanism/shared/trade_plan.py"}
}
```

**The contracts that already exist in practice** (documentation of the past, not a change):

| Contract | Effective | Difference |
|---|---|---|
| `donchian_breakout v1 / c1` | 2026-09-28 | Ledger start; guards inert |
| `donchian_breakout v1 / c2` | 2026-10-02 (`GUARDS_EFFECTIVE_FROM`) | Universe guards active; the writer becomes strict |

**Research rule sets get their own named contracts and are never called "v1":**

| Name | Rule |
|---|---|
| `donchian_breakout research/dataset_r3` | Close entry, 3R target, no gaps, no cost (the backtest `r_single`) |
| `research/exec_l3` | Next-open entry, gap fills, 10 bps (Notebook 02's primary rule) |

Paper tracker variants keep their `strategy_variant` rows. Their definitions can reference a base contract by hash.

## 3. Rules of the contract

1. **Immutable.** A contract row and its hash never change. A correction is a new `contract_version`.
2. **Effective by session.** Every ledger row maps to exactly one contract by `(strategy_id, signal_date ≥ effective_from_session)`, the latest one. The ledger itself needs no new column. A later migration may add a `contract_hash` column, written going forward only.
3. **Drift-tested.** Tests assert that the latest contract equals the code constants: `trade_plan` multipliers and horizon, the evaluator's tie/fill/expiry behaviour (via the replay self-check), the writer's entry basis, and the presence of the guards boundary. A code change that alters behaviour without a new contract fails CI.
4. **Every number cites its contract.** Strategy Intelligence, `/api/strategies/*`, Lab reports and notebooks report `contract_version` + hash next to each metric. Mixing contracts in one aggregate is refused, or split by contract.
5. **No retroactive change.** Existing `strategies` rows, `signal_ledger` rows and `strategy_variant` rows are untouched. Contracts c1/c2 *describe* what already ran.

## 4. Storage options

| Option | What | Migration | Recommendation |
|---|---|---|---|
| **A: code registry** | `mechanism/shared/strategy_contracts.py`: canonical JSON, hash, `effective_from`, lookup `contract_for(strategy, session)`; served by `/api/strategies/{key}/{version}/contract`; drift tests | None | **First step**: immediate value and no schema risk |
| **B: database registry** | New append-only `strategy_contract` table modelled on `strategy_variant` (hash unique, supersedes chain, ALWAYS-enabled immutability triggers, `IF NOT EXISTS`, preflight/rollback refusing when rows exist); registered by a manual CLI as with the variants | Additive, later, separately approved | After A has settled. It makes contracts queryable and joinable. |
| C: widen `strategy_variant.variant_kind` | Allow a `contract` kind | Alters a CHECK on an immutable table | Not recommended: it mixes two concepts and changes an applied migration's contract |

## 5. Tests (option A)

- **Canonicalisation:** the hash is stable across key order; any field change changes the hash; a contract with an unknown field is refused.
- **Effective-date mapping:** 09-28 → 10-01 maps to c1; 10-02 onward maps to c2; dates before the first contract map to none (explicit `not_available`).
- **Drift:**
  - `trade_plan` constants equal the c2 risk/targets/expiry.
  - `EXIT_RULES` is generated from the contract (one source).
  - The evaluator replay (`selfcheck_nb02` logic, on fixtures) matches the contract's resolution semantics.
- **API:** the contract endpoint is read-only, and `/summary` responses include `contract_version` and `contract_hash`.

## 6. Risks

- **The guards boundary lives in an environment variable.** If production's value differs from the 2026-10-02 in the release records, c2's `effective_from` is wrong. Read it from the running configuration before registering. Only the setting's name is needed; it is not a secret.
- **Option A makes code the source of truth.** A deploy that changes code without a contract bump is caught only by tests, which is why the drift tests are the core of this proposal.
