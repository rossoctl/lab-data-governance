"""The audit: every property the derived tables must satisfy for one
trace, stated in the contract's vocabulary and parameterised by the app's
declaration (``fleet.py``). Nothing here names an app.

Five axes, and a check belongs to exactly one:

- **soundness** — what the tables hold is well-formed and internally
  consistent (every span attached once, one anchor per interaction, kinds
  and content kinds as the contract classifies them, legs mirror spans,
  identities read off the facts, causal order, the forest law, no edge
  the topology forbids, one entity per workload);
- **completeness** — everything the turn did is in the turn's tree (the
  declared entities, edges, protocols and depth are all present; nothing
  of the app's activity in the window sits in another trace);
- **fidelity** — rows carry the truth of the act (the entry request holds
  the text the driver sent, the entry response holds the reply it got);
- **invariance** — the same truth yields the same rows (ids are the
  contract's uuid5, a quiet re-read changes nothing, no foreign marker).

- **risk** — the risk layer read the lineage it was given (a record's
  evidenced legs are the interaction's payload legs: one flow per leg).

Every check records expected and observed; the audit never stops at the
first failure, so one run shows every defect at once. A check that fails
exactly inside a gap the fleet declares (``known_gap``) is **KNOWN**: not a
failure of the run, never silent — it is listed by gap id in every record
and report. Assertion policy: soundness, fidelity, invariance and risk are
asserted outright; completeness is asserted against the declared turn
minus the declared gaps and reports the whole gap.
"""

from __future__ import annotations

import json
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from tests.live.fleet import Edge, Fleet, name_of

AXES = ("soundness", "completeness", "fidelity", "invariance", "risk")

NS_ENTITY = uuid.UUID("6f8d1c2e-0a3b-4d5e-8f90-1a2b3c4d5e6f")
NS_INTERACTION = uuid.UUID("9c7e2b4a-1d6f-4a8b-9c0d-2e3f4a5b6c7d")

# The contract's classification, as the test's own oracle (never imported
# from the code under test): (direction, protocol) → caller kind, callee
# kind, request content kind, response content kind.
KINDS: dict[tuple[str, str], tuple[str, str, str | None, str | None]] = {
    ("inbound", "a2a"): ("user", "agent", "agent_request", "agent_response"),
    ("inbound", "mcp"): ("user", "tool", "tool_call_arguments", "tool_call_result"),
    ("inbound", "inference"): ("user", "llm", "llm_chat_prompt", "llm_completion"),
    ("inbound", "http"): ("user", "agent", None, None),
    ("outbound", "a2a"): ("agent", "agent", "agent_request", "agent_response"),
    ("outbound", "mcp"): ("agent", "tool", "tool_call_arguments", "tool_call_result"),
    ("outbound", "inference"): ("agent", "llm", "llm_chat_prompt", "llm_completion"),
    ("outbound", "http"): ("agent", "service", None, None),
}
MCP_LIFECYCLE = ("mcp_lifecycle_request", "mcp_lifecycle_result")
MCP_DISCOVERY = ("tool_discovery_request", "tool_discovery_result")
FAILED_OUTCOMES = {"denied", "error", "abandoned"}


def content_kinds(direction: str, protocol: str, mcp_method: str | None) -> tuple[str | None, str | None]:
    row = KINDS[(direction, protocol)]
    if protocol == "mcp" and mcp_method:
        if mcp_method in ("initialize", "ping") or mcp_method.startswith(
                ("notifications/", "logging/", "$transport/")):
            return MCP_LIFECYCLE
        if mcp_method in ("tools/list", "resources/list", "prompts/list",
                          "resources/templates/list"):
            return MCP_DISCOVERY
    return row[2], row[3]


# --- what one trace looks like, read once -------------------------------------


@dataclass
class Span:
    span_id: str
    parent_id: str | None
    started_at: Any
    ended_at: Any
    error: bool | None
    attrs: dict

    def a(self, key: str) -> Any:
        return self.attrs.get(key)


@dataclass
class Interaction:
    id: str
    parent_id: str | None
    caller: dict  # entity row
    callee: dict
    spans: dict[str, str]  # span_id -> role
    legs: dict[str, dict]  # leg_type -> {occurred_at, payload_hash, error, content_kind}


