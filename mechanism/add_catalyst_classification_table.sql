-- mechanism/add_catalyst_classification_table.sql   (migration 28 -- catalyst classification, an INTERPRETATION layer)
--
-- A classification says what a classifier (a deterministic rule, a model or a human) thinks a stored fact MEANS. The fact itself --
-- a migration-25 `market_event_revision` row, e.g. an SEC filing mapped by filing_contract.to_event_draft -- is never touched:
--   * a classification REFERENCES the immutable revision (revision_id) and PINS what was read (fact_hash == the revision's payload
--     fact_hash, checked by trigger), so it can never be silently re-pointed at different content;
--   * it is append-only and VERSIONED: a new classifier version, a model re-run or a human override is a NEW row that may name the row
--     it replaces (supersedes_id); the replaced row stays, so "what did we think on day D" is always answerable;
--   * the classifier's IDENTITY is mandatory and typed by method: a model row must carry model_id + prompt_hash, a human row a
--     reviewer id, a rule row its rule id -- so a result is always reproducible or attributable;
--   * a rule has no confidence (it is deterministic); confidence exists only for model / human rows.
-- AVAILABILITY of a classification is classified_at, stamped by the database. A classification produced today about a 2024 filing is
-- not "known in 2024"; a deterministic rule's output can instead be re-derived from the fact itself by a feature builder.
--
-- REQUIRES migration 25 (market_event_revision). Independent of 24-for-writes beyond what 25 itself requires.
-- APPLY ATOMICALLY:   psql -v ON_ERROR_STOP=1 -1 -f mechanism/add_catalyst_classification_table.sql
-- NOT applied to production by committing this file; nothing writes to it until a separate owner-authorised activation.
-- Re-run deploy/db/research_roles.sql (+ verifier) afterwards: append-only for the runtime role, excluded from the full-DML baseline.
--
-- Collision safety. Marker 'catalyst_classification_migration_28'; section 0 refuses on a missing prerequisite or a foreign object.

-- ---------------------------------------------------------------------------------------------------
-- 0. Collision guard (read-only; raises, never repairs).
-- ---------------------------------------------------------------------------------------------------
DO $guard$
DECLARE
    marker CONSTANT TEXT := 'catalyst_classification_migration_28';
    f TEXT;
    ix RECORD;
BEGIN
    IF to_regclass('market_event_revision') IS NULL
       OR coalesce(obj_description(to_regclass('market_event_revision'), 'pg_class'), '') <> 'market_intelligence_migration_25' THEN
        RAISE EXCEPTION 'migration 28 refused: migration 25 (market_event_revision) is required and is not present';
    END IF;
    IF to_regclass('catalyst_classification') IS NOT NULL
       AND coalesce(obj_description(to_regclass('catalyst_classification'), 'pg_class'), '') <> marker THEN
        RAISE EXCEPTION 'migration 28 refused: relation catalyst_classification already exists and is not a migration-28 object (no marker)';
    END IF;
    FOREACH f IN ARRAY ARRAY['research_classification_guard()', 'research_classification_consistency()'] LOOP
        IF to_regprocedure(f) IS NOT NULL AND coalesce(obj_description(to_regprocedure(f), 'pg_proc'), '') <> marker THEN
            RAISE EXCEPTION 'migration 28 refused: function % already exists and is not a migration-28 object (no marker)', f;
        END IF;
    END LOOP;
    FOR ix IN SELECT * FROM (VALUES
            ('idx_catalyst_classification_revision', 'catalyst_classification'),
            ('idx_catalyst_classification_asof',     'catalyst_classification')) AS v(idx, tbl) LOOP
        IF to_regclass(ix.idx) IS NOT NULL AND NOT EXISTS (
               SELECT 1 FROM pg_index i WHERE i.indexrelid = to_regclass(ix.idx) AND i.indrelid = to_regclass(ix.tbl)) THEN
            RAISE EXCEPTION 'migration 28 refused: index % already exists on a different relation', ix.idx;
        END IF;
    END LOOP;
END;
$guard$;

