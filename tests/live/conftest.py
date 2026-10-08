"""Session fixtures for the live tier (``-m live``).

Nothing in this directory runs unless asked for by marker. When it runs,
it drives a real kind cluster: the travel_advisor app under the lineage
kit and cortex sidecar, the deployed data-governance pods, a real OPA. It
never seeds a table and never truncates one; it reads what the real chain
wrote, addressed by trace id.

Contract with the operator, all through ``E2E_*`` environment variables
(``Env``): where the cluster is, where the two sibling clones are (for the
commit pins), and a handful of tunables. Everything the run depends on is
pinned in ``pins.yaml`` and verified by ``preflight`` before any traffic is
sent; a mismatch fails the session with the exact expected/observed pair.

The test catalog (``catalog_e2e.json``) reaches OPA through the production
path only (``deploy/create-opa-configmap.sh <path>``) and the shipped
catalog is restored the same way on teardown, even after a failure.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import socket
import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest
import yaml

HERE = Path(__file__).parent
REPO = HERE.parent.parent
CATALOG_E2E = HERE / "catalog_e2e.json"
SINK_MANIFEST = HERE / "k8s" / "e2e-sink.yaml"

# One session is one app (E2E_APP, default travel_advisor): its declaration
# (tests/live/apps/<app>.yaml) names the sidecar'd workloads preflight pins,
# the agents whose LLM env and peer resolution it checks, and the workloads
# that run the plain (un-shimmed) app image.
from tests.live import fleet as _fleet_mod  # noqa: E402

E2E_APP = os.environ.get("E2E_APP", "travel_advisor")
_FLEET_PATH = HERE / "apps" / f"{E2E_APP}.yaml"
# Loaded lazily: an unknown E2E_APP is a live-session error (the fixtures
# raise it with the known list), never an import error that aborts the
# default suite's collection.
FLEET = _fleet_mod.load(_FLEET_PATH) if _FLEET_PATH.exists() else None
ATTACHED_WORKLOADS = sorted(FLEET.workloads) if FLEET else []
AGENT_WORKLOADS = sorted(FLEET.preflight.get("llm_env_agents") or (FLEET.agents - {FLEET.entry})) if FLEET else []
PLAIN_IMAGE_WORKLOADS = list(FLEET.preflight.get("plain_image_workloads") or []) if FLEET else []


def _require_fleet():
    if FLEET is None:
        raise pytest.UsageError(
            f"E2E_APP={E2E_APP!r} has no declaration; known apps: "
            + ", ".join(sorted(p.stem for p in (HERE / "apps").glob("*.yaml"))))
    return FLEET



def opa_input(flow: dict, leg: str = "request") -> dict:
    """One flow as the engine would send it: the OPA input is one flow per
    leg, keyed by leg (``risk/engine/utils.py``, #271/#274). Every direct
    OPA proof in the tier goes through here so a change of the input layout
    is one edit."""
    return {"input": {"flows": {leg: flow}}}


# The catalog fixture's proof input: fires E2E-PI-INT and E2E-INT-DATA
# under the test catalog and nothing under the shipped one.
PROOF_INPUT = opa_input({
    "event_type": "internal_sharing",
    "data_items": [{"regulatory_tags": ["PI"], "classification_level": "INTERNAL"}],
    "data_destinations": [{"data_destination_categories": ["internal"],
                           "data_destination_trust_level": "UNKNOWN"}],
    "requested_actions": ["send"],
})
SHIPPED_PROOF_INPUT = opa_input({
    "event_type": "external_sharing",
    "data_items": [{"regulatory_tags": ["PII"], "classification_level": "CONFIDENTIAL"}],
    "data_destinations": [{"data_destination_categories": ["external"],
                           "data_destination_trust_level": "UNTRUSTED_EXTERNAL"}],
    "requested_actions": ["send"],
})
OPA_DECISION_PATH = "/v1/data/data_governance/policy_decision"


def pytest_collection_modifyitems(items):
    """Every test under tests/live is live, whether or not it says so."""
    for item in items:
        if str(item.fspath).startswith(str(HERE)):
            item.add_marker(pytest.mark.live)


# --- environment ---------------------------------------------------------------


@dataclass(frozen=True)
class Env:
    kube_context: str
    agent_examples_snp: Path
    cortex_dir: Path
    app_ns: str = "travel-advisor"
    dg_ns: str = "data-governance"
    dg_api: str = "http://dg.localtest.me:8080"
    kind_node: str = "rossoctl-control-plane"
    settle_timeout: float = 600.0
    quiet_seconds: float = 12.0
    run_dir: Path = REPO / "tests" / "live" / "runs"
    destructive: bool = False

    @classmethod
    def from_environ(cls) -> "Env":
        missing = [k for k in ("E2E_KUBE_CONTEXT", "E2E_AGENT_EXAMPLES_SNP", "E2E_CORTEX_DIR")
                   if not os.environ.get(k)]
        if missing:
            raise pytest.UsageError(
                "live tier needs " + ", ".join(missing) + " (see docs/LIVE-E2E.md)")
        g = os.environ.get
        return cls(
            kube_context=g("E2E_KUBE_CONTEXT"),
            agent_examples_snp=Path(g("E2E_AGENT_EXAMPLES_SNP")).expanduser(),
            cortex_dir=Path(g("E2E_CORTEX_DIR")).expanduser(),
            app_ns=g("E2E_APP_NS", _require_fleet().namespace),
            dg_ns=g("E2E_DG_NS", "data-governance"),
            dg_api=g("E2E_DG_API", "http://dg.localtest.me:8080"),
            kind_node=g("E2E_KIND_NODE", "rossoctl-control-plane"),
            settle_timeout=float(g("E2E_SETTLE_TIMEOUT", "600")),
            quiet_seconds=float(g("E2E_QUIET_SECONDS", "12")),
            run_dir=Path(g("E2E_RUN_DIR", str(REPO / "tests" / "live" / "runs"))),
            destructive=g("E2E_DESTRUCTIVE", "0") == "1",
        )


@pytest.fixture(scope="session")
def env() -> Env:
    return Env.from_environ()


# --- kubectl -------------------------------------------------------------------


class Kube:
    def __init__(self, env: Env) -> None:
        self.env = env

    def run(self, args: list[str], *, ns: str | None = None, input: str | None = None,
            timeout: float = 120.0, check: bool = True) -> str:
        cmd = ["kubectl", "--context", self.env.kube_context]
        if ns:
            cmd += ["-n", ns]
        cmd += args
        p = subprocess.run(cmd, input=input, capture_output=True, text=True, timeout=timeout)
        if check and p.returncode != 0:
            raise RuntimeError(f"{' '.join(cmd)} failed ({p.returncode}): {p.stderr.strip()}")
        return p.stdout

    def json(self, args: list[str], *, ns: str | None = None) -> Any:
        return json.loads(self.run(args + ["-o", "json"], ns=ns))

    def exists(self, kind: str, name: str, *, ns: str) -> bool:
        p = subprocess.run(
            ["kubectl", "--context", self.env.kube_context, "-n", ns, "get", kind, name,
             "-o", "name"], capture_output=True, text=True)
        return p.returncode == 0

    def exec_py(self, program: str, spec: dict[str, Any], *, timeout: float,
                deploy: str = "deploy/demo-client", container: str = "client") -> str:
        """Run *program* with ``python3 -`` inside the app's demo-client
        pod; *spec* travels as one env var, never through a shell."""
        return self.run(
            ["exec", "-i", deploy, "-c", container, "--",
             "env", f"E2E_SPEC={json.dumps(spec)}", "python3", "-"],
            ns=self.env.app_ns, input=program, timeout=timeout)

    def rollout_restart(self, deploy: str, *, ns: str, timeout: float = 180.0) -> None:
        self.run(["rollout", "restart", f"deploy/{deploy}"], ns=ns)
        self.run(["rollout", "status", f"deploy/{deploy}", f"--timeout={int(timeout)}s"],
                 ns=ns, timeout=timeout + 30)


@pytest.fixture(scope="session")
def kube(env: Env) -> Kube:
    return Kube(env)


class PortForward:
    """``kubectl port-forward`` to a Service, on a free local port, waited
    on until the socket accepts. Stopped explicitly (the OPA restart kills
    its own forward, so the catalog fixture makes a fresh one)."""

    def __init__(self, kube: Kube, *, ns: str, service: str, remote_port: int) -> None:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self.proc = subprocess.Popen(
            ["kubectl", "--context", kube.env.kube_context, "-n", ns, "port-forward",
             f"svc/{service}", f"{self.port}:{remote_port}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        for _ in range(60):
            if self.proc.poll() is not None:
                raise RuntimeError(f"port-forward svc/{service} died: {self.proc.stderr.read()}")
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.5):
                    return
            except OSError:
                time.sleep(0.5)
        self.stop()
        raise RuntimeError(f"port-forward svc/{service} never accepted a connection")

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()


# --- pins and preflight --------------------------------------------------------


@pytest.fixture(scope="session")
def pins() -> dict[str, Any]:
    """The filled, gitignored ``pins.<E2E_APP>.local.yaml`` first; else the
    committed ``pins.<E2E_APP>.yaml`` or ``pins.yaml`` (which carry FILL_ME
    and refuse preflight — the values are a cluster's, never the repo's).
    The file used is recorded with the run."""
    candidates = [HERE / f"pins.{E2E_APP}.local.yaml", HERE / f"pins.{E2E_APP}.yaml", HERE / "pins.yaml"]
    path = next(p for p in candidates if p.exists())
    with path.open(encoding="utf-8") as f:
        d = yaml.safe_load(f)
    d["_file"] = path.name
    return d


def _placeholders(d: Any, path: str = "") -> list[str]:
    out: list[str] = []
    if isinstance(d, dict):
        for k, v in d.items():
            out += _placeholders(v, f"{path}.{k}" if path else k)
    elif isinstance(d, str) and ("FILL_ME" in d or d.strip() == "" or "<tag>" in d):
        out.append(path)
    return out


def _script_directory():
    from alembic.script import ScriptDirectory
    from data_governance.db.migrate import _alembic_config
    return ScriptDirectory.from_config(_alembic_config())


def repo_alembic_head() -> str:
    """The single head revision of this checkout's migrations, via Alembic's own
    script directory (not a file-name sort: revision ids and file dates differ,
    e.g. 0010 is dated after 0014)."""
    heads = _script_directory().get_heads()
    assert len(heads) == 1, f"migrations have {len(heads)} heads: {heads}"
    return heads[0]


def repo_alembic_revisions() -> set[str]:
    """Every revision id this checkout's migrations directory knows."""
    return {r.revision for r in _script_directory().walk_revisions()}


def accepted_alembic_head(expected: str, accepted: str | None, known: set[str]) -> tuple[str, str | None]:
    """The head preflight requires, and why an override was refused.

    ``E2E_ACCEPT_ALEMBIC_HEAD`` names a revision of ANOTHER branch deployed on
    top of this checkout's schema. Such a revision is one this checkout does
    not have: an override naming a revision of this checkout (its own head,
    or an older one) would let a run proceed on the wrong schema, so it is
    refused rather than honoured."""
    if not accepted:
        return expected, None
    if accepted in known:
        return expected, (f"E2E_ACCEPT_ALEMBIC_HEAD={accepted!r} is a revision of this checkout "
                          f"(head {expected!r}); the override names a later revision of another "
                          f"branch, never one of this checkout's own")
    return accepted, None


def _git_head(path: Path) -> str:
    return subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()


def _ollama_digest(model: str) -> str | None:
    if shutil.which("ollama") is None:
        return None
    p = subprocess.run(["ollama", "show", model, "--modelfile"], capture_output=True, text=True)
    if p.returncode != 0:
        return None
    m = re.search(r"sha256[-:]([0-9a-f]{64})", p.stdout)
    return f"sha256:{m.group(1)}" if m else None


@dataclass
class Preflight:
    observed: dict[str, Any] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)

    def expect(self, key: str, expected: Any, observed: Any) -> None:
        self.observed[key] = {"expected": expected, "observed": observed}
        if expected != observed:
            self.problems.append(f"{key}: expected {expected!r}, observed {observed!r}")

    def expect_suffix(self, key: str, expected_suffix: str, observed: str | None) -> None:
        self.observed[key] = {"expected_suffix": expected_suffix, "observed": observed}
        if not observed or not observed.endswith(expected_suffix):
            self.problems.append(f"{key}: expected …{expected_suffix}, observed {observed!r}")