@dataclass
class Snapshot:
    trace_id: str
    spans: dict[str, Span]
    interactions: dict[str, Interaction]
    window: tuple[Any, Any]
    probe_parents: frozenset[str] = frozenset()  # wire parents of the tier's own probes

    def is_probe(self, i: "Interaction") -> bool:
        """A probe the tier injected into this trace (its anchor is parented on
        a span id the test minted): asserted by the scenario that sent it, not
        by the app's topology."""
        a = self.anchor(i)
        return bool(a and a.parent_id and a.parent_id in self.probe_parents)

    @property
    def turn(self) -> dict[str, "Interaction"]:
        return {k: v for k, v in self.interactions.items() if not self.is_probe(v)}

    def anchor(self, i: Interaction) -> Span | None:
        ids = [s for s, r in i.spans.items() if r == "anchor"]
        return self.spans.get(ids[0]) if len(ids) == 1 else None

    def response_of(self, s: Span) -> Span | None:
        """The response span of *s*'s own exchange (same exchange id, same
        direction, role response)."""
        for c in self.spans.values():
            if (c.a("lineage.role") == "response"
                    and c.a("lineage.exchange.id") == s.a("lineage.exchange.id")
                    and c.a("lineage.direction") == s.a("lineage.direction")):
                return c
        return None

    def echo_of(self, i: Interaction) -> Span | None:
        """The callee-side inbound request span attached to *i*, if the
        callee is sidecar'd."""
        for sid, role in i.spans.items():
            s = self.spans.get(sid)
            if (s and role != "anchor" and s.a("lineage.role") == "request"
                    and s.a("lineage.direction") == "inbound"):
                return s
        return None


def read(dg, trace_id: str, *, lead: int = 5, tail: int = 60,
         probe_parents: frozenset[str] = frozenset()) -> Snapshot:
    spans = {r[0]: Span(r[0], r[1], r[2], r[3], r[4], r[5]) for r in dg.q(
        "SELECT span_id, parent_id, started_at, ended_at, error, attributes FROM spans "
        "WHERE trace_id = %s ORDER BY seq", (trace_id,))}
    assert spans, f"trace {trace_id} has no spans"
    start = min(s.started_at for s in spans.values()) - timedelta(seconds=lead)
    end = max((s.ended_at or s.started_at) for s in spans.values()) + timedelta(seconds=tail)
    ents = {}
    inters: dict[str, Interaction] = {}
    for iid, parent, caller, callee in dg.q(
            "SELECT id, parent_interaction_id, caller_entity_id, callee_entity_id "
            "FROM interactions WHERE trace_id = %s", (trace_id,)):
        for eid in (caller, callee):
            if eid not in ents:
                row = dg.q("SELECT id, kind::text, natural_key, namespace FROM entities WHERE id = %s", (eid,))
                ents[eid] = ({"id": row[0][0], "kind": row[0][1], "key": row[0][2], "ns": row[0][3]}
                             if row else {"id": eid, "kind": None, "key": None, "ns": None})
        inters[iid] = Interaction(iid, parent, ents[caller], ents[callee], {}, {})
    for iid, sid, role in dg.q(
            "SELECT interaction_id, span_id, role::text FROM interaction_spans WHERE trace_id = %s",
            (trace_id,)):
        inters.setdefault(iid, Interaction(iid, None, {}, {}, {}, {})).spans[sid] = role
    if inters:
        for iid, leg, at, ph, err, ck in dg.q("""
                SELECT l.interaction_id, l.leg_type::text, l.occurred_at, l.payload_hash, l.error,
                       p.content_kind
                FROM interaction_legs l LEFT JOIN interaction_payloads p ON p.content_hash = l.payload_hash
                WHERE l.interaction_id = ANY(%s)""", (list(inters),)):
            inters[iid].legs[leg] = {"occurred_at": at, "payload_hash": ph, "error": err,
                                     "content_kind": ck, "payload_exists": ck is not None or ph is None}
    return Snapshot(trace_id, spans, inters, (start, end), frozenset(probe_parents))


# --- the record --------------------------------------------------------------


@dataclass
class Check:
    id: str
    axis: str
    title: str
    ok: bool
    expected: Any
    observed: Any
    detail: Any = None
    known: str | None = None  # the declared gap this failure sits in, if any

    @property
    def verdict(self) -> str:
        return "PASS" if self.ok else ("KNOWN" if self.known else "FAIL")


