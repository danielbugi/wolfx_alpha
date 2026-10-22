# Proposal A — Data-driven capability flags and coverage labels

Status: **proposal for owner review**, 2026-10-22. Read-only research; no code changed.

## Problem (verified in the running production image `662cb38593ed`)

| Served value | Where | Reality in production (2026-10-22) |
|---|---|---|
| `forward_return_labels: available=False, release B` | `strategy_analytics/definitions.CAPABILITIES` (static dict, last edited 2026-10-04) | `forward_return_label`: 29,427 rows, horizons 1/3/5 sessions, from t0 2026-10-09 |
| `candidate_observations: False` / `feature_snapshots: False` / `training_ready_observations: False` | same | 14,629 candidates and 14,629 snapshots, 9 sessions from 2026-10-09; 1,468 ledger rows linked |
| `forward_outcomes: not_available — "migration 23 and the fwd_v1 label set do not exist yet"` | `strategy_analytics/research.FORWARD_OUTCOMES` (static, 2026-10-01) | The labels exist; no reader serves them |
| `UNAVAILABLE_DIMENSIONS[relative_strength].requires = "sector_snapshot / feature_snapshot (migrations 22, 24)"` | `performance.py` (static) | Observed `stock_relative_strength` (`rs_v1`) exists from 2026-10-12, covering 1,359 / 2,973 ledger rows |
| Research `availability()` | `research.py` | **Data-driven and correct** (schema check plus capture-run count), so the same API both affirms and denies the candidate data |

**Root cause:** availability was declared once as constants and never re-derived.

## Proposal

1. **One availability function per capability**, computed from the database at request time with a cheap `EXISTS` and a cached `count`, never from a constant. Each returns:
   ```json
   {"capability": "forward_return_labels", "state": "ok|no_data|not_available|partial",
    "rows": 29427, "first_session": "2026-10-09", "last_session": "2026-10-19",
    "coverage": {"ledger_rows_linked": 696, "ledger_rows_total": 2973},
    "provenance": "observed", "release": "B", "reason": null}
   ```
   - **`not_available`**: the table does not exist (migration absent). The reason names the migration.
   - **`no_data`**: the table exists and is empty.
   - **`partial`**: rows exist, but the coverage of ledger rows is below 100%. Always the case for anything that started after 2026-09-28.
   - **`ok`**: rows exist and coverage is complete for the window it describes.
2. **Coverage labels are mandatory for every dimension.** Report `n` covered / `n` total ledger rows, plus the first observed session. A ledger row before that session reads **"not observed"**, never "neutral", "0" or filled from later data.
3. **Provenance on every value:** `observed` (our own forward capture, DB-stamped), `derived` (computed now from observed prices, e.g. MFE, post-exit path, VIX tercile), `reconstructed` (not point-in-time inputs, e.g. modern sector membership), `retrospective` (the vendor's current value for a past moment, e.g. today's earnings calendar). Unlabelled values are rejected by tests.
4. **Timing caveat as data.** For regime and RS: `available_at` = the snapshot `captured_at` (about 03:45 UTC on the next day), with `valid_for: "next_open_decision"`. The ledger's close-entry convention is flagged when it is joined.
5. **Remove the stale stubs:**
   - `FORWARD_OUTCOMES` becomes a real read-only summary of `forward_return_label` (by horizon and direction, final labels only, with a day-cluster CI).
   - `UNAVAILABLE_DIMENSIONS` keeps only catalyst and ML (still truly unavailable).
   - Regime, RS and VIX become **partial** dimensions with coverage.

## What is NOT proposed

- Backfilling any table, or converting retrospective or reconstructed values into "observed".
- Changing a ledger row or the evaluator.
- Any migration.

## Tests

- **States:** each state (`not_available`, `no_data`, `partial`, `ok`) is produced from a scratch schema (self-skipping real-Postgres tests, as in `db-bootstrap-integration`).
- **Guard against drift:** a test fails if any capability is declared by a constant.
- **Labels:** pre-coverage ledger rows read "not observed"; every value carries a provenance label.
- **Cost:** the availability calls add at most one cheap query per capability, with the same aggregate-once rule as PR #52 (no per-row lookups).

## Delivery

One backend PR. It ships with a normal health-gated backend release and is independent of the mechanism pin and Stage 2. Rollback: the previous backend image.
