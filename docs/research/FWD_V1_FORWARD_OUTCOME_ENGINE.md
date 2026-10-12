# First Light Algo: `fwd_v1`, the forward-outcome engine (A2)

Status: 2026-10-04, `lab/first-light-algo`, Slice 2. Built and tested locally; **nothing applied to production, nothing scheduled, nothing writing.**
Code: `mechanism/research/labels/` (`fwd_v1.py` pure engine, `sessions.py` calendar, `repository.py` SQL, `runner.py` CLI).
Schema: migration **26**, `mechanism/add_forward_return_label_table.sql` (registry: `docs/architecture/MIGRATION_REGISTRY.md`).

## 1. What it is

`fwd_v1` answers one question per observation and horizon: *what happened to this candidate afterwards?*

| Layer | Question | Where |
|---|---|---|
| `signal_ledger` | what was selected and tracked | existing |
| `candidate_observation` | what the strategy evaluated at T0 | migration 22 |
| `feature_snapshot` | what was known at T0 | migration 22 |
| **`forward_return_label`** | **what happened afterwards** | **migration 26** |
| market intelligence | market and sector context at T0 | migrations 24/25 |

Labels are keyed on `candidate_observation` (selected **and** merely evaluated candidates), so they are independent of `signal_ledger` and of any
strategy's exit rule. A label is a property of the price path, not of a trade plan.

## 2. Horizon and maturity semantics

- Horizons: **1, 3, 5, 10, 20, 60** trading sessions (DB `CHECK`).
- T0 = the observation's `session_date`. **Horizon N = the Nth session strictly after T0** in an explicit trading-session calendar. Sessions, never calendar
  days; weekends and holidays do not count.
- `as_of_session` is an explicit, required argument: the latest session the caller vouches is complete. The engine never reads a clock.
  A label is **determinable only when `horizon_session <= as_of_session`**; otherwise it is `Pending` and **no row exists**.
- Only two stored states exist:
  - `final`: the horizon session is complete and every input the return needs was usable;
  - `void`: terminal; the inputs were still unusable `VOID_GRACE_SESSIONS` (3) sessions after the horizon. A void row carries a `void_reason` and **no outcome
    value at all** (DB `CHECK`).
- **Pending has no row**, so a partial label cannot exist, and a dataset builder cannot consume one by accident. Training code must read through
  `repository.fetch_mature_labels()` (final only) or filter `label_status = 'final'`. Void rows are kept so "we tried and the data was unusable" is auditable and
  so the runner does not retry forever.
- **Anti-leakage rules (all tested):**
  1. Bars dated after the horizon session are never read for the value, the excursions or the input hash (poisoned later bars change nothing).
  2. A later `as_of_session` changes only the audit field `computed_as_of_session`, never an outcome.
  3. Everything in a label is a function of data at or before its own `horizon_session`; for a model trained at session S the safe filter is
     `horizon_session <= S` (an index on `(label_version, horizon_session) WHERE final` serves it). T0 features plus labels with `horizon_session > S` is leakage.
  4. `computed_at` is audit-only wall time and is never an input.
  5. The grace period means a label for horizon H may be written up to 3 sessions later than H. Maturity is defined by `horizon_session`, not by `computed_at`.
     A dataset builder that embargoes by `computed_as_of_session` is conservative and also correct.

## 3. Outcome semantics

Let `ref` = stored T0 close, `hz` = stored close on the horizon session, `d` = direction (+1 long, -1 short).

| Field | Definition |
|---|---|
| `raw_return` | `hz / ref - 1` (simple return, fraction). The stock's own move, independent of direction. |
| `directional_return` | `d * raw_return`. A short profits when price falls. Equal to `d * raw_return` exactly (DB `CHECK`). |
| `benchmark_return` | `^GSPC` close on the horizon session over its close on T0, minus 1. Same two dates as the stock. |
| `excess_return` | `raw_return - benchmark_return` (benchmark-relative, not direction-adjusted). |
| `directional_excess_return` | `d * excess_return`. |
| `mfe` | direction-adjusted favourable excursion, `>= 0`, over sessions T0+1 .. horizon. T0 itself is **excluded**. |
| `mae` | direction-adjusted adverse excursion, `<= 0`, same window. |

MFE/MAE use `H = max(high, close)` and `L = min(low, close)` per session (a stored close outside the stored range still counts):
long: `mfe = max(0, max(H)/ref - 1)`, `mae = min(0, min(L)/ref - 1)`; short: `mfe = max(0, 1 - min(L)/ref)`, `mae = min(0, 1 - max(H)/ref)`.
They are daily-bar extremes, not tick data: the order of high and low inside a bar is unknown, so MFE/MAE are **bounds**, not a path.
`path_state = complete` only when every session T0+1..horizon has a bar with a high and a low; otherwise `incomplete` and **both are NULL** (never partial).