@dataclass
class Audit:
    trace_id: str
    fleet: Fleet | None = None
    checks: list[Check] = field(default_factory=list)

    def add(self, id: str, axis: str, title: str, ok: bool, expected: Any, observed: Any,
            detail: Any = None, *, gap_check: str | None = None) -> Check:
        """*gap_check*: the id under which the fleet may declare a known gap
        for this check (defaults to *id*); a failure inside such a gap is KNOWN."""
        known = None
        if not ok and self.fleet is not None:
            g = self.fleet.gap_for_check(gap_check or id)
            known = g.id if g else None
        c = Check(id, axis, title, bool(ok), expected, observed, detail, known)
        self.checks.append(c)
        return c

    @property
    def failed(self) -> list[Check]:
        return [c for c in self.checks if not c.ok and not c.known]

    @property
    def known(self) -> list[Check]:
        return [c for c in self.checks if not c.ok and c.known]

    def as_dict(self) -> dict:
        return {"trace_id": self.trace_id,
                "summary": {ax: {"pass": sum(1 for c in self.checks if c.axis == ax and c.ok),
                                 "known": sum(1 for c in self.checks if c.axis == ax and not c.ok and c.known),
                                 "fail": sum(1 for c in self.checks if c.axis == ax and not c.ok and not c.known)}
                            for ax in AXES},
                "known_gaps_hit": sorted({c.known for c in self.known}),
                "checks": [{**c.__dict__, "verdict": c.verdict} for c in self.checks]}

    def markdown(self, *, failed_only: bool = False) -> str:
        rows = ["| # | axis | check | verdict | expected | observed |", "|---|---|---|---|---|---|"]
        for c in self.checks:
            if failed_only and c.ok:
                continue
            v = {"PASS": "PASS", "KNOWN": f"KNOWN `{c.known}`", "FAIL": "**FAIL**"}[c.verdict]
            rows.append(f"| {c.id} | {c.axis} | {c.title} | {v} | "
                        f"{_cell(c.expected)} | {_cell(c.observed)} |")
        s = self.as_dict()["summary"]
        head = " · ".join(f"{k} {v['pass']}/{v['pass'] + v['known'] + v['fail']}"
                          + (f" ({v['known']} known)" if v["known"] else "") for k, v in s.items())
        return f"trace `{self.trace_id}` — {head}\n\n" + "\n".join(rows) + "\n"


def _cell(v: Any) -> str:
    s = v if isinstance(v, str) else json.dumps(v, default=str, sort_keys=True)
    s = s.replace("|", "\\|").replace("\n", " ")
    return s if len(s) <= 160 else s[:157] + "…"


def _json(v: Any) -> Any:
    return json.loads(json.dumps(v, default=str))


# --- the checks --------------------------------------------------------------


def observed_edges(snap: Snapshot) -> dict[Edge, list[str]]:
    out: dict[Edge, list[str]] = defaultdict(list)
    for i in snap.turn.values():
        a = snap.anchor(i)
        proto = a.a("lineage.protocol") if a else "?"
        out[(name_of(i.caller["kind"], i.caller["key"]), name_of(i.callee["kind"], i.callee["key"]),
             proto)].append(i.id)
    return out


def observed_entities(snap: Snapshot) -> dict[str, set[str]]:
    """name -> kinds it was seen under (the turn's interactions, not the probes)."""
    out: dict[str, set[str]] = defaultdict(set)
    for i in snap.turn.values():
        for e in (i.caller, i.callee):
            if e.get("key"):
                out[name_of(e["kind"], e["key"])].add(e["kind"])
    return out


