"""Tests for get_spans subtree-expansion path — issue #14.

This file maps each acceptance criterion onto a focused test. The
parameter-compatibility raise (`parent_id` requires `trace_id`) is
already enforced by issue #12 and lives in
``test_get_spans_listing_roots.py``; this file covers the third query
path's behaviour: direct children of a parent within a trace, sorted
``seq asc`` (chronological by arrival), cursor-paginated by ``seq``
(sort and cursor axes aligned — see #30 for why), plus the
trace-tree-render columns the UI needs (``kind``, ``error``,
``status_message``, ``events``, ``links``) projected onto returned
``Span`` rows.
"""

from __future__ import annotations

import datetime as dt

from data_governance.retrieval import GetSpansResult, get_spans

UTC = dt.timezone.utc


def _ts(year=2026, month=5, day=1, hour=12, minute=0, second=0) -> dt.datetime:
    return dt.datetime(year, month, day, hour, minute, second, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Span field projections — required by the trace-tree row + detail panel
# ---------------------------------------------------------------------------


def test_span_carries_kind(configured_db, insert_span):
    insert_span(
        trace_id="T", span_id="s1", name="x", kind="SERVER",
    )
    result = get_spans(trace_id="T")
    assert len(result.spans) == 1
    assert result.spans[0].kind == "SERVER"


def test_span_carries_error_and_status_message(configured_db, insert_span):
    insert_span(
        trace_id="T", span_id="s1", name="x",
        error=True, status_message="boom",
    )
    result = get_spans(trace_id="T")
    assert result.spans[0].error is True
    assert result.spans[0].status_message == "boom"


def test_span_error_defaults_to_none(configured_db, insert_span):
    insert_span(trace_id="T", span_id="s1", name="x")
    result = get_spans(trace_id="T")
    # OTLP UNSET → null.
    assert result.spans[0].error is None
    assert result.spans[0].status_message is None


def test_span_carries_events_and_links(configured_db, insert_span):
    insert_span(
        trace_id="T", span_id="s1", name="x",
        events=[{"name": "exception", "time_unix_nano": 1, "attributes": {}}],
        links=[{"trace_id": "T2", "span_id": "x2", "attributes": {}}],
    )
    result = get_spans(trace_id="T")
    assert result.spans[0].events == [
        {"name": "exception", "time_unix_nano": 1, "attributes": {}}
    ]
    assert result.spans[0].links == [
        {"trace_id": "T2", "span_id": "x2", "attributes": {}}
    ]


def test_span_events_and_links_default_to_none(configured_db, insert_span):
    """PROJECT.md §3 events/links: NULL is canonical for "no events / no links"."""
    insert_span(trace_id="T", span_id="s1", name="x")
    result = get_spans(trace_id="T")
    assert result.spans[0].events is None
    assert result.spans[0].links is None


# ---------------------------------------------------------------------------
# parent_id query path — direct children of P within T
# ---------------------------------------------------------------------------


def test_parent_id_returns_only_direct_children(configured_db, insert_span):
    # Root + direct children + a grandchild that should NOT appear.
    insert_span(
        trace_id="T", span_id="root", name="root",
        parent_id=None, started_at=_ts(hour=10),
    )
    insert_span(
        trace_id="T", span_id="c1", name="c1",
        parent_id="root", started_at=_ts(hour=11),
    )
    insert_span(
        trace_id="T", span_id="c2", name="c2",
        parent_id="root", started_at=_ts(hour=12),
    )
    insert_span(
        trace_id="T", span_id="gc", name="grandchild",
        parent_id="c1", started_at=_ts(hour=13),
    )

    result = get_spans(trace_id="T", parent_id="root")
    assert isinstance(result, GetSpansResult)
    span_ids = sorted(s.span_id for s in result.spans)
    assert span_ids == ["c1", "c2"]


def test_parent_id_excludes_other_traces(configured_db, insert_span):
    insert_span(
        trace_id="T1", span_id="r", name="r",
        parent_id=None, started_at=_ts(hour=10),
    )
    insert_span(
        trace_id="T1", span_id="c", name="c",
        parent_id="r", started_at=_ts(hour=11),
    )
    # Different trace, same span_id values — parent_id filter must scope to T1.
    insert_span(
        trace_id="T2", span_id="r", name="r",
        parent_id=None, started_at=_ts(hour=10),
    )
    insert_span(
        trace_id="T2", span_id="c", name="c-other-trace",
        parent_id="r", started_at=_ts(hour=11),
    )

    result = get_spans(trace_id="T1", parent_id="r")
    assert [s.trace_id for s in result.spans] == ["T1"]
    assert [s.span_id for s in result.spans] == ["c"]


def test_parent_id_no_children_returns_empty(configured_db, insert_span):
    insert_span(
        trace_id="T", span_id="leaf", name="leaf",
        parent_id=None, started_at=_ts(hour=10),
    )
    result = get_spans(trace_id="T", parent_id="leaf")
    assert result.spans == []


def test_parent_id_counts_is_none(configured_db, insert_span):
    """counts is the root_only-only ridealong; subtree path returns None."""
    insert_span(
        trace_id="T", span_id="root", name="r",
        parent_id=None, started_at=_ts(hour=10),
    )
    insert_span(
        trace_id="T", span_id="c", name="c",
        parent_id="root", started_at=_ts(hour=11),
    )
    result = get_spans(trace_id="T", parent_id="root")
    assert result.counts is None


# ---------------------------------------------------------------------------
# Sort order — seq ASC (chronological by arrival at the receiver)
# ---------------------------------------------------------------------------


def test_parent_id_sorts_seq_asc(configured_db, insert_span):
    """PROJECT.md §6 Path 3: parent_id set → seq asc.

    Sort axis matches cursor axis on this path. ``seq`` is allocated
    on arrival at the receiver, so for a single parent's children it
    is chronological-by-arrival — the property a top-down trace-tree
    view actually wants. ``started_at`` (the producer's clock) can
    skew under async exporters, retries, and span buffering, so it
    is *not* the sort axis here; sorting on it while cursoring on
    ``seq`` would create the same skip-class hazard documented for
    the listing-roots path in #30.

    Insert children with ``started_at`` deliberately *uncorrelated*
    with insertion order (i.e. with ``seq``). The result must reflect
    insertion / ``seq`` order, not ``started_at`` order.
    """
    insert_span(
        trace_id="T", span_id="root", name="root",
        parent_id=None, started_at=_ts(hour=10),
    )
    # Insertion order: c1, c2, c3 (so seq increases c1 < c2 < c3).
    # started_at order: c2 (h=11) < c3 (h=12) < c1 (h=14) — different.
    insert_span(
        trace_id="T", span_id="c1", name="c1",
        parent_id="root", started_at=_ts(hour=14),
    )
    insert_span(
        trace_id="T", span_id="c2", name="c2",
        parent_id="root", started_at=_ts(hour=11),
    )
    insert_span(
        trace_id="T", span_id="c3", name="c3",
        parent_id="root", started_at=_ts(hour=12),
    )

    result = get_spans(trace_id="T", parent_id="root")
    assert [s.span_id for s in result.spans] == ["c1", "c2", "c3"]


# ---------------------------------------------------------------------------
# Cursor pagination — wide-fanout parent (loops, batch jobs)
# ---------------------------------------------------------------------------


def test_parent_id_paginates_wide_fanout_parent(
    configured_db, insert_span,
):
    """Parent with > limit children paginates across multiple calls,
    even when ``seq`` and ``started_at`` are deliberately
    uncorrelated.

    Regression guard for the cursor/sort-axis-mismatch bug class
    documented in #30 (and surfaced for this path in PR #31's
    review). Children are inserted in **reverse** ``started_at``
    order — so as ``seq`` increases (insertion order), ``started_at``
    decreases. Under the old broken contract (sort by
    ``started_at asc`` while cursoring on ``seq``), once page 1's
    cursor advanced to ``max(seq)`` of that page, every remaining
    child — which all had *lower* ``started_at`` and *higher*
    ``seq`` than seen so far — would be silently skipped. Under the
    fixed contract (sort *and* cursor on ``seq asc``), the walk
    yields all children exactly once.

    The mental regression check: revert the sort axis to
    ``started_at`` while keeping the ``seq`` cursor, and this test
    must fail (the union it sees will be a strict subset of the
    seeded children).
    """
    insert_span(
        trace_id="T", span_id="p", name="p",
        parent_id=None, started_at=_ts(hour=8),
    )
    n = 25
    for i in range(n):
        # Insertion order i = 0..24 (so seq is monotonically
        # increasing). started_at is set in *reverse* — c00 has the
        # latest started_at, c24 the earliest. seq and started_at
        # are now strongly anticorrelated.
        insert_span(
            trace_id="T", span_id=f"c{i:02d}", name=f"c{i:02d}",
            parent_id="p",
            started_at=_ts(hour=10) + dt.timedelta(seconds=(n - 1 - i)),
        )

    seen: list[str] = []
    cursor: int | None = None
    for _ in range(10):  # safety cap
        page = get_spans(
            trace_id="T", parent_id="p", cursor=cursor, limit=10,
        )
        if not page.spans:
            break
        seen.extend(s.span_id for s in page.spans)
        cursor = max(s.seq for s in page.spans)

    # Union across pages must equal the seeded set — no skips, no
    # duplicates. (If a future change reverts the subtree sort to
    # ``started_at asc`` while cursoring on ``seq``, this assertion
    # fails: the seq cursor would skip past low-seq children whose
    # started_at-sorted position is past the page boundary.)
    assert set(seen) == {f"c{i:02d}" for i in range(n)}
    assert len(seen) == n  # no duplicates
    # And on this path (axes aligned) the order is seq-asc, which is
    # insertion order:
    assert seen == [f"c{i:02d}" for i in range(n)]


def test_parent_id_cursor_excludes_already_seen_seq(
    configured_db, insert_span,
):
    insert_span(
        trace_id="T", span_id="p", name="p",
        parent_id=None, started_at=_ts(hour=8),
    )
    for i in range(5):
        insert_span(
            trace_id="T", span_id=f"c{i}", name=f"c{i}",
            parent_id="p",
            started_at=_ts(hour=10) + dt.timedelta(seconds=i),
        )

    page1 = get_spans(trace_id="T", parent_id="p", limit=3)
    assert len(page1.spans) == 3
    last_seq = max(s.seq for s in page1.spans)

    page2 = get_spans(trace_id="T", parent_id="p", cursor=last_seq, limit=3)
    assert all(s.seq > last_seq for s in page2.spans)
    assert {s.span_id for s in page1.spans}.isdisjoint(
        {s.span_id for s in page2.spans}
    )
