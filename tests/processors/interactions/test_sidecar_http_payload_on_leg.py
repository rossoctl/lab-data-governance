"""Plain-HTTP legs carry their bodies (issue #309; wire contract v1.8 §5).

A tool's REST backend is an exchange no parser claims: ``lineage.protocol=http``
with the raw bodies as ``input.value`` / ``output.value`` once the producer
captures them (lab-dg #308, the cortex half). Before #309 the kind table gave
the two http rows no content kinds and ``_mk_payload`` dropped a captured body
on that account: the leg landed with ``payload_hash = NULL``, so classification
and data lineage never saw a REST backend's data. Now the http rows name their
kinds and a captured body is content-addressed like any other; a bodyless
exchange (a CONNECT tunnel, a GET) keeps its kinds and a NULL hash.
"""

from __future__ import annotations

import psycopg

from data_governance.processors.interactions.procedure import _interaction_id
from data_governance.processors.interactions.sidecar_driver import drain

from . import sidecar_golden as golden

# Two more outbound exchanges of the golden agent, under its entry: a JSON POST
# to a weather backend whose reply is plain text, and a CONNECT tunnel to a PSP.
H1, H2 = "c1c1c1c1c1c1c1c1", "c2c2c2c2c2c2c2c2"
H3, H4 = "c3c3c3c3c3c3c3c3", "c4c4c4c4c4c4c4c4"
REQ_BODY = '{"city":"Tokyo","units":"C"}'   # what the wire carries: the body as a string
RESP_BODY = "sunny, 22C"                     # not JSON — stays a string in the payload row
_HTTP = {
    "lineage.direction": "outbound", "lineage.protocol": "http",
    "lineage.self.id": "weather-service", "lineage.self.namespace": "team1",
}
HTTP_SPANS = [
    (H1, golden.A1, "CLIENT", {**_HTTP, "lineage.role": "request", "lineage.exchange.id": H1,
                               "lineage.peer.host": "weather-backend:9000", "http.method": "POST",
                               "url.path": "/weather", "input.value": REQ_BODY}),
    (H2, H1, "CLIENT", {**_HTTP, "lineage.role": "response", "lineage.exchange.id": H1,
                        "lineage.outcome": "ok", "http.status_code": 200, "output.value": RESP_BODY}),
    (H3, golden.A1, "CLIENT", {**_HTTP, "lineage.role": "request", "lineage.exchange.id": H3,
                               "lineage.peer.host": "psp.example:443", "http.method": "CONNECT",
                               "url.scheme": "tcp"}),
    (H4, H3, "CLIENT", {**_HTTP, "lineage.role": "response", "lineage.exchange.id": H3,
                        "lineage.outcome": "ok", "http.status_code": 200}),
]
POST = _interaction_id(golden.TRACE, H1)
TUNNEL = _interaction_id(golden.TRACE, H3)


def _legs(dsn: str) -> dict[tuple[str, str], tuple[str | None, str | None]]:
    """(interaction_id, leg_type) -> (payload_hash, content_kind)."""
    with psycopg.connect(dsn) as conn:
        return {
            (iid, leg): (ph, ck)
            for iid, leg, ph, ck in conn.execute(
                "SELECT interaction_id, leg_type::text, payload_hash, content_kind "
                "FROM interaction_legs"
            ).fetchall()
        }


def test_http_bodies_land_on_their_legs(configured_db: str) -> None:
    golden.insert_spans(configured_db, extra=HTTP_SPANS)
    drain(0)

    legs = _legs(configured_db)
    req_hash, req_kind = legs[(POST, "request")]
    resp_hash, resp_kind = legs[(POST, "response")]
    assert (req_kind, resp_kind) == ("http_request_body", "http_response_body")
    assert req_hash is not None and resp_hash is not None
    with psycopg.connect(configured_db) as conn:
        content = dict(conn.execute(
            "SELECT content_hash, content FROM interaction_payloads WHERE content_hash = ANY(%s)",
            ([req_hash, resp_hash],),
        ).fetchall())
        (callee_kind,) = conn.execute(
            "SELECT e.kind FROM interactions i JOIN entities e ON e.id = i.callee_entity_id WHERE i.id = %s",
            (POST,),
        ).fetchone()
    # The bodies are stored as the wire carried them: JSON text as the parsed
    # document, anything else as the string it was.
    assert content[req_hash] == {"city": "Tokyo", "units": "C"}
    assert content[resp_hash] == RESP_BODY
    assert callee_kind == "service"  # the entity side of the http row is unchanged


def test_bodyless_http_exchange_keeps_kinds_and_null_hash(configured_db: str) -> None:
    golden.insert_spans(configured_db, extra=HTTP_SPANS)
    drain(0)

    legs = _legs(configured_db)
    assert legs[(TUNNEL, "request")] == (None, "http_request_body")
    assert legs[(TUNNEL, "response")] == (None, "http_response_body")
    # Nothing else moved: the golden trace's own payload rows plus the two bodies.
    with psycopg.connect(configured_db) as conn:
        (payloads,) = conn.execute("SELECT count(*) FROM interaction_payloads").fetchone()
        (interactions,) = conn.execute("SELECT count(*) FROM interactions").fetchone()
    assert payloads == 7 + 2
    assert interactions == 4 + 2
