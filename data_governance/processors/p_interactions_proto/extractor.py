"""Graph-based extractor: spans -> (entities, interactions, interaction_spans, payloads)
plus the intermediate base / colored / entity graphs for evaluation. THROWAWAY.

Algorithm (see docs/adr/0007-p-interactions-graph-algorithm.md):

  Step 1     — build the white base graph from spans + traceparent edges
  Step 2.b   — agentic coloring (Blue nodes/edges, boundary marking, additive)
  Step 2.c   — combined source-and-target span duplication; tool nodes inferred
               from LLM `tool_calls`; synthesize missing peers (boundaries with
               no interaction edges → inferred peer with bidirectional
               interaction edges) + flag Blue nodes between boundaries
  Step 2.d   — merge identical interactions on the execution graph (node merge
               then edge merge)
  Step 3.a   — create the entity-graph *nodes*: connected components over Blue
               via non-interaction edges → groups (structural), then combine
               same-entity groups (semantic). Each node is keyed/named from its
               subgraph: service.name, else the natural-key suffix, else
               'unknown' (hostname-based naming is deferred — see ADR-0007)
  Step 3.b   — create the entity-graph *edges*: one interaction per Teal
               transport chain between two Blue components (per-chain — distinct
               calls stay distinct)

Then the extractor derives ProtoEntity / ProtoInteraction / ProtoPayload
output rows from the entity graph.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import uuid
from collections.abc import Iterable
from typing import Any

from data_governance.retrieval import Span

from .adapters import extract_facts, payload_shapes_for_facts
from .builder import (
    _node_is_boundary,
    build_base_graph,
    build_entity_graph,
    color_agentic,
    color_transport,
    combine_identical_entities,
    duplicate_combined_nodes,
    flag_between_boundaries,
    infer_agent_from_bare_leaf_llms,
    infer_tool_calls_from_attributes,
    merge_identical_interactions,
    synthesize_missing_peers,
)
from .graph import BaseGraph, EntityGraph


# ---------------------------------------------------------------------------
# Output dataclasses (same shape as the linear-pass prototype for comparison)
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class ProtoEntity:
    id: str
    # natural_key is the classifier-derived label with a typed prefix:
    # `llm:<model>` | `tool:<name>` | `agent:<name>`. The prefix doubles
    # as the coarse kind (consumers split on `:` when they need it). Per
    # ADR-0007 "Natural-key prefixes (an implementation construct, not a spec
    # vocabulary)", this format is an implementation decision, not spec-derived.
    # There is no separate `kind` column.
    natural_key: str
    display_name: str
    detected_from: str
    scope_name: str
    anchor_span_id: str | None
    # True iff every base-graph node absorbed into this entity was an inferred
    # peer (e.g. a Step 2.c unobserved-peer stub). Per ADR-0007 this is the
    # sole sanctioned signal for "inferred entity" — the UI must filter on this
    # boolean, never on the label/natural_key string.
    inferred: bool = False


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
    # Intra-turn ordering tiebreak (ADR-0007 "Inferred interaction ordering").
    # Interactions derived from one span share `started_at`; consumers sort by
    # `(started_at, order)` so the spec order (input tools → LLM call/response
    # → output tools; call before response) survives. Copied from
    # `EntityEdge.order`.
    order: int = 0


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
    # snapshotted *before* Step 2 mutates it; the colored graph is the
    # post-Step-2 state (after coloring, inference, and the Step 2.d merge). The
    # entity graph is the post-Step-3 state.
    base_graph: BaseGraph
    colored_graph: BaseGraph
    entity_graph: EntityGraph


# ---------------------------------------------------------------------------
# Payload extraction (unchanged from linear-pass prototype)
# ---------------------------------------------------------------------------


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, default=str).encode("utf-8")


def _hash_payload(canonical: bytes) -> str:
    return hashlib.sha256(canonical).hexdigest()


# Payload-shape selection (LLM messages vs. tool/agent input.value/output.value)
# lives in `adapters.payload_shapes_for_facts`, keyed off `SpanFacts.kind`.
# This module only owns hashing and `ProtoPayload` construction.


def _proto_payload(shape: tuple[str, Any] | None) -> ProtoPayload | None:
    if shape is None:
        return None
    content_kind, content = shape
    canon = _canonical_bytes(content)
    return ProtoPayload(_hash_payload(canon), content_kind, content, len(canon))


# ---------------------------------------------------------------------------
# Entity + interaction derivation from the EntityGraph
# ---------------------------------------------------------------------------


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


def _entity_display_name(
    entity_node, natural_key: str, span_by_id: dict[str, Span]
) -> str:
    """Step 3.a entity key/naming — derive a display key for an entity from its subgraph.

    Precedence (ADR-0007 Step 3.a entity key, as scoped today):
      1. **service.name** of any contributing span — the OTel resource service
         that emitted the agentic spans (`dl-demo-travel-advisor`,
         `patent-assistant`). This is the typed `Span.service_name` field, not
         a raw attribute read, so it stays within the adapter-layer isolation
         rule.
      2. the model / tool / agent **name** parsed from the `natural_key`
         suffix (`llm:gpt-4o` → `gpt-4o`, `tool:get_weather` → `get_weather`).
      3. `"unknown"` when neither is available.

    The spec's preferred identifier — a hostname — lives on non-agentic
    (httpx/botocore) spans that are not part of the entity-forming subgraph at
    this stage, so it is unreachable until the cross-scope enrichment stage
    runs; service.name is the best identifier available now. See ADR-0007
    "Deferred to later stages → Hostname-based entity naming".
    """
    for sid in entity_node.span_ids:
        s = span_by_id.get(sid)
        if s is not None and s.service_name:
            return s.service_name
    if natural_key and natural_key != "unknown" and ":" in natural_key:
        suffix = natural_key.split(":", 1)[1].strip()
        if suffix:
            return suffix
    return "unknown"


def _derive_entities(
    entity_graph: EntityGraph, span_by_id: dict[str, Span]
) -> list[ProtoEntity]:
    """Build ProtoEntity rows from the entity graph.

    Step 3.a entity key per ADR-0007: each entity is named from its subgraph —
    service.name, else the natural-key suffix, else 'unknown' (see
    `_entity_display_name`). Hostname-based naming is deferred to the
    cross-scope enrichment stage (the host-bearing spans are not yet in the
    entity subgraph).

    The classifier-derived natural key (`tool:<name>`, `llm:<model>`,
    `agent:<name>`) is propagated as-is so prototype consumers can tell
    entities apart, and so payload-routing in `_derive_interactions` can
    split on the prefix.
    """
    out = []
    for n in entity_graph.nodes:
        anchor = n.span_ids[0] if n.span_ids else None
        if n.inferred:
            detected = "inferred"
        elif n.span_ids:
            detected = "observed"
        else:
            detected = "inferred stub"
        natural_key = n.label or "unknown"
        out.append(ProtoEntity(
            id=n.id,
            natural_key=natural_key,
            display_name=_entity_display_name(n, natural_key, span_by_id),
            detected_from=detected,
            scope_name=_scopes_for_entity(n, span_by_id),
            anchor_span_id=anchor,
            inferred=n.inferred,
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

        # The anchor is the edge's *source* span (stamped first on the edge by
        # build_entity_graph) — the span that originates this specific call.
        # **Timing follows the anchor**, not an aggregate over `edge_spans`:
        # after Step 2.d merges a repeatedly-called peer into one node, an
        # interaction's pooled spans can include spans from *other* turns (the
        # merged peer carries an earlier turn's span), so `min(started_at)` would
        # drag every turn's interaction to the earliest turn's time. The anchor
        # is the single source of truth for the interaction's identity, so its
        # times define the interaction's time and its (started_at, order) sort
        # position. `error`, by contrast, still considers the whole call/response
        # pair — either side erroring marks the interaction errored.
        anchor_span = edge_spans[0]
        started_at = anchor_span.started_at
        ended_at = anchor_span.ended_at
        error = (
            True if any(s.error for s in edge_spans)
            else (False if any(s.error is False for s in edge_spans) else None)
        )

        # Payload shape is chosen by the adapter from `SpanFacts.kind` — the
        # extractor neither inspects raw attributes nor branches on the
        # natural-key prefix string.
        req_shape, resp_shape = payload_shapes_for_facts(extract_facts(anchor_span))
        # Step 2.c case 3: a tool inferred from an LLM span's tool_calls carries
        # its arguments on the entity edge (the anchor span is the LLM span, so
        # deriving from its facts would yield the LLM completion, not the tool
        # arguments). Prefer the edge-carried request payload when present.
        if ee.req_payload is not None:
            req_shape = ee.req_payload
        req_hash = _ensure(_proto_payload(req_shape))
        resp_hash = _ensure(_proto_payload(resp_shape))

        # The summary uses the natural_key (the typed classifier label), which
        # is the most distinguishing identifier; the display_name (Step 3.a entity key) is
        # the friendlier service/entity name surfaced separately on the entity.
        caller_label = caller.natural_key or caller.display_name
        callee_label = callee.natural_key or callee.display_name
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
            summary=f"{caller_label} → {callee_label}",
            order=ee.order,
        ))

        # One evidence row per interaction: the anchor span. (error / timing /
        # payload above are still computed over every span the edge pooled, so
        # a callee span's error signal is not lost.)
        ix_spans.append(ProtoInteractionSpan(
            interaction_id=ix_id,
            trace_id=anchor_span.trace_id,
            span_id=anchor_span.span_id,
            is_anchor=True,
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

    # Step 2 → 3 — coloring + inference on a working copy
    # Step 2.a — transport scope (Teal). Runs before agentic coloring; the two
    # scopes are disjoint (transport scopes are not agentic), so order only
    # matters for readability.
    color_transport(working)
    color_agentic(working, span_by_id)
    duplicate_combined_nodes(working, span_by_id)
    # Step 2.c case 3 — infer tool nodes from LLM-output tool_calls. Must run
    # AFTER color_agentic (so the new Blue fold edge isn't seen by the
    # interaction edge-promotion loop) and BEFORE synthesize_missing_peers (so
    # the inferred tool-call/tool nodes already have their interaction edges and
    # aren't themselves stubbed, while the LLM span node — still edgeless — still
    # gets its llm peer).
    infer_tool_calls_from_attributes(working, span_by_id)
    synthesize_missing_peers(working, span_by_id)
    # Step 2.c case 4 — infer an agent node when the framework emits only bare
    # leaf LLM spans under a transport parent (no agent/run wrapper). Runs after
    # the other Step 2.c inference so the LLM spans already carry their servers.
    infer_agent_from_bare_leaf_llms(working, span_by_id)
    # Step 2.d — single node-and-edge merge on the execution-flow graph, before
    # the Step 3.a entity grouping: collapse same-entity nodes (inferred/inferred, inferred/observed,
    # observed/observed) then same-interaction edges. Runs BEFORE the
    # snapshot so the colored execution graph reflects every merge.
    n_nodes_merged, n_edges_merged = merge_identical_interactions(
        working, span_by_id
    )
    flag_between_boundaries(working, span_by_id)
    colored_snapshot = _snapshot(working)

    # Step 3.a (structural grouping) — connected Blue components → entity nodes
    # (groups); Step 3.b — each dropped Teal transport chain → a pair of entity
    # edges (one interaction per chain).
    entity_graph = build_entity_graph(working, span_by_id)
    # Step 3.a (semantic combine) — combine entity nodes representing the same
    # entity (inferred peers AND observed entities sharing a typed key + kind +
    # scope), maintaining all edges. `span_by_id` supplies each entity's scope
    # for the combine key.
    n_entities_merged = combine_identical_entities(entity_graph, span_by_id)

    # Entity naming is applied during output derivation (entities are named from
    # service.name / natural-key suffix; the classifier label is kept on the
    # EntityNode for the future enrichment stage).
    entities = _derive_entities(entity_graph, span_by_id)
    entity_by_id = {e.id: e for e in entities}
    interactions, ix_spans, payloads, ix_notes = _derive_interactions(
        entity_graph, spans_list, entity_by_id
    )

    notes: list[str] = []
    notes.append(f"base graph: {len(base_snapshot.nodes)} nodes, {len(base_snapshot.edges)} edges")
    n_blue = sum(1 for n in colored_snapshot.nodes if n.color == "blue")
    n_teal = sum(1 for n in colored_snapshot.nodes if n.color == "teal")
    n_boundary = sum(
        1 for n in colored_snapshot.nodes if _node_is_boundary(n, span_by_id)
    )
    n_flag = sum(1 for n in colored_snapshot.nodes if n.flagged)
    n_dup = sum(1 for n in colored_snapshot.nodes if n.is_target_duplicate)
    n_inferred = sum(1 for n in colored_snapshot.nodes if n.is_inferred)
    notes.append(
        f"colored graph: {n_blue} blue, {n_teal} teal, {n_boundary} boundaries "
        f"({n_dup} target duplicates, {n_inferred} inferred peers), "
        f"{n_flag} between-boundary flags "
        f"(Step 2.d merged {n_nodes_merged} same-entity nodes, "
        f"{n_edges_merged} same-interaction edges)"
    )
    notes.append(
        f"entity graph: {len(entity_graph.nodes)} entities, {len(entity_graph.edges)} edges "
        f"(Step 3.a combined {n_entities_merged} same-entity nodes)"
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
