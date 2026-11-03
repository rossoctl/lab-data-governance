"""The verdict service (``verdict_app``): ``GET /enforce/v1/verdicts/{trace}/{exchange}``.

Driven over a real socket (uvicorn in a thread) against a real Postgres, so the
wake is the migrations' own ``dg_interaction_risk_written`` / ``dg_legs_inserted``
triggers reaching the change hub:

- decided: a record that evidences the request leg answers at once; one written
  during the wait answers promptly; a record that does not evidence an existing
  request leg is no answer; when the interaction has no request leg at all, its
  latest record is the answer; ``require_approval`` is returned as decided;
- unjudged: a record whose request body was not captured or was cut says so, at once,
  with the record's verdict; a body-size fact of 0 or a payload is what makes it decided;
- what was caught: rules, labels and, on a block, the payload with each finding replaced;
  a value never reaches the answer; a text that cannot be rendered leaves the block standing;
- pending at ``wait_ms``; ``wait_ms`` above 3000 is refused, never clamped;
- identity: with a secret, no or a wrong bearer is 401;
- the cap: beyond ``max_waiters`` the answer is 503 ``verdict_busy`` at once;
- one read per wake: many waiters, few writes, a handful of batched reads; every
  waiter on one interaction is answered by its one write;
- priority classification: an unclassified request payload is classified here (the classifier's
  own write-once row) and the interaction is named on ``dg_priority_decision``; a
  payload whose row lands after the request started is picked up inside the
  budget; ``VERDICT_PRIORITY_CLASSIFICATION=off`` and ``VERDICT_PRIORITY_DECISION=off`` do what they say;
- readiness waits for the warm model; one structured log line per request;
- the service serves the verdict and nothing else; the full API does not serve it.
"""

from __future__ import annotations

import contextlib
import json
import logging
import socket
import threading
import time
from collections.abc import Iterator

import httpx
import psycopg
import pytest
import uvicorn

from data_governance import db
from data_governance.processors.classification.projection import project
from data_governance.processors.interactions.procedure import _interaction_id
from data_governance.risk.api import verdict_app, verdict_routes
from data_governance.risk.api.verdict_app import build_verdict_app
from data_governance.risk.api.verdict_routes import PriorityClassifier, Settings

_TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"
_EXCHANGE = "00f067aa0ba902b7"
_IID = _interaction_id(_TRACE, _EXCHANGE)
_SECRET = "s3cret-for-tests"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _null_priority_classifier(settings: Settings) -> PriorityClassifier:
    return PriorityClassifier(lambda: None, 2, settings.priority_classification_concurrency)


@contextlib.contextmanager
def _serve(dsn: str, settings: Settings, priority_classifier_factory=_null_priority_classifier) -> Iterator[tuple[str, object]]:
    app = build_verdict_app(listen_dsn=dsn, settings=settings, priority_classifier_factory=priority_classifier_factory)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    assert server.started, "verdict server did not start"
    assert app.state.change_hub.connected.wait(5.0), "change hub never registered its LISTEN"
    try:
        yield f"http://127.0.0.1:{port}", app
    finally:
        server.should_exit = True
        thread.join(timeout=10)


@pytest.fixture()
def serve(configured_db: str):
    def _factory(**overrides):
        priority_classifier_factory = overrides.pop("priority_classifier_factory", _null_priority_classifier)
        base = dict(secret=_SECRET, priority_classification=False, priority_decision=False)
        base.update(overrides)
        return _serve(configured_db, Settings(**base), priority_classifier_factory)
    return _factory


def _get(base: str, wait_ms: int | str, *, trace: str = _TRACE, exchange: str = _EXCHANGE,
         secret: str | None = _SECRET, timeout: float = 10) -> httpx.Response:
    headers = {"Authorization": f"Bearer {secret}"} if secret is not None else {}
    return httpx.get(f"{base}/enforce/v1/verdicts/{trace}/{exchange}?wait_ms={wait_ms}",
                     headers=headers, timeout=timeout)