@pytest.fixture(scope="session", autouse=True)
def preflight(env: Env, kube: Kube, pins: dict[str, Any], run_record) -> Preflight:
    """Refuse to send traffic unless the cluster is exactly what pins.yaml
    describes. Every check records expected and observed; all mismatches
    are reported together."""
    pf = Preflight()
    holes = _placeholders(pins)
    if holes:
        pytest.fail("pins.yaml has unfilled values: " + ", ".join(holes)
                    + " (docs/LIVE-E2E.md → 'Fill pins.<app>.local.yaml')")

    # The catalog script uses plain kubectl (no --context), so the current
    # context must be the one this run targets.
    current = subprocess.run(["kubectl", "config", "current-context"],
                             capture_output=True, text=True).stdout.strip()
    pf.expect("kube.current_context", env.kube_context, current)

    pf.expect("agent_examples_snp.commit", pins["agent_examples_snp"]["commit"],
              _git_head(env.agent_examples_snp))
    pf.expect("cortex.commit", pins["cortex"]["commit"], _git_head(env.cortex_dir))
    pf.observed["dg.commit"] = {"observed": _git_head(REPO), "recorded": pins["dg"].get("commit")}

    # Contract vendored byte-identical on both sides.
    dg_contract = REPO / "docs" / "sidecar-wire-contract.md"
    # cortex flattened authbridge/ into its root (2026-09); either layout is
    # a valid clone, the pin is the commit.
    cx_contract = next((p for p in (env.cortex_dir / "docs" / "lineage-wire-contract.md",
                                    env.cortex_dir / "authbridge" / "docs" / "lineage-wire-contract.md")
                        if p.exists()), env.cortex_dir / "docs" / "lineage-wire-contract.md")
    if dg_contract.exists() and cx_contract.exists():
        pf.expect("contract.byte_identical", True, dg_contract.read_bytes() == cx_contract.read_bytes())
        # The pin names the contract version this tier was written against; the
        # file's title line is the version on the branch under test.
        m = re.search(r"\(v(\d+\.\d+\.\d+)\)", dg_contract.read_text(encoding="utf-8").splitlines()[0])
        pf.expect("contract.version", pins["contract"]["version"], m.group(1) if m else None)
    else:
        pf.problems.append(f"contract files missing: {dg_contract.exists()=} {cx_contract.exists()=}")

    # App workloads: pinned shim image, sidecar present, pod 2/2.
    for d in ATTACHED_WORKLOADS:
        pods = kube.json(["get", "pod", "-l", f"app.kubernetes.io/name={d}"], ns=env.app_ns)["items"]
        running = [p for p in pods if p["status"].get("phase") == "Running"
                   and not p["metadata"].get("deletionTimestamp")]  # a terminating pod is not running
        if len(running) != 1:
            pf.problems.append(f"{d}: expected 1 running pod, found {len(running)}")
            continue
        pod = running[0]
        statuses = {c["name"]: c for c in pod["status"].get("containerStatuses", [])}
        inits = {c["name"]: c for c in pod["status"].get("initContainerStatuses", [])}
        app_container = next((c for c in statuses if c not in ("envoy-proxy",)), None)
        pf.expect_suffix(f"{d}.image_id", pins["agent_examples_snp"]["shim_image_id"],
                         statuses.get(app_container, {}).get("imageID") if app_container else None)
        sidecar = statuses.get("envoy-proxy") or inits.get("envoy-proxy")
        pf.expect(f"{d}.envoy_proxy_ready", True, bool(sidecar and sidecar.get("ready")))
        pf.expect(f"{d}.proxy_init_present", True, "proxy-init" in inits)
        if "proxy-init" in inits:
            # The pod's own record, not only the node's image store: a re-attach
            # with another init image must be refused here, by workload.
            pf.expect_suffix(f"{d}.proxy_init_image_id", pins["cortex"]["proxy_init_image_id"],
                             inits["proxy-init"].get("imageID"))
        if sidecar:
            pf.expect_suffix(f"{d}.sidecar_image_id", pins["cortex"]["sidecar_image_id"],
                             sidecar.get("imageID"))
    for w in PLAIN_IMAGE_WORKLOADS:
        pods = [p for p in kube.json(["get", "pod", "-l", f"app.kubernetes.io/name={w}"],
                                     ns=env.app_ns)["items"] if not p["metadata"].get("deletionTimestamp")]
        img = next((c.get("imageID") for p in pods for c in p["status"].get("containerStatuses", [])), None)
        pf.expect_suffix(f"{w}.image_id", pins["agent_examples_snp"]["image_id"], img)

    # Every agent resolved every peer at startup. The app resolves peers once
    # and never retries: an agent that came up while a peer was still rolling
    # runs for its whole life without that peer, and every turn silently lacks
    # the delegation (a fleet-wide roll does it). The pod's own
    # log says so; refuse before any traffic.
    for d in AGENT_WORKLOADS:
        try:
            # the whole log of the current container: a ledger-writing agent
            # pushes its startup lines far past any fixed tail
            log = kube.run(["logs", f"deploy/{d}", "-c", "agent"], ns=env.app_ns, timeout=180)
        except RuntimeError as e:
            pf.problems.append(f"{d}: cannot read the agent log to check peer resolution: {e}")
            continue
        if "ready:" not in log:
            pf.problems.append(f"{d}: the agent log of the current container shows no 'ready:' line")
        failed = sorted({ln.split("peer failed", 1)[1].split(":")[0].strip()
                         for ln in log.splitlines() if "peer failed" in ln})
        pf.expect(f"{d}.peers_resolved", [], failed)

    # LLM pins on the four agents.
    for d in AGENT_WORKLOADS:
        dep = kube.json(["get", "deploy", d], ns=env.app_ns)
        envs = {e["name"]: e.get("value") for c in dep["spec"]["template"]["spec"]["containers"]
                for e in c.get("env", [])}
        pf.expect(f"{d}.LLM_URL", pins["llm"]["url"], envs.get("LLM_URL"))
        pf.expect(f"{d}.LLM_MODEL", pins["llm"]["model"], envs.get("LLM_MODEL"))
    pf.expect("llm.digest", pins["llm"]["digest"],
              _ollama_digest(pins["llm"]["model"]) or "no digest: the ollama CLI is not on PATH or the model is not pulled")

    # DG pods, OPA image, migration head.
    dg_pods = kube.json(["get", "pod", "-l", "app.kubernetes.io/part-of=data-governance"],
                        ns=env.dg_ns)["items"]
    images = {c["name"]: c.get("imageID") for p in dg_pods
              for c in p["status"].get("containerStatuses", [])}
    pf.observed["dg.container_image_ids"] = images
    for name, key in (("receiver", "receiver_image_id"), ("classification", "classification_image_id")):
        got = next((v for k, v in images.items() if name in k), None)
        pf.expect_suffix(f"dg.{name}.image_id", pins["dg"][key], got)
    opa_pods = kube.json(["get", "pod", "-l", "app.kubernetes.io/name=opa"], ns=env.dg_ns)["items"]
    opa_image = next((c["image"] for p in opa_pods for c in p["spec"]["containers"]), None)
    pf.expect("dg.opa_image", pins["dg"]["opa_image"], opa_image)

    # Node image store: every pinned image id present. Read through podman:
    # the tier runs on podman + kind only (docs/LIVE-E2E.md); a docker host
    # is refused here, before any traffic.
    if shutil.which("podman") is None:
        pf.problems.append("podman is not on PATH: the tier needs podman + kind (a docker host is not supported)")
        crictl = None
    else:
        crictl = subprocess.run(
            ["podman", "exec", env.kind_node, "crictl", "images", "--digests", "--no-trunc"],
            capture_output=True, text=True)
    if crictl is None:
        pass
    elif crictl.returncode != 0:
        pf.problems.append(f"crictl on {env.kind_node} failed: {crictl.stderr.strip()}")
    else:
        for key in ("shim_image_id", "image_id"):
            digest = pins["agent_examples_snp"][key].split(":")[-1]
            pf.expect(f"node.has.{key}", True, digest in crictl.stdout)
        for key in ("sidecar_image_id", "proxy_init_image_id"):
            digest = pins["cortex"][key].split(":")[-1]
            pf.expect(f"node.has.{key}", True, digest in crictl.stdout)

    run_record.write("preflight.json", pf.observed)
    if pf.problems:
        pytest.fail("preflight refused the cluster:\n  " + "\n  ".join(pf.problems))
    return pf


