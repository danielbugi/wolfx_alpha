-- mechanism/add_dataset_experiment_registry_tables.sql   (migration 30 -- dataset manifest + experiment registry)
--
-- BEFORE any ML experiment exists, the thing it will train on and the claim it will test are written down, immutably:
--
--   dataset_manifest         what a dataset IS: code SHA, dataset version, label version + methodology + horizons, feature versions,
--                            universe definition + hash, the point-in-time cutoff, train / validation / test windows, embargo and purge
--                            (in trading sessions), calendar source, input hashes, creation time. Identity = manifest_hash.
--   experiment_registration  a PRE-registered experiment: it names one manifest, the model/configuration, the evaluation plan and the
--                            decision rule BEFORE results exist. Identity = registration_hash.
--   experiment_result        what happened, appended: validation / test / failed / abandoned. The test window can be evaluated ONCE per
--                            experiment (partial unique index): re-testing after seeing a result is a new experiment.
--
-- Leakage controls enforced in the database as well as in market_intelligence-free pure code (research.lab.manifest):
--   * embargo_sessions >= 60 + purge_sessions (60 = the longest fwd_v1 horizon, which label_horizons is confined to);
--   * windows are strictly ordered train < validation < test, separated by at least the embargo (the DB can only prove the necessary
--     calendar-day lower bound; the exact trading-session count is checked by the pure validator against the explicit calendar);
--   * the label-maturity session and the knowledge cutoff cannot precede the end of the test window.
-- These tables record and constrain; they train nothing and compute nothing. Logically they sit on migration 22 (candidate_observation)
-- and 26 (forward_return_label) but carry NO foreign key to them, so applying order is free and a manifest is a pure record.
--
-- Immutability, two layers (as migrations 24 / 26): runtime role SELECT + INSERT only, and ENABLE ALWAYS BEFORE UPDATE / DELETE row +
-- BEFORE TRUNCATE statement triggers that always raise. created_at is DB-stamped.
--
-- APPLY ATOMICALLY:   psql -v ON_ERROR_STOP=1 -1 -f mechanism/add_dataset_experiment_registry_tables.sql
-- NOT applied to production by committing this file. Re-run deploy/db/research_roles.sql (+ verifier) afterwards.
-- Collision safety. Marker 'dataset_experiment_registry_migration_30'.

DO $guard$
DECLARE
    marker CONSTANT TEXT := 'dataset_experiment_registry_migration_30';
    t TEXT;
    f TEXT;
    ix RECORD;
BEGIN
    FOREACH t IN ARRAY ARRAY['dataset_manifest', 'experiment_registration', 'experiment_result'] LOOP
        IF to_regclass(t) IS NOT NULL AND coalesce(obj_description(to_regclass(t), 'pg_class'), '') <> marker THEN
            RAISE EXCEPTION 'migration 30 refused: relation % already exists and is not a migration-30 object (no marker)', t;
        END IF;
    END LOOP;
    FOREACH f IN ARRAY ARRAY['research_registry_guard()', 'research_registry_stamp()', 'research_registry_consistency()'] LOOP
        IF to_regprocedure(f) IS NOT NULL AND coalesce(obj_description(to_regprocedure(f), 'pg_proc'), '') <> marker THEN
            RAISE EXCEPTION 'migration 30 refused: function % already exists and is not a migration-30 object (no marker)', f;
        END IF;
    END LOOP;
    FOR ix IN SELECT * FROM (VALUES
            ('idx_experiment_registration_manifest', 'experiment_registration'),
            ('idx_experiment_result_experiment',     'experiment_result'),
            ('experiment_result_one_test',           'experiment_result')) AS v(idx, tbl) LOOP
        IF to_regclass(ix.idx) IS NOT NULL AND NOT EXISTS (
               SELECT 1 FROM pg_index i WHERE i.indexrelid = to_regclass(ix.idx) AND i.indrelid = to_regclass(ix.tbl)) THEN
            RAISE EXCEPTION 'migration 30 refused: index % already exists on a different relation', ix.idx;
        END IF;
    END LOOP;
END;
$guard$;