-- ---------------------------------------------------------------------------------------------------
-- 1. The table.
-- ---------------------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS catalyst_classification (
    id                  BIGSERIAL     PRIMARY KEY,
    revision_id         BIGINT        NOT NULL REFERENCES market_event_revision (id),
    event_key           VARCHAR(200)  NOT NULL,        -- identity copy, checked against the revision by trigger
    fact_hash           CHAR(64)      NOT NULL,        -- the fact content the classifier read; must equal the revision's payload fact_hash
    classifier          VARCHAR(60)   NOT NULL,        -- e.g. items_rule
    classifier_version  VARCHAR(30)   NOT NULL,        -- a new version is a NEW row
    method              VARCHAR(5)    NOT NULL,        -- rule | model | human
    classifier_identity JSONB         NOT NULL,        -- rule: {rule_id}; model: {model_id, prompt_hash, ...}; human: {reviewer}
    label               VARCHAR(60)   NOT NULL,        -- the classifier version's own vocabulary
    evidence            JSONB         NOT NULL,        -- citations of the fact's own fields only (the contract limits the keys)
    confidence          NUMERIC(5,4),                  -- [0,1]; NULL for a rule (deterministic -- there is nothing to be confident about)
    supersedes_id       BIGINT        REFERENCES catalyst_classification (id),
    classified_at       TIMESTAMPTZ   NOT NULL,        -- DB-stamped: THE availability time of this classification
    code_ref            VARCHAR(160)  NOT NULL,
    CONSTRAINT catalyst_classification_key UNIQUE (revision_id, classifier, classifier_version),
    CONSTRAINT catalyst_classification_one_successor UNIQUE (supersedes_id),
    CONSTRAINT catalyst_classification_method_chk CHECK (method IN ('rule', 'model', 'human')),
    CONSTRAINT catalyst_classification_hash_chk CHECK (fact_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT catalyst_classification_text_chk CHECK (length(classifier) > 0 AND length(classifier_version) > 0 AND length(label) > 0),
    CONSTRAINT catalyst_classification_json_chk CHECK (jsonb_typeof(classifier_identity) = 'object' AND jsonb_typeof(evidence) = 'object'),
    CONSTRAINT catalyst_classification_identity_chk CHECK (
        (method = 'rule'  AND classifier_identity ? 'rule_id')
     OR (method = 'model' AND classifier_identity ?& ARRAY['model_id', 'prompt_hash'])
     OR (method = 'human' AND classifier_identity ? 'reviewer')),
    CONSTRAINT catalyst_classification_confidence_chk CHECK (
        (method = 'rule' AND confidence IS NULL)
     OR (method <> 'rule' AND (confidence IS NULL OR confidence BETWEEN 0 AND 1))),
    CONSTRAINT catalyst_classification_not_self_chk CHECK (supersedes_id IS NULL OR supersedes_id <> id)
);
CREATE INDEX IF NOT EXISTS idx_catalyst_classification_revision ON catalyst_classification (revision_id);
CREATE INDEX IF NOT EXISTS idx_catalyst_classification_asof     ON catalyst_classification (classified_at);

COMMENT ON TABLE catalyst_classification IS 'catalyst_classification_migration_28';

-- ---------------------------------------------------------------------------------------------------
-- 2. Functions (created only when absent).
-- ---------------------------------------------------------------------------------------------------
DO $mk$
BEGIN
    IF to_regprocedure('research_classification_guard()') IS NULL THEN
        EXECUTE $f$
CREATE FUNCTION research_classification_guard() RETURNS trigger LANGUAGE plpgsql AS $fn$
BEGIN
    RAISE EXCEPTION 'append-only: % on % is not permitted (classifications are immutable; supersede with a new row)',
        TG_OP, TG_TABLE_NAME USING ERRCODE = 'integrity_constraint_violation';
END;
$fn$ $f$;
        COMMENT ON FUNCTION research_classification_guard() IS 'catalyst_classification_migration_28';
        REVOKE ALL ON FUNCTION research_classification_guard() FROM PUBLIC;
    END IF;
    IF to_regprocedure('research_classification_consistency()') IS NULL THEN
        EXECUTE $f$
CREATE FUNCTION research_classification_consistency() RETURNS trigger LANGUAGE plpgsql AS $fn$
DECLARE
    rev RECORD;
    prior RECORD;
BEGIN
    NEW.classified_at := clock_timestamp();
    SELECT event_key, payload->>'fact_hash' AS fact_hash INTO rev FROM market_event_revision WHERE id = NEW.revision_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'catalyst_classification refused: revision % does not exist', NEW.revision_id
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF rev.fact_hash IS NULL THEN
        RAISE EXCEPTION 'catalyst_classification refused: revision % carries no fact_hash, so nothing can be pinned', NEW.revision_id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF rev.event_key <> NEW.event_key OR rev.fact_hash <> NEW.fact_hash THEN
        RAISE EXCEPTION 'catalyst_classification refused: event_key / fact_hash do not match revision %', NEW.revision_id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.supersedes_id IS NOT NULL THEN
        SELECT revision_id INTO prior FROM catalyst_classification WHERE id = NEW.supersedes_id;
        IF NOT FOUND OR prior.revision_id <> NEW.revision_id THEN
            RAISE EXCEPTION 'catalyst_classification refused: supersedes_id % is not a classification of revision %',
                NEW.supersedes_id, NEW.revision_id USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$fn$ $f$;
        COMMENT ON FUNCTION research_classification_consistency() IS 'catalyst_classification_migration_28';
        REVOKE ALL ON FUNCTION research_classification_consistency() FROM PUBLIC;
    END IF;
END;
$mk$;

-- ---------------------------------------------------------------------------------------------------
-- 3. Triggers (an existing one must already be the right function and ENABLE ALWAYS, otherwise refuse).
-- ---------------------------------------------------------------------------------------------------
DO $trg$
DECLARE
    r RECORD;
    cur_mode "char";
    cur_fn   TEXT;
BEGIN
    FOR r IN SELECT * FROM (VALUES
        ('catalyst_classification_immutable_row',      'BEFORE UPDATE OR DELETE', 'ROW',       'research_classification_guard'),
        ('catalyst_classification_immutable_truncate', 'BEFORE TRUNCATE',         'STATEMENT', 'research_classification_guard'),
        ('catalyst_classification_consistency',        'BEFORE INSERT',           'ROW',       'research_classification_consistency')
    ) AS v(trg, timing, lvl, fn) LOOP
        SELECT t.tgenabled, p.proname INTO cur_mode, cur_fn
        FROM pg_trigger t JOIN pg_proc p ON p.oid = t.tgfoid
        WHERE t.tgrelid = to_regclass('catalyst_classification') AND t.tgname = r.trg AND NOT t.tgisinternal;
        IF FOUND THEN
            IF cur_mode <> 'A' OR cur_fn <> r.fn THEN
                RAISE EXCEPTION 'migration 28 refused: trigger % exists but is not (function %, ENABLE ALWAYS)', r.trg, r.fn;
            END IF;
        ELSE
            EXECUTE format('CREATE TRIGGER %I %s ON catalyst_classification FOR EACH %s EXECUTE FUNCTION %I()',
                           r.trg, r.timing, r.lvl, r.fn);
            EXECUTE format('ALTER TABLE catalyst_classification ENABLE ALWAYS TRIGGER %I', r.trg);
        END IF;
    END LOOP;
END;
$trg$;
