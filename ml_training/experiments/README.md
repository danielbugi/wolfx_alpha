# ml_training/experiments

Offline, read-only research scripts (not imported by the pipeline, the backend or the live signal path). Run them against an ISOLATED copy of the database, never production.

- regime_and_ml_study.py: data quality, strategy baseline by direction/year (month-block bootstrap), market regime from the price panel, out-of-sample ML on the exact trade-plan outcome.
- ranker_stability.py: year-by-year stability of parameter-free rankers (declares how many candidates were tried).

Results of the 2026-10-11 run: docs/research/QUANT_LAB_PHASE1_FINDINGS.md and docs/research/evidence/quant_lab_2026-10-11/.