def soundness(au: Audit, snap: Snapshot, fleet: Fleet, *, skew_seconds: float = 0.5,
              attach: dict[str, set[int]] | None = None) -> None:
    S = snap
    # S1 every span attached exactly once
    attached = Counter(sid for i in S.interactions.values() for sid in i.spans)
    unattached = [sid for sid in S.spans if sid not in attached]
    twice = [sid for sid, n in attached.items() if n > 1]
    au.add("S1", "soundness", "every span of the trace is attached to exactly one interaction",
           not unattached and not twice, {"unattached": 0, "twice": 0},
           {"unattached": len(unattached), "twice": len(twice)},
           {"unattached": unattached[:10], "twice": twice[:10]})
    # S2 one anchor, 2 or 4 spans
    bad = {i.id: dict(Counter(i.spans.values())) for i in S.interactions.values()
           if list(i.spans.values()).count("anchor") != 1 or len(i.spans) not in (2, 4)}
    au.add("S2", "soundness", "one anchor per interaction; two spans (bare callee) or four (sidecar'd)",
           not bad, "1 anchor, 2|4 spans", f"{len(bad)} violations", bad)
    # S3 producer purity
    foreign = [sid for sid, s in S.spans.items() if not s.a("lineage.exchange.id")]
    au.add("S3", "soundness", "no span without lineage.exchange.id in the trace (producer purity)",
           not foreign, 0, len(foreign), foreign[:10])
    # S4 referential
    dangling_e = [i.id for i in S.interactions.values() if not i.caller.get("key") or not i.callee.get("key")]
    dangling_p = [i.id for i in S.interactions.values() for l in i.legs.values()
                  if l["payload_hash"] and not l["payload_exists"]]
    au.add("S4", "soundness", "caller/callee entities and leg payloads exist",
           not dangling_e and not dangling_p, {"entities": 0, "payloads": 0},
           {"entities": len(dangling_e), "payloads": len(dangling_p)})
    # S5 kinds per the contract's table
    kind_bad, ck_bad = {}, {}
    served = {name_of(i.callee["kind"], i.callee["key"]): i.callee["kind"]
              for i in S.interactions.values() if i.callee.get("key") and i.callee["kind"] in ("agent", "tool")}
    for i in S.interactions.values():
        a = S.anchor(i)
        if not a:
            continue
        d, p = a.a("lineage.direction"), a.a("lineage.protocol")
        if (d, p) not in KINDS:
            kind_bad[i.id] = f"unknown ({d}, {p})"
            continue
        ck, ce, _r, _s = KINDS[(d, p)]
        # A workload's caller kind is the kind it was seen serving (a tool pod
        # calling an agent is still a tool) — the table's caller kind is the
        # fallback when the trace never saw it serve.
        caller_name = name_of(i.caller["kind"], i.caller["key"]) if i.caller.get("key") else None
        ck = served.get(caller_name, ck)
        if (i.caller["kind"], i.callee["kind"]) != (ck, ce):
            kind_bad[i.id] = {"expected": (ck, ce), "observed": (i.caller["kind"], i.callee["kind"])}
        rq, rs = content_kinds(d, p, a.a("mcp.method"))
        for leg, want in (("request", rq), ("response", rs)):
            got = i.legs.get(leg, {}).get("content_kind")
            if i.legs.get(leg, {}).get("payload_hash") and got != want:
                ck_bad[f"{i.id}/{leg}"] = {"expected": want, "observed": got}
    au.add("S5", "soundness", "caller/callee kinds follow the contract's table",
           not kind_bad, "as classified", f"{len(kind_bad)} kind violations", kind_bad)
    au.add("S5.content", "soundness", "content kinds follow the contract's table (payload identity is content+kind)",
           not ck_bad, "as classified", f"{len(ck_bad)} content-kind violations", ck_bad)
    # S6 legs mirror spans
    leg_bad = {}
    for i in S.interactions.values():
        a = S.anchor(i)
        if not a:
            continue
        r = S.response_of(a)
        want = {"request": bool(a.a("input.value"))}
        if r is not None:
            want["response"] = bool(r.a("output.value"))
        got = {k: bool(v["payload_hash"]) for k, v in i.legs.items()}
        problems = []
        if set(want) != set(got):
            problems.append(f"legs {sorted(got)} vs spans {sorted(want)}")
        for k in want.keys() & got.keys():
            if want[k] != got[k]:
                problems.append(f"{k} payload {got[k]} vs span capture {want[k]}")
        if r is not None and "response" in i.legs:
            out = r.a("lineage.outcome")
            want_err = out in FAILED_OUTCOMES if out else r.error
            if i.legs["response"]["error"] != want_err:
                problems.append(f"response error {i.legs['response']['error']} vs outcome {out}")
        if "request" in i.legs and i.legs["request"]["error"] is not None:
            problems.append("request leg carries an error flag")
        if problems:
            leg_bad[i.id] = problems
    au.add("S6", "soundness", "legs mirror spans: request always, response iff response span, payload iff captured, error iff outcome failed",
           not leg_bad, "mirror", f"{len(leg_bad)} violations", leg_bad)
    # S7 identities from facts
    id_bad = {}
    for i in S.interactions.values():
        a = S.anchor(i)
        if not a:
            continue
        p = []
        if a.a("lineage.direction") == "outbound":
            want = f"{i.caller['kind']}:{a.a('lineage.self.namespace')}/{a.a('lineage.self.id')}"
            if i.caller["key"] != want:
                p.append({"caller": i.caller["key"], "expected": want})
        echo = S.echo_of(i)
        if echo is not None:
            want = f"{i.callee['kind']}:{echo.a('lineage.self.namespace')}/{echo.a('lineage.self.id')}"
        elif a.a("lineage.protocol") == "inference":
            want = f"llm:{a.a('lineage.peer.host')}/{a.a('inference.model')}"
        else:
            want = f"{i.callee['kind']}:{a.a('lineage.peer.host')}"
        if i.callee["key"] != want:
            p.append({"callee": i.callee["key"], "expected": want})
        if p:
            id_bad[i.id] = p
    au.add("S7", "soundness", "caller = anchor's self, callee = echo's self or peer.host (llm: host/model)",
           not id_bad, "from facts", f"{len(id_bad)} violations", id_bad)
    # S8 causal order, S9 containment
    order_bad, contain_bad = {}, {}
    for i in S.interactions.values():
        p = S.interactions.get(i.parent_id) if i.parent_id else None
        if not p:
            continue
        cr, pr = i.legs.get("request", {}).get("occurred_at"), p.legs.get("request", {}).get("occurred_at")
        if cr and pr and cr < pr:
            order_bad[i.id] = {"child_request": cr, "parent_request": pr}
        cs, ps = i.legs.get("response", {}).get("occurred_at"), p.legs.get("response", {}).get("occurred_at")
        if cs and ps and (cs - ps).total_seconds() > skew_seconds:
            contain_bad[i.id] = {"child_response": cs, "parent_response": ps,
                                 "late_by_s": round((cs - ps).total_seconds(), 3),
                                 "edge": f"{name_of(i.caller['kind'], i.caller['key'])}→{name_of(i.callee['kind'], i.callee['key'])}"}
    au.add("S8", "soundness", "a child's request never precedes its parent's request",
           not order_bad, 0, len(order_bad), _json(order_bad))
    au.add("S9", "soundness", f"a child's response never follows its parent's response by more than {skew_seconds}s",
           not contain_bad, 0, len(contain_bad), _json(contain_bad))
    # S10 forest law
    roots = [i for i in S.interactions.values() if not i.parent_id]
    orphans = [i.id for i in S.interactions.values() if i.parent_id and i.parent_id not in S.interactions]
    unstamped = [s for s in S.spans.values() if s.a("lineage.role") == "request"
                 and (s.a("lineage.parent.source") in ("wire", "none") or not s.a("lineage.parent.source"))]
    entry_ok = all(s.a("lineage.self.id") == fleet.entry for s in unstamped)
    au.add("S10", "soundness", "forest law: roots = unstamped entries, all the entry's; no orphan",
           len(roots) == len(unstamped) and not orphans and entry_ok and len(roots) >= 1,
           {"roots=entries": True, "orphans": 0, "entry": fleet.entry},
           {"roots": len(roots), "entries": len(unstamped), "orphans": len(orphans),
            "entry_ids": sorted({s.a("lineage.self.id") for s in unstamped})})
    # S11 no edge outside the topology
    edges = observed_edges(S)
    undeclared = {f"{c}→{e} [{p}]": ids for (c, e, p), ids in edges.items() if (c, e, p) not in fleet.edges}
    au.add("S11", "soundness", "every observed edge is in the declared topology",
           not undeclared, "declared only", f"{len(undeclared)} undeclared", undeclared)
    # S12 one entity per workload
    ents = observed_entities(S)
    multi = {n: sorted(k) for n, k in ents.items() if len(k) > 1}
    wrong = {n: sorted(k) for n, k in ents.items()
             if fleet.declared_kind(n) and fleet.declared_kind(n) not in k}
    au.add("S12", "soundness", "one entity per workload, of its declared kind",
           not multi and not wrong, "one kind each", {"two kinds": multi, "wrong kind": wrong})
    # S14 the attach preserves every declared wire-invisible act: a non-HTTP
    # egress port the fleet declares must be excluded from interception on
    # that workload's pod, or the sidecar swallows the act (SMTP, for one).
    if attach is not None:
        swallowed = {w: sorted(fleet.dark_ports(w) - set(attach.get(w, set())))
                     for w in fleet.workloads if fleet.dark_ports(w) - set(attach.get(w, set()))}
        au.add("S14", "soundness", "every declared non-HTTP egress port is excluded from interception on its pod",
               not swallowed, "all declared ports excluded", swallowed or "all excluded",
               {"declared": {w: sorted(fleet.dark_ports(w)) for w in fleet.workloads if fleet.dark_ports(w)},
                "excluded_on_pod": {w: sorted(v) for w, v in attach.items()}})