`benchmark_state`: `ok`, or `unavailable` (the stock return is still valid; the three benchmark-relative fields are NULL, never 0), or `not_evaluated` (void).
A benchmark gap on either endpoint date is treated as a lagging feed: the label stays pending until the grace period ends, then finalises as `unavailable`.
Only the two endpoint dates matter; a gap in the middle of the window is irrelevant.

**Benchmark basis mismatch (documented, not hidden):** `^GSPC` is a *price* index; the stock bars are provider-adjusted (dividend-adjusted). `excess_return` is
therefore not a clean total-return comparison. It is consistent across all labels and sufficient for ranking and relative analysis; a total-return benchmark
(e.g. SPY adjusted close) would be a future `label_version`, not an edit.

## 4. Price basis, adjusted-price drift and reproducibility

Facts (verified from the schema and the updaters' behaviour, see `stock_prices`): bars come from a provider that returns **adjusted** history and are
**upserted in place**; `adj_close` equals `close`. So history can **restate** after a split or a dividend: every earlier bar is rescaled. `candidate_observation.entry_close`
is the **raw close captured at T0**, which never restates.

`fwd_v1` does not pretend this away:

1. **Returns are computed on the stored basis at label time**, using stored T0 and stored horizon closes together. A uniformly restated history gives the same
   percentage return as the raw one (a split-adjusted series is internally consistent), and a dividend-adjusted series gives a total-return-style figure.
2. **The captured entry close is recorded beside it**: `entry_close_captured`, `reference_close`, `basis_ratio = entry_close_captured / reference_close`.
   `|basis_ratio - 1| > 1e-4` sets `data_quality = 'basis_adjusted'` with a `dq_details.basis_note`. The label is still valid; the flag tells a consumer that the
   stored history no longer equals what was seen at T0 (a consumer who needs the raw basis must exclude these).
3. **A split-like jump between consecutive stored closes inside [T0, horizon] voids the label** (after the grace period; pending before it). The jump test is the
   repository's one shared rule (`alerts.price_guard.is_split_like`: moves beyond about 3x, or landing on 1/3, 1/2, 2/3, 2, 3 within 3%). A jump inside the window means the stored
   series mixes bases (the provider restated only part of history). A real -20% crash is not flagged; a wrongly voided real move costs one label, a missed split
   would fabricate a return.
4. **`input_hash`** = sha256 of every input value the label used (the T0..horizon window's OHLC, the entry close, the two benchmark endpoints, the window dates, the
   versions). Labels are immutable, so a later provider restatement cannot change a stored label; it can be **detected**: `runner --restatements` recomputes the hash
   from today's stored prices and reports every final label whose inputs changed (read-only; it never rewrites). A dataset builder can exclude those labels or
   a new `label_version` can recompute them.
5. **Re-running is stable.** A re-run, a concurrent run, or a run after the data changed inserts nothing for an existing key (`UNIQUE (observation_id, horizon_sessions, label_version)`,
   `ON CONFLICT DO NOTHING`); the first valid write wins. A changed input hash on a conflict is counted as `restated`, not written.

Residual risks (not eliminated): (a) a **partial** restatement that mixes bases by less than a split-sized jump (an unadjusted pre-ex-dividend bar next to an adjusted one) is
smaller than the split detector's threshold and is caught only afterwards, by the restatement report; (b) benchmark basis mismatch above; (c) **delisted symbols** have no bars after
delisting, so labels past the last bar void (`missing_horizon_bar`); a delisting return is **not** imputed, which makes the labelled set slightly survivor-biased for long horizons.

## 5. Missing-data policy

| Situation | Result |
|---|---|
| Horizon not reached / T0 not in the calendar | Pending, no row |
| Horizon bar, T0 bar or any in-window bar unusable (missing, `<= 0`, NaN/inf, high < low) | Pending until grace elapses, then `void` with the first reason in priority order `missing_reference_bar`, `missing_horizon_bar`, `invalid_bar`, `split_like_discontinuity` (all reasons are in `dq_details.all_reasons`) |
| A path bar missing between T0 and the horizon (halt, gap) | `final`; `path_state = incomplete`; `mfe`/`mae` NULL; `bars_observed < bars_expected` |
| Bars without high/low | `final`; `path_state = incomplete` |
| Benchmark endpoint missing | Pending until grace elapses, then `final` with `benchmark_state = unavailable` |
| Delisted / bars stop | `void` (`missing_horizon_bar`) after grace; no imputed return |

Nothing is defaulted to zero or carried forward.

## 6. Calendar

Counting horizons on a wrong calendar silently shifts every label, so the calendar is validated and fails closed:
`validate_calendar` rejects empty, weekend dates, non-increasing/duplicate dates, and any gap longer than 4 days (a missing session). `sessions.derive_sessions` builds it
from `stock_prices` coverage (a date counts when at least half the median number of symbols have a bar), refuses when the S&P index series has a bar on a date the
stock-derived calendar lacks, and optionally requires exact agreement with an independent authoritative calendar (e.g. the Alpaca one in `shared.market_calendar`).
`as_of_session` must be a session of the calendar, so an as-of date without bars cannot run. **Residual risk:** a session absent from both stocks and the index, not
producing a >4-day gap, is undetectable from the database alone; pass the authoritative calendar when activating.

## 7. Schema and privileges

`forward_return_label` (migration 26): identity (`observation_id` FK, `horizon_sessions`, `label_version`, `methodology_version`, `label_status`, `void_reason`), copies checked
against the observation by `research_label_consistency()` (`symbol`, `direction`, `t0_session`), `horizon_session`, `computed_as_of_session`, reference/provenance
(`reference_close`, `entry_close_captured`, `basis_ratio`, `horizon_close`), outcomes (`raw_return`, `directional_return`, `benchmark_symbol`, `benchmark_state`,
`benchmark_return`, `excess_return`, `directional_excess_return`, `path_state`, `mfe`, `mae`, `bars_expected`, `bars_observed`), quality and reproducibility (`data_quality`,
`dq_details` JSONB, `price_source`, `price_basis`, `calendar_source`, `input_hash`, `code_ref`, `computed_at`). `label_version = 'fwd_v1'`, `methodology_version = 'fwd_v1.m1'`.
Immutable: `ENABLE ALWAYS` row and truncate triggers (they survive `session_replication_role = replica`); a correction is a new `label_version`.

Privileges (`deploy/db/research_roles.sql` section 7, verified by `research_roles_verify.sql`): `donchian_app` **SELECT + INSERT** only (no UPDATE, DELETE or TRUNCATE; the new
table is on the full-DML exclusion list), `donchian_research_admin` SELECT, owner `donchian_owner`. The runner's minimum privileges are therefore
SELECT on `candidate_observation`, `stock_prices`, `market_index_prices`, `forward_return_label` and INSERT on `forward_return_label`. It touches nothing else and never `signal_ledger`.

## 8. Operating it (not authorised for production)

```
python -m research.labels.runner --as-of 2026-10-02                 # dry run, writes nothing
python -m research.labels.runner --as-of 2026-10-02 --apply         # writes; needs migration 26 + roles re-run
python -m research.labels.runner --as-of 2026-10-02 --restatements  # read-only drift report
```
No systemd timer, no importer in the pipeline (a static test pins that nothing outside the package imports it). Activation is a separate, owner-authorised stage after S11:
apply 26, re-run and verify the roles script, run the dry run, review, then `--apply`. There is **no production backfill** here; the first apply would label whatever
observations exist (capture is OFF in production, so there are none yet).

## 9. What CI must prove

- `mechanism/research/tests/test_fwd_v1_engine.py` (pure, 50 tests): maturity at N-1 and N for every horizon, later bars ignored, as-of independence, long/short, benchmark-relative,
  MFE/MAE, missing/halted/delisted/invalid bars, benchmark gaps, split and reverse-split discontinuities, calendar validation, hash sensitivity, no clock/IO in the engine.
- `mechanism/research/tests/test_fwd_v1_db.py` (real Postgres scratch schema with migrations 19-22 and 26 on the real base schema): the DB constraints and immutability (update, delete,
  truncate, replication-role bypass), identity consistency trigger, void/final constraints, idempotency, concurrency (four simultaneous runners), dry-run, as-of explicitness,
  fail-closed calendar, restatement reporting without rewrite, split-adjusted history, basis flag.
- `mechanism/research/tests/test_roles_full_schema.py` and `test_roles.py` (need a **superuser** DB role; skipped locally on the non-superuser dev role, **a skip in CI is a failure**):
  `donchian_app` has SELECT + INSERT and nothing else on `forward_return_label` and its functions, against a full compose bootstrap that includes migration 26.
- `mechanism/shared/tests/test_migration_registry.py`: compose mounts, SQL files and registry rows agree.
- `mechanism/research/tests/test_import_separation.py`: only `labels/repository.py` writes the table, only `INSERT`, nothing outside the package imports the engine, no clock, no ledger access.
