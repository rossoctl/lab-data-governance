"""Tests for GET /graph — issue #58 acceptance criteria.

In-process server + httpx, mirroring the GET /spans tests. Asserts the JSON
body shape tracks the retrieval dataclasses and that query params pass through
to get_entities / get_edges.
"""

from __future__ import annotations

import httpx
import psycopg

from data_governance.api import SpansApiServer


def _base_url(server: SpansApiServer) -> str:
    return f"http://127.0.0.1:{server.port}"


def _seed_entity(conn, entity_id, *, service=None, kind="UNKNOWN", sub=None) -> None:
    conn.execute(
        "INSERT INTO entities (entity_id, service_name, semantic_kind, sub_kind, "
        "display_name, first_seen_at, last_seen_at) "
        "VALUES (%s, %s, %s, %s, %s, now(), now())",
        (entity_id, service, kind, sub, f"{service} · {kind}"),
    )
    conn.commit()


def _seed_span(conn, *, trace_id, span_id, parent_id="p") -> None:
    conn.execute(
        "INSERT INTO spans (trace_id, span_id, parent_id, kind, name, started_at, "
        "attributes, seq, arrival_seq, observed_at) "
        "VALUES (%s, %s, %s, 'INTERNAL', 'n', now(), '{}'::jsonb, "
        "nextval('spans_seq'), currval('spans_seq'), now())",
        (trace_id, span_id, parent_id),
    )
    conn.commit()


def _seed_edge(conn, *, trace_id, span_id, parent_id, to_entity, from_entity=None, kind=None) -> None:
    conn.execute(
        "INSERT INTO edges (trace_id, span_id, parent_id, to_entity, from_entity, edge_kind) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (trace_id, span_id, parent_id, to_entity, from_entity, kind),
    )
    conn.commit()


def test_graph_returns_entities_and_edges(api_server, configured_db):
    with psycopg.connect(configured_db) as conn:
        _seed_entity(conn, "from_e", service="svc", kind="CHAIN")
        _seed_entity(conn, "to_e", service="svc", kind="LLM", sub="gpt-4")
        _seed_span(conn, trace_id="t", span_id="c", parent_id="p")
        _seed_edge(
            conn, trace_id="t", span_id="c", parent_id="p",
            to_entity="to_e", from_entity="from_e", kind="CHAIN_LLM",
        )

    resp = httpx.get(f"{_base_url(api_server)}/graph")
    assert resp.status_code == 200
    data = resp.json()
    assert set(data) == {"entities", "edges"}

    assert len(data["edges"]) == 1
    edge = data["edges"][0]
    assert edge["from_entity"] == "from_e"
    assert edge["to_entity"] == "to_e"
    assert edge["edge_kind"] == "CHAIN_LLM"
    # Timing surfaced via the spans JOIN, serialized as ISO-8601.
    assert edge["started_at"] is not None

    ids = {e["entity_id"] for e in data["entities"]}
    assert ids == {"from_e", "to_e"}


def test_graph_empty_is_well_formed(api_server, configured_db):
    resp = httpx.get(f"{_base_url(api_server)}/graph")
    assert resp.status_code == 200
    assert resp.json() == {"entities": [], "edges": []}


def test_graph_trace_id_filters_edges(api_server, configured_db):
    with psycopg.connect(configured_db) as conn:
        _seed_entity(conn, "e", service="svc", kind="UNKNOWN")
        for tr in ("t1", "t2"):
            _seed_span(conn, trace_id=tr, span_id="c", parent_id="p")
            _seed_edge(conn, trace_id=tr, span_id="c", parent_id="p", to_entity="e")

    resp = httpx.get(f"{_base_url(api_server)}/graph", params={"trace_id": "t1"})
    assert resp.status_code == 200
    edges = resp.json()["edges"]
    assert {e["trace_id"] for e in edges} == {"t1"}


def test_graph_semantic_kind_filters_entities(api_server, configured_db):
    with psycopg.connect(configured_db) as conn:
        _seed_entity(conn, "a_llm", service="svc", kind="LLM")
        _seed_entity(conn, "b_tool", service="svc", kind="TOOL")

    resp = httpx.get(f"{_base_url(api_server)}/graph", params={"semantic_kind": "LLM"})
    assert resp.status_code == 200
    ids = {e["entity_id"] for e in resp.json()["entities"]}
    assert ids == {"a_llm"}


def test_graph_bad_param_returns_400(api_server, configured_db):
    resp = httpx.get(f"{_base_url(api_server)}/graph", params={"limit": "501"})
    assert resp.status_code == 400
    assert "error" in resp.json()
