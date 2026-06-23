"""Tests for the P-interactions derived-schema migration (issue #69).

The migration creates the first real ``entities`` / ``interactions`` /
``entity_spans`` / ``interaction_spans`` / ``interaction_payloads`` tables, the
three structural ENUM types (ADR-0014), the ``entities_seq`` /
``interactions_seq`` sequences, and the ``processor_state`` cursor table
(ADR-0007).

Like ``test_migrations.py`` these run the real migration against a real
Postgres and inspect the resulting schema via information_schema / pg_catalog.
They assert what the migration produces, not how — the test does not care
whether the DDL is one ``op.execute`` or twenty.
"""

from __future__ import annotations

import psycopg

# The shared `migrated_dsn` fixture (tests/conftest.py) runs the migrate CLI to
# head, so it already includes this revision once it lands in versions/.


# --- helpers -----------------------------------------------------------------


def _table_exists(dsn: str, table: str) -> bool:
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = %s",
            (table,),
        ).fetchone()
    return row is not None


def _column_udt(dsn: str, table: str, column: str) -> str | None:
    """The user-defined type name (``udt_name``) of a column — this is how an
    ENUM-typed column is distinguished from a plain ``text`` column."""
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT udt_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s AND column_name = %s",
            (table, column),
        ).fetchone()
    return row[0] if row else None


def _columns(dsn: str, table: str) -> dict[str, dict[str, object]]:
    """Map column-name -> {data_type, is_nullable}."""
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            "SELECT column_name, data_type, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s",
            (table,),
        ).fetchall()
    return {name: {"data_type": dt, "is_nullable": nn} for name, dt, nn in rows}


def _pk_columns(dsn: str, table: str) -> list[str]:
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            """
            SELECT a.attname
            FROM pg_constraint c
            JOIN pg_class t ON t.oid = c.conrelid
            JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = ANY(c.conkey)
            WHERE c.contype = 'p' AND t.relname = %s
            ORDER BY array_position(c.conkey, a.attnum)
            """,
            (table,),
        ).fetchall()
    return [r[0] for r in rows]


def _enum_labels(dsn: str, type_name: str) -> list[str]:
    """Ordered ENUM labels for a Postgres type, or [] if the type is absent."""
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            """
            SELECT e.enumlabel
            FROM pg_type t
            JOIN pg_enum e ON e.enumtypid = t.oid
            WHERE t.typname = %s
            ORDER BY e.enumsortorder
            """,
            (type_name,),
        ).fetchall()
    return [r[0] for r in rows]


def _unique_column_sets(dsn: str, table: str) -> set[tuple[str, ...]]:
    """All UNIQUE / PK constraint column-tuples on *table*."""
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            """
            SELECT array_agg(a.attname ORDER BY array_position(c.conkey, a.attnum))
            FROM pg_constraint c
            JOIN pg_class t ON t.oid = c.conrelid
            JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = ANY(c.conkey)
            WHERE c.contype IN ('p', 'u') AND t.relname = %s
            GROUP BY c.oid
            """,
            (table,),
        ).fetchall()
    return {tuple(cols) for (cols,) in rows}


# --- tracer bullet -----------------------------------------------------------


def test_entities_table_exists_with_enum_kind(migrated_dsn: str) -> None:
    """The derived ``entities`` table exists and its ``kind`` is the
    ``entity_kind`` ENUM (ADR-0014), not plain text."""
    assert _table_exists(migrated_dsn, "entities")
    assert _column_udt(migrated_dsn, "entities", "kind") == "entity_kind"


# --- the five derived tables exist -------------------------------------------


def test_all_derived_tables_exist(migrated_dsn: str) -> None:
    for table in (
        "entities",
        "entity_spans",
        "interactions",
        "interaction_spans",
        "interaction_payloads",
        "processor_state",
    ):
        assert _table_exists(migrated_dsn, table), table


# --- structural ENUMs (ADR-0014) ---------------------------------------------