def _seed(dsn: str, iid: str = _IID, trace: str = _TRACE, *, payload_hash: str | None = None,
          payload: dict | None = None, request_leg: bool = True, response_leg: bool = False,
          exchange: str = _EXCHANGE, span_attributes: dict | None = None) -> None:
    with psycopg.connect(dsn) as conn:
        if request_leg:
            # The exchange's request span. By default what the sidecar writes for a body
            # it emitted (a payload) or a request with none.
            attributes = {"http.request.body.size": 2 if payload_hash else 0} if span_attributes is None else span_attributes
            conn.execute(
                "INSERT INTO spans (trace_id, span_id, parent_id, kind, name, started_at, attributes, "
                "seq, arrival_seq, observed_at) VALUES (%s, %s, NULL, 'CLIENT', 'lineage', now(), %s::jsonb, "
                "nextval('spans_seq'), currval('spans_seq'), now()) ON CONFLICT DO NOTHING",
                (trace, exchange, json.dumps(attributes)),
            )
        for eid in ("ent-caller", "ent-callee"):
            conn.execute(
                "INSERT INTO entities (id, kind, natural_key, display_name, namespace, "
                "detected_from, original_seq) VALUES (%s, 'agent', %s, %s, 'pharmacy', 'span-attr', 1) "
                "ON CONFLICT (id) DO NOTHING",
                (eid, f"nk-{eid}", eid),
            )
        conn.execute(
            "INSERT INTO interactions (id, trace_id, caller_entity_id, callee_entity_id, summary) "
            "VALUES (%s, %s, 'ent-caller', 'ent-callee', 'report') ON CONFLICT (id) DO NOTHING",
            (iid, trace),
        )
        if payload_hash is not None:
            conn.execute(
                "INSERT INTO interaction_payloads (content_hash, content, byte_size) "
                "VALUES (%s, %s::jsonb, 2) ON CONFLICT (content_hash) DO NOTHING",
                (payload_hash, json.dumps(payload or {})),
            )
        for leg, on in (("request", request_leg), ("response", response_leg)):
            if on:
                conn.execute(
                    "INSERT INTO interaction_legs (interaction_id, leg_type, occurred_at, payload_hash, error) "
                    "VALUES (%s, %s, now(), %s, false)",
                    (iid, leg, payload_hash if leg == "request" else None),
                )
        conn.commit()


def _write_record(dsn: str, iid: str = _IID, trace: str = _TRACE, *, enforcement: str | None = "block",
                  legs: str = "{request}") -> None:
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "INSERT INTO interaction_risk_records (interaction_id, trace_id, caller_entity_id, "
            "callee_entity_id, version, computed_at, risk_level, enforcement_type, "
            "policy_event_count, triggered_rule_ids, legs_evidenced) "
            "SELECT %s, %s, 'ent-caller', 'ent-callee', COALESCE(MAX(version), 0) + 1, now(), "
            "'critical', %s, 1, '{DG-001}', %s::text[] "
            "FROM interaction_risk_records WHERE interaction_id = %s",
            (iid, trace, enforcement, legs, iid),
        )
        conn.commit()


def _classified(dsn: str, content_hash: str) -> bool:
    with psycopg.connect(dsn) as conn:
        return conn.execute(
            "SELECT 1 FROM payload_classifications WHERE content_hash = %s", (content_hash,)
        ).fetchone() is not None


# --- decided / pending ----------------------------------------------------------


def test_existing_record_answers_at_once(serve, configured_db) -> None:
    _seed(configured_db)
    _write_record(configured_db)
    with serve() as (base, _app):
        started = time.monotonic()
        r = _get(base, 3000)
        assert time.monotonic() - started < 1.0
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "decided"
    assert body["verdict"] == "block"
    assert body["exchange_id"] == _EXCHANGE
    assert body["version"] == 1
    assert body["rules"] == ["DG-001"]
    assert sorted(body) == ["exchange_id", "labels", "rules", "state", "verdict", "version", "waited_ms"]


def test_no_record_waits_then_pending(serve) -> None:
    with serve() as (base, _app):
        started = time.monotonic()
        r = _get(base, 600)
        elapsed = time.monotonic() - started
    assert r.status_code == 200
    assert r.json()["state"] == "pending"
    assert r.json()["waited_ms"] >= 590
    assert 0.55 <= elapsed < 2.0


def test_record_written_during_the_wait_answers_promptly(serve, configured_db) -> None:
    _seed(configured_db)
    with serve() as (base, _app):
        writer = threading.Timer(0.5, _write_record, args=(configured_db,))
        writer.start()
        started = time.monotonic()
        r = _get(base, 3000)
        elapsed = time.monotonic() - started
        writer.join()
    assert r.json()["state"] == "decided"
    assert elapsed < 0.5 + verdict_routes.BACKSTOP_SECONDS + 0.5


def test_a_record_not_evidencing_an_existing_request_leg_is_no_answer(serve, configured_db) -> None:
    """The request leg exists and the record does not evidence it: the request's
    facts are not in it, so the gate keeps waiting — until a record that does."""
    _seed(configured_db, response_leg=True)
    _write_record(configured_db, legs="{response}")
    with serve() as (base, _app):
        assert _get(base, 300).json()["state"] == "pending"
        _write_record(configured_db, enforcement="allow", legs="{request,response}")
        body = _get(base, 300).json()
    assert body["state"] == "decided"
    assert body["verdict"] == "allow"
    assert body["version"] == 2


def test_with_no_request_leg_the_latest_record_is_the_answer(serve, configured_db) -> None:
    """A derivation that never writes a request leg (the graph algorithm's
    request-less call): no record will ever evidence one, so the gate must not
    wait out its budget for it."""
    _seed(configured_db, request_leg=False, response_leg=True)
    _write_record(configured_db, enforcement="allow", legs="{response}")
    with serve() as (base, _app):
        body = _get(base, 3000).json()
    assert body["state"] == "decided"
    assert body["verdict"] == "allow"


def test_require_approval_is_decided(serve, configured_db) -> None:
    _seed(configured_db)
    _write_record(configured_db, enforcement="require_approval")
    with serve() as (base, _app):
        body = _get(base, 100).json()
    assert body["state"] == "decided"
    assert body["verdict"] == "require_approval"


