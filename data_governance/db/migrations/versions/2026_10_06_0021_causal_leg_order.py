"""Rebuild data lineage in causal leg order and queue later trace corrections.

Existing lineage was derived in ingestion order. Clear its rows and coverage
claims, then queue every trace with legs for re-derivation by the data-lineage
processor. The queue is durable across restarts and is also filled when an
interaction's parent/endpoints or a leg's timing/payload changes in place.
Deleting an interaction or leg also queues the old trace and removes lineage
for the deleted leg before its trace identity disappears.

The graph algorithm used to give tool calls inferred from an LLM span the
span's full start/end interval. Project existing output calls to the LLM
completion boundary and input replays to its request boundary before replay.
Only tool calls sharing the LLM anchor and carrying tool-call arguments qualify;
an independently observed tool response keeps its own time.

``prefix_leg_keys`` records the inclusive causal prefix at a partial trace's
missing-payload cutoff. ``stopped_at_seq`` remains the cutoff leg's ingestion id,
but cannot be used as a numeric boundary for missing-row recovery.

Revision ID: 0021_causal_leg_order
Revises: 0020_entity_namespace
Create Date: 2026-10-06
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "0021_causal_leg_order"
down_revision: Union[str, Sequence[str], None] = "0020_entity_namespace"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE lineage_trace_status ADD COLUMN prefix_leg_keys TEXT[] "
        "NOT NULL DEFAULT '{}'::TEXT[]"
    )
    op.execute("CREATE SEQUENCE data_lineage_dirty_generation")
    op.execute(
        """
        CREATE TABLE data_lineage_dirty_traces (
            trace_id TEXT PRIMARY KEY,
            generation BIGINT NOT NULL
                DEFAULT nextval('data_lineage_dirty_generation')
        )
        """
    )
    op.execute(
        """
        CREATE FUNCTION dg_mark_lineage_interaction_dirty() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            trace TEXT;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                DELETE FROM lineage_metadata WHERE interaction_id = OLD.id;
                trace := OLD.trace_id;
            ELSIF TG_OP = 'UPDATE'
               AND NEW.parent_interaction_id IS NOT DISTINCT FROM OLD.parent_interaction_id
               AND NEW.caller_entity_id IS NOT DISTINCT FROM OLD.caller_entity_id
               AND NEW.callee_entity_id IS NOT DISTINCT FROM OLD.callee_entity_id
               AND NEW.trace_id IS NOT DISTINCT FROM OLD.trace_id
            THEN
                RETURN NEW;
            ELSE
                trace := NEW.trace_id;
                IF TG_OP = 'UPDATE' AND NEW.trace_id IS DISTINCT FROM OLD.trace_id THEN
                    INSERT INTO data_lineage_dirty_traces (trace_id)
                    VALUES (OLD.trace_id)
                    ON CONFLICT (trace_id) DO UPDATE
                        SET generation = nextval('data_lineage_dirty_generation');
                END IF;
            END IF;
            INSERT INTO data_lineage_dirty_traces (trace_id)
            VALUES (trace)
            ON CONFLICT (trace_id) DO UPDATE
                SET generation = nextval('data_lineage_dirty_generation');
            PERFORM pg_notify('dg_legs_inserted', '');
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER dg_lineage_interaction_dirty
        AFTER INSERT OR UPDATE OR DELETE ON interactions
        FOR EACH ROW EXECUTE FUNCTION dg_mark_lineage_interaction_dirty()
        """
    )
    op.execute(
        """
        CREATE FUNCTION dg_mark_lineage_leg_dirty() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            trace TEXT;
            interaction TEXT;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                DELETE FROM lineage_metadata
                WHERE interaction_id = OLD.interaction_id AND leg_type = OLD.leg_type;
                interaction := OLD.interaction_id;
            ELSIF TG_OP = 'UPDATE'
               AND NEW.occurred_at IS NOT DISTINCT FROM OLD.occurred_at
               AND NEW.payload_hash IS NOT DISTINCT FROM OLD.payload_hash
               AND NEW.seq IS NOT DISTINCT FROM OLD.seq
               AND NEW.interaction_id IS NOT DISTINCT FROM OLD.interaction_id
               AND NEW.leg_type IS NOT DISTINCT FROM OLD.leg_type
            THEN
                RETURN NEW;
            ELSE
                interaction := NEW.interaction_id;
                IF TG_OP = 'UPDATE' AND (
                    NEW.interaction_id IS DISTINCT FROM OLD.interaction_id OR
                    NEW.leg_type IS DISTINCT FROM OLD.leg_type
                ) THEN
                    DELETE FROM lineage_metadata
                    WHERE interaction_id = OLD.interaction_id AND leg_type = OLD.leg_type;
                    SELECT i.trace_id INTO trace FROM interactions i
                    WHERE i.id = OLD.interaction_id;
                    IF trace IS NOT NULL THEN
                        INSERT INTO data_lineage_dirty_traces (trace_id)
                        VALUES (trace)
                        ON CONFLICT (trace_id) DO UPDATE
                            SET generation = nextval('data_lineage_dirty_generation');
                    END IF;
                END IF;
            END IF;
            SELECT i.trace_id INTO trace FROM interactions i
            WHERE i.id = interaction;
            IF trace IS NOT NULL THEN
                INSERT INTO data_lineage_dirty_traces (trace_id)
                VALUES (trace)
                ON CONFLICT (trace_id) DO UPDATE
                    SET generation = nextval('data_lineage_dirty_generation');
            END IF;
            PERFORM pg_notify('dg_legs_inserted', '');
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER dg_lineage_leg_dirty
        AFTER INSERT OR UPDATE OR DELETE ON interaction_legs
        FOR EACH ROW EXECUTE FUNCTION dg_mark_lineage_leg_dirty()
        """
    )

    # Older graph projections copied the entire LLM span interval onto a tool
    # call inferred from that span's messages. Its output-side request cannot
    # precede the LLM response that disclosed it; an input-side replay cannot
    # extend past the LLM request that contains it. The graph edge ordinal
    # (persisted as seq) identifies which side of the LLM exchange it belongs
    # to, without treating seq as a general cross-service execution clock.
    # Response legs are moved only when they still look synthetic: the same
    # response payload and end boundary as the originating LLM. A tool with
    # independently observed completion keeps that observation.
    op.execute(
        """
        WITH inferred_tool_calls AS (
            SELECT tool_ix.id AS interaction_id,
                   s.ended_at AS old_response_at,
                   llm_response.payload_hash AS llm_response_hash,
                   CASE
                       WHEN tool_request.seq > llm_response.seq THEN s.ended_at
                       WHEN COALESCE(tool_response.seq, tool_request.seq)
                            < llm_request.seq THEN s.started_at
                   END AS projected_at
            FROM interactions tool_ix
            JOIN entities tool_entity
              ON tool_entity.id = tool_ix.callee_entity_id
             AND tool_entity.kind = 'tool'
            JOIN interaction_legs tool_request
              ON tool_request.interaction_id = tool_ix.id
             AND tool_request.leg_type = 'request'
            LEFT JOIN interaction_legs tool_response
              ON tool_response.interaction_id = tool_ix.id
             AND tool_response.leg_type = 'response'
            JOIN interaction_payloads args
              ON args.content_hash = tool_request.payload_hash
             AND args.content_kind = 'tool_call_arguments'
            JOIN interaction_spans tool_anchor
              ON tool_anchor.interaction_id = tool_ix.id
             AND tool_anchor.role = 'anchor'
            JOIN spans s
              ON s.trace_id = tool_ix.trace_id
             AND s.span_id = split_part(tool_anchor.span_id, '#', 1)
            JOIN interaction_spans llm_anchor
              ON llm_anchor.trace_id = tool_ix.trace_id
             AND llm_anchor.role = 'anchor'
             AND split_part(llm_anchor.span_id, '#', 1) = s.span_id
            JOIN interactions llm_ix
              ON llm_ix.id = llm_anchor.interaction_id
             AND llm_ix.caller_entity_id = tool_ix.caller_entity_id
            JOIN entities llm_entity
              ON llm_entity.id = llm_ix.callee_entity_id
             AND llm_entity.kind = 'llm'
            JOIN interaction_legs llm_request
              ON llm_request.interaction_id = llm_ix.id
             AND llm_request.leg_type = 'request'
            LEFT JOIN interaction_legs llm_response
              ON llm_response.interaction_id = llm_ix.id
             AND llm_response.leg_type = 'response'
        )
        UPDATE interaction_legs leg
        SET occurred_at = inferred.projected_at
        FROM inferred_tool_calls inferred
        WHERE leg.interaction_id = inferred.interaction_id
          AND inferred.projected_at IS NOT NULL
          AND (
              leg.leg_type = 'request'
              OR (
                  leg.leg_type = 'response'
                  AND leg.occurred_at IS NOT DISTINCT FROM inferred.old_response_at
                  AND leg.payload_hash IS NOT DISTINCT FROM inferred.llm_response_hash
              )
          )
          AND leg.occurred_at IS DISTINCT FROM inferred.projected_at
        """
    )

    # A previous complete claim can be wrong even when every payload exists.
    # Remove both the metadata and the claim before replay; absence reads as
    # unknown while the durable queue is drained.
    op.execute("DELETE FROM lineage_metadata")
    op.execute("DELETE FROM lineage_trace_status")
    op.execute(
        """
        INSERT INTO data_lineage_dirty_traces (trace_id)
        SELECT DISTINCT i.trace_id
        FROM interactions i JOIN interaction_legs l ON l.interaction_id = i.id
        ON CONFLICT (trace_id) DO NOTHING
        """
    )


def downgrade() -> None:
    # The timestamp correction above is a data repair. Downgrading removes the
    # new replay machinery but does not invent the old, misleading tool times.
    op.execute("DROP TRIGGER IF EXISTS dg_lineage_leg_dirty ON interaction_legs")
    op.execute("DROP FUNCTION IF EXISTS dg_mark_lineage_leg_dirty()")
    op.execute("DROP TRIGGER IF EXISTS dg_lineage_interaction_dirty ON interactions")
    op.execute("DROP FUNCTION IF EXISTS dg_mark_lineage_interaction_dirty()")
    op.execute("DROP TABLE IF EXISTS data_lineage_dirty_traces")
    op.execute("DROP SEQUENCE IF EXISTS data_lineage_dirty_generation")
    op.execute("ALTER TABLE lineage_trace_status DROP COLUMN prefix_leg_keys")
    op.execute("DELETE FROM lineage_metadata")
    op.execute("DELETE FROM lineage_trace_status")
    op.execute(
        "UPDATE processor_state SET last_processed_seq = 0 "
        "WHERE processor_name = 'data_lineage'"
    )