def completeness(au: Audit, dg, snap: Snapshot, fleet: Fleet, *, known: set[str]) -> dict:
    S = snap
    ents = observed_entities(S)
    edges = observed_edges(S)
    # Asserted: the deterministic part of the turn, outside the declared gaps.
    # Reported: how much of the LLM-chosen part this turn happened to cover,
    # and which of the covered hops landed in a declared gap instead of here.
    must_ents = fleet.must_entities - fleet.gap_entities
    missing = sorted(must_ents - set(ents))
    chosen_ents = fleet.turn_entities - fleet.must_entities
    in_gap = sorted((fleet.turn_entities & fleet.gap_entities) - set(ents))
    au.add("C1", "completeness", "every entity the turn deterministically involves is in the turn's tree; LLM-chosen coverage reported",
           not missing, f"{len(must_ents)} deterministic", len(set(ents) & must_ents),
           {"missing": missing, "llm_chosen_present": sorted(chosen_ents & set(ents)),
            "llm_chosen_absent": sorted(chosen_ents - set(ents)),
            "absent_in_declared_gap": in_gap, "present": sorted(ents)})
    must_edges = fleet.must_edges - fleet.gap_edges
    missing_e = sorted(f"{c}→{e} [{p}]" for (c, e, p) in must_edges - set(edges))
    chosen_edges = fleet.turn_edges - fleet.must_edges
    in_gap_e = sorted(f"{c}→{e} [{p}]" for (c, e, p) in (fleet.turn_edges & fleet.gap_edges) - set(edges))
    au.add("C2", "completeness", "every deterministic edge of the turn is in the turn's tree; LLM-chosen coverage reported",
           not missing_e, f"{len(must_edges)} deterministic", len(set(edges) & must_edges),
           {"missing": missing_e,
            "llm_chosen_coverage": f"{len(chosen_edges & set(edges))} of {len(chosen_edges)}",
            "llm_chosen_present": sorted(f"{c}→{e} [{p}]" for (c, e, p) in chosen_edges & set(edges)),
            "llm_chosen_absent": sorted(f"{c}→{e} [{p}]" for (c, e, p) in chosen_edges - set(edges)),
            "absent_in_declared_gap": in_gap_e})
    expected_edges = must_edges
    want_p = {p for _c, _e, p in expected_edges}
    got_p = {p for _c, _e, p in edges}
    au.add("C3", "completeness", "every protocol the turn uses (outside the declared gaps) appears in the tree",
           want_p <= got_p, sorted(want_p), sorted(got_p))
    want_d = max(fleet.depth_of(fleet.must_edges).values(), default=0)
    depth: dict[str, int] = {}

    def d_of(i: Interaction) -> int:
        if i.id in depth:
            return depth[i.id]
        depth[i.id] = 0 if not i.parent_id or i.parent_id not in S.interactions else 1 + d_of(S.interactions[i.parent_id])
        return depth[i.id]
    got_d = max((d_of(i) for i in S.turn.values()), default=0)
    au.add("C4", "completeness", "the tree is as deep as the topology (longest declared path)",
           got_d >= want_d, want_d, got_d, dict(Counter(depth.values())))
    # C5 window census
    start, end = S.window
    rows = dg.q("""
        SELECT trace_id, count(*),
               string_agg(DISTINCT attributes->>'lineage.self.id', ','),
               string_agg(DISTINCT attributes->>'lineage.self.namespace', ','),
               string_agg(DISTINCT attributes->>'lineage.protocol', ','),
               string_agg(DISTINCT split_part(attributes->>'lineage.peer.host', ':', 1), ','),
               string_agg(DISTINCT attributes->>'lineage.parent.source', ','),
               bool_or(attributes->>'lineage.exchange.id' IS NULL),
               (SELECT count(*) FROM interactions x WHERE x.trace_id = s.trace_id),
               (SELECT min(started_at) FROM spans y WHERE y.trace_id = s.trace_id)
        FROM spans s WHERE started_at BETWEEN %s AND %s GROUP BY trace_id ORDER BY min(started_at)""",
        (start, end))
    census, outside, in_gap, foreign_spans = [], [], [], 0
    for tid, n, selfs, nss, protos, peers, srcs, foreign, inter, first in rows:
        gap = None
        if tid == S.trace_id:
            cls = "turn"
        elif tid in known:
            cls = "known"
        elif foreign:
            cls, foreign_spans = "foreign", foreign_spans + n
        elif fleet.namespace in (nss or "").split(","):
            # An app trace that is not ours: a declared gap (a framework's
            # own-trace MCP session from a named workload) or an escape.
            own = [x for x in (selfs or "").split(",") if x]
            gaps = {g.id for x in own for g in [fleet.gap_for_stray(x, None)] if g
                    and all(p in (g.stray_protocol, "http", None) for p in (protos or "").split(","))}
            gap = sorted(gaps)[0] if gaps and len(own) <= 2 else None
            cls = "gap" if gap else "fleet"
        else:
            cls = "other"
        row = {"trace": tid, "class": cls, "gap": gap, "spans": n, "self_ids": selfs, "protocols": protos,
               "peers": peers, "parent_sources": srcs, "interactions_all_time": inter,
               "long_lived": bool(first and first < start),
               "has_entry": any(x in (srcs or "") for x in ("wire", "none"))}
        census.append(row)
        if cls == "fleet":
            outside.append(row)
        elif cls == "gap":
            in_gap.append(row)
    au.add("C5", "completeness", "no activity of the app in the window sits outside the turn, the known traces and the declared gaps",
           not outside, 0, len(outside),
           {"outside": [{k: v for k, v in r.items() if k != "class"} for r in outside],
            "in_declared_gap": [{"trace": r["trace"], "gap": r["gap"], "self_ids": r["self_ids"],
                                 "protocols": r["protocols"]} for r in in_gap],
            "long_lived_without_entry": [r["trace"] for r in outside if r["long_lived"] and not r["has_entry"]]})
    # I4 no probe payload of ours leaked into a stray trace (a critical
    # record there would mean a sidecar attributed our bytes elsewhere).
    stray_ids = [r["trace"] for r in in_gap + outside]
    crit = dg.q("SELECT trace_id, count(*) FROM interaction_risk_records WHERE trace_id = ANY(%s) "
                "AND risk_level = 'critical' GROUP BY 1", (stray_ids,)) if stray_ids else []
    au.add("I4", "invariance", "no critical risk record on a stray trace of the window (nothing of ours leaked there)",
           not crit, 0, len(crit), {"traces": dict(crit)})
    au.add("S13", "soundness", "no foreign (non-sidecar) span in the window",
           foreign_spans == 0, 0, foreign_spans)
    # C7 observed anywhere in the window
    seen = set()
    for r in census:
        for x in (r["self_ids"] or "").split(","):
            seen.add(x)
        for x in (r["peers"] or "").split(","):
            seen.add(x.split(".")[0] if x.endswith("svc.cluster.local") else x)
    for i in S.interactions.values():
        for e in (i.caller, i.callee):
            if e.get("key"):
                seen.add(name_of(e["kind"], e["key"]))
    never = sorted(n for n in fleet.turn_entities if n not in seen and n.split("/")[0].split(":")[0] not in seen)
    never_must = [n for n in never if n in fleet.must_entities]
    au.add("C7", "completeness", "every entity the turn deterministically involves was observed by some sidecar in the window (any trace); the rest reported",
           not never_must, len(fleet.must_entities), len(fleet.must_entities) - len(never_must),
           {"never_observed_deterministic": never_must, "never_observed_llm_chosen": [n for n in never if n not in fleet.must_entities],
            "observed_of_turn": f"{len(fleet.turn_entities) - len(never)} of {len(fleet.turn_entities)}"})
    return {"census": _json(census)}