def test_a_decided_record_with_no_verdict_is_returned_as_is(serve, configured_db) -> None:
    """The gate reads an empty verdict as unknown; the service does not invent one."""
    _seed(configured_db)
    _write_record(configured_db, enforcement=None)
    with serve() as (base, _app):
        body = _get(base, 100).json()
    assert body["state"] == "decided"
    assert body["verdict"] is None


def test_the_latest_record_evidencing_the_request_leg_answers(serve, configured_db) -> None:
    _seed(configured_db)
    _write_record(configured_db, enforcement="allow")
    _write_record(configured_db, enforcement="block")
    with serve() as (base, _app):
        body = _get(base, 100).json()
    assert (body["verdict"], body["version"]) == ("block", 2)


# --- unjudged -------------------------------------------------------------------


def test_a_body_the_sidecar_did_not_emit_is_unjudged_at_once(serve, configured_db) -> None:
    """capture_io off: the size says a body was there and no value came with it, so
    the record's allow says nothing about it. The gate must not wait out its budget."""
    _seed(configured_db, span_attributes={"http.request.body.size": 2048})
    _write_record(configured_db, enforcement="allow")
    with serve() as (base, _app):
        started = time.monotonic()
        body = _get(base, 3000).json()
        assert time.monotonic() - started < 1.0
    assert body["state"] == "unjudged"
    assert body["reason"] == "body_not_captured"
    assert body["verdict"] == "allow", "the record's verdict, as it is"
    assert sorted(body) == ["exchange_id", "labels", "reason", "rules", "state", "verdict", "version", "waited_ms"]


def test_a_body_with_no_size_fact_is_unjudged(serve, configured_db) -> None:
    """A sidecar older than contract v1.8 sends no size: nobody knows there was no body."""
    _seed(configured_db, span_attributes={})
    _write_record(configured_db, enforcement="allow")
    with serve() as (base, _app):
        body = _get(base, 100).json()
    assert (body["state"], body["reason"]) == ("unjudged", "body_not_captured")


def test_a_cut_body_is_unjudged(serve, configured_db) -> None:
    _seed(configured_db, payload_hash="h-cut", span_attributes={
        "http.request.body.size": 9000, "input.value": "x" * 4084 + "…[truncated]"})
    _write_record(configured_db, enforcement="allow")
    with serve() as (base, _app):
        body = _get(base, 100).json()
    assert (body["state"], body["reason"]) == ("unjudged", "body_truncated")


def test_a_block_on_what_was_read_stays_a_block(serve, configured_db) -> None:
    _seed(configured_db, payload_hash="h-cut2", span_attributes={
        "http.request.body.size": 9000, "input.value": "x…[truncated]"})
    _write_record(configured_db, enforcement="block")
    with serve() as (base, _app):
        body = _get(base, 100).json()
    assert (body["state"], body["verdict"]) == ("unjudged", "block"), "the gate denies on the verdict"


# --- what was caught -------------------------------------------------------------


_SSN = "123-45-6789"
_PAYLOAD = {"drug": "Lisinopril", "ssn": _SSN}
_TEXT = project(_PAYLOAD)
_FINDING = {"entity_type": "PN", "category": "person.govID", "sensitivity_level": "RESTRICTED",
            "start": _TEXT.index(_SSN), "end": _TEXT.index(_SSN) + len(_SSN), "text": _SSN}


def _classify(dsn: str, content_hash: str, findings) -> None:
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "INSERT INTO payload_classifications (content_hash, sensitivity_level, regulatory_tags, "
            "contains_identity_bundle, is_personalized, primary_domain, findings, model_version) "
            "VALUES (%s, 'RESTRICTED', '{}', false, false, 'person', %s::jsonb, 2)",
            (content_hash, json.dumps(findings)),
        )
        conn.commit()


def test_a_block_says_what_was_caught_and_never_the_value(serve, configured_db) -> None:
    _seed(configured_db, payload_hash="h-pii", payload=_PAYLOAD)
    _classify(configured_db, "h-pii", [_FINDING])
    _write_record(configured_db, enforcement="block")
    with serve() as (base, _app):
        r = _get(base, 100)
    body = r.json()
    assert body["state"] == "decided"
    assert body["rules"] == ["DG-001"]
    assert body["labels"] == ["person.govID"]
    assert body["redacted_payload"] == _TEXT.replace(_SSN, "[person.govID]")
    assert _SSN not in r.text


