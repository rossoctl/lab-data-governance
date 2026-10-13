"""Regression tests for the default UI policy on standalone CONNECT traces."""

from __future__ import annotations

import datetime as dt
import json

import pytest

from data_governance.retrieval import get_spans


UTC = dt.timezone.utc
_COMPONENT = {"authbridge.component": "lineage-telemetry"}


def _connect_request(exchange_id: str) -> dict[str, str]:
    return {
        "http.method": "CONNECT",
        "url.scheme": "tcp",
        "lineage.role": "request",
        "lineage.direction": "outbound",
        "lineage.parent.source": "none",
        "lineage.exchange.id": exchange_id,
    }


def _connect_response(exchange_id: str) -> dict[str, str]:
    return {
        "lineage.role": "response",
        "lineage.direction": "outbound",
        "lineage.exchange.id": exchange_id,
        "lineage.outcome": "abandoned",
    }


def _insert(
    conn,
    *,
    trace_id: str,
    span_id: str,
    parent_id: str | None,
    started_at: dt.datetime,
    attributes: dict[str, str],
    resource_attributes: dict[str, str] | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO spans (
            trace_id, span_id, parent_id, kind, name, service_name,
            started_at, attributes, resource_attributes,
            seq, arrival_seq, observed_at
        ) VALUES (
            %s, %s, %s, 'CLIENT', %s, 'authbridge',
            %s, %s::jsonb, %s::jsonb,
            nextval('spans_seq'), currval('spans_seq'), now()
        )
        """,
        (
            trace_id,
            span_id,
            parent_id,
            "client http" if parent_id is None else "client http response",
            started_at,
            json.dumps(attributes),
            json.dumps(resource_attributes) if resource_attributes else None,
        ),
    )
    conn.commit()


def _insert_connect_pair(
    conn,
    *,
    trace_id: str,
    request_id: str,
    started_at: dt.datetime,
) -> None:
    _insert(
        conn,
        trace_id=trace_id,
        span_id=request_id,
        parent_id=None,
        started_at=started_at,
        attributes=_connect_request(request_id),
        resource_attributes=_COMPONENT,
    )
    _insert(
        conn,
        trace_id=trace_id,
        span_id=f"{request_id}-response",
        parent_id=request_id,
        started_at=started_at + dt.timedelta(microseconds=1),
        attributes=_connect_response(request_id),
        resource_attributes=_COMPONENT,
    )


def test_connect_only_trace_is_hidden_without_deleting_direct_access(raw_conn):
    _insert_connect_pair(
        raw_conn,
        trace_id="connect-only",
        request_id="connect-request",
        started_at=dt.datetime(2026, 9, 29, 10, tzinfo=UTC),
    )

    visible = get_spans(root_only=True, hide_connect_only_traces=False)
    hidden = get_spans(root_only=True, hide_connect_only_traces=True)
    direct = get_spans(
        root_only=True,
        trace_id="connect-only",
        hide_connect_only_traces=True,
    )
    stored = get_spans(trace_id="connect-only")

    assert [span.trace_id for span in visible.spans] == ["connect-only"]
    assert hidden.spans == []
    assert [span.trace_id for span in direct.spans] == ["connect-only"]
    assert {span.span_id for span in stored.spans} == {
        "connect-request",
        "connect-request-response",
    }


def test_connect_root_with_another_exchange_remains_visible(raw_conn):
    _insert_connect_pair(
        raw_conn,
        trace_id="mixed",
        request_id="connect-request",
        started_at=dt.datetime(2026, 9, 29, 10, tzinfo=UTC),
    )
    _insert(
        raw_conn,
        trace_id="mixed",
        span_id="application-span",
        parent_id="connect-request",
        started_at=dt.datetime(2026, 9, 29, 10, 0, 1, tzinfo=UTC),
        attributes={"lineage.exchange.id": "application-exchange"},
    )

    result = get_spans(root_only=True, hide_connect_only_traces=True)

    assert [span.trace_id for span in result.spans] == ["mixed"]


def test_connect_method_without_full_signature_remains_visible(raw_conn):
    _insert(
        raw_conn,
        trace_id="generic-connect",
        span_id="request",
        parent_id=None,
        started_at=dt.datetime(2026, 9, 29, 10, tzinfo=UTC),
        attributes={
            "http.method": "CONNECT",
            "lineage.exchange.id": "request",
        },
    )

    result = get_spans(root_only=True, hide_connect_only_traces=True)

    assert [span.trace_id for span in result.spans] == ["generic-connect"]


@pytest.mark.parametrize(
    "missing_attribute",
    [
        "http.method",
        "url.scheme",
        "lineage.role",
        "lineage.direction",
        "lineage.parent.source",
        "lineage.exchange.id",
    ],
)
def test_each_connect_signature_attribute_is_required(
    raw_conn,
    missing_attribute: str,
):
    attributes = _connect_request("request")
    del attributes[missing_attribute]
    _insert(
        raw_conn,
        trace_id=f"missing-{missing_attribute}",
        span_id="request",
        parent_id=None,
        started_at=dt.datetime(2026, 9, 29, 10, tzinfo=UTC),
        attributes=attributes,
        resource_attributes=_COMPONENT,
    )

    result = get_spans(root_only=True, hide_connect_only_traces=True)

    assert [span.trace_id for span in result.spans] == [
        f"missing-{missing_attribute}"
    ]


def test_connect_signature_requires_authbridge_component(raw_conn):
    _insert(
        raw_conn,
        trace_id="missing-component",
        span_id="request",
        parent_id=None,
        started_at=dt.datetime(2026, 9, 29, 10, tzinfo=UTC),
        attributes=_connect_request("request"),
    )

    result = get_spans(root_only=True, hide_connect_only_traces=True)

    assert [span.trace_id for span in result.spans] == ["missing-component"]


def test_connect_exchange_id_must_identify_root_span(raw_conn):
    _insert(
        raw_conn,
        trace_id="wrong-exchange-id",
        span_id="request",
        parent_id=None,
        started_at=dt.datetime(2026, 9, 29, 10, tzinfo=UTC),
        attributes=_connect_request("different-span"),
        resource_attributes=_COMPONENT,
    )

    result = get_spans(root_only=True, hide_connect_only_traces=True)

    assert [span.trace_id for span in result.spans] == ["wrong-exchange-id"]


def test_orphan_connect_listing_root_remains_visible(raw_conn):
    _insert(
        raw_conn,
        trace_id="orphan-connect",
        span_id="request",
        parent_id="missing-parent",
        started_at=dt.datetime(2026, 9, 29, 10, tzinfo=UTC),
        attributes=_connect_request("request"),
        resource_attributes=_COMPONENT,
    )

    result = get_spans(root_only=True, hide_connect_only_traces=True)

    assert [span.trace_id for span in result.spans] == ["orphan-connect"]


def test_connect_only_filter_does_not_depend_on_span_count(raw_conn):
    started_at = dt.datetime(2026, 9, 29, 10, tzinfo=UTC)
    _insert_connect_pair(
        raw_conn,
        trace_id="three-span-connect",
        request_id="request",
        started_at=started_at,
    )
    _insert(
        raw_conn,
        trace_id="three-span-connect",
        span_id="additional-evidence",
        parent_id="request",
        started_at=started_at + dt.timedelta(microseconds=2),
        attributes={"lineage.exchange.id": "request"},
        resource_attributes=_COMPONENT,
    )

    result = get_spans(root_only=True, hide_connect_only_traces=True)

    assert result.spans == []


def test_connect_span_nested_in_application_trace_remains_visible(raw_conn):
    started_at = dt.datetime(2026, 9, 29, 10, tzinfo=UTC)
    _insert(
        raw_conn,
        trace_id="application",
        span_id="application-root",
        parent_id=None,
        started_at=started_at,
        attributes={},
    )
    _insert(
        raw_conn,
        trace_id="application",
        span_id="nested-connect",
        parent_id="application-root",
        started_at=started_at + dt.timedelta(seconds=1),
        attributes=_connect_request("nested-connect"),
        resource_attributes=_COMPONENT,
    )

    result = get_spans(root_only=True, hide_connect_only_traces=True)

    assert [span.trace_id for span in result.spans] == ["application"]


def test_hidden_rows_do_not_consume_page_capacity(raw_conn):
    old = dt.datetime(2026, 9, 29, 10, tzinfo=UTC)
    _insert(
        raw_conn,
        trace_id="old-visible",
        span_id="old-root",
        parent_id=None,
        started_at=old,
        attributes={},
    )
    _insert_connect_pair(
        raw_conn,
        trace_id="middle-hidden",
        request_id="connect-request",
        started_at=old + dt.timedelta(minutes=1),
    )
    _insert(
        raw_conn,
        trace_id="new-visible",
        span_id="new-root",
        parent_id=None,
        started_at=old + dt.timedelta(minutes=2),
        attributes={},
    )

    result = get_spans(
        root_only=True,
        hide_connect_only_traces=True,
        limit=2,
    )

    assert [span.trace_id for span in result.spans] == [
        "new-visible",
        "old-visible",
    ]
    assert set(result.counts or {}) == {"new-visible", "old-visible"}
