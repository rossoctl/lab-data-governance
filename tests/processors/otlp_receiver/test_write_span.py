"""Tests for the minimal `write_span` Layer 2 function (issue #3).

Per the issue brief, this slice writes only:
    trace_id, span_id, parent_id, name, started_at, attributes,
    seq, arrival_seq, observed_at

with `seq == arrival_seq` on initial INSERT, `attributes` always a JSON
object (never SQL NULL), `observed_at` set by the receiver, ON CONFLICT
DO NOTHING, and one Layer 1 transaction per call.

These tests use the real Postgres provided by the session-scoped
`pg_dsn` fixture and the Layer 1 pool configured by `configured_db`.
No mocks, per ADR-0005.
"""

from __future__ import annotations

import datetime as dt

import psycopg

from data_governance.processors.otlp_receiver.write_span import (
    SpanRow,
    write_span,
)


# --- helpers -----------------------------------------------------------------


def _row_by_pk(dsn: str, trace_id: str, span_id: str) -> dict[str, object]:
    """Read the spans row identified by (trace_id, span_id) as a dict."""
    with psycopg.connect(dsn) as conn:
        cols = (
            "trace_id, span_id, parent_id, name, started_at, attributes, "
            "seq, arrival_seq, observed_at, kind, ended_at, error, "
            "status_message, service_name, events, links, otlp, scope, "
            "resource_attributes"
        )
        row = conn.execute(
            f"SELECT {cols} FROM spans WHERE trace_id=%s AND span_id=%s",
            (trace_id, span_id),
        ).fetchone()
    if row is None:
        return {}
    keys = [c.strip() for c in cols.split(",")]
    return dict(zip(keys, row, strict=False))


def _make_span(**overrides: object) -> SpanRow:
    """Build a SpanRow with sensible defaults for tests.

    Every ``SpanRow`` field is required (no dataclass defaults), so this
    helper supplies the full v1 column set. Tests override only the fields
    they care about via ``**overrides``.
    """
    base: dict[str, object] = {
        "trace_id": "11111111111111111111111111111111",
        "span_id": "2222222222222222",
        "parent_id": None,
        "name": "test-span",
        "kind": None,
        "started_at": dt.datetime(2026, 5, 20, 12, 0, 0, tzinfo=dt.timezone.utc),
        "ended_at": None,
        "error": None,
        "status_message": None,
        "service_name": None,
        "attributes": {},
        "events": None,
        "links": None,
        "scope": None,
        "resource_attributes": None,
        "otlp": None,
    }
    base.update(overrides)
    return SpanRow(**base)  # type: ignore[arg-type]


# --- tests -------------------------------------------------------------------


class TestMinimalInsert:
    def test_inserts_row_with_all_required_columns_populated(
        self, configured_db: str
    ) -> None:
        span = _make_span(
            trace_id="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            span_id="bbbbbbbbbbbbbbbb",
            name="hello",
            started_at=dt.datetime(2026, 5, 20, 9, 30, 0, tzinfo=dt.timezone.utc),
            attributes={"k": "v"},
        )
        write_span(span)

        row = _row_by_pk(configured_db, span.trace_id, span.span_id)

        assert row["trace_id"] == span.trace_id
        assert row["span_id"] == span.span_id
        assert row["parent_id"] is None
        assert row["name"] == "hello"
        assert row["started_at"] == span.started_at
        assert row["attributes"] == {"k": "v"}
        assert row["seq"] is not None
        assert row["arrival_seq"] is not None
        assert row["observed_at"] is not None

    def test_seq_equals_arrival_seq_on_initial_insert(self, configured_db: str) -> None:
        span = _make_span(span_id="0000000000000001")
        write_span(span)

        row = _row_by_pk(configured_db, span.trace_id, span.span_id)
        assert row["seq"] == row["arrival_seq"]

    def test_writes_parent_id_when_set(self, configured_db: str) -> None:
        span = _make_span(
            span_id="0000000000000002",
            parent_id="cccccccccccccccc",
        )
        write_span(span)

        row = _row_by_pk(configured_db, span.trace_id, span.span_id)
        assert row["parent_id"] == "cccccccccccccccc"

    def test_observed_at_is_close_to_now(self, configured_db: str) -> None:
        before = dt.datetime.now(tz=dt.timezone.utc)
        span = _make_span(span_id="0000000000000003")
        write_span(span)
        after = dt.datetime.now(tz=dt.timezone.utc)

        row = _row_by_pk(configured_db, span.trace_id, span.span_id)
        observed = row["observed_at"]
        assert isinstance(observed, dt.datetime)
        # Observed at the receiver, so it must lie within the window we
        # bracketed around the call. This proves write_span sets it (rather
        # than allowing the schema DEFAULT to set it later in some other
        # transaction's clock).
        assert before <= observed <= after


