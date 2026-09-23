"""Contract tests for the Rossoctl-owned proxy pipeline reconciler (#256)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
RECONCILER = REPO_ROOT / "deploy" / "lineage-attach" / "reconcile-existing-proxy.py"


def _run_render(
    config: str,
    *,
    forward_proxy_addr: str = ":8084",
    reverse_proxy_addr: str = ":8080",
    reverse_proxy_backend: str = "http://127.0.0.1:8081",
) -> subprocess.CompletedProcess[str]:
    document = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": "authbridge-config-travel-advisor"},
        "data": {"config.yaml": config, "owned-by-operator": "keep-me"},
    }
    return subprocess.run(
        [
            "python3",
            str(RECONCILER),
            "render",
            "--self-id",
            "travel-advisor",
            "--otel-endpoint",
            "otel-collector.rossoctl-system.svc.cluster.local:4317",
            "--forward-proxy-addr",
            forward_proxy_addr,
            "--reverse-proxy-addr",
            reverse_proxy_addr,
            "--reverse-proxy-backend",
            reverse_proxy_backend,
        ],
        input=json.dumps(document),
        capture_output=True,
        text=True,
        check=False,
    )


def test_render_preserves_auth_and_reconciles_both_directions() -> None:
    result = _run_render(
        """listener:
  forward_proxy_addr: :8084
  reverse_proxy_addr: :8080
  reverse_proxy_backend: http://127.0.0.1:8081
mode: proxy-sidecar
pipeline:
  inbound:
    plugins:
    - config:
        issuer: https://issuer.example
      name: jwt-validation
  outbound:
    plugins:
    - config:
        default_policy: passthrough
      name: token-exchange
spiffe:
  socket: unix:///spiffe-workload-api/spire-agent.sock
"""
    )

    assert result.returncode == 0, result.stderr
    rendered = json.loads(result.stdout)
    assert rendered["data"]["owned-by-operator"] == "keep-me"
    body = rendered["data"]["config.yaml"]
    assert body.index("name: jwt-validation") < body.index("name: a2a-parser")
    assert body.index("name: token-exchange") < body.rindex("name: a2a-parser")
    assert body.count("name: a2a-parser") == 2
    assert body.count("name: mcp-parser") == 2
    assert body.count("name: inference-parser") == 2
    assert body.count("name: lineage-telemetry") == 2
    assert body.count('self_id: "travel-advisor"') == 2
    assert body.count(
        'namespace_file: "/var/run/secrets/kubernetes.io/serviceaccount/namespace"'
    ) == 2
    assert "reverse_proxy_addr: :8080" in body
    assert "forward_proxy_addr: :8084" in body

    rerun = subprocess.run(
        [
            "python3",
            str(RECONCILER),
            "render",
            "--self-id",
            "travel-advisor",
            "--otel-endpoint",
            "otel-collector.rossoctl-system.svc.cluster.local:4317",
            "--forward-proxy-addr",
            ":8084",
            "--reverse-proxy-addr",
            ":8080",
            "--reverse-proxy-backend",
            "http://127.0.0.1:8081",
        ],
        input=result.stdout,
        capture_output=True,
        text=True,
        check=False,
    )
    assert rerun.returncode == 0, rerun.stderr
    assert json.loads(rerun.stdout) == rendered


def test_render_refuses_non_proxy_or_incomplete_listener() -> None:
    for config in (
        "mode: envoy-sidecar\npipeline: {}\n",
        "mode: proxy-sidecar\npipeline: {}\n",
    ):
        result = _run_render(config)
        assert result.returncode == 2
        assert "error:" in result.stderr


def test_render_repairs_listener_drift() -> None:
    result = _run_render(
        """listener:
  forward_proxy_addr: :9999
  reverse_proxy_addr: :9998
  reverse_proxy_backend: http://127.0.0.1:9997
mode: proxy-sidecar
pipeline:
  inbound:
    plugins:
      - name: jwt-validation
  outbound:
    plugins:
      - name: token-exchange
"""
    )

    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)["data"]["config.yaml"]
    assert "forward_proxy_addr: :8084" in body
    assert "reverse_proxy_addr: :8080" in body
    assert "reverse_proxy_backend: http://127.0.0.1:8081" in body
    assert ":999" not in body


def test_render_uses_the_live_tool_listener_contract() -> None:
    result = _run_render(
        """listener:
  forward_proxy_addr: :9999
  reverse_proxy_addr: :9998
  reverse_proxy_backend: http://127.0.0.1:9997
mode: proxy-sidecar
pipeline:
  inbound:
    plugins:
      - name: jwt-validation
  outbound:
    plugins:
      - name: token-exchange
""",
        forward_proxy_addr=":8081",
        reverse_proxy_addr=":8000",
        reverse_proxy_backend="http://127.0.0.1:8001",
    )

    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)["data"]["config.yaml"]
    assert "forward_proxy_addr: :8081" in body
    assert "reverse_proxy_addr: :8000" in body
    assert "reverse_proxy_backend: http://127.0.0.1:8001" in body


def test_pipeline_validation_requires_exact_reconciled_plugin_sequences() -> None:
    rendered = _run_render(
        """listener:
  forward_proxy_addr: :8084
  reverse_proxy_addr: :8080
  reverse_proxy_backend: http://127.0.0.1:8081
mode: proxy-sidecar
pipeline:
  inbound:
    plugins:
      - name: jwt-validation
  outbound:
    plugins:
      - name: token-exchange
"""
    )
    assert rendered.returncode == 0, rendered.stderr
    contract = subprocess.run(
        ["python3", str(RECONCILER), "pipeline-contract"],
        input=rendered.stdout,
        capture_output=True,
        text=True,
        check=False,
    )
    assert contract.returncode == 0, contract.stderr
    expected = json.loads(contract.stdout)
    assert expected["inbound"] == [
        "jwt-validation",
        "a2a-parser",
        "mcp-parser",
        "inference-parser",
        "lineage-telemetry",
    ]

    live = {
        "inbound": [{"name": name} for name in expected["inbound"]],
        "outbound": [{"name": name} for name in expected["outbound"]],
    }
    for direction in ("inbound", "outbound"):
        live[direction][-1]["config"] = {
            "otel_endpoint": "otel-collector.rossoctl-system.svc.cluster.local:4317",
            "capture_io": False,
            "self_id": "travel-advisor",
            "namespace_file": "/var/run/secrets/kubernetes.io/serviceaccount/namespace",
        }

    command = [
        "python3", str(RECONCILER), "pipeline-valid",
        "--self-id", "travel-advisor",
        "--otel-endpoint", "otel-collector.rossoctl-system.svc.cluster.local:4317",
        "--expected", contract.stdout,
    ]
    valid = subprocess.run(
        command, input=json.dumps(live), capture_output=True, text=True, check=False
    )
    assert valid.returncode == 0, valid.stderr

    live["inbound"].insert(2, {"name": "a2a-parser"})
    duplicate = subprocess.run(
        command, input=json.dumps(live), capture_output=True, text=True, check=False
    )
    assert duplicate.returncode == 1

    live["inbound"] = [item for item in live["inbound"] if item["name"] != "jwt-validation"]
    missing_auth = subprocess.run(
        command, input=json.dumps(live), capture_output=True, text=True, check=False
    )
    assert missing_auth.returncode == 1
