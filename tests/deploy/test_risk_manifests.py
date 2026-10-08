"""Manifest tests for the DAS risk workloads (issues #102/#164, #158, #162).

Mirrors ``test_manifests.py``'s per-workload assertions for the risk-side
Deployments: same one-image/`command:`-override convention, migrate init
container, single-replica no-surge rollout for cursor drainers, no
Service/ports for DB consumers. Kept in a separate module so the risk
manifests' expectations grow here per issue instead of inflating the core
manifest test file.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

MANIFESTS_DIR = Path(__file__).resolve().parents[2] / "deploy" / "k8s"


def _load_all_docs() -> list[dict]:
    docs: list[dict] = []
    for path in sorted(MANIFESTS_DIR.rglob("*.yaml")):
        with path.open() as fh:
            for doc in yaml.safe_load_all(fh):
                if doc is not None:
                    docs.append(doc)
    return docs


@pytest.fixture(scope="module")
def docs() -> list[dict]:
    return _load_all_docs()


def _by_kind(docs: list[dict], kind: str, name: str) -> list[dict]:
    return [
        d
        for d in docs
        if d.get("kind") == kind and d.get("metadata", {}).get("name") == name
    ]


# ---------------------------------------------------------------------------
# Trace-risk-trigger processor Deployment (issues #102/#164)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def trace_trigger_deployment(docs: list[dict]) -> dict:
    deps = _by_kind(docs, "Deployment", "data-governance-risk-trace-trigger")
    assert len(deps) == 1, (
        "expected exactly one Deployment named data-governance-risk-trace-trigger"
    )
    return deps[0]


def test_trace_trigger_single_replica_no_surge(trace_trigger_deployment: dict) -> None:
    """Single-cursor drainer: one replica, and a rollout must tear the old pod
    down before the new one starts (no voluntary two-cursor overlap)."""
    spec = trace_trigger_deployment["spec"]
    assert spec["replicas"] == 1
    rolling = spec["strategy"]["rollingUpdate"]
    assert rolling["maxSurge"] == 0
    assert rolling["maxUnavailable"] == 1


def test_trace_trigger_runs_on_receiver_image_with_command_override(
    trace_trigger_deployment: dict,
) -> None:
    """One repo image; deployments differ only by `command:` (issue #38)."""
    (container,) = trace_trigger_deployment["spec"]["template"]["spec"]["containers"]
    assert container["image"] == "data-governance/receiver:latest"
    assert container["command"] == [
        "python",
        "-m",
        "data_governance.processors.risk.trace_trigger",
    ]


def test_trace_trigger_has_migrate_init_container(
    trace_trigger_deployment: dict,
) -> None:
    """ADR-0002: the migrate init container is the schema-ordering gate; the
    processor container itself never runs alembic."""
    pod = trace_trigger_deployment["spec"]["template"]["spec"]
    (init,) = pod["initContainers"]
    assert init["command"] == ["python", "-m", "data_governance.db.migrate"]
    (container,) = pod["containers"]
    assert "migrate" not in " ".join(container["command"])


def test_trace_trigger_has_no_service_and_no_ports(
    docs: list[dict], trace_trigger_deployment: dict
) -> None:
    """A DB consumer, not an inbound API: no Service routes to it and its
    container declares no ports (the 9095 metrics surface stays in-pod)."""
    services = [d for d in docs if d.get("kind") == "Service"]
    label = "data-governance-risk-trace-trigger"
    for svc in services:
        selector = (svc.get("spec") or {}).get("selector") or {}
        assert selector.get("app.kubernetes.io/name") != label
    (container,) = trace_trigger_deployment["spec"]["template"]["spec"]["containers"]
    assert "ports" not in container


# ---------------------------------------------------------------------------
# UI/API Deployment serves the deployed catalog (opa-policy ConfigMap)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ui_deployment(docs: list[dict]) -> dict:
    (dep,) = _by_kind(docs, "Deployment", "data-governance-ui")
    return dep


def test_ui_reads_the_rule_catalog_from_the_opa_policy_configmap(
    ui_deployment: dict,
) -> None:
    """`GET /risk/rules*` must list the catalog OPA enforces: the API pod
    mounts the same `opa-policy` ConfigMap 95-opa.yaml mounts, and
    RISK_RULES_SOURCE points at its `rules_source.json` key."""
    pod = ui_deployment["spec"]["template"]["spec"]
    (container,) = pod["containers"]
    env = {e["name"]: e.get("value") for e in container["env"]}
    source = env["RISK_RULES_SOURCE"]

    (volume,) = [v for v in pod["volumes"] if v["name"] == "policy"]
    cm = volume["configMap"]
    assert cm["name"] == "opa-policy"
    (item,) = cm["items"]
    assert item["key"] == "rules_source.json"

    (mount,) = [m for m in container["volumeMounts"] if m["name"] == volume["name"]]
    assert mount["readOnly"] is True
    assert "subPath" not in mount, "a subPath mount is never updated in place"
    assert source == f"{mount['mountPath']}/{item['path']}"


def test_ui_starts_before_the_opa_policy_configmap_exists(ui_deployment: dict) -> None:
    """deploy/dg.sh applies the manifests and waits for this rollout BEFORE
    deploy/create-opa-configmap.sh ever runs, so the mount must be optional."""
    pod = ui_deployment["spec"]["template"]["spec"]
    (volume,) = [v for v in pod["volumes"] if v["name"] == "policy"]
    assert volume["configMap"]["optional"] is True