def test_what_was_caught_is_the_request_legs_own(serve, configured_db) -> None:
    _seed(configured_db, payload_hash="h-req", payload=_PAYLOAD)
    answer = {"note": "from the response"}
    with psycopg.connect(configured_db) as conn:
        conn.execute("INSERT INTO interaction_payloads (content_hash, content, byte_size) VALUES ('h-resp', %s::jsonb, 2)",
                     (json.dumps(answer),))
        conn.execute("INSERT INTO interaction_legs (interaction_id, leg_type, occurred_at, payload_hash, error) "
                     "VALUES (%s, 'response', now(), 'h-resp', false)", (_IID,))
        conn.commit()
    _classify(configured_db, "h-req", [_FINDING])
    response_text = project(answer)
    _classify(configured_db, "h-resp", [{"entity_type": "X", "category": "response.only", "start": 0, "end": 1,
                                         "text": response_text[0]}])
    _write_record(configured_db, enforcement="block")
    with serve() as (base, _app):
        body = _get(base, 100).json()
    assert body["labels"] == ["person.govID"]
    assert body["redacted_payload"] == _TEXT.replace(_SSN, "[person.govID]")


def test_an_allow_has_labels_and_no_text(serve, configured_db) -> None:
    _seed(configured_db, payload_hash="h-allow", payload=_PAYLOAD)
    _classify(configured_db, "h-allow", [_FINDING])
    _write_record(configured_db, enforcement="allow")
    with serve() as (base, _app):
        r = _get(base, 100)
    body = r.json()
    assert (body["verdict"], body["labels"]) == ("allow", ["person.govID"])
    assert "redacted_payload" not in body
    assert _SSN not in r.text


def test_an_unjudged_block_has_the_text_it_read(serve, configured_db) -> None:
    cut = f"ssn {_SSN} and more…[truncated]"
    _seed(configured_db, payload_hash="h-cut3", payload=cut, span_attributes={
        "http.request.body.size": 9000, "input.value": cut})
    _classify(configured_db, "h-cut3", [{"category": "person.govID", "start": 4, "end": 15, "text": _SSN}])
    _write_record(configured_db, enforcement="block")
    with serve() as (base, _app):
        body = _get(base, 100).json()
    assert (body["state"], body["reason"]) == ("unjudged", "body_truncated")
    assert body["redacted_payload"] == "ssn [person.govID] and more…[truncated]"


def test_an_unclassified_block_has_no_text_and_no_labels(serve, configured_db) -> None:
    _seed(configured_db, payload_hash="h-none", payload=_PAYLOAD)
    _write_record(configured_db, enforcement="block")
    with serve() as (base, _app):
        r = _get(base, 100)
    body = r.json()
    assert (body["verdict"], body["labels"]) == ("block", [])
    assert "redacted_payload" not in body
    assert _SSN not in r.text


def test_a_stored_payload_above_the_cap_is_not_fetched(serve, configured_db, monkeypatch) -> None:
    """The seed stores byte_size 2, so a cap below that leaves the body unread."""
    monkeypatch.setattr(verdict_routes, "REDACTED_MAX_BYTES", 1)
    calls = []
    monkeypatch.setattr(verdict_routes, "_redact", lambda *a: calls.append(a) or "x")
    _seed(configured_db, payload_hash="h-big", payload=_PAYLOAD)
    _classify(configured_db, "h-big", [_FINDING])
    _write_record(configured_db, enforcement="block")
    with serve() as (base, _app):
        body = _get(base, 100).json()
    assert body["verdict"] == "block" and body["labels"] == ["person.govID"]
    assert "redacted_payload" not in body and calls == []


def test_a_malformed_finding_leaves_the_text_out_and_the_block_stands(serve, configured_db, caplog) -> None:
    _seed(configured_db, payload_hash="h-bad", payload=_PAYLOAD)
    _classify(configured_db, "h-bad", [{**_FINDING, "start": 0, "end": 3}])
    _write_record(configured_db, enforcement="block")
    with caplog.at_level(logging.WARNING, logger=verdict_routes.log.name), serve() as (base, _app):
        r = _get(base, 100)
    body = r.json()
    assert (r.status_code, body["verdict"]) == (200, "block")
    assert "redacted_payload" not in body
    assert _SSN not in caplog.text and "no redacted payload" in caplog.text


def test_pending_names_the_exchange(serve) -> None:
    with serve() as (base, _app):
        body = _get(base, 0).json()
    assert sorted(body) == ["exchange_id", "state", "waited_ms"]
    assert (body["state"], body["exchange_id"]) == ("pending", _EXCHANGE)


def test_a_parser_reduction_reads_as_decided(serve, configured_db) -> None:
    """The stated limit: a value shorter than the size is what a parser's reduction looks
    like, and nothing says what it left out. The service does not guess."""
    _seed(configured_db, payload_hash="h-reduced", span_attributes={
        "lineage.protocol": "inference", "http.request.body.size": 167, "input.value": '[{"role":"user"}]'})
    _write_record(configured_db, enforcement="allow")
    with serve() as (base, _app):
        assert _get(base, 100).json()["state"] == "decided"


def test_no_body_and_a_whole_body_are_decided(serve, configured_db) -> None:
    trace, exchange = "a" * 32, "b" * 16
    iid = _interaction_id(trace, exchange)
    _seed(configured_db)
    _write_record(configured_db, enforcement="allow")
    _seed(configured_db, iid, trace, exchange=exchange, payload_hash="h-whole", span_attributes={
        "http.request.body.size": 16, "input.value": '{"city":"Tokyo"}'})
    _write_record(configured_db, iid, trace, enforcement="allow")
    with serve() as (base, _app):
        assert _get(base, 100).json()["state"] == "decided"
        assert _get(base, 100, trace=trace, exchange=exchange).json()["state"] == "decided"


