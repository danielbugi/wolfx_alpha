-- mechanism/add_strategy_variant_registry.sql   (migration 35 -- versioned, append-only registry of strategy VARIANTS, e.g. donchian_breakout v1 x an exit policy)
--
-- WHY. The Quant Lab compares exit policies (and later other variations) on top of the live strategy. A variant needs a durable, versioned identity so a
-- result can always be traced to the exact definition that produced it, and so a changed definition can never silently reuse an old name. This table is that
-- identity. It deliberately does NOT add rows to `strategies` and never touches `signal_ledger`:
--   * `strategies` is enumerated by Strategy Intelligence (strategy_analytics.list), by the ledger writer and by the research readers. A variant row there would
--     show up as a production strategy with no signals. A variant lives here and only REFERENCES its parent strategy row (read-only foreign key).
--   * the live ledger keeps accounting exactly one strategy, exactly as before.
--
--   strategy_variant   one immutable row per (parent strategy, variant key, variant version).
--     parent_strategy_id      the production identity this variant is derived from (FK to strategies; the parent row is never modified).
--     variant_key/_version    name and version, version = v<integer>. v1 is the first definition; v(n+1) supersedes v(n) of the SAME key and parent.
--     variant_kind            what varies (exit_policy today).
--     definition              the full, canonical definition (entry rule, exit parameters, engine constants); echoes the parent key/version, which the
--                             database checks against the parent row.
--     definition_hash         sha256 of the canonical definition, computed by the writer and re-verified by research.strategy_variants.verify; unique per parent so
--                             one definition cannot hide under two names.
--     supersedes_variant_id   NULL for v1; otherwise the immediately preceding version (same parent and key, version exactly one lower, and each row can be
--                             superseded once, so the lineage is a straight line). A changed definition is ALWAYS a new version; nothing is ever updated.
--   created_at is DB-stamped. Immutability: ENABLE ALWAYS BEFORE UPDATE / DELETE row + TRUNCATE triggers that always raise (runtime role: SELECT + INSERT only,
--   see deploy/db/research_roles.sql).
--
-- Additive and forward-safe (IF NOT EXISTS; refuses to adopt same-named objects it did not create). Independent of migrations 24-34 except that it needs
-- `strategies` (migration at add_strategy_identity_release_a.sql). NOT applied to production by committing this file; production application is a separate, owner-approved step (backup + preflight_35.sql first; deploy/db/research_roles.sql is re-run afterwards). Rollback while empty: deploy/db/rollback_35.sql.

DO $guard$
BEGIN
    IF to_regclass('strategies') IS NULL THEN
        RAISE EXCEPTION 'migration 35 refused: strategies does not exist in the current schema (apply add_strategy_identity_release_a.sql first / wrong search_path)';
    END IF;
    IF to_regclass('strategy_variant') IS NOT NULL
       AND coalesce(obj_description(to_regclass('strategy_variant'), 'pg_class'), '') <> 'strategy_variant_migration_35' THEN
        RAISE EXCEPTION 'migration 35 refused: table strategy_variant already exists and is not a migration-35 object (no marker)';
    END IF;
END;
$guard$;

