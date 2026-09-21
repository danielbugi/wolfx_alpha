-- Widen ml_models.version from VARCHAR(20) to VARCHAR(50)
-- File Location: mechanism/widen_ml_models_version.sql
-- Run this against an existing database.
--
-- Why: momentum_predictor.py's actual version strings are
-- "momentum_predictor_v<YYYYMMDD>_<HHMM>" (34 chars). VARCHAR(20) went
-- undetected since the table's creation because register_model() (the
-- first thing to ever INSERT into ml_models) was only added 2026-09-18 --
-- see CLAUDE.md §4/§9. Caught on the very first real training run.

ALTER TABLE ml_models ALTER COLUMN version TYPE VARCHAR(50);
