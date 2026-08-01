"""HTTP tests for the two lineage-graph endpoints (ADR-0028 D14).

Covers what only the wire can show: the envelope's exact key set (the field names ARE
the contract — the handler is ``dataclasses.asdict`` over the retrieval result), the
``direction`` query parameter's validation, and the status codes. The traversal
semantics live in ``tests/retrieval/test_lineage_walk.py`` (pure) and
``tests/retrieval/test_lineage_graph.py`` (DB-backed); these do not re-assert them.

Two conventions pinned here deliberately:

- an unknown trace or entity is **200 with an empty result**, never 404 — the
  collection-read convention shared with the other trace sub-resources;
- a missing or unrecognized ``direction`` is **400**, never defaulted. The two
  directions answer different questions, so guessing would answer one the caller did
  not ask.
"""

from __future__ import annotations

import json

import psycopg
import pytest

from data_governance.api import SpansApiServer

pytest.importorskip("httpx")
import httpx  # noqa: E402

_TID = "trace-lgraph-api-1"
_USER = "ent-api-user"
_AGENT = "ent-api-agent"
_TOOL = "ent-api-tool"
_IX_UA = "ix-api-ua"
_IX_AT = "ix-api-at"


def _base_url(server: SpansApiServer) -> str:
    return f"http://127.0.0.1:{server.port}"


def _graph_url(server: SpansApiServer, tid: str, eid: str, direction: str) -> str:
    return (
        f"{_base_url(server)}/api/traces/{tid}/entities/{eid}"
        f"/data-lineage-graph?direction={direction}"
    )


@pytest.fixture()
def seeded(configured_db: str) -> str:
    """``user -> agent -> tool``, request legs derived; enough for a two-hop walk."""
    with psycopg.connect(configured_db) as conn:
        for eid, kind, nk, seq in (
            (_USER, "user", "user", 1),
            (_AGENT, "agent", "advisor", 2),
            (_TOOL, "tool", "search", 3),
        ):
            conn.execute(
                "INSERT INTO entities (id, kind, natural_key, display_name, "
                "detected_from, original_seq) "
                "VALUES (%s, %s, %s, %s, 'span-attr', %s)",
                (eid, kind, nk, nk.title(), seq),
            )
            conn.execute(
                "INSERT INTO spans (trace_id, span_id, parent_id, kind, name, "
                "started_at, attributes, seq, arrival_seq, observed_at) "
                "VALUES (%s, %s, NULL, 'INTERNAL', 'n', now(), '{}'::jsonb, "
                "nextval('spans_seq'), currval('spans_seq'), now())",
                (_TID, f"s-{eid}"),
            )
            conn.execute(
                "INSERT INTO entity_spans (trace_id, span_id, entity_id, role) "
                "VALUES (%s, %s, %s, 'discovered_via')",
                (_TID, f"s-{eid}", eid),
            )
        for ix_id, caller, callee, seq in (
            (_IX_UA, _USER, _AGENT, 1),
            (_IX_AT, _AGENT, _TOOL, 2),
        ):
            conn.execute(
                "INSERT INTO interactions (id, trace_id, caller_entity_id, "
                "callee_entity_id, summary) VALUES (%s, %s, %s, %s, 'call')",
                (ix_id, _TID, caller, callee),
            )
            conn.execute(
                "INSERT INTO interaction_legs (interaction_id, leg_type, "
                "occurred_at, payload_hash, error) "
                "VALUES (%s, 'request', now(), 'h', false)",
                (ix_id,),
            )
            conn.execute(
                "INSERT INTO lineage_metadata (interaction_id, leg_type, "
                "data_sources, source_transformations, entities, payload_hash, seq) "
                "VALUES (%s, 'request', %s, %s::jsonb, %s, 'h', %s)",
                (ix_id, ["user"], json.dumps({"user": []}), ["user"], seq),
            )
        conn.commit()
    return _TID


# ---------------------------------------------------------------------------
# data-lineage-graph
# ---------------------------------------------------------------------------


def test_graph_wire_shape(seeded: str, api_server: SpansApiServer) -> None:
    """The envelope's key set is the contract."""
    resp = httpx.get(_graph_url(api_server, seeded, _USER, "fanout"))
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {
        "direction",
        "seed_entity_id",
        "entities",
        "legs",
        "state",
        "pending_frontier",
        "truncated",
        "status",
        "stopped_at_seq",
    }
    assert body["direction"] == "fanout"
    assert body["seed_entity_id"] == _USER
    assert set(body["entities"][0]) == {
        "id",
        "natural_key",
        "kind",
        "display_name",
        "hops",
    }
    assert set(body["legs"][0]) == {
        "interaction_id",
        "leg_type",
        "from_entity_id",
        "to_entity_id",
        "seq",
    }


