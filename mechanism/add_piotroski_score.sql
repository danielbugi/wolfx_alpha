-- Add Piotroski F-Score to quarterly_fundamentals
-- File Location: mechanism/add_piotroski_score.sql
-- Run this against an existing database (created before 2026-09-17).
--
-- Why: mechanism/shared/tiingo_client.py's get_quarterly_statements() pulls
-- a ready-made Piotroski F-Score (0-9) from Tiingo's Fundamentals API
-- 'overview' section -- a well-established fundamental-turnaround signal,
-- not available from the yfinance path at all. Added specifically to feed
-- ml_training/data_preparation/feature_builder.py's earnings-trajectory
-- features (see CLAUDE.md changelog, 2026-09-17).

ALTER TABLE quarterly_fundamentals
    ADD COLUMN IF NOT EXISTS piotroski_f_score SMALLINT;
