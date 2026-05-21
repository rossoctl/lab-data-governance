"""End-to-end tests for issue #12 via the OTLP harness from #5.

Each test sends real OTLP spans through ``P-otel-receiver`` and then
exercises ``get_spans`` against the resulting rows. These tests anchor
the listing-root / window / counts behaviour against the actual ingest
path (verbatim writes, listing-root-fallback over real orphan rows, etc.)
rather than the direct-INSERT fixtures the in-process retrieval suite
uses.
"""

from __future__ import annotations

import datetime as dt
import secrets

import psycopg

from data_governance import retrieval
from tests.harness.fixtures import OtlpHarness

UTC = dt.timezone.utc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _new_trace_id() -> str:
    """Hex trace_id matching what the OTLP SDK accepts (16 bytes / 32 hex)."""
    return secrets.token_hex(16)


def _set_started_at(dsn: str, *, trace_id: str, span_id: str, ts: dt.datetime) -> None:
    """Move a real span's started_at to a controlled value for window assertions.

    The OTLP-supplied ``started_at`` is derived from the SDK's clock at
    ``send_*_span`` time and lands within microseconds of "now"; tests
    that assert window inclusion / exclusion need stable relative
    positions. The receiver's other invariants are unaffected by a
    direct-SQL bump of ``started_at``.
    """
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "UPDATE spans SET started_at = %s WHERE trace_id = %s AND span_id = %s",
            (ts, trace_id, span_id),
        )
        conn.commit()


def _set_parent_id(
    dsn: str, *, trace_id: str, span_id: str, parent_id: str | None
) -> None:
    """Stamp ``parent_id`` on a real OTLP-ingested row.

    The harness's ``send_*_span`` exporter starts each span as a real
    root or with a synthetic remote parent; when a test needs a specific
    parent shape (e.g. a child of a known span_id, or an orphan
    referencing a deliberately-missing parent), we stamp it post-insert.
    """
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "UPDATE spans SET parent_id = %s WHERE trace_id = %s AND span_id = %s",
            (parent_id, trace_id, span_id),
        )
        conn.commit()


def _set_error(dsn: str, *, trace_id: str, span_id: str, error: bool) -> None:
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "UPDATE spans SET error = %s WHERE trace_id = %s AND span_id = %s",
            (error, trace_id, span_id),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Listing-root cases — real root, orphan fallback, real-root preference
# ---------------------------------------------------------------------------


def test_e2e_listing_root_real_root_when_present(
    otlp_harness: OtlpHarness,
) -> None:
    """A trace with a real root (``parent_id IS NULL``) — listing root is the real root."""
    trace_id = _new_trace_id()
    _, root_span_id = otlp_harness.client.send_grpc_span(
        name="real-root", trace_id=trace_id
    )
    # Add a child so listing-root selection has more than one row to choose.
    _, child_span_id = otlp_harness.client.send_grpc_span(
        name="child", trace_id=trace_id
    )
    _set_parent_id(
        otlp_harness.dsn,
        trace_id=trace_id,
        span_id=child_span_id,
        parent_id=root_span_id,
    )

    # Both sends create real roots; clear parent_id on the chosen root just to be sure.
    _set_parent_id(
        otlp_harness.dsn,
        trace_id=trace_id,
        span_id=root_span_id,
        parent_id=None,
    )

    result = retrieval.get_spans(root_only=True, trace_id=trace_id)
    assert len(result.spans) == 1
    assert result.spans[0].span_id == root_span_id
    assert result.spans[0].parent_id is None


def test_e2e_listing_root_orphan_fallback(otlp_harness: OtlpHarness) -> None:
    """A trace with only orphans — listing root is the earliest orphan."""
    trace_id = _new_trace_id()
    _, late_span_id = otlp_harness.client.send_grpc_span(
        name="late-orphan", trace_id=trace_id
    )
    _, early_span_id = otlp_harness.client.send_grpc_span(
        name="early-orphan", trace_id=trace_id
    )

    # Make both rows orphans by pointing them at a span that does not exist.
    _set_parent_id(
        otlp_harness.dsn,
        trace_id=trace_id,
        span_id=late_span_id,
        parent_id="missing-parent-late",
    )
    _set_parent_id(
        otlp_harness.dsn,
        trace_id=trace_id,
        span_id=early_span_id,
        parent_id="missing-parent-early",
    )
    # Place "early" before "late" on the trace clock.
    _set_started_at(
        otlp_harness.dsn,
        trace_id=trace_id,
        span_id=early_span_id,
        ts=dt.datetime(2026, 5, 1, 10, tzinfo=UTC),
    )
    _set_started_at(
        otlp_harness.dsn,
        trace_id=trace_id,
        span_id=late_span_id,
        ts=dt.datetime(2026, 5, 1, 14, tzinfo=UTC),
    )

    result = retrieval.get_spans(root_only=True, trace_id=trace_id)
    assert len(result.spans) == 1
    assert result.spans[0].span_id == early_span_id
    assert result.spans[0].parent_id == "missing-parent-early"


