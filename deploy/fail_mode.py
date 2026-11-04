#!/usr/bin/env python3
"""Validate an app namespace's fail-mode document and render it into the Sentry gate's rows.

The document is the ``table`` key of ConfigMap ``dg-fail-mode`` in the app namespace, a
human's document kept verbatim (``dg.sh namespace <ns> fail-mode set <file>``). It is JSON::

    {"rows": [{"destination": "peer.example", "fallback": "allow"},
              {"destination": "slow.example", "wait_ms": 3000}]}

A row overrides, for requests to one destination host, the gate's wait (``wait_ms``,
1..3000, how long it holds a request for a verdict) or its fallback (``deny`` or
``allow``, what the request gets when the wait ends with no verdict), or both. The rules:

- the document is an object with one key, ``rows``, a list of objects;
- a row has only ``destination``, ``wait_ms`` and ``fallback``, and gives ``wait_ms`` or
  ``fallback`` or both;
- the destination is a host: present, not empty, no port, no scheme, no ``/``, no
  whitespace, no wildcard (``*``, ``?``, ``[``), no ``$`` (the sidecar's config loader
  expands it), since the gate refuses a row that could
  never match a request's host;
- a destination appears once, compared folded, as the gate matches it: the request host,
  port stripped and folded, equals the row's destination, folded (``fold_host``: case
  folded, one trailing dot dropped). Nothing else matches (no glob, no suffix).

``dg.sh namespace <ns> instrument --enforce|--observe`` renders the rows into the gate's
entry as ``fail_mode`` (``gate_config.py``), each row with only the keys it has.

Subcommands:

- ``validate <file>``: one line per problem, each naming the rule it breaks; exit 1 on any.
- ``render``: the document on stdin; the gate's rows as JSON on stdout; exit 1 with the
  problems when it is not valid.

Standard library only, as ``dg.sh`` is a standalone cluster-management script.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

CONFIGMAP = "dg-fail-mode"
TABLE_KEY = "table"
# The keys of a row, in the order they are rendered into the gate.
ROW_KEYS = ("destination", "wait_ms", "fallback")
WAIT_MS_MIN, WAIT_MS_MAX = 1, 3000
FALLBACKS = ("deny", "allow")
WILDCARDS = "*?["


def fold_host(host: str) -> str:
    """A host as the gate compares a row's destination: case folded, and the trailing dot of
    a fully qualified name dropped, since both spellings dial the same host."""
    host = host.lower()
    return host[:-1] if host.endswith(".") else host


def row_problems(where: str, row) -> list[str]:
    if not isinstance(row, dict):
        return [f"{where}: a row is an object"]
    out = [f"{where}: unknown key {key!r} (a row has {', '.join(ROW_KEYS)})"
           for key in row if key not in ROW_KEYS]
    destination = row.get("destination")
    if "destination" not in row:
        out.append(f"{where}: a row names a destination")
    elif not isinstance(destination, str) or not fold_host(destination).strip():
        out.append(f"{where}: an empty destination ({json.dumps(destination)})")
    elif "://" in destination:
        out.append(f"{where}: a destination has no scheme ({json.dumps(destination)})")
    elif ":" in destination:
        out.append(f"{where}: a destination has no port ({json.dumps(destination)})")
    elif any(c.isspace() for c in destination):
        out.append(f"{where}: a destination has no whitespace ({json.dumps(destination)})")
    elif "/" in destination:
        out.append(f"{where}: a destination has no / ({json.dumps(destination)})")
    elif any(c in WILDCARDS for c in destination):
        out.append(f"{where}: a destination has no wildcard (*, ?, [) ({json.dumps(destination)})")
    elif "$" in destination:
        out.append(f"{where}: a destination has no $, which the sidecar's config loader expands ({json.dumps(destination)})")
    if "wait_ms" not in row and "fallback" not in row:
        out.append(f"{where}: a row gives wait_ms or fallback")
    wait = row.get("wait_ms")
    if "wait_ms" in row and (isinstance(wait, bool) or not isinstance(wait, int)
                             or not WAIT_MS_MIN <= wait <= WAIT_MS_MAX):
        out.append(f"{where}: wait_ms is an integer {WAIT_MS_MIN}..{WAIT_MS_MAX} ({json.dumps(wait)})")
    if "fallback" in row and row["fallback"] not in FALLBACKS:
        out.append(f"{where}: fallback is deny or allow ({json.dumps(row['fallback'])})")
    return out


def problems(text: str) -> list[str]:
    """Every rule ``text`` breaks; [] when it is a valid document."""
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as error:
        return [f"document: not JSON ({error})"]
    if not isinstance(doc, dict) or set(doc) != {"rows"}:
        return ['document: the document is {"rows": [...]}, one key']
    rows = doc["rows"]
    if not isinstance(rows, list):
        return ["rows: rows is a list"]
    out: list[str] = []
    seen: dict[str, int] = {}
    for index, row in enumerate(rows):
        where = f"rows[{index}]"
        out += row_problems(where, row)
        destination = row.get("destination") if isinstance(row, dict) else None
        if isinstance(destination, str) and fold_host(destination).strip():
            folded = fold_host(destination)
            if folded in seen:
                out.append(f"{where}: a duplicate destination ({json.dumps(destination)}, as rows[{seen[folded]}])")
            else:
                seen[folded] = index
    return out


def render(text: str) -> list[dict]:
    """The gate's rows of a valid document: each row's keys in ROW_KEYS order, document order."""
    return [{key: row[key] for key in ROW_KEYS if key in row} for row in json.loads(text)["rows"]]


def validate_command(args: argparse.Namespace) -> int:
    try:
        text = Path(args.file).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        print(f"{args.file}: cannot be read ({error})", file=sys.stderr)
        return 1
    found = problems(text)
    for problem in found:
        print(f"{args.file}: {problem}", file=sys.stderr)
    if found:
        return 1
    print(f"{args.file}: a valid fail-mode document, {len(render(text))} rows")
    return 0


def render_command(_args: argparse.Namespace) -> int:
    text = sys.stdin.read()
    found = problems(text)
    for problem in found:
        print(f"{CONFIGMAP}: {problem}", file=sys.stderr)
    if found:
        return 1
    json.dump(render(text), sys.stdout)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("file")
    validate.set_defaults(func=validate_command)
    sub.add_parser("render").set_defaults(func=render_command)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
