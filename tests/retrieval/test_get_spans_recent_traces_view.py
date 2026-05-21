"""Tests for get_spans returning service_name on Span rows — issue #13.

The recent-traces UI listing displays each row's ``service_name`` (per
PROJECT.md §8 / docs/ui-design.md §8). The retrieval-library ``Span``
dataclass therefore carries ``service_name`` so the UI can render it
without a second fetch.

Slice #4/#12 surfaced ``service_name`` on the schema but never read it
out via ``get_spans``; this slice (issue #13) closes that gap.
"""

from __future__ import annotations

import datetime as dt

from data_governance.retrieval import Span, get_spans


UTC = dt.timezone.utc


def _ts(hour: int = 12) -> dt.datetime:
    return dt.datetime(2026, 5, 1, hour, 0, 0, tzinfo=UTC)


def test_span_dataclass_has_service_name_field():
    """``Span`` must carry ``service_name`` so the listing row can render it."""
    fields = {f for f in Span.__dataclass_fields__}
    assert "service_name" in fields


def test_get_spans_returns_service_name_on_listing_root(
    configured_db, insert_span_with_service_name
):
    """With ``root_only=True`` the returned listing root carries its
    ``service_name`` (resource-level OTLP attribute, populated at write time
    by the receiver)."""
    insert_span_with_service_name(
        trace_id="T",
        span_id="r",
        name="real-root",
        parent_id=None,
        started_at=_ts(),
        service_name="auth-service",
    )

    result = get_spans(root_only=True)
    assert len(result.spans) == 1
    assert result.spans[0].service_name == "auth-service"


def test_get_spans_returns_service_name_on_non_root_query(
    configured_db, insert_span_with_service_name
):
    """The bare-cursor query path also surfaces ``service_name``."""
    insert_span_with_service_name(
        trace_id="T",
        span_id="s",
        name="span",
        started_at=_ts(),
        service_name="billing-service",
    )

    result = get_spans()
    assert len(result.spans) == 1
    assert result.spans[0].service_name == "billing-service"


def test_get_spans_returns_none_when_service_name_absent(
    configured_db, insert_span
):
    """``service_name`` is NULLable; ``Span.service_name`` is ``None`` for
    rows where the OTLP resource carried no ``service.name`` attribute."""
    insert_span(trace_id="T", span_id="s", name="span", started_at=_ts())

    result = get_spans()
    assert len(result.spans) == 1
    assert result.spans[0].service_name is None
