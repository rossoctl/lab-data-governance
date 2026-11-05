-- Adopt the already-applied 0016..0019 branch on historical risk 0020/0022.
-- Run only as described in docs/migrations-main-risk.md, then use the normal
-- migration CLI. This changes version bookkeeping only; no evidence is rewritten.
BEGIN;
LOCK TABLE public.alembic_version IN EXCLUSIVE MODE;

DO $adopt$
DECLARE
    versions TEXT[];
    mismatch TEXT;
BEGIN
    SELECT array_agg(version_num::text ORDER BY version_num) INTO versions
    FROM public.alembic_version;
    IF versions IS NULL OR versions NOT IN (
        ARRAY['0020_entity_namespace'],
        ARRAY['0022_content_kind_on_legs'],
        ARRAY['0019_interaction_risk_seq', '0020_entity_namespace'],
        ARRAY['0019_interaction_risk_seq', '0022_content_kind_on_legs']
    ) THEN
        RAISE EXCEPTION 'Historical risk adoption supports only 0020/0022; found %', versions;
    END IF;

    -- The shipped 0016..0019 schema, including the decision cache, aggregation
    -- modes, and interaction risk cursor. A partial risk schema must not be
    -- marked as applied merely because one of its tables exists.
    SELECT string_agg(r.table_name || '.' || r.column_name, ', ' ORDER BY r.table_name, r.column_name)
    INTO mismatch
    FROM (VALUES
        ('interaction_risk_records', 'interaction_risk_id', 'uuid', 'NO'),
        ('interaction_risk_records', 'interaction_id', 'text', 'NO'),
        ('interaction_risk_records', 'trace_id', 'text', 'NO'),
        ('interaction_risk_records', 'parent_interaction_id', 'text', 'YES'),
        ('interaction_risk_records', 'caller_entity_id', 'text', 'NO'),
        ('interaction_risk_records', 'callee_entity_id', 'text', 'NO'),
        ('interaction_risk_records', 'version', 'int4', 'NO'),
        ('interaction_risk_records', 'computed_at', 'timestamptz', 'NO'),
        ('interaction_risk_records', 'risk_level', 'text', 'NO'),
        ('interaction_risk_records', 'enforcement_type', 'text', 'YES'),
        ('interaction_risk_records', 'policy_event_count', 'int4', 'NO'),
        ('interaction_risk_records', 'triggered_rule_ids', '_text', 'NO'),
        ('interaction_risk_records', 'legs_evidenced', '_text', 'NO'),
        ('interaction_risk_records', 'classification_summary', 'jsonb', 'YES'),
        ('interaction_risk_records', 'opa_policy_versions_used', '_text', 'NO'),
        ('interaction_risk_records', 'overall_confidence', 'numeric', 'YES'),
        ('interaction_risk_records', 'seq', 'int8', 'NO'),
        ('trace_risk_records', 'trace_risk_id', 'uuid', 'NO'),
        ('trace_risk_records', 'trace_id', 'text', 'NO'),
        ('trace_risk_records', 'version', 'int4', 'NO'),
        ('trace_risk_records', 'computed_at', 'timestamptz', 'NO'),
        ('trace_risk_records', 'trace_risk_level', 'text', 'NO'),
        ('trace_risk_records', 'trace_enforcement_type', 'text', 'YES'),
        ('trace_risk_records', 'risk_compounding_mode', 'text', 'YES'),
        ('trace_risk_records', 'enforcement_aggregation_mode', 'text', 'YES'),
        ('trace_risk_records', 'interaction_count', 'int4', 'NO'),
        ('trace_risk_records', 'policy_event_count', 'int4', 'NO'),
        ('trace_risk_records', 'all_entity_ids', '_text', 'NO'),
        ('trace_risk_records', 'triggered_rule_ids', '_text', 'NO'),
        ('trace_risk_records', 'overall_confidence', 'numeric', 'YES'),
        ('trace_risk_records', 'contributing_interaction_risk_ids', '_uuid', 'NO'),
        ('alerts', 'alert_id', 'uuid', 'NO'),
        ('alerts', 'timestamp', 'timestamptz', 'NO'),
        ('alerts', 'trace_id', 'text', 'NO'),
        ('alerts', 'trace_risk_record_id', 'uuid', 'YES'),
        ('alerts', 'risk_level', 'text', 'NO'),
        ('alerts', 'enforcement_type', 'text', 'YES'),
        ('alerts', 'title', 'text', 'NO'),
        ('alerts', 'description', 'text', 'NO'),
        ('alerts', 'involved_entities', 'jsonb', 'NO'),
        ('alerts', 'triggered_rule_ids', '_text', 'NO'),
        ('alerts', 'status', 'text', 'NO'),
        ('alerts', 'alert_type', 'text', 'YES'),
        ('alerts', 'assigned_to', 'text', 'YES'),
        ('alerts', 'dedup_key', 'text', 'YES'),
        ('alerts', 'duplicate_count', 'int4', 'NO'),
        ('alerts', 'superseded_by', 'uuid', 'YES'),
        ('interaction_policy_decisions', 'decision_id', 'uuid', 'NO'),
        ('interaction_policy_decisions', 'interaction_id', 'text', 'NO'),
        ('interaction_policy_decisions', 'version', 'int4', 'NO'),
        ('interaction_policy_decisions', 'evaluated_at', 'timestamptz', 'NO'),
        ('interaction_policy_decisions', 'risk_level', 'text', 'NO'),
        ('interaction_policy_decisions', 'enforcement_type', 'text', 'YES'),
        ('interaction_policy_decisions', 'allowed_actions', '_text', 'NO'),
        ('interaction_policy_decisions', 'explanation', 'text', 'YES'),
        ('interaction_policy_decisions', 'triggered_rules', '_text', 'NO'),
        ('interaction_policy_decisions', 'confidence', 'numeric', 'YES'),
        ('interaction_policy_decisions', 'policy_version', 'text', 'YES'),
        ('interaction_policy_decisions', 'evidence_fingerprint', 'text', 'NO')
    ) AS r(table_name, column_name, udt_name, nullable)
    LEFT JOIN information_schema.columns c
      ON c.table_schema = 'public' AND c.table_name = r.table_name AND c.column_name = r.column_name
    WHERE c.udt_name IS DISTINCT FROM r.udt_name OR c.is_nullable IS DISTINCT FROM r.nullable;
    IF mismatch IS NOT NULL THEN
        RAISE EXCEPTION 'Historical risk schema is incomplete or incompatible: %', mismatch;
    END IF;

    SELECT string_agg(r.table_name || '.' || r.column_name, ', ') INTO mismatch
    FROM (VALUES
        ('interaction_risk_records', 'interaction_risk_id', 'gen_random_uuid()'),
        ('trace_risk_records', 'trace_risk_id', 'gen_random_uuid()'),
        ('alerts', 'alert_id', 'gen_random_uuid()'),
        ('interaction_policy_decisions', 'decision_id', 'gen_random_uuid()'),
        ('interaction_risk_records', 'triggered_rule_ids', '''{}''::text[]'),
        ('interaction_risk_records', 'legs_evidenced', '''{}''::text[]'),
        ('interaction_risk_records', 'opa_policy_versions_used', '''{}''::text[]'),
        ('trace_risk_records', 'all_entity_ids', '''{}''::text[]'),
        ('trace_risk_records', 'triggered_rule_ids', '''{}''::text[]'),
        ('trace_risk_records', 'contributing_interaction_risk_ids', '''{}''::uuid[]'),
        ('alerts', 'involved_entities', '''[]''::jsonb'),
        ('alerts', 'triggered_rule_ids', '''{}''::text[]'),
        ('alerts', 'status', '''open''::text'),
        ('alerts', 'duplicate_count', '0'),
        ('interaction_policy_decisions', 'allowed_actions', '''{}''::text[]'),
        ('interaction_policy_decisions', 'triggered_rules', '''{}''::text[]')
    ) AS r(table_name, column_name, expected_default)
    JOIN information_schema.columns c
      ON c.table_schema = 'public' AND c.table_name = r.table_name AND c.column_name = r.column_name
    WHERE c.column_default IS DISTINCT FROM r.expected_default;
    IF mismatch IS NOT NULL THEN
        RAISE EXCEPTION 'Historical risk schema has incompatible defaults: %', mismatch;
    END IF;
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND (table_name, column_name) IN (
              ('interaction_risk_records', 'overall_confidence'),
              ('trace_risk_records', 'overall_confidence'),
              ('interaction_policy_decisions', 'confidence'))
          AND (numeric_precision IS DISTINCT FROM 4 OR numeric_scale IS DISTINCT FROM 3)
    ) THEN
        RAISE EXCEPTION 'Historical risk confidence columns must be NUMERIC(4,3)';
    END IF;
    IF pg_get_serial_sequence('public.interaction_risk_records', 'seq')
           IS DISTINCT FROM 'public.interaction_risk_seq'
       OR NOT EXISTS (
           SELECT 1 FROM information_schema.columns
           WHERE table_schema = 'public' AND table_name = 'interaction_risk_records' AND column_name = 'seq'
             AND column_default = 'nextval(''interaction_risk_seq''::regclass)'
       ) THEN
        RAISE EXCEPTION 'Historical risk cursor sequence/default is missing or incompatible';
    END IF;

    SELECT string_agg(r.index_name, ', ') INTO mismatch
    FROM (VALUES
        ('interaction_risk_records', 'interaction_risk_records_pkey', ARRAY['interaction_risk_id'], true, 'btree'),
        ('interaction_risk_records', 'interaction_risk_records_version_uq', ARRAY['interaction_id', 'version'], true, 'btree'),
        ('interaction_risk_records', 'interaction_risk_records_latest_idx', ARRAY['interaction_id', 'computed_at'], false, 'btree'),
        ('interaction_risk_records', 'interaction_risk_records_trace_idx', ARRAY['trace_id'], false, 'btree'),
        ('interaction_risk_records', 'interaction_risk_records_risk_level_idx', ARRAY['risk_level'], false, 'btree'),
        ('interaction_risk_records', 'interaction_risk_records_rule_ids_gin', ARRAY['triggered_rule_ids'], false, 'gin'),
        ('interaction_risk_records', 'interaction_risk_records_seq_idx', ARRAY['seq'], false, 'btree'),
        ('trace_risk_records', 'trace_risk_records_pkey', ARRAY['trace_risk_id'], true, 'btree'),
        ('trace_risk_records', 'trace_risk_records_version_uq', ARRAY['trace_id', 'version'], true, 'btree'),
        ('trace_risk_records', 'trace_risk_records_latest_idx', ARRAY['trace_id', 'computed_at'], false, 'btree'),
        ('alerts', 'alerts_pkey', ARRAY['alert_id'], true, 'btree'),
        ('alerts', 'alerts_dedup_key_idx', ARRAY['dedup_key'], false, 'btree'),
        ('alerts', 'alerts_status_risk_level_idx', ARRAY['status', 'risk_level'], false, 'btree'),
        ('alerts', 'alerts_trace_idx', ARRAY['trace_id'], false, 'btree'),
        ('interaction_policy_decisions', 'interaction_policy_decisions_pkey', ARRAY['decision_id'], true, 'btree'),
        ('interaction_policy_decisions', 'interaction_policy_decisions_version_uq', ARRAY['interaction_id', 'version'], true, 'btree'),
        ('interaction_policy_decisions', 'interaction_policy_decisions_latest_idx', ARRAY['interaction_id', 'evaluated_at'], false, 'btree')
    ) AS r(table_name, index_name, columns, is_unique, method)
    LEFT JOIN pg_class idx ON idx.relname = r.index_name AND idx.relnamespace = 'public'::regnamespace
    LEFT JOIN pg_index i ON i.indexrelid = idx.oid AND i.indrelid = to_regclass('public.' || r.table_name)
    LEFT JOIN pg_am am ON am.oid = idx.relam
    WHERE i.indisvalid IS DISTINCT FROM true OR i.indisready IS DISTINCT FROM true
       OR i.indisunique IS DISTINCT FROM r.is_unique OR am.amname IS DISTINCT FROM r.method
       OR i.indpred IS NOT NULL OR i.indexprs IS NOT NULL
       OR r.columns IS DISTINCT FROM ARRAY(
           SELECT pg_get_indexdef(idx.oid, n, true) FROM generate_series(1, i.indnkeyatts) n
       );
    IF mismatch IS NOT NULL THEN
        RAISE EXCEPTION 'Historical risk indexes are missing or incompatible: %', mismatch;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger t
        WHERE t.tgrelid = 'public.interaction_risk_records'::regclass
          AND t.tgname = 'dg_interaction_risk_notify' AND t.tgtype = 4 AND t.tgenabled IN ('O', 'A')
          AND t.tgfoid = to_regprocedure('public.dg_notify_interaction_risk()')
    ) THEN
        RAISE EXCEPTION 'Historical risk INSERT notification trigger is missing or incompatible';
    END IF;

    INSERT INTO public.alembic_version (version_num) VALUES ('0019_interaction_risk_seq')
    ON CONFLICT (version_num) DO NOTHING;
END;
$adopt$;
COMMIT;
