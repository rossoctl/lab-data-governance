#!/usr/bin/env python3
"""Validate an app namespace's fail-mode document and render it into the Sentry gate's rows and slices.

The document is the ``table`` key of ConfigMap ``dg-fail-mode`` in the app namespace, a
human's document kept verbatim (``dg.sh namespace <ns> fail-mode set <file>``). It is JSON::

    {"rows": [{"destination": "peer.example", "fallback": "allow"},
              {"source": "desk", "wait_ms": 3000},
              {"source": "desk", "destination": "peer.example", "fallback": "deny"}]}

A row overrides the gate's wait (``wait_ms``, 1..3000, how long it holds a request for a
verdict) or its fallback (``deny`` or ``allow``, what the request gets when the wait ends
with no verdict), or both, for the requests it names: a destination row for every request
to one destination host, a source row for every request from one workload (the name
lineage reports as ``self_id``), a pair row (both) for the requests from that workload to
that host. Per key, the most specific row wins: pair > destination > source > the gate's
own value. The rules:

- the document is an object with one key, ``rows``, a list of objects;
- a row has only ``source``, ``destination``, ``wait_ms`` and ``fallback``, names a source,
  a destination or both, and gives ``wait_ms`` or ``fallback`` or both;
- the destination is a host: not empty, no port, no scheme, no ``/``, no whitespace, no
  wildcard (``*``, ``?``, ``[``), since the gate refuses a row that could never match a
  request's host, and no ``$``, which the sidecar's config loader expands;
- the source is a workload name: not empty, no whitespace, no ``/``, no ``$``;
- each (source, destination) appears once, the destination compared folded, as the gate
  matches it: the request host, port stripped and folded, equals the row's destination,
  folded (``fold_host``: case folded, one trailing dot dropped). Nothing else matches (no
  glob, no suffix). The source is compared exactly, as the gate looks it up.

``dg.sh namespace <ns> instrument --enforce|--observe`` renders the document into the
gate's entry (``gate_config.py``): the destination rows as ``fail_mode``, and per source a
slice under ``sources`` whose defaults are the source row's keys and whose rows are, for
each destination with a destination row or a pair row of that source, the pair's keys over
the destination row's keys. The gate fills a slice row's missing key from the slice's
defaults, and those from its own top-level keys. It also writes the document's revision as
``fail_mode_revision``, which the gate records with every request that got no verdict, so a
record names the document that decided it.

Subcommands:

- ``validate <file>``: one line per problem, each naming the rule it breaks; exit 1 on any.
- ``render``: the document on stdin; ``{"fail_mode": [...], "sources": {...}}`` as JSON on
  stdout (``sources`` left out when no row names a source); exit 1 with the problems when
  it is not valid.
- ``revision <file>``: the document's revision, the sha256 of its bytes as ``set`` keeps them
  (verbatim: no newline or whitespace is normalised), in lowercase hex; ``-`` reads stdin.

Standard library only, as ``dg.sh`` is a standalone cluster-management script.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

CONFIGMAP = "dg-fail-mode"
TABLE_KEY = "table"
# The keys of a gate row, in the order they are rendered into the gate.
ROW_KEYS = ("destination", "wait_ms", "fallback")
# What a row sets; a slice's defaults are these keys too.
SETTING_KEYS = ("wait_ms", "fallback")
# The keys of a document row.
DOCUMENT_KEYS = ("source", *ROW_KEYS)
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
    out = [f"{where}: unknown key {key!r} (a row has {', '.join(DOCUMENT_KEYS)})"
           for key in row if key not in DOCUMENT_KEYS]
    if "source" not in row and "destination" not in row:
        out.append(f"{where}: a row names a source, a destination or both")
    if "source" in row:
        source = row["source"]
        if not isinstance(source, str) or not source:
            out.append(f"{where}: an empty source ({json.dumps(source)})")
        elif any(c.isspace() for c in source):
            out.append(f"{where}: a source has no whitespace ({json.dumps(source)})")
        elif "/" in source:
            out.append(f"{where}: a source has no / ({json.dumps(source)})")
        elif "$" in source:
            out.append(f"{where}: a source has no $, which the sidecar's config loader expands ({json.dumps(source)})")
    if "destination" in row:
        destination = row["destination"]
        if not isinstance(destination, str) or not fold_host(destination).strip():
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
    seen: dict[tuple, int] = {}
    for index, row in enumerate(rows):
        where = f"rows[{index}]"
        found = row_problems(where, row)
        out += found
        if found:
            continue
        key = edge(row)
        if key in seen:
            kind = "pair" if None not in key else "source" if key[1] is None else "destination"
            names = ", ".join(json.dumps(row[k]) for k in ("source", "destination") if k in row)
            out.append(f"{where}: a duplicate {kind} ({names}, as rows[{seen[key]}])")
        else:
            seen[key] = index
    return out


def edge(row: dict) -> tuple:
    """A valid row's (source, destination) as the gate matches them: the destination
    folded (``fold_host``), None for what the row does not name."""
    destination = row.get("destination")
    return row.get("source"), fold_host(destination) if destination is not None else None


def gate_row(row: dict) -> dict:
    return {key: row[key] for key in ROW_KEYS if key in row}


def render(text: str) -> dict:
    """The gate's keys of a valid document, in document order: ``fail_mode``, the
    destination rows; ``sources``, one slice per source a source or pair row names, its
    defaults the source row's keys, its rows for each destination with a destination row
    or a pair row of that source, the pair's keys over the destination row's keys. Only
    the keys given; the gate fills the rest."""
    rows = json.loads(text)["rows"]
    by_edge = {edge(row): row for row in rows}
    out = {"fail_mode": [gate_row(row) for row in rows if "source" not in row]}
    sources: dict[str, dict] = {}
    for source in dict.fromkeys(row["source"] for row in rows if "source" in row):
        own = by_edge.get((source, None), {})
        slice_ = {key: own[key] for key in SETTING_KEYS if key in own}
        destinations = dict.fromkeys(d for of, d in map(edge, rows) if d is not None and of in (None, source))
        slice_rows = [gate_row({**by_edge.get((None, d), {}), **by_edge.get((source, d), {})}) for d in destinations]
        if slice_rows:
            slice_["fail_mode"] = slice_rows
        sources[source] = slice_
    if sources:
        out["sources"] = sources
    return out


def revision(data: bytes) -> str:
    """The revision of a document: the sha256 of its bytes, lowercase hex."""
    return hashlib.sha256(data).hexdigest()


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
    rendered = render(text)
    print(f"{args.file}: a valid fail-mode document, {len(json.loads(text)['rows'])} rows, "
          f"{len(rendered.get('sources', {}))} sources")
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


def revision_command(args: argparse.Namespace) -> int:
    try:
        data = sys.stdin.buffer.read() if args.file == "-" else Path(args.file).read_bytes()
    except OSError as error:
        print(f"{args.file}: cannot be read ({error})", file=sys.stderr)
        return 1
    print(revision(data))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("file")
    validate.set_defaults(func=validate_command)
    sub.add_parser("render").set_defaults(func=render_command)
    revision_ = sub.add_parser("revision")
    revision_.add_argument("file")
    revision_.set_defaults(func=revision_command)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
