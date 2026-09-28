-- mechanism/add_strategy_identity_release_a.sql
-- Release A of the Strategy Intelligence architecture (agent_reports/architecture/
-- 2026-09-28_strategy-intelligence-ml-architecture.md, corrected per 2026-09-28 review):
-- give every signal_ledger row a real strategy identity/version, and fix the ledger's
-- duplicate-signal policy, BEFORE any real production signal accumulates under the old,
-- ambiguous-identity constraint. No candidate-observation or feature-snapshot tables yet --
-- those are Release B. Additive/backfill only, per CLAUDE.md's migration invariant.

-- 1. Strategy identity: a strategy is a (key, version) pair, versions are separate rows,
-- never an UPDATE to an existing version's definition. `donchian_breakout` v1 is what
-- ships today; a v2 later is a NEW row, so historical signals keep pointing at exactly
-- the version that produced them.
CREATE TABLE IF NOT EXISTS strategies (
    id               BIGSERIAL PRIMARY KEY,
    strategy_key     VARCHAR(60) NOT NULL,
    strategy_version VARCHAR(20) NOT NULL,
    description      TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (strategy_key, strategy_version)
);

INSERT INTO strategies (strategy_key, strategy_version, description)
VALUES ('donchian_breakout', 'v1', 'The live multi-timeframe Donchian breakout screener, as shipped today.')
ON CONFLICT (strategy_key, strategy_version) DO NOTHING;

-- 2. signal_ledger gains a real strategy identity, denormalized onto the row itself (not
-- only reachable via a join) so a signal's full lineage is self-evident from one row, and
-- placeholders for the lineage columns Release B will populate (feature_set_version stays
-- NULL until a feature-snapshot layer exists to produce one -- never fabricated).
ALTER TABLE signal_ledger ADD COLUMN IF NOT EXISTS strategy_id BIGINT REFERENCES strategies(id);
ALTER TABLE signal_ledger ADD COLUMN IF NOT EXISTS strategy_version VARCHAR(20);
ALTER TABLE signal_ledger ADD COLUMN IF NOT EXISTS feature_set_version VARCHAR(20);
ALTER TABLE signal_ledger ADD COLUMN IF NOT EXISTS model_version VARCHAR(80);
ALTER TABLE signal_ledger ADD COLUMN IF NOT EXISTS observation_id BIGINT;
ALTER TABLE signal_ledger ADD COLUMN IF NOT EXISTS feature_snapshot_id BIGINT;

-- Backfill any pre-existing rows (there should be none in production yet -- this table has
-- not been deployed -- but the backfill is here so the column can be made NOT NULL safely
-- regardless) to the seed strategy, then enforce NOT NULL going forward.
UPDATE signal_ledger
SET strategy_id = (SELECT id FROM strategies WHERE strategy_key = 'donchian_breakout' AND strategy_version = 'v1'),
    strategy_version = 'v1'
WHERE strategy_id IS NULL;

ALTER TABLE signal_ledger ALTER COLUMN strategy_id SET NOT NULL;
ALTER TABLE signal_ledger ALTER COLUMN strategy_version SET NOT NULL;

-- 3. Fix the duplicate-signal policy. The original UNIQUE(symbol, signal_date, direction)
-- was two different rules conflated into one, and only actually enforced the weaker one:
--
--   EVENT IDEMPOTENCY  -- the same strategy cannot record the same symbol/direction signal
--                          twice for the same session (a same-day screener re-run must not
--                          duplicate a row). This is what the old constraint actually did.
--
--   POSITION INVARIANT -- a given (symbol, strategy, direction) must never have more than
--                          one OPEN position at a time, regardless of what day each signal
--                          fired on. The old constraint did NOT enforce this: three separate
--                          bullish NVDA/donchian_breakout signals on three different days
--                          would each satisfy UNIQUE(symbol, signal_date, direction) and all
--                          three would insert as separate open rows.
--
-- Two separate constraints for two separate invariants:
ALTER TABLE signal_ledger DROP CONSTRAINT IF EXISTS signal_ledger_symbol_signal_date_direction_key;

ALTER TABLE signal_ledger ADD CONSTRAINT signal_ledger_event_identity_key
    UNIQUE (symbol, strategy_id, direction, signal_date);

-- Partial unique index enforced by Postgres itself, not just application logic in
-- signal_ledger_writer.py -- the position invariant holds even against a future direct
-- insert path or a race, not only through this one writer's own pre-check.
CREATE UNIQUE INDEX IF NOT EXISTS idx_signal_ledger_one_open_position
    ON signal_ledger (symbol, strategy_id, direction)
    WHERE status = 'open';