# --- the request --------------------------------------------------------------


@pytest.mark.parametrize(
    ("trace", "exchange", "wait", "error"),
    [
        ("XYZ", _EXCHANGE, "0", "bad_key"),
        (_TRACE.upper(), _EXCHANGE, "0", "bad_key"),
        (_TRACE, "short", "0", "bad_key"),
        (_TRACE, _EXCHANGE, "soon", "bad_wait_ms"),
        (_TRACE, _EXCHANGE, "-1", "bad_wait_ms"),
        (_TRACE, _EXCHANGE, "1_000", "bad_wait_ms"),
        (_TRACE, _EXCHANGE, "%2B5", "bad_wait_ms"),
        (_TRACE, _EXCHANGE, "%205", "bad_wait_ms"),
        (_TRACE, _EXCHANGE, "9" * 5000, "bad_wait_ms"),
        (_TRACE + "%0A", _EXCHANGE, "0", "bad_key"),
        (_TRACE, _EXCHANGE + "%0A", "0", "bad_key"),
    ],
)
def test_bad_requests(serve, trace, exchange, wait, error) -> None:
    with serve() as (base, _app):
        r = _get(base, wait, trace=trace, exchange=exchange)
    assert r.status_code == 400
    assert r.json()["error"] == error


def test_wait_above_the_cap_is_refused_not_clamped(serve) -> None:
    with serve() as (base, _app):
        started = time.monotonic()
        r = _get(base, 3001)
        assert time.monotonic() - started < 0.5
        assert _get(base, 0).status_code == 200
    assert r.status_code == 400
    assert r.json() == {"error": "wait_ms_too_large", "max": 3000}


@pytest.mark.parametrize("header", [None, "Bearer wrong", f"Bearer {_SECRET}x", f"Basic {_SECRET}", _SECRET])
def test_identity_is_required(serve, configured_db, header) -> None:
    _seed(configured_db)
    _write_record(configured_db)
    with serve() as (base, _app):
        r = httpx.get(f"{base}/enforce/v1/verdicts/{_TRACE}/{_EXCHANGE}?wait_ms=0",
                      headers={"Authorization": header} if header else {}, timeout=10)
        ok = _get(base, 0)
    assert r.status_code == 401
    assert r.json() == {"error": "unauthorized"}
    assert ok.status_code == 200 and ok.json()["verdict"] == "block"


def test_no_secret_configured_serves_without_identity(serve, configured_db) -> None:
    _seed(configured_db)
    _write_record(configured_db)
    with serve(secret="") as (base, _app):
        assert _get(base, 0, secret=None).json()["verdict"] == "block"


def test_beyond_the_waiter_cap_is_busy_at_once(serve) -> None:
    with serve(max_waiters=3) as (base, app):
        results: list[int] = []
        threads = [
            threading.Thread(target=lambda: results.append(_get(base, 1500).status_code)) for _ in range(3)
        ]
        for t in threads:
            t.start()
        deadline = time.monotonic() + 3
        while app.state.verdict.active < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert app.state.verdict.active == 3
        started = time.monotonic()
        busy = _get(base, 1500)
        busy_s = time.monotonic() - started
        for t in threads:
            t.join()
        after = _get(base, 0)
    assert busy.status_code == 503
    assert busy.json() == {"error": "verdict_busy"}
    assert busy_s < 0.3
    assert results == [200, 200, 200]
    assert after.status_code == 200


# --- one read per wake ----------------------------------------------------------


def test_many_waiters_few_writes_one_read_per_wake(serve, configured_db) -> None:
    keys = [(f"{i:032x}", f"{i:016x}") for i in range(1, 31)]
    for trace, exchange in keys:
        _seed(configured_db, _interaction_id(trace, exchange), trace, exchange=exchange)
    decided_keys = keys[:3]
    with serve() as (base, app):
        results: dict[tuple[str, str], tuple[dict, float]] = {}

        def ask(trace, exchange):
            started = time.monotonic()
            body = _get(base, 1500, trace=trace, exchange=exchange).json()
            results[(trace, exchange)] = (body, time.monotonic() - started)

        threads = [threading.Thread(target=ask, args=k) for k in keys]
        for t in threads:
            t.start()
        deadline = time.monotonic() + 3
        while app.state.verdict.active < len(keys) and time.monotonic() < deadline:
            time.sleep(0.01)
        reads_before = app.state.verdict.dispatch_reads
        for trace, exchange in decided_keys:
            _write_record(configured_db, _interaction_id(trace, exchange), trace)
        for t in threads:
            t.join()
        reads = app.state.verdict.dispatch_reads - reads_before
    for key in keys:
        body, elapsed = results[key]
        if key in decided_keys:
            assert body["state"] == "decided" and elapsed < 1.2, (key, body, elapsed)
        else:
            assert body["state"] == "pending", (key, body)
    # 30 waiters for ~1.5 s: the backstop (every 0.2 s) plus the three writes'
    # wakes, each ONE batched read — not one read per waiter per wake (~240).
    assert reads <= 1.5 / verdict_routes.BACKSTOP_SECONDS + len(decided_keys) + 4, reads


