"""Contract tests for the namespace AuthBridge pipeline reconciler (#256, #326)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
RECONCILER = REPO_ROOT / "deploy" / "lineage-attach" / "reconcile-existing-proxy.py"


OTEL_ENDPOINT = "otel-collector.rossoctl-system.svc.cluster.local:4317"

# The namespace ConfigMap as the Rossoctl chart emits it.
CHART_CONFIG = """
pipeline:
  inbound:
    plugins:
      - name: jwt-validation
        config:
          issuer: "https://issuer.example"
  outbound:
    plugins:
      - name: token-exchange
        config:
          default_policy: "passthrough"
"""


def _run_render(
    config: str, *, otel_endpoint: str = OTEL_ENDPOINT
) -> subprocess.CompletedProcess[str]:
    document = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": "authbridge-runtime-config"},
        "data": {"config.yaml": config, "owned-by-operator": "keep-me"},
    }
    return subprocess.run(
        ["python3", str(RECONCILER), "render", "--otel-endpoint", otel_endpoint],
        input=json.dumps(document),
        capture_output=True,
        text=True,
        check=False,
    )


def _rendered_config(config: str) -> str:
    result = _run_render(config)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)["data"]["config.yaml"]


def test_render_preserves_auth_and_reconciles_both_directions() -> None:
    result = _run_render(
        """mode: proxy-sidecar
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
    assert "self_id" not in body
    assert body.count(f"otel_endpoint: {OTEL_ENDPOINT}\n") == 2
    assert body.count(
        "namespace_file: /var/run/secrets/kubernetes.io/serviceaccount/namespace\n"
    ) == 2
    assert "spiffe:\n  socket: unix:///spiffe-workload-api/spire-agent.sock\n" in body

    rerun = subprocess.run(
        ["python3", str(RECONCILER), "render", "--otel-endpoint", OTEL_ENDPOINT],
        input=result.stdout,
        capture_output=True,
        text=True,
        check=False,
    )
    assert rerun.returncode == 0, rerun.stderr
    assert json.loads(rerun.stdout) == rendered


def test_render_survives_a_pyyaml_round_trip() -> None:
    # Another writer (an app deploy script) re-dumps the namespace ConfigMap
    # with PyYAML; the canonical bytes must come back unchanged.
    redumped = yaml.safe_dump(yaml.safe_load(_rendered_config(CHART_CONFIG)), sort_keys=False)
    assert _rendered_config(redumped) == redumped
    lineage = yaml.safe_load(redumped)["pipeline"]["outbound"]["plugins"][-1]
    assert lineage == {
        "name": "lineage-telemetry",
        "config": {
            "otel_endpoint": OTEL_ENDPOINT,
            "capture_io": False,
            "namespace_file": "/var/run/secrets/kubernetes.io/serviceaccount/namespace",
        },
    }


def test_render_appends_after_a_last_line_without_newline() -> None:
    # A chart's `config.yaml: |-` block strips the final newline.
    body = _rendered_config(CHART_CONFIG.rstrip("\n"))
    plugins = yaml.safe_load(body)["pipeline"]["outbound"]["plugins"]
    assert [plugin["name"] for plugin in plugins] == [
        "token-exchange", "a2a-parser", "mcp-parser", "inference-parser", "lineage-telemetry",
    ]
    assert plugins[0]["config"] == {"default_policy": "passthrough"}


def test_render_refuses_a_config_without_both_pipeline_directions() -> None:
    for config in (
        "mode: proxy-sidecar\n",
        "pipeline: {}\n",
        "pipeline:\n  inbound:\n    plugins: []\n",
    ):
        result = _run_render(config)
        assert result.returncode == 2
        assert "error:" in result.stderr


def test_render_refuses_a_plugin_item_the_pipeline_contract_cannot_name() -> None:
    result = _run_render(
        """pipeline:
  inbound:
    plugins:
      - {name: jwt-validation}
  outbound:
    plugins:
      - name: token-exchange
"""
    )
    assert result.returncode == 2
    assert "unnamed plugin in pipeline.inbound" in result.stderr


def test_render_refuses_an_endpoint_that_is_not_a_plain_scalar() -> None:
    result = _run_render(CHART_CONFIG, otel_endpoint="collector:4317 # comment")
    assert result.returncode == 2
    assert "not a plain host:port or URL" in result.stderr


def test_pipeline_validation_requires_exact_reconciled_plugin_sequences() -> None:
    rendered = _run_render(
        """pipeline:
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
            "namespace_file": "/var/run/secrets/kubernetes.io/serviceaccount/namespace",
        }

    command = [
        "python3", str(RECONCILER), "pipeline-valid",
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