def fidelity(au: Audit, snap: Snapshot, fleet: Fleet, *, markers: list[str], reply: str | None) -> None:
    S = snap
    entries = [i for i in S.turn.values() if not i.parent_id
               and (S.anchor(i) is not None) and S.anchor(i).a("lineage.self.id") == fleet.entry]
    if not markers and reply is None:
        return
    if len(entries) != 1:
        au.add("F0", "fidelity", "exactly one entry interaction to compare the driver's text against",
               False, 1, len(entries))
        return
    a = S.anchor(entries[0])
    inp = a.a("input.value") or ""
    miss = [m for m in markers if m not in inp]
    if markers:
        au.add("F1", "fidelity", "the entry request payload carries the text the driver sent",
               not miss, markers, {"missing": miss, "captured_chars": len(inp)})
    if reply is not None:
        r = S.response_of(a)
        out = ((r.a("output.value") or "") if r else "").strip()
        want = reply.strip()
        # An uncaptured or missing response span is a FAIL here, not a pass by
        # vacuity: "" is a substring of everything.
        au.add("F2", "fidelity", "the entry response payload is the reply the driver received",
               r is not None and out != "" and want != "" and (want in out or out in want),
               want[:80], (out[:80] if r else "no response span"))


def invariance(au: Audit, dg, snap: Snapshot, *, quiet_seconds: float, foreign_markers: list[str]) -> None:
    S = snap
    bad_i, bad_e = [], []
    for i in S.interactions.values():
        a = S.anchor(i)
        if a and i.id != str(uuid.uuid5(NS_INTERACTION, f"{S.trace_id}/{a.span_id}")):
            bad_i.append(i.id)
        for e in (i.caller, i.callee):
            if e.get("key") and e["id"] != str(uuid.uuid5(NS_ENTITY, e["key"])):
                bad_e.append(e["id"])
    au.add("I1", "invariance", "ids are the contract's uuid5 (trace/anchor; natural_key)",
           not bad_i and not bad_e, 0, {"interactions": len(bad_i), "entities": len(set(bad_e))})
    if foreign_markers:
        hits = {}
        for m in foreign_markers:
            rows = dg.q("""SELECT attributes->>'lineage.self.id', attributes->>'lineage.direction'
                           FROM spans WHERE trace_id = %s
                           AND (attributes->>'input.value' LIKE %s OR attributes->>'output.value' LIKE %s)""",
                        (S.trace_id, f"%{m}%", f"%{m}%"))
            if rows:
                hits[m] = [f"{r[0]}/{r[1]}" for r in rows]
        au.add("I2", "invariance", "no payload of the trace carries another trace's marker (isolation)",
               not hits, [], hits)
    before = _fingerprint(dg, S.trace_id)
    time.sleep(quiet_seconds)
    after = _fingerprint(dg, S.trace_id)
    au.add("I3", "invariance", f"a quiet re-read after {quiet_seconds:.0f}s changes no lineage row",
           before == after, before, after)


