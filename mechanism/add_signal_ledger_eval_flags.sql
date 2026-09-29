-- mechanism/add_signal_ledger_eval_flags.sql
-- Migration 21: two nullable flag columns on signal_ledger, written only by
-- mechanism/screeners/evaluate_signal_ledger.py. They are separate on purpose -- they
-- describe different states and must never be conflated downstream (analytics, Data Health, ML):
--
--   resolution_flag -- metadata about an ACTUAL resolution. The row is resolved; the flag says
--                      how much the result can be trusted.
--        'same_bar_stop_and_target'  the resolving daily bar touched both the stop and a target.
--                                    Daily OHLC can't order them, so the conservative convention
--                                    (stop wins, identical to ml_training's plan_outcomes()) was
--                                    applied: status='stopped', outcome_r=-1. Research/ML can
--                                    include, exclude, or separately analyze these rows.
--
--   evaluation_flag -- a condition that is HOLDING an unresolved row. The row stays status='open'.
--        'split_suspect'             a close-to-close jump matching alerts/price_guard.is_split_like()
--                                    (the repo's one shared split rule) appeared inside the row's
--                                    evaluation window. The stored entry/stop/targets may no longer be
--                                    in the same price scale as stock_prices, so the evaluator does
--                                    not resolve the row and stops re-evaluating it until the flag is
--                                    cleared through the evaluator's explicit --clear-evaluation-flag
--                                    path after manual review. Nothing is rewritten or adjusted.
--
-- The CHECK constraints fix each column's vocabulary (like `status`): a new flag value is a
-- deliberate schema change, not a free-text string. No index: the evaluator's working set is
-- still served by idx_signal_ledger_open, and flagged rows are expected to be rare -- add one only
-- if Data Health queries on a much larger ledger ever show a need.
--
-- Additive only, per CLAUDE.md's migration invariant: nullable columns, no default, no backfill,
-- no rewrite of existing rows. Safe to re-run -- ADD COLUMN IF NOT EXISTS skips the whole clause,
-- CHECK included, when the column already exists.

ALTER TABLE signal_ledger
    ADD COLUMN IF NOT EXISTS resolution_flag VARCHAR(30)
        CHECK (resolution_flag IN ('same_bar_stop_and_target'));

ALTER TABLE signal_ledger
    ADD COLUMN IF NOT EXISTS evaluation_flag VARCHAR(30)
        CHECK (evaluation_flag IN ('split_suspect'));
