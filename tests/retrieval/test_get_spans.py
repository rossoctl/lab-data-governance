"""Tests for get_spans — issue #4 acceptance criteria."""

from __future__ import annotations

import pytest

from data_governance.retrieval import GetSpansResult, get_spans


# ---------------------------------------------------------------------------
# Acceptance criteria
# ---------------------------------------------------------------------------


def test_no_filter_returns_all_asc(configured_db, insert_span):
    insert_span(trace_id="aaa", span_id="s1", name="first")
    insert_span(trace_id="bbb", span_id="s2", name="second")
    insert_span(trace_id="ccc", span_id="s3", name="third")

    result = get_spans()
    assert isinstance(result, GetSpansResult)
    names = [s.name for s in result.spans]
    assert names == ["first", "second", "third"]


def test_order_desc_flips_order(configured_db, insert_span):
    insert_span(trace_id="aaa", span_id="s1", name="first")
    insert_span(trace_id="bbb", span_id="s2", name="second")

    result = get_spans(order="desc")
    names = [s.name for s in result.spans]
    assert names == ["second", "first"]


def test_trace_id_filter(configured_db, insert_span):
    insert_span(trace_id="trace-A", span_id="s1", name="span-A1")
    insert_span(trace_id="trace-A", span_id="s2", name="span-A2")
    insert_span(trace_id="trace-B", span_id="s3", name="span-B1")

    result = get_spans(trace_id="trace-A")
    names = [s.name for s in result.spans]
    assert sorted(names) == ["span-A1", "span-A2"]
    assert all(s.trace_id == "trace-A" for s in result.spans)


def test_trace_id_and_span_id_returns_single_row(configured_db, insert_span):
    insert_span(trace_id="trace-A", span_id="s1", name="span-A1")
    insert_span(trace_id="trace-A", span_id="s2", name="span-A2")

    result = get_spans(trace_id="trace-A", span_id="s1")
    assert len(result.spans) == 1
    assert result.spans[0].span_id == "s1"


def test_trace_id_and_span_id_no_match_returns_empty(configured_db, insert_span):
    insert_span(trace_id="trace-A", span_id="s1", name="span-A1")

    result = get_spans(trace_id="trace-A", span_id="missing")
    assert result.spans == []


def test_limit_above_500_raises(configured_db):
    with pytest.raises(ValueError, match="500"):
        get_spans(limit=501)


def test_invalid_order_raises(configured_db):
    with pytest.raises(ValueError, match="order"):
        get_spans(order="invalid")  # type: ignore[arg-type]


def test_limit_defaults_to_50(configured_db, insert_span):
    for i in range(60):
        insert_span(trace_id="t", span_id=f"s{i}", name=f"span-{i}")

    result = get_spans()
    assert len(result.spans) == 50


def test_counts_always_none(configured_db, insert_span):
    insert_span(trace_id="t", span_id="s1", name="x")
    result = get_spans()
    assert result.counts is None


def test_cursor_pagination_asc(configured_db, insert_span):
    for i in range(5):
        insert_span(trace_id="t", span_id=f"s{i}", name=f"span-{i}")

    page1 = get_spans(limit=3)
    assert len(page1.spans) == 3
    last_seq = page1.spans[-1].seq

    page2 = get_spans(cursor=last_seq, limit=3)
    assert len(page2.spans) == 2
    assert all(s.seq > last_seq for s in page2.spans)


def test_cursor_pagination_desc(configured_db, insert_span):
    for i in range(5):
        insert_span(trace_id="t", span_id=f"s{i}", name=f"span-{i}")

    page1 = get_spans(limit=3, order="desc")
    assert len(page1.spans) == 3
    first_seq = page1.spans[-1].seq

    page2 = get_spans(cursor=first_seq, limit=3, order="desc")
    assert len(page2.spans) == 2
    assert all(s.seq < first_seq for s in page2.spans)
