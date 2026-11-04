#!/usr/bin/env python3
"""Put the Sentry gate into a namespace's AuthBridge runtime ConfigMap, and check
that a sidecar runs it.

``dg.sh namespace <ns> instrument --enforce|--observe`` first renders the lineage
pipeline into ``authbridge-runtime-config`` (``lineage-attach/reconcile-existing-
proxy.py render``, the source every pod admission rebuilds the sidecar's config
from), then pipes the result through ``apply`` here, which:

- sets both lineage entries to ``capture_io: true`` and ``export_delay_ms: 10``: the
  gate can judge only what lineage captured, and the verdict service only answers
  once the request span has left the sidecar;
- removes any ``sentry-gate`` entry and writes one after ``lineage-telemetry`` on the
  outbound chain (the gate requires lineage before it; the reverse order makes the
  sidecar refuse the config), with the verdict URL, the shared secret, the pass
  list and, when the spec names them, the wait (``wait_ms``), the fallback (``deny`` or ``allow``), the
  ``fail_mode`` rows and the ``sources`` slices rendered from the namespace's fail-mode
  document (``fail_mode.py``) and that document's ``fail_mode_revision``, and under
  ``--observe`` the framework's ``on_error: observe``. Every other gate key keeps the gate's default (``wait_ms`` absent
  is the gate's 2000, ``fallback`` absent its ``deny``).

``pipeline-valid`` reads a sidecar's ``/v1/pipeline`` and says whether it runs
exactly that: the plugin names of the ConfigMap, lineage before the gate, the
lineage values above, the gate's URL, pass list, wait, fallback, ``fail_mode`` rows (as a set
by destination), ``sources`` slices (by source, each slice's rows as a set by destination),
``fail_mode_revision`` and mode, and a secret present (the endpoint masks its value).

``read`` gives the gate entry of a runtime ConfigMap: its mode, pass list, wait, fallback, the
number of its ``fail_mode`` rows, the number of its ``sources`` slices and its
``fail_mode_revision``.

``pass-host-problem <glob>`` says why the gate would refuse a ``--pass-host`` glob.

Standard library only, as ``dg.sh`` is a standalone cluster-management script.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import re
import sys

from fail_mode import ROW_KEYS, SETTING_KEYS, fold_host

GATE_PLUGIN = "sentry-gate"
DEFAULT_WAIT_MS = 2000  # the gate's own default when its entry has no wait_ms
DEFAULT_FALLBACK = "deny"  # the gate's own default when its entry has no fallback
EXPORT_DELAY_MS = 10
MODES = ("enforce", "observe")


def load_reconcile(kit_dir: str):
    path = Path(kit_dir) / "reconcile-existing-proxy.py"
    spec = importlib.util.spec_from_file_location("reconcile_existing_proxy", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # its dataclass looks its module up there
    spec.loader.exec_module(module)
    return module


_PLAIN = re.compile(r"[A-Za-z0-9_/][A-Za-z0-9._/-]*(:[A-Za-z0-9._/-]+)*")
_YAML_TYPED = re.compile(r"(?i)(true|false|yes|no|on|off|y|n|null|~|[-+]?0[xob][0-9a-f_]+|[-+]?\.(inf|nan)|[-+]?[0-9][0-9_.:eE+-]*|[0-9]{4}-[0-9]{1,2}-[0-9]{1,2}([Tt ].*)?)")


def scalar(value) -> str:
    """A YAML scalar that loads back as ``value``: ints and bools bare, a string plain
    when YAML would read it as that string, else single-quoted."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    text = str(value)
    if _PLAIN.fullmatch(text) and not _YAML_TYPED.fullmatch(text):
        return text
    return "'" + text.replace("'", "''") + "'"


def row_lines(pad: str, rows: list[dict]) -> list[str]:
    """``fail_mode:`` and its rows at ``pad``, each row with only the keys it has."""
    lines = [f"{pad}fail_mode:\n"]
    for row in rows:
        fields = [field for field in ROW_KEYS if field in row]
        lines += [f"{pad}{'- ' if n == 0 else '  '}{field}: {scalar(row[field])}\n" for n, field in enumerate(fields)]
    return lines


