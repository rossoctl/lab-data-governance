#!/usr/bin/env python3
"""Reconcile lineage into a Rossoctl-owned AuthBridge proxy ConfigMap.

The platform owns the ConfigMap and all authentication plugins.  This tool
therefore edits only the two ``pipeline.*.plugins`` sequences, preserving every
unmanaged entry and every other ConfigMap field byte-for-byte where practical.
It intentionally uses only the Python standard library so ``dg.sh`` remains a
standalone cluster-management script.
"""

from __future__ import annotations

import argparse
import json
import re
import sys


MANAGED_PLUGINS = (
    "a2a-parser",
    "mcp-parser",
    "inference-parser",
    "lineage-telemetry",
)
NAMESPACE_FILE = "/var/run/secrets/kubernetes.io/serviceaccount/namespace"


class ConfigError(ValueError):
    """The operator ConfigMap is outside the safe reconciliation envelope."""


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _key(line: str, name: str) -> bool:
    return bool(re.match(rf"^\s*{re.escape(name)}:\s*(?:#.*)?$", line.rstrip("\n")))


def _section(lines: list[str], name: str, start: int, end: int, parent_indent: int) -> tuple[int, int]:
    for index in range(start, end):
        line = lines[index]
        if _indent(line) > parent_indent and _key(line, name):
            indent = _indent(line)
            section_end = end
            for cursor in range(index + 1, end):
                candidate = lines[cursor]
                indentless_item = bool(
                    _indent(candidate) == indent and candidate.lstrip().startswith("- ")
                )
                if (
                    candidate.strip()
                    and not candidate.lstrip().startswith("#")
                    and _indent(candidate) <= indent
                    and not indentless_item
                ):
                    section_end = cursor
                    break
            return index, section_end
    raise ConfigError(f"missing {name}: section")


def _plugin_name(block: list[str]) -> str | None:
    for line in block:
        match = re.match(r"^\s*(?:-\s*)?name:\s*['\"]?([^'\"\s#]+)", line)
        if match:
            return match.group(1)
    return None


def _managed_blocks(indent: int, self_id: str, otel_endpoint: str) -> list[str]:
    pad = " " * indent
    child = " " * (indent + 2)
    config = " " * (indent + 4)
    return [
        f"{pad}- name: a2a-parser\n",
        f"{pad}- name: mcp-parser\n",
        f"{pad}- name: inference-parser\n",
        f"{pad}- name: lineage-telemetry\n",
        f"{child}config:\n",
        f'{config}otel_endpoint: "{otel_endpoint}"\n',
        f"{config}capture_io: false\n",
        f'{config}self_id: "{self_id}"\n',
        f'{config}namespace_file: "{NAMESPACE_FILE}"\n',
    ]


def _reconcile_direction(
    lines: list[str], direction: str, self_id: str, otel_endpoint: str
) -> list[str]:
    pipeline_index = next((i for i, line in enumerate(lines) if _key(line, "pipeline")), None)
    if pipeline_index is None:
        raise ConfigError("missing pipeline: section")
    pipeline_indent = _indent(lines[pipeline_index])
    pipeline_end = len(lines)
    for cursor in range(pipeline_index + 1, len(lines)):
        line = lines[cursor]
        if line.strip() and not line.lstrip().startswith("#") and _indent(line) <= pipeline_indent:
            pipeline_end = cursor
            break

    direction_index, direction_end = _section(
        lines, direction, pipeline_index + 1, pipeline_end, pipeline_indent
    )
    direction_indent = _indent(lines[direction_index])
    plugins_index, plugins_end = _section(
        lines, "plugins", direction_index + 1, direction_end, direction_indent
    )
    plugins_indent = _indent(lines[plugins_index])

    first_item = None
    item_indent = None
    for cursor in range(plugins_index + 1, plugins_end):
        match = re.match(r"^(\s*)-\s+", lines[cursor])
        if match:
            first_item = cursor
            item_indent = len(match.group(1))
            break
    if first_item is None or item_indent is None:
        first_item = plugins_index + 1
        item_indent = plugins_indent + 2

    item_starts = [
        cursor
        for cursor in range(first_item, plugins_end)
        if re.match(rf"^ {{{item_indent}}}-\s+", lines[cursor])
    ]
    preserved: list[str] = []
    if item_starts:
        for position, block_start in enumerate(item_starts):
            block_end = item_starts[position + 1] if position + 1 < len(item_starts) else plugins_end
            block = lines[block_start:block_end]
            if _plugin_name(block) not in MANAGED_PLUGINS:
                preserved.extend(block)

    replacement = lines[:first_item] + preserved + _managed_blocks(item_indent, self_id, otel_endpoint)
    replacement.extend(lines[plugins_end:])
    return replacement


def reconcile_config(config: str, self_id: str, otel_endpoint: str) -> str:
    if not re.search(r"(?m)^mode:\s*proxy-sidecar\s*$", config):
        raise ConfigError("existing sidecar config is not mode: proxy-sidecar")
    for required in ("forward_proxy_addr", "reverse_proxy_addr", "reverse_proxy_backend"):
        if not re.search(rf"(?m)^\s*{required}:\s*\S+", config):
            raise ConfigError(f"existing proxy config is missing {required}")
    lines = config.splitlines(keepends=True)
    lines = _reconcile_direction(lines, "inbound", self_id, otel_endpoint)
    lines = _reconcile_direction(lines, "outbound", self_id, otel_endpoint)
    return "".join(lines)


def render(args: argparse.Namespace) -> int:
    document = json.load(sys.stdin)
    data = document.get("data") or {}
    config = data.get("config.yaml")
    if not isinstance(config, str):
        raise ConfigError("ConfigMap has no data.config.yaml string")
    data["config.yaml"] = reconcile_config(config, args.self_id, args.otel_endpoint)
    document["data"] = data
    json.dump(document, sys.stdout)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    render_parser = subparsers.add_parser("render")
    render_parser.add_argument("--self-id", required=True)
    render_parser.add_argument("--otel-endpoint", required=True)
    render_parser.set_defaults(func=render)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return args.func(args)
    except (ConfigError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
