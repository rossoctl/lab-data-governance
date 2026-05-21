"""Tests for get_spans listing-roots / time-window slice — issue #12.

Each test maps to one acceptance-criterion checkbox on the issue. The file
is deliberately separated from ``test_get_spans.py`` (issue #4 acceptance
suite) so future readers can map test → issue without grepping.
"""

from __future__ import annotations

import datetime as dt

import pytest

from data_governance.retrieval import (
    GetSpansResult,
    Span,
    TraceCounts,
    get_spans,
)

UTC = dt.timezone.utc


# ---------------------------------------------------------------------------
# Parameter validation (no DB rows required)
# ---------------------------------------------------------------------------


def test_naive_time_from_raises(configured_db):
    naive = dt.datetime(2026, 5, 1, 12, 0, 0)  # no tzinfo
    with pytest.raises(ValueError, match="naive"):
        get_spans(time_from=naive)


def test_naive_time_to_raises(configured_db):
    naive = dt.datetime(2026, 5, 1, 12, 0, 0)
    with pytest.raises(ValueError, match="naive"):
        get_spans(time_to=naive)


def test_parent_id_without_trace_id_raises(configured_db):
    with pytest.raises(ValueError, match="parent_id"):
        get_spans(parent_id="p1")


def test_root_only_with_parent_id_raises(configured_db):
    with pytest.raises(ValueError, match="root_only"):
        get_spans(root_only=True, trace_id="t", parent_id="p")


def test_root_only_with_span_id_raises(configured_db):
    with pytest.raises(ValueError, match="root_only"):
        get_spans(root_only=True, trace_id="t", span_id="s")


def test_root_only_with_trace_id_is_compatible(configured_db, insert_span):
    # Should not raise.
    insert_span(trace_id="t", span_id="s", name="x")
    result = get_spans(root_only=True, trace_id="t")
    assert isinstance(result, GetSpansResult)


# ---------------------------------------------------------------------------
# Time window filtering — non-root_only path
# ---------------------------------------------------------------------------


def _ts(year=2026, month=5, day=1, hour=12, minute=0, second=0) -> dt.datetime:
    return dt.datetime(year, month, day, hour, minute, second, tzinfo=UTC)


def test_time_from_filters_started_at(configured_db, insert_span):
    insert_span(
        trace_id="t", span_id="s1", name="early", started_at=_ts(hour=10)
    )
    insert_span(
        trace_id="t", span_id="s2", name="late", started_at=_ts(hour=14)
    )
    result = get_spans(time_from=_ts(hour=12))
    names = [s.name for s in result.spans]
    assert names == ["late"]


def test_time_to_filters_started_at(configured_db, insert_span):
    insert_span(
        trace_id="t", span_id="s1", name="early", started_at=_ts(hour=10)
    )
    insert_span(
        trace_id="t", span_id="s2", name="late", started_at=_ts(hour=14)
    )
    result = get_spans(time_to=_ts(hour=12))
    names = [s.name for s in result.spans]
    assert names == ["early"]


def test_time_from_and_time_to_filter(configured_db, insert_span):
    insert_span(
        trace_id="t", span_id="s1", name="early", started_at=_ts(hour=8)
    )
    insert_span(
        trace_id="t", span_id="s2", name="middle", started_at=_ts(hour=12)
    )
    insert_span(
        trace_id="t", span_id="s3", name="late", started_at=_ts(hour=18)
    )
    result = get_spans(time_from=_ts(hour=10), time_to=_ts(hour=14))
    names = [s.name for s in result.spans]
    assert names == ["middle"]


def test_int_nanoseconds_accepted(configured_db, insert_span):
    insert_span(
        trace_id="t", span_id="s1", name="early", started_at=_ts(hour=10)
    )
    insert_span(
        trace_id="t", span_id="s2", name="late", started_at=_ts(hour=14)
    )
    twelve_utc_ns = int(_ts(hour=12).timestamp() * 1_000_000_000)
    result = get_spans(time_from=twelve_utc_ns)
    names = [s.name for s in result.spans]
    assert names == ["late"]


def test_unbounded_when_both_none(configured_db, insert_span):
    insert_span(trace_id="t", span_id="s1", name="a", started_at=_ts(hour=8))
    insert_span(trace_id="t", span_id="s2", name="b", started_at=_ts(hour=22))

    result = get_spans()
    names = sorted(s.name for s in result.spans)
    assert names == ["a", "b"]


# ---------------------------------------------------------------------------
# in_time_window field on Span
# ---------------------------------------------------------------------------


def test_in_time_window_default_true_when_no_window(configured_db, insert_span):
    insert_span(trace_id="t", span_id="s1", name="x")
    result = get_spans()
    assert all(s.in_time_window is True for s in result.spans)


def test_in_time_window_true_for_in_window_span(configured_db, insert_span):
    insert_span(
        trace_id="t", span_id="s1", name="x", started_at=_ts(hour=12)
    )
    result = get_spans(time_from=_ts(hour=10), time_to=_ts(hour=14))
    assert len(result.spans) == 1
    assert result.spans[0].in_time_window is True