def risk(au: Audit, dg, snap: Snapshot) -> None:
    """The risk layer read the lineage it was given. R1: a record's evidenced
    legs are exactly the interaction's legs at the time of reading (a leg
    without a payload is still evidence: it decides as {"payload": null}) —
    the OPA input is one flow per leg (#271/#274), so a record computed on
    fewer legs than exist is stale, one on more is impossible. Stated on the
    record, not on the input layout."""
    S = snap
    rows = dg.q("""
        SELECT DISTINCT ON (interaction_id) interaction_id, legs_evidenced, version
        FROM interaction_risk_records WHERE trace_id = %s
        ORDER BY interaction_id, version DESC""", (S.trace_id,))
    bad, no_record = {}, []
    for i in S.interactions.values():
        present = sorted(i.legs)
        rec = next((r for r in rows if r[0] == i.id), None)
        if rec is None:
            if present:
                no_record.append(i.id)
            continue
        ev = rec[1] if isinstance(rec[1], list) else json.loads(rec[1] or "[]")
        if sorted(ev) != present:
            bad[i.id] = {"record_v": rec[2], "legs_evidenced": sorted(ev), "legs_present": present}
    au.add("R1", "risk", "every current record's evidenced legs are the interaction's legs (one flow per leg)",
           not bad and not no_record, "records = legs present",
           {"mismatch": len(bad), "legs_without_record": len(no_record)},
           {"mismatch": bad, "legs_without_record": no_record})


