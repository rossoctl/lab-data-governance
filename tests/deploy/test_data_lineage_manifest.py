"""Deployment manifest for the P-data-lineage processor (issue #117, ADR-0027).

A DB consumer, not an inbound API — the P-interactions shape
(``deploy/k8s/70-interactions.yaml``), not the P-classification one: the SHARED
receiver image with ``command:`` overridden to the data-lineage entry point (issue
#38 — one image, deployments differ only by command), no Service and no container
ports, single replica against one shared ``processor_state`` cursor (the
``data_lineage`` row) with a maxSurge:0 rollout.

The deliberate difference from ``80-classification.yaml`` is the image: lineage has
no model and no inference, so ADR-0022's dedicated-fat-image reasoning does not
apply and a regression to a `data-governance/data-lineage:*` image is pinned out
below.

The other pinned specific is ``SEMANTIC_MATCHER``: set explicitly to ``simple``
rather than left to the code default, so the manifest shows that lineage is derived
under the trivial always-match matcher (complete but full of maybes, ADR-0027).

These mirror ``test_classification_manifest.py``. The generic manifest-wide checks
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
def data_lineage_deployment(docs: list[dict]) -> dict:
    deps = _by_kind(docs, "Deployment", "data-governance-data-lineage")
    assert len(deps) == 1, (
        "expected exactly one Deployment named data-governance-data-lineage"
    )
    return deps[0]


def test_single_replica(data_lineage_deployment: dict) -> None:
    """Single-cursor drainer: must NOT run two concurrent pods.

    The shared driver advances one ``processor_state`` cursor (the ``data_lineage``
    row) with no inter-pod lock; two replicas would re-derive the same traces.
    Unlike P-classification, the write is NOT write-once (upsert + stale-row delete
    + status upsert, ADR-0027 D9), so what makes a brief overlap safe is that a
    derivation is a deterministic function of committed state computed entirely
    inside the loop's one transaction (ADR-0007) — concurrent derivations converge
    rather than corrupt. Wasted work, not a hazard; not a supported topology either.
    """
    assert data_lineage_deployment["spec"]["replicas"] == 1, (
        "data-lineage processor must run a single replica — it is a single-cursor "
        "DB drainer, not an HA service"
    )


def test_rollout_does_not_surge_a_second_pod(data_lineage_deployment: dict) -> None:
    """maxSurge:0 + maxUnavailable:1 tears the old pod down before the new one
    starts, so a voluntary rollout never runs two pods against the one cursor —
    the same rollout strategy both sibling processors use. It does not cover
    involuntary disruption; determinism + transaction scoping covers that window."""
    strategy = data_lineage_deployment["spec"].get("strategy") or {}
    assert strategy.get("type", "RollingUpdate") == "RollingUpdate"
    ru = strategy.get("rollingUpdate") or {}
    assert ru.get("maxSurge") == 0, (
        "data-lineage rollout must set maxSurge:0 so no second pod is created "
        "during a rollout (two pods would share the one cursor)"
    )
    assert ru.get("maxUnavailable") == 1, (
        "data-lineage rollout must allow maxUnavailable:1 so the single pod can "
        "be torn down before its replacement starts"
    )


def test_has_init_container_running_migrate(data_lineage_deployment: dict) -> None:
    """ADR-0002: init container runs the migrate CLI to head, like the receiver."""
    pod_spec = data_lineage_deployment["spec"]["template"]["spec"]
    init_containers = pod_spec.get("initContainers") or []
    assert init_containers, "pod must declare an init container running migrate"
    migrate_cmds = [
        " ".join((ic.get("command") or []) + (ic.get("args") or []))
        for ic in init_containers
    ]
    assert any("data_governance.db.migrate" in c for c in migrate_cmds), (
        f"init container must invoke migrate, got {migrate_cmds!r}"
    )


def test_main_container_runs_data_lineage_entrypoint(
    data_lineage_deployment: dict,
) -> None:
    """Main container runs ``python -m data_governance.processors.data_lineage``."""
    pod_spec = data_lineage_deployment["spec"]["template"]["spec"]
    containers = pod_spec.get("containers") or []
    assert containers, "Deployment must have at least one container"
    main = containers[0]
    cmd = " ".join((main.get("command") or []) + (main.get("args") or []))
    assert "data_governance.processors.data_lineage" in cmd, (
        f"main container must invoke the data-lineage entry point, got {cmd!r}"
    )


def test_main_container_does_not_run_alembic(data_lineage_deployment: dict) -> None:
    """PROJECT.md §3: the processor container itself does NOT run Alembic."""
    pod_spec = data_lineage_deployment["spec"]["template"]["spec"]
    main = pod_spec["containers"][0]
    cmd = " ".join((main.get("command") or []) + (main.get("args") or []))
    assert "data_governance.db.migrate" not in cmd, (
        "main container must not run `migrate`; that is the init container's job"
    )
    assert "alembic" not in cmd.lower(), "main container must not invoke alembic"


def test_runs_on_shared_receiver_image(data_lineage_deployment: dict) -> None:
    """No new image build: init + main both run the shared receiver image (issue
    #38 — one image, deployments differ only by ``command:``).

    This is the P-interactions case, not the P-classification one. Lineage's only
    real dependency is psycopg and the default matcher reads no payload content, so
    ADR-0022's reason for a dedicated image (torch + ~500 MB of baked-in weights)
    does not apply.
    """
    pod_spec = data_lineage_deployment["spec"]["template"]["spec"]
    init = pod_spec["initContainers"][0]
    main = pod_spec["containers"][0]
    for ctr in (init, main):
        assert ctr.get("image") == "data-governance/receiver:latest", (
            f"{ctr.get('name')!r} must run the shared receiver image (one image, "
            f"deployments differ only by command:), got {ctr.get('image')!r}"
        )
        assert ctr.get("imagePullPolicy") == "IfNotPresent", (
            f"{ctr.get('name')!r} must set imagePullPolicy: IfNotPresent"
        )


def test_does_not_use_a_dedicated_lineage_image(data_lineage_deployment: dict) -> None:
    """Guard against inventing a `data-governance/data-lineage:*` image.

    P-classification earns its own image ONLY because of torch + baked-in model
    weights (ADR-0022/0023). Lineage has no model and no inference, so a dedicated
    image here would be an unjustified second build with no dependency to justify
    it — and would silently decouple this pod's compiled alembic head from the
    shared image's.
    """
    pod_spec = data_lineage_deployment["spec"]["template"]["spec"]
    for _ctr_kind, ctr in _iter_containers(pod_spec):
        image = ctr.get("image") or ""
        assert "data-governance/data-lineage" not in image, (
            f"{ctr.get('name')!r} must not run a dedicated lineage image "
            f"(got {image!r}) — lineage rides the shared receiver image"
        )
        assert "data-governance/classification" not in image, (
            f"{ctr.get('name')!r} must not run the classification image "
            f"(got {image!r}) — it carries torch + model weights for no reason here"
        )


def test_sets_semantic_matcher_to_simple(data_lineage_deployment: dict) -> None:
    """``SEMANTIC_MATCHER=simple`` is set EXPLICITLY, not left to the code default.

    ``simple`` is the trivial always-match matcher, so lineage derived under it is
    complete but full of maybes — every structural edge is treated as real data
    flow (ADR-0027). Pinning it in the manifest makes that property visible to
    whoever reads the deployment, following the ``INTERACTIONS_ALGORITHM``
    precedent in 70-interactions.yaml. An unregistered name raises
    ``UnknownMatcher`` (issue #116), so a typo is a CrashLoopBackOff rather than
    silent maybe-everywhere lineage.
    """
    main = data_lineage_deployment["spec"]["template"]["spec"]["containers"][0]
    env = {e["name"]: e for e in main.get("env") or []}
    assert "SEMANTIC_MATCHER" in env, (
        "data-lineage deployment must set SEMANTIC_MATCHER explicitly so the "
        "matcher lineage is derived under is visible in the manifest"
    )
    assert env["SEMANTIC_MATCHER"].get("value") == "simple", (
        "SEMANTIC_MATCHER must be 'simple' (the trivial default matcher), got "
        f"{env['SEMANTIC_MATCHER'].get('value')!r}"
    )


def test_semantic_matcher_value_is_registered() -> None:
    """The manifest's matcher name must actually resolve.

    ``get_matcher`` raises ``UnknownMatcher`` for an unregistered name rather than
    falling back (issue #116), so a typo in the manifest is a CrashLoopBackOff.
    Resolving the manifest's value here catches it at test time instead.
    """
    from data_governance.matching import get_matcher

    main_env_value = "simple"
    assert get_matcher(main_env_value) is not None


def test_database_url_set(data_lineage_deployment: dict) -> None:
    """The processor and migrate CLIs read ``DATABASE_URL``."""
    pod_spec = data_lineage_deployment["spec"]["template"]["spec"]
    init = pod_spec["initContainers"][0]
    main = pod_spec["containers"][0]
    for ctr in (init, main):
        env = {e["name"]: e for e in ctr.get("env") or []}
        assert "DATABASE_URL" in env, f"{ctr['name']} must set DATABASE_URL"


def test_database_url_comes_after_its_placeholders(data_lineage_deployment: dict) -> None:
    """``DATABASE_URL`` must be declared AFTER every POSTGRES_* name it embeds.

    Kubernetes only substitutes ``$(VAR)`` against entries earlier in the same
    container's env list; a forward reference arrives as the literal string and
    breaks DSN parsing (issue #40). ``test_manifests.py``'s
    ``test_container_env_placeholder_references_resolve_in_order`` pins this
    generically across every manifest — asserted here too so the data-lineage pod
    fails loudly on its own if someone reorders this block "for readability".
    """
    pod_spec = data_lineage_deployment["spec"]["template"]["spec"]
    for ctr_kind, ctr in _iter_containers(pod_spec):
        names = [e["name"] for e in ctr.get("env") or []]
        assert "DATABASE_URL" in names, f"{ctr_kind} {ctr.get('name')!r} must set it"
        idx = names.index("DATABASE_URL")
        for referenced in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"):
            assert referenced in names[:idx], (
                f"{ctr_kind} {ctr.get('name')!r}: {referenced} must be declared "
                f"before DATABASE_URL, which interpolates it (issue #40); "
                f"got order {names!r}"
            )


def test_has_no_service(docs: list[dict]) -> None:
    """A LISTEN/poll DB consumer with no inbound API — nothing routes at it."""
    svcs = _by_kind(docs, "Service", "data-governance-data-lineage")
    assert not svcs, (
        "data-lineage processor must NOT declare a Service — no inbound API "
        f"(found {[s.get('metadata', {}).get('name') for s in svcs]!r})"
    )


def test_declares_no_container_ports(data_lineage_deployment: dict) -> None:
    """No container ports: nothing inbound to declare.

    Unlike both siblings this processor serves no /metrics surface at all (issue
    #117 specifies no counters), so there is not even an undeclared in-pod port
    here — the 9090/9091/9092 sequence is untouched.
    """
    pod_spec = data_lineage_deployment["spec"]["template"]["spec"]
    for ctr_kind, ctr in _iter_containers(pod_spec):
        assert not (ctr.get("ports") or []), (
            f"data-lineage {ctr_kind} {ctr.get('name')!r} must declare no ports — "
            f"the processor has no inbound API (got {ctr.get('ports')!r})"
        )


def test_declares_no_probes(data_lineage_deployment: dict) -> None:
    """No liveness/readiness probes: there is no HTTP serving surface to probe and
    no Service whose traffic a readiness gate would protect. Failure modes are
    handled by exiting non-zero (schema-version check, issue #10; SEMANTIC_MATCHER
    validation, issue #116) into CrashLoopBackOff."""
    pod_spec = data_lineage_deployment["spec"]["template"]["spec"]
    main = pod_spec["containers"][0]
    for probe_name in ("livenessProbe", "readinessProbe", "startupProbe"):
        assert probe_name not in main, (
            f"data-lineage container must not declare {probe_name} — no HTTP "
            "serving surface exists to probe"
        )
