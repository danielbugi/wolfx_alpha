-- deploy/db/fundamental_update_publication_verify.sql -- READ-ONLY verification of migration 41 (mechanism/add_fundamental_update_publication.sql).
-- Every check RAISEs on failure; success prints 'migration 41 verified'. Safe in a forced read-only session:
--     docker exec -i donchian-screener-postgres-1 sh -c 'PGOPTIONS="-c default_transaction_read_only=on" psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d trading_production' < deploy/db/fundamental_update_publication_verify.sql
--
-- Checks:
--   (1) every column with its exact type, nullability, default and order;
--   (2) every constraint and its exact definition (the nested allowlist CHECK included);
--   (3) every index and its exact definition, and that each is valid;
--   (4) both append-only triggers, their exact definition, and that they fire ALWAYS;
--   (5) the sequence is owned by the id column;
--   (6) the view's exact columns;
--   (7) privileges: PUBLIC has none. donchian_app has exactly SELECT + INSERT on the table, SELECT on the view, and USAGE (not UPDATE) on the
--       sequence; it does not own the table;
--   (8) the view's BEHAVIOUR on the rows actually present: one row per update key, always its highest version, first_published_at and
--       first_public_date taken from version 1, every key starting at version 1 with no gaps;
--   (9) the nested allowlist function: IMMUTABLE, not SECURITY DEFINER, accepts a valid shape and refuses each kind of unapproved nested field,
--       EXECUTE for donchian_app and not PUBLIC; and EVERY stored row passes it (a row stored before a weaker version of it would be found).

DO $$
DECLARE
    t   regclass;
    v   regclass;
    got text;
    n   bigint;
    good jsonb;
    who CONSTANT text := 'verify';
