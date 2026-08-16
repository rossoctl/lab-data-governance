"""Manifest tests for the leg-ready consumer Deployment (issues #123/#158)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

MANIFEST = Path(__file__).resolve().parents[2] / "deploy" / "k8s" / "85-leg-ready.yaml"


@pytest.fixture(scope="module")
def deployment() -> dict:
    with MANIFEST.open() as fh:
        docs = [doc for doc in yaml.safe_load_all(fh) if doc is not None]
    (dep,) = [d for d in docs if d["kind"] == "Deployment"]
    return dep


def test_single_replica_no_surge(deployment: dict) -> None:
    spec = deployment["spec"]
    assert spec["replicas"] == 1
    assert spec["strategy"]["rollingUpdate"] == {"maxSurge": 0, "maxUnavailable": 1}


def test_runs_leg_ready_on_receiver_image(deployment: dict) -> None:
    (container,) = deployment["spec"]["template"]["spec"]["containers"]
    assert container["image"] == "data-governance/receiver:latest"
    assert container["command"] == [
        "python",
        "-m",
        "data_governance.processors.leg_ready",
    ]


def test_has_migrate_init_container(deployment: dict) -> None:
    (init,) = deployment["spec"]["template"]["spec"]["initContainers"]
    assert init["command"] == ["python", "-m", "data_governance.db.migrate"]


def test_opa_base_url_points_at_the_opa_service(deployment: dict) -> None:
    """#158: the engine observer needs OPA; the manifest states the base URL
    explicitly (equal to the code default, resolving the 95-opa.yaml
    Service)."""
    (container,) = deployment["spec"]["template"]["spec"]["containers"]
    env = {e["name"]: e.get("value") for e in container["env"]}
    assert env["RISK_OPA_BASE_URL"] == "http://opa:8181"


def test_no_ports_declared(deployment: dict) -> None:
    (container,) = deployment["spec"]["template"]["spec"]["containers"]
    assert "ports" not in container