def gate_block(indent: int, spec: dict, mode: str) -> list[str]:
    pad, child, key, item = (" " * (indent + n) for n in (0, 2, 4, 6))
    lines = [f"{pad}- name: {GATE_PLUGIN}\n"]
    if mode == "observe":
        lines.append(f"{child}on_error: observe\n")
    lines += [
        f"{child}config:\n",
        f"{key}verdict_url: {scalar(spec['verdict_url'])}\n",
        f"{key}verdict_secret: {scalar(spec['verdict_secret'])}\n",
    ]
    if spec.get("wait_ms"):
        lines.append(f"{key}wait_ms: {scalar(spec['wait_ms'])}\n")
    if spec.get("fallback"):
        lines.append(f"{key}fallback: {scalar(spec['fallback'])}\n")
    if spec.get("fail_mode"):
        lines += row_lines(key, spec["fail_mode"])
    if spec.get("sources"):
        lines.append(f"{key}sources:\n")
        for source, slice_ in spec["sources"].items():
            lines.append(f"{key}  {scalar(source)}:\n")
            lines += [f"{key}    {field}: {scalar(slice_[field])}\n" for field in SETTING_KEYS if field in slice_]
            if slice_.get("fail_mode"):
                lines += row_lines(f"{key}    ", slice_["fail_mode"])
    if spec.get("fail_mode_revision"):
        lines.append(f"{key}fail_mode_revision: {scalar(spec['fail_mode_revision'])}\n")
    if spec.get("pass_hosts"):
        lines.append(f"{key}pass_hosts:\n")
        lines += [f"{key}- {scalar(host)}\n" for host in spec["pass_hosts"]]
    return lines


def gated_lineage_lines(lines: list[str], reconcile, otel_endpoint: str, direction: str) -> list[str]:
    """Replace the lineage block reconcile wrote at the end of ``direction`` with the
    gated one (capture_io true, export_delay_ms)."""
    section = reconcile._plugin_blocks(lines, direction)
    written = reconcile._managed_blocks(section.item_indent, otel_endpoint)
    start = section.section_end - len(written)
    if lines[start:section.section_end] != written:
        raise ValueError(f"pipeline.{direction}: the lineage entries are not as the reconciler wrote them")
    gated = [line.replace("capture_io: false", "capture_io: true") for line in written]
    config_pad = " " * (section.item_indent + 4)
    gated.append(f"{config_pad}export_delay_ms: {EXPORT_DELAY_MS}\n")
    return lines[:start] + gated + lines[section.section_end:]


def apply_gate(config: str, reconcile, otel_endpoint: str, spec: dict, mode: str) -> str:
    lines = config.splitlines(keepends=True)
    for direction in ("inbound", "outbound"):
        lines = gated_lineage_lines(lines, reconcile, otel_endpoint, direction)
    # Any gate entry, wherever it sits, goes; the canonical one follows lineage.
    for direction in ("inbound", "outbound"):
        section = reconcile._plugin_blocks(lines, direction)
        for start, end in reversed(section.ranges):
            if reconcile._plugin_name(lines[start:end]) == GATE_PLUGIN:
                del lines[start:end]
    section = reconcile._plugin_blocks(lines, "outbound")
    end = section.section_end
    return "".join(lines[:end] + gate_block(section.item_indent, spec, mode) + lines[end:])


def apply_command(args: argparse.Namespace) -> int:
    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    reconcile = load_reconcile(args.kit_dir)
    document = json.load(sys.stdin)
    data = document.get("data") or {}
    config = data.get("config.yaml")
    if not isinstance(config, str):
        raise ValueError("ConfigMap has no data.config.yaml string")
    data["config.yaml"] = apply_gate(config, reconcile, args.otel_endpoint, spec, args.mode)
    document["data"] = data
    json.dump(document, sys.stdout)
    return 0


def rows_by_destination(rows) -> dict:
    """fail_mode rows keyed by destination, folded as the gate matches it (``fold_host``):
    the comparison ignores their order, the destination's case and a trailing dot."""
    return {fold_host(str(row.get("destination"))): dict(row, destination=fold_host(str(row.get("destination"))))
            for row in rows or []}


def slices_by_source(sources) -> dict:
    """sources slices keyed by source, each with its defaults and its rows keyed by
    destination as ``rows_by_destination`` does."""
    return {str(source): {**{key: slice_[key] for key in SETTING_KEYS if key in slice_},
                          "fail_mode": rows_by_destination(slice_.get("fail_mode"))}
            for source, slice_ in (sources or {}).items()}


