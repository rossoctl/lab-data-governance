"""Lineage REST contract (runs / hops / edges), served off the materialized graph.

These queries reproduce the shapes of arielf's standalone ``lineage_service``
(``RunResponse`` / ``HopResponse`` / ``CommonEdgeResponse`` / ``PrincipalPathResponse``)
so the Kagenti UI's **Data Lineage** page can be repointed at the DG pod without
any UI change. The data is the sidecar lineage graph the ``graph_builder``
materialized into ``entities`` + ``edges`` (``edge_kind`` is the hop kind), joined
back to ``spans`` for timing and the wire-body ``attributes`` (ADR-0006).

Identity mapping (the UI types ``run_id``/``hop_id`` as opaque strings):
``run_id`` = ``trace_id``, ``hop_id`` = ``span_id``. A *hop* is one sidecar
lineage edge; a *run* is one trace with at least one hop.

The patent_search demo has no authenticated inbound principal, so ``principal_id``
is a placeholder and ``username`` is null — the same data the sidecar captured.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

from data_governance import db

__all__ = [
    "list_runs",
    "get_trajectory",
    "get_run_graph",
    "get_run_sequence",
    "get_run_tree",
    "get_data_graph",
    "list_common_edges",
    "list_principal_paths",
    "list_principal_agents",
    "autocomplete_agents",
    "autocomplete_tools",
]

# The hop kinds the sidecar stamps as ``edge_kind`` on lineage edges.
_HOP_KINDS = ("principal_to_agent", "agent_to_agent", "agent_to_tool", "agent_to_llm")
_HOP_KINDS_SQL = "(" + ",".join("%s" for _ in _HOP_KINDS) + ")"

# No authenticated principal in this demo; surface a stable placeholder rather
# than NULL so the UI's principal columns render.
_PLACEHOLDER_PRINCIPAL = "anonymous"

_HARD_LIMIT = 500


def _clamp(limit: int) -> int:
    return max(1, min(int(limit), _HARD_LIMIT))


def _ms(started: dt.datetime | None, ended: dt.datetime | None) -> int | None:
    if started is None or ended is None:
        return None
    return int((ended - started).total_seconds() * 1000)


def _attrs(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    return {}


def list_runs(*, limit: int = 50) -> list[dict[str, Any]]:
    """One row per trace that has at least one lineage hop. Newest first."""
    sql = f"""
        SELECT s.trace_id,
               MIN(s.started_at)                          AS started_at,
               MAX(COALESCE(s.ended_at, s.started_at))    AS ended_at,
               COUNT(*)                                   AS hop_count,
               MAX(s.attributes->>'enduser.id')           AS username
        FROM edges e
        JOIN spans s ON s.trace_id = e.trace_id AND s.span_id = e.span_id
        WHERE e.edge_kind IN {_HOP_KINDS_SQL}
        GROUP BY s.trace_id
        ORDER BY started_at DESC
        LIMIT %s
    """
    params = [*_HOP_KINDS, _clamp(limit)]
    with db.transaction() as tx:
        rows = tx.fetch_all(sql, params)
    out: list[dict[str, Any]] = []
    for trace_id, started_at, ended_at, hop_count, username in rows:
        out.append(
            {
                "run_id": trace_id,
                "trace_id": trace_id,
                "principal_id": username or _PLACEHOLDER_PRINCIPAL,
                "username": username,
                "started_at": started_at,
                "ended_at": ended_at,
                "hop_count": hop_count,
            }
        )
    return out


def get_trajectory(run_id: str) -> list[dict[str, Any]]:
    """The ordered hops for one run (``run_id`` == ``trace_id``).

    One row per sidecar lineage edge, in ``started_at`` order. Unlike arielf's
    ``lineage_service`` we do **not** collapse on a 1-second bucket: DG stores
    exactly one sidecar span per tool call (no A2A client/server span pair to
    merge), so bucketing would wrongly fold legitimate repeated calls — e.g. the
    two ``read_file`` / two ``web_search`` hops of the prior-art turn — into one.
    """
    sql = f"""
        SELECT s.span_id, s.parent_id, s.started_at, s.ended_at, s.attributes,
               e.edge_kind, fe.service_name AS source_id, te.service_name AS target_id
        FROM edges e
        JOIN spans s ON s.trace_id = e.trace_id AND s.span_id = e.span_id
        JOIN entities te ON te.entity_id = e.to_entity
        LEFT JOIN entities fe ON fe.entity_id = e.from_entity
        WHERE e.trace_id = %s AND e.edge_kind IN {_HOP_KINDS_SQL}
        ORDER BY s.started_at, s.span_id
    """
    params = [run_id, *_HOP_KINDS]
    with db.transaction() as tx:
        rows = tx.fetch_all(sql, params)
    out: list[dict[str, Any]] = []
    for span_id, parent_id, started_at, ended_at, attrs, edge_kind, source_id, target_id in rows:
        out.append(
            {
                "hop_id": span_id,
                "run_id": run_id,
                "span_id": span_id,
                "parent_span_id": parent_id or None,
                "source_id": source_id,
                "target_id": target_id,
                "hop_kind": edge_kind,
                "started_at": started_at,
                "duration_ms": _ms(started_at, ended_at),
                "attrs": _attrs(attrs),
            }
        )
    # Surface in real chronological order for the sequence/hop-log views.
    out.sort(key=lambda h: h["started_at"])
    return out


# Node-role inference for the per-run views: the caller of any agent_to_* hop is
# an AGENT (a PRINCIPAL for principal_to_agent); the callee's kind follows the hop.
_SRC_ROLE = {"principal_to_agent": "PRINCIPAL"}
_DST_ROLE = {
    "principal_to_agent": "AGENT",
    "agent_to_agent": "AGENT",
    "agent_to_tool": "TOOL",
    "agent_to_llm": "LLM",
    "agent_to_service": "SERVICE",
}


def _hop_label(hop: dict[str, Any]) -> str:
    """Short human label for a hop, used on sequence-diagram messages."""
    a = hop.get("attrs") or {}
    kind = hop["hop_kind"]
    if kind == "agent_to_llm":
        pin = a.get("llm.token_count.prompt")
        pout = a.get("llm.token_count.completion")
        toks = f", {pin}->{pout} tok" if pin is not None and pout is not None else ""
        dur = f"{hop['duration_ms']}ms" if hop.get("duration_ms") is not None else ""
        return f"chat ({dur}{toks})".replace("()", "").strip()
    if kind == "agent_to_tool":
        method = a.get("mcp.method") or "call"
        dur = f" {hop['duration_ms']}ms" if hop.get("duration_ms") is not None else ""
        return f"{method}{dur}"
    dur = f" {hop['duration_ms']}ms" if hop.get("duration_ms") is not None else ""
    return f"{kind}{dur}"


def get_run_graph(run_id: str) -> dict[str, Any]:
    """Entity graph for one run: distinct nodes + aggregated edges.

    The 'Entities' tab. Processing lives here (DG) — the UI only lays the
    returned nodes/edges out with React Flow.
    """
    hops = get_trajectory(run_id)
    nodes: dict[str, dict[str, Any]] = {}
    edges: dict[tuple[str, str, str], dict[str, Any]] = {}

    def _add_node(name: str | None, role: str) -> None:
        if not name or name in nodes:
            if name and role != "AGENT":  # let a more specific role win over a default
                nodes[name]["kind"] = nodes[name].get("kind") or role
            return
        nodes[name] = {"id": name, "label": name, "kind": role}

    for h in hops:
        kind = h["hop_kind"]
        src, dst = h.get("source_id"), h["target_id"]
        _add_node(src, _SRC_ROLE.get(kind, "AGENT"))
        _add_node(dst, _DST_ROLE.get(kind, "SERVICE"))
        if not src:
            continue
        key = (src, dst, kind)
        if key in edges:
            edges[key]["count"] += 1
        else:
            edges[key] = {"source": src, "target": dst, "kind": kind, "count": 1}

    return {"nodes": list(nodes.values()), "edges": list(edges.values())}


def get_run_sequence(run_id: str) -> dict[str, Any]:
    """Sequence view for one run: ordered participants + messages (semantic).

    The 'Sequence Diagram' tab. DG owns all the processing — ordering,
    participant extraction, and the per-message label — and returns a
    view-agnostic model. The UI maps it onto its diagram renderer (mermaid); DG
    stays free of any UI-rendering syntax (clean layering).
    """
    hops = get_trajectory(run_id)
    participants: list[str] = []
    messages: list[dict[str, Any]] = []
    for idx, h in enumerate(hops):
        src = h.get("source_id") or "principal"
        dst = h["target_id"]
        for name in (src, dst):
            if name not in participants:
                participants.append(name)
        messages.append(
            {
                "seq": idx + 1,
                "from": src,
                "to": dst,
                "hop_kind": h["hop_kind"],
                "label": _hop_label(h),
                "duration_ms": h.get("duration_ms"),
                "started_at": h["started_at"],
            }
        )
    return {"participants": participants, "messages": messages}


# ── Tree View (execution forest) — derived from the same materialized hops ────
# Datastore overlay rules ported from forest_scenario_overlay.js (ADR-0010):
# a tool maps to a store (D database / F filesystem·name); web_search → none.
_STORE_RULES = {
    "read_patent": {"base": "D", "kind": "database", "name_keys": []},
    "read_file": {"base": "F", "kind": "filesystem", "name_keys": ["name", "path", "file", "key", "filename"]},
    "write_file": {"base": "F", "kind": "filesystem", "name_keys": ["name", "path", "file", "key", "filename"]},
}


def _norm_tool(target: str | None) -> str:
    t = (target or "").lower().replace("-", "_").replace(" ", "_")
    return t[:-4] if t.endswith("_mcp") else t


def _store_for(hop: dict[str, Any]) -> dict[str, Any] | None:
    rule = _STORE_RULES.get(_norm_tool(hop.get("target_id")))
    if not rule:
        return None  # web_search etc. — external egress, no store node
    suffix = ""
    raw = (hop.get("attrs") or {}).get("input.value")
    if rule["name_keys"] and raw:
        try:
            args = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(args, dict):
                for k in rule["name_keys"]:
                    if args.get(k):
                        suffix = str(args[k])
                        break
        except (json.JSONDecodeError, TypeError):
            pass
    sid = rule["base"] + (f"/{suffix}" if suffix else "")
    label = rule["base"] + (f" · {suffix}" if suffix else "")
    return {"id": sid, "label": label, "kind": rule["kind"]}


def get_run_tree(run_id: str) -> dict[str, Any]:
    """Execution-forest tree for one run: U → A → {L, tools} + datastore overlay.

    The 'Tree View' tab. Built server-side from the materialized hops (which
    already deduped sidecar vs in-process), so it is the forest the DG-pod UI
    draws — without re-running forest_logic.js. The UI only renders the tree.
    """
    hops = get_trajectory(run_id)
    if not hops:
        return {"user": None, "agent": None, "children": [], "stores": [], "links": []}
    agent_name = next((h["source_id"] for h in hops if h.get("source_id")), "agent")
    children: list[dict[str, Any]] = []
    stores: dict[str, dict[str, Any]] = {}
    links: list[dict[str, Any]] = []
    for i, h in enumerate(hops):
        cid = f"c{i}"
        if h["hop_kind"] == "agent_to_llm":
            children.append({"id": cid, "role": "L", "label": "LLM",
                             "target": h["target_id"], "detail": _hop_label(h)})
        else:
            children.append({"id": cid, "role": "tool", "label": h["target_id"],
                             "target": h["target_id"], "detail": _hop_label(h)})
            st = _store_for(h)
            if st:
                stores[st["id"]] = st
                op = "read" if "read" in _norm_tool(h.get("target_id")) else "write"
                links.append({"child": cid, "store": st["id"], "op": op})
    return {
        "user": {"id": "U", "label": "user"},
        "agent": {"id": "A", "label": agent_name},
        "children": children,
        "stores": list(stores.values()),
        "links": links,
    }


# ── Data Graph — the MOCKED lineage view (ADR-0012), ported verbatim from ──────
# data_graph_mock.js. Hardcoded / identical for every run; the UI labels it
# MOCKED. A real, computed data graph (from edge_annotations taint propagation)
# is separate future work.
_DATA_GRAPH_MOCK: dict[str, Any] = {
    "mocked": True,
    "title": "Desired data graph — where the patent data actually went",
    "columns": 5,
    "boundaryAfter": 2,
    "boundaryLabel": "fresh trace · session + store gap",
    "nodes": [
        {"id": "D", "col": 0, "zone": "T1", "type": "datastore", "label": "patent DB · Tier-1", "verdict": "confidential",
         "why": "The confidential patent database (Tier-1). SOURCE FLOOR: anything read from D starts confidential regardless of its bytes."},
        {"id": "A1", "col": 1, "zone": "T1", "type": "agent", "label": "patent-agent", "verdict": "confidential",
         "why": "The agent reads d (the patent text) from D and hands it to its LLM. Holds confidential data."},
        {"id": "L", "lane": "top", "over": "A1", "zone": "T1", "type": "transform", "label": "LLM", "verdict": "confidential",
         "why": "The TRANSFORMATION node (the LLM): turns d into d1. The agent then writes two parts of d1 (d2, d3) that diverge in classification."},
        {"id": "F1", "col": 2, "zone": "F", "type": "file", "label": "F1 · keywords.txt", "sub": "MinIO object", "verdict": "clean",
         "why": "CLEAN. By ancestry d2 ⟵ d1 ⟵ d ⟵ D, yet keywords.txt carries NO patent essence, so it is DECLASSIFIED. The PRECISION win."},
        {"id": "F2", "col": 2, "zone": "F", "type": "file", "label": "F2 · summary.txt", "sub": "MinIO object", "verdict": "confidential",
         "why": "CONFIDENTIAL. summary.txt carries the patent essence (and inherits D's floor)."},
        {"id": "A2", "col": 3, "zone": "T2", "type": "agent", "label": "patent-agent · fresh trace", "verdict": "mixed",
         "why": "T2 is a NEW A2A session = a NEW trace. Nothing in T2's trace points back to T1 — the gap a session-scoped tracer cannot bridge."},
        {"id": "W1", "col": 4, "zone": "T2", "type": "external", "label": "web_search", "sub": "egress", "verdict": "clean",
         "why": "CLEAN. Approved egress — sharing d2 (keywords.txt) is allowed."},
        {"id": "W2", "col": 4, "zone": "T2", "type": "external", "label": "web_search", "sub": "egress", "verdict": "confidential", "leak": True,
         "why": "THE VIOLATION. Tracing is BLIND (fresh trace); lineage matches DATA IDENTITY d3 ⟵ d1 ⟵ d ⟵ D back to the confidential DB. The RECALL win."},
    ],
    "edges": [
        {"from": "D", "to": "A1", "data": "d", "verdict": "confidential"},
        {"from": "A1", "to": "L", "data": "d", "verdict": "confidential", "vert": True},
        {"from": "L", "to": "A1", "data": "d1", "verdict": "confidential", "vert": True},
        {"from": "A1", "to": "F1", "data": "d2", "verdict": "clean", "fork": True},
        {"from": "A1", "to": "F2", "data": "d3", "verdict": "confidential", "fork": True},
        {"from": "F1", "to": "A2", "data": "d2", "verdict": "clean"},
        {"from": "F2", "to": "A2", "data": "d3", "verdict": "confidential"},
        {"from": "A2", "to": "W1", "data": "d2", "verdict": "clean"},
        {"from": "A2", "to": "W2", "data": "d3", "verdict": "confidential", "leak": True},
    ],
    "legend": [
        {"cls": "clean", "label": "clean — declassified / approved (true-negative)"},
        {"cls": "conf", "label": "confidential ‼ — carries the patent essence (source floor)"},
    ],
    "notes": [
        "MOCKED this round — drawn by hand from scenario.md §8, not computed from spans.",
        "F1 / F2 are one node each: the same bytes written in T1 and read in T2.",
        "The real builder would populate DG's edge_annotations (derived / derived_from) from captured spans + a content classifier.",
        "Precision (T1) and recall (T2) are DISTINCT wins. The airtight differentiation is cross-session recall.",
    ],
}


def get_data_graph() -> dict[str, Any]:
    """The mocked lineage data graph (ADR-0012), run-agnostic."""
    return _DATA_GRAPH_MOCK


def list_common_edges(*, hop_kind: str = "agent_to_agent", limit: int = 50) -> list[dict[str, Any]]:
    """Aggregated source->target edges of one hop kind, most frequent first."""
    sql = """
        SELECT fe.service_name AS source_id,
               te.service_name AS target_id,
               COUNT(*)                   AS total_count,
               COUNT(DISTINCT e.trace_id) AS principal_count,
               MIN(s.started_at)          AS first_seen,
               MAX(s.started_at)          AS last_seen
        FROM edges e
        JOIN spans s ON s.trace_id = e.trace_id AND s.span_id = e.span_id
        JOIN entities te ON te.entity_id = e.to_entity
        LEFT JOIN entities fe ON fe.entity_id = e.from_entity
        WHERE e.edge_kind = %s
        GROUP BY fe.service_name, te.service_name
        ORDER BY total_count DESC
        LIMIT %s
    """
    with db.transaction() as tx:
        rows = tx.fetch_all(sql, [hop_kind, _clamp(limit)])
    return [
        {
            "source_id": source_id,
            "target_id": target_id,
            "total_count": total_count,
            "principal_count": principal_count,
            "first_seen": first_seen,
            "last_seen": last_seen,
        }
        for source_id, target_id, total_count, principal_count, first_seen, last_seen in rows
    ]


def list_principal_paths(*, agent: str, tool: str) -> list[dict[str, Any]]:
    """Which principals drove a given agent->tool hop (placeholder principal here)."""
    sql = """
        SELECT COUNT(*)          AS count,
               MIN(s.started_at) AS first_seen,
               MAX(s.started_at) AS last_seen
        FROM edges e
        JOIN spans s ON s.trace_id = e.trace_id AND s.span_id = e.span_id
        JOIN entities te ON te.entity_id = e.to_entity
        LEFT JOIN entities fe ON fe.entity_id = e.from_entity
        WHERE e.edge_kind = 'agent_to_tool'
          AND fe.service_name = %s AND te.service_name = %s
    """
    with db.transaction() as tx:
        rows = tx.fetch_all(sql, [agent, tool])
    out: list[dict[str, Any]] = []
    for count, first_seen, last_seen in rows:
        if not count:
            continue
        out.append(
            {
                "principal_id": _PLACEHOLDER_PRINCIPAL,
                "count": count,
                "first_seen": first_seen,
                "last_seen": last_seen,
            }
        )
    return out


def list_principal_agents(principal_id: str) -> list[str]:
    """Agents reached by a principal (delegation targets). Empty without principals."""
    sql = """
        SELECT DISTINCT te.service_name
        FROM edges e
        JOIN entities te ON te.entity_id = e.to_entity
        WHERE e.edge_kind IN ('principal_to_agent', 'agent_to_agent')
        ORDER BY te.service_name
    """
    with db.transaction() as tx:
        rows = tx.fetch_all(sql, [])
    return [r[0] for r in rows if r[0]]


def autocomplete_agents(*, prefix: str = "", limit: int = 20) -> list[str]:
    sql = """
        SELECT DISTINCT e2.service_name FROM (
            SELECT te.service_name FROM edges e
              JOIN entities te ON te.entity_id = e.to_entity
              WHERE e.edge_kind IN ('principal_to_agent', 'agent_to_agent')
            UNION
            SELECT fe.service_name FROM edges e
              JOIN entities fe ON fe.entity_id = e.from_entity
              WHERE e.edge_kind IN ('agent_to_agent', 'agent_to_tool', 'agent_to_llm')
        ) e2
        WHERE e2.service_name ILIKE %s
        ORDER BY 1 LIMIT %s
    """
    with db.transaction() as tx:
        rows = tx.fetch_all(sql, [f"{prefix}%", _clamp(limit)])
    return [r[0] for r in rows if r[0]]


def autocomplete_tools(*, prefix: str = "", limit: int = 20) -> list[str]:
    sql = """
        SELECT DISTINCT te.service_name
        FROM edges e
        JOIN entities te ON te.entity_id = e.to_entity
        WHERE e.edge_kind = 'agent_to_tool' AND te.service_name ILIKE %s
        ORDER BY te.service_name LIMIT %s
    """
    with db.transaction() as tx:
        rows = tx.fetch_all(sql, [f"{prefix}%", _clamp(limit)])
    return [r[0] for r in rows if r[0]]
