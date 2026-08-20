"""Shared fixtures for the real-OPA test suite (issue #173).

Mirrors ``tests/conftest.py``'s Postgres-container conventions
(``DOCKER_HOST``/``TESTCONTAINERS_RYUK_DISABLED`` defaults set before
testcontainers imports its docker client; a session-scoped container so the
whole ``-m opa`` run pays one container-start cost, not one per test) for a
generic ``openpolicyagent/opa:latest`` container instead of Postgres —
``testcontainers`` has no dedicated OPA wrapper, so this uses the generic
``DockerContainer`` and waits on OPA's own startup log line rather than a
readiness probe endpoint.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import httpx
import pytest
from testcontainers.core.container import DockerContainer
from testcontainers.core.waiting_utils import wait_for_logs

# Same defaults as tests/conftest.py, set before testcontainers touches its
# docker client. Honors an existing DOCKER_HOST (e.g. CI).
_uid = os.getuid()
os.environ.setdefault("DOCKER_HOST", f"unix:///run/user/{_uid}/podman/podman.sock")
os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")

_OPA_PORT = 8181


@pytest.fixture(scope="session")
def opa_base_url() -> Iterator[str]:
    """One OPA container per test session, started with its REST server on
    the default port. Yields the container's reachable base URL
    (``http://<host>:<mapped-port>``)."""
    container = (
        DockerContainer("docker.io/openpolicyagent/opa:latest")
        .with_command(f"run --server --addr :{_OPA_PORT}")
        .with_exposed_ports(_OPA_PORT)
    )
    with container:
        wait_for_logs(container, "Initializing server", timeout=30)
        host = container.get_container_host_ip()
        port = container.get_exposed_port(_OPA_PORT)
        base_url = f"http://{host}:{port}"
        # wait_for_logs confirms the log line was written, not that the
        # listener actually accepts connections yet on every runtime; poll
        # briefly so the first real test doesn't race a connection refused.
        with httpx.Client(timeout=1.0) as client:
            for _ in range(30):
                try:
                    client.get(f"{base_url}/health")
                    break
                except httpx.TransportError:
                    import time

                    time.sleep(0.2)
        yield base_url


@pytest.fixture()
def opa_client(opa_base_url: str) -> Iterator[httpx.Client]:
    """An HTTP client for the session-scoped OPA container, with the one
    policy this test PUTs cleaned up afterward.

    Every compiled Rego module declares ``package data_governance`` (a
    compile-time constant — see ``rego.py``'s ``_PACKAGE``), so two tests
    both PUTting a policy under the *same* container without cleanup
    collide on OPA's shared package namespace: OPA identifies rules by
    ``package``, not by the policy id in the PUT path, so a second
    ``data_governance`` module gets merged with the first (multiple
    ``default policy_decision`` blocks) rather than replacing it — the
    exact "multiple default rules" error hit while hand-verifying this
    compiler. Deleting this test's policy id after each test, rather than
    giving every test a distinct package name, keeps the compiler's single
    hardcoded package name realistic (that's what it will emit in
    production) while still isolating tests from each other.
    """
    policy_id = "data_governance"
    with httpx.Client(base_url=opa_base_url, timeout=5.0) as client:
        try:
            yield client
        finally:
            client.delete(f"/v1/policies/{policy_id}")