CREATE TABLE IF NOT EXISTS dataset_manifest (
    id                          BIGSERIAL     PRIMARY KEY,
    dataset_name                VARCHAR(80)   NOT NULL,
    dataset_version             VARCHAR(40)   NOT NULL,
    manifest_hash               CHAR(64)      NOT NULL,         -- sha256 of the canonical manifest document (everything below except created_at)
    code_sha                    CHAR(40)      NOT NULL,         -- full git commit of the code that built the dataset (a clean tree only)
    label_version               VARCHAR(30)   NOT NULL,         -- e.g. fwd_v1
    label_methodology_version   VARCHAR(30)   NOT NULL,
    label_horizons              SMALLINT[]    NOT NULL,         -- horizons (sessions) of the labels the dataset reads
    feature_versions            JSONB         NOT NULL,         -- {"<feature set>": "<version>"} -- at least one
    universe_id                 VARCHAR(80)   NOT NULL,         -- the named, versioned universe definition
    universe_hash               CHAR(64)      NOT NULL,         -- sha256 of the sorted member list + the rule that produced it
    knowledge_cutoff_at         TIMESTAMPTZ   NOT NULL,         -- every input had availability <= this instant (PIT cutoff)
    label_maturity_session      DATE          NOT NULL,         -- latest session whose prices were read to finalise labels
    train_start                 DATE          NOT NULL,
    train_end                   DATE          NOT NULL,
    validation_start            DATE          NOT NULL,
    validation_end              DATE          NOT NULL,
    test_start                  DATE          NOT NULL,
    test_end                    DATE          NOT NULL,
    embargo_sessions            SMALLINT      NOT NULL,         -- trading sessions kept empty between consecutive windows
    purge_sessions              SMALLINT      NOT NULL,         -- extra sessions beyond the label horizon (boundary overlap of feature windows)
    calendar_source             VARCHAR(60)   NOT NULL,         -- the explicit trading calendar the session counts used
    input_hashes                JSONB         NOT NULL,         -- {"<input name>": "<sha256>"} -- the exact rows / files read
    config                      JSONB         NOT NULL,         -- anything else that shaped the dataset (filters, price basis ...)
    manifest                    JSONB         NOT NULL,         -- the full canonical document manifest_hash was computed from
    created_at                  TIMESTAMPTZ   NOT NULL,         -- DB-stamped
    CONSTRAINT dataset_manifest_name_version UNIQUE (dataset_name, dataset_version),
    CONSTRAINT dataset_manifest_hash_key UNIQUE (manifest_hash),
    CONSTRAINT dataset_manifest_hashes_chk CHECK (manifest_hash ~ '^[0-9a-f]{64}$' AND universe_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT dataset_manifest_sha_chk CHECK (code_sha ~ '^[0-9a-f]{40}$'),
    CONSTRAINT dataset_manifest_text_chk CHECK (length(dataset_name) > 0 AND length(dataset_version) > 0 AND length(label_version) > 0
                                                AND length(label_methodology_version) > 0 AND length(universe_id) > 0
                                                AND length(calendar_source) > 0),
    CONSTRAINT dataset_manifest_json_chk CHECK (jsonb_typeof(feature_versions) = 'object' AND feature_versions <> '{}'::jsonb
                                                AND jsonb_typeof(input_hashes) = 'object' AND input_hashes <> '{}'::jsonb
                                                AND jsonb_typeof(config) = 'object' AND jsonb_typeof(manifest) = 'object'),
    CONSTRAINT dataset_manifest_horizons_chk CHECK (cardinality(label_horizons) >= 1 AND label_horizons <@ ARRAY[1, 3, 5, 10, 20, 60]::smallint[]),
    -- 60 is the longest fwd_v1 horizon and label_horizons is confined to fwd_v1's set above, so this covers every label's forward window
    CONSTRAINT dataset_manifest_embargo_chk CHECK (purge_sessions >= 0 AND embargo_sessions >= 60 + purge_sessions),
    CONSTRAINT dataset_manifest_window_order_chk CHECK (
        train_start <= train_end AND validation_start <= validation_end AND test_start <= test_end
        AND train_end < validation_start AND validation_end < test_start),
    -- necessary lower bound only: n trading sessions need at least n calendar days (the exact session count is the pure validator's job)
    CONSTRAINT dataset_manifest_gap_days_chk CHECK (
        validation_start - train_end > embargo_sessions AND test_start - validation_end > embargo_sessions),
    CONSTRAINT dataset_manifest_cutoff_chk CHECK (
        label_maturity_session > test_end AND knowledge_cutoff_at >= (label_maturity_session::timestamp AT TIME ZONE 'UTC'))
);

CREATE TABLE IF NOT EXISTS experiment_registration (
    id                  BIGSERIAL     PRIMARY KEY,
    experiment_name     VARCHAR(100)  NOT NULL,
    registration_hash   CHAR(64)      NOT NULL,           -- sha256 of the canonical registration document (everything but created_at)
    manifest_hash       CHAR(64)      NOT NULL REFERENCES dataset_manifest (manifest_hash),
    code_sha            CHAR(40)      NOT NULL,           -- the code that will run the experiment (a clean tree only)
    label_version       VARCHAR(30)   NOT NULL,           -- copied from the manifest; checked by trigger
    feature_versions    JSONB         NOT NULL,           -- the subset of the manifest's features this experiment uses; checked by trigger
    model_spec          JSONB         NOT NULL,           -- family + configuration (all hyper-parameters that are fixed)
    search_budget       INTEGER       NOT NULL,           -- the number of configurations the experiment is allowed to try (multiple-testing accounting)
    seed                BIGINT        NOT NULL,
    evaluation_plan     JSONB         NOT NULL,           -- metrics, the primary metric, and the decision rule, written BEFORE results
    hypothesis          TEXT          NOT NULL,
    created_at          TIMESTAMPTZ   NOT NULL,           -- DB-stamped
    CONSTRAINT experiment_registration_name UNIQUE (experiment_name),
    CONSTRAINT experiment_registration_hash_key UNIQUE (registration_hash),
    CONSTRAINT experiment_registration_hash_chk CHECK (registration_hash ~ '^[0-9a-f]{64}$' AND manifest_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT experiment_registration_sha_chk CHECK (code_sha ~ '^[0-9a-f]{40}$'),
    CONSTRAINT experiment_registration_json_chk CHECK (jsonb_typeof(feature_versions) = 'object' AND feature_versions <> '{}'::jsonb
                                                       AND jsonb_typeof(model_spec) = 'object' AND model_spec <> '{}'::jsonb
                                                       AND jsonb_typeof(evaluation_plan) = 'object'
                                                       AND evaluation_plan ?& ARRAY['metrics', 'primary_metric', 'decision_rule']),
    CONSTRAINT experiment_registration_budget_chk CHECK (search_budget >= 1),
    CONSTRAINT experiment_registration_text_chk CHECK (length(experiment_name) > 0 AND length(hypothesis) > 0 AND length(label_version) > 0)
);
CREATE INDEX IF NOT EXISTS idx_experiment_registration_manifest ON experiment_registration (manifest_hash);

CREATE TABLE IF NOT EXISTS experiment_result (
    id                  BIGSERIAL     PRIMARY KEY,
    registration_hash   CHAR(64)      NOT NULL REFERENCES experiment_registration (registration_hash),
    result_kind         VARCHAR(10)   NOT NULL,           -- validation | test | failed | abandoned
    metrics             JSONB         NOT NULL,           -- {} for failed / abandoned
    n_configs_tried     INTEGER       NOT NULL,           -- must stay within the registered search_budget (checked by trigger)
    artifact_hashes     JSONB         NOT NULL,           -- {"<artifact>": "<sha256>"}: model file, prediction file ...
    code_sha            CHAR(40)      NOT NULL,
    note                TEXT,
    created_at          TIMESTAMPTZ   NOT NULL,           -- DB-stamped
    CONSTRAINT experiment_result_kind_chk CHECK (result_kind IN ('validation', 'test', 'failed', 'abandoned')),
    CONSTRAINT experiment_result_hash_chk CHECK (registration_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT experiment_result_sha_chk CHECK (code_sha ~ '^[0-9a-f]{40}$'),
    CONSTRAINT experiment_result_json_chk CHECK (jsonb_typeof(metrics) = 'object' AND jsonb_typeof(artifact_hashes) = 'object'),
    CONSTRAINT experiment_result_metrics_chk CHECK ((result_kind IN ('validation', 'test')) = (metrics <> '{}'::jsonb)),
    CONSTRAINT experiment_result_tried_chk CHECK (n_configs_tried >= 0)
);
CREATE INDEX IF NOT EXISTS idx_experiment_result_experiment ON experiment_result (registration_hash, created_at);
-- the test window may be evaluated ONCE per experiment
CREATE UNIQUE INDEX IF NOT EXISTS experiment_result_one_test ON experiment_result (registration_hash) WHERE result_kind = 'test';

COMMENT ON TABLE dataset_manifest         IS 'dataset_experiment_registry_migration_30';
COMMENT ON TABLE experiment_registration  IS 'dataset_experiment_registry_migration_30';
COMMENT ON TABLE experiment_result        IS 'dataset_experiment_registry_migration_30';

DO $mk$
BEGIN
    IF to_regprocedure('research_registry_guard()') IS NULL THEN
        EXECUTE $f$
CREATE FUNCTION research_registry_guard() RETURNS trigger LANGUAGE plpgsql AS $fn$
BEGIN
    RAISE EXCEPTION 'append-only: % on % is not permitted (manifests, registrations and results are immutable; register a new version)',
        TG_OP, TG_TABLE_NAME USING ERRCODE = 'integrity_constraint_violation';
END;
$fn$ $f$;
        COMMENT ON FUNCTION research_registry_guard() IS 'dataset_experiment_registry_migration_30';
        REVOKE ALL ON FUNCTION research_registry_guard() FROM PUBLIC;
    END IF;
    IF to_regprocedure('research_registry_stamp()') IS NULL THEN
        EXECUTE $f$
CREATE FUNCTION research_registry_stamp() RETURNS trigger LANGUAGE plpgsql AS $fn$
BEGIN
    NEW.created_at := clock_timestamp();
    RETURN NEW;
END;
$fn$ $f$;
        COMMENT ON FUNCTION research_registry_stamp() IS 'dataset_experiment_registry_migration_30';
        REVOKE ALL ON FUNCTION research_registry_stamp() FROM PUBLIC;
    END IF;
    IF to_regprocedure('research_registry_consistency()') IS NULL THEN
        EXECUTE $f$
CREATE FUNCTION research_registry_consistency() RETURNS trigger LANGUAGE plpgsql AS $fn$
DECLARE
    m RECORD;
    r RECORD;
    tried INTEGER;
BEGIN
    IF TG_TABLE_NAME = 'dataset_manifest' THEN
        -- the PIT cutoff cannot lie in the future: a dataset cannot have read information that did not exist yet
        IF NEW.knowledge_cutoff_at > clock_timestamp() THEN
            RAISE EXCEPTION 'dataset_manifest refused: knowledge_cutoff_at % is in the future', NEW.knowledge_cutoff_at
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    ELSIF TG_TABLE_NAME = 'experiment_registration' THEN
        SELECT label_version, feature_versions INTO m FROM dataset_manifest WHERE manifest_hash = NEW.manifest_hash;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'experiment_registration refused: manifest % does not exist', NEW.manifest_hash
                USING ERRCODE = 'foreign_key_violation';
        END IF;
        IF m.label_version <> NEW.label_version THEN
            RAISE EXCEPTION 'experiment_registration refused: label_version % differs from the manifest''s %', NEW.label_version, m.label_version
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        IF NOT (m.feature_versions @> NEW.feature_versions) THEN
            RAISE EXCEPTION 'experiment_registration refused: feature_versions are not a subset of the manifest''s'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    ELSIF TG_TABLE_NAME = 'experiment_result' THEN
        SELECT search_budget, code_sha INTO r FROM experiment_registration WHERE registration_hash = NEW.registration_hash;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'experiment_result refused: registration % does not exist', NEW.registration_hash
                USING ERRCODE = 'foreign_key_violation';
        END IF;
        IF NEW.n_configs_tried > r.search_budget THEN
            RAISE EXCEPTION 'experiment_result refused: % configurations tried exceeds the registered search_budget %',
                NEW.n_configs_tried, r.search_budget USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        IF NEW.result_kind = 'test' AND NOT EXISTS (
               SELECT 1 FROM experiment_result WHERE registration_hash = NEW.registration_hash AND result_kind = 'validation') THEN
            RAISE EXCEPTION 'experiment_result refused: a test result needs a prior validation result for the same registration'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        IF NEW.result_kind IN ('validation', 'test') AND EXISTS (
               SELECT 1 FROM experiment_result WHERE registration_hash = NEW.registration_hash AND result_kind IN ('failed', 'abandoned')) THEN
            RAISE EXCEPTION 'experiment_result refused: the experiment was already closed as failed/abandoned'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$fn$ $f$;
        COMMENT ON FUNCTION research_registry_consistency() IS 'dataset_experiment_registry_migration_30';
        REVOKE ALL ON FUNCTION research_registry_consistency() FROM PUBLIC;
    END IF;
END;
$mk$;

DO $trg$
DECLARE
    r RECORD;
    cur_mode "char";
    cur_fn   TEXT;
BEGIN
    FOR r IN SELECT * FROM (VALUES
        ('dataset_manifest',        'dataset_manifest_immutable_row',          'BEFORE UPDATE OR DELETE', 'ROW',       'research_registry_guard'),
        ('dataset_manifest',        'dataset_manifest_immutable_truncate',     'BEFORE TRUNCATE',         'STATEMENT', 'research_registry_guard'),
        ('dataset_manifest',        'dataset_manifest_stamp',                  'BEFORE INSERT',           'ROW',       'research_registry_stamp'),
        ('dataset_manifest',        'dataset_manifest_consistency',            'BEFORE INSERT',           'ROW',       'research_registry_consistency'),
        ('experiment_registration', 'experiment_registration_immutable_row',   'BEFORE UPDATE OR DELETE', 'ROW',       'research_registry_guard'),
        ('experiment_registration', 'experiment_registration_immutable_truncate', 'BEFORE TRUNCATE',      'STATEMENT', 'research_registry_guard'),
        ('experiment_registration', 'experiment_registration_stamp',           'BEFORE INSERT',           'ROW',       'research_registry_stamp'),
        ('experiment_registration', 'experiment_registration_consistency',     'BEFORE INSERT',           'ROW',       'research_registry_consistency'),
        ('experiment_result',       'experiment_result_immutable_row',         'BEFORE UPDATE OR DELETE', 'ROW',       'research_registry_guard'),
        ('experiment_result',       'experiment_result_immutable_truncate',    'BEFORE TRUNCATE',         'STATEMENT', 'research_registry_guard'),
        ('experiment_result',       'experiment_result_stamp',                 'BEFORE INSERT',           'ROW',       'research_registry_stamp'),
        ('experiment_result',       'experiment_result_consistency',           'BEFORE INSERT',           'ROW',       'research_registry_consistency')
    ) AS v(tbl, trg, timing, lvl, fn) LOOP
        SELECT t.tgenabled, p.proname INTO cur_mode, cur_fn
        FROM pg_trigger t JOIN pg_proc p ON p.oid = t.tgfoid
        WHERE t.tgrelid = to_regclass(r.tbl) AND t.tgname = r.trg AND NOT t.tgisinternal;
        IF FOUND THEN
            IF cur_mode <> 'A' OR cur_fn <> r.fn THEN
                RAISE EXCEPTION 'migration 30 refused: trigger % exists but is not (function %, ENABLE ALWAYS)', r.trg, r.fn;
            END IF;
        ELSE
            EXECUTE format('CREATE TRIGGER %I %s ON %I FOR EACH %s EXECUTE FUNCTION %I()', r.trg, r.timing, r.tbl, r.lvl, r.fn);
            EXECUTE format('ALTER TABLE %I ENABLE ALWAYS TRIGGER %I', r.tbl, r.trg);
        END IF;
    END LOOP;
END;
$trg$;