BEGIN
    t := to_regclass('public.fundamental_update_publication');
    v := to_regclass('public.fundamental_update_current');
    IF t IS NULL THEN RAISE EXCEPTION 'fundamental_update_publication is missing'; END IF;
    IF v IS NULL THEN RAISE EXCEPTION 'fundamental_update_current is missing'; END IF;
    IF (SELECT relkind FROM pg_class WHERE oid = t) <> 'r' THEN RAISE EXCEPTION 'fundamental_update_publication is not a plain table'; END IF;
    IF (SELECT relkind FROM pg_class WHERE oid = v) <> 'v' THEN RAISE EXCEPTION 'fundamental_update_current is not a view'; END IF;

    -- (1) columns
    SELECT string_agg(a.attname || ' ' || format_type(a.atttypid, a.atttypmod) || CASE WHEN a.attnotnull THEN ' not null' ELSE '' END
                      || coalesce(' default ' || pg_get_expr(d.adbin, d.adrelid), ''), ', ' ORDER BY a.attnum)
      INTO got FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
     WHERE a.attrelid = t AND a.attnum > 0 AND NOT a.attisdropped;
    IF got IS DISTINCT FROM 'id bigint not null default nextval(''fundamental_update_publication_id_seq''::regclass), update_key character varying(80) not null, '
        'version integer not null, kind character varying(10) not null, changed jsonb not null default ''[]''::jsonb, public_json jsonb not null, '
        'content_hash character(64) not null, render_version character varying(10) not null, symbol character varying(20) not null, quarter_end date not null, '
        'event_type character varying(40) not null, sector character varying(50), public_date date not null, event_id bigint, '
        'method_version character varying(30) not null, published_at timestamp with time zone not null default now()' THEN
        RAISE EXCEPTION 'unexpected columns: %', got;
    END IF;

    -- (2) constraints
    SELECT string_agg(conname || ': ' || pg_get_constraintdef(oid), E'\n' ORDER BY conname) INTO got FROM pg_constraint WHERE conrelid = t;
    IF got IS DISTINCT FROM concat_ws(E'\n',
        'fundamental_update_publication_changed_chk: CHECK ((jsonb_typeof(changed) = ''array''::text))',
        'fundamental_update_publication_first_chk: CHECK (((version = 1) = ((kind)::text = ''initial''::text)))',
        'fundamental_update_publication_hash_chk: CHECK ((content_hash ~ ''^[0-9a-f]{64}$''::text))',
        'fundamental_update_publication_key_chk: CHECK (((update_key)::text ~ ''^[A-Z][A-Z0-9.\-]{0,9}/\d{4}-\d{2}-\d{2}/[a-z\-]+$''::text))',
        'fundamental_update_publication_kind_chk: CHECK (((kind)::text = ANY ((ARRAY[''initial''::character varying, ''restated''::character varying, ''redated''::character varying, ''withdrawn''::character varying])::text[])))',
        'fundamental_update_publication_pkey: PRIMARY KEY (id)',
        'fundamental_update_publication_public_keys_chk: CHECK (((jsonb_typeof(public_json) = ''object''::text) AND (public_json ?& ARRAY[''key''::text, ''symbol''::text, ''company_name''::text, ''event_type''::text, ''label''::text, ''quarter_end''::text, ''fiscal_label''::text, ''public_date''::text, ''date_source''::text, ''sector''::text, ''industry''::text, ''market_cap''::text, ''figures''::text, ''why''::text, ''links''::text, ''news''::text, ''content_hash''::text, ''render_version''::text]) AND ((public_json - ARRAY[''key''::text, ''symbol''::text, ''company_name''::text, ''event_type''::text, ''label''::text, ''quarter_end''::text, ''fiscal_label''::text, ''public_date''::text, ''date_source''::text, ''sector''::text, ''industry''::text, ''market_cap''::text, ''figures''::text, ''why''::text, ''links''::text, ''news''::text, ''content_hash''::text, ''render_version''::text]) = ''{}''::jsonb) AND (jsonb_typeof((public_json -> ''figures''::text)) = ''array''::text) AND (jsonb_typeof((public_json -> ''news''::text)) = ''array''::text) AND (jsonb_typeof((public_json -> ''links''::text)) = ''object''::text)))',
        'fundamental_update_publication_public_match_chk: CHECK ((((public_json ->> ''key''::text) = (update_key)::text) AND ((public_json ->> ''symbol''::text) = (symbol)::text) AND ((public_json ->> ''content_hash''::text) = (content_hash)::text) AND ((public_json ->> ''render_version''::text) = (render_version)::text) AND ((public_json ->> ''event_type''::text) = (event_type)::text)))',
        'fundamental_update_publication_public_nested_chk: CHECK (fundamental_update_public_json_ok(public_json))',
        'fundamental_update_publication_version: UNIQUE (update_key, version)',
        'fundamental_update_publication_version_chk: CHECK ((version >= 1))') THEN
        RAISE EXCEPTION 'unexpected constraints:%', E'\n' || coalesce(got, '(none)');
    END IF;

    -- (3) indexes
    SELECT string_agg(pg_get_indexdef(indexrelid), E'\n' ORDER BY pg_get_indexdef(indexrelid)) INTO got FROM pg_index WHERE indrelid = t;
    IF got IS DISTINCT FROM concat_ws(E'\n',
        'CREATE INDEX idx_fup_key_version ON public.fundamental_update_publication USING btree (update_key, version DESC)',
        'CREATE INDEX idx_fup_published ON public.fundamental_update_publication USING btree (published_at DESC) WHERE (version = 1)',
        'CREATE UNIQUE INDEX fundamental_update_publication_pkey ON public.fundamental_update_publication USING btree (id)',
        'CREATE UNIQUE INDEX fundamental_update_publication_version ON public.fundamental_update_publication USING btree (update_key, version)') THEN
        RAISE EXCEPTION 'unexpected indexes:%', E'\n' || coalesce(got, '(none)');
    END IF;
    IF EXISTS (SELECT 1 FROM pg_index WHERE indrelid = t AND NOT indisvalid) THEN RAISE EXCEPTION 'an index is not valid'; END IF;

    -- (4) triggers ('A' = fires ALWAYS, so a session_replication_role = replica session cannot bypass them)
    SELECT string_agg(tgname || ' ' || tgenabled::text || ' ' || pg_get_triggerdef(oid), E'\n' ORDER BY tgname) INTO got FROM pg_trigger WHERE tgrelid = t AND NOT tgisinternal;
    IF got IS DISTINCT FROM concat_ws(E'\n',
        'fundamental_update_publication_no_truncate A CREATE TRIGGER fundamental_update_publication_no_truncate BEFORE TRUNCATE ON public.fundamental_update_publication FOR EACH STATEMENT EXECUTE FUNCTION fundamental_update_publication_append_only()',
        'fundamental_update_publication_no_update A CREATE TRIGGER fundamental_update_publication_no_update BEFORE DELETE OR UPDATE ON public.fundamental_update_publication FOR EACH ROW EXECUTE FUNCTION fundamental_update_publication_append_only()') THEN
        RAISE EXCEPTION 'unexpected triggers:%', E'\n' || coalesce(got, '(none)');
    END IF;

    -- (5) the sequence belongs to the id column (dropping the table drops it; nothing else uses it)
    IF pg_get_serial_sequence('public.fundamental_update_publication', 'id') IS DISTINCT FROM 'public.fundamental_update_publication_id_seq' THEN
        RAISE EXCEPTION 'id sequence is not owned by fundamental_update_publication.id';
    END IF;

    -- (6) view columns
    SELECT string_agg(attname || ' ' || format_type(atttypid, atttypmod), ', ' ORDER BY attnum) INTO got FROM pg_attribute WHERE attrelid = v AND attnum > 0 AND NOT attisdropped;
    IF got IS DISTINCT FROM 'update_key character varying(80), version integer, kind character varying(10), public_json jsonb, content_hash character(64), '
                            'symbol character varying(20), quarter_end date, event_type character varying(40), sector character varying(50), public_date date, '
                            'updated_at timestamp with time zone, first_published_at timestamp with time zone, first_public_date date' THEN
        RAISE EXCEPTION 'unexpected view columns: %', got;
    END IF;

    -- (7) privileges
    IF has_table_privilege('public', t, 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') OR has_table_privilege('public', v, 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE')
       OR has_sequence_privilege('public', 'public.fundamental_update_publication_id_seq', 'USAGE,SELECT,UPDATE') THEN
        RAISE EXCEPTION 'PUBLIC has a privilege on the publication table, view or sequence';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'donchian_app') THEN
        IF NOT has_table_privilege('donchian_app', t, 'SELECT') OR NOT has_table_privilege('donchian_app', t, 'INSERT') THEN
            RAISE EXCEPTION 'donchian_app lacks SELECT or INSERT on the table';
        END IF;
        IF has_table_privilege('donchian_app', t, 'UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') THEN
            RAISE EXCEPTION 'donchian_app must not have UPDATE/DELETE/TRUNCATE/REFERENCES/TRIGGER on the table';
        END IF;
        IF NOT has_table_privilege('donchian_app', v, 'SELECT') OR has_table_privilege('donchian_app', v, 'INSERT,UPDATE,DELETE,TRUNCATE') THEN
            RAISE EXCEPTION 'donchian_app must have exactly SELECT on the view';
        END IF;
        IF NOT has_sequence_privilege('donchian_app', 'public.fundamental_update_publication_id_seq', 'USAGE')
           OR has_sequence_privilege('donchian_app', 'public.fundamental_update_publication_id_seq', 'UPDATE') THEN
            RAISE EXCEPTION 'donchian_app must have USAGE and not UPDATE (setval) on the id sequence';
        END IF;
        IF (SELECT relowner FROM pg_class WHERE oid = t) = (SELECT oid FROM pg_roles WHERE rolname = 'donchian_app') THEN
            RAISE EXCEPTION 'donchian_app owns the table: an owner could drop or alter it';
        END IF;
    ELSE
        RAISE NOTICE 'role donchian_app does not exist here: its privilege checks were skipped';
    END IF;

    -- (8) view behaviour on the rows present
    SELECT count(*) INTO n FROM (SELECT update_key FROM fundamental_update_publication GROUP BY update_key HAVING min(version) <> 1 OR max(version) <> count(*)) x;
    IF n > 0 THEN RAISE EXCEPTION '% update keys do not start at version 1 or have a gap in their versions', n; END IF;
    IF (SELECT count(*) FROM fundamental_update_current) <> (SELECT count(DISTINCT update_key) FROM fundamental_update_publication) THEN
        RAISE EXCEPTION 'the view does not have exactly one row per update key';
    END IF;
    SELECT count(*) INTO n
      FROM fundamental_update_current c
      JOIN (SELECT update_key, max(version) AS v FROM fundamental_update_publication GROUP BY update_key) m ON m.update_key = c.update_key
      JOIN fundamental_update_publication f ON f.update_key = c.update_key AND f.version = 1
      JOIN fundamental_update_publication l ON l.update_key = c.update_key AND l.version = m.v
     WHERE c.version <> m.v OR c.kind <> l.kind OR c.public_json <> l.public_json OR c.updated_at <> l.published_at
        OR c.first_published_at <> f.published_at OR c.first_public_date <> f.public_date;
    IF n > 0 THEN RAISE EXCEPTION '% view rows are not the latest version with their first-version dates', n; END IF;

    -- (9) the nested allowlist
    -- the nested allowlist function: IMMUTABLE, not SECURITY DEFINER, and it behaves: one valid shape accepted, each kind of unapproved field refused
    IF (SELECT provolatile::text || prosecdef::text FROM pg_proc WHERE oid = 'public.fundamental_update_public_json_ok(jsonb)'::regprocedure) IS DISTINCT FROM 'ifalse' THEN
        RAISE EXCEPTION '%: fundamental_update_public_json_ok must be IMMUTABLE and not SECURITY DEFINER', who;
    END IF;
    good := '{"key": "A/2026-09-30/margin-expansion", "symbol": "A", "company_name": null, "event_type": "margin_expansion", "label": "l",
              "quarter_end": "2026-09-30", "fiscal_label": "f", "public_date": "2026-11-05", "date_source": "d", "sector": null, "industry": null,
              "market_cap": {"value": "$1B", "as_of": "2026-11-05"}, "figures": [{"label": "l", "value": "v", "compare": null}], "why": "w",
              "links": {"finviz": "u", "detail": "u", "sec": null, "sec_kind": null}, "news": [{"published_at": "t", "source": "s", "headline": "h", "url": "u"}],
              "content_hash": "h", "render_version": "fu1"}';
    IF fundamental_update_public_json_ok(good) IS NOT TRUE
       OR fundamental_update_public_json_ok(jsonb_set(jsonb_set(good, '{news}', '[]'), '{market_cap}', 'null')) IS NOT TRUE
       OR fundamental_update_public_json_ok(jsonb_set(good, '{figures,0,score}', '91.5')) IS NOT FALSE
       OR fundamental_update_public_json_ok(jsonb_set(good, '{news,0,internal}', '"x"')) IS NOT FALSE
       OR fundamental_update_public_json_ok(jsonb_set(good, '{links,admin}', '"x"')) IS NOT FALSE
       OR fundamental_update_public_json_ok(jsonb_set(good, '{market_cap,raw}', '1')) IS NOT FALSE
       OR fundamental_update_public_json_ok(jsonb_set(good, '{why}', '{"score": 1}')) IS NOT FALSE
       OR fundamental_update_public_json_ok(jsonb_set(good, '{figures,0,value}', '1.5')) IS NOT FALSE
       OR fundamental_update_public_json_ok(jsonb_set(good, '{links,sec_kind}', '"other"')) IS NOT FALSE
       OR fundamental_update_public_json_ok(jsonb_set(good, '{figures}', '[1]')) IS NOT FALSE
       OR fundamental_update_public_json_ok(good - 'why') IS NOT FALSE THEN
        RAISE EXCEPTION '%: fundamental_update_public_json_ok does not enforce the nested allowlist', who;
    END IF;
    IF has_function_privilege('public', 'public.fundamental_update_public_json_ok(jsonb)', 'EXECUTE') THEN
        RAISE EXCEPTION 'PUBLIC can execute fundamental_update_public_json_ok';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'donchian_app')
       AND NOT has_function_privilege('donchian_app', 'public.fundamental_update_public_json_ok(jsonb)', 'EXECUTE') THEN
        RAISE EXCEPTION 'donchian_app cannot execute fundamental_update_public_json_ok: its inserts would fail the nested CHECK';
    END IF;
    SELECT count(*) INTO n FROM fundamental_update_publication WHERE fundamental_update_public_json_ok(public_json) IS NOT TRUE;
    IF n > 0 THEN RAISE EXCEPTION '% stored rows hold an unapproved nested field', n; END IF;
END $$;

SELECT count(*) AS publication_rows, count(DISTINCT update_key) AS updates FROM fundamental_update_publication;
SELECT kind, count(*) AS rows FROM fundamental_update_publication GROUP BY kind ORDER BY kind;
SELECT 'migration 41 verified' AS result;