def pipeline_problems(document: dict, expected: dict, otel_endpoint: str, namespace_file: str,
                      managed: tuple[str, ...], spec: dict, mode: str) -> list[str]:
    """Every way a sidecar's /v1/pipeline differs from the gated pipeline."""
    problems = []
    for direction in ("inbound", "outbound"):
        plugins = document.get(direction) or []
        names = [p.get("name") for p in plugins]
        if names != expected.get(direction):
            problems.append(f"{direction}: plugins {names}, want {expected.get(direction)}")
            continue
        lineage = next(p for p in plugins if p.get("name") == managed[-1])
        want = {"otel_endpoint": otel_endpoint, "capture_io": True, "export_delay_ms": EXPORT_DELAY_MS,
                "namespace_file": namespace_file}
        got = lineage.get("config") or {}
        for key, value in want.items():
            if got.get(key) != value:
                problems.append(f"{direction}: lineage {key}={got.get(key)!r}, want {value!r}")
    outbound = document.get("outbound") or []
    names = [p.get("name") for p in outbound]
    if GATE_PLUGIN not in names or managed[-1] not in names or names.index(GATE_PLUGIN) < names.index(managed[-1]):
        problems.append("outbound: the gate is not after lineage-telemetry")
        return problems
    gate = outbound[names.index(GATE_PLUGIN)]
    config = gate.get("config") or {}
    if config.get("verdict_url") != spec.get("verdict_url"):
        problems.append("gate verdict_url differs")
    if (config.get("pass_hosts") or []) != (spec.get("pass_hosts") or []):
        problems.append("gate pass_hosts differ")
    if (config.get("wait_ms") or DEFAULT_WAIT_MS) != (spec.get("wait_ms") or DEFAULT_WAIT_MS):
        problems.append("gate wait_ms differs")
    if (config.get("fallback") or DEFAULT_FALLBACK) != (spec.get("fallback") or DEFAULT_FALLBACK):
        problems.append("gate fallback differs")
    if rows_by_destination(config.get("fail_mode")) != rows_by_destination(spec.get("fail_mode")):
        problems.append("gate fail_mode rows differ")
    live_sources, want_sources = config.get("sources") or {}, spec.get("sources") or {}
    # /v1/pipeline masks a map key named like a secret's (a source ending in key, token,
    # secret, password...): such a slice is compared by its presence only.
    masked = {source for source, slice_ in live_sources.items() if not isinstance(slice_, dict)}
    if not masked <= set(want_sources) or slices_by_source(
            {s: v for s, v in live_sources.items() if s not in masked}) != slices_by_source(
            {s: v for s, v in want_sources.items() if s not in masked}):
        problems.append("gate sources slices differ")
    if (config.get("fail_mode_revision") or "") != (spec.get("fail_mode_revision") or ""):
        problems.append("gate fail_mode_revision differs")
    if not config.get("verdict_secret"):
        problems.append("gate has no verdict secret")
    if (gate.get("onError") == "observe") != (mode == "observe"):
        problems.append(f"gate mode is {gate.get('onError') or 'enforce'}, want {mode}")
    return problems


def pipeline_valid_command(args: argparse.Namespace) -> int:
    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    reconcile = load_reconcile(args.kit_dir)
    problems = pipeline_problems(json.load(sys.stdin), json.loads(args.expected), args.otel_endpoint,
                                 reconcile.NAMESPACE_FILE, reconcile.MANAGED_PLUGINS, spec, args.mode)
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