def test_every_waiter_on_one_interaction_is_answered_by_its_one_write(serve, configured_db) -> None:
    _seed(configured_db)
    with serve() as (base, app):
        bodies: list[dict] = []
        threads = [threading.Thread(target=lambda: bodies.append(_get(base, 3000).json())) for _ in range(8)]
        for t in threads:
            t.start()
        deadline = time.monotonic() + 3
        while app.state.verdict.active < 8 and time.monotonic() < deadline:
            time.sleep(0.01)
        _write_record(configured_db)
        for t in threads:
            t.join()
    assert [b["state"] for b in bodies] == ["decided"] * 8


def test_a_write_wakes_the_waiter_with_the_backstop_out_of_reach(serve, configured_db, monkeypatch) -> None:
    """The wake is the migrations' own trigger on the real channels."""
    monkeypatch.setattr(verdict_routes, "BACKSTOP_SECONDS", 30.0)
    _seed(configured_db)
    with serve() as (base, _app):
        writer = threading.Timer(0.3, _write_record, args=(configured_db,))
        writer.start()
        started = time.monotonic()
        body = _get(base, 3000).json()
        elapsed = time.monotonic() - started
        writer.join()
    assert body["state"] == "decided"
    assert elapsed < 1.5


def test_the_backstop_answers_with_no_wake_at_all(serve, configured_db, monkeypatch) -> None:
    """A hub whose session is down: nothing announces the write, the re-read finds it."""
    monkeypatch.setattr(verdict_routes, "HUB_CHANNELS", ("nobody_fires_this",))
    _seed(configured_db)
    with serve() as (base, _app):
        writer = threading.Timer(0.3, _write_record, args=(configured_db,))
        writer.start()
        started = time.monotonic()
        body = _get(base, 3000).json()
        elapsed = time.monotonic() - started
        writer.join()
    assert body["state"] == "decided"
    assert elapsed < 0.3 + verdict_routes.BACKSTOP_SECONDS + 0.7


def test_a_finished_waiter_leaves_the_map(serve, configured_db) -> None:
    _seed(configured_db)
    _write_record(configured_db)
    with serve() as (base, app):
        _get(base, 100)
        _get(base, 100, trace="c" * 32, exchange="d" * 16)
        assert app.state.verdict._waiters == {}


# --- priority classification -------------------------------------------------------------------


def _leg_ready_stand_in(dsn: str, content_hash: str, stop: threading.Event, *, enforcement="block") -> None:
    """What leg-ready's priority decision does once the payload is classified."""
    while not stop.is_set():
        if _classified(dsn, content_hash):
            _write_record(dsn, enforcement=enforcement)
            return
        time.sleep(0.02)


def test_the_priority_classifier_classifies_and_names_the_interaction(serve, configured_db) -> None:
    _seed(configured_db, payload_hash="h-req", payload={"drug": "x"})
    stop = threading.Event()
    stand_in = threading.Thread(target=_leg_ready_stand_in, args=(configured_db, "h-req", stop))
    with db.listen_payloads(verdict_routes.PRIORITY_DECISION_CHANNEL, configured_db) as decision_listener:
        with serve(priority_classification=True, priority_decision=True) as (base, _app):
            stand_in.start()
            body = _get(base, 3000).json()
            stop.set()
            stand_in.join()
        notes = decision_listener.wait(2.0)
        deadline = time.monotonic() + 2
        while len(notes) < 2 and time.monotonic() < deadline:
            notes += decision_listener.wait(0.5)
    assert body["state"] == "decided" and body["verdict"] == "block"
    assert _classified(configured_db, "h-req"), "priority classification wrote the classifier's own row"
    assert (verdict_routes.PRIORITY_DECISION_CHANNEL, _IID) in notes
    assert len(notes) == 2, "named when the wait started undecided, and again once it was classified"


def test_the_priority_classifier_waits_for_a_payload_still_in_flight(serve, configured_db) -> None:
    """The interaction is not derived yet when the gate asks; its leg and payload
    land 0.4 s later (the dg_legs_inserted wake), and priority classification classifies them
    inside the same budget."""
    stop = threading.Event()
    stand_in = threading.Thread(target=_leg_ready_stand_in, args=(configured_db, "h-late", stop))
    with serve(priority_classification=True, priority_decision=True) as (base, _app):
        stand_in.start()
        seeder = threading.Timer(0.4, _seed, args=(configured_db,), kwargs={"payload_hash": "h-late"})
        seeder.start()
        started = time.monotonic()
        body = _get(base, 3000).json()
        elapsed = time.monotonic() - started
        seeder.join()
        stop.set()
        stand_in.join()
    assert body["state"] == "decided"
    assert elapsed < 2.0


