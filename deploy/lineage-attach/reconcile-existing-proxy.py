#!/usr/bin/env python3
"""Reconcile lineage into a namespace's AuthBridge runtime ConfigMap.

Rossoctl admission rebuilds every proxy's ``authbridge-config-<name>`` from the
namespace ``authbridge-runtime-config`` on each pod admission, so lineage lives
in the namespace ConfigMap. The platform owns it and all authentication
plugins. This tool canonicalizes the managed parser/lineage entries in both
``pipeline.*.plugins`` sequences, while preserving every unmanaged plugin and
every unrelated ConfigMap field byte-for-byte where practical. The lineage
entry names no ``self_id``: each sidecar reads its own admitted client id.
It intentionally uses only the Python standard library so ``dg.sh`` remains a
standalone cluster-management script.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import re
import sys


PARSER_PLUGINS = (
    "a2a-parser",
    "mcp-parser",
    "inference-parser",
)
LINEAGE_PLUGIN = "lineage-telemetry"
MANAGED_PLUGINS = (*PARSER_PLUGINS, LINEAGE_PLUGIN)
NAMESPACE_FILE = "/var/run/secrets/kubernetes.io/serviceaccount/namespace"


class ConfigError(ValueError):
    """The operator ConfigMap is outside the safe reconciliation envelope."""


@dataclass(frozen=True)
class _PluginBlocks:
    """Locations and indentation of one pipeline direction's plugin blocks."""

    first_item: int
    section_end: int
    item_indent: int
    ranges: tuple[tuple[int, int], ...]


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _key(line: str, name: str) -> bool:
    return bool(re.match(rf"^\s*{re.escape(name)}:\s*(?:#.*)?$", line.rstrip("\n")))


def _section_end(
    lines: list[str],
    start: int,
    end: int,
    indent: int,
    *,
    allow_indentless_items: bool = False,
) -> int:
    """Find the next sibling, allowing YAML's indentationless list form."""
    for cursor in range(start, end):
        candidate = lines[cursor]
        indentless_item = bool(
            allow_indentless_items
            and _indent(candidate) == indent
            and candidate.lstrip().startswith("- ")
        )
        if (
            candidate.strip()
            and not candidate.lstrip().startswith("#")
            and _indent(candidate) <= indent
            and not indentless_item
        ):
            return cursor
    return end


def _section(lines: list[str], name: str, start: int, end: int, parent_indent: int) -> tuple[int, int]:
    for index in range(start, end):
        line = lines[index]
        if _indent(line) > parent_indent and _key(line, name):
            indent = _indent(line)
            return index, _section_end(
                lines, index + 1, end, indent, allow_indentless_items=True
            )
    raise ConfigError(f"missing {name}: section")


def _plugin_name(block: list[str]) -> str | None:
    for line in block:
        match = re.match(r"^\s*(?:-\s*)?name:\s*['\"]?([^'\"\s#]+)", line)
        if match:
            return match.group(1)
    return None


def _managed_blocks(indent: int, otel_endpoint: str) -> list[str]:
    # Plain scalars, as PyYAML dumps them: another writer's safe_load and
    # safe_dump(sort_keys=False) round trip then leaves the canonical bytes
    # unchanged.
    pad = " " * indent
    child = " " * (indent + 2)
    config = " " * (indent + 4)
    return [
        *(f"{pad}- name: {name}\n" for name in PARSER_PLUGINS),
        f"{pad}- name: {LINEAGE_PLUGIN}\n",
        f"{child}config:\n",
        f"{config}otel_endpoint: {otel_endpoint}\n",
        f"{config}capture_io: false\n",
        f"{config}namespace_file: {NAMESPACE_FILE}\n",
    ]


def _plugin_blocks(lines: list[str], direction: str) -> _PluginBlocks:
    pipeline_index = next((i for i, line in enumerate(lines) if _key(line, "pipeline")), None)
    if pipeline_index is None:
        raise ConfigError("missing pipeline: section")
    pipeline_indent = _indent(lines[pipeline_index])
    direction_index, direction_end = _section(
        lines, direction, pipeline_index + 1, len(lines), pipeline_indent
    )
    plugins_index, plugins_end = _section(
        lines, "plugins", direction_index + 1, direction_end, _indent(lines[direction_index])
    )
    first_item = next(
        (
            cursor
            for cursor in range(plugins_index + 1, plugins_end)
            if re.match(r"^\s*-\s+", lines[cursor])
        ),
        None,
    )
    if first_item is None:
        return _PluginBlocks(
            first_item=plugins_index + 1,
            section_end=plugins_end,
            item_indent=_indent(lines[plugins_index]) + 2,
            ranges=(),
        )
    item_indent = _indent(lines[first_item])
    item_starts = [
        cursor
        for cursor in range(first_item, plugins_end)
        if re.match(rf"^ {{{item_indent}}}-\s+", lines[cursor])
    ]
    ranges = tuple(
        (
            start,
            item_starts[position + 1]
            if position + 1 < len(item_starts)
            else plugins_end,
        )
        for position, start in enumerate(item_starts)
    )
    return _PluginBlocks(first_item, plugins_end, item_indent, ranges)


