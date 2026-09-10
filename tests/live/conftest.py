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

ATTACHED_WORKLOADS = [
    "search-destinations", "create-booking", "get-payment-info", "send-notification",
    "get-weather", "get-flights", "charge-card",
    "payment-agent", "research-agent", "booking-agent", "travel-advisor", "demo-client",
]
AGENT_WORKLOADS = ["payment-agent", "research-agent", "booking-agent", "travel-advisor"]

# The catalog fixture's proof input: fires E2E-PI-INT and E2E-INT-DATA
# under the test catalog and nothing under the shipped one.
PROOF_INPUT = {
    "input": {
        "event_type": "internal_sharing",
        "data_items": [{"regulatory_tags": ["PI"], "classification_level": "INTERNAL"}],
        "data_destinations": [{"data_destination_categories": ["internal"],
                               "data_destination_trust_level": "UNKNOWN"}],
        "requested_actions": ["send"],
    }
}
SHIPPED_PROOF_INPUT = {
    "input": {
        "event_type": "external_sharing",
        "data_items": [{"regulatory_tags": ["PII"], "classification_level": "CONFIDENTIAL"}],
        "data_destinations": [{"data_destination_categories": ["external"],
                               "data_destination_trust_level": "UNTRUSTED_EXTERNAL"}],
        "requested_actions": ["send"],
    }
}
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
            app_ns=g("E2E_APP_NS", "travel-advisor"),
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
    with (HERE / "pins.yaml").open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _placeholders(d: Any, path: str = "") -> list[str]:
    out: list[str] = []
    if isinstance(d, dict):
        for k, v in d.items():
            out += _placeholders(v, f"{path}.{k}" if path else k)
    elif isinstance(d, str) and ("FILL_ME" in d or d.strip() == ""):
        out.append(path)
    return out


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
                    + " (docs/LIVE-E2E.md → 'Fill pins.yaml')")

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
    cx_contract = env.cortex_dir / "authbridge" / "docs" / "lineage-wire-contract.md"
    if dg_contract.exists() and cx_contract.exists():
        pf.expect("contract.byte_identical", True, dg_contract.read_bytes() == cx_contract.read_bytes())
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
        if sidecar:
            pf.expect_suffix(f"{d}.sidecar_image_id", pins["cortex"]["sidecar_image_id"],
                             sidecar.get("imageID"))
    psp = [p for p in kube.json(["get", "pod", "-l", "app.kubernetes.io/name=psp-mock"],
                                ns=env.app_ns)["items"] if not p["metadata"].get("deletionTimestamp")]
    psp_img = next((c.get("imageID") for p in psp for c in p["status"].get("containerStatuses", [])), None)
    pf.expect_suffix("psp-mock.image_id", pins["agent_examples_snp"]["image_id"], psp_img)

    # LLM pins on the four agents.
    for d in AGENT_WORKLOADS:
        dep = kube.json(["get", "deploy", d], ns=env.app_ns)
        envs = {e["name"]: e.get("value") for c in dep["spec"]["template"]["spec"]["containers"]
                for e in c.get("env", [])}
        pf.expect(f"{d}.LLM_URL", pins["llm"]["url"], envs.get("LLM_URL"))
        pf.expect(f"{d}.LLM_MODEL", pins["llm"]["model"], envs.get("LLM_MODEL"))
    pf.expect("llm.digest", pins["llm"]["digest"], _ollama_digest(pins["llm"]["model"]))

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

    # Node image store: every pinned image id present.
    crictl = subprocess.run(
        ["podman", "exec", env.kind_node, "crictl", "images", "--digests", "--no-trunc"],
        capture_output=True, text=True)
    if crictl.returncode != 0:
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
def dg(env: Env, kube: Kube, pins: dict[str, Any], preflight) -> Iterator[Dg]:
    secret = kube.json(["get", "secret", "data-governance-postgres"], ns=env.dg_ns)["data"]
    dec = {k: base64.b64decode(v).decode() for k, v in secret.items()}
    pf = PortForward(kube, ns=env.dg_ns, service="data-governance-postgres", remote_port=5432)
    dsn = (f"postgresql://{dec['POSTGRES_USER']}:{dec['POSTGRES_PASSWORD']}"
           f"@127.0.0.1:{pf.port}/{dec['POSTGRES_DB']}")
    d = Dg(dsn)
    (head,) = d.one("SELECT version_num FROM alembic_version")
    assert head == pins["dg"]["alembic_head"], (
        f"alembic head {head!r} != pinned {pins['dg']['alembic_head']!r}")
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
    caps = Caps(alerts=alerts, health=health)
    run_record.write("capabilities.json", {"alerts": alerts, "health": health,
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
        after = kube.run(["get", "cm", "opa-policy", "-o", "yaml"], ns=env.dg_ns)
        run_record.write("catalog-after.yaml", after, raw=True)
        run_record.write("catalog-restored-proof.json", restored)
        assert restored.get("triggered_rules") == ["0000"], (
            f"shipped catalog NOT restored — cluster left dirty: {restored}")


# --- the abandoned-exchange sink -----------------------------------------------


@pytest.fixture(scope="session")
def sink(env: Env, kube: Kube, pins: dict[str, Any], preflight, state) -> Iterator[str]:
    manifest = SINK_MANIFEST.read_text(encoding="utf-8").replace(
        "__IMAGE__", pins["agent_examples_snp"]["image"])
    kube.run(["apply", "-f", "-"], ns=env.app_ns, input=manifest)
    kube.run(["rollout", "status", "deploy/e2e-sink", "--timeout=120s"], ns=env.app_ns,
             timeout=150)
    # A Ready pod is not yet a reachable Service: the endpoints controller
    # fills the Service's addresses a moment later, and until then the
    # client's sidecar gets a fast 503 from kube-proxy (observed live: a
    # 503 in 3 ms instead of a 45 s hold). Wait for the endpoints themselves.
    for _ in range(60):
        ips = kube.run(["get", "endpoints", "e2e-sink",
                        "-o", "jsonpath={.subsets[*].addresses[*].ip}"], ns=env.app_ns).strip()
        if ips:
            break
        time.sleep(1)
    else:
        raise RuntimeError("e2e-sink Service never got an endpoint")
    # Endpoints listed is still not the data path: kube-proxy keeps its
    # reject rule for a Service that had no endpoints until its next sync,
    # and a probe in that window gets a 503 in milliseconds (runs 8 and 9).
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

    def scenario(self, name: str):
        rec = self

        class _S:
            def write(self, fname: str, obj: Any, *, raw: bool = False) -> Path:
                return rec.write(f"{name}/{fname}", obj, raw=raw)
        return _S()


@pytest.fixture(scope="session")
def run_record(env: Env) -> Iterator[RunRecord]:
    sha = _git_head(REPO)[:7]
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    rec = RunRecord(env.run_dir / f"{stamp}-{sha}")
    rec.write("env.json", {k: str(v) for k, v in env.__dict__.items()})
    rec.write("pins.yaml", (HERE / "pins.yaml").read_text(encoding="utf-8"), raw=True)
    try:
        yield rec
    finally:
        rec.write("verdict.json", rec.outcomes)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    if rep.when != "call":
        return
    rec = item.session.config.stash.get(_RECORD_KEY, None)
    if rec is not None:
        rec.outcomes[item.name] = {"outcome": rep.outcome, "duration": round(rep.duration, 1),
                                   "longrepr": str(rep.longrepr)[-2000:] if rep.failed else None}


_RECORD_KEY = pytest.StashKey[RunRecord]()


@pytest.fixture(scope="session", autouse=True)
def _stash_record(request, run_record: RunRecord) -> None:
    request.config.stash[_RECORD_KEY] = run_record


@pytest.fixture(scope="session")
def state() -> dict[str, Any]:
    """Cross-scenario state: the baseline trace id and the probe parent
    ids the later scenarios address. Scenarios run in file order."""
    return {}