# --- database and API ----------------------------------------------------------


class Dg:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def q(self, sql: str, params: tuple | list = ()) -> list[tuple]:
        with psycopg.connect(self.dsn) as conn:
            return conn.execute(sql, params).fetchall()

    def one(self, sql: str, params: tuple | list = ()) -> tuple:
        rows = self.q(sql, params)
        assert len(rows) == 1, f"expected one row, got {len(rows)}: {sql}"
        return rows[0]


@pytest.fixture(scope="session")
def dg(env: Env, kube: Kube, pins: dict[str, Any], preflight, run_record) -> Iterator[Dg]:
    secret = kube.json(["get", "secret", "data-governance-postgres"], ns=env.dg_ns)["data"]
    dec = {k: base64.b64decode(v).decode() for k, v in secret.items()}
    pf = PortForward(kube, ns=env.dg_ns, service="data-governance-postgres", remote_port=5432)
    dsn = (f"postgresql://{dec['POSTGRES_USER']}:{dec['POSTGRES_PASSWORD']}"
           f"@127.0.0.1:{pf.port}/{dec['POSTGRES_DB']}")
    d = Dg(dsn)
    # The deployed schema must be the schema of THIS checkout: the head is read
    # off the migrations directory (the same one the pods' migrate init
    # container runs), never hand-copied into pins.yaml, so adding a migration
    # moves the expectation by itself and a cluster left on an older head is
    # refused with both values named.
    (head,) = d.one("SELECT version_num FROM alembic_version")
    checkout_head = repo_alembic_head()
    # E2E_ACCEPT_ALEMBIC_HEAD=<rev>: run against a cluster whose DG stack is a
    # LATER revision than this checkout (another branch's processors on top of
    # the same derived tables). Named explicitly, recorded in the preflight
    # and in the report header; never a wildcard, never a revision this
    # checkout has (accepted_alembic_head).
    accepted = os.environ.get("E2E_ACCEPT_ALEMBIC_HEAD")
    expected_head, refused = accepted_alembic_head(checkout_head, accepted, repo_alembic_revisions())
    preflight.observed["dg.alembic_head"] = {"expected": expected_head, "observed": head,
                                             **({"checkout_head": checkout_head, "accepted_override": accepted} if accepted else {}),
                                             **({"override_refused": refused} if refused else {})}
    run_record.write("preflight.json", preflight.observed)  # the head check lands after the file was first written
    assert not refused, refused
    assert head == expected_head, (
        f"alembic head {head!r} on the cluster != {expected_head!r}, the head of "
        f"{REPO / 'data_governance/db/migrations/versions'} in this checkout — "
        f"migrate the cluster (kubectl rollout restart the DG pods) or check out the deployed revision")
    from data_governance import db  # ported code may use db.transaction()
    db.close_pool()
    db.configure(dsn)
    try:
        yield d
    finally:
        db.close_pool()
        pf.stop()