class TestStructuralEnums:
    def test_entity_kind_labels(self, migrated_dsn: str) -> None:
        assert _enum_labels(migrated_dsn, "entity_kind") == [
            "user",
            "client",
            "agent",
            "tool",
            "llm",
            "service",
        ]

    def test_entity_span_role_labels(self, migrated_dsn: str) -> None:
        assert _enum_labels(migrated_dsn, "entity_span_role") == [
            "discovered_via",
            "identified_via",
        ]

    def test_interaction_span_role_labels(self, migrated_dsn: str) -> None:
        assert _enum_labels(migrated_dsn, "interaction_span_role") == [
            "anchor",
            "info",
            "connector",
        ]

    def test_structural_role_columns_are_enum_typed(self, migrated_dsn: str) -> None:
        assert _column_udt(migrated_dsn, "entity_spans", "role") == "entity_span_role"
        assert (
            _column_udt(migrated_dsn, "interaction_spans", "role")
            == "interaction_span_role"
        )

    def test_enum_rejects_out_of_set_value(self, migrated_dsn: str) -> None:
        """An out-of-set entity kind is a processor bug; the ENUM rejects it at
        write (the whole point of ADR-0014)."""
        with psycopg.connect(migrated_dsn) as conn:
            try:
                conn.execute(
                    "INSERT INTO entities "
                    "(id, kind, natural_key, display_name, detected_from, original_seq) "
                    "VALUES ('e1', 'gremlin', 'k1', 'n', 'd', 1)"
                )
            except psycopg.errors.InvalidTextRepresentation:
                pass
            else:
                raise AssertionError("ENUM accepted an out-of-set value")


# --- content_kind stays TEXT (ADR-0014) --------------------------------------


def test_content_kind_is_text_not_enum(migrated_dsn: str) -> None:
    """content_kind churns as the payload classifier matures; it is TEXT, not
    an ENUM (ADR-0014)."""
    assert _columns(migrated_dsn, "interaction_payloads")["content_kind"][
        "data_type"
    ] == "text"


def test_content_kind_accepts_arbitrary_value(migrated_dsn: str) -> None:
    """A new content kind is a code change, not a migration — so an unknown
    value writes fine."""
    with psycopg.connect(migrated_dsn) as conn:
        conn.execute(
            "INSERT INTO interaction_payloads "
            "(content_hash, content_kind, content, byte_size) "
            "VALUES ('h1', 'some_future_kind', '{}'::jsonb, 2)"
        )
        row = conn.execute(
            "SELECT content_kind FROM interaction_payloads WHERE content_hash='h1'"
        ).fetchone()
    assert row == ("some_future_kind",)


# --- entities: cross-trace stable, no trace_id (decisions memory) ------------


class TestEntities:
    def test_no_trace_id_column(self, migrated_dsn: str) -> None:
        assert "trace_id" not in _columns(migrated_dsn, "entities")

    def test_natural_key_is_unique(self, migrated_dsn: str) -> None:
        assert ("natural_key",) in _unique_column_sets(migrated_dsn, "entities")

    def test_keeps_seq_and_original_seq(self, migrated_dsn: str) -> None:
        cols = _columns(migrated_dsn, "entities")
        assert "seq" in cols and "original_seq" in cols

    def test_no_retracted_at(self, migrated_dsn: str) -> None:
        assert "retracted_at" not in _columns(migrated_dsn, "entities")


# --- interactions: single row (ADR-0013) -------------------------------------


class TestInteractions:
    def test_is_single_row_with_both_payload_hashes(self, migrated_dsn: str) -> None:
        cols = _columns(migrated_dsn, "interactions")
        assert "request_payload_hash" in cols
        assert "response_payload_hash" in cols

    def test_no_direction_column(self, migrated_dsn: str) -> None:
        """ADR-0013: no (id, direction) two-leg model."""
        assert "direction" not in _columns(migrated_dsn, "interactions")

    def test_id_is_sole_primary_key(self, migrated_dsn: str) -> None:
        assert _pk_columns(migrated_dsn, "interactions") == ["id"]

    def test_keeps_trace_id_seq_original_seq(self, migrated_dsn: str) -> None:
        cols = _columns(migrated_dsn, "interactions")
        for c in ("trace_id", "seq", "original_seq"):
            assert c in cols, c

    def test_drops_prototype_only_columns(self, migrated_dsn: str) -> None:
        cols = _columns(migrated_dsn, "interactions")
        for c in ("anchor_rule", "primary_anchor_span_id", "retracted_at"):
            assert c not in cols, c