def _plugin_names(lines: list[str], direction: str) -> list[str]:
    section = _plugin_blocks(lines, direction)
    names: list[str] = []
    for start, end in section.ranges:
        name = _plugin_name(lines[start:end])
        if name is None:
            raise ConfigError(f"unnamed plugin in pipeline.{direction}")
        names.append(name)
    return names


def _reconcile_direction(lines: list[str], direction: str, otel_endpoint: str) -> list[str]:
    section = _plugin_blocks(lines, direction)
    preserved: list[str] = []
    for block_start, block_end in section.ranges:
        block = lines[block_start:block_end]
        name = _plugin_name(block)
        # The same refusal as pipeline-contract, so nothing is written that the
        # verification after the rollout cannot read (a flow-style item, say).
        if name is None:
            raise ConfigError(f"unnamed plugin in pipeline.{direction}")
        if name not in MANAGED_PLUGINS:
            preserved.extend(block)

    replacement = lines[: section.first_item] + preserved
    replacement.extend(_managed_blocks(section.item_indent, otel_endpoint))
    replacement.extend(lines[section.section_end :])
    return replacement


def reconcile_config(config: str, otel_endpoint: str) -> str:
    # Every sidecar in the namespace renders this text: refuse a value that is
    # not a plain YAML scalar rather than risk a document admission cannot parse.
    if not re.fullmatch(r"[A-Za-z0-9._:/-]+", otel_endpoint):
        raise ConfigError(f"otel endpoint {otel_endpoint!r} is not a plain host:port or URL")
    lines = config.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    lines = _reconcile_direction(lines, "inbound", otel_endpoint)
    lines = _reconcile_direction(lines, "outbound", otel_endpoint)
    return "".join(lines)


def render(args: argparse.Namespace) -> int:
    document = json.load(sys.stdin)
    data = document.get("data") or {}
    config = data.get("config.yaml")
    if not isinstance(config, str):
        raise ConfigError("ConfigMap has no data.config.yaml string")
    data["config.yaml"] = reconcile_config(config, args.otel_endpoint)
    document["data"] = data
    json.dump(document, sys.stdout)
    return 0


def catalog_valid(_args: argparse.Namespace) -> int:
    document = json.load(sys.stdin)
    names = {plugin.get("name") for plugin in document.get("plugins") or []}
    return 0 if set(MANAGED_PLUGINS) <= names else 1


def pipeline_valid(args: argparse.Namespace) -> int:
    document = json.load(sys.stdin)
    expected = json.loads(args.expected)
    for direction in ("inbound", "outbound"):
        plugins = document.get(direction) or []
        names = [plugin.get("name") for plugin in plugins]
        if names != expected.get(direction):
            return 1
        try:
            positions = [names.index(name) for name in MANAGED_PLUGINS]
        except ValueError:
            return 1
        if positions != sorted(positions):
            return 1
        lineage = plugins[positions[-1]]
        config = lineage.get("config") or {}
        expected_config = {
            "otel_endpoint": args.otel_endpoint,
            "capture_io": False,
            "namespace_file": NAMESPACE_FILE,
        }
        if any(config.get(key) != value for key, value in expected_config.items()):
            return 1
    return 0


def pipeline_contract(_args: argparse.Namespace) -> int:
    document = json.load(sys.stdin)
    config = (document.get("data") or {}).get("config.yaml")
    if not isinstance(config, str):
        raise ConfigError("ConfigMap has no data.config.yaml string")
    lines = config.splitlines(keepends=True)
    contract = {
        direction: _plugin_names(lines, direction)
        for direction in ("inbound", "outbound")
    }
    json.dump(contract, sys.stdout)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    render_parser = subparsers.add_parser("render")
    render_parser.add_argument("--otel-endpoint", required=True)
    render_parser.set_defaults(func=render)
    catalog_parser = subparsers.add_parser("catalog-valid")
    catalog_parser.set_defaults(func=catalog_valid)
    pipeline_parser = subparsers.add_parser("pipeline-valid")
    pipeline_parser.add_argument("--otel-endpoint", required=True)
    pipeline_parser.add_argument("--expected", required=True)
    pipeline_parser.set_defaults(func=pipeline_valid)
    contract_parser = subparsers.add_parser("pipeline-contract")
    contract_parser.set_defaults(func=pipeline_contract)
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