def test_priority_classifier_off_classifies_nothing_and_names_nothing(serve, configured_db) -> None:
    _seed(configured_db, payload_hash="h-off")
    with db.listen_payloads(verdict_routes.PRIORITY_DECISION_CHANNEL, configured_db) as decision_listener:
        with serve(priority_classification=False, priority_decision=False) as (base, _app):
            assert _get(base, 600).json()["state"] == "pending"
        assert decision_listener.wait(0.5) == []
    assert not _classified(configured_db, "h-off")


def test_priority_decision_off_classifies_but_names_nothing(serve, configured_db) -> None:
    _seed(configured_db, payload_hash="h-nw")
    with db.listen_payloads(verdict_routes.PRIORITY_DECISION_CHANNEL, configured_db) as decision_listener:
        with serve(priority_classification=True, priority_decision=False) as (base, _app):
            assert _get(base, 600).json()["state"] == "pending"
        assert decision_listener.wait(0.5) == []
    assert _classified(configured_db, "h-nw")


def test_a_payload_nobody_waits_on_does_not_run_ahead_of_a_live_gate(serve, configured_db) -> None:
    """Twelve gates give up while one classification runs. None of theirs is queued,
    so the gate that came last gets a worker as soon as the running one ends."""
    class Slow(PriorityClassifier):
        def _classify(self, content_hash):
            time.sleep(0.3)
            return super()._classify(content_hash)

    keys = [(f"{i:032x}", f"{i:016x}") for i in range(1, 13)]
    for i, (trace, exchange) in enumerate(keys):
        _seed(configured_db, _interaction_id(trace, exchange), trace, exchange=exchange, payload_hash=f"h-gone-{i}")
    _seed(configured_db, payload_hash="h-live")

    def factory(settings):
        return Slow(lambda: None, 2, settings.priority_classification_concurrency)

    with serve(priority_classification=True, priority_classifier_factory=factory) as (base, app):
        deadline = time.monotonic() + 5
        while not app.state.verdict.ready and time.monotonic() < deadline:
            time.sleep(0.01)
        threads = [threading.Thread(target=_get, args=(base, 50), kwargs={"trace": t, "exchange": e}) for t, e in keys]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        _get(base, 1500)
        assert _classified(configured_db, "h-live")
    gone = sum(_classified(configured_db, f"h-gone-{i}") for i in range(len(keys)))
    assert gone <= 2, f"{gone} classifications ran for gates that had left"


def test_nothing_is_classified_before_the_model_is_warm(serve, configured_db) -> None:
    """A row written with no model would be a permanent no-findings row."""
    release = threading.Event()

    def cold(settings):
        def factory():
            release.wait(10)
        return PriorityClassifier(factory, 2, settings.priority_classification_concurrency)

    _seed(configured_db, payload_hash="h-cold")
    # Two workers: the load holds one, so only the warm check keeps the other from classifying.
    with serve(priority_classification=True, priority_classification_concurrency=2,
               priority_classifier_factory=cold) as (base, _app):
        assert _get(base, 300).json()["state"] == "pending"
        assert not _classified(configured_db, "h-cold")
        release.set()


def test_a_model_that_does_not_load_ends_the_process(serve, monkeypatch) -> None:
    codes: list[int] = []
    monkeypatch.setattr(verdict_app, "_exit", codes.append)

    def broken(settings):
        def factory():
            raise OSError("no weights")
        return PriorityClassifier(factory, 2, settings.priority_classification_concurrency)

    with serve(priority_classification=True, priority_classifier_factory=broken):
        deadline = time.monotonic() + 5
        while not codes and time.monotonic() < deadline:
            time.sleep(0.02)
    assert codes == [1]


def test_the_priority_classifier_classifies_with_the_model_and_generation_it_was_given(configured_db, monkeypatch) -> None:
    from data_governance.processors.classification import driver as classification_driver
    from data_governance.processors.classification import verdict as classification_verdict

    _seed(configured_db, payload_hash="h-model", payload={"drug": "x"})
    detector = object()
    seen: list[dict] = []
    monkeypatch.setattr(classification_verdict, "classify", lambda *a, **k: None)
    monkeypatch.setattr(classification_driver, "process_payload", lambda tx, payload, **k: seen.append({"hash": payload.content_hash, **k}))
    priority_classifier = PriorityClassifier(lambda: detector, 7, 1)
    try:
        priority_classifier._load_and_warm()
        priority_classifier._classify("h-model")
    finally:
        priority_classifier.shutdown()
    assert seen == [{"hash": "h-model", "detector": detector, "model_version": 7}]


def test_the_default_priority_classifier_is_the_classifiers_own() -> None:
    from data_governance.processors.classification import __main__ as classifier

    priority_classifier = verdict_app._default_priority_classifier(Settings())
    try:
        assert priority_classifier._factory is classifier._build_detector
        assert priority_classifier._model_version == classifier.MODEL_VERSION
    finally:
        priority_classifier.shutdown()


def test_the_service_names_on_the_channel_the_arm_hears() -> None:
    from data_governance.processors.leg_ready import priority_decision

    assert verdict_routes.PRIORITY_DECISION_CHANNEL == priority_decision.PRIORITY_DECISION_CHANNEL


