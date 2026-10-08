"""The app's declaration: the one app-specific input the audit takes.

The audit (``tests/live/audit.py``) is general — every check is stated in
the contract's vocabulary (workloads, protocols, entity kinds, legs) and
never names an app. What it cannot know on its own is what the app *is*:
which workloads carry a sidecar, which hosts are bare callees, which
hops the topology allows, and which of them one full turn must produce.
That is this file, loaded from ``tests/live/apps/<app>.yaml``.

Names are the workload's ``lineage.self.id`` for sidecar'd pods, the host
(no port) for bare HTTP callees, and ``host:port/model`` for the LLM. The
symbol ``llm`` in an edge stands for the declared LLM.

``known_gap`` entries declare what the app is known NOT to deliver today
and why (a framework's pod-lifetime MCP session, a stale toolset parent,
a derivation defect held for a fix). A check that fails exactly inside a
declared gap reads KNOWN, not FAIL: the run stays green on the honest
number and every report shows the gap by id. A gap is never silent and
never widens a check beyond what it names.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

Edge = tuple[str, str, str]  # (caller, callee, protocol)


@dataclass(frozen=True)
class KnownGap:
    id: str
    why: str
    entities: frozenset[str] = frozenset()
    edges: frozenset[Edge] = frozenset()
    checks: frozenset[str] = frozenset()      # check ids this gap may fail (e.g. S9, S5.content)
    stray_from: frozenset[str] = frozenset()  # workloads whose own-trace MCP sessions are this gap
    stray_protocol: str | None = None
    issue: str | None = None                  # where it is tracked


@dataclass(frozen=True)
class Fleet:
    app: str
    namespace: str
    entry: str
    agents: frozenset[str]
    tools: frozenset[str]
    llm: str
    services: frozenset[str]
    edges: frozenset[Edge]
    unobservable: tuple[dict, ...]
    turn_entities: frozenset[str]
    turn_edges: frozenset[Edge]
    known_gaps: tuple[KnownGap, ...] = ()
    # The turn's edges that do not depend on a model's choice: the entry, and
    # hops a tool or the framework makes unconditionally. Everything else in
    # the turn is LLM-chosen — its coverage is reported, never asserted
    # (shape before counts: a 7B model skips tools between two identical
    # prompts). Defaults to the entry edge alone.
    must_edges: frozenset[Edge] = frozenset()
    preflight: dict = field(default_factory=dict)  # plain_image_workloads, llm_env_agents
    service_kinds: dict = field(default_factory=dict)  # bare callee -> kind when not 'service' (an MCP-shaped host is a tool)

    @property
    def workloads(self) -> frozenset[str]:
        """Every name whose sidecar emits ``lineage.self.id``."""
        return self.agents | self.tools

    @property
    def names(self) -> frozenset[str]:
        return self.workloads | self.services | {self.llm}

    def declared_kind(self, name: str) -> str | None:
        if name in self.agents:
            return "agent"
        if name in self.tools:
            return "tool"
        if name in self.services:
            return self.service_kinds.get(name, "service")
        if name == self.llm:
            return "llm"
        return None

    @property
    def must_entities(self) -> frozenset[str]:
        return frozenset(n for e in self.must_edges for n in e[:2])

    @property
    def gap_entities(self) -> frozenset[str]:
        return frozenset(n for g in self.known_gaps for n in g.entities)

    @property
    def gap_edges(self) -> frozenset[Edge]:
        return frozenset(e for g in self.known_gaps for e in g.edges)

    def gap_for_check(self, check_id: str) -> KnownGap | None:
        return next((g for g in self.known_gaps if check_id in g.checks), None)

    def gap_for_stray(self, self_id: str, protocol: str | None) -> KnownGap | None:
        return next((g for g in self.known_gaps
                     if self_id in g.stray_from
                     and (protocol is None or g.stray_protocol in (None, protocol))), None)

    def dark_ports(self, workload: str) -> frozenset[int]:
        """Non-HTTP egress ports a workload uses (declared on ``unobservable``
        entries as ``port``): the attach must exclude every one of them, or the
        act is not invisible — it is broken."""
        return frozenset(int(u["port"]) for u in self.unobservable
                         if u.get("who") == workload and u.get("port"))

    def depth_of(self, edges: frozenset[Edge]) -> dict[Edge, int]:
        """Depth of every edge in a forest rooted at the entry: the root
        edge is depth 0, an edge whose caller is the callee of a depth-d
        edge is depth d+1 (longest path; the topology is a DAG)."""
        dist: dict[str, int] = {self.entry: 0}
        changed = True
        while changed:
            changed = False
            for caller, callee, _p in edges:
                if caller in dist and dist.get(callee, -1) < dist[caller] + 1:
                    dist[callee] = dist[caller] + 1
                    changed = True
        return {e: dist[e[0]] for e in edges if e[0] in dist}


def _edges(rows: list, llm: str) -> frozenset[Edge]:
    out = set()
    for caller, callee, proto in rows:
        out.add((caller, llm if callee == "llm" else callee, proto))
    return frozenset(out)


def load(path: Path) -> Fleet:
    d = yaml.safe_load(path.read_text(encoding="utf-8"))
    llm = f"{d['llm']['host']}/{d['llm']['model']}"
    services, service_kinds = [], {}
    for sv in d.get("services") or []:
        if isinstance(sv, dict):
            services.append(sv["name"])
            if sv.get("kind"):
                service_kinds[sv["name"]] = sv["kind"]
        else:
            services.append(sv)
    edges = _edges(d["edges"], llm)
    turn = d.get("turn") or {}
    turn_edges = _edges(turn.get("edges", []), llm) if "edges" in turn else edges
    names = {n for e in turn_edges for n in e[:2]}
    turn_entities = frozenset(turn.get("entities") or names)
    must = _edges(turn.get("deterministic") or [], llm) if "deterministic" in turn else frozenset(
        e for e in turn_edges if e[0] == d["entry"])
    gaps = tuple(
        KnownGap(id=g["id"], why=g["why"], issue=g.get("issue"),
                 entities=frozenset(g.get("entities") or []),
                 edges=_edges(g.get("edges") or [], llm),
                 checks=frozenset(g.get("checks") or []),
                 stray_from=frozenset((g.get("stray") or {}).get("from") or []),
                 stray_protocol=(g.get("stray") or {}).get("protocol"))
        for g in d.get("known_gap") or [])
    return Fleet(
        app=d["app"], namespace=d["namespace"], entry=d["entry"],
        agents=frozenset(d["workloads"]["agents"]), tools=frozenset(d["workloads"]["tools"]),
        llm=llm, services=frozenset(services), service_kinds=service_kinds, edges=edges,
        unobservable=tuple(d.get("unobservable") or []),
        turn_entities=turn_entities, turn_edges=turn_edges, known_gaps=gaps, must_edges=must,
        preflight=dict(d.get("preflight") or {}),
    )


def name_of(kind: str, natural_key: str) -> str:
    """The fleet name an entity row stands for. ``kind:ns/self.id`` names
    the workload, ``kind:host[:port]`` the host, ``llm:host:port/model``
    the LLM."""
    key = natural_key.split(":", 1)[1] if ":" in natural_key else natural_key
    if kind == "llm":
        return key
    if "/" in key:
        return key.split("/", 1)[1]
    host = key.split(":")[0]
    return host.split(".")[0] if host.endswith(".svc.cluster.local") else host
