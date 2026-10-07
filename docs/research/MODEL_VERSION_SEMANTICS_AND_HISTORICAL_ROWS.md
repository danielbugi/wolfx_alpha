# model_version semantics, and the historical 'unknown' rows

Status: DECISION RECORD, prepared on lab branch `lab/first-light-algo`. Nothing here has been run against production.

## Contract (A1, implemented on the lab branch, not merged, not deployed)

`signal_ledger.model_version` and `candidate_observation.ml_model_version` name **the model that actually scored that signal**.
They are NULL when no validated model did: no model loaded, signal not processed (near-breakout, illiquid, guard-dropped),
prediction failed, or the model reported no version. They are never a placeholder, and a model that was merely loaded while the
signal went unscored is not credited. One definition: `mechanism/shared/model_provenance.py::scoring_model_version`.

| Where | Before | After |
|---|---|---|
| `multi_timeframe_screener.enhance_signals_with_ml` | `ml_data.get('ml_model_version', 'unknown')` -> `'unknown'` whenever the enhancer returned `_unavailable()` (no key) | `scoring_model_version(ml_data)` -> NULL unless scored |
| `multi_timeframe_screener._merge_ml_scores` (signal never ML-processed) | the loaded enhancer's version | NULL |
| `multi_timeframe_screener` fallback branch (no ML entry for the signal) | key absent | explicit NULL |
| prediction-log batch (`ml_predictions`) | version of `enhanced_signals[0]`, `'unknown'` if empty (signal 0 may be unscored) | version of a scored signal; the tracker's existing default only if none carries one |
| `signal_ledger_writer` | `signal.get("ml_model_version")` (stored whatever the screener put there) | `scoring_model_version(signal)` (defence in depth: the ledger can never store a placeholder even from a different caller) |
| `research.observer._funnel` | version only `if scored` (but a scored signal with `'unknown'`/blank version was stored as such) | `scoring_model_version(final)` |
| `strategy_analytics.data_health` coverage of `model_version` | `count(model_version)` counted legacy `'unknown'` as covered | excludes legacy placeholders (`LEGACY_UNSCORED_MODEL_VERSIONS`); DEFINITIONS_VERSION 2026-10-04.1 |

Preserved: `feature_snapshot` stays strategy/direction-neutral T0; `candidate_observation` stays strategy/session/direction-specific. No schema change.

## Historical rows

At the S11 baseline there are 165 production `signal_ledger` rows with `model_version = 'unknown'`. They are **not rewritten**, by instruction.
Their meaning is unambiguous (the screener wrote `'unknown'` precisely when no validated model scored the signal), so no information is lost by leaving them.

Read-side rule until a decision is taken: every consumer treats `'unknown'` (case/whitespace-insensitive) as "not scored" = NULL.
Implemented for data-health coverage; the performance engine and dataset builder use the same constant.

### Options for a later, separate decision (after S11 PASS and owner approval)

| Option | Effect | Cost / risk |
|---|---|---|
| A. Leave rows, normalize on read (current) | zero production risk | one more rule every consumer must honour; raw table still contains a placeholder |
| B. One-off `UPDATE signal_ledger SET model_version = NULL WHERE model_version = 'unknown'` | table matches the new contract | changes the S11 baseline "immutable fingerprint" (it includes `model_version`) so it must be sequenced after S11 closes; touches ledger rows that the evaluator also updates; needs a maintenance window, a pre/post fingerprint, and a backup restore point |
| C. Add a read view (`signal_ledger_normalized`) | no data change, one canonical read path | extra object, grants for `donchian_app` |

Recommendation: A now; B only if the owner wants the raw table clean, as its own authorized stage. Prepared (NOT run) verification query for that stage:

```sql
-- read-only audit
SELECT model_version, count(*) FROM signal_ledger GROUP BY 1 ORDER BY 2 DESC;
-- B (do not run): UPDATE signal_ledger SET model_version = NULL WHERE lower(btrim(model_version)) = 'unknown';
```
Rollback for B: restore from the pre-update backup, or `UPDATE ... SET model_version='unknown' WHERE id = ANY(<saved id list>)`.

## Activation
None needed beyond a normal mechanism image build + manual CD, after S11. The change only alters values written going forward.

---

## S11 measurement and the completeness audit of the fix (2026-10-07, before the activation candidate)

