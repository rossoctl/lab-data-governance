"""Label lineage: the edges labels travel along, what each leg inherited, and the
interactions whose decision must be recomputed.

Four tables beside ``lineage_metadata`` (data lineage is untouched), written by the
``label_lineage`` processor per trace, idempotently — every write is an upsert keyed
deterministically plus a trace-scoped delete of the rows the derivation no longer
produces (set membership, never a seq threshold; ADR-0028 D9's shape).

``payload_label_edges`` — one row per edge: a unit (``origin_*`` / ``unit_*``: the
classifier finding where it was found, its extent and labels) present at the to
leg's payload, reached from the occurrence at the from leg's payload. The key
``edge_id`` is a uuid5 of the natural key (trace, to leg, origin, from leg, held
run, span), so a re-derivation writes the same id. ``matched_*`` is the span at the
to payload, nullable for a match that has no span. ``match_kind`` declares the five
kinds the finders may write; the exact finder writes ``whole``. ``held_from`` /
``held_to`` are the run of the origin's canonical value the occurrence holds (the
whole value for a whole match). ``from_trace_id`` / ``origin_trace_id`` name another
trace when the carrier or the origin lives there; NULL means this trace, and in one
conversation they are always NULL. ``recombined`` is false for whole values.

``leg_inherited_labels`` — one row per payload-bearing leg of a derived trace, even
with nothing inherited: what reached it (tags by kind, highest level, origin and
edge counts), whether it was classified, and the ``payload_hash`` and ``matcher``
it was derived with — what the processor's stale arm compares and what the
leg-ready consumer's readiness waits for. A table, not a view: one indexed read per
leg at decision time, and a view has no derived-with hash to compare.

``label_lineage_trace_status`` — one row per derived trace: ``complete`` when every
payload-bearing leg was classified at derivation time, else ``partial``; absence
means not derived.

``risk_recompute_requests`` — one row per interaction whose inherited summary
changed after the risk engine may have decided it; the leg-ready consumer drains it.

``interaction_legs`` has no ``trace_id`` (ADR-0025), so the trace id is written on
every row here: the processor deletes and reads per trace without a join.

Hand-written ``op.execute`` only, per ADR-0002/0005. Reversible: the downgrade drops
the four tables (derived data, re-derivable from the stream by resetting the
``label_lineage`` cursor).

Revision ID: 0024_label_lineage
Revises: 0023_trace_risk_seq
Create Date: 2026-10-18
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "0024_label_lineage"
down_revision: Union[str, Sequence[str], None] = "0023_trace_risk_seq"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE payload_label_edges (
            edge_id               TEXT PRIMARY KEY,
            trace_id              TEXT NOT NULL,
            to_interaction_id     TEXT NOT NULL,
            to_leg_type           leg_type NOT NULL,
            to_payload_hash       TEXT NOT NULL,
            from_interaction_id   TEXT NOT NULL,
            from_leg_type         leg_type NOT NULL,
            from_payload_hash     TEXT NOT NULL,
            from_trace_id         TEXT,
            origin_interaction_id TEXT NOT NULL,
            origin_leg_type       leg_type NOT NULL,
            origin_payload_hash   TEXT NOT NULL,
            origin_trace_id       TEXT,
            unit_start            INTEGER NOT NULL,
            unit_end              INTEGER NOT NULL,
            unit_text             TEXT NOT NULL,
            unit_value            TEXT NOT NULL,
            entity_type           TEXT NOT NULL,
            regulatory_tags       TEXT[] NOT NULL DEFAULT '{}',
            sensitivity_level     TEXT NOT NULL,
            category              TEXT NOT NULL DEFAULT '',
            matched_text          TEXT,
            matched_start         INTEGER,
            matched_end           INTEGER,
            matcher               TEXT NOT NULL,
            match_kind            TEXT NOT NULL CHECK (match_kind IN
                                      ('whole', 'normalized', 'structural', 'fragment', 'semantic')),
            score                 NUMERIC(4,3) NOT NULL CHECK (score >= 0 AND score <= 1),
            held_from             INTEGER NOT NULL,
            held_to               INTEGER NOT NULL,
            recombined            BOOLEAN NOT NULL DEFAULT false,
            seq                   BIGINT NOT NULL,
            CHECK (0 <= held_from AND held_from <= held_to),
            CHECK ((matched_start IS NULL) = (matched_end IS NULL))
        )
        """
    )
    op.execute("CREATE INDEX payload_label_edges_trace_idx ON payload_label_edges (trace_id)")
    op.execute(
        "CREATE INDEX payload_label_edges_to_idx "
        "ON payload_label_edges (to_interaction_id, to_leg_type)"
    )
    op.execute(
        "CREATE INDEX payload_label_edges_origin_idx "
        "ON payload_label_edges (origin_payload_hash, unit_start, unit_end)"
    )
    op.execute(
        """
        CREATE TABLE leg_inherited_labels (
            interaction_id          TEXT NOT NULL,
            leg_type                leg_type NOT NULL,
            trace_id                TEXT NOT NULL,
            payload_hash            TEXT NOT NULL,
            seq                     BIGINT NOT NULL,
            matcher                 TEXT NOT NULL,
            classified              BOOLEAN NOT NULL,
            inherited_tags_whole    TEXT[] NOT NULL DEFAULT '{}',
            inherited_tags_fragment TEXT[] NOT NULL DEFAULT '{}',
            max_inherited_level     TEXT,
            origin_count            INTEGER NOT NULL DEFAULT 0,
            recombined              BOOLEAN NOT NULL DEFAULT false,
            recombined_units        JSONB NOT NULL DEFAULT '[]'::jsonb,
            edge_count              INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (interaction_id, leg_type)
        )
        """
    )
    op.execute("CREATE INDEX leg_inherited_labels_trace_idx ON leg_inherited_labels (trace_id)")
    op.execute(
        """
        CREATE TABLE label_lineage_trace_status (
            trace_id            TEXT PRIMARY KEY,
            status              TEXT NOT NULL CHECK (status IN ('complete', 'partial')),
            unclassified_legs   INTEGER NOT NULL,
            unanchored_findings INTEGER NOT NULL,
            derived_legs        INTEGER NOT NULL,
            edge_count          INTEGER NOT NULL,
            matcher             TEXT NOT NULL,
            derived_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE risk_recompute_requests (
            interaction_id TEXT PRIMARY KEY,
            requested_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            reason         TEXT NOT NULL
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS risk_recompute_requests")
    op.execute("DROP TABLE IF EXISTS label_lineage_trace_status")
    op.execute("DROP TABLE IF EXISTS leg_inherited_labels")
    op.execute("DROP TABLE IF EXISTS payload_label_edges")