# ---------------------------------------------------------------------------
# Window filtering and in_time_window flag
# ---------------------------------------------------------------------------


def test_e2e_window_filter_excludes_out_of_window_span(
    otlp_harness: OtlpHarness,
) -> None:
    trace_id = _new_trace_id()
    _, in_span_id = otlp_harness.client.send_grpc_span(
        name="in-window", trace_id=trace_id
    )
    _, out_span_id = otlp_harness.client.send_grpc_span(
        name="out-of-window", trace_id=trace_id
    )
    _set_started_at(
        otlp_harness.dsn,
        trace_id=trace_id,
        span_id=in_span_id,
        ts=dt.datetime(2026, 5, 1, 12, tzinfo=UTC),
    )
    _set_started_at(
        otlp_harness.dsn,
        trace_id=trace_id,
        span_id=out_span_id,
        ts=dt.datetime(2026, 5, 1, 20, tzinfo=UTC),
    )

    result = retrieval.get_spans(
        trace_id=trace_id,
        time_from=dt.datetime(2026, 5, 1, 10, tzinfo=UTC),
        time_to=dt.datetime(2026, 5, 1, 14, tzinfo=UTC),
    )
    span_ids = [s.span_id for s in result.spans]
    assert span_ids == [in_span_id]
    assert result.spans[0].in_time_window is True


# ---------------------------------------------------------------------------
# Per-trace counts ridealong
# ---------------------------------------------------------------------------


def test_e2e_counts_total_in_window_error(otlp_harness: OtlpHarness) -> None:
    trace_id = _new_trace_id()
    _, root_id = otlp_harness.client.send_grpc_span(
        name="root", trace_id=trace_id
    )
    _, child_in_id = otlp_harness.client.send_grpc_span(
        name="child-in", trace_id=trace_id
    )
    _, child_out_id = otlp_harness.client.send_grpc_span(
        name="child-out", trace_id=trace_id
    )

    _set_parent_id(otlp_harness.dsn, trace_id=trace_id, span_id=root_id, parent_id=None)
    _set_parent_id(
        otlp_harness.dsn, trace_id=trace_id, span_id=child_in_id, parent_id=root_id
    )
    _set_parent_id(
        otlp_harness.dsn, trace_id=trace_id, span_id=child_out_id, parent_id=root_id
    )

    _set_started_at(
        otlp_harness.dsn,
        trace_id=trace_id,
        span_id=root_id,
        ts=dt.datetime(2026, 5, 1, 12, tzinfo=UTC),
    )
    _set_started_at(
        otlp_harness.dsn,
        trace_id=trace_id,
        span_id=child_in_id,
        ts=dt.datetime(2026, 5, 1, 13, tzinfo=UTC),
    )
    _set_started_at(
        otlp_harness.dsn,
        trace_id=trace_id,
        span_id=child_out_id,
        ts=dt.datetime(2026, 5, 1, 20, tzinfo=UTC),
    )
    _set_error(otlp_harness.dsn, trace_id=trace_id, span_id=child_in_id, error=True)

    result = retrieval.get_spans(
        root_only=True,
        time_from=dt.datetime(2026, 5, 1, 10, tzinfo=UTC),
        time_to=dt.datetime(2026, 5, 1, 14, tzinfo=UTC),
    )

    assert result.counts is not None
    counts = result.counts[trace_id]
    assert counts.total == 3
    assert counts.in_window == 2
    assert counts.error_count == 1


# ---------------------------------------------------------------------------
# Sort: root_only orders by listing-root started_at desc
# ---------------------------------------------------------------------------


def test_e2e_root_only_sort_started_at_desc(otlp_harness: OtlpHarness) -> None:
    trace_a = _new_trace_id()
    trace_b = _new_trace_id()
    _, root_a = otlp_harness.client.send_grpc_span(name="rA", trace_id=trace_a)
    _, root_b = otlp_harness.client.send_grpc_span(name="rB", trace_id=trace_b)
    _set_parent_id(otlp_harness.dsn, trace_id=trace_a, span_id=root_a, parent_id=None)
    _set_parent_id(otlp_harness.dsn, trace_id=trace_b, span_id=root_b, parent_id=None)
    _set_started_at(
        otlp_harness.dsn, trace_id=trace_a, span_id=root_a,
        ts=dt.datetime(2026, 5, 1, 10, tzinfo=UTC),
    )
    _set_started_at(
        otlp_harness.dsn, trace_id=trace_b, span_id=root_b,
        ts=dt.datetime(2026, 5, 1, 14, tzinfo=UTC),
    )

    result = retrieval.get_spans(root_only=True)
    span_ids = [s.span_id for s in result.spans]
    # Newest listing root first.
    assert span_ids.index(root_b) < span_ids.index(root_a)