# --- span-attachment tables --------------------------------------------------


class TestSpanAttachmentTables:
    def test_interaction_spans_unique_trace_span(self, migrated_dsn: str) -> None:
        """ADR-0011 §3: a span belongs to at most one interaction."""
        assert ("trace_id", "span_id") in _unique_column_sets(
            migrated_dsn, "interaction_spans"
        )

    def test_interaction_spans_keeps_trace_id(self, migrated_dsn: str) -> None:
        assert "trace_id" in _columns(migrated_dsn, "interaction_spans")

    def test_entity_spans_keeps_trace_id(self, migrated_dsn: str) -> None:
        assert "trace_id" in _columns(migrated_dsn, "entity_spans")


# --- sequences (ADR-0007 cursorable streams) ---------------------------------


class TestSequences:
    def test_both_sequences_exist(self, migrated_dsn: str) -> None:
        for name in ("entities_seq", "interactions_seq"):
            with psycopg.connect(migrated_dsn) as conn:
                row = conn.execute(
                    "SELECT 1 FROM information_schema.sequences "
                    "WHERE sequence_schema = 'public' AND sequence_name = %s",
                    (name,),
                ).fetchone()
            assert row is not None, name

    def test_entities_seq_allocates_monotonically(self, migrated_dsn: str) -> None:
        """A writer that omits ``seq`` relies on the DEFAULT and must still get
        strictly-increasing values — that is what makes ``seq`` a usable
        cursor (ADR-0007)."""
        with psycopg.connect(migrated_dsn) as conn:
            for i in range(3):
                conn.execute(
                    "INSERT INTO entities "
                    "(id, kind, natural_key, display_name, detected_from, original_seq) "
                    "VALUES (%s, 'agent', %s, 'n', 'd', 1)",
                    (f"e{i}", f"k{i}"),
                )
            seqs = [
                r[0]
                for r in conn.execute("SELECT seq FROM entities ORDER BY id").fetchall()
            ]
        assert seqs == sorted(seqs)
        assert len(set(seqs)) == 3


# --- processor_state cursor (ADR-0007) ---------------------------------------


class TestProcessorState:
    def test_processor_name_is_primary_key(self, migrated_dsn: str) -> None:
        assert _pk_columns(migrated_dsn, "processor_state") == ["processor_name"]

    def test_has_cursor_and_timestamp_columns(self, migrated_dsn: str) -> None:
        cols = _columns(migrated_dsn, "processor_state")
        assert "last_processed_seq" in cols
        assert cols["updated_at"]["data_type"] == "timestamp with time zone"


# --- downgrade round-trip ----------------------------------------------------


def test_downgrade_then_upgrade_round_trips(pg_dsn: str, monkeypatch) -> None:
    """upgrade -> downgrade -> upgrade leaves the derived tables and ENUM types
    present again (the migration ships a real downgrade, unlike the baseline).

    Drives Alembic through the project's own config builder (the same one the
    migrate CLI uses) so script_location / DSN translation resolve identically.
    Downgrade stops one revision below this one — the spans side stays put.
    """
    from alembic import command

    from data_governance.db.migrate import _alembic_config

    monkeypatch.setenv("DATABASE_URL", pg_dsn)
    cfg = _alembic_config()

    # Up to head (includes 0004).
    command.upgrade(cfg, "head")
    assert _table_exists(pg_dsn, "interactions")

    # Down one revision: derived schema gone, spans side intact.
    command.downgrade(cfg, "0003_rejected_spans")
    assert not _table_exists(pg_dsn, "interactions")
    assert not _table_exists(pg_dsn, "entities")
    assert _enum_labels(pg_dsn, "entity_kind") == []  # ENUM type dropped too
    assert _table_exists(pg_dsn, "spans")  # spans side untouched

    # Back up to head: everything returns.
    command.upgrade(cfg, "head")
    assert _table_exists(pg_dsn, "interactions")
    assert _enum_labels(pg_dsn, "entity_kind") != []
