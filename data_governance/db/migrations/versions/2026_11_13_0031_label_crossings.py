"""Label lineage across conversations: what persistent entities received, and the
crossings drawn from it.

Two tables beside the label-lineage tables of 0024, which are not altered. A
crossing edge is an ordinary ``payload_label_edges`` row: its carrier is the leg of
an earlier conversation where the entity received the value (``from_trace_id``) and
its origin is that occurrence's origin unit (``origin_trace_id``), so every reader
keyed by the origin unit follows it unchanged. What the edge table cannot hold
lives here.

``label_occurrences`` — one row per labelled value a persistent entity received at
one leg, written by the ``label_lineage`` processor in the transaction that derives
the receiving conversation and replaced when it is derived again (trace-scoped
delete by set membership, as for the edges). ``occurrence_id`` is a uuid5 of its
natural key (entity, leg, origin unit, span, held run). It carries the value
(``value_text``, ``value_hash``, and ``core_hash`` with ``word_count``: the hash of
the value from its first letter or digit to its last, and how many words that holds.
A derivation hashes every run of 1..K consecutive words of a payload the entity sends,
K the most words among its occurrences of other conversations valid for those sends,
and loads by those hashes, so exactly the held values the payload contains are loaded;
the load index is (entity, core_hash, valid_to), and K is read from (entity, valid_to)
with the other columns it filters on included, so neither scans expired history), the
record that held it at the leg (``record_*``, NULL when it had none), the origin
unit and its labels (the type is the label type at the received leg), how the leg
knew the label (``evidence``, with ``level`` when crossed) and the interval in event
time it is valid for, strictly inside ``(valid_from, valid_to)``. A row is deleted
only when its conversation is derived again: there is no expiry job, and no result
depends on one.

``label_crossings`` — one row per outcome at a leaving leg: an asserted crossing
(``edge_id``, its ``level``, the occurrence it rests on) or a common value (no
edge: the value was held from more than M other conversations). ``conversations``
is the count taken at match time over the occurrences valid at the payload's time,
by distinct other conversation; stored as the evidence, never read as the rule.

Revision ID: 0031_label_crossings
Revises: 0029_hash_evidence_fingerprints (0030 is held by an open pull request;
re-pointed to it when that merges)
Create Date: 2026-11-13
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0031_label_crossings"
down_revision: Union[str, Sequence[str], None] = "0029_hash_evidence_fingerprints"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE label_occurrences (
            occurrence_id         TEXT PRIMARY KEY,
            entity_id             TEXT NOT NULL,
            trace_id              TEXT NOT NULL,
            interaction_id        TEXT NOT NULL,
            leg_type              leg_type NOT NULL,
            payload_hash          TEXT NOT NULL,
            value_text            TEXT NOT NULL,
            value_hash            TEXT NOT NULL,
            core_hash             TEXT NOT NULL,
            word_count            INTEGER NOT NULL,
            span_start            INTEGER NOT NULL,
            span_end              INTEGER NOT NULL,
            record_start          INTEGER,
            record_end            INTEGER,
            origin_trace_id       TEXT NOT NULL,
            origin_interaction_id TEXT NOT NULL,
            origin_leg_type       leg_type NOT NULL,
            origin_payload_hash   TEXT NOT NULL,
            unit_start            INTEGER NOT NULL,
            unit_end              INTEGER NOT NULL,
            unit_text             TEXT NOT NULL,
            unit_value            TEXT NOT NULL,
            entity_type           TEXT NOT NULL,
            regulatory_tags       TEXT[] NOT NULL DEFAULT '{}',
            sensitivity_level     TEXT NOT NULL,
            category              TEXT NOT NULL DEFAULT '',
            held_from             INTEGER NOT NULL,
            held_to               INTEGER NOT NULL,
            evidence              TEXT NOT NULL CHECK (evidence IN ('classified', 'inherited', 'crossed')),
            level                 TEXT CHECK (level IN ('value_only', 'corroborated')),
            valid_from            TIMESTAMPTZ NOT NULL,
            valid_to              TIMESTAMPTZ NOT NULL,
            CHECK (valid_from < valid_to),
            CHECK ((record_start IS NULL) = (record_end IS NULL)),
            CHECK ((level IS NOT NULL) = (evidence = 'crossed'))
        )
        """
    )
    op.execute("CREATE INDEX label_occurrences_load_idx ON label_occurrences (entity_id, core_hash, valid_to)")
    op.execute("CREATE INDEX label_occurrences_valid_idx ON label_occurrences (entity_id, valid_to) "
               "INCLUDE (word_count, valid_from, trace_id)")
    op.execute("CREATE INDEX label_occurrences_trace_idx ON label_occurrences (trace_id)")
    op.execute(
        """
        CREATE TABLE label_crossings (
            crossing_id    TEXT PRIMARY KEY,
            trace_id       TEXT NOT NULL,
            interaction_id TEXT NOT NULL,
            leg_type       leg_type NOT NULL,
            value_hash     TEXT NOT NULL,
            conversations  INTEGER NOT NULL CHECK (conversations >= 0),
            edge_id        TEXT UNIQUE,
            occurrence_id  TEXT,
            level          TEXT CHECK (level IN ('value_only', 'corroborated')),
            CHECK ((edge_id IS NULL) = (level IS NULL)),
            CHECK ((edge_id IS NULL) = (occurrence_id IS NULL))
        )
        """
    )
    op.execute("CREATE INDEX label_crossings_trace_idx ON label_crossings (trace_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS label_crossings")
    op.execute("DROP TABLE IF EXISTS label_occurrences")
