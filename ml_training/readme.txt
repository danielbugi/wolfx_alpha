ml_training/ — see the authoritative docs instead of this file
================================================================

This file described a version of the ML training pipeline that no longer exists: it references
automation/, ml_training/data_preparation/feature_builder.py, ml_training/deployment/ml_integration.py
and model_registry.py, and setup_ml_environment.py — all confirmed dead by the 2026-09-25 codebase
audit and removed 2026-09-26 (Phase 4B). The old JSON output format, table names, and CLI commands
below no longer match the current pipeline. Replaced 2026-09-27 rather than kept in sync, since a
second, drifting copy of this information is exactly what caused it to go stale in the first place.

Current, accurate documentation:
- docs/architecture/SYSTEM_OVERVIEW.md §4/§5 — what ml_training/ owns, where new ML work goes.
- docs/architecture/CODEBASE_AUDIT.md, "### ml_training/" — the live chain (features/price_features.py
  -> data_preparation/build_dataset.py -> models/momentum_predictor.py) and what's LEGACY/DEAD/why.
- docs/architecture/DATABASE.md §3 "Screener / ML" — ml_breakout_dataset_v2, ml_models, ml_predictions
  and the other tables this pipeline reads/writes.
- docs/dev/TESTING.md — how to run ml_training/tests.
- docs/history/DATA_ML_MILESTONES.md — why the promotion gate has never let a model into production
  (working as designed, not a bug — `ml_predict_momentum()` returns a `no_model`/reason code).

The pre-Phase-4B version of this file, if you need the historical detail, is in git history.