@pytest.fixture(scope="session")
def api(env: Env) -> Iterator[httpx.Client]:
    with httpx.Client(base_url=env.dg_api, timeout=30.0) as c:
        yield c


@dataclass(frozen=True)
class Caps:
    alerts: bool
    health: bool
    ledger: bool = False  # the app pods write a ledger (ROSSOCTL_LEDGER=stdout)


@pytest.fixture(scope="session")
def capabilities(env: Env, kube: Kube, dg: Dg, api: httpx.Client, run_record) -> Caps:
    """What the deployed branch runs. Never assumed: the alerts processor
    and the health route exist only on some risk branches."""
    has_alerts_deploy = kube.exists("deploy", "data-governance-risk-alerts", ns=env.dg_ns)
    (has_seq,) = dg.one(
        "SELECT count(*) FROM information_schema.columns "
        "WHERE table_name = 'trace_risk_records' AND column_name = 'seq'")
    alerts = has_alerts_deploy and has_seq == 1
    try:
        health = api.get("/risk/health").status_code == 200
    except httpx.HTTPError:
        health = False
    deploys = kube.json(["get", "deploy"], ns=env.app_ns)["items"]
    ledger_on = {d["metadata"]["name"] for d in deploys
                 if any(e.get("name") == "ROSSOCTL_LEDGER" and e.get("value") == "stdout"
                        for c in d["spec"]["template"]["spec"]["containers"] for e in c.get("env", []))}
    ledger = set(ATTACHED_WORKLOADS) <= ledger_on
    caps = Caps(alerts=alerts, health=health, ledger=ledger)
    run_record.write("capabilities.json", {"alerts": alerts, "health": health, "ledger": ledger,
                                           "ledger_on": sorted(ledger_on),
                                           "alerts_deploy": has_alerts_deploy,
                                           "trace_risk_records.seq": bool(has_seq)})
    return caps


