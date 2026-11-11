"""The verdict service's exchange reads (``retrieval.get_exchange_requests`` /
``get_request_findings``), against a real Postgres, no service.

The answers they make are in ``tests/api/test_verdict_route.py``; here, the typed
read: one row per exchange, the capture status derived from the request span's facts,
and the findings and content of a classified request payload."""

from __future__ import annotations

import json

import psycopg

from data_governance import retrieval
from tests.api.test_verdict_route import _seed

_T = "1" * 32


def _key(n: int) -> tuple[str, str, str]:
    exchange = f"{n:016x}"
    return f"ix-{n}", _T, exchange


def _classify(dsn: str, content_hash: str, findings: list[dict]) -> None:
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "INSERT INTO payload_classifications (content_hash, sensitivity_level, findings, model_version) "
            "VALUES (%s, 'high', %s::jsonb, 1)",
            (content_hash, json.dumps(findings)),
        )


def test_each_exchange_gets_its_request_with_its_capture_status(configured_db) -> None:
    cases = {
        1: dict(request_leg=False),
        2: dict(span_attributes={"http.request.body.size": 0}),
        3: dict(span_attributes={"http.request.body.size": 2048}),
        4: dict(span_attributes={}),
        5: dict(payload_hash="h-cut", span_attributes={"http.request.body.size": 9000, "input.value": "x…[truncated]"}),
        6: dict(payload_hash="h-whole", span_attributes={"http.request.body.size": 2, "input.value": "{}"}),
    }
    for n, seed in cases.items():
        iid, trace, exchange = _key(n)
        _seed(configured_db, iid, trace, exchange=exchange, **seed)
    _classify(configured_db, "h-whole", [])

    got = retrieval.get_exchange_requests([_key(n) for n in (*cases, 7)])

    assert {iid: v.unread_body for iid, v in got.items()} == {
        "ix-1": None, "ix-2": None, "ix-3": "body_not_captured", "ix-4": "body_not_captured",
        "ix-5": "body_truncated", "ix-6": None, "ix-7": None}
    assert got["ix-1"].has_request_leg is False and got["ix-7"].has_request_leg is False
    assert (got["ix-5"].payload_hash, got["ix-5"].payload_stored, got["ix-5"].classified) == ("h-cut", True, False)
    assert (got["ix-6"].payload_stored, got["ix-6"].classified) == (True, True)
    assert retrieval.get_exchange_requests([]) == {}


def test_findings_for_classified_requests_and_content_only_when_asked_and_small(configured_db) -> None:
    findings = [{"category": "person.name", "start": 9, "end": 13, "text": "Rosa"}]
    for n, payload_hash in ((1, "h-1"), (2, "h-2"), (3, None), (4, "h-unclassified")):
        iid, trace, exchange = _key(n)
        _seed(configured_db, iid, trace, exchange=exchange, payload_hash=payload_hash, payload={"name": "Rosa"})
    _classify(configured_db, "h-1", findings)
    _classify(configured_db, "h-2", findings)
    ids = ["ix-1", "ix-2", "ix-3", "ix-4"]

    got = retrieval.get_request_findings(ids, content_for={"ix-1"}, max_content_bytes=2)

    assert sorted(got) == ["ix-1", "ix-2"], "no request payload, or not classified yet: no entry"
    assert got["ix-1"].findings == findings and got["ix-1"].content == {"name": "Rosa"}
    assert got["ix-2"].content is None, "content only for the ids asked"
    assert retrieval.get_request_findings(ids, content_for={"ix-1"}, max_content_bytes=1)["ix-1"].content is None
    assert retrieval.get_request_findings([]) == {}