# ---------------------------------------------------------------------------
# root_only=True — listing roots
# ---------------------------------------------------------------------------


def test_root_only_returns_real_root_per_trace(configured_db, insert_span):
    # Trace A: real root + child
    insert_span(
        trace_id="A", span_id="A-root", name="A-root",
        parent_id=None, started_at=_ts(hour=10),
    )
    insert_span(
        trace_id="A", span_id="A-child", name="A-child",
        parent_id="A-root", started_at=_ts(hour=11),
    )
    # Trace B: real root + child
    insert_span(
        trace_id="B", span_id="B-root", name="B-root",
        parent_id=None, started_at=_ts(hour=15),
    )
    insert_span(
        trace_id="B", span_id="B-child", name="B-child",
        parent_id="B-root", started_at=_ts(hour=16),
    )

    result = get_spans(root_only=True)
    span_ids = sorted(s.span_id for s in result.spans)
    assert span_ids == ["A-root", "B-root"]


def test_listing_root_orphan_fallback(configured_db, insert_span):
    # Trace has only orphans — earliest one is the listing root.
    insert_span(
        trace_id="T", span_id="orphan-late", name="orphan-late",
        parent_id="missing-1", started_at=_ts(hour=14),
    )
    insert_span(
        trace_id="T", span_id="orphan-early", name="orphan-early",
        parent_id="missing-2", started_at=_ts(hour=10),
    )

    result = get_spans(root_only=True)
    assert len(result.spans) == 1
    assert result.spans[0].span_id == "orphan-early"
    assert result.spans[0].parent_id == "missing-2"  # carries parent_id


def test_listing_root_real_root_preferred_over_orphan(
    configured_db, insert_span
):
    # Trace has both a real root (parent_id IS NULL) and an orphan.
    insert_span(
        trace_id="T", span_id="orphan", name="orphan",
        parent_id="missing", started_at=_ts(hour=9),  # earlier than real root
    )
    insert_span(
        trace_id="T", span_id="real", name="real",
        parent_id=None, started_at=_ts(hour=12),
    )

    result = get_spans(root_only=True)
    assert len(result.spans) == 1
    assert result.spans[0].span_id == "real"
    assert result.spans[0].parent_id is None


def test_root_only_with_trace_id_returns_listing_root(
    configured_db, insert_span
):
    insert_span(
        trace_id="A", span_id="rA", name="rA",
        parent_id=None, started_at=_ts(hour=10),
    )
    insert_span(
        trace_id="B", span_id="rB", name="rB",
        parent_id=None, started_at=_ts(hour=11),
    )

    result = get_spans(root_only=True, trace_id="A")
    span_ids = [s.span_id for s in result.spans]
    assert span_ids == ["rA"]


def test_root_only_with_trace_id_ignores_window(configured_db, insert_span):
    """root_only=True + trace_id always answers, even outside window."""
    insert_span(
        trace_id="A", span_id="rA", name="rA",
        parent_id=None, started_at=_ts(hour=10),
    )

    # Window that excludes the real root's started_at entirely.
    result = get_spans(
        root_only=True,
        trace_id="A",
        time_from=_ts(hour=20),
        time_to=_ts(hour=22),
    )
    assert [s.span_id for s in result.spans] == ["rA"]


def test_root_only_with_trace_id_empty_when_no_spans(
    configured_db, insert_span
):
    # Insert a span for a different trace; query asks about a missing one.
    insert_span(
        trace_id="A", span_id="rA", name="rA",
        parent_id=None, started_at=_ts(hour=10),
    )
    result = get_spans(root_only=True, trace_id="missing")
    assert result.spans == []


def test_root_only_in_time_window_false_when_root_out_of_window(
    configured_db, insert_span,
):
    """A trace's listing root may be out of window if a child is in window."""
    # Real root is before the window…
    insert_span(
        trace_id="T", span_id="root", name="root",
        parent_id=None, started_at=_ts(hour=8),
    )
    # …but a child lands inside the window.
    insert_span(
        trace_id="T", span_id="child", name="child",
        parent_id="root", started_at=_ts(hour=12),
    )

    result = get_spans(
        root_only=True, time_from=_ts(hour=10), time_to=_ts(hour=14)
    )
    assert len(result.spans) == 1
    assert result.spans[0].span_id == "root"
    assert result.spans[0].in_time_window is False


def test_root_only_sort_listing_root_started_at_desc(
    configured_db, insert_span
):
    insert_span(
        trace_id="A", span_id="rA", name="rA",
        parent_id=None, started_at=_ts(hour=10),
    )
    insert_span(
        trace_id="B", span_id="rB", name="rB",
        parent_id=None, started_at=_ts(hour=14),
    )
    insert_span(
        trace_id="C", span_id="rC", name="rC",
        parent_id=None, started_at=_ts(hour=12),
    )

    result = get_spans(root_only=True)
    span_ids = [s.span_id for s in result.spans]
    assert span_ids == ["rB", "rC", "rA"]