def test_fanout_serializes_the_reached_chain(
    seeded: str, api_server: SpansApiServer
) -> None:
    """Two hops downstream of the user, with the distance on each entity."""
    body = httpx.get(_graph_url(api_server, seeded, _USER, "fanout")).json()
    assert body["state"] == "derived"
    assert {e["id"]: e["hops"] for e in body["entities"]} == {_AGENT: 1, _TOOL: 2}
    assert body["truncated"] is False


def test_fanin_serializes_the_provenance_chain(
    seeded: str, api_server: SpansApiServer
) -> None:
    body = httpx.get(_graph_url(api_server, seeded, _TOOL, "fanin")).json()
    assert {e["id"]: e["hops"] for e in body["entities"]} == {_AGENT: 1, _USER: 2}


def test_missing_direction_is_400(seeded: str, api_server: SpansApiServer) -> None:
    """Required, and deliberately not defaulted to either value."""
    url = (
        f"{_base_url(api_server)}/api/traces/{seeded}/entities/{_USER}"
        f"/data-lineage-graph"
    )
    resp = httpx.get(url)
    assert resp.status_code == 400
    assert "direction" in resp.json()["error"]


def test_unknown_direction_is_400(seeded: str, api_server: SpansApiServer) -> None:
    resp = httpx.get(_graph_url(api_server, seeded, _USER, "sideways"))
    assert resp.status_code == 400
    # The message names what IS accepted, not only what was rejected.
    assert "fanin" in resp.json()["error"]
    assert "fanout" in resp.json()["error"]


def test_unknown_trace_is_empty_not_404(
    seeded: str, api_server: SpansApiServer
) -> None:
    resp = httpx.get(_graph_url(api_server, "no-such-trace", _USER, "fanout"))
    assert resp.status_code == 200
    assert resp.json()["entities"] == []


def test_unknown_entity_is_empty_not_404(
    seeded: str, api_server: SpansApiServer
) -> None:
    resp = httpx.get(_graph_url(api_server, seeded, "ent-nobody", "fanout"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["entities"] == []
    assert body["state"] == "no-adjacent"


def test_pending_state_is_served_not_collapsed_to_empty(
    seeded: str, api_server: SpansApiServer, configured_db: str
) -> None:
    """With nothing derived, the wire says ``pending`` and names the frontier.

    A client must be able to tell "not computed yet" from "nothing flowed" — the
    same three-valued discipline as D6's coverage status.
    """
    with psycopg.connect(configured_db) as conn:
        conn.execute("DELETE FROM lineage_metadata")
        conn.commit()
    body = httpx.get(_graph_url(api_server, seeded, _USER, "fanout")).json()
    assert body["entities"] == []
    assert body["state"] == "pending"
    assert body["pending_frontier"] == [_AGENT]


def test_graph_empty_when_lineage_table_absent(
    seeded: str, api_server: SpansApiServer, configured_db: str
) -> None:
    """A mid-upgrade DB serves an empty result, not a 500."""
    with psycopg.connect(configured_db) as conn:
        conn.execute("DROP TABLE IF EXISTS lineage_metadata CASCADE")
        conn.commit()
    resp = httpx.get(_graph_url(api_server, seeded, _USER, "fanout"))
    assert resp.status_code == 200
    assert resp.json()["entities"] == []


# ---------------------------------------------------------------------------
# data-lineage-summary
# ---------------------------------------------------------------------------


def test_summary_wire_shape(seeded: str, api_server: SpansApiServer) -> None:
    resp = httpx.get(
        f"{_base_url(api_server)}/api/traces/{seeded}/data-lineage-summary"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"sources", "destinations", "status", "stopped_at_seq"}
    # sources are natural keys from the triple; destinations are entity rows —
    # different grains, deliberately not two views of one list.
    assert body["sources"] == ["user"]
    assert [d["id"] for d in body["destinations"]] == [_TOOL]


def test_summary_status_is_null_when_unknown(
    seeded: str, api_server: SpansApiServer
) -> None:
    """Unknown coverage is ``null`` on the wire, never defaulted to ``complete``."""
    body = httpx.get(
        f"{_base_url(api_server)}/api/traces/{seeded}/data-lineage-summary"
    ).json()
    assert body["status"] is None
    assert body["stopped_at_seq"] is None


def test_summary_unknown_trace_is_empty_not_404(
    seeded: str, api_server: SpansApiServer
) -> None:
    resp = httpx.get(
        f"{_base_url(api_server)}/api/traces/no-such-trace/data-lineage-summary"
    )
    assert resp.status_code == 200
    assert resp.json() == {
        "sources": [],
        "destinations": [],
        "status": None,
        "stopped_at_seq": None,
    }