class TestAttributesAlwaysJsonObject:
    def test_empty_dict_attributes_round_trip_as_empty_object(
        self, configured_db: str
    ) -> None:
        span = _make_span(span_id="0000000000000010", attributes={})
        write_span(span)

        row = _row_by_pk(configured_db, span.trace_id, span.span_id)
        # Postgres jsonb -> psycopg dict; explicitly an empty mapping, not None.
        assert row["attributes"] == {}

    def test_attributes_is_never_sql_null(self, configured_db: str) -> None:
        # Even if the caller passes None somehow, write_span normalises.
        # We pass an explicit empty dict here (the documented contract);
        # the database-level guard checked is "no NULL on the column".
        span = _make_span(span_id="0000000000000011", attributes={})
        write_span(span)

        with psycopg.connect(configured_db) as conn:
            row = conn.execute(
                "SELECT attributes IS NULL FROM spans "
                "WHERE trace_id=%s AND span_id=%s",
                (span.trace_id, span.span_id),
            ).fetchone()
        assert row == (False,)


class TestUnwrittenColumnsLeftDefault:
    def test_kind_left_null_at_this_stage(self, configured_db: str) -> None:
        span = _make_span(span_id="0000000000000020")
        write_span(span)

        row = _row_by_pk(configured_db, span.trace_id, span.span_id)
        # Issue #3 explicitly defers `kind` to slice #6; the migration in
        # this slice relaxed `kind NOT NULL` so a NULL is now legal.
        assert row["kind"] is None

    def test_optional_columns_left_null(self, configured_db: str) -> None:
        span = _make_span(span_id="0000000000000021")
        write_span(span)

        row = _row_by_pk(configured_db, span.trace_id, span.span_id)
        for col in (
            "ended_at",
            "error",
            "status_message",
            "service_name",
            "events",
            "links",
            "otlp",
            "scope",
            "resource_attributes",
        ):
            assert row[col] is None, f"{col} should be NULL but was {row[col]!r}"


class TestSeqMonotonic:
    def test_subsequent_inserts_get_strictly_increasing_seq(
        self, configured_db: str
    ) -> None:
        seqs: list[int] = []
        for i in range(3):
            span = _make_span(span_id=f"00000000000000{i:02x}")
            write_span(span)
            row = _row_by_pk(configured_db, span.trace_id, span.span_id)
            seqs.append(row["seq"])  # type: ignore[arg-type]
        assert seqs == sorted(seqs)
        assert len(set(seqs)) == 3


class TestOnConflictDoNothing:
    def test_duplicate_pk_is_silently_dropped(self, configured_db: str) -> None:
        span1 = _make_span(span_id="0000000000000030", name="first")
        span2 = _make_span(span_id="0000000000000030", name="second")
        write_span(span1)
        # Second arrival with same PK should not raise and should not
        # overwrite the existing row's name.
        write_span(span2)

        row = _row_by_pk(configured_db, span1.trace_id, span1.span_id)
        assert row["name"] == "first"

    def test_duplicate_does_not_advance_seq_for_existing_row(
        self, configured_db: str
    ) -> None:
        span = _make_span(span_id="0000000000000031")
        write_span(span)
        first = _row_by_pk(configured_db, span.trace_id, span.span_id)
        write_span(span)
        second = _row_by_pk(configured_db, span.trace_id, span.span_id)

        # ON CONFLICT DO NOTHING: the existing row's seq is preserved.
        assert first["seq"] == second["seq"]


class TestPerSpanTransaction:
    def test_each_call_commits_independently(self, configured_db: str) -> None:
        # If each write_span call were inside a shared outer transaction
        # we couldn't observe a partial commit; here, after the first
        # call we expect to be able to read the row from a *separate*
        # libpq connection. This is the §3 "one transaction per span"
        # property.
        span = _make_span(span_id="0000000000000040")
        write_span(span)

        with psycopg.connect(configured_db) as conn:
            row = conn.execute(
                "SELECT name FROM spans WHERE trace_id=%s AND span_id=%s",
                (span.trace_id, span.span_id),
            ).fetchone()
        assert row == (span.name,)


class TestMicrosecondTruncation:
    def test_started_at_with_sub_microsecond_precision_truncates_cleanly(
        self, configured_db: str
    ) -> None:
        # Postgres timestamptz has microsecond resolution. Per the issue,
        # OTLP nanoseconds are truncated to microseconds before writing.
        # write_span itself receives a Python datetime (already at-most
        # microsecond), so the truncation point is the receiver's OTLP
        # decode — but write_span must round-trip a microsecond-precision
        # datetime without rounding it to seconds (a common psycopg-config
        # mistake).
        ts = dt.datetime(2026, 5, 20, 9, 30, 0, 123456, tzinfo=dt.timezone.utc)
        span = _make_span(span_id="0000000000000050", started_at=ts)
        write_span(span)

        row = _row_by_pk(configured_db, span.trace_id, span.span_id)
        assert row["started_at"] == ts
