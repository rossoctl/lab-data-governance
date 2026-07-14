"""Deployment manifest for the P-classification processor (issue #77).

A DB consumer, not an inbound API — same shape as the P-interactions Deployment
(``deploy/k8s/70-interactions.yaml``): the shared receiver image, ``command:``
overridden to the classification entry point, no Service and no container ports,
single replica against one shared ``processor_state`` cursor (the ``classification``
row) with a maxSurge:0 rollout. The stub ships in the shared image; torch + the
NER model arrive in a later slice as a separate image (ADR-0022/0023).

These mirror the interactions-deployment assertions in ``test_manifests.py`` and
pin the deliberate parallels/differences. The generic manifest-wide checks
(placeholder ordering, apiVersion/kind/name presence) in ``test_manifests.py``
already sweep this file via ``rglob('*.yaml')``.
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


def _by_kind(docs: list[dict], kind: str, name: str | None = None) -> list[dict]:
    out = [d for d in docs if d.get("kind") == kind]
    if name is not None:
        out = [d for d in out if d.get("metadata", {}).get("name") == name]
    return out


def _iter_containers(pod_spec: dict):
    for ic in pod_spec.get("initContainers") or []:
        yield "initContainer", ic
    for c in pod_spec.get("containers") or []:
        yield "container", c


@pytest.fixture(scope="module")
def docs() -> list[dict]:
    return _load_all_docs()


@pytest.fixture(scope="module")
def classification_deployment(docs: list[dict]) -> dict:
    deps = _by_kind(docs, "Deployment", "data-governance-classification")
    assert len(deps) == 1, (
        "expected exactly one Deployment named data-governance-classification"
    )
    return deps[0]


def test_single_replica(classification_deployment: dict) -> None:
    """Single-cursor drainer: must NOT run two concurrent pods.

    The shared driver advances one ``processor_state`` cursor (the
    ``classification`` row) with no inter-pod lock; two replicas would
    double-process payloads. What keeps a brief overlap safe is the write-once,
    idempotent per-payload write (ADR-0024/ADR-0007), not the replica count.
    """
    assert classification_deployment["spec"]["replicas"] == 1


def test_rollout_does_not_surge_a_second_pod(classification_deployment: dict) -> None:
    """maxSurge:0 + maxUnavailable:1 tears the old pod down before the new one
    starts, so a voluntary rollout never runs two pods against the one cursor —
    the same rollout strategy the interactions processor uses."""
    strategy = classification_deployment["spec"].get("strategy") or {}
    assert strategy.get("type", "RollingUpdate") == "RollingUpdate"
    ru = strategy.get("rollingUpdate") or {}
    assert ru.get("maxSurge") == 0
    assert ru.get("maxUnavailable") == 1


def test_has_init_container_running_migrate(classification_deployment: dict) -> None:
    """ADR-0002: init container runs the migrate CLI to head, like the receiver."""
    pod_spec = classification_deployment["spec"]["template"]["spec"]
    init_containers = pod_spec.get("initContainers") or []
    assert init_containers, "pod must declare an init container running migrate"
    migrate_cmds = [
        " ".join((ic.get("command") or []) + (ic.get("args") or []))
        for ic in init_containers
    ]
    assert any("data_governance.db.migrate" in c for c in migrate_cmds), (
        f"init container must invoke migrate, got {migrate_cmds!r}"
    )


def test_main_container_runs_classification_entrypoint(
    classification_deployment: dict,
) -> None:
    """Main container runs ``python -m data_governance.processors.classification``."""
    pod_spec = classification_deployment["spec"]["template"]["spec"]
    containers = pod_spec.get("containers") or []
    assert containers, "Deployment must have at least one container"
    main = containers[0]
    cmd = " ".join((main.get("command") or []) + (main.get("args") or []))
    assert "data_governance.processors.classification" in cmd, (
        f"main container must invoke the classification entry point, got {cmd!r}"
    )


def test_main_container_does_not_run_alembic(classification_deployment: dict) -> None:
    """PROJECT.md §3: the processor container itself does NOT run Alembic."""
    pod_spec = classification_deployment["spec"]["template"]["spec"]
    main = pod_spec["containers"][0]
    cmd = " ".join((main.get("command") or []) + (main.get("args") or []))
    assert "data_governance.db.migrate" not in cmd
    assert "alembic" not in cmd.lower()


def test_runs_on_receiver_image(classification_deployment: dict) -> None:
    """No new image build for the stub (ADR-0022 defers the torch image): init +
    main both run the shared receiver image."""
    pod_spec = classification_deployment["spec"]["template"]["spec"]
    init = pod_spec["initContainers"][0]
    main = pod_spec["containers"][0]
    for ctr in (init, main):
        assert ctr.get("image") == "data-governance/receiver:latest", (
            f"{ctr.get('name')!r} must run the shared receiver image, got "
            f"{ctr.get('image')!r}"
        )
        assert ctr.get("imagePullPolicy") == "IfNotPresent"


def test_database_url_set(classification_deployment: dict) -> None:
    """The processor and migrate CLIs read ``DATABASE_URL``."""
    pod_spec = classification_deployment["spec"]["template"]["spec"]
    init = pod_spec["initContainers"][0]
    main = pod_spec["containers"][0]
    for ctr in (init, main):
        env = {e["name"]: e for e in ctr.get("env") or []}
        assert "DATABASE_URL" in env, f"{ctr['name']} must set DATABASE_URL"


def test_has_no_service(docs: list[dict]) -> None:
    """A poll/LISTEN DB consumer with no inbound API — nothing routes at it."""
    svcs = _by_kind(docs, "Service", "data-governance-classification")
    assert not svcs, (
        "classification processor must NOT declare a Service — no inbound API"
    )


def test_declares_no_container_ports(classification_deployment: dict) -> None:
    """No container ports: nothing inbound to declare. The in-pod /metrics
    surface stays undeclared/unexposed in v1, like the interactions 9091."""
    pod_spec = classification_deployment["spec"]["template"]["spec"]
    for ctr_kind, ctr in _iter_containers(pod_spec):
        assert not (ctr.get("ports") or []), (
            f"classification {ctr_kind} {ctr.get('name')!r} must declare no ports"
        )


def test_does_not_override_metrics_port(classification_deployment: dict) -> None:
    """Production leaves ``CLASSIFICATION_METRICS_PORT`` unset so the processor
    serves /metrics on its collision-free code default (9092, distinct from the
    receiver's 9090 and the interactions 9091; issue #81).

    The env override exists only so tests can run co-located processors without
    colliding — a manifest that pinned it risks silently re-introducing a
    collision, so we assert the main container never sets it.
    """
    main = classification_deployment["spec"]["template"]["spec"]["containers"][0]
    env_names = {e["name"] for e in main.get("env") or []}
    assert "CLASSIFICATION_METRICS_PORT" not in env_names, (
        "classification deployment must not pin the metrics port — production "
        "uses the collision-free code default 9092"
    )
