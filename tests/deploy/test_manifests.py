"""Schema and policy assertions for the v1 Kubernetes manifests (issue #16).

PROJECT.md §1 / §7 pin the deployment topology tightly: 2-replica receiver
Deployment, ClusterIP Service exposing OTLP gRPC (4317) + OTLP HTTP (4318) +
the receiver's HTTP surface for ``/healthz``, Postgres
StatefulSet in the same namespace, UI backend Deployment + Service, and a
NetworkPolicy restricting ingress to the Kagenti namespace.

These tests exercise structural invariants that are easy to break by hand-
edit and load-bearing for the v1 deployment. They do NOT spin up a real
cluster — the "applies cleanly to a fresh cluster" and "OTLP span lands and
surfaces in UI" acceptance criteria need a live cluster and live receiver
and are exercised manually for now (see deploy/k8s/README.md).

What they DO check:

- Every YAML doc parses, carries ``apiVersion`` / ``kind`` / ``metadata.name``.
- The receiver Deployment has 2 replicas and the init-container shape PROJECT.md
  §3 calls for (``python -m data_governance.db.migrate`` to completion before
  the main container starts).
- The receiver Service exposes 4317 / 4318 / 9090 on ClusterIP, and the
  receiver container declares matching ``containerPort`` entries — flipping
  either side of that pairing must fail the test (issue #36).
- The Postgres StatefulSet lives in the same namespace as the receiver.
- The UI backend Deployment + Service are present and wired to port 8080.
- The NetworkPolicy targets receiver + UI workloads, allows ingress only from
  the Kagenti namespace, and covers every port the listed services expose.
- Probe paths align with the §3.1 ingest blocklist already shipped in #9 — a
  drift here would silently flood ``spans`` with probe rows.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from data_governance.processors.otlp_receiver import blocklist


MANIFESTS_DIR = Path(__file__).resolve().parents[2] / "deploy" / "k8s"

# `$(VAR)` matches an unescaped substitution. The negative lookbehind keeps us
# from matching the literal-`$(...)` escape sequence `$$(VAR)` documented at
# https://kubernetes.io/docs/tasks/inject-data-application/define-interdependent-environment-variables/.
_PLACEHOLDER_RE = re.compile(r"(?<!\$)\$\(([A-Za-z_][A-Za-z0-9_]*)\)")


def _load_all_docs() -> list[dict]:
    """Load every YAML document under ``deploy/k8s/`` as a list of dicts.

    Multi-document files (``---`` separated) are flattened. Empty docs are
    dropped — ``yaml.safe_load_all`` yields ``None`` for them.
    """
    docs: list[dict] = []
    for path in sorted(MANIFESTS_DIR.rglob("*.yaml")):
        with path.open() as fh:
            for doc in yaml.safe_load_all(fh):
                if doc is None:
                    continue
                assert isinstance(doc, dict), (
                    f"{path}: top-level YAML doc must be a mapping, got {type(doc).__name__}"
                )
                docs.append(doc)
    return docs


@pytest.fixture(scope="module")
def docs() -> list[dict]:
    return _load_all_docs()


def _by_kind(docs: list[dict], kind: str, name: str | None = None) -> list[dict]:
    out = [d for d in docs if d.get("kind") == kind]
    if name is not None:
        out = [d for d in out if d.get("metadata", {}).get("name") == name]
    return out


def _iter_pod_specs(docs: list[dict]):
    """Yield ``(doc_kind, doc_name, pod_spec_path, pod_spec)`` for every workload.

    Covers Deployment / StatefulSet / DaemonSet / Job / CronJob — anything
    whose ``spec.template.spec`` (or, for CronJob, ``jobTemplate.spec.template.spec``)
    holds a Pod spec.
    """
    for doc in docs:
        kind = doc.get("kind")
        name = (doc.get("metadata") or {}).get("name", "<unnamed>")
        if kind in ("Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Job"):
            pod_spec = (
                ((doc.get("spec") or {}).get("template") or {}).get("spec")
            )
            if pod_spec:
                yield kind, name, "spec.template.spec", pod_spec
        elif kind == "CronJob":
            pod_spec = (
                ((((doc.get("spec") or {}).get("jobTemplate") or {}).get("spec") or {})
                 .get("template") or {}).get("spec")
            )
            if pod_spec:
                yield kind, name, "spec.jobTemplate.spec.template.spec", pod_spec
        elif kind == "Pod":
            pod_spec = doc.get("spec")
            if pod_spec:
                yield kind, name, "spec", pod_spec


def _iter_containers(pod_spec: dict):
    """Yield ``(container_kind, container_dict)`` for init + main containers."""
    for ic in pod_spec.get("initContainers") or []:
        yield "initContainer", ic
    for c in pod_spec.get("containers") or []:
        yield "container", c


# ---------------------------------------------------------------------------
# Manifest discovery + parsing
# ---------------------------------------------------------------------------


def test_manifests_directory_exists() -> None:
    assert MANIFESTS_DIR.is_dir(), f"missing {MANIFESTS_DIR}"


def test_manifests_present(docs: list[dict]) -> None:
    """We require at least one of every kind PROJECT.md §1 / §7 pins."""
    assert _by_kind(docs, "Namespace"), "expected a Namespace manifest"
    assert _by_kind(docs, "Deployment"), "expected at least one Deployment"
    assert _by_kind(docs, "StatefulSet"), "expected the Postgres StatefulSet"
    assert _by_kind(docs, "Service"), "expected at least one Service"
    assert _by_kind(docs, "NetworkPolicy"), "expected the NetworkPolicy"


def test_every_doc_has_apiversion_kind_name(docs: list[dict]) -> None:
    """Catches truncated / malformed manifests early."""
    for d in docs:
        assert d.get("apiVersion"), f"missing apiVersion: {d!r}"
        assert d.get("kind"), f"missing kind: {d!r}"
        meta = d.get("metadata") or {}
        assert meta.get("name"), f"missing metadata.name: {d!r}"


# ---------------------------------------------------------------------------
# Receiver Deployment
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def receiver_deployment(docs: list[dict]) -> dict:
    deps = _by_kind(docs, "Deployment", "data-governance-receiver")
    assert len(deps) == 1, "expected exactly one Deployment named data-governance-receiver"
    return deps[0]


def test_receiver_replica_count(receiver_deployment: dict) -> None:
    """PROJECT.md §1: 2 replicas behind a ClusterIP Service."""
    assert receiver_deployment["spec"]["replicas"] == 2


def test_receiver_has_init_container_running_migrate(receiver_deployment: dict) -> None:
    """PROJECT.md §3 / ADR-0002: init container runs the migrate CLI to head."""
    pod_spec = receiver_deployment["spec"]["template"]["spec"]
    init_containers = pod_spec.get("initContainers") or []
    assert init_containers, "receiver pod must declare an init container running migrate"
    # k8s gates the main container on every initContainer reaching completion;
    # the AC "init container completion gates main container start" reduces to
    # "there is an initContainer running the migrate CLI."
    migrate_cmds = []
    for ic in init_containers:
        cmd = ic.get("command") or []
        args = ic.get("args") or []
        migrate_cmds.append(" ".join(cmd + args))
    assert any(
        "data_governance.db.migrate" in c for c in migrate_cmds
    ), f"init container must invoke `python -m data_governance.db.migrate`, got {migrate_cmds!r}"


def test_receiver_main_container_runs_receiver(receiver_deployment: dict) -> None:
    """The receiver container itself runs ``python -m data_governance.processors.otlp_receiver``."""
    pod_spec = receiver_deployment["spec"]["template"]["spec"]
    containers = pod_spec.get("containers") or []
    assert containers, "receiver Deployment must have at least one container"
    main = containers[0]
    cmd = " ".join((main.get("command") or []) + (main.get("args") or []))
    assert "data_governance.processors.otlp_receiver" in cmd, (
        f"main container must invoke the receiver entry point, got {cmd!r}"
    )


def test_receiver_main_container_does_not_run_alembic(receiver_deployment: dict) -> None:
    """PROJECT.md §3: the receiver container itself does NOT run Alembic."""
    pod_spec = receiver_deployment["spec"]["template"]["spec"]
    main = pod_spec["containers"][0]
    cmd = " ".join((main.get("command") or []) + (main.get("args") or []))
    assert "data_governance.db.migrate" not in cmd, (
        "main container must not run `migrate`; that is the init container's job"
    )
    assert "alembic" not in cmd.lower(), "main container must not invoke alembic"


def test_receiver_probes_target_healthz(receiver_deployment: dict) -> None:
    """Liveness + readiness on ``GET /healthz`` (PROJECT.md §5.1)."""
    pod_spec = receiver_deployment["spec"]["template"]["spec"]
    main = pod_spec["containers"][0]
    for probe_name in ("livenessProbe", "readinessProbe"):
        probe = main.get(probe_name)
        assert probe, f"{probe_name} required on receiver container"
        http = probe.get("httpGet")
        assert http, f"{probe_name} must use httpGet"
        assert http.get("path") == "/healthz", f"{probe_name} path must be /healthz"
        # The receiver serves /healthz on the HTTP/protobuf port (4318).
        assert http.get("port") in (4318, "otlp-http"), (
            f"{probe_name} must target the HTTP/protobuf port 4318 (or its 'otlp-http' name)"
        )


def test_receiver_probe_path_is_on_blocklist() -> None:
    """The §3.1 blocklist already drops ``/healthz`` probe spans (issue #9).

    If a future refactor moves the probe to a different path, that path's
    spans would silently land in ``spans``. Pinning this regression keeps
    the probe path and the blocklist tied together.
    """
    assert blocklist.match("/healthz") is not None, (
        "blocklist must drop /healthz probe spans; otherwise probe traffic floods spans"
    )
    assert blocklist.match("GET /healthz") is not None, (
        "blocklist must also drop the OTel HTTP-instrumentation `GET /healthz` shape"
    )


def test_receiver_database_url_set(receiver_deployment: dict) -> None:
    """The receiver and migrate CLIs read ``DATABASE_URL`` (see __main__.py / migrate.py)."""
    pod_spec = receiver_deployment["spec"]["template"]["spec"]
    init = pod_spec["initContainers"][0]
    main = pod_spec["containers"][0]
    for ctr in (init, main):
        env = {e["name"]: e for e in ctr.get("env") or []}
        assert "DATABASE_URL" in env, f"{ctr['name']} must set DATABASE_URL"


# ---------------------------------------------------------------------------
# Receiver Service
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def receiver_service(docs: list[dict]) -> dict:
    svcs = _by_kind(docs, "Service", "data-governance-receiver")
    assert len(svcs) == 1, "expected exactly one Service named data-governance-receiver"
    return svcs[0]


def test_receiver_service_is_clusterip(receiver_service: dict) -> None:
    """PROJECT.md §1 / §7: cluster-internal exposure only in v1."""
    assert receiver_service["spec"].get("type", "ClusterIP") == "ClusterIP"


def test_receiver_service_exposes_otlp_and_http_surface(
    receiver_service: dict, receiver_deployment: dict
) -> None:
    """OTLP gRPC 4317, OTLP HTTP/protobuf + /healthz on 4318, /metrics on 9090.

    Issue #36 wired ``MetricsServer`` into the receiver entry point, so the
    v1 manifest re-adds 9090 to the Service and the receiver container.
    Both halves of that pairing must hold: a Service port without a
    matching ``containerPort`` would route scrape traffic to a closed
    socket; a ``containerPort`` without a Service port would expose the
    surface only to in-pod traffic.
    """
    ports = {p["port"]: p for p in receiver_service["spec"]["ports"]}
    assert 4317 in ports, "Service must expose OTLP gRPC on 4317"
    assert 4318 in ports, "Service must expose OTLP HTTP/protobuf + /healthz on 4318"
    assert 9090 in ports, "Service must expose Prometheus /metrics on 9090"

    # Pin the Service-port-vs-containerPort pairing for /metrics. Removing
    # the containerPort declaration on the receiver container without also
    # removing the Service port would silently route scrape traffic at a
    # closed socket — the failure mode that motivated the temporary strip
    # in PR #34 in the first place.
    main = receiver_deployment["spec"]["template"]["spec"]["containers"][0]
    container_ports = {p["containerPort"] for p in main.get("ports") or []}
    assert 9090 in container_ports, (
        "receiver container must declare containerPort 9090 to back the "
        "Service's /metrics port"
    )


def test_receiver_service_grpc_port_is_grpc(receiver_service: dict) -> None:
    """gRPC ports should declare protocol TCP and an appName/appProtocol cue."""
    grpc_port = next(p for p in receiver_service["spec"]["ports"] if p["port"] == 4317)
    # Protocol TCP is the default; explicit is fine, missing is fine. We assert
    # name presence so collector configs can target by name.
    assert grpc_port.get("name"), "OTLP gRPC port should be named"


# ---------------------------------------------------------------------------
# P-interactions processor Deployment (issue #74)
# ---------------------------------------------------------------------------
#
# A DB consumer, not an inbound API: same image as the receiver, command
# overridden to the interactions entry point, no Service and no container
# ports. Single replica because the driver drains against one shared
# `processor_state` cursor with no inter-pod lock (driver.py) — two pods would
# double-process. The assertions below mirror the receiver's
# init-container/main-container shape and pin the deliberate differences.


@pytest.fixture(scope="module")
def interactions_deployment(docs: list[dict]) -> dict:
    deps = _by_kind(docs, "Deployment", "data-governance-interactions")
    assert len(deps) == 1, (
        "expected exactly one Deployment named data-governance-interactions"
    )
    return deps[0]


def test_interactions_single_replica(interactions_deployment: dict) -> None:
    """Single-cursor drainer: must NOT run two concurrent pods.

    The driver advances one shared ``processor_state`` cursor with no
    inter-pod lock; two replicas would poll the same cursor, double-process
    spans, and contend on derived-table writes. Issue #74 pins one replica.
    """
    assert interactions_deployment["spec"]["replicas"] == 1, (
        "interactions processor must run a single replica — it is a "
        "single-cursor DB drainer, not an HA service"
    )


def test_interactions_rollout_does_not_surge_a_second_pod(
    interactions_deployment: dict,
) -> None:
    """RollingUpdate must tear the old pod down before the new one starts.

    A surge-first rollout (maxSurge>=1) would briefly run two pods against the
    one cursor during a *voluntary* (controller-driven) rollout. maxSurge:0 +
    maxUnavailable:1 makes the rollout old-pod-gone-first. This does NOT cover
    involuntary disruption (node drain / eviction can overlap two pods); that
    window is made safe by the idempotent per-span re-derive (ADR-0007), not
    by this strategy.
    """
    strategy = interactions_deployment["spec"].get("strategy") or {}
    assert strategy.get("type", "RollingUpdate") == "RollingUpdate"
    ru = strategy.get("rollingUpdate") or {}
    assert ru.get("maxSurge") == 0, (
        "interactions rollout must set maxSurge:0 so no second pod is created "
        "during a rollout (two pods would share the one cursor)"
    )
    assert ru.get("maxUnavailable") == 1, (
        "interactions rollout must allow maxUnavailable:1 so the single pod "
        "can be torn down before its replacement starts"
    )


def test_interactions_has_init_container_running_migrate(
    interactions_deployment: dict,
) -> None:
    """ADR-0002: init container runs the migrate CLI to head, like the receiver."""
    pod_spec = interactions_deployment["spec"]["template"]["spec"]
    init_containers = pod_spec.get("initContainers") or []
    assert init_containers, (
        "interactions pod must declare an init container running migrate"
    )
    migrate_cmds = []
    for ic in init_containers:
        cmd = ic.get("command") or []
        args = ic.get("args") or []
        migrate_cmds.append(" ".join(cmd + args))
    assert any(
        "data_governance.db.migrate" in c for c in migrate_cmds
    ), f"init container must invoke `python -m data_governance.db.migrate`, got {migrate_cmds!r}"


def test_interactions_main_container_runs_processor(
    interactions_deployment: dict,
) -> None:
    """Main container runs ``python -m data_governance.processors.interactions``."""
    pod_spec = interactions_deployment["spec"]["template"]["spec"]
    containers = pod_spec.get("containers") or []
    assert containers, "interactions Deployment must have at least one container"
    main = containers[0]
    cmd = " ".join((main.get("command") or []) + (main.get("args") or []))
    assert "data_governance.processors.interactions" in cmd, (
        f"main container must invoke the interactions entry point, got {cmd!r}"
    )


def test_interactions_deployment_selects_the_sidecar_algorithm(
    interactions_deployment: dict,
) -> None:
    """This deployment ingests the two-span AuthBridge source, so the env pins
    INTERACTIONS_ALGORITHM=sidecar explicitly (ADR-0028) — the code default
    stays "streaming"."""
    pod_spec = interactions_deployment["spec"]["template"]["spec"]
    main = pod_spec["containers"][0]
    env = {e["name"]: e.get("value") for e in (main.get("env") or [])}
    assert env.get("INTERACTIONS_ALGORITHM") == "sidecar", (
        "interactions container must pin INTERACTIONS_ALGORITHM=sidecar, "
        f"got {env.get('INTERACTIONS_ALGORITHM')!r}"
    )


def test_interactions_main_container_does_not_run_alembic(
    interactions_deployment: dict,
) -> None:
    """PROJECT.md §3: the processor container itself does NOT run Alembic."""
    pod_spec = interactions_deployment["spec"]["template"]["spec"]
    main = pod_spec["containers"][0]
    cmd = " ".join((main.get("command") or []) + (main.get("args") or []))
    assert "data_governance.db.migrate" not in cmd, (
        "main container must not run `migrate`; that is the init container's job"
    )
    assert "alembic" not in cmd.lower(), "main container must not invoke alembic"


def test_interactions_runs_on_receiver_image(
    interactions_deployment: dict,
) -> None:
    """No new image build: init + main both run the receiver image (issue #38)."""
    pod_spec = interactions_deployment["spec"]["template"]["spec"]
    init = pod_spec["initContainers"][0]
    main = pod_spec["containers"][0]
    for ctr in (init, main):
        assert ctr.get("image") == "data-governance/receiver:latest", (
            f"{ctr.get('name')!r} must run the receiver image (one image, two "
            f"tags, deployments differ only by command:), got {ctr.get('image')!r}"
        )
        assert ctr.get("imagePullPolicy") == "IfNotPresent", (
            f"{ctr.get('name')!r} must set imagePullPolicy: IfNotPresent"
        )


def test_interactions_database_url_set(interactions_deployment: dict) -> None:
    """The processor and migrate CLIs read ``DATABASE_URL`` (see __main__.py / migrate.py)."""
    pod_spec = interactions_deployment["spec"]["template"]["spec"]
    init = pod_spec["initContainers"][0]
    main = pod_spec["containers"][0]
    for ctr in (init, main):
        env = {e["name"]: e for e in ctr.get("env") or []}
        assert "DATABASE_URL" in env, f"{ctr['name']} must set DATABASE_URL"


def test_interactions_has_no_service(docs: list[dict]) -> None:
    """Deliberate difference from the receiver: the processor has no Service.

    It is a poll/LISTEN DB consumer with no inbound API, so nothing should
    route traffic at it.
    """
    svcs = _by_kind(docs, "Service", "data-governance-interactions")
    assert not svcs, (
        "interactions processor must NOT declare a Service — it has no inbound "
        f"API (found {[s.get('metadata', {}).get('name') for s in svcs]!r})"
    )


def test_interactions_declares_no_container_ports(
    interactions_deployment: dict,
) -> None:
    """No container ports: nothing inbound to declare (issue #74 AC).

    The in-pod Prometheus /metrics surface (9091) is intentionally left
    undeclared and unexposed in v1, matching the receiver's 9090 not being
    opened by the v1 NetworkPolicy — v1 has no monitoring peer.
    """
    pod_spec = interactions_deployment["spec"]["template"]["spec"]
    for ctr_kind, ctr in _iter_containers(pod_spec):
        assert not (ctr.get("ports") or []), (
            f"interactions {ctr_kind} {ctr.get('name')!r} must declare no "
            f"ports — the processor has no inbound API (got {ctr.get('ports')!r})"
        )


# ---------------------------------------------------------------------------
# Postgres StatefulSet
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def postgres_statefulset(docs: list[dict]) -> dict:
    sts = _by_kind(docs, "StatefulSet")
    assert sts, "expected a Postgres StatefulSet"
    # Allow whatever name the implementation chose, but require a single one.
    assert len(sts) == 1, "expected exactly one StatefulSet"
    return sts[0]


def test_postgres_in_same_namespace_as_receiver(
    postgres_statefulset: dict, receiver_deployment: dict
) -> None:
    """PROJECT.md §1: Postgres StatefulSet lives in the same namespace."""
    pg_ns = postgres_statefulset.get("metadata", {}).get("namespace")
    rx_ns = receiver_deployment.get("metadata", {}).get("namespace")
    assert pg_ns and rx_ns, "both manifests must declare a namespace"
    assert pg_ns == rx_ns, f"Postgres ({pg_ns}) must share namespace with receiver ({rx_ns})"


def test_postgres_service_exists(docs: list[dict]) -> None:
    """The receiver reaches Postgres through a stable DNS name; need a Service."""
    pg_services = [
        s for s in _by_kind(docs, "Service")
        if "postgres" in (s.get("metadata", {}).get("name") or "").lower()
    ]
    assert pg_services, "expected a Postgres Service so the receiver can reach it by name"


# ---------------------------------------------------------------------------
# UI backend Deployment + Service
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ui_deployment(docs: list[dict]) -> dict:
    deps = _by_kind(docs, "Deployment", "data-governance-ui")
    assert len(deps) == 1, "expected exactly one UI backend Deployment named data-governance-ui"
    return deps[0]


@pytest.fixture(scope="module")
def ui_service(docs: list[dict]) -> dict:
    svcs = _by_kind(docs, "Service", "data-governance-ui")
    assert len(svcs) == 1, "expected exactly one UI backend Service named data-governance-ui"
    return svcs[0]


def test_ui_service_is_clusterip_on_8080(ui_service: dict) -> None:
    """UI backend defaults to port 8080 (see ``data_governance.api.DEFAULT_PORT``)."""
    assert ui_service["spec"].get("type", "ClusterIP") == "ClusterIP"
    ports = {p["port"] for p in ui_service["spec"]["ports"]}
    assert 8080 in ports, "UI backend Service must expose 8080"


def test_ui_deployment_runs_api(ui_deployment: dict) -> None:
    pod_spec = ui_deployment["spec"]["template"]["spec"]
    main = pod_spec["containers"][0]
    cmd = " ".join((main.get("command") or []) + (main.get("args") or []))
    assert "data_governance.api" in cmd, (
        f"UI backend container must invoke the api entry point, got {cmd!r}"
    )


def test_ui_deployment_has_probes(ui_deployment: dict) -> None:
    """Probes on /healthz keep the UI backend's rolling updates honest."""
    pod_spec = ui_deployment["spec"]["template"]["spec"]
    main = pod_spec["containers"][0]
    for probe_name in ("livenessProbe", "readinessProbe"):
        probe = main.get(probe_name)
        assert probe, f"{probe_name} required on UI backend container"
        http = probe.get("httpGet")
        assert http and http.get("path") == "/healthz", f"{probe_name} must hit /healthz"


# ---------------------------------------------------------------------------
# Gateway API routing (UI exposure on the kagenti shared Gateway)
# ---------------------------------------------------------------------------
#
# The UI is exposed at http://dg.localtest.me:8080 via an HTTPRoute attached
# to the kagenti-system/http Gateway, mirroring how phoenix and other
# kagenti services are exposed. Because the route lives in kagenti-system
# (the only namespace labelled shared-gateway-access=true) but targets a
# Service in data-governance, a ReferenceGrant must permit that single
# cross-namespace edge.


def test_ui_httproute_exists_and_targets_ui_service(docs: list[dict]) -> None:
    routes = _by_kind(docs, "HTTPRoute", "data-governance-ui")
    assert len(routes) == 1, "expected one HTTPRoute named data-governance-ui"
    route = routes[0]
    assert route["metadata"]["namespace"] == "kagenti-system", (
        "HTTPRoute must live in kagenti-system (the shared-gateway-access ns)"
    )
    hostnames = route["spec"].get("hostnames") or []
    assert "dg.localtest.me" in hostnames, (
        f"HTTPRoute must expose dg.localtest.me, got {hostnames}"
    )
    parents = route["spec"].get("parentRefs") or []
    assert any(
        p.get("name") == "http"
        and p.get("namespace") == "kagenti-system"
        and p.get("kind", "Gateway") == "Gateway"
        for p in parents
    ), f"HTTPRoute must attach to kagenti-system/http Gateway, got {parents}"
    backends = [b for r in route["spec"].get("rules") or [] for b in r.get("backendRefs") or []]
    assert any(
        b.get("name") == "data-governance-ui"
        and b.get("namespace") == "data-governance"
        and b.get("port") == 8080
        for b in backends
    ), f"HTTPRoute must target data-governance/data-governance-ui:8080, got {backends}"


def test_ui_referencegrant_permits_cross_namespace_route(docs: list[dict]) -> None:
    grants = _by_kind(docs, "ReferenceGrant")
    assert grants, "expected a ReferenceGrant for cross-ns HTTPRoute -> Service"
    matching = [
        g for g in grants
        if g["metadata"].get("namespace") == "data-governance"
        and any(
            f.get("kind") == "HTTPRoute" and f.get("namespace") == "kagenti-system"
            for f in g["spec"].get("from") or []
        )
        and any(
            t.get("kind") == "Service" and t.get("name") == "data-governance-ui"
            for t in g["spec"].get("to") or []
        )
    ]
    assert matching, (
        "expected a ReferenceGrant in data-governance permitting "
        "HTTPRoutes from kagenti-system to target the data-governance-ui Service"
    )


# ---------------------------------------------------------------------------
# NetworkPolicy
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def network_policies(docs: list[dict]) -> list[dict]:
    nps = _by_kind(docs, "NetworkPolicy")
    assert nps, "expected at least one NetworkPolicy"
    return nps


def test_network_policy_includes_ingress(network_policies: list[dict]) -> None:
    """At least one policy must declare ``policyTypes: [Ingress]``."""
    for np in network_policies:
        types = np["spec"].get("policyTypes") or []
        if "Ingress" in types:
            return
    raise AssertionError("no NetworkPolicy with policyTypes including Ingress found")


def _matching_policy_for(np_list: list[dict], target_pod_label_value: str) -> dict | None:
    """Return the first policy whose podSelector matches a given app label."""
    for np in np_list:
        sel = np["spec"].get("podSelector") or {}
        match_labels = sel.get("matchLabels") or {}
        if match_labels.get("app.kubernetes.io/name") == target_pod_label_value:
            return np
    return None


def test_network_policy_targets_receiver_workload(network_policies: list[dict]) -> None:
    """The receiver pods must be covered by a NetworkPolicy."""
    np = _matching_policy_for(network_policies, "data-governance-receiver")
    assert np is not None, (
        "expected a NetworkPolicy with podSelector matching app.kubernetes.io/name=data-governance-receiver"
    )


def test_network_policy_targets_ui_workload(network_policies: list[dict]) -> None:
    """The UI backend must also be covered."""
    np = _matching_policy_for(network_policies, "data-governance-ui")
    assert np is not None, (
        "expected a NetworkPolicy with podSelector matching app.kubernetes.io/name=data-governance-ui"
    )


def test_network_policy_allows_only_kagenti_namespace(network_policies: list[dict]) -> None:
    """The receiver and UI policies must allow ingress only from the Kagenti namespace.

    PROJECT.md §7: "v1 is unauthenticated and intended cluster-internal […] running
    it outside an isolated cluster is unsupported." The NetworkPolicy is the
    sole security boundary.
    """
    for target in ("data-governance-receiver", "data-governance-ui"):
        np = _matching_policy_for(network_policies, target)
        assert np is not None, f"missing policy for {target}"
        rules = np["spec"].get("ingress") or []
        assert rules, f"{target}: NetworkPolicy must have at least one ingress rule"
        for rule in rules:
            sources = rule.get("from") or []
            assert sources, (
                f"{target}: ingress rule with empty `from` allows all sources — "
                "must be restricted to a namespaceSelector"
            )
            for src in sources:
                # Every source peer must restrict by namespace. ipBlock or bare
                # podSelector (intra-namespace only) are not what v1 wants.
                ns_sel = src.get("namespaceSelector")
                assert ns_sel is not None, (
                    f"{target}: each `from` peer must use namespaceSelector "
                    f"(got {src!r})"
                )
                # Selector must restrict by label rather than match anything.
                match_labels = ns_sel.get("matchLabels") or {}
                match_exprs = ns_sel.get("matchExpressions") or []
                assert match_labels or match_exprs, (
                    f"{target}: namespaceSelector must restrict to the Kagenti namespace, "
                    f"empty selector matches all namespaces"
                )
                # The label we agree to match. v1 expects the upstream Kagenti
                # namespace to carry `kubernetes.io/metadata.name=kagenti` (the
                # default label every k8s namespace gets) or
                # `kagenti.io/namespace-role=workload`. Either is a *named*
                # selector — we just require it to be specific.
                if match_labels:
                    assert "kagenti" in str(match_labels).lower(), (
                        f"{target}: namespaceSelector must reference the kagenti namespace "
                        f"(got {match_labels!r})"
                    )


def _namespace_selector_admits(ns_sel: dict, ns_labels: dict[str, str]) -> bool:
    """Return True iff a Kubernetes ``namespaceSelector`` admits a namespace
    with the given labels.

    Implements the subset of the LabelSelector semantics we exercise here:
    ``matchLabels`` (all key/value pairs must be present) AND ``matchExpressions``
    with operators ``In`` / ``NotIn`` / ``Exists`` / ``DoesNotExist``. An empty
    selector admits everything (the k8s default).
    """
    match_labels = ns_sel.get("matchLabels") or {}
    for k, v in match_labels.items():
        if ns_labels.get(k) != v:
            return False
    for expr in ns_sel.get("matchExpressions") or []:
        op = expr.get("operator")
        key = expr.get("key")
        values = expr.get("values") or []
        if op == "In":
            if ns_labels.get(key) not in values:
                return False
        elif op == "NotIn":
            if ns_labels.get(key) in values:
                return False
        elif op == "Exists":
            if key not in ns_labels:
                return False
        elif op == "DoesNotExist":
            if key in ns_labels:
                return False
        else:
            raise AssertionError(f"unsupported operator in matchExpressions: {op!r}")
    return True


def test_network_policy_admits_kagenti_system_namespace(
    network_policies: list[dict],
) -> None:
    """Ingress from the kagenti-system namespace must be permitted (issue #42).

    The kagenti otel-collector lives in the ``kagenti-system`` namespace on
    every cluster we currently target — there is no namespace literally named
    ``kagenti``. A v1 NetworkPolicy that only admits ``kubernetes.io/metadata.name=kagenti``
    silently drops every span the collector tries to export to us. PROJECT.md §7
    refers to "the Kagenti namespace" but does not pin the literal name, so the
    policy must admit at least the actually-deployed name (``kagenti-system``)
    on both the receiver and UI policies.
    """
    kagenti_system_labels = {"kubernetes.io/metadata.name": "kagenti-system"}
    for target, ports_required in (
        ("data-governance-receiver", {4317, 4318}),
        ("data-governance-ui", {8080}),
    ):
        np = _matching_policy_for(network_policies, target)
        assert np is not None, f"missing policy for {target}"
        admitted_ports: set[int] = set()
        for rule in np["spec"].get("ingress") or []:
            for src in rule.get("from") or []:
                ns_sel = src.get("namespaceSelector") or {}
                if _namespace_selector_admits(ns_sel, kagenti_system_labels):
                    for p in rule.get("ports") or []:
                        admitted_ports.add(p["port"])
                    break
        missing = ports_required - admitted_ports
        assert not missing, (
            f"{target}: NetworkPolicy must admit ingress from the kagenti-system "
            f"namespace on ports {sorted(ports_required)}; missing {sorted(missing)}. "
            f"The kagenti otel-collector lives in `kagenti-system`; restricting "
            f"the policy to a namespace literally named `kagenti` would drop every "
            f"span the collector exports."
        )


def test_network_policy_covers_otlp_and_ui_ports(network_policies: list[dict]) -> None:
    """Receiver policy must allow 4317 + 4318; UI policy must allow 8080."""
    rx_np = _matching_policy_for(network_policies, "data-governance-receiver")
    assert rx_np is not None
    rx_ports: set[int] = set()
    for rule in rx_np["spec"].get("ingress") or []:
        for p in rule.get("ports") or []:
            rx_ports.add(p["port"])
    assert {4317, 4318}.issubset(rx_ports), (
        f"receiver NetworkPolicy must allow OTLP gRPC 4317 and OTLP HTTP 4318, got {rx_ports}"
    )

    ui_np = _matching_policy_for(network_policies, "data-governance-ui")
    assert ui_np is not None
    ui_ports: set[int] = set()
    for rule in ui_np["spec"].get("ingress") or []:
        for p in rule.get("ports") or []:
            ui_ports.add(p["port"])
    assert 8080 in ui_ports, (
        f"UI backend NetworkPolicy must allow 8080, got {ui_ports}"
    )


# ---------------------------------------------------------------------------
# Container env-var ordering (issue #40)
# ---------------------------------------------------------------------------
#
# Kubernetes ``$(VAR_NAME)`` substitution inside a container's ``env[].value``
# only resolves variables that appear EARLIER in the same container's ``env``
# list. References to later entries (or to entries that don't exist) are left
# as the literal string ``$(VAR_NAME)`` and reach the container as-is. See
# https://kubernetes.io/docs/tasks/inject-data-application/define-interdependent-environment-variables/
#
# That behaviour is easy to break by hand-edit (e.g. moving DATABASE_URL to
# the top of the list "for readability"). The check below is generic — it
# fires for any forward reference, not just DATABASE_URL — so the same class
# of bug is caught for any future env construction.


def test_container_env_placeholder_references_resolve_in_order(
    docs: list[dict],
) -> None:
    """``$(VAR)`` inside any ``env[].value`` must reference a name defined earlier.

    Kubernetes only substitutes ``$(VAR)`` against entries that appear earlier
    in the same container's ``env`` list. A forward reference (or a reference
    to a name that never appears in the list) reaches the container as the
    literal string ``$(VAR)``.

    Regression for issue #40, where ``DATABASE_URL`` was the first entry in
    the receiver / migrate / UI env blocks even though its value embedded
    ``$(POSTGRES_USER)`` / ``$(POSTGRES_PASSWORD)`` / ``$(POSTGRES_DB)``,
    so the runtime DSN contained literal ``$(POSTGRES_USER)`` and Postgres
    rejected the connection with ``password authentication failed for user
    "$(POSTGRES_USER)"``.

    Scope: walks every workload's container ``env[]`` entries that carry an
    inline ``value:`` and reports any forward reference, regardless of
    variable name. Names brought in via ``envFrom`` are not considered — they
    are not currently used in this repo, and Kubernetes does not document a
    stable substitution-ordering contract between ``envFrom`` and ``env[]``,
    so a check that hard-codes one would be wrong.
    """
    failures: list[str] = []
    # Workloads whose containers we expect to walk. If `_iter_pod_specs` yields
    # nothing for one of these (e.g. someone misspells `kind:` and the iterator
    # silently skips it) the test would otherwise pass vacuously and let the
    # original bug back in. The expected names match `metadata.name` on the
    # workloads in `deploy/k8s/`.
    expected_workloads = {
        "data-governance-receiver",
        "data-governance-ui",
        "data-governance-interactions",
    }
    seen_workloads: set[str] = set()
    for kind, name, path, pod_spec in _iter_pod_specs(docs):
        seen_workloads.add(name)
        for ctr_kind, ctr in _iter_containers(pod_spec):
            env_list = ctr.get("env") or []
            seen: set[str] = set()
            for entry in env_list:
                entry_name = entry.get("name")
                value = entry.get("value")
                if isinstance(value, str):
                    for ref in _PLACEHOLDER_RE.findall(value):
                        if ref not in seen:
                            failures.append(
                                f"{kind}/{name} ({path}) "
                                f"{ctr_kind} {ctr.get('name')!r} env {entry_name!r}: "
                                f"value references $({ref}) but {ref} is not defined "
                                f"earlier in the same env list (k8s only substitutes "
                                f"prior entries; a forward reference reaches the "
                                f"container as literal '$( {ref} )')."
                            )
                if entry_name is not None:
                    seen.add(entry_name)
    missing = expected_workloads - seen_workloads
    assert not missing, (
        "env-ordering check did not walk expected workload(s): "
        f"{sorted(missing)} — the iterator silently skipped them, which would "
        "let the issue #40 bug class regress undetected. Check `kind:` "
        "spellings in deploy/k8s/."
    )
    assert not failures, "env placeholder ordering violations:\n  " + "\n  ".join(failures)
