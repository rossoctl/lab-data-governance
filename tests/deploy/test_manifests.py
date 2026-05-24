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
- The receiver Service exposes 4317 / 4318 on ClusterIP. (The Prometheus
  /metrics port 9090 is deferred until ``MetricsServer`` is wired into the
  receiver entry point — see the follow-up issue referenced in the
  deploy/k8s/README.md "Out of scope" section.)
- The Postgres StatefulSet lives in the same namespace as the receiver.
- The UI backend Deployment + Service are present and wired to port 8080.
- The NetworkPolicy targets receiver + UI workloads, allows ingress only from
  the Kagenti namespace, and covers every port the listed services expose.
- Probe paths align with the §3.1 ingest blocklist already shipped in #9 — a
  drift here would silently flood ``spans`` with probe rows.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from data_governance.processors.otlp_receiver import blocklist


MANIFESTS_DIR = Path(__file__).resolve().parents[2] / "deploy" / "k8s"


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


def test_receiver_service_exposes_otlp_and_http_surface(receiver_service: dict) -> None:
    """OTLP gRPC 4317 and OTLP HTTP/protobuf + /healthz on 4318.

    The Prometheus /metrics port (9090) is intentionally NOT exposed by the
    v1 manifest: ``MetricsServer`` exists in the codebase but is not yet
    started by the receiver entry point. Re-adding the port on the Service
    while no process listens on it would route traffic to a closed socket.
    See the follow-up tracked in deploy/k8s/README.md ("Out of scope for v1").
    """
    ports = {p["port"]: p for p in receiver_service["spec"]["ports"]}
    assert 4317 in ports, "Service must expose OTLP gRPC on 4317"
    assert 4318 in ports, "Service must expose OTLP HTTP/protobuf + /healthz on 4318"
    assert 9090 not in ports, (
        "Service must NOT expose 9090 until MetricsServer is started by the "
        "receiver __main__ — see follow-up issue."
    )


def test_receiver_service_grpc_port_is_grpc(receiver_service: dict) -> None:
    """gRPC ports should declare protocol TCP and an appName/appProtocol cue."""
    grpc_port = next(p for p in receiver_service["spec"]["ports"] if p["port"] == 4317)
    # Protocol TCP is the default; explicit is fine, missing is fine. We assert
    # name presence so collector configs can target by name.
    assert grpc_port.get("name"), "OTLP gRPC port should be named"


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