# --- test catalog through the production path ----------------------------------


def _opa_decide(kube: Kube, env: Env, body: dict) -> dict:
    pf = PortForward(kube, ns=env.dg_ns, service="opa", remote_port=8181)
    try:
        r = httpx.post(f"http://127.0.0.1:{pf.port}{OPA_DECISION_PATH}", json=body, timeout=10)
        r.raise_for_status()
        return r.json().get("result", {})
    finally:
        pf.stop()


def _apply_catalog(env: Env, kube: Kube, path: Path | None) -> str:
    cmd = [str(REPO / "deploy" / "create-opa-configmap.sh")]
    if path is not None:
        cmd.append(str(path))
    p = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"create-opa-configmap.sh failed: {p.stderr}")
    kube.rollout_restart("opa", ns=env.dg_ns)
    return p.stdout


@pytest.fixture(scope="session")
def catalog(env: Env, kube: Kube, api: httpx.Client, run_record, preflight) -> Iterator[dict]:
    """Deploy tests/live/catalog_e2e.json exactly the way a rule change is
    deployed, prove OPA serves it, and restore the shipped catalog on
    teardown, proving that too."""
    before = kube.run(["get", "cm", "opa-policy", "-o", "yaml"], ns=env.dg_ns)
    run_record.write("catalog-before.yaml", before, raw=True)
    shipped = _opa_decide(kube, env, SHIPPED_PROOF_INPUT)
    assert "DG-001" in shipped.get("triggered_rules", []), (
        f"shipped catalog not serving before the swap: {shipped}")

    out = _apply_catalog(env, kube, CATALOG_E2E)
    run_record.write("catalog-apply.log", out, raw=True)
    proof = _opa_decide(kube, env, PROOF_INPUT)
    assert sorted(proof.get("triggered_rules", [])) == ["E2E-INT-DATA", "E2E-PI-INT"], (
        f"test catalog not served by OPA: {proof}")
    assert (proof["risk_level"], proof["enforcement_type"]) == ("medium", "escalate"), proof
    run_record.write("catalog-proof.json", proof)
    # Evidence for the catalog seam: the API serves the image-baked catalog,
    # OPA the applied one. Recorded, never asserted on (docs/LIVE-E2E.md).
    try:
        run_record.write("rules_api.json", api.get("/risk/rules").json())
    except (httpx.HTTPError, ValueError) as e:
        run_record.write("rules_api.json", {"error": str(e)})
    try:
        yield {"proof": proof, "version": "1.0.0-e2e"}
    finally:
        out = _apply_catalog(env, kube, None)
        run_record.write("catalog-restore.log", out, raw=True)
        restored = _opa_decide(kube, env, PROOF_INPUT)
        shipped_again = _opa_decide(kube, env, SHIPPED_PROOF_INPUT)
        after = kube.run(["get", "cm", "opa-policy", "-o", "yaml"], ns=env.dg_ns)
        run_record.write("catalog-after.yaml", after, raw=True)
        run_record.write("catalog-restored-proof.json", {"e2e_input": restored, "shipped_input": shipped_again})
        # Both halves: the E2E rules are gone (the fallback alone fires) AND the
        # shipped rules answer again (an empty catalog would pass the first).
        assert restored.get("triggered_rules") == ["0000"], (
            f"shipped catalog NOT restored — cluster left dirty: {restored}")
        assert "DG-001" in shipped_again.get("triggered_rules", []), (
            f"shipped catalog NOT serving after the restore — cluster left dirty: {shipped_again}")


