"""Graph-based extractor: spans -> (entities, interactions, interaction_spans, payloads)
plus the intermediate base / colored / entity graphs for evaluation. THROWAWAY.

Algorithm (see docs/adr/0007-p-interactions-graph-algorithm.md):

  Step 1     — build the white base graph from spans + traceparent edges
  Step 2.a   — agentic coloring (Gray nodes/edges, Black boundaries, additive)
  Step 2.b   — combined source-and-target span duplication
  Step 2.c   — synthesize missing peers (Black boundaries with no Black
               edges → synthetic peer with bidirectional Black edges)
               + flag Gray nodes between Black boundaries
  Step 3.a   — connected components over Gray/Black via White+Gray edges
               → entity graph; Black edges → entity edges
  Step 3.b   — merge identical synthetic peers (same source-span identifying
               attributes → one entity)
  Step 3.c   — assign 'unknown' as every entity's display name (richer
               naming is deferred — see ADR-0007)

Then the extractor derives ProtoEntity / ProtoInteraction / ProtoPayload
output rows from the post-3.c entity graph.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import uuid
from collections.abc import Iterable
from typing import Any

from data_governance.retrieval import Span

from .builder import (
    build_base_graph,
    build_entity_graph,
    color_agentic,
    duplicate_combined_nodes,
    flag_between_boundaries,
    merge_synthetic_peers,
    synthesize_missing_peers,
)
from .graph import BaseGraph, EntityGraph


# ---------------------------------------------------------------------------
# Output dataclasses (same shape as the linear-pass prototype for comparison)
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class ProtoEntity:
    id: str
    kind: str
    natural_key: str
    display_name: str
    detected_from: str
    scope_name: str
    anchor_span_id: str | None


@dataclasses.dataclass
class ProtoInteraction:
    id: str
    caller_entity_id: str
    callee_entity_id: str
    started_at: Any
    ended_at: Any
    error: bool | None
    request_payload_hash: str | None
    response_payload_hash: str | None
    summary: str


@dataclasses.dataclass
class ProtoInteractionSpan:
    interaction_id: str
    trace_id: str
    span_id: str
    is_anchor: bool


@dataclasses.dataclass
class ProtoPayload:
    content_hash: str
    content_kind: str
    content: Any
    byte_size: int


@dataclasses.dataclass
class ExtractResult:
    entities: list[ProtoEntity]
    interactions: list[ProtoInteraction]
    interaction_spans: list[ProtoInteractionSpan]
    payloads: list[ProtoPayload]
    notes: list[str]
    # Intermediate graphs for evaluation. The base graph after Step 1 is
    # snapshotted *before* Steps 2.a–2.c mutate it; the colored graph is the
    # post-2.c state (after coloring, combined-span duplication, and
    # synthetic-peer insertion). The entity graph is the post-3.b state
    # (after synthetic-peer merging).
    base_graph: BaseGraph
    colored_graph: BaseGraph
    entity_graph: EntityGraph


# ---------------------------------------------------------------------------
# Payload extraction (unchanged from linear-pass prototype)
# ---------------------------------------------------------------------------


def _attr(span: Span, key: str) -> Any:
    return (span.attributes or {}).get(key)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, default=str).encode("utf-8")


def _hash_payload(canonical: bytes) -> str:
    return hashlib.sha256(canonical).hexdigest()


def _extract_llm_messages(span: Span, prefix: str) -> list[dict[str, Any]] | None:
    attrs = span.attributes or {}
    msgs: dict[int, dict[str, Any]] = {}
    full_prefix = f"{prefix}."
    for key, value in attrs.items():
        if not key.startswith(full_prefix):
            continue
        rest = key[len(full_prefix):]
        parts = rest.split(".", 1)
        if len(parts) != 2 or not parts[0].isdigit():
            continue
        idx = int(parts[0])
        msgs.setdefault(idx, {})[parts[1]] = value
    if not msgs:
        return None
    return [msgs[i] for i in sorted(msgs)]


def _payload_for_llm_call(span: Span) -> tuple[ProtoPayload | None, ProtoPayload | None]:
    req_msgs = _extract_llm_messages(span, "llm.input_messages")
    resp_msgs = _extract_llm_messages(span, "llm.output_messages")
    req = None
    resp = None
    if req_msgs is not None:
        canon = _canonical_bytes({"messages": req_msgs})
        req = ProtoPayload(_hash_payload(canon), "llm_chat_prompt", {"messages": req_msgs}, len(canon))
    if resp_msgs is not None:
        canon = _canonical_bytes({"messages": resp_msgs})
        resp = ProtoPayload(_hash_payload(canon), "llm_completion", {"messages": resp_msgs}, len(canon))
    return req, resp


def _payload_for_tool_call(span: Span) -> tuple[ProtoPayload | None, ProtoPayload | None]:
    iv = _attr(span, "input.value")
    ov = _attr(span, "output.value")
    req = None
    resp = None
    if iv is not None:
        canon = _canonical_bytes(iv)
        req = ProtoPayload(_hash_payload(canon), "tool_call_arguments", iv, len(canon))
    if ov is not None:
        canon = _canonical_bytes(ov)
        resp = ProtoPayload(_hash_payload(canon), "tool_call_result", ov, len(canon))
    return req, resp


# ---------------------------------------------------------------------------
# Entity + interaction derivation from the EntityGraph
# ---------------------------------------------------------------------------


def _kind_from_label(label: str | None) -> str:
    if not label:
        return "service"
    if label.startswith("llm:"):
        return "llm"
    if label.startswith("tool:"):
        return "tool"
    if label.startswith("agent:"):
        return "agent"
    return "service"


def _scopes_for_entity(entity_node, span_by_id: dict[str, Span]) -> str:
    scopes: list[str] = []
    for sid in entity_node.span_ids:
        s = span_by_id.get(sid)
        if s is None:
            continue
        scope = (s.scope or {}).get("name") or ""
        if scope and scope not in scopes:
            scopes.append(scope)
    return ",".join(scopes)


def _derive_entities(
    entity_graph: EntityGraph, span_by_id: dict[str, Span]
) -> list[ProtoEntity]:
    """Build ProtoEntity rows from the post-Step-3.b entity graph.

    Step 3.c per ADR-0007: every entity gets the literal ID 'unknown'.
    Richer naming (hostname / service.name / framework attributes) is
    deferred to the cross-scope enrichment stage. We keep the classifier-
    derived `kind` (llm / tool / agent / service) so payload extraction in
    `_derive_interactions` can still pick the right schema; that's an
    interaction-routing concern, not an entity ID.
    """
    out = []
    for n in entity_graph.nodes:
        kind = _kind_from_label(n.label)
        anchor = n.span_ids[0] if n.span_ids else None
        if n.synthetic:
            detected = "synthetic"
        elif n.span_ids:
            detected = "observed"
        else:
            detected = "inferred stub"
        out.append(ProtoEntity(
            id=n.id,
            kind=kind,
            natural_key="unknown",
            display_name="unknown",
            detected_from=detected,
            scope_name=_scopes_for_entity(n, span_by_id),
            anchor_span_id=anchor,
        ))
    return out


def _derive_interactions(
    entity_graph: EntityGraph,
    spans: list[Span],
    entity_by_id: dict[str, ProtoEntity],
) -> tuple[list[ProtoInteraction], list[ProtoInteractionSpan], list[ProtoPayload], list[str]]:
    notes: list[str] = []
    interactions: list[ProtoInteraction] = []
    ix_spans: list[ProtoInteractionSpan] = []
    payloads: dict[str, ProtoPayload] = {}
    span_by_id: dict[str, Span] = {s.span_id: s for s in spans}

    def _ensure(p: ProtoPayload | None) -> str | None:
        if p is None:
            return None
        if p.content_hash not in payloads:
            payloads[p.content_hash] = p
        return p.content_hash

    for ee in entity_graph.edges:
        caller = entity_by_id.get(ee.from_node_id)
        callee = entity_by_id.get(ee.to_node_id)
        if not caller or not callee:
            notes.append(f"SKIP edge {ee.from_node_id[:8]}→{ee.to_node_id[:8]}: entity missing")
            continue

        edge_spans = [span_by_id[sid] for sid in ee.span_ids if sid in span_by_id]
        if not edge_spans:
            notes.append(f"SKIP edge {caller.display_name}→{callee.display_name}: no evidence spans")
            continue

        anchor_span = min(edge_spans, key=lambda s: s.started_at)
        started_at = min(s.started_at for s in edge_spans)
        ended_at = max((s.ended_at for s in edge_spans if s.ended_at), default=None)
        error = (
            True if any(s.error for s in edge_spans)
            else (False if any(s.error is False for s in edge_spans) else None)
        )

        req_hash = None
        resp_hash = None
        if callee.kind == "llm":
            req_p, resp_p = _payload_for_llm_call(anchor_span)
            req_hash = _ensure(req_p)
            resp_hash = _ensure(resp_p)
        elif callee.kind == "tool":
            req_p, resp_p = _payload_for_tool_call(anchor_span)
            req_hash = _ensure(req_p)
            resp_hash = _ensure(resp_p)

        ix_id = str(uuid.uuid4())
        interactions.append(ProtoInteraction(
            id=ix_id,
            caller_entity_id=caller.id,
            callee_entity_id=callee.id,
            started_at=started_at,
            ended_at=ended_at,
            error=error,
            request_payload_hash=req_hash,
            response_payload_hash=resp_hash,
            summary=f"{caller.display_name} → {callee.display_name}",
        ))

        for span in edge_spans:
            ix_spans.append(ProtoInteractionSpan(
                interaction_id=ix_id,
                trace_id=span.trace_id,
                span_id=span.span_id,
                is_anchor=(span.span_id == anchor_span.span_id),
            ))

    notes.append(
        f"derived {len(interactions)} interactions, "
        f"{len(ix_spans)} evidence rows, {len(payloads)} unique payloads"
    )
    return interactions, ix_spans, list(payloads.values()), notes


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------


def _snapshot(graph: BaseGraph) -> BaseGraph:
    """Deep copy of a BaseGraph (for retaining the post-Step-1 state while
    Step 2 mutates the working copy)."""
    snap = BaseGraph(notes=list(graph.notes))
    for n in graph.nodes:
        snap.nodes.append(dataclasses.replace(n, attributes=dict(n.attributes)))
    for e in graph.edges:
        snap.edges.append(dataclasses.replace(e, colors=set(e.colors)))
    return snap


def extract(spans: Iterable[Span]) -> ExtractResult:
    spans_list = list(spans)
    spans_list.sort(key=lambda s: (s.started_at, s.seq))
    span_by_id = {s.span_id: s for s in spans_list}

    # Step 1 — base graph
    working = build_base_graph(spans_list)
    base_snapshot = _snapshot(working)

    # Step 2.a → 2.c — coloring on a working copy
    color_agentic(working, span_by_id)
    duplicate_combined_nodes(working)
    synthesize_missing_peers(working)
    flag_between_boundaries(working)
    colored_snapshot = _snapshot(working)

    # Step 3.a — entity graph
    entity_graph = build_entity_graph(working)

    # Step 3.b — merge identical synthetic peers
    n_merged = merge_synthetic_peers(entity_graph)

    # Step 3.c is applied during output derivation (entities get 'unknown'
    # display; the underlying classifier label is kept on the EntityNode for
    # the future enrichment stage).
    entities = _derive_entities(entity_graph, span_by_id)
    entity_by_id = {e.id: e for e in entities}
    interactions, ix_spans, payloads, ix_notes = _derive_interactions(
        entity_graph, spans_list, entity_by_id
    )

    notes: list[str] = []
    notes.append(f"base graph: {len(base_snapshot.nodes)} nodes, {len(base_snapshot.edges)} edges")
    n_gray = sum(1 for n in colored_snapshot.nodes if n.color == "gray")
    n_black = sum(1 for n in colored_snapshot.nodes if n.color == "black")
    n_flag = sum(1 for n in colored_snapshot.nodes if n.flagged)
    n_dup = sum(1 for n in colored_snapshot.nodes if n.is_target_duplicate)
    n_synth = sum(1 for n in colored_snapshot.nodes if n.is_synthetic)
    notes.append(
        f"colored graph: {n_gray} gray, {n_black} black "
        f"({n_dup} target duplicates, {n_synth} synthetic peers), "
        f"{n_flag} between-boundary flags"
    )
    notes.append(
        f"entity graph: {len(entity_graph.nodes)} entities, {len(entity_graph.edges)} edges "
        f"(Step 3.b merged {n_merged} synthetic peers)"
    )
    notes.extend(ix_notes)

    return ExtractResult(
        entities=entities,
        interactions=interactions,
        interaction_spans=ix_spans,
        payloads=payloads,
        notes=notes,
        base_graph=base_snapshot,
        colored_graph=colored_snapshot,
        entity_graph=entity_graph,
    )