def _fingerprint(dg, trace_id: str) -> dict:
    (n_s,) = dg.one("SELECT count(*) FROM spans WHERE trace_id = %s", (trace_id,))
    rows = dg.q("SELECT id, parent_interaction_id, caller_entity_id, callee_entity_id "
                "FROM interactions WHERE trace_id = %s ORDER BY id", (trace_id,))
    (n_l,) = dg.one("SELECT count(*) FROM interaction_legs l JOIN interactions i ON i.id = l.interaction_id "
                    "WHERE i.trace_id = %s", (trace_id,))
    (n_is,) = dg.one("SELECT count(*) FROM interaction_spans WHERE trace_id = %s", (trace_id,))
    return {"spans": n_s, "interactions": len(rows), "legs": n_l, "attached": n_is,
            "shape": str(uuid.uuid5(NS_INTERACTION, json.dumps(rows)))}


def run(dg, trace_id: str, fleet: Fleet, *, known: set[str] = frozenset(), markers: list[str] = (),
        reply: str | None = None, foreign_markers: list[str] = (), quiet_seconds: float = 12.0,
        skew_seconds: float = 0.5, attach: dict[str, set[int]] | None = None,
        quiet_reread: bool = True, probe_parents: frozenset[str] = frozenset(),
        with_risk: bool = True) -> tuple[Audit, dict]:
    """All axes; ``with_risk=False`` leaves R1 out so a lineage-only scenario
    reads nothing from the risk tables (``risk_only`` asserts it later)."""
    snap = read(dg, trace_id, probe_parents=probe_parents)
    au = Audit(trace_id, fleet)
    soundness(au, snap, fleet, skew_seconds=skew_seconds, attach=attach)
    extra = completeness(au, dg, snap, fleet, known=set(known) - {trace_id})
    fidelity(au, snap, fleet, markers=list(markers), reply=reply)
    invariance(au, dg, snap, quiet_seconds=quiet_seconds if quiet_reread else 0.0,
               foreign_markers=list(foreign_markers))
    if with_risk:
        risk(au, dg, snap)
    extra["edges"] = {f"{c}→{e} [{p}]": len(ids) for (c, e, p), ids in observed_edges(snap).items()}
    extra["entities"] = {n: sorted(k) for n, k in observed_entities(snap).items()}
    extra["spans"] = len(snap.spans)
    extra["interactions"] = len(snap.interactions)
    extra["probes"] = sorted(i.id for i in snap.interactions.values() if snap.is_probe(i))
    return au, extra


def risk_only(dg, trace_id: str, fleet: Fleet, *, probe_parents: frozenset[str] = frozenset()) -> Audit:
    """The risk axis alone (R1) on a trace whose lineage axes were audited
    earlier: the first scenario that reads the risk tables asserts it."""
    snap = read(dg, trace_id, probe_parents=probe_parents)
    au = Audit(trace_id, fleet)
    risk(au, dg, snap)
    return au