# --- the abandoned-exchange sink -----------------------------------------------


@pytest.fixture(scope="session")
def sink(env: Env, kube: Kube, pins: dict[str, Any], preflight, state) -> Iterator[str]:
    manifest = SINK_MANIFEST.read_text(encoding="utf-8").replace(
        "__IMAGE__", pins["agent_examples_snp"]["image"])
    kube.run(["apply", "-f", "-"], ns=env.app_ns, input=manifest)
    for d in ("e2e-sink", "e2e-reflect"):
        kube.run(["rollout", "status", f"deploy/{d}", "--timeout=120s"], ns=env.app_ns, timeout=150)
    # A Ready pod is not yet a reachable Service: the endpoints controller
    # fills the Service's addresses a moment later, and until then the
    # client's sidecar gets a fast 503 from kube-proxy (observed live: a
    # 503 in 3 ms instead of a 45 s hold). Wait for the endpoints themselves.
    for svc in ("e2e-sink", "e2e-reflect"):
        for _ in range(60):
            ips = kube.run(["get", "endpoints", svc,
                            "-o", "jsonpath={.subsets[*].addresses[*].ip}"], ns=env.app_ns).strip()
            if ips:
                break
            time.sleep(1)
        else:
            raise RuntimeError(f"{svc} Service never got an endpoint")
    # Endpoints listed is still not the data path: kube-proxy keeps its
    # reject rule for a Service that had no endpoints until its next sync,
    # and a probe in that window gets a 503 in milliseconds (observed live).
    # The sink's own readiness signal is that a short-timeout request HANGS:
    # only then does the client reach the socket that never answers. Probe
    # that way, under a trace id registered as the test's own.
    from tests.live import driver
    ready_trace = driver.mint_trace_id()
    state.setdefault("traces", set()).add(ready_trace)
    for _ in range(30):
        r = driver.probe(kube, trace_id=ready_trace, parent_id=driver.mint_span_id(),
                         label="sink_ready", url=driver.SINK_URL, host=driver.SINK_HOST,
                         payload={"ping": 1}, timeout=2)
        if r.outcome == "client_timeout":
            break
        time.sleep(1)
    else:
        raise RuntimeError(f"e2e-sink never held a connection: last {r}")
    # The reflector answers; its readiness is a 200 with a JSON-RPC result.
    for _ in range(30):
        r = driver.probe(kube, trace_id=ready_trace, parent_id=driver.mint_span_id(),
                         label="reflect_ready", url=driver.REFLECT_URL, host=driver.REFLECT_HOST,
                         payload=driver.benign(), timeout=5)
        if r.outcome == "ok" and r.status == 200:
            break
        time.sleep(1)
    else:
        raise RuntimeError(f"e2e-reflect never answered 200: last {r}")
    try:
        yield "e2e-sink"
    finally:
        kube.run(["delete", "-f", "-", "--ignore-not-found"], ns=env.app_ns, input=manifest)


