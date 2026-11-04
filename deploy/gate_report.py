#!/usr/bin/env python3
"""What a namespace's Sentry gates did with the requests that got no verdict:
``dg.sh namespace <ns> gate-report [--since <time>]``.

The gate records every request it holds as an invocation on the sidecar's session events
(``:9094/v1/sessions``). A request with no verdict is recorded as one of: ``fallback_allow``
(delivered on the fallback), ``fallback_deny`` (refused on the fallback), a fault, refused
whatever the fallback, under its cause (``verdict_unauthorized``, ``verdict_error``,
``verdict_empty``, ``no_lineage_stamp``, ``no_exchange_key``), or a body lineage marked
(``body_cut`` or ``body_not_captured`` in the details), refused under its cause. Each such
record carries ``fail_mode_revision``, the revision of the fail-mode document the gate ran
with, when it ran with one. Under the pipeline's ``on_error: observe`` (``instrument
--observe``) a refusal is not made and the framework marks its record ``shadow``.

This tool reads those records from every sidecar of the namespace (``kubectl exec ... wget``,
paging back with ``before=`` to the window's start) and prints one table, a row per source
workload and destination: the source is the ``self_id`` lineage wrote into its record of the
same request, the name a fail-mode row's ``source`` matches (the gate's record names none), else
the workload whose pod the record was read from; the destination the request's host as the session API gives it,
port stripped, case-folded and without a trailing dot, as the gate matches a row. The columns: ``refused``
(``fallback_deny`` and the faults), ``fallback_allow``, ``would_refuse`` (the shadow records:
what enforcing would have refused), ``body_cut`` and ``body_not_captured`` (the marked-body
refusals by mark). Under it the refusals by reason, and the revisions the records name. The
header line has the namespace, the window and the namespace's current revision. A refusal by a
verdict is the verdict service's to report, not this tool's.

A sidecar keeps its records in its process: a roll empties them, so a window is read before
the roll that ends it. It also keeps only its most recently active sessions (100 by default).
Standard library only, like ``dg.sh``.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
import re
import subprocess
import sys
from urllib.parse import quote

GATE = "sentry-gate"
LINEAGE = "lineage-telemetry"
SESSION_API = "http://127.0.0.1:9094"
PAGE = 2000  # the session API's largest page
FAULTS = ("verdict_unauthorized", "verdict_error", "verdict_empty", "no_lineage_stamp", "no_exchange_key")
MARKS = ("body_cut", "body_not_captured")
COLUMNS = ("refused", "fallback_allow", "would_refuse", *MARKS)
SHORT = 12  # a revision is shown by its first 12 characters, as dg.sh status shows it


def parse_time(text: str, now: datetime) -> datetime:
    """RFC 3339 (2026-10-29T10:00:00Z), or a span back from now (90m, 1h, 30s)."""
    match = re.fullmatch(r"([0-9]+)([smh])", text)
    if match:
        unit = {"s": "seconds", "m": "minutes", "h": "hours"}[match.group(2)]
        return now - timedelta(**{unit: int(match.group(1))})
    return parse_at(text)


def parse_at(text: str) -> datetime:
    """A session event's time (RFC 3339 with up to nanoseconds) as UTC."""
    match = re.fullmatch(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(\.\d+)?(Z|[+-]\d{2}:\d{2})", text)
    if not match:
        raise ValueError(f"not an RFC 3339 time: {text!r}")
    frac = ((match.group(2) or ".") + "000000")[:7]  # Python 3.9 reads exactly 6 digits
    zone = "+00:00" if match.group(3) == "Z" else match.group(3)
    return datetime.fromisoformat(match.group(1) + frac + zone).astimezone(timezone.utc)


def iso(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def host_only(host: str) -> str:
    """The host as the gate matches a row: port stripped, case-folded, one trailing dot
    dropped (``fail_mode.fold_host``)."""
    host = host or ""
    if host.startswith("["):
        return host[1:].split("]")[0].lower()
    host = (host.rsplit(":", 1)[0] if host.count(":") == 1 else host).lower()
    return host[:-1] if host.endswith(".") else host


def get(namespace: str, pod: str, path: str) -> dict:
    """One session API answer of a sidecar, through ``kubectl exec ... wget``."""
    out = subprocess.run(["kubectl", "-n", namespace, "exec", pod, "-c", "authbridge-proxy", "--",
                          "wget", "-qO-", SESSION_API + path], capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        raise RuntimeError(f"GET {path}: {out.stderr.strip()[:200] or 'exit %d' % out.returncode}")
    return json.loads(out.stdout)


def read_events(namespace: str, pod: str, since: datetime | None) -> list[dict]:
    """Every event of every session the sidecar holds, back to ``since`` (all when None)."""
    events: list[dict] = []
    for session in get(namespace, pod, "/v1/sessions").get("sessions") or []:
        sid = quote(str(session.get("id")), safe="")
        before = None
        while True:
            page = get(namespace, pod, f"/v1/sessions/{sid}?limit={PAGE}&view=summary"
                                        + (f"&before={before}" if before else ""))
            batch = page.get("events") or []
            if not batch:
                break
            events += batch
            first = batch[0]
            more = "oldestSeq" in page and first.get("seq", 0) > page["oldestSeq"]
            if not more or not first.get("seq") or (since is not None and parse_at(first["at"]) < since):
                break
            before = first["seq"]
    return events


def outcome(inv: dict) -> str | None:
    """The column a gate record of a request with no verdict counts in; None for any other."""
    details = inv.get("details") or {}
    reason, action = inv.get("reason") or "", inv.get("action")
    if reason == "fallback_allow":
        return "fallback_allow"
    mark = next((m for m in MARKS if details.get(m) == "true"), None)
    if action != "deny" or not (mark or reason == "fallback_deny" or reason in FAULTS):
        return None
    if inv.get("shadow"):
        return "would_refuse"
    return mark or "refused"


def no_verdict_records(source: str, events: list[dict], since: datetime | None) -> list[dict]:
    """The gate's records of requests with no verdict on the outbound events at or after ``since``."""
    records = []
    for event in events:
        if event.get("direction") != "outbound" or (since is not None and parse_at(event["at"]) < since):
            continue
        invocations = (event.get("invocations") or {}).get("outbound") or []
        # The slice the gate picked is keyed by lineage's self_id, in lineage's record of the pass.
        self_id = next(((inv.get("details") or {}).get("self_id") for inv in invocations if inv.get("plugin") == LINEAGE), None)
        for inv in invocations:
            column = inv.get("plugin") == GATE and outcome(inv)
            if not column:
                continue
            details = inv.get("details") or {}
            mark = next((m for m in MARKS if details.get(m) == "true"), "")
            records.append({"source": self_id or source, "destination": host_only(event.get("host") or ""), "column": column,
                            "reason": " ".join(filter(None, (inv.get("reason"), mark, "shadow" if inv.get("shadow") else ""))),
                            "revision": details.get("fail_mode_revision") or ""})
    return records


def table(rows: list[list[str]]) -> list[str]:
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    return ["  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip() for row in rows]


def report(namespace: str, gate: dict, records: list[dict], sidecars: list[str], since, until) -> str:
    current = (gate.get("fail_mode_revision") or "none")[:SHORT]
    out = [f"gate-report namespace={namespace} since={iso(since) if since else 'all held'} until={iso(until)} "
           f"mode={gate.get('mode', 'enforce')} revision={current}"]
    out += sidecars
    if not records:
        out.append("no request without a verdict in the window")
        return "\n".join(out) + "\n"
    cells = Counter((r["source"], r["destination"], r["column"]) for r in records)
    edges = sorted({(r["source"], r["destination"]) for r in records})
    out += table([["source", "destination", *COLUMNS]]
                 + [[s, d, *(str(cells[(s, d, c)]) for c in COLUMNS)] for s, d in edges])
    out.append("by reason:")
    reasons = Counter((r["source"], r["destination"], r["reason"]) for r in records if r["column"] != "fallback_allow")
    out += ["  " + line for line in table([[s, d, reason, str(n)] for (s, d, reason), n in sorted(reasons.items())])] \
        if reasons else ["  none"]
    seen = Counter(r["revision"][:SHORT] or "none" for r in records)
    out.append(f"revisions seen: {', '.join(f'{rev} {n}' for rev, n in sorted(seen.items()))} (current {current})")
    return "\n".join(out) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--targets", required=True, help="file: one '<entity>\\t<pod>' line per Running pod, '<entity>\\t' for a workload with none")
    parser.add_argument("--gate", required=True, help="the namespace gate as dg.sh reads it (gate_config.py read)")
    parser.add_argument("--since", help="RFC 3339, or 90m / 1h / 30s back from now; default: all the sidecars hold")
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    try:
        since = parse_time(args.since, now) if args.since else None
    except ValueError as error:
        print(f"gate-report: --since: {error}", file=sys.stderr)
        return 2
    records: list[dict] = []
    sidecars: list[str] = []
    read = 0
    for line in open(args.targets, encoding="utf-8").read().splitlines():
        if not line.strip():
            continue
        entity, _, pod = line.partition("\t")
        if not pod:
            sidecars.append(f"sidecar {entity} pod=- read=no (no Running pod)")
            continue
        try:
            events = read_events(args.namespace, pod, since)
        except (RuntimeError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
            # One line per sidecar: kubectl exec adds its own line to the remote error.
            sidecars.append(f"sidecar {entity} pod={pod} read=no ({' '.join(str(error).split())[:160]})")
            continue
        mine = no_verdict_records(entity, events, since)
        records += mine
        read += 1
        sidecars.append(f"sidecar {entity} pod={pod} read=yes events={len(events)} no_verdict_records={len(mine)}")
    out = report(args.namespace, json.loads(args.gate), records, sidecars, since, now)
    if not read:
        # Nothing read is not "nothing refused".
        sys.stdout.write("\n".join([out.split("\n", 1)[0], *sidecars, "no sidecar could be read"]) + "\n")
        return 1
    sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