CREATE TABLE IF NOT EXISTS strategy_variant (
    id                    BIGSERIAL    PRIMARY KEY,
    parent_strategy_id    BIGINT       NOT NULL REFERENCES strategies (id),
    variant_key           VARCHAR(60)  NOT NULL,
    variant_version       VARCHAR(20)  NOT NULL,
    variant_kind          VARCHAR(30)  NOT NULL,
    definition            JSONB        NOT NULL,
    definition_hash       CHAR(64)     NOT NULL,
    supersedes_variant_id BIGINT       REFERENCES strategy_variant (id),
    description           TEXT,
    created_at            TIMESTAMPTZ  NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT strategy_variant_kind_chk    CHECK (variant_kind IN ('exit_policy')),
    CONSTRAINT strategy_variant_key_chk     CHECK (variant_key ~ '^[a-z0-9_]{3,60}$'),
    CONSTRAINT strategy_variant_version_chk CHECK (variant_version ~ '^v[0-9]{1,6}$'),
    CONSTRAINT strategy_variant_hash_chk    CHECK (definition_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT strategy_variant_def_chk     CHECK (jsonb_typeof(definition) = 'object'),
    CONSTRAINT strategy_variant_v1_chk      CHECK ((supersedes_variant_id IS NULL) = (variant_version = 'v1')),
    CONSTRAINT uq_strategy_variant_identity UNIQUE (parent_strategy_id, variant_key, variant_version),
    CONSTRAINT uq_strategy_variant_hash     UNIQUE (parent_strategy_id, definition_hash)
);
-- each version can be superseded at most once: the lineage of a key is a straight line
CREATE UNIQUE INDEX IF NOT EXISTS uq_strategy_variant_supersedes ON strategy_variant (supersedes_variant_id) WHERE supersedes_variant_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_strategy_variant_parent ON strategy_variant (parent_strategy_id, variant_key);
COMMENT ON TABLE strategy_variant IS 'strategy_variant_migration_35';

DO $mk$
BEGIN
    IF to_regprocedure('research_strategy_variant_guard()') IS NULL THEN
        EXECUTE $f$
CREATE FUNCTION research_strategy_variant_guard() RETURNS trigger LANGUAGE plpgsql AS $fn$
BEGIN
    RAISE EXCEPTION 'append-only: % on % is not permitted (a variant definition is immutable; register a new version)',
        TG_OP, TG_TABLE_NAME USING ERRCODE = 'integrity_constraint_violation';
END;
$fn$ $f$;
        COMMENT ON FUNCTION research_strategy_variant_guard() IS 'strategy_variant_migration_35';
        REVOKE ALL ON FUNCTION research_strategy_variant_guard() FROM PUBLIC;
    END IF;

    IF to_regprocedure('research_strategy_variant_stamp()') IS NULL THEN
        EXECUTE $f$
CREATE FUNCTION research_strategy_variant_stamp() RETURNS trigger LANGUAGE plpgsql AS $fn$
DECLARE
    parent_row RECORD;
    prev       RECORD;
BEGIN
    -- serialise registrations for one parent (held until the writer's transaction ends)
    PERFORM pg_advisory_xact_lock(hashtextextended('strategy_variant|' || NEW.parent_strategy_id::text || '|' || NEW.variant_key, 0));
    NEW.created_at := clock_timestamp();
    SELECT strategy_key, strategy_version INTO parent_row FROM strategies WHERE id = NEW.parent_strategy_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'strategy_variant refused: parent strategy % does not exist', NEW.parent_strategy_id USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.definition ->> 'parent_strategy_key' IS DISTINCT FROM parent_row.strategy_key
       OR NEW.definition ->> 'parent_strategy_version' IS DISTINCT FROM parent_row.strategy_version THEN
        RAISE EXCEPTION 'strategy_variant refused: the definition names parent %/% but parent_strategy_id points to %/%',
            NEW.definition ->> 'parent_strategy_key', NEW.definition ->> 'parent_strategy_version', parent_row.strategy_key, parent_row.strategy_version
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.definition ->> 'kind' IS DISTINCT FROM NEW.variant_kind THEN
        RAISE EXCEPTION 'strategy_variant refused: definition kind % differs from variant_kind %', NEW.definition ->> 'kind', NEW.variant_kind
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.supersedes_variant_id IS NOT NULL THEN
        SELECT parent_strategy_id, variant_key, variant_version INTO prev FROM strategy_variant WHERE id = NEW.supersedes_variant_id;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'strategy_variant refused: superseded variant % does not exist', NEW.supersedes_variant_id USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        IF prev.parent_strategy_id <> NEW.parent_strategy_id OR prev.variant_key <> NEW.variant_key THEN
            RAISE EXCEPTION 'strategy_variant refused: a version may only supersede the same key under the same parent' USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        IF substring(NEW.variant_version FROM 2)::integer <> substring(prev.variant_version FROM 2)::integer + 1 THEN
            RAISE EXCEPTION 'strategy_variant refused: version % must be exactly one above superseded %', NEW.variant_version, prev.variant_version
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$fn$ $f$;
        COMMENT ON FUNCTION research_strategy_variant_stamp() IS 'strategy_variant_migration_35';
        REVOKE ALL ON FUNCTION research_strategy_variant_stamp() FROM PUBLIC;
    END IF;
END;
$mk$;

DO $trg$
DECLARE
    r        RECORD;
    cur_mode "char";
    cur_fn   NAME;
BEGIN
    FOR r IN SELECT * FROM (VALUES
        ('strategy_variant', 'strategy_variant_immutable_row',      'BEFORE UPDATE OR DELETE', 'ROW',       'research_strategy_variant_guard'),
        ('strategy_variant', 'strategy_variant_immutable_truncate', 'BEFORE TRUNCATE',         'STATEMENT', 'research_strategy_variant_guard'),
        ('strategy_variant', 'strategy_variant_stamp',              'BEFORE INSERT',           'ROW',       'research_strategy_variant_stamp')
    ) AS v(tbl, trg, timing, lvl, fn) LOOP
        SELECT t.tgenabled, p.proname INTO cur_mode, cur_fn
        FROM pg_trigger t JOIN pg_proc p ON p.oid = t.tgfoid
        WHERE t.tgrelid = to_regclass(r.tbl) AND t.tgname = r.trg AND NOT t.tgisinternal;
        IF FOUND THEN
            IF cur_mode <> 'A' OR cur_fn <> r.fn THEN
                RAISE EXCEPTION 'migration 35 refused: trigger % exists but is not (function %, ENABLE ALWAYS)', r.trg, r.fn;
            END IF;
        ELSE
            EXECUTE format('CREATE TRIGGER %I %s ON %I FOR EACH %s EXECUTE FUNCTION %I()', r.trg, r.timing, r.tbl, r.lvl, r.fn);
            EXECUTE format('ALTER TABLE %I ENABLE ALWAYS TRIGGER %I', r.tbl, r.trg);
        END IF;
    END LOOP;
END;
$trg$;