# --- run record ----------------------------------------------------------------


class RunRecord:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.outcomes: dict[str, dict] = {}

    def write(self, name: str, obj: Any, *, raw: bool = False) -> Path:
        p = self.root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        if raw:
            p.write_text(obj if isinstance(obj, str) else str(obj), encoding="utf-8")
        else:
            p.write_text(json.dumps(obj, indent=1, default=str), encoding="utf-8")
        return p

    def write_table(self) -> None:
        """The whole run as one unpivoted table (every scenario's checks.json
        concatenated): checks.csv and checks.md beside verdict.json."""
        import csv
        import io
        rows = []
        for d in sorted(self.root.iterdir()):
            f = d / "checks.json"
            if d.is_dir() and f.exists():
                rows += json.loads(f.read_text(encoding="utf-8"))
        cols = ["case", "id", "property", "expected", "actual", "status", "comment"]
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({k: (r[k] if isinstance(r[k], str) or r[k] is None else json.dumps(r[k], default=str, sort_keys=True))
                        for k in cols})
        self.write("checks.csv", buf.getvalue(), raw=True)
        self.write("checks.json", rows)

    def scenario(self, name: str):
        rec = self

        class _S:
            def write(self, fname: str, obj: Any, *, raw: bool = False) -> Path:
                return rec.write(f"{name}/{fname}", obj, raw=raw)
        return _S()