def test_settings_from_env() -> None:
    s = Settings.from_env({"VERDICT_PRIORITY_CLASSIFICATION": "off", "VERDICT_PRIORITY_DECISION": "on", "VERDICT_SECRET": " x "})
    assert (s.priority_classification, s.priority_decision, s.secret, s.max_waiters) == (False, False, "x", 64)
    d = Settings.from_env({})
    assert (d.priority_classification, d.priority_decision, d.max_waiters) == (True, True, 64)
    assert d.pool_size == d.read_workers + d.priority_classification_concurrency + 1 == 6
    with pytest.raises(ValueError):
        Settings.from_env({"VERDICT_PRIORITY_CLASSIFICATION": "maybe"})
    with pytest.raises(ValueError, match="VERDICT_MAX_WAITERS"):
        Settings.from_env({"VERDICT_MAX_WAITERS": "abc"})


def test_main_refuses_to_start_without_a_secret(monkeypatch, capsys) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgres://unused")
    monkeypatch.delenv("VERDICT_SECRET", raising=False)
    assert verdict_app.main() == 2
    assert "VERDICT_SECRET must be set" in capsys.readouterr().err


def test_main_refuses_another_schema(monkeypatch, capsys) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgres://unused")
    monkeypatch.setenv("VERDICT_SECRET", _SECRET)
    monkeypatch.setattr(db, "configure", lambda *a, **k: None)
    monkeypatch.setattr(db, "close_pool", lambda: None)

    def mismatch() -> None:
        raise verdict_app.SchemaVersionMismatch("schema is old")

    monkeypatch.setattr(verdict_app, "check_schema_version", mismatch)
    assert verdict_app.main() == 3
    assert "schema is old" in capsys.readouterr().err


# --- readiness, the log line, the surface ---------------------------------------


def test_ready_only_after_the_model_is_warm(serve) -> None:
    release = threading.Event()

    def slow_priority_classifier(settings):
        def factory():
            release.wait(10)
            return None
        return PriorityClassifier(factory, 2, settings.priority_classification_concurrency)

    with serve(priority_classification=True, priority_classifier_factory=slow_priority_classifier) as (base, _app):
        assert httpx.get(f"{base}/healthz", timeout=5).text == "ok"
        assert httpx.get(f"{base}/readyz", timeout=5).status_code == 503
        release.set()
        deadline = time.monotonic() + 5
        while httpx.get(f"{base}/readyz", timeout=5).status_code != 200 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert httpx.get(f"{base}/readyz", timeout=5).status_code == 200


def test_priority_classifier_off_is_ready_at_once(serve) -> None:
    with serve(priority_classification=False) as (base, _app):
        assert httpx.get(f"{base}/readyz", timeout=5).status_code == 200


def test_one_structured_log_line_per_request(serve, configured_db, caplog) -> None:
    _seed(configured_db, payload_hash="h-log")
    stop = threading.Event()
    stand_in = threading.Thread(target=_leg_ready_stand_in, args=(configured_db, "h-log", stop))
    caplog.set_level(logging.INFO, logger=verdict_routes.log.name)
    with serve(priority_classification=True, priority_decision=True) as (base, _app):
        stand_in.start()
        _get(base, 3000)
        _get(base, 0, secret="wrong")
        stop.set()
        stand_in.join()
    lines = [json.loads(r.getMessage()[len("verdict "):]) for r in caplog.records
             if r.name == verdict_routes.log.name and r.getMessage().startswith("verdict {")]
    assert len(lines) == 2
    decided, refused = lines
    assert decided["trace"] == _TRACE and decided["exchange"] == _EXCHANGE
    assert decided["interaction_id"] == _IID
    assert (decided["status"], decided["state"], decided["verdict"]) == (200, "decided", "block")
    assert decided["priority_classified"] is True and isinstance(decided["classify_ms"], int)
    assert isinstance(decided["waited_ms"], int)
    assert (refused["status"], refused["error"]) == (401, "unauthorized")


def test_a_long_key_with_no_bearer_writes_a_short_line(serve, caplog) -> None:
    caplog.set_level(logging.INFO, logger=verdict_routes.log.name)
    with serve() as (base, _app):
        assert _get(base, 0, trace="x" * 60000, secret=None).status_code == 401
    (line,) = [r.getMessage() for r in caplog.records if r.getMessage().startswith("verdict {")]
    assert len(line) < 300


def test_the_service_serves_the_verdict_and_nothing_else(serve, configured_db) -> None:
    _seed(configured_db)
    _write_record(configured_db)
    with serve() as (base, _app):
        assert _get(base, 0).json()["verdict"] == "block"
        assert httpx.get(f"{base}/api/traces", timeout=5).status_code == 404
        assert httpx.get(f"{base}/api/payloads/{'0' * 64}", timeout=5).status_code == 404


def test_the_full_api_does_not_serve_the_verdict(api_server, configured_db) -> None:
    r = httpx.get(f"http://{api_server.host}:{api_server.port}/enforce/v1/verdicts/{_TRACE}/{_EXCHANGE}",
                  timeout=5)
    assert r.status_code == 404
