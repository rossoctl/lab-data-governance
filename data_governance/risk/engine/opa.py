"""Synchronous OPA client for the interaction risk computation engine (issue #101).

Calls OPA once per **interaction**, not per span: the issue text's "call OPA
for any span not yet evaluated" is read as "any *interaction* not yet
evaluated" (see the module docstring on migration 0013 for the full
reasoning). The interaction's span set feeds *into* the request body rather
than fanning out into per-span calls.

Built on ``httpx`` (a runtime dependency as of this issue — previously
dev-only). Retries on request failure (timeout, connection error, non-2xx)
up to ``max_retries`` additional attempts, then raises a typed error so the
caller can distinguish "OPA is unreachable/erroring" (:class:`OpaRequestError`
/ :class:`OpaTimeoutError`) from "OPA answered but the payload is
unusable" (:class:`OpaResponseError`).
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any

import httpx

from data_governance.risk import config


class OpaError(Exception):
    """Base class for every error this client raises."""


class OpaTimeoutError(OpaError):
    """OPA did not respond within the configured timeout, even after retries."""


class OpaRequestError(OpaError):
    """The HTTP request failed (connection error or non-2xx status), even
    after retries."""


class OpaResponseError(OpaError):
    """OPA responded with a 2xx status but the body could not be parsed into
    a decision (malformed JSON, missing ``result``, missing required
    field)."""


@dataclasses.dataclass(frozen=True)
class OpaDecision:
    """One parsed OPA policy decision. Mirrors
    :class:`data_governance.risk.engine.aggregate.PolicyDecision`'s fields —
    this is the raw parsed form; ``compute.py`` maps it into that dataclass
    (or persists it directly, they are field-for-field identical) rather than
    the two modules importing from each other."""

    risk_level: str
    enforcement_type: str | None
    allowed_actions: list[str]
    explanation: str | None
    triggered_rules: list[str]
    confidence: float | None
    policy_version: str | None


def _parse_decision(body: dict[str, Any]) -> OpaDecision:
    result = body.get("result")
    if not isinstance(result, dict):
        raise OpaResponseError(f"OPA response missing 'result' object: {body!r}")
    risk_level = result.get("risk_level")
    if risk_level is None:
        raise OpaResponseError(f"OPA result missing required 'risk_level': {result!r}")
    return OpaDecision(
        risk_level=risk_level,
        enforcement_type=result.get("enforcement_type"),
        allowed_actions=list(result.get("allowed_actions") or []),
        explanation=result.get("explanation"),
        triggered_rules=list(result.get("triggered_rules") or []),
        confidence=result.get("confidence"),
        policy_version=result.get("policy_version"),
    )


class OpaClient:
    """Thin synchronous wrapper over one ``httpx.Client`` for OPA decision
    calls.

    *http_client* is injected (rather than constructed internally from a base
    URL) so tests can supply an ``httpx.MockTransport``-backed client with no
    real network, and so the caller controls the client's lifecycle
    (connection pooling, close on shutdown).
    """

    def __init__(
        self,
        *,
        http_client: httpx.Client,
        decision_path: str,
        max_retries: int,
    ) -> None:
        self._http_client = http_client
        self._decision_path = decision_path
        self._max_retries = max_retries

    def evaluate(
        self,
        *,
        interaction_id: str,
        opa_input: dict[str, Any],
    ) -> OpaDecision:
        """Call OPA once for this interaction and return the parsed decision.

        *opa_input* is the schema-conformant payload built by
        :func:`data_governance.risk.engine.aggregate.build_opa_input` — this
        method adds only the ``interaction_id`` routing key alongside it
        (OPA needs to know which interaction it is evaluating, but that is
        not part of ``opa_input.schema.json`` itself, which describes the
        event being evaluated, not routing metadata) and never mutates the
        dict it was given.

        Retries up to ``max_retries`` additional times (so ``max_retries=2``
        means at most 3 attempts total) on a timeout, connection error, or
        non-2xx status. A response-parsing failure (malformed JSON, missing
        required field) is NOT retried — retrying an unparseable response
        from a reachable, responding OPA would not help.
        """
        payload = {
            "input": {
                "interaction_id": interaction_id,
                **opa_input,
            }
        }

        last_timeout: httpx.TimeoutException | None = None
        last_request_error: Exception | None = None
        attempts = self._max_retries + 1
        for attempt in range(attempts):
            try:
                response = self._http_client.post(self._decision_path, json=payload)
            except httpx.TimeoutException as exc:
                last_timeout = exc
                continue
            except httpx.HTTPError as exc:
                last_request_error = exc
                continue

            if response.status_code // 100 != 2:
                last_request_error = OpaRequestError(
                    f"OPA returned status {response.status_code}: {response.text!r}"
                )
                continue

            try:
                body = response.json()
            except (json.JSONDecodeError, ValueError) as exc:
                raise OpaResponseError(
                    f"OPA response was not valid JSON: {exc}"
                ) from exc
            return _parse_decision(body)

        if last_timeout is not None:
            raise OpaTimeoutError(
                f"OPA did not respond after {attempts} attempt(s)"
            ) from last_timeout
        raise OpaRequestError(
            f"OPA request failed after {attempts} attempt(s)"
        ) from last_request_error


def create_opa_client(
    *,
    base_url: str | None = None,
    decision_path: str | None = None,
    timeout_seconds: float | None = None,
    max_retries: int | None = None,
) -> OpaClient:
    """Build an :class:`OpaClient` wired to a real ``httpx.Client``, reading
    defaults from :mod:`data_governance.risk.config`. This is what
    production callers of ``compute_interaction_risk`` use; tests that need
    control over the transport construct an :class:`OpaClient` directly.
    """
    http_client = httpx.Client(
        base_url=base_url if base_url is not None else config.OPA_BASE_URL,
        timeout=(
            timeout_seconds if timeout_seconds is not None else config.OPA_TIMEOUT_SECONDS
        ),
    )
    return OpaClient(
        http_client=http_client,
        decision_path=decision_path if decision_path is not None else config.OPA_DECISION_PATH,
        max_retries=max_retries if max_retries is not None else config.OPA_MAX_RETRIES,
    )