@pytest.fixture(scope="session")
def run_record(env: Env, pins: dict[str, Any]) -> Iterator[RunRecord]:
    sha = _git_head(REPO)[:7]
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    rec = RunRecord(env.run_dir / f"{stamp}-{sha}")
    rec.write("env.json", {k: str(v) for k, v in env.__dict__.items()})
    used = HERE / pins["_file"]
    rec.write(used.name, used.read_text(encoding="utf-8"), raw=True)
    try:
        yield rec
    finally:
        rec.write("verdict.json", rec.outcomes)
        rec.write_table()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    rec = item.session.config.stash.get(_RECORD_KEY, None)
    if rec is None:
        return
    # A skip raised by a fixture happens in the setup phase; a skip or xfail
    # raised in the test body in the call phase. Both are recorded, with the
    # reason, and an xfail is told apart from a skip (pytest files both as
    # "skipped").
    if rep.when == "setup" and rep.skipped:
        reason = rep.longrepr[2] if isinstance(rep.longrepr, tuple) else str(rep.longrepr)
        rec.outcomes[item.name] = {"outcome": "skipped", "duration": round(rep.duration, 1), "longrepr": None,
                                   "reason": reason.removeprefix("Skipped: ")}
        return
    if rep.when != "call":
        return
    entry = {"outcome": rep.outcome, "duration": round(rep.duration, 1),
             "longrepr": str(rep.longrepr)[-2000:] if rep.failed else None}
    if hasattr(rep, "wasxfail"):
        entry["outcome"] = "xfail"
        entry["reason"] = rep.wasxfail
    elif rep.skipped:
        reason = rep.longrepr[2] if isinstance(rep.longrepr, tuple) else str(rep.longrepr)
        entry["reason"] = reason.removeprefix("Skipped: ")
    rec.outcomes[item.name] = entry


_RECORD_KEY = pytest.StashKey[RunRecord]()


@pytest.fixture(scope="session", autouse=True)
def _stash_record(request, run_record: RunRecord) -> None:
    request.config.stash[_RECORD_KEY] = run_record


@pytest.fixture(scope="session")
def fleet():
    """The app's declaration (tests/live/apps/<E2E_APP>.yaml): the one
    app-specific input of the audit, and the source of preflight's lists."""
    return _require_fleet()


@pytest.fixture(scope="session")
def attach(env: Env, kube: Kube, preflight) -> dict[str, set[int]]:
    """What the attach actually excluded from interception, per workload:
    the proxy-init's OUTBOUND_PORTS_EXCLUDE on the running pod. The audit
    checks every declared non-HTTP egress port against it (S14)."""
    out: dict[str, set[int]] = {}
    for d in ATTACHED_WORKLOADS:
        pods = kube.json(["get", "pod", "-l", f"app.kubernetes.io/name={d}"], ns=env.app_ns)["items"]
        for p in pods:
            if p["metadata"].get("deletionTimestamp"):
                continue
            for c in p["spec"].get("initContainers", []):
                if c["name"] == "proxy-init":
                    val = next((e.get("value") or "" for e in c.get("env", [])
                                if e["name"] == "OUTBOUND_PORTS_EXCLUDE"), "")
                    out[d] = {int(x) for x in val.split(",") if x.strip().isdigit()}
    return out


@pytest.fixture(scope="session")
def state() -> dict[str, Any]:
    """Cross-scenario state: the baseline trace id and the probe parent
    ids the later scenarios address. Scenarios run in file order."""
    return {}
