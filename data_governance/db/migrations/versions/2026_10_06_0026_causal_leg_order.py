"""Rebuild data lineage in causal leg order and queue later trace corrections.

Existing lineage was derived in ingestion order. Clear its rows and coverage
claims, then queue every trace with legs for re-derivation by the data-lineage
processor. The queue is durable across restarts and is also filled when an
interaction's parent/endpoints or a leg's timing/payload changes in place.
Deleting an interaction or leg also queues the old trace and removes lineage
for the deleted leg before its trace identity disappears. An inserted leg is
queued only when the ``seq`` cursor might never offer it: its ``seq`` is at or
below the data-lineage cursor or the ``seq`` of a leg outside its statement. The
graph algorithm writes its own edge ordinals as ``seq``, so its legs can land
there, even in a trace that is already derived. A leg taking the next ``seq``
reaches the processor through the cursor, and an interaction gains lineage only
through its legs. Queuing those too would derive every new trace twice.

The graph algorithm used to give tool calls inferred from an LLM span the
span's full start/end interval. Project existing output calls to the LLM
completion boundary and input replays to its request boundary before replay.
Only tool calls sharing the LLM anchor and carrying tool-call arguments qualify;
an independently observed tool response keeps its own time.
An unended LLM span also gives its own existing, untimed response its start,
unless the LLM call encloses another call, matching the graph adapter.

``prefix_leg_keys`` records the inclusive causal prefix at a partial trace's
missing-payload cutoff. ``stopped_at_seq`` remains the cutoff leg's ingestion id,
but cannot be used as a numeric boundary for missing-row recovery.

**Numbered 0026, on 0022.** This revision was written as 0021 on 0020, and
main's 0022 (issue #286) shipped on the same parent first. Two revisions off one
parent are two heads, and only the unshipped one may move (see 0020's note). The
``risk`` chain already stamps 0023 and 0024 in a live ``alembic_version``, and
0025 is taken on a lineage branch, so this revision takes the next free number.
This supersedes 0022's note that it would re-parent onto this PR's 0021: 0022
shipped unchanged, and this revision moved onto it instead.
On 0022 the content kind lives on ``interaction_legs``, not on the payload row,
so the inferred-tool repair below reads the leg's kind, with a structural
fallback for legs written before 0022 (see the repair's comment).

Revision ID: 0026_causal_leg_order
Revises: 0022_content_kind_on_legs
Create Date: 2026-10-06
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "0026_causal_leg_order"
down_revision: Union[str, Sequence[str], None] = "0022_content_kind_on_legs"
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
    # The processor drains the queue oldest generation first, in LIMITed
    # batches. The upgrade below queues every trace, so the read needs an
    # index rather than a full scan and sort.
    op.execute(
        "CREATE INDEX data_lineage_dirty_traces_generation_idx "
        "ON data_lineage_dirty_traces (generation)"
    )
    # Queue (or re-queue under a newer generation) one trace and wake the
    # processor. 0012 announces leg inserts and updates on the same channel;
    # this covers interaction corrections and deletions, which it does not.
    op.execute(
        """
        CREATE FUNCTION dg_queue_lineage_trace(trace TEXT) RETURNS void
        LANGUAGE plpgsql AS $$
        BEGIN
            IF trace IS NULL THEN
                RETURN;
            END IF;
            INSERT INTO data_lineage_dirty_traces (trace_id)
            VALUES (trace)
            ON CONFLICT (trace_id) DO UPDATE
                SET generation = nextval('data_lineage_dirty_generation');
            PERFORM pg_notify('dg_legs_inserted', '');
        END;
        $$
        """
    )
    # Corrections and deletions queue. A new leg normally takes a new seq and
    # reaches the processor through its cursor; the insert trigger further down
    # queues one that cannot. An interaction without legs has no lineage and
    # constrains no leg's order. The UPDATE triggers filter
    # on causal inputs in WHEN, so an unchanged re-derivation upsert never
    # enters plpgsql. A DELETE trigger's WHEN cannot read NEW, hence two
    # triggers per table.
    op.execute(
        """
        CREATE FUNCTION dg_mark_lineage_interaction_dirty() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                DELETE FROM lineage_metadata WHERE interaction_id = OLD.id;
                PERFORM dg_queue_lineage_trace(OLD.trace_id);
                RETURN NULL;
            END IF;
            -- The sidecar writer links a new child's parent before writing
            -- its legs. Inserting those legs reaches the processor.
            IF NOT EXISTS (
                SELECT 1 FROM interaction_legs WHERE interaction_id = NEW.id
            ) THEN
                RETURN NULL;
            END IF;
            PERFORM dg_queue_lineage_trace(NEW.trace_id);
            IF NEW.trace_id <> OLD.trace_id THEN
                PERFORM dg_queue_lineage_trace(OLD.trace_id);
            END IF;
            RETURN NULL;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER dg_lineage_interaction_dirty
        AFTER UPDATE ON interactions
        FOR EACH ROW
        WHEN (
            NEW.parent_interaction_id IS DISTINCT FROM OLD.parent_interaction_id
            OR NEW.caller_entity_id IS DISTINCT FROM OLD.caller_entity_id
            OR NEW.callee_entity_id IS DISTINCT FROM OLD.callee_entity_id
            OR NEW.trace_id IS DISTINCT FROM OLD.trace_id
        )
        EXECUTE FUNCTION dg_mark_lineage_interaction_dirty()
        """
    )
    op.execute(
        """
        CREATE TRIGGER dg_lineage_interaction_deleted
        AFTER DELETE ON interactions
        FOR EACH ROW EXECUTE FUNCTION dg_mark_lineage_interaction_dirty()
        """
    )
    op.execute(
        """
        CREATE FUNCTION dg_mark_lineage_leg_dirty() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'UPDATE' THEN
                PERFORM dg_queue_lineage_trace(
                    (SELECT trace_id FROM interactions WHERE id = NEW.interaction_id)
                );
                IF NEW.interaction_id = OLD.interaction_id
                   AND NEW.leg_type = OLD.leg_type THEN
                    RETURN NULL;
                END IF;
            END IF;
            -- A deleted or re-keyed leg leaves its old position. Remove that
            -- position's lineage before its trace identity can disappear.
            DELETE FROM lineage_metadata
            WHERE interaction_id = OLD.interaction_id AND leg_type = OLD.leg_type;
            PERFORM dg_queue_lineage_trace(
                (SELECT trace_id FROM interactions WHERE id = OLD.interaction_id)
            );
            RETURN NULL;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER dg_lineage_leg_dirty
        AFTER UPDATE ON interaction_legs
        FOR EACH ROW
        WHEN (
            NEW.occurred_at IS DISTINCT FROM OLD.occurred_at
            OR NEW.payload_hash IS DISTINCT FROM OLD.payload_hash
            OR NEW.seq IS DISTINCT FROM OLD.seq
            OR NEW.interaction_id IS DISTINCT FROM OLD.interaction_id
            OR NEW.leg_type IS DISTINCT FROM OLD.leg_type
        )
        EXECUTE FUNCTION dg_mark_lineage_leg_dirty()
        """
    )
    op.execute(
        """
        CREATE TRIGGER dg_lineage_leg_deleted
        AFTER DELETE ON interaction_legs
        FOR EACH ROW EXECUTE FUNCTION dg_mark_lineage_leg_dirty()
        """
    )
    # A new leg reaches the cursor arm only if the leg's seq ends up above the
    # cursor when the leg becomes visible. The streaming and sidecar writers
    # take nextval, above every stored leg. The graph writer supplies its
    # trace's edge ordinals, which can sit at or below the cursor, including in
    # a trace that is already derived. The stale sweep skips such a leg when it
    # has no payload or lies outside a partial trace's stored causal prefix.
    #
    # Below the cursor is not the whole test. The cursor also passes a seq
    # while the leg is uncommitted if it derives a committed leg with an equal
    # or higher seq, and graph ordinals of other traces are commonly higher. So
    # a leg queues unless its seq is above both the cursor and every leg written
    # outside its statement. Rows of the same statement commit together and
    # are excluded, so a multi-row nextval insert queues nothing. Every leg a
    # snapshot shows here is committed, or written earlier by this transaction.
    # With one leg writer at a time, which the nextval cursor already assumes,
    # no leg that commits before this one can pass it unseen.
    #
    # Statement-level with a transition table, as WHEN cannot hold a subquery:
    # the cursor and the highest other seq are two index reads per statement,
    # and an upsert that inserted nothing returns before both.
    op.execute(
        """
        CREATE FUNCTION dg_mark_lineage_legs_inserted() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            reach BIGINT;
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM inserted_legs) THEN
                RETURN NULL;
            END IF;
            SELECT GREATEST(
                COALESCE((
                    SELECT last_processed_seq FROM processor_state
                    WHERE processor_name = 'data_lineage'
                ), 0),
                (
                    SELECT l.seq FROM interaction_legs l
                    WHERE (l.interaction_id, l.leg_type) NOT IN (
                        SELECT n.interaction_id, n.leg_type FROM inserted_legs n
                    )
                    ORDER BY l.seq DESC
                    LIMIT 1
                )
            ) INTO reach;
            PERFORM dg_queue_lineage_trace(behind.trace_id)
            FROM (
                SELECT DISTINCT i.trace_id
                FROM inserted_legs n
                JOIN interactions i ON i.id = n.interaction_id
                WHERE n.seq <= reach
                ORDER BY i.trace_id
            ) behind;
            RETURN NULL;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER dg_lineage_leg_inserted
        AFTER INSERT ON interaction_legs
        REFERENCING NEW TABLE AS inserted_legs
        FOR EACH STATEMENT EXECUTE FUNCTION dg_mark_lineage_legs_inserted()
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
    #
    # The LLM call compared is the one holding the span's own anchor row. The
    # adapter gives that row to the span's first call by callee key, so an LLM
    # (`llm:` sorts before `tool:`). The row is unique per span, while synthetic
    # `<span>#<callee>#<order>` rows can name other LLM calls, including ones
    # left by an earlier derivation of the trace, whose legs may disagree about
    # the side. DISTINCT ON keeps one row per tool call, so each leg takes one
    # defined projection. As in the adapter, an output call on an LLM span
    # with no recorded end falls back to the span's start.
    #
    # "Carries tool-call arguments" is read from the leg's own content kind
    # (0022). Legs written before 0022 have no kind: 0022 dropped the payload
    # row's column without a backfill, and it runs before this revision. For
    # those legs the test is structural. An inferred call without arguments
    # takes its request body off the shared LLM span, so a request body that is
    # neither of the LLM's own bodies is the call's arguments.
    op.execute(
        """
        WITH inferred_tool_calls AS (
            SELECT DISTINCT ON (tool_ix.id)
                   tool_ix.id AS interaction_id,
                   s.ended_at AS old_response_at,
                   llm_response.payload_hash AS llm_response_hash,
                   CASE
                       WHEN tool_request.seq > llm_response.seq
                           THEN COALESCE(s.ended_at, s.started_at)
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
             AND tool_request.payload_hash IS NOT NULL
            LEFT JOIN interaction_legs tool_response
              ON tool_response.interaction_id = tool_ix.id
             AND tool_response.leg_type = 'response'
            JOIN interaction_spans tool_anchor
              ON tool_anchor.interaction_id = tool_ix.id
             AND tool_anchor.role = 'anchor'
            JOIN spans s
              ON s.trace_id = tool_ix.trace_id
             AND s.span_id = split_part(tool_anchor.span_id, '#', 1)
            JOIN interaction_spans llm_anchor
              ON llm_anchor.trace_id = tool_ix.trace_id
             AND llm_anchor.role = 'anchor'
             AND llm_anchor.span_id = s.span_id
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
            WHERE tool_request.content_kind = 'tool_call_arguments'
               OR (
                   tool_request.content_kind IS NULL
                   AND tool_request.payload_hash
                       IS DISTINCT FROM llm_request.payload_hash
                   AND tool_request.payload_hash
                       IS DISTINCT FROM llm_response.payload_hash
               )
            ORDER BY tool_ix.id, tool_anchor.span_id
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

    # An output call on an unended LLM span was moved to the span's start above.
    # Its LLM's own response must land there too, before the call's higher graph
    # ordinal, or lineage replay loses the LLM hop. Mirror the graph adapter's
    # _time_unended_llm_responses: only an existing untimed response on a real
    # OpenInference LLM span, and not a call enclosing another interaction.
    # Read the span evidence rather than content_kind, which 0022 did not
    # backfill on old legs. Synthetic anchor ids still name the same real span.
    # Freeze this repair in SQL so later adapter changes cannot alter upgrades.
    op.execute(
        """
        WITH unended_llm_calls AS (
            SELECT DISTINCT ON (llm_ix.id)
                   llm_ix.id AS interaction_id, s.started_at
            FROM interactions llm_ix
            JOIN entities llm_entity
              ON llm_entity.id = llm_ix.callee_entity_id
             AND llm_entity.kind = 'llm'
            JOIN interaction_spans anchor
              ON anchor.interaction_id = llm_ix.id
             AND anchor.trace_id = llm_ix.trace_id
             AND anchor.role = 'anchor'
            JOIN spans s
              ON s.trace_id = llm_ix.trace_id
             AND s.span_id = split_part(anchor.span_id, '#', 1)
            WHERE s.ended_at IS NULL
              AND s.attributes ->> 'openinference.span.kind' = 'LLM'
              AND starts_with(s.scope ->> 'name', 'openinference.instrumentation.')
              AND NOT EXISTS (
                  SELECT 1 FROM interactions child
                  WHERE child.trace_id = llm_ix.trace_id
                    AND child.parent_interaction_id = llm_ix.id
              )
            ORDER BY llm_ix.id, anchor.span_id
        )
        UPDATE interaction_legs leg
        SET occurred_at = llm.started_at
        FROM unended_llm_calls llm
        WHERE leg.interaction_id = llm.interaction_id
          AND leg.leg_type = 'response'
          AND leg.occurred_at IS NULL
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
    # The timestamp corrections above are data repairs. Downgrading removes
    # the replay machinery while retaining the repaired times.
    op.execute("DROP TRIGGER IF EXISTS dg_lineage_leg_inserted ON interaction_legs")
    op.execute("DROP FUNCTION IF EXISTS dg_mark_lineage_legs_inserted()")
    op.execute("DROP TRIGGER IF EXISTS dg_lineage_leg_deleted ON interaction_legs")
    op.execute("DROP TRIGGER IF EXISTS dg_lineage_leg_dirty ON interaction_legs")
    op.execute("DROP FUNCTION IF EXISTS dg_mark_lineage_leg_dirty()")
    op.execute("DROP TRIGGER IF EXISTS dg_lineage_interaction_deleted ON interactions")
    op.execute("DROP TRIGGER IF EXISTS dg_lineage_interaction_dirty ON interactions")
    op.execute("DROP FUNCTION IF EXISTS dg_mark_lineage_interaction_dirty()")
    op.execute("DROP FUNCTION IF EXISTS dg_queue_lineage_trace(TEXT)")
    op.execute("DROP TABLE IF EXISTS data_lineage_dirty_traces")
    op.execute("DROP SEQUENCE IF EXISTS data_lineage_dirty_generation")
    op.execute("ALTER TABLE lineage_trace_status DROP COLUMN prefix_leg_keys")
    op.execute("DELETE FROM lineage_metadata")
    op.execute("DELETE FROM lineage_trace_status")
    op.execute(
        "UPDATE processor_state SET last_processed_seq = 0 "
        "WHERE processor_name = 'data_lineage'"
    )
