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
