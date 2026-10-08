"""Tier C — the app's own ledger against the derived tables.

The app (agent-examples-snp, ``ROSSOCTL_LEDGER=stdout``) writes one JSON
line per act it performs: every HTTP exchange in and out with the
``traceparent`` as sent, every tool call, every store act (``ledger/README.md``
in the app repo). This module reads those lines back from the pods' logs for
the run window and compares them with what the sidecars recorded and the
derivation produced, hop by hop:

- **exchange ⟷ anchor**, one to one: an outbound ledger record whose sent
  trace is the turn's joins the outbound request span of the same pod, host
  and method in that trace; an inbound ledger record joins the inbound
  request span. Residuals are listed both ways — a ledger-only exchange is a
  sidecar miss unless the fleet declares that wire invisible; a table-only
  exchange is a derivation or attribution defect.
- **payload digest**: the sidecar's ``input.value`` is the parser's reduction
  of the request (contract §5); the ledger's ``reduced_sha`` is the SHA-256
  of the canonical JSON of the same reduction. Equal means the captured bytes
  are the bytes the app sent, not a truncation or a re-serialisation.
- **timing envelope**: outbound, the span's start falls inside the app's send
  window; inbound, the sidecar saw the request no later than the app began
  handling it (a busy tool server may queue a request for seconds before the
  app reads it, so the wait has no upper bound).
- **dark hops**: every store record under the turn's trace is listed, and by
  construction has no table counterpart.
- **self-reported strays**: an outbound record with no ambient trace whose
  sent traceparent names another trace — the framework's own session, said
  by the app, not inferred from the tables.
- **turn completeness**: the pods whose ledgers carry the turn's trace id
  versus the entity set of the turn's tree.

Nothing here is specific to one app; the fleet declaration names the
workloads whose logs to read.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

PREFIX = "LEDGER "
# The lineage plugin emits nothing for these paths by default (contract):
# an exchange the ledger saw there has no span by design.
BYPASS_PREFIXES = ("/.well-known/", "/healthz", "/readyz", "/health")


def canonical_sha(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


_WIRE_KEYS = {"xid", "dir", "event", "method", "host"}


def _well_formed(rec: Any) -> bool:
    """A record the join can use. Log text is untrusted: a line that merely
    contains the prefix, or a record missing the keys the join needs, is
    counted as malformed, never raised on."""
    if not isinstance(rec, dict) or rec.get("ledger") != 1 or not isinstance(rec.get("kind"), str):
        return False
    if rec["kind"] == "wire":
        if not _WIRE_KEYS <= set(rec) or rec["dir"] not in ("in", "out") or rec["event"] not in ("start", "end"):
            return False
        for k in ("t0", "ts", "ms"):
            if k in rec and rec[k] is not None and not isinstance(rec[k], (int, float)):
                return False
    elif rec["kind"] == "tool":
        if not isinstance(rec.get("name"), str):
            return False
    elif rec["kind"] == "store":
        if not isinstance(rec.get("store"), str):
            return False
    return True


def collect(kube, env, workloads: list[str], since: datetime) -> tuple[list[dict], dict]:
    """Every ledger record of every workload since *since* (UTC), from the
    pods' logs, plus a tally of lines that carried the prefix but were not a
    well-formed record (a forged or truncated line is a count, not a crash).
    A pod that writes no ledger line contributes nothing — the comparison
    then reports it under ``pods_without_ledger``."""
    out: list[dict] = []
    malformed: dict[str, int] = {}
    stamp = since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for w in workloads:
        text = kube.run(["logs", f"deploy/{w}", "--all-containers", f"--since-time={stamp}",
                         "--timestamps=false"], ns=env.app_ns, check=False, timeout=120)
        for line in text.splitlines():
            if not line.startswith(PREFIX):   # anchored: the ledger writes the prefix at column 0
                continue
            try:
                rec = json.loads(line[len(PREFIX):])
            except json.JSONDecodeError:
                malformed[w] = malformed.get(w, 0) + 1
                continue
            if not _well_formed(rec):
                malformed[w] = malformed.get(w, 0) + 1
                continue
            # A tool pod names itself by its function (create_booking); the
            # sidecar's self.id is the workload name (create-booking). Join on
            # the workload, keep the app's own name for the record.
            rec["pod_name"] = rec.get("pod")
            rec["pod"] = w
            rec["_workload"] = w
            out.append(rec)
    return out, malformed


def short_host(h: str | None) -> str:
    h = (h or "").split(":")[0]
    return h.split(".")[0] if h.endswith(".svc.cluster.local") or h.endswith(".svc") else h


def _span_digests(value: str | None) -> set[str]:
    """Every digest the sidecar's captured value can legitimately equal on the
    ledger's side: the canonical JSON (the ledger's reduction), the raw text
    (an a2a message reduced to its text), and for a chat request the
    messages projected to role+content (the sidecar adds a byte count)."""
    if not value:
        return set()
    out = {hashlib.sha256(value.encode("utf-8")).hexdigest()}
    try:
        j = json.loads(value)
    except Exception:
        return out
    out.add(canonical_sha(j))
    if isinstance(j, list) and j and all(isinstance(m, dict) and "role" in m for m in j):
        out.add(canonical_sha([{"role": m.get("role"), "content": m.get("content")} for m in j]))
    return out


def _ledger_digests(part: dict | None) -> set[str]:
    return {v for k, v in (part or {}).items() if k in ("reduced_sha", "text_sha", "calls_sha", "sha") and v}


def compare(dg, trace_id: str, records: list[dict], fleet, *, skew_seconds: float = 2.0,
            malformed: dict | None = None) -> dict:
    T = trace_id
    rows = dg.q("""
        SELECT span_id, started_at, ended_at, attributes FROM spans
        WHERE trace_id = %s AND attributes->>'lineage.role' = 'request' ORDER BY seq""", (T,))
    spans = [{"span_id": r[0], "started_at": r[1], "ended_at": r[2], **{
        "self_id": r[3].get("lineage.self.id"), "direction": r[3].get("lineage.direction"),
        "protocol": r[3].get("lineage.protocol"), "peer": short_host(r[3].get("lineage.peer.host")),
        "method": r[3].get("http.method"), "path": r[3].get("url.path"),
        "input_shas": _span_digests(r[3].get("input.value")),
        "mcp_method": r[3].get("mcp.method"), "a2a_method": r[3].get("a2a.method")}} for r in rows]
    resp = {r[0]: r[1] for r in dg.q("""
        SELECT attributes->>'lineage.exchange.id', attributes->>'output.value' FROM spans
        WHERE trace_id = %s AND attributes->>'lineage.role' = 'response'""", (T,))}
    for sp in spans:
        sp["output_shas"] = _span_digests(resp.get(sp["span_id"]))

    # The app's view of the turn: exchanges whose wire carried this trace.
    out_starts = [r for r in records if r.get("kind") == "wire" and r.get("dir") == "out"
                  and r.get("event") == "start" and r.get("sent_trace") == T]
    out_ends = {r["xid"]: r for r in records if r.get("kind") == "wire" and r.get("dir") == "out"
                and r.get("event") == "end"}
    in_ends = [r for r in records if r.get("kind") == "wire" and r.get("dir") == "in"
               and r.get("event") == "end" and r.get("received_trace") == T]
    tools = [r for r in records if r.get("kind") == "tool" and r.get("ambient_trace") == T]
    stores = [r for r in records if r.get("kind") == "store" and r.get("ambient_trace") == T]

    def key_of_span(s):
        return (s["self_id"], s["direction"], s["peer"] if s["direction"] == "outbound" else None, s["method"])

    def key_of_out(r):
        return (r["pod"], "outbound", short_host(r["host"]), r["method"])

    def key_of_in(r):
        return (r["pod"], "inbound", None, r["method"])

    # Workloads whose process wrote no ledger line in the window cannot be
    # compared (the driver's own probes from demo-client are kubectl-exec'd
    # programs, not the app's process): their spans are reported apart.
    ledgered = {r["_workload"] for r in records}
    unmatched_spans = {s["span_id"]: s for s in spans if s["self_id"] in ledgered}
    unledgered_spans = [{"span_id": s["span_id"], "pod": s["self_id"], "dir": s["direction"], "peer": s["peer"],
                         "method": s["method"], "proto": s["protocol"]} for s in spans if s["self_id"] not in ledgered]
    pairs, ledger_only = [], []

    def verdict(want: set[str], have: set[str]) -> str:
        if not have:
            return "uncaptured"
        if not want:
            return "n/a"
        return "equal" if want & have else "different"

    for r in out_starts + in_ends:
        k = key_of_out(r) if r["dir"] == "out" else key_of_in(r)
        want = _ledger_digests(r.get("req")) if isinstance(r.get("req"), dict) else set()
        cands = [s for s in unmatched_spans.values() if key_of_span(s) == k]
        # Prefer the candidates whose captured input is what the app sent, and
        # among them (two identical requests in one trace, such as two MCP
        # `initialize` calls) or among all (a bodyless exchange has no digest)
        # the nearest in time.
        t0 = r.get("t0") or r.get("ts")
        same = [s for s in cands if want & s["input_shas"]] or cands
        best = min(same, key=lambda s: abs(s["started_at"].timestamp() - float(t0)) if t0 else 0) if same else None
        if best is None:
            ledger_only.append({"pod": r["pod"], "dir": r["dir"], "host": r.get("host"), "method": r["method"],
                                "path": r.get("path"), "proto": r.get("proto"), "status": r.get("status"),
                                "declared_dark": _declared_dark(fleet, r)})
            continue
        del unmatched_spans[best["span_id"]]
        end = out_ends.get(r.get("xid")) if r["dir"] == "out" else r
        resp_want = _ledger_digests((end or {}).get("resp")) if end else set()
        started = best["started_at"].timestamp()
        if t0 is None:
            window_ok = True
        elif r["dir"] == "out":
            # the sidecar sees the request inside the app's send window
            window_ok = float(t0) - skew_seconds <= started <= float(t0) + ((end or {}).get("ms") or 0) / 1000 + skew_seconds
        else:
            # inbound: the sidecar saw the request no later than the app began
            # handling it; a busy single-worker tool server queues a request
            # for seconds first, so no lower bound on the wait
            window_ok = started <= float(t0) + skew_seconds
        pairs.append({"pod": r["pod"], "dir": r["dir"], "host": r.get("host"), "method": r["method"],
                      "proto_ledger": r.get("proto"), "proto_sidecar": best["protocol"], "span_id": best["span_id"],
                      "jsonrpc_method": (r.get("req") or {}).get("jsonrpc_method"),
                      "req_digest": verdict(want, best["input_shas"]),
                      "resp_digest": verdict(resp_want, best["output_shas"]),
                      "timing_ok": window_ok, "streamed": (end or {}).get("streamed")})
    table_only = [{"span_id": s["span_id"], "pod": s["self_id"], "dir": s["direction"], "peer": s["peer"],
                   "method": s["method"], "proto": s["protocol"]} for s in unmatched_spans.values()]

    # Turn completeness: pods whose ledger carries T vs the tree's entity set.
    ledger_pods = sorted({r["pod"] for r in records
                          if T in (r.get("sent_trace"), r.get("received_trace"), r.get("ambient_trace"))})
    ents = {name for (name,) in dg.q("""
        SELECT DISTINCT e.natural_key FROM interactions i
        JOIN entities e ON e.id IN (i.caller_entity_id, i.callee_entity_id) WHERE i.trace_id = %s""", (T,))}
    tree_pods = sorted({k.split(":", 1)[1].split("/", 1)[1] for k in ents if "/" in k} & set(fleet.workloads))

    strays = [{"pod": r["pod"], "host": r.get("host"), "proto": r.get("proto"), "sent_trace": r.get("sent_trace")}
              for r in records if r.get("kind") == "wire" and r.get("dir") == "out" and r.get("event") == "start"
              and r.get("ambient_trace") is None and r.get("sent_trace") and r.get("sent_trace") != T
              and r.get("proto") in ("mcp", "a2a")]
    pods_without = sorted(set(fleet.workloads) - {r["_workload"] for r in records})
    # Where the two reductions are defined to coincide (a tool call's arguments
    # and result text, an a2a message's text), a difference is a capture defect
    # and is asserted. Elsewhere (lifecycle results, a chat request carrying a
    # tool-call turn, a2a replies that the server emits twice) the sidecar's
    # reduction differs from the ledger's by construction: recorded apart.
    def aligned(p):
        return (p["jsonrpc_method"] == "tools/call" and p["proto_sidecar"] == "mcp") or \
               (p["proto_sidecar"] == "a2a" and p["dir"] == "in")
    digest_bad = [p for p in pairs if aligned(p) and (p["req_digest"] == "different" or
                                                      (p["jsonrpc_method"] == "tools/call" and p["resp_digest"] == "different"))]
    digest_unaligned = [p for p in pairs if not aligned(p) and (p["req_digest"] == "different" or p["resp_digest"] == "different")]
    return {
        "trace_id": T, "pairs": pairs, "ledger_only": ledger_only, "table_only": table_only,
        "unledgered_spans": unledgered_spans,
        "ledger_only_undeclared": [x for x in ledger_only if not x["declared_dark"]],
        "digest_mismatch": digest_bad, "digest_unaligned": digest_unaligned,
        "timing_violations": [p for p in pairs if not p["timing_ok"]],
        "tools": [{"pod": r["pod"], "name": r["name"], "error": r.get("error")} for r in tools],
        "dark_hops": [{"pod": r["pod"], "store": r["store"], "op": r.get("op"), "verb": r.get("verb"),
                       "table": r.get("table")} for r in stores],
        "ledger_pods": ledger_pods, "tree_pods": tree_pods,
        "ledger_not_in_tree": sorted(set(ledger_pods) - set(tree_pods)),
        "tree_not_in_ledger": sorted(set(tree_pods) - set(ledger_pods)),
        "self_reported_strays": strays, "pods_without_ledger": pods_without, "malformed": malformed or {},
        "counts": {"records": len(records), "out": len(out_starts), "in": len(in_ends), "spans": len(spans),
                   "pairs": len(pairs)},
    }


def _declared_dark(fleet, r: dict) -> bool:
    """A destination the fleet lists as wire-invisible, an https call, or a
    path the lineage plugin bypasses by contract."""
    host = short_host(r.get("host"))
    path = r.get("path") or ""
    return (any(host and host in str(u.get("what", "")) for u in fleet.unobservable) or r.get("proto") == "https"
            or any(path.startswith(b) for b in BYPASS_PREFIXES))


def assert_consistent(c: dict, checks=None) -> None:
    """The comparisons that must hold whenever a ledger exists. An empty ledger
    is not a consistent one: with no records every residual is trivially
    empty, so the first assertion is that the app actually wrote the turn.
    With ``checks`` every residual is a row (asserted or recorded)."""
    from tests.live.checks import expect, record
    expect(checks, "ledger.joined", "ledger records joined to this trace", "records > 0 and pairs > 0",
           c["counts"], ok=c["counts"]["records"] > 0 and c["counts"]["pairs"] > 0,
           comment=f"pods without a record in the window: {c['pods_without_ledger']} (is ROSSOCTL_LEDGER=stdout on the fleet?)")
    expect(checks, "ledger.malformed", "log lines carrying the ledger prefix that were not records", {}, c.get("malformed") or {})
    expect(checks, "ledger.ledger_only_undeclared", "exchanges the app made that no sidecar recorded and no declaration covers",
           [], c["ledger_only_undeclared"])
    expect(checks, "ledger.table_only", "sidecar exchanges the app's ledger does not know", [], c["table_only"])
    expect(checks, "ledger.digest_mismatch", "captured payload differs from what the app sent/received (aligned pairs)", [],
           [(p["pod"], p["dir"], p["host"], p["jsonrpc_method"], p["req_digest"], p["resp_digest"]) for p in c["digest_mismatch"]])
    expect(checks, "ledger.timing_violations", "spans outside the app's send window", [],
           [(p["pod"], p["dir"], p["host"], p["span_id"]) for p in c["timing_violations"]])
    missing = [p for p in c["tree_not_in_ledger"] if p not in c["pods_without_ledger"]]
    expect(checks, "ledger.tree_in_ledger", "entities in the tree whose pod wrote a ledger but not for this trace", [], missing)
    record(checks, "ledger.pairs", "ledger exchange ⟷ request span pairs", len(c["pairs"]),
           comment=f"out {c['counts']['out']} in {c['counts']['in']} of {c['counts']['spans']} spans")
    record(checks, "ledger.digest_unaligned", "pairs whose reductions are not aligned (recorded)", len(c["digest_unaligned"]))
    record(checks, "ledger.unledgered_spans", "spans of pods that wrote no ledger line", len(c["unledgered_spans"]),
           comment=", ".join(sorted({s["pod"] for s in c["unledgered_spans"]})))
    record(checks, "ledger.ledger_only_declared", "ledger-only exchanges covered by a declaration or a bypass path",
           len(c["ledger_only"]) - len(c["ledger_only_undeclared"]))
    record(checks, "ledger.dark_hops", "store records under the trace", len(c["dark_hops"]))
    record(checks, "ledger.tools", "tool records under the trace", len(c["tools"]))
    record(checks, "ledger.strays", "self-reported strays (ambient trace null, fresh sent trace)", len(c["self_reported_strays"]))
    record(checks, "ledger.pods", "pods whose ledger carries the trace / in the tree", f"{c['ledger_pods']} / {c['tree_pods']}")
    record(checks, "ledger.pods_without", "pods that wrote no ledger line in the window", c["pods_without_ledger"])
