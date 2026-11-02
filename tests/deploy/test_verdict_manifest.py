"""Manifest tests for the verdict service (deploy/k8s/45-verdict.yaml)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

MANIFEST = Path(__file__).resolve().parents[2] / "deploy" / "k8s" / "45-verdict.yaml"
NETPOL = Path(__file__).resolve().parents[2] / "deploy" / "k8s" / "50-networkpolicy.yaml"


@pytest.fixture(scope="module")
def docs() -> list[dict]:
    with MANIFEST.open() as fh:
        return [doc for doc in yaml.safe_load_all(fh) if doc is not None]


@pytest.fixture(scope="module")
def deployment(docs) -> dict:
    (dep,) = [d for d in docs if d["kind"] == "Deployment"]
    return dep


@pytest.fixture(scope="module")
def container(deployment) -> dict:
    (c,) = deployment["spec"]["template"]["spec"]["containers"]
    return c


def _env(container: dict) -> dict:
    return {e["name"]: e for e in container["env"]}


def test_two_replicas_never_both_down(deployment) -> None:
    spec = deployment["spec"]
    assert spec["replicas"] == 2
    assert spec["strategy"]["rollingUpdate"] == {"maxSurge": 1, "maxUnavailable": 0}


def test_runs_the_verdict_app_from_the_classification_image(container) -> None:
    """Priority classification needs the model, so the verdict runs where the model is."""
    assert container["image"] == "data-governance/classification:latest"
    assert container["command"] == ["python", "-m", "data_governance.risk.api.verdict_app"]


def test_the_secret_comes_from_the_dg_verdict_secret(container) -> None:
    ref = _env(container)["VERDICT_SECRET"]["valueFrom"]["secretKeyRef"]
    assert ref == {"name": "dg-verdict-secret", "key": "secret"}, "not optional: no secret, no pod"


def test_the_knobs_are_explicit(container) -> None:
    env = {k: v.get("value") for k, v in _env(container).items()}
    assert env["VERDICT_PRIORITY_CLASSIFICATION"] == "on"
    assert env["VERDICT_PRIORITY_DECISION"] == "on"
    assert env["VERDICT_MAX_WAITERS"] == "64"
    assert env["VERDICT_PRIORITY_CLASSIFICATION_CONCURRENCY"] == "1"
    assert env["VERDICT_READ_WORKERS"] == "4"
    assert env["DB_POOL_TIMEOUT"] == "3", "a read fails inside the gate's wait"
    assert env["OMP_NUM_THREADS"] == "2"


def test_the_pool_matches_the_bounded_workers(container) -> None:
    from data_governance.risk.api.verdict_routes import Settings

    env = {k: v.get("value") for k, v in _env(container).items()}
    settings = Settings.from_env({k: v for k, v in env.items() if v is not None and k.startswith("VERDICT_")})
    assert int(env["DB_POOL_MAX_SIZE"]) == settings.pool_size == 6


def test_reads_no_risk_settings(container) -> None:
    """It never calls OPA nor writes a record: leg-ready's RISK_* do not apply here."""
    assert not [name for name in _env(container) if name.startswith("RISK_")]


def test_resources(container) -> None:
    res = container["resources"]
    assert res["requests"] == {"cpu": "400m", "memory": "1Gi"}
    assert res["limits"] == {"cpu": "2", "memory": "3Gi"}


def test_ready_only_after_warm(container) -> None:
    assert container["readinessProbe"]["httpGet"]["path"] == "/readyz"
    assert container["livenessProbe"]["httpGet"]["path"] == "/healthz"


def test_service_is_clusterip_on_8080(docs) -> None:
    (svc,) = [d for d in docs if d["kind"] == "Service"]
    assert svc["spec"]["type"] == "ClusterIP"
    assert [p["port"] for p in svc["spec"]["ports"]] == [8080]
    assert svc["metadata"]["name"] == "data-governance-verdict"


def test_a_stopping_pod_outlives_its_route(container) -> None:
    assert container["lifecycle"]["preStop"]["exec"]["command"] == ["sleep", "5"]


def test_the_policy_admits_workload_namespaces_to_8080_only(docs) -> None:
    (pol,) = [d for d in docs if d["kind"] == "NetworkPolicy"]
    assert pol["spec"]["podSelector"]["matchLabels"] == {"app.kubernetes.io/name": "data-governance-verdict"}
    (rule,) = pol["spec"]["ingress"]
    assert rule["from"] == [{"namespaceSelector": {"matchLabels": {"rossoctl-enabled": "true"}}}]
    assert rule["ports"] == [{"port": 8080, "protocol": "TCP"}]


def test_the_full_api_stays_platform_only() -> None:
    """Admitting workloads to the verdict must not have widened the UI's policy."""
    with NETPOL.open() as fh:
        policies = [d for d in yaml.safe_load_all(fh) if d and d["kind"] == "NetworkPolicy"]
    (ui,) = [p for p in policies if p["metadata"]["name"] == "data-governance-ui-ingress"]
    for rule in ui["spec"]["ingress"]:
        for peer in rule.get("from", []):
            assert peer.get("namespaceSelector", {}).get("matchLabels") != {"rossoctl-enabled": "true"}


def test_leg_ready_runs_the_priority_decision_arm() -> None:
    path = MANIFEST.parent / "85-leg-ready.yaml"
    with path.open() as fh:
        (dep,) = [d for d in yaml.safe_load_all(fh) if d and d["kind"] == "Deployment"]
    (c,) = dep["spec"]["template"]["spec"]["containers"]
    env = {e["name"]: e.get("value") for e in c["env"]}
    assert env["LEG_READY_PRIORITY_DECISION"] == "on"
