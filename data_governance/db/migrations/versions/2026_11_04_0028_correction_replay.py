"""Replay label lineage and risk after in-place interaction corrections.

Each consumer retains its own durable correction progress; consuming data
lineage's queue cannot make label lineage miss an already-cursored correction.
Generations fence acknowledgments against a correction committed during a
derivation. Existing traces are queued to adopt the shared causal leg order.

Revision ID: 0028_correction_replay
Revises: 0027_merge_main_and_risk
Create Date: 2026-11-04
"""

from __future__ import annotations

from alembic import op

revision: str = "0028_correction_replay"
down_revision: str = "0027_merge_main_and_risk"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SEQUENCE label_lineage_dirty_generation")
    op.execute(
        """
        CREATE TABLE label_lineage_dirty_traces (
            trace_id TEXT PRIMARY KEY,
            generation BIGINT NOT NULL DEFAULT nextval('label_lineage_dirty_generation')
        )
        """
    )
    op.execute(
        "CREATE INDEX label_lineage_dirty_traces_generation_idx "
        "ON label_lineage_dirty_traces (generation)"
    )
    op.execute(
        """
        CREATE FUNCTION dg_queue_label_trace(trace TEXT) RETURNS void
        LANGUAGE plpgsql AS $$
        BEGIN
            IF trace IS NULL THEN
                RETURN;
            END IF;
            INSERT INTO label_lineage_dirty_traces (trace_id) VALUES (trace)
            ON CONFLICT (trace_id) DO UPDATE
                SET generation = nextval('label_lineage_dirty_generation');
            PERFORM pg_notify('dg_interaction_leg_ready', '');
        END;
        $$
        """
    )
    op.execute("CREATE SEQUENCE risk_recompute_generation")
    op.execute(
        "ALTER TABLE risk_recompute_requests "
        "ADD COLUMN generation BIGINT NOT NULL DEFAULT nextval('risk_recompute_generation'), "
        "ADD COLUMN base_recompute BOOLEAN NOT NULL DEFAULT false, "
        "ADD COLUMN labels_pending BOOLEAN NOT NULL DEFAULT true"
    )
    op.execute("CREATE INDEX risk_recompute_requests_generation_idx ON risk_recompute_requests (generation)")
    op.execute(
        """
        CREATE FUNCTION dg_refresh_risk_request_generation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            NEW.generation := nextval('risk_recompute_generation');
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER dg_risk_request_generation BEFORE UPDATE ON risk_recompute_requests "
        "FOR EACH ROW EXECUTE FUNCTION dg_refresh_risk_request_generation()"
    )
    op.execute(
        """
        CREATE FUNCTION dg_queue_interaction_risk(interaction TEXT) RETURNS void
        LANGUAGE plpgsql AS $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM interaction_legs WHERE interaction_id = interaction) THEN
                RETURN;
            END IF;
            INSERT INTO risk_recompute_requests (interaction_id, reason, base_recompute, labels_pending)
            VALUES (interaction, 'interaction corrected', true, false)
            ON CONFLICT (interaction_id) DO UPDATE SET
                base_recompute = true, requested_at = now(), reason = EXCLUDED.reason;
            PERFORM pg_notify('dg_interaction_leg_ready', '');
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE SEQUENCE trace_risk_recompute_generation
        """
    )
    op.execute(
        """
        CREATE TABLE trace_risk_recompute_requests (
            trace_id TEXT PRIMARY KEY,
            generation BIGINT NOT NULL DEFAULT nextval('trace_risk_recompute_generation')
        )
        """
    )
    op.execute(
        "CREATE INDEX trace_risk_recompute_requests_generation_idx "
        "ON trace_risk_recompute_requests (generation)"
    )
    op.execute(
        """
        CREATE FUNCTION dg_queue_trace_risk(trace TEXT) RETURNS void
        LANGUAGE plpgsql AS $$
        BEGIN
            IF trace IS NULL THEN
                RETURN;
            END IF;
            -- A trace with no risk evidence yet is first assessed when its
            -- first interaction risk record reaches the ordinary cursor arm.
            IF NOT EXISTS (SELECT 1 FROM trace_risk_records WHERE trace_id = trace)
               AND NOT EXISTS (SELECT 1 FROM interaction_risk_records WHERE trace_id = trace) THEN
                RETURN;
            END IF;
            INSERT INTO trace_risk_recompute_requests (trace_id) VALUES (trace)
            ON CONFLICT (trace_id) DO UPDATE
                SET generation = nextval('trace_risk_recompute_generation');
            PERFORM pg_notify('dg_interaction_risk_written', '');
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION dg_mark_governance_interaction_dirty() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                DELETE FROM leg_inherited_labels WHERE interaction_id = OLD.id;
                DELETE FROM payload_label_edges WHERE to_interaction_id = OLD.id;
                DELETE FROM risk_recompute_requests WHERE interaction_id = OLD.id;
                PERFORM dg_queue_label_trace(OLD.trace_id);
                PERFORM dg_queue_trace_risk(OLD.trace_id);
                RETURN NULL;
            END IF;
            IF EXISTS (SELECT 1 FROM interaction_legs WHERE interaction_id = NEW.id) THEN
                PERFORM dg_queue_label_trace(t.trace_id)
                FROM (SELECT DISTINCT unnest(ARRAY[OLD.trace_id, NEW.trace_id]) AS trace_id) t
                ORDER BY t.trace_id;
                PERFORM dg_queue_interaction_risk(NEW.id);
            END IF;
            PERFORM dg_queue_trace_risk(t.trace_id)
            FROM (SELECT DISTINCT unnest(ARRAY[OLD.trace_id, NEW.trace_id]) AS trace_id) t
            ORDER BY t.trace_id;
            RETURN NULL;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER dg_governance_interaction_deleted AFTER DELETE ON interactions "
        "FOR EACH ROW EXECUTE FUNCTION dg_mark_governance_interaction_dirty()"
    )
    op.execute(
        """
        CREATE TRIGGER dg_governance_interaction_dirty
        AFTER UPDATE ON interactions FOR EACH ROW
        WHEN (
            NEW.parent_interaction_id IS DISTINCT FROM OLD.parent_interaction_id
            OR NEW.caller_entity_id IS DISTINCT FROM OLD.caller_entity_id
            OR NEW.callee_entity_id IS DISTINCT FROM OLD.callee_entity_id
            OR NEW.trace_id IS DISTINCT FROM OLD.trace_id
        )
        EXECUTE FUNCTION dg_mark_governance_interaction_dirty()
        """
    )
    op.execute(
        """
        CREATE FUNCTION dg_mark_governance_leg_dirty() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'UPDATE' THEN
                PERFORM dg_queue_label_trace(i.trace_id)
                FROM interactions i
                WHERE i.id IN (OLD.interaction_id, NEW.interaction_id)
                ORDER BY i.trace_id;
                PERFORM dg_queue_trace_risk(i.trace_id)
                FROM interactions i
                WHERE i.id IN (OLD.interaction_id, NEW.interaction_id)
                ORDER BY i.trace_id;
                PERFORM dg_queue_interaction_risk(NEW.interaction_id);
                IF OLD.interaction_id = NEW.interaction_id AND OLD.leg_type = NEW.leg_type THEN
                    RETURN NULL;
                END IF;
            END IF;
            DELETE FROM leg_inherited_labels
            WHERE interaction_id = OLD.interaction_id AND leg_type = OLD.leg_type;
            DELETE FROM payload_label_edges
            WHERE to_interaction_id = OLD.interaction_id AND to_leg_type = OLD.leg_type;
            PERFORM dg_queue_label_trace((SELECT trace_id FROM interactions WHERE id = OLD.interaction_id));
            PERFORM dg_queue_trace_risk((SELECT trace_id FROM interactions WHERE id = OLD.interaction_id));
            PERFORM dg_queue_interaction_risk(OLD.interaction_id);
            RETURN NULL;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER dg_governance_leg_deleted AFTER DELETE ON interaction_legs "
        "FOR EACH ROW EXECUTE FUNCTION dg_mark_governance_leg_dirty()"
    )
    op.execute(
        """
        CREATE TRIGGER dg_governance_leg_dirty
        AFTER UPDATE ON interaction_legs FOR EACH ROW
        WHEN (
            NEW.occurred_at IS DISTINCT FROM OLD.occurred_at
            OR NEW.payload_hash IS DISTINCT FROM OLD.payload_hash
            OR NEW.seq IS DISTINCT FROM OLD.seq
            OR NEW.interaction_id IS DISTINCT FROM OLD.interaction_id
            OR NEW.leg_type IS DISTINCT FROM OLD.leg_type
        )
        EXECUTE FUNCTION dg_mark_governance_leg_dirty()
        """
    )
    op.execute(
        """
        CREATE FUNCTION dg_mark_governance_legs_inserted() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            reach BIGINT;
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM inserted_legs) THEN
                RETURN NULL;
            END IF;
            -- Normal nextval inserts reach both consumers through their
            -- ingestion cursors. Graph-supplied ordinals may land behind a
            -- cursor or another already-committed leg (ADR-0035 / 0026).
            SELECT GREATEST(
                COALESCE((SELECT max(last_processed_seq) FROM processor_state
                          WHERE processor_name IN ('label_lineage', 'leg_ready')), 0),
                (SELECT l.seq FROM interaction_legs l
                 WHERE (l.interaction_id, l.leg_type) NOT IN (
                     SELECT n.interaction_id, n.leg_type FROM inserted_legs n)
                 ORDER BY l.seq DESC LIMIT 1)
            ) INTO reach;
            PERFORM dg_queue_label_trace(t.trace_id)
            FROM (SELECT DISTINCT i.trace_id FROM inserted_legs n
                  JOIN interactions i ON i.id = n.interaction_id WHERE n.seq <= reach) t
            ORDER BY t.trace_id;
            PERFORM dg_queue_interaction_risk(t.interaction_id)
            FROM (SELECT DISTINCT interaction_id FROM inserted_legs WHERE seq <= reach) t
            ORDER BY t.interaction_id;
            RETURN NULL;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER dg_governance_legs_inserted AFTER INSERT ON interaction_legs
        REFERENCING NEW TABLE AS inserted_legs
        FOR EACH STATEMENT EXECUTE FUNCTION dg_mark_governance_legs_inserted()
        """
    )
    op.execute(
        """
        INSERT INTO label_lineage_dirty_traces (trace_id)
        SELECT trace_id FROM label_lineage_trace_status
        UNION
        SELECT i.trace_id FROM interactions i JOIN interaction_legs l ON l.interaction_id = i.id
        """
    )
    op.execute(
        """
        INSERT INTO risk_recompute_requests (interaction_id, reason, base_recompute, labels_pending)
        SELECT DISTINCT i.id, 'causal order correction replay', true, false
        FROM interactions i JOIN interaction_legs l ON l.interaction_id = i.id
        ON CONFLICT (interaction_id) DO UPDATE SET
            base_recompute = true, requested_at = now(), reason = EXCLUDED.reason
        """
    )
    op.execute(
        """
        INSERT INTO trace_risk_recompute_requests (trace_id)
        SELECT trace_id FROM trace_risk_records
        UNION
        SELECT trace_id FROM interaction_risk_records
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER dg_governance_legs_inserted ON interaction_legs")
    op.execute("DROP FUNCTION dg_mark_governance_legs_inserted()")
    op.execute("DROP TRIGGER dg_governance_leg_deleted ON interaction_legs")
    op.execute("DROP TRIGGER dg_governance_leg_dirty ON interaction_legs")
    op.execute("DROP FUNCTION dg_mark_governance_leg_dirty()")
    op.execute("DROP TRIGGER dg_governance_interaction_deleted ON interactions")
    op.execute("DROP TRIGGER dg_governance_interaction_dirty ON interactions")
    op.execute("DROP FUNCTION dg_mark_governance_interaction_dirty()")
    op.execute("DROP FUNCTION dg_queue_trace_risk(TEXT)")
    op.execute("DROP TABLE trace_risk_recompute_requests")
    op.execute("DROP SEQUENCE trace_risk_recompute_generation")
    op.execute("DROP FUNCTION dg_queue_interaction_risk(TEXT)")
    op.execute("DROP TRIGGER dg_risk_request_generation ON risk_recompute_requests")
    op.execute("DROP FUNCTION dg_refresh_risk_request_generation()")
    op.execute("DROP INDEX risk_recompute_requests_generation_idx")
    op.execute(
        "ALTER TABLE risk_recompute_requests DROP COLUMN generation, "
        "DROP COLUMN base_recompute, DROP COLUMN labels_pending"
    )
    op.execute("DROP SEQUENCE risk_recompute_generation")
    op.execute("DROP FUNCTION dg_queue_label_trace(TEXT)")
    op.execute("DROP TABLE label_lineage_dirty_traces")
    op.execute("DROP SEQUENCE label_lineage_dirty_generation")
