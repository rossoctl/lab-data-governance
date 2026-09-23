"""Contract tests for the Rossoctl-owned proxy pipeline reconciler (#256)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
RECONCILER = REPO_ROOT / "deploy" / "lineage-attach" / "reconcile-existing-proxy.py"


def _run_render(config: str) -> subprocess.CompletedProcess[str]:
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