**Measured in production (read-only, S11 final checkpoint).** New ledger rows since the 2026-10-02 baseline: 225 (10-05: 126, 10-06: 99) = **222 `'unknown'`**, 3 NULL, 0 real versions;
by grade A 31 / B 45 / C 31 / D 34 / F 81 `unknown` (the 3 NULL: B 1, F 2). All ledger rows now: 860 NULL, **387 `'unknown'`** (165 from 10-02 + 222), so the historical placeholder
population grows by about 100-125 rows per session until the image containing this fix is pinned. The defect reproduces exactly as diagnosed. Evidence: `docs/research/evidence/s11_final_2026-10-07/`.

**Why NULL is the correct representation.** The column answers "which model scored this signal". With no validated model (production has none: the pipeline logs "No ML models found", and the nightly candidate models were NOT PROMOTED on both observed sessions) the true answer is "none", i.e. unknown-because-nonexistent, which SQL expresses as NULL. A placeholder string is a value that a filter
(`WHERE model_version = ...`), a `GROUP BY`, a `count(model_version)` coverage figure or an equality join treats as a real model; NULL is excluded by all of them and
cannot be mistaken for a version. The legacy `'unknown'` also could not be told apart from a genuinely unversioned model, and the screener wrote it for signals that were never
scored at all. The schema already supports it: `signal_ledger.model_version VARCHAR(80)` is nullable with no default and no CHECK; `candidate_observation.ml_model_version` likewise.

**Audit: every producer and consumer.**
| Where | Role | State on the lab branch |
|---|---|---|
| `ml_signal_enhancer` (`self.ml_model_version`, three result dicts) | produces the loaded model's name; `_unavailable()` has no version key | unchanged: never trusted on its own |
| `shared/model_provenance.scoring_model_version` | the single definition: a version only when `ml_prediction_available` and a finite probability and a real, non-placeholder string | the one predicate |
| `multi_timeframe_screener` (enhance, merge fallback, no-ML fallback, prediction-log batch) | writes `ml_model_version` into each signal | all four use the predicate or an explicit NULL; the `'unknown'` default is gone |
| `signal_ledger_writer.write_signals` | the only writer of `signal_ledger.model_version` | `scoring_model_version(signal)`: defence in depth, a different caller cannot store a placeholder either |
| `research/observer._funnel` | `candidate_observation.ml_model_version` | the same predicate |
| `strategy_analytics` (`performance.py` `model_scored` bucket, `analytics.py` coverage/filter, `definitions.LEGACY_UNSCORED_MODEL_VERSIONS`) | read side | NULL and the legacy `'unknown'` are both "not scored"; coverage excludes both |
| `backend/routers/strategy_intelligence` | optional `model_version` filter (max 80) | equality filter: a NULL row is simply not matched |
| frontend `strategyApi.ts` / `SignalDetailDialog` / `ResearchTab` | types `string | null`; renders "Not scored (no validated model)" | already NULL-aware |
| `alerts/strategy_screens` | "not scored (no validated model)" when falsy | already NULL-aware |
| `ml_training/evaluation/performance_tracker.record_predictions(model_version="unknown")` | writes **`ml_predictions.model_version`**, a different table (the ML prediction log, unique on (symbol, date)) | **deliberately unchanged**: it is not `signal_ledger`, the screener now passes a real version whenever one scored, and its metrics `groupby('model_version')`, which drops NULL keys, so a NULL would silently remove those predictions from the performance metrics; recorded here as the one remaining `'unknown'` producer, outside the S11 defect |

**Tests that encode `'unknown'`** are all read-side or negative: `test_strategy_analytics` (legacy rows are not coverage), `test_model_provenance` (placeholders are never a version),
`test_signal_ledger_writer` (a placeholder input is stored as NULL), `test_observer`. None asserts that `'unknown'` is *written*. Added now: two end-to-end regression tests
(`test_universe_guards.py`) that drive the S11-observed shape (no model scored, grades A-F) through the real screener merge **and** the real ledger writer and require NULL for every grade, and a
version only on the scored signal; verified to FAIL on the pre-fix code and pass on the fix.

**Migration: none required.** The fix changes only the value written going forward. No schema object, constraint, default, grant or backfill is involved.
Historical rows are not modified (387 and counting remain `'unknown'`); the read side already treats them as unscored. A one-off normalisation (option B above) would change the immutable ledger
fingerprint (`model_version` is part of it) and stays a separate owner decision.

**Production effect.** Nothing until a mechanism image built from the activation candidate is pinned (runbook step "pin the image"). From the first pipeline run after the pin, new ledger rows carry NULL.
