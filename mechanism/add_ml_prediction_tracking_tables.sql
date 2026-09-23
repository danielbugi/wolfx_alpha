-- mechanism/add_ml_prediction_tracking_tables.sql
--
-- Captures 4 tables that were created directly against production (no tracked migration ever
-- created them) and are actively read/written by live application code -- see
-- docs/devops/PRODUCTION_SCHEMA_DRIFT.md for the full per-table investigation this file is based
-- on. This migration does NOT change production's schema in any way; it makes the *repository*
-- able to reproduce what production already has, for fresh installs, CI, and local development.
--
-- Safe to run against:
--   (A) a fresh, empty database -- creates all 4 tables, their sequences, and grants, exactly
--       matching production's real structure.
--   (B) an existing production database where these tables already exist -- every `IF NOT
--       EXISTS` statement below is a no-op in that case: Postgres sees the relation already
--       exists and skips the statement entirely, without parsing or comparing the columns/
--       constraints inside it. This is NOT schema validation or reconciliation -- it does not
--       check that an existing table matches the definition below. The reason this migration is
--       safe to run against the current production database is that its definitions were derived
--       directly from Phase 4A.5's independent, read-only inspection of production's real
--       structures (documented in PRODUCTION_SCHEMA_DRIFT.md), not from any guarantee this file
--       or Postgres provides at apply time.
-- No DROP, no data transformation, no DELETE/UPDATE, no destructive ALTER anywhere in this file.

-- =============================================================================
-- inactive_symbols -- natural key (symbol), no sequence
-- =============================================================================

CREATE TABLE IF NOT EXISTS inactive_symbols (
    symbol varchar(10) NOT NULL,
    reason varchar(200),
    marked_inactive_date date DEFAULT CURRENT_DATE,
    CONSTRAINT inactive_symbols_pkey PRIMARY KEY (symbol)
);

GRANT ALL PRIVILEGES ON inactive_symbols TO trading_user;

-- =============================================================================
-- ml_predictions -- referenced by ml_prediction_outcomes below, so created first
-- =============================================================================

CREATE SEQUENCE IF NOT EXISTS ml_predictions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

CREATE TABLE IF NOT EXISTS ml_predictions (
    id integer NOT NULL DEFAULT nextval('ml_predictions_id_seq'::regclass),
    symbol varchar(10),
    prediction_date date,
    breakout_type varchar(20),
    entry_price numeric(10,2),
    ml_probability numeric(5,3),
    ml_confidence varchar(20),
    ml_recommendation varchar(20),
    ml_risk_score integer,
    model_version varchar(50),
    created_at timestamp DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT ml_predictions_pkey PRIMARY KEY (id),
    CONSTRAINT ml_predictions_symbol_prediction_date_key UNIQUE (symbol, prediction_date)
);

ALTER SEQUENCE ml_predictions_id_seq OWNED BY ml_predictions.id;

GRANT ALL PRIVILEGES ON ml_predictions TO trading_user;
GRANT ALL PRIVILEGES ON ml_predictions_id_seq TO trading_user;

-- =============================================================================
-- ml_prediction_outcomes -- FK to ml_predictions(id)
-- =============================================================================

CREATE SEQUENCE IF NOT EXISTS ml_prediction_outcomes_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

CREATE TABLE IF NOT EXISTS ml_prediction_outcomes (
    id integer NOT NULL DEFAULT nextval('ml_prediction_outcomes_id_seq'::regclass),
    prediction_id integer,
    symbol varchar(10),
    prediction_date date,
    evaluation_date date,
    days_elapsed integer,
    actual_return_pct numeric(8,2),
    max_gain_pct numeric(8,2),
    max_loss_pct numeric(8,2),
    momentum_achieved boolean,
    momentum_score integer,
    created_at timestamp DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT ml_prediction_outcomes_pkey PRIMARY KEY (id),
    CONSTRAINT ml_prediction_outcomes_prediction_id_fkey FOREIGN KEY (prediction_id) REFERENCES ml_predictions(id)
);

ALTER SEQUENCE ml_prediction_outcomes_id_seq OWNED BY ml_prediction_outcomes.id;

GRANT ALL PRIVILEGES ON ml_prediction_outcomes TO trading_user;
GRANT ALL PRIVILEGES ON ml_prediction_outcomes_id_seq TO trading_user;

-- =============================================================================
-- ml_performance_metrics -- no FK
-- =============================================================================

CREATE SEQUENCE IF NOT EXISTS ml_performance_metrics_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

CREATE TABLE IF NOT EXISTS ml_performance_metrics (
    id integer NOT NULL DEFAULT nextval('ml_performance_metrics_id_seq'::regclass),
    metric_date date,
    model_version varchar(50),
    total_predictions integer,
    correct_predictions integer,
    accuracy numeric(5,3),
    precision_high_prob numeric(5,3),
    recall_high_prob numeric(5,3),
    avg_return_predicted_high numeric(8,2),
    avg_return_predicted_low numeric(8,2),
    sharpe_ratio numeric(6,3),
    max_drawdown numeric(8,2),
    created_at timestamp DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT ml_performance_metrics_pkey PRIMARY KEY (id)
);

ALTER SEQUENCE ml_performance_metrics_id_seq OWNED BY ml_performance_metrics.id;

GRANT ALL PRIVILEGES ON ml_performance_metrics TO trading_user;
GRANT ALL PRIVILEGES ON ml_performance_metrics_id_seq TO trading_user;
