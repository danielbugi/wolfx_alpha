-- mechanism/add_fundamental_inflection_tables.sql   (migration 40 -- Fundamental Inflection Engine: features, events, alert delivery log, run log)
--
-- Additive only (IF NOT EXISTS); touches no existing table. Apply manually:  psql -v ON_ERROR_STOP=1 -1 -f mechanism/add_fundamental_inflection_tables.sql
-- Applying it activates nothing: the tables stay empty until mechanism/inflection/updater.py runs.
--
--   fundamental_feature          one row per (symbol, fiscal quarter, method version): the normalized metrics, the quarter's inflection score and its components, the availability date
--                                (when the figures were public) and its basis, the source and its timestamp, and a data-quality status.
--   fundamental_inflection_event one row per detected event, identified by (symbol, quarter end, event type, method version). Idempotent: re-detecting the same event changes nothing;
--                                a restated quarter that changes the event's content updates it and increments revision_count (event_hash differs); first_detected_at is DB-stamped.
--   inflection_alert_delivery    what was sent where, once: UNIQUE(dedupe_key) is the atomic claim, so a re-run can never send the same alert twice.
--   inflection_run_log           one row per updater run (observability: counts, errors, duration).
-- available_at is the date the information was PUBLIC (what a backtest may use); first_detected_at is when THIS system first knew (never earlier than available_at).

CREATE TABLE IF NOT EXISTS fundamental_feature (
    symbol              VARCHAR(20)  NOT NULL,
    quarter_end         DATE         NOT NULL,
    method_version      VARCHAR(30)  NOT NULL,
    fiscal_year         INTEGER,
    fiscal_quarter      INTEGER,
    available_at        DATE         NOT NULL,
    availability_basis  VARCHAR(40)  NOT NULL,
    metrics             JSONB        NOT NULL,
    score               NUMERIC(5,1) NOT NULL,
    components          JSONB        NOT NULL,
    data_quality        VARCHAR(12)  NOT NULL,
    quality_notes       JSONB        NOT NULL DEFAULT '[]'::jsonb,
    source              VARCHAR(40)  NOT NULL,
    source_updated_at   TIMESTAMPTZ,
    computed_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    PRIMARY KEY (symbol, quarter_end, method_version),
    CONSTRAINT fundamental_feature_quality_chk CHECK (data_quality IN ('ok', 'partial'))
);
CREATE INDEX IF NOT EXISTS idx_fundamental_feature_available ON fundamental_feature (available_at DESC);

CREATE TABLE IF NOT EXISTS fundamental_inflection_event (
    id                  BIGSERIAL    PRIMARY KEY,
    symbol              VARCHAR(20)  NOT NULL,
    quarter_end         DATE         NOT NULL,
    event_type          VARCHAR(50)  NOT NULL,
    method_version      VARCHAR(30)  NOT NULL,
    fiscal_year         INTEGER,
    fiscal_quarter      INTEGER,
    strength            NUMERIC(5,1) NOT NULL,
    severity            VARCHAR(8)   NOT NULL,
    score               NUMERIC(5,1) NOT NULL,
    metrics             JSONB        NOT NULL,
    explanation         JSONB        NOT NULL,
    components          JSONB        NOT NULL,
    context             JSONB        NOT NULL DEFAULT '{}'::jsonb,       -- market context at the last run (reaction, return vs sector, valuation); NOT part of the event's identity
    available_at        DATE         NOT NULL,
    availability_basis  VARCHAR(60)  NOT NULL,
    source              VARCHAR(40)  NOT NULL,
    source_updated_at   TIMESTAMPTZ,
    data_quality        VARCHAR(12)  NOT NULL,
    quality_notes       JSONB        NOT NULL DEFAULT '[]'::jsonb,
    event_hash          CHAR(64)     NOT NULL,
    revision_count      INTEGER      NOT NULL DEFAULT 0,
    first_detected_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT fundamental_inflection_event_identity UNIQUE (symbol, quarter_end, event_type, method_version),
    CONSTRAINT fundamental_inflection_event_severity_chk CHECK (severity IN ('low', 'medium', 'high')),
    CONSTRAINT fundamental_inflection_event_hash_chk CHECK (event_hash ~ '^[0-9a-f]{64}$')
);
CREATE INDEX IF NOT EXISTS idx_fie_available ON fundamental_inflection_event (available_at DESC);
CREATE INDEX IF NOT EXISTS idx_fie_type_strength ON fundamental_inflection_event (event_type, strength DESC);
CREATE INDEX IF NOT EXISTS idx_fie_symbol ON fundamental_inflection_event (symbol, quarter_end DESC);

CREATE TABLE IF NOT EXISTS inflection_alert_delivery (
    id            BIGSERIAL    PRIMARY KEY,
    event_id      BIGINT       REFERENCES fundamental_inflection_event (id),
    channel       VARCHAR(30)  NOT NULL,       -- telegram_owner | email_digest | email_weekly | email_research
    recipient_ref VARCHAR(60)  NOT NULL,       -- a label ('owner'), never an address
    dedupe_key    VARCHAR(200) NOT NULL,
    status        VARCHAR(12)  NOT NULL DEFAULT 'claimed',     -- claimed | sent | failed
    attempts      INTEGER      NOT NULL DEFAULT 0,
    claimed_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    sent_at       TIMESTAMPTZ,
    last_error    TEXT,
    CONSTRAINT inflection_alert_delivery_dedupe UNIQUE (dedupe_key),
    CONSTRAINT inflection_alert_delivery_status_chk CHECK (status IN ('claimed', 'sent', 'failed'))
);

CREATE TABLE IF NOT EXISTS inflection_run_log (
    id               BIGSERIAL    PRIMARY KEY,
    method_version   VARCHAR(30)  NOT NULL,
    started_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    finished_at      TIMESTAMPTZ,
    asof             DATE         NOT NULL,
    symbols_due      INTEGER      NOT NULL DEFAULT 0,
    symbols_processed INTEGER     NOT NULL DEFAULT 0,
    features_written INTEGER      NOT NULL DEFAULT 0,
    events_new       INTEGER      NOT NULL DEFAULT 0,
    events_revised   INTEGER      NOT NULL DEFAULT 0,
    errors           INTEGER      NOT NULL DEFAULT 0,
    status           VARCHAR(12)  NOT NULL DEFAULT 'running',  -- running | ok | partial | failed
    note             TEXT
);

-- Read access for the dashboard API login when that role exists (SELECT only; the writer is the mechanism's own login).
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'donchian_app') THEN
        -- donchian_app is the role the pipeline runs as (updater + owner alerts WRITE these tables) and the API runs as (READS): tables created by a manual apply do not inherit the
        -- default privileges the older tables have, so the grant must be explicit. No DELETE: nothing deletes. (Found in production 2026-10-31, applied there by hand.)
        GRANT SELECT, INSERT, UPDATE ON fundamental_feature, fundamental_inflection_event, inflection_alert_delivery, inflection_run_log TO donchian_app;
        GRANT USAGE, SELECT ON SEQUENCE fundamental_inflection_event_id_seq, inflection_alert_delivery_id_seq, inflection_run_log_id_seq TO donchian_app;
    END IF;
END;
$$;
