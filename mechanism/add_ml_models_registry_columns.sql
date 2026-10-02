-- Migration 23: the three ml_models columns that ml_training/models/momentum_predictor.py used to create at runtime.
-- File Location: mechanism/add_ml_models_registry_columns.sql
--
-- WHY: register_model() ran `ALTER TABLE ml_models ADD COLUMN IF NOT EXISTS ...` on every promotion. That is runtime DDL:
-- it needs table OWNERSHIP (even when the column already exists), so it would fail for the least-privilege runtime role
-- (donchian_app) -- inside a try/except that only printed a WARNING, i.e. a promoted model silently left unregistered.
-- The schema now lives here, and the application only performs a read-only capability check
-- (momentum_predictor.missing_registry_columns) before it trains/promotes anything.
--
-- Additive and forward-safe only: no DROP, no type change, no data change, safe to re-run. Apply as the table owner /
-- superuser (docs/operations/RELEASE_B_ACTIVATION.md). IMMUTABLE once committed: never edit this file; any further
-- ml_models change is a NEW numbered migration.
--
-- NOTE: production had none of these columns when this was written (2026-10-02 read-only audit), because register_model
-- had never completed there; the first gate-passing training run is what would have created them.

ALTER TABLE ml_models ADD COLUMN IF NOT EXISTS evaluation JSONB;
ALTER TABLE ml_models ADD COLUMN IF NOT EXISTS feature_set_version VARCHAR(20);
ALTER TABLE ml_models ADD COLUMN IF NOT EXISTS target VARCHAR(30);