# ---------------------------------------------------------------------------
# Per-trace counts
# ---------------------------------------------------------------------------


def test_counts_none_when_root_only_false(configured_db, insert_span):
    insert_span(trace_id="t", span_id="s", name="x")
    result = get_spans()
    assert result.counts is None


def test_counts_total_in_window_error(configured_db, insert_span):
    # Trace T:
    #   real root, in-window
    #   child, in-window, error=True
    #   child2, OUT of window
    insert_span(
        trace_id="T", span_id="r", name="r",
        parent_id=None, started_at=_ts(hour=12),
    )
    insert_span(
        trace_id="T", span_id="c1", name="c1",
        parent_id="r", started_at=_ts(hour=13), error=True,
    )
    insert_span(
        trace_id="T", span_id="c2", name="c2",
        parent_id="r", started_at=_ts(hour=20),
    )

    result = get_spans(
        root_only=True, time_from=_ts(hour=10), time_to=_ts(hour=14)
    )
    assert result.counts is not None
    counts = result.counts["T"]
    assert isinstance(counts, TraceCounts)
    assert counts.total == 3
    assert counts.in_window == 2  # r + c1
    assert counts.error_count == 1


def test_counts_in_window_excludes_out_of_window_listing_root(
    configured_db, insert_span,
):
    """If listing root itself is out of window, it doesn't count in in_window."""
    # Real root out of window.
    insert_span(
        trace_id="T", span_id="root", name="root",
        parent_id=None, started_at=_ts(hour=8),
    )
    # Child in-window.
    insert_span(
        trace_id="T", span_id="child", name="child",
        parent_id="root", started_at=_ts(hour=12),
    )

    result = get_spans(
        root_only=True, time_from=_ts(hour=10), time_to=_ts(hour=14)
    )
    counts = result.counts["T"]
    assert counts.total == 2
    assert counts.in_window == 1  # only the child; root excluded


def test_counts_error_counts_only_true(configured_db, insert_span):
    insert_span(
        trace_id="T", span_id="r", name="r",
        parent_id=None, started_at=_ts(hour=12), error=False,
    )
    insert_span(
        trace_id="T", span_id="c1", name="c1",
        parent_id="r", started_at=_ts(hour=12), error=None,
    )
    insert_span(
        trace_id="T", span_id="c2", name="c2",
        parent_id="r", started_at=_ts(hour=12), error=True,
    )
    insert_span(
        trace_id="T", span_id="c3", name="c3",
        parent_id="r", started_at=_ts(hour=12), error=True,
    )

    result = get_spans(root_only=True)
    counts = result.counts["T"]
    assert counts.error_count == 2


# ---------------------------------------------------------------------------
# Sort defaults — bare cursor stream
# ---------------------------------------------------------------------------


def test_bare_stream_orders_by_seq_asc(configured_db, insert_span):
    insert_span(trace_id="t", span_id="s1", name="first")
    insert_span(trace_id="t", span_id="s2", name="second")
    result = get_spans()
    seqs = [s.seq for s in result.spans]
    assert seqs == sorted(seqs)


def test_order_desc_flips_seq_order(configured_db, insert_span):
    insert_span(trace_id="t", span_id="s1", name="first")
    insert_span(trace_id="t", span_id="s2", name="second")
    result = get_spans(order="desc")
    seqs = [s.seq for s in result.spans]
    assert seqs == sorted(seqs, reverse=True)


# ---------------------------------------------------------------------------
# Snapshot invariant: listing-root SELECT and counts share one
# REPEATABLE READ transaction
# ---------------------------------------------------------------------------


def test_counts_snapshot_isolated_from_concurrent_writes(
    configured_db, insert_span, monkeypatch
):
    """Concurrent commits between root-select and counts must not bleed in.

    Setup: trace ``T`` with 2 non-error spans. Between the listing-root
    SELECT and ``_compute_counts_for_traces``, a separate connection
    flips both rows to ``error=true`` and commits. Under REPEATABLE READ
    on a shared transaction, the counts query must NOT see those writes,
    so ``error_count == 0``. If the two queries ran in independent
    transactions (the bug), ``error_count`` would be ``2``.
    """
    import psycopg

    from data_governance import retrieval as retrieval_mod

    insert_span(trace_id="T", span_id="s1", name="a", error=False)
    insert_span(trace_id="T", span_id="s2", name="b", error=False)

    original = retrieval_mod._compute_counts_for_traces

    def racing(tx, trace_ids, *, time_from, time_to):
        with psycopg.connect(configured_db) as conn:
            conn.execute("UPDATE spans SET error = TRUE WHERE trace_id = 'T'")
            conn.commit()
        return original(tx, trace_ids, time_from=time_from, time_to=time_to)

    monkeypatch.setattr(retrieval_mod, "_compute_counts_for_traces", racing)

    result = get_spans(root_only=True)
    counts = result.counts["T"]
    assert counts.total == 2
    assert counts.error_count == 0, (
        "REPEATABLE READ snapshot was not shared between the listing-root "
        "SELECT and the counts query — concurrent UPDATE leaked in."
    )
