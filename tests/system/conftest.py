"""Fixtures for the system tests: the OTLP harness (real receiver + Postgres),
and one real OPA with the suite's one rule (T-SENS)."""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest

from data_governance.risk.engine.opa import create_opa_client
from data_governance.risk.rules.rego import compile_policy

# The real-OPA container fixture of the rules suite, reused as is.
from tests.risk.rules.conftest import opa_base_url  # noqa: F401

from . import rules


@pytest.fixture(scope="session")
def trunk_opa(opa_base_url: str) -> Iterator[object]:
    """The suite's policy (one rule, T-SENS) compiled to Rego and loaded into the
    session's OPA; yields an OpaClient aimed at it."""
    rego = compile_policy(rules.policy())
    with httpx.Client(base_url=opa_base_url, timeout=5.0) as http:
        r = http.put("/v1/policies/data_governance", content=rego, headers={"Content-Type": "text/plain"})
        r.raise_for_status()
        client = create_opa_client(base_url=opa_base_url)
        try:
            yield client
        finally:
            http.delete("/v1/policies/data_governance")