def read_command(args: argparse.Namespace) -> int:
    """The gate entry of a runtime ConfigMap as {"mode", "pass_hosts", "wait_ms", "fallback",
    "fail_mode_rows", "sources", "fail_mode_revision"}, {} when none; wait_ms, fallback and
    fail_mode_revision are None when the entry has no such key; sources counts the slices. Each key is read at the indentation of the entry's
    config keys, so a row's or a slice's wait or fallback is not the gate's."""
    reconcile = load_reconcile(args.kit_dir)
    config = (json.load(sys.stdin).get("data") or {}).get("config.yaml") or ""
    lines = config.splitlines(keepends=True)
    section = reconcile._plugin_blocks(lines, "outbound")
    for start, end in section.ranges:
        block = lines[start:end]
        if reconcile._plugin_name(block) != GATE_PLUGIN:
            continue
        text = "".join(block)
        entry = re.search(r"^( *)config:\s*$", text, re.M)
        k = re.escape(entry.group(1) + "  ") if entry else "(?!)"
        hosts = re.search(rf"^{k}pass_hosts:\n((?:{k}- .*\n)*)", text, re.M)
        wait_ms = re.search(rf"^{k}wait_ms:\s*(\d+)\s*$", text, re.M)
        fallback = re.search(rf"^{k}fallback:\s*(\S+)\s*$", text, re.M)
        rows = re.search(rf"^{k}fail_mode:\n((?:{k}(?:- |  ).*\n)*)", text, re.M)
        sources = re.search(rf"^{k}sources:\n((?:{k}  .*\n)*)", text, re.M)
        revision = re.search(rf"^{k}fail_mode_revision:\s*(\S+)\s*$", text, re.M)
        json.dump({"mode": "observe" if re.search(r"on_error:\s*observe", text) else "enforce",
                   "pass_hosts": [h.split("- ", 1)[1].strip().strip("'") for h in hosts.group(1).splitlines()] if hosts else [],
                   "wait_ms": int(wait_ms.group(1)) if wait_ms else None,
                   "fallback": fallback.group(1).strip("'\"") if fallback else None,
                   "fail_mode_rows": len(re.findall(rf"^{k}- ", rows.group(1), re.M)) if rows else 0,
                   "sources": len(re.findall(rf"^{k}  \S", sources.group(1), re.M)) if sources else 0,
                   "fail_mode_revision": revision.group(1).strip("'\"") if revision else None},
                  sys.stdout)
        return 0
    json.dump({}, sys.stdout)
    return 0


def pass_host_problem(glob: str) -> str:
    """Why the gate refuses ``glob`` as a pass_hosts entry, or "" when it takes it. The gate
    trims and case-folds an entry, then refuses it when it is empty or ``path.Match`` calls
    it malformed: a trailing ``\\``, or a ``[`` class that is unclosed, empty, or has a ``-``
    or ``]`` where a character belongs. A refused entry makes the sidecar refuse its whole
    config, so instrument checks the globs before writing anything."""
    p = glob.strip().lower()
    if not p:
        return "an empty glob"
    if "$" in p:
        return "a $, which the sidecar's config loader expands"
    n = len(p)

    def char_end(i: int) -> int:
        # path.Match's getEsc: one class character (or \\-escaped one), with the class
        # still open after it; -1 when malformed.
        if i >= n or p[i] in "-]":
            return -1
        if p[i] == "\\":
            i += 1
        return i + 1 if i + 1 < n else -1

    i = 0
    while i < n:
        if p[i] == "\\":
            if i + 1 >= n:
                return "a trailing \\"
            i += 2
        elif p[i] == "[":
            i += 1
            if i < n and p[i] == "^":
                i += 1
            ranges = 0
            while not (i < n and p[i] == "]" and ranges):
                i = char_end(i)
                if i >= 0 and p[i] == "-":
                    i = char_end(i + 1)
                if i < 0:
                    return "a [ class that is not closed, is empty, or has a - or ] where a character belongs"
                ranges += 1
            i += 1
        else:
            i += 1
    return ""


def pass_host_problem_command(args: argparse.Namespace) -> int:
    problem = pass_host_problem(args.glob)
    if problem:
        print(problem)
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name, func in (("apply", apply_command), ("pipeline-valid", pipeline_valid_command)):
        p = sub.add_parser(name)
        p.add_argument("--kit-dir", required=True)
        p.add_argument("--spec", required=True, help="JSON: verdict_url, verdict_secret, pass_hosts, wait_ms, fallback, fail_mode, sources and fail_mode_revision (optional)")
        p.add_argument("--mode", choices=MODES, required=True)
        p.add_argument("--otel-endpoint", required=True)
        if name == "pipeline-valid":
            p.add_argument("--expected", required=True, help="the ConfigMap's plugin names (reconcile pipeline-contract)")
        p.set_defaults(func=func)
    read = sub.add_parser("read")
    read.add_argument("--kit-dir", required=True)
    read.set_defaults(func=read_command)
    check = sub.add_parser("pass-host-problem", help="why the gate would refuse a pass_hosts glob; exit 1 when it would")
    check.add_argument("glob")
    check.set_defaults(func=pass_host_problem_command)
    args = parser.parse_args()
    try:
        return args.func(args)
    except (ValueError, KeyError, json.JSONDecodeError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
