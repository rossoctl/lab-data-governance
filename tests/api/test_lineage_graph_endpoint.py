"""HTTP tests for the two lineage-graph endpoints (ADR-0028 D14).

Covers what only the wire can show: the envelope's exact key set (the field names ARE
the contract — the handler is ``dataclasses.asdict`` over the retrieval result), the
``direction`` and ``source`` query parameters' validation, and the status codes. The
traversal semantics live in ``tests/retrieval/test_lineage_walk.py`` (pure) and
``tests/retrieval/test_lineage_graph.py`` (DB-backed); these do not re-assert them.

Three conventions pinned here deliberately:

- an unknown trace, entity **or source** is **200 with an empty result**, never 404 —
  the collection-read convention shared with the other trace sub-resources. For
  ``source`` the reason is stronger than convention: 404-ing an unrecognized key would
  mean deciding "unknown to this trace" from the *absence* of derived rows, which a
  mid-derivation trace cannot distinguish from "not there yet";
- a missing or unrecognized ``direction`` is **400**, never defaulted. The two
  directions answer different questions, so guessing would answer one the caller did
  not ask;
- a missing or empty ``source`` is **400** on the same footing (ADR-0028 D15). It is
  the other half of the question, and the only candidate default — the union over all
  sources — is the multi-source read the spec explicitly defers.
"""

from __future__ import annotations

import json
from urllib.parse import quote

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

# The data source natural key under trace. `source` is a natural key, never an entity
# id (ADR-0027/0028), and is exactly what `data-lineage-summary`'s `sources` lists.
_SRC = "user"


def _base_url(server: SpansApiServer) -> str:
    return f"http://127.0.0.1:{server.port}"


def _graph_url(
    server: SpansApiServer,
    tid: str,
    eid: str,
    direction: str,
    source: str | None = _SRC,
) -> str:
    """The graph URL. ``source=None`` omits the parameter entirely, which is how the
    "required parameter missing" case is distinguished from "present but empty"."""
    url = (
        f"{_base_url(server)}/api/traces/{tid}/entities/{eid}"
        f"/data-lineage-graph?direction={direction}"
    )
    return url if source is None else f"{url}&source={quote(source)}"


@pytest.fixture()
def seeded(configured_db: str) -> str:
    """``user -> agent -> tool``, request legs derived; enough for a two-hop walk.

    Legs are inserted in execution order so ``interaction_legs.seq`` ascends the way the
    trace ran — the seq rule (ADR-0028 D15 clause 4) now depends on it, so a fixture
    that inserted them in an arbitrary order would test an impossible trace.
    """
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
        for ix_id, caller, callee in (
            (_IX_UA, _USER, _AGENT),
            (_IX_AT, _AGENT, _TOOL),
        ):
            conn.execute(
                "INSERT INTO interactions (id, trace_id, caller_entity_id, "
                "callee_entity_id, summary) VALUES (%s, %s, %s, %s, 'call')",
                (ix_id, _TID, caller, callee),
            )
        for seq, ix_id in ((1, _IX_UA), (2, _IX_AT)):
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
                (ix_id, [_SRC], json.dumps({_SRC: []}), [_SRC], seq),
            )
        conn.commit()
    return _TID


# ---------------------------------------------------------------------------
# data-lineage-graph
# ---------------------------------------------------------------------------


def test_graph_wire_shape(seeded: str, api_server: SpansApiServer) -> None:
    """The envelope's key set is the contract.

    ``source`` joins it as a top-level field (ADR-0028 D15): the answer is
    unattributable without it, since the same seed has a different fanout per source.
    """
    resp = httpx.get(_graph_url(api_server, seeded, _USER, "fanout"))
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {
        "direction",
        "seed_entity_id",
        "source",
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
    assert body["source"] == _SRC
    assert set(body["entities"][0]) == {
        "id",
        "natural_key",
        "kind",
        "display_name",
        "namespace",  # wire contract v1.7 / migration 0020; null for non-pods
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
        f"/data-lineage-graph?source={_SRC}"
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


def test_missing_source_is_400(seeded: str, api_server: SpansApiServer) -> None:
    """``source`` is required on the same footing as ``direction`` (ADR-0028 D15).

    Omitted entirely — no ``&source=`` at all. The 400 rather than a default because the
    only candidate default is the union over all sources, which is the multi-source read
    the spec explicitly defers.
    """
    resp = httpx.get(_graph_url(api_server, seeded, _USER, "fanout", source=None))
    assert resp.status_code == 400
    assert "source" in resp.json()["error"]


def test_empty_source_is_400(seeded: str, api_server: SpansApiServer) -> None:
    """Present but empty is the same failure as absent — an unanswerable question.

    Worth pinning separately: a UI that always appends ``&source=`` and leaves it blank
    when nothing is selected must get the 400, not a silent whole-graph answer.
    """
    resp = httpx.get(_graph_url(api_server, seeded, _USER, "fanout", source=""))
    assert resp.status_code == 400
    assert "source" in resp.json()["error"]


def test_the_400s_share_one_error_shape(seeded: str, api_server: SpansApiServer) -> None:
    """Both malformed-question arms encode the same way, so a client parses one shape."""
    bad_direction = httpx.get(_graph_url(api_server, seeded, _USER, "sideways"))
    bad_source = httpx.get(
        _graph_url(api_server, seeded, _USER, "fanout", source=None)
    )
    assert bad_direction.status_code == bad_source.status_code == 400
    assert set(bad_direction.json()) == set(bad_source.json()) == {"error"}


def test_unknown_source_is_empty_not_404(
    seeded: str, api_server: SpansApiServer
) -> None:
    """**The unknown-source decision, on the wire.**

    A key matching no ``data_sources`` value anywhere in the trace is a valid, complete,
    EMPTY answer — 200 with ``state="no-adjacent"``. Not a 404, because deciding
    "unknown to this trace" would mean reading it off the *absence* of derived rows, and
    a mid-derivation trace cannot tell that from "not there yet": the same request would
    404 now and 200 later. That is the collapse the three-valued ``state`` and D6's
    ``status`` both exist to prevent.
    """
    resp = httpx.get(
        _graph_url(api_server, seeded, _USER, "fanout", source="no-such-source")
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["entities"] == []
    assert body["legs"] == []
    assert body["state"] == "no-adjacent"
    assert body["pending_frontier"] == []
    # Echoed back, so the caller can tell which question this empty answer belongs to.
    assert body["source"] == "no-such-source"


def test_a_source_with_url_special_characters_round_trips(
    seeded: str, api_server: SpansApiServer, configured_db: str
) -> None:
    """Real source keys are not URL-safe, and the parameter must survive encoding.

    The derivation writes keys like
    ``tool:agent:(travel_advisor,travel-advisor):search_destinations`` — colons,
    parentheses and commas. If the handler compared a mangled value the walk would
    silently answer ``no-adjacent`` for every real key, which looks exactly like a
    correct empty answer.
    """
    gnarly = "tool:agent:(travel_advisor,travel-advisor):search_destinations"
    with psycopg.connect(configured_db) as conn:
        conn.execute(
            "UPDATE lineage_metadata SET data_sources = %s", ([gnarly],)
        )
        conn.commit()
    body = httpx.get(
        _graph_url(api_server, seeded, _USER, "fanout", source=gnarly)
    ).json()
    assert body["source"] == gnarly
    assert body["state"] == "derived"
    assert {e["id"] for e in body["entities"]} == {_AGENT, _TOOL}


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
