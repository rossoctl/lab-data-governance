"""Runs the Rego policy's own opa-test suite inside the pinned OPA image
(issue #162).

The decision policy (``data_governance/risk/rules/rego/data_governance.rego``)
is executed by a stock OPA server in production, so its tests are Rego tests
(``data_governance_test.rego``) evaluated by OPA itself — this module is the
pytest bridge that runs them, real-infrastructure style (no mocks, same
discipline as the testcontainers Postgres suites): it pulls the exact image
tag ``deploy/k8s/95-opa.yaml`` pins, mounts the repo's rules tree read-only,
and runs ``opa test`` against the SAME rules_source.json the server loads.

The manifest is the single source of truth for the OPA version — this test
reads the tag out of the YAML rather than duplicating the pin, so bumping the
deployment automatically re-validates the policy on the new version.
"""

from __future__ import annotations

import re
from pathlib import Path

import docker
import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
RULES_DIR = REPO_ROOT / "data_governance" / "risk" / "rules"
OPA_MANIFEST = REPO_ROOT / "deploy" / "k8s" / "95-opa.yaml"


def _pinned_opa_image() -> str:
    match = re.search(r"image:\s*(\S*openpolicyagent/opa:\S+)", OPA_MANIFEST.read_text())
    assert match, "95-opa.yaml must pin an openpolicyagent/opa image tag"
    image = match.group(1)
    assert not image.endswith(":latest"), "the OPA image must be version-pinned"
    return image


def test_opa_image_is_pinned() -> None:
    """The deployment pins a specific released OPA version (issue #162:
    'frozen version of opensource OPA REST server')."""
    _pinned_opa_image()


def test_rego_policy_suite_passes() -> None:
    """``opa test`` over the rego/ suite + the real rules_source.json passes
    on the pinned server image. Failure output (which test, which line) is
    surfaced through the raised ContainerError's stderr/logs."""
    client = docker.from_env()
    image = _pinned_opa_image()
    try:
        client.images.get(image)
    except docker.errors.ImageNotFound:
        client.images.pull(image)

    output = client.containers.run(
        image,
        command=[
            "test",
            "/rules/rego",
            "/rules/_policy_data/rules_source.json",
            "-v",
        ],
        volumes={str(RULES_DIR): {"bind": "/rules", "mode": "ro"}},
        remove=True,
        stdout=True,
        stderr=True,
    )
    text = output.decode()
    match = re.search(r"PASS: (\d+)/(\d+)", text)
    assert match and match.group(1) == match.group(2), text
    assert int(match.group(1)) > 0, text
