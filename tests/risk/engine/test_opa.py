"""Tests for the synchronous OPA client (issue #101).

``data_governance.risk.engine.opa`` calls OPA once per **interaction** (not
per span — the interaction risk engine's span set feeds *into* the request
rather than fanning out into per-span calls). Tested against
``httpx.MockTransport`` — httpx's own test seam — so there is no real OPA
and no real network, matching this repo's "no mocking of Postgres, but
Postgres is the only thing we don't mock" posture (OPA is an external HTTP
dependency, not the DB under test).

The request body's ``input`` is whatever :func:`utils.build_opa_input`
produced (schema-conformant, tested exhaustively in ``test_utils.py``) —
``evaluate`` itself only adds the ``interaction_id`` routing key alongside it
and is not responsible for the mapping's correctness, so these tests treat
the ``opa_input`` dict as an opaque payload and focus on request/response/
retry mechanics.
"""

from __future__ import annotations

import httpx
import pytest

from data_governance.risk import config as risk_config
from data_governance.risk.engine.opa import (
    OpaClient,
    OpaRequestError,
    OpaResponseError,
    OpaTimeoutError,
    create_opa_client,
)


def _client(handler, *, max_retries: int = 2) -> OpaClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(
        base_url="http://opa-test:8181", transport=transport
    )
    return OpaClient(
        http_client=http_client,
        decision_path="/v1/data/data_governance/policy_decision",
        max_retries=max_retries,
    )


def _full_decision_body() -> dict:
    return {
        "result": {
            "risk_level": "high",
            "enforcement_type": "block",
            "allowed_actions": ["retry"],
            "explanation": "PII sent externally",
            "triggered_rules": ["r1", "r2"],
            "confidence": 0.87,
            "policy_version": "3",
        }
    }


def _opa_input(**overrides) -> dict:
    defaults = {
        "data_items": [],
        "requested_actions": [],
    }
    defaults.update(overrides)
    return defaults


# --- request shape ---------------------------------------------------------


def test_request_includes_interaction_id_alongside_the_opa_input():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = __import__("json").loads(request.content)
        captured["url"] = str(request.url)
        return httpx.Response(200, json=_full_decision_body())

    client = _client(handler)
    opa_input = _opa_input(requested_actions=["send"])
    client.evaluate(interaction_id="ix1", opa_input=opa_input)

    assert "/v1/data/data_governance/policy_decision" in captured["url"]
    body = captured["json"]["input"]
    assert body["interaction_id"] == "ix1"
    assert body["requested_actions"] == ["send"]


def test_request_does_not_mutate_the_passed_in_opa_input():
    opa_input = _opa_input()
    client = _client(lambda r: httpx.Response(200, json=_full_decision_body()))
    client.evaluate(interaction_id="ix1", opa_input=opa_input)
    assert opa_input == _opa_input()


# --- response parsing --------------------------------------------------------


def test_parses_full_decision_response():
    client = _client(lambda r: httpx.Response(200, json=_full_decision_body()))
    decision = client.evaluate(interaction_id="ix1", opa_input=_opa_input())
    assert decision.risk_level == "high"
    assert decision.enforcement_type == "block"
    assert decision.allowed_actions == ["retry"]
    assert decision.explanation == "PII sent externally"
    assert decision.triggered_rules == ["r1", "r2"]
    assert decision.confidence == 0.87
    assert decision.policy_version == "3"


def test_missing_optional_fields_default_sensibly():
    """Only risk_level is required in the result; every other field is
    optional and must default without raising."""
    client = _client(
        lambda r: httpx.Response(200, json={"result": {"risk_level": "low"}})
    )
    decision = client.evaluate(interaction_id="ix1", opa_input=_opa_input())
    assert decision.risk_level == "low"
    assert decision.enforcement_type is None
    assert decision.allowed_actions == []
    assert decision.explanation is None
    assert decision.triggered_rules == []
    assert decision.confidence is None
    assert decision.policy_version is None


def test_missing_result_key_raises_response_error():
    client = _client(lambda r: httpx.Response(200, json={}))
    with pytest.raises(OpaResponseError):
        client.evaluate(interaction_id="ix1", opa_input=_opa_input())


def test_missing_risk_level_raises_response_error():
    client = _client(lambda r: httpx.Response(200, json={"result": {}}))
    with pytest.raises(OpaResponseError):
        client.evaluate(interaction_id="ix1", opa_input=_opa_input())


def test_malformed_json_raises_response_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json{{{")

    client = _client(handler)
    with pytest.raises(OpaResponseError):
        client.evaluate(interaction_id="ix1", opa_input=_opa_input())


# --- HTTP error handling -----------------------------------------------------


def test_non_200_raises_request_error_after_exhausting_retries():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500, text="internal error")

    client = _client(handler, max_retries=2)
    with pytest.raises(OpaRequestError):
        client.evaluate(interaction_id="ix1", opa_input=_opa_input())
    assert calls["n"] == 3  # initial attempt + 2 retries


def test_timeout_raises_opa_timeout_error_after_exhausting_retries():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.TimeoutException("timed out")

    client = _client(handler, max_retries=1)
    with pytest.raises(OpaTimeoutError):
        client.evaluate(interaction_id="ix1", opa_input=_opa_input())
    assert calls["n"] == 2  # initial attempt + 1 retry


def test_retry_then_succeed():
    """The first call fails, the retry succeeds — the client must not raise
    and must return the successful decision."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, text="unavailable")
        return httpx.Response(200, json=_full_decision_body())

    client = _client(handler, max_retries=2)
    decision = client.evaluate(interaction_id="ix1", opa_input=_opa_input())
    assert decision.risk_level == "high"
    assert calls["n"] == 2


def test_no_retries_configured_raises_on_first_failure():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500)

    client = _client(handler, max_retries=0)
    with pytest.raises(OpaRequestError):
        client.evaluate(interaction_id="ix1", opa_input=_opa_input())
    assert calls["n"] == 1


# --- create_opa_client factory --------------------------------------------------


def test_create_opa_client_returns_an_opa_client():
    client = create_opa_client()
    assert isinstance(client, OpaClient)


def test_create_opa_client_uses_config_defaults(monkeypatch: pytest.MonkeyPatch):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        assert request.url.path == risk_config.OPA_DECISION_PATH
        return httpx.Response(
            200,
            json={
                "result": {
                    "risk_level": "low",
                    "enforcement_type": None,
                    "allowed_actions": [],
                    "explanation": None,
                    "triggered_rules": [],
                    "confidence": None,
                    "policy_version": None,
                }
            },
        )

    real_client_cls = httpx.Client
    monkeypatch.setattr(
        "data_governance.risk.engine.opa.httpx.Client",
        lambda **kwargs: real_client_cls(
            base_url=kwargs["base_url"],
            timeout=kwargs["timeout"],
            transport=httpx.MockTransport(handler),
        ),
    )
    client = create_opa_client()
    decision = client.evaluate(interaction_id="ix1", opa_input=_opa_input())
    assert decision.risk_level == "low"
    assert calls["n"] == 1


def test_create_opa_client_honors_max_retries_override():
    client = create_opa_client(max_retries=0)
    assert client._max_retries == 0


def test_create_opa_client_honors_decision_path_override():
    client = create_opa_client(decision_path="/v1/data/custom/path")
    assert client._decision_path == "/v1/data/custom/path"
