"""Adapt the graph algorithm's ``ExtractResult`` to the production schema.

The batch graph algorithm (``processors.p_interactions_proto``) derives the same
**Entities** and **Interactions** as the streaming algorithm, but emits its own
prototype-shaped rows (coarse ``natural_key`` labels with no ``kind`` column,
run-unstable ``uuid4`` ids, no ``seq``). This module maps one ``ExtractResult``
onto the *production* row model so the graph algorithm can write the SAME tables
(``entities`` / ``interactions`` / ``interaction_spans`` / ``interaction_payloads``)
through the SAME write path — ``state.flush``.

Single-source-of-truth choices (CLAUDE.md):

- **Identity is re-derived, not translated.** For each entity we run the streaming
  algorithm's own ``caller_inference`` builders over the entity's real spans, so
  the resulting ``(kind, natural_key, project_name, display_name)`` is byte-identical
  to what the streaming algorithm produces. Because entity ids are ``uuid5`` of the
  ``natural_key`` (``procedure._entity_id``), the SAME logical entity collapses onto
  the SAME ``entities`` row regardless of which algorithm wrote it. Translating the
  proto's coarse ``llm:<model>`` / ``tool:<name>`` label is deliberately rejected:
  it cannot recover the host-qualified LLM key, the ``(project,canonical)`` tuple
  keys, or the user/client/service distinctions, and would mint a parallel,
  non-colliding entity universe that fails the ``entity_kind`` ENUM.
- **Ids are deterministic.** Entity ``id = procedure._entity_id(natural_key)``;
  interaction ``id = procedure._interaction_id(trace_id, anchor_span_id)`` where
  the anchor is the edge's source span (``EntityEdge.span_ids[0]``).
- **The write path is reused, not duplicated.** ``adapt`` returns a duck-typed
  ``ProductionRows`` holder exposing exactly the six attributes ``state.flush``
  reads; no SQL lives here.

Known first-cut limitations (recorded in ADR-0025, closed in follow-ups):

- ``parent_interaction_id`` is left ``NULL`` — the graph algorithm expresses
  nesting through its global ``order`` ordinal, which has no column in the
  production schema; unifying it with the streaming algorithm's ADR-0008 parent
  walk is a driver-phase concern.
- Only ``anchor``-role ``interaction_spans`` are emitted (the graph algorithm's
  prototype output carries one evidence span per interaction). The ``info`` /
  ``connector`` "territory" rows the streaming algorithm attaches are a documented
  gap; the raw material (``EntityNode.span_ids``) exists for a follow-up.
"""

from __future__ import annotations

import dataclasses

from data_governance.retrieval import Span

from . import caller_inference as ci
from . import procedure
from ..p_interactions_proto.extractor import ExtractResult
from ..p_interactions_proto.graph import EntityNode


# ---------------------------------------------------------------------------
# The duck-typed holder state.flush consumes
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class ProductionRows:
    """Exactly the six attributes ``state.flush(tx, proc, span)`` reads off its
    ``proc`` argument (see ``state.py``). ``adapt`` fills these; ``flush`` never
    isinstance-checks ``proc``, so this stands in for a real ``Processor``."""

    entities: dict[str, procedure.ProtoEntity]  # natural_key -> entity
    payloads: dict[str, procedure.ProtoPayload]  # content_hash -> payload
    interactions_by_anchor: dict[str, procedure.ProtoInteraction]  # anchor span id -> ix
    interaction_spans: list[procedure.ProtoInteractionSpan]
    entity_spans: list[procedure.ProtoEntitySpan]
    _repaired_span_ids: set[str]


# ---------------------------------------------------------------------------
# Entity identity re-derivation
# ---------------------------------------------------------------------------


def _is_mcp_server(span: Span) -> bool:
    """A deployed-tool signal: this service exposes an HTTP ``/mcp`` path.

    Mirrors the streaming algorithm's deployed-vs-in-process discriminator
    (``procedure._tool_transport_signal``) at the granularity the batch adapter
    has: any span whose route/target/url path contains ``/mcp``.
    """
    for key in ("http.route", "http.target", "url.path", "http.url", "url.full"):
        val = (span.attributes or {}).get(key)
        if isinstance(val, str) and "/mcp" in val:
            return True
    return False


def _coarse_kind(node: EntityNode) -> str | None:
    """The node's coarse kind from the proto's ``label`` prefix.

    Per ADR-0025 the label prefix (``agent:`` / ``llm:`` / ``tool:``) is the
    sanctioned coarse-kind signal — it is set from classification, not guessed
    from a pooled span. Only the natural-key *format* after the prefix is a
    prototype construct we must re-derive; the prefix itself is trustworthy and
    tells us WHICH ``caller_inference`` builder applies. This is why we dispatch
    on it rather than scanning the node's pooled spans (an agent node legitimately
    pools an LLM ``generation`` span in its own subtree — scanning for "any LLM
    span" would mis-identify the agent as an llm).
    """
    label = node.label
    if not label or ":" not in label:
        return None
    return label.split(":", 1)[0]


def _representative_identity(
    node: EntityNode, node_spans: list[Span], owning_agent_nk: str | None
) -> ci.Identity | None:
    """Re-derive a production ``Identity`` for one entity node.

    Builder selection is driven by the node's coarse kind (``_coarse_kind``); the
    *span* fed to the builder is the first span of the matching OI kind within the
    node, so the rich natural key is derived from the identity-bearing span, not a
    pooled neighbour. Falls back to a span-kind scan when the node has no label.
    """
    kind = _coarse_kind(node)

    if kind == "llm":
        for s in node_spans:
            if ci._is_oi_kind(s, "LLM"):
                ident = ci.llm_identity(s)
                if ident is not None:
                    return ident

    if kind == "tool":
        tool_spans = [s for s in node_spans if ci._is_oi_kind(s, "TOOL")]
        deployed = any(_is_mcp_server(s) for s in node_spans)
        for s in tool_spans:
            if deployed:
                ident = ci.deployed_tool_identity(s)
                if ident is not None:
                    return ident
            elif owning_agent_nk is not None:
                ident = ci.in_process_tool_identity(s, owning_agent_nk)
                if ident is not None:
                    return ident
        # A deployed-tool service whose only spans are SERVER/wrappers (no OI TOOL).
        if deployed:
            for s in node_spans:
                ident = ci.deployed_tool_identity(s)
                if ident is not None:
                    return ident
        # An INFERRED tool (Step 2.c: inferred from an LLM's tool_calls) has no
        # TOOL span of its own — its pooled spans are the LLM's. Build the
        # in-process identity from the label's tool name so it collides with an
        # observed in-process tool of the same name under the same agent.
        if node.inferred and owning_agent_nk is not None:
            name = (node.label or "tool:").split(":", 1)[1] or "(unnamed tool)"
            return ci.Identity(
                kind="tool",
                natural_key=f"tool:{owning_agent_nk}:{name}",
                display_name=name,
                project_name=None,
                detected_from="inferred",
            )

    if kind == "agent" or kind is None:
        # Prefer an AGENT/CHAIN span so canonical service naming applies.
        ordered = [s for s in node_spans if ci._is_oi_kind(s, "AGENT", "CHAIN")]
        ordered += [s for s in node_spans if s not in ordered]
        for s in ordered:
            ident = ci._agent_or_deployed_tool_from_service(s)
            if ident is not None:
                return ident

    # An inferred node NEVER falls through to the caller ladder — it has no spans
    # of its own, so the ladder would mint a phantom client from a borrowed peer
    # span. Its kind came from the label; if we could not build a rich key above,
    # skip it (guarded by tests: no phantom client/service entities).
    if node.inferred:
        return None

    # Fallbacks for OBSERVED nodes whose kind didn't resolve above: external
    # service, then the caller ladder (user/client), then any named service.
    for s in node_spans:
        ident = ci.service_identity_from_client(s)
        if ident is not None:
            return ident
    for s in node_spans:
        ident = ci.infer_caller_for_orphan_server(s)
        if ident is not None:
            return ident
    for s in node_spans:
        ident = ci._agent_or_deployed_tool_from_service(s)
        if ident is not None:
            return ident
    return None


def _owning_agent_nk(
    node: EntityNode,
    entity_graph,
    node_identity: dict[str, ci.Identity],
) -> str | None:
    """The natural key of the agent entity calling this (tool) node.

    An in-process tool's key is ``tool:<owning_agent_nk>:<name>``, so the caller
    entity on the incoming edge must be resolved first (pitfall #2). This reads
    the already-resolved identity of the edge's source node.
    """
    for ee in entity_graph.edges:
        if ee.to_node_id == node.id:
            caller = node_identity.get(ee.from_node_id)
            if caller is not None and caller.kind == "agent":
                return caller.natural_key
    return None


# ---------------------------------------------------------------------------
# Adapter entry
# ---------------------------------------------------------------------------


def adapt(result: ExtractResult, spans: list[Span]) -> ProductionRows:
    """Map one ``ExtractResult`` onto the production row model.

    ``spans`` is the same span list ``extract`` ran over (used to look up each
    node/edge span by id and to compute seq horizons).
    """
    span_by_id = {s.span_id: s for s in spans}
    entity_graph = result.entity_graph

    def _node_spans(n: EntityNode) -> list[Span]:
        return [span_by_id[sid] for sid in n.span_ids if sid in span_by_id]

    # --- pass 1: resolve every node's identity WITHOUT tool ownership ----------
    # (a tool's owning agent may itself be an agent node resolved in this pass.)
    node_identity: dict[str, ci.Identity] = {}
    for n in entity_graph.nodes:
        ident = _representative_identity(n, _node_spans(n), owning_agent_nk=None)
        if ident is not None:
            node_identity[n.id] = ident

    # --- pass 2: resolve in-process tools now that agent keys are known --------
    # An in-process tool's natural_key nests its owning agent's key, so the owner
    # (the caller entity on the incoming edge) must be resolved first (pass 1). This
    # covers BOTH observed in-process tools (own OI TOOL span) and inferred ones
    # (Step 2.c, inferred from an LLM's tool_calls — no TOOL span, only the LLM's
    # spans pooled). Both are coarse kind `tool` with no deployed `/mcp` signal.
    for n in entity_graph.nodes:
        ns = _node_spans(n)
        is_inprocess_tool = (
            _coarse_kind(n) == "tool"
            and not any(_is_mcp_server(s) for s in ns)
        )
        if is_inprocess_tool:
            owner = _owning_agent_nk(n, entity_graph, node_identity)
            if owner is not None:
                ident = _representative_identity(n, ns, owning_agent_nk=owner)
                if ident is not None:
                    node_identity[n.id] = ident

    # --- build production entities, collapsing on natural_key ------------------
    entities: dict[str, procedure.ProtoEntity] = {}
    # proto node id -> production entity id (uuid5). Many proto nodes may collapse
    # onto one natural_key; every node must still map to the one uuid5.
    entity_id_by_node: dict[str, str] = {}
    for n in entity_graph.nodes:
        ident = node_identity.get(n.id)
        if ident is None:
            continue
        eid = procedure._entity_id(ident.natural_key)
        entity_id_by_node[n.id] = eid
        ns = _node_spans(n)
        seq = min((s.seq for s in ns), default=0)
        detected = "inferred" if n.inferred else ident.detected_from
        existing = entities.get(ident.natural_key)
        if existing is None:
            entities[ident.natural_key] = procedure.ProtoEntity(
                id=eid,
                kind=ident.kind,
                natural_key=ident.natural_key,
                display_name=ident.display_name,
                project_name=ident.project_name,
                detected_from=detected,
                first_seen_seq=seq,
                seq=seq,
                original_seq=seq,
            )
        else:
            # Collapse: keep the earliest seq; prefer an observed detected_from.
            existing.first_seen_seq = min(existing.first_seen_seq, seq)
            existing.seq = min(existing.seq, seq)
            existing.original_seq = min(existing.original_seq, seq)
            if not n.inferred:
                existing.detected_from = ident.detected_from

    # --- build interactions, payloads, spans, entity_spans ---------------------
    payloads: dict[str, procedure.ProtoPayload] = {}
    interactions_by_anchor: dict[str, procedure.ProtoInteraction] = {}
    interaction_spans: list[procedure.ProtoInteractionSpan] = []
    entity_spans: list[procedure.ProtoEntitySpan] = []

    # Reuse the proto's own payload rows (same content_kind + hashing as main).
    proto_payload_by_hash = {p.content_hash: p for p in result.payloads}

    def _ensure_payload(content_hash: str | None) -> str | None:
        if content_hash is None:
            return None
        if content_hash not in payloads:
            src = proto_payload_by_hash.get(content_hash)
            if src is None:
                return None
            payloads[content_hash] = procedure.ProtoPayload(
                content_hash=src.content_hash,
                content_kind=src.content_kind,
                content=src.content,
                byte_size=src.byte_size,
            )
        return content_hash

    anchor_by_ix_id = {
        s.interaction_id: s for s in result.interaction_spans if s.is_anchor
    }
    # node id -> re-derived natural_key (for the composite interaction key + summary).
    nk_by_node = {nid: ident.natural_key for nid, ident in node_identity.items()}

    # The graph algorithm forms a BIDIRECTIONAL interaction per call (ADR-0025
    # Step 3.b): a request edge (forward, caller→callee) and a structurally-
    # reconstructed response edge (callee→caller). The production schema is
    # single-row (ADR-0013): request + response payloads side-by-side on ONE
    # interaction oriented caller→callee. So the two legs of one call collapse into
    # one production interaction, oriented by the REQUEST leg (the forward edge,
    # assigned the lower `order` by the Step 3.b execution-order walk).
    #
    # A leg is grouped by (anchor_span_id, unordered entity pair): the request and
    # response legs of one call share both; two DISTINCT calls that happen to share
    # an anchor span (an LLM call plus a tool call inferred from that LLM's
    # `tool_calls` — the inferred tool has no span of its own) differ by callee and
    # stay separate. The interaction id folds the callee natural key in so co-anchored
    # calls get distinct ids (see [[project_two_interaction_algorithms]]).

    @dataclasses.dataclass
    class _Leg:
        pi: object
        caller_eid: str
        callee_eid: str
        callee_nk: str
        req: str | None
        resp: str | None

    groups: dict[tuple, list[_Leg]] = {}
    group_anchor: dict[tuple, str] = {}
    for pi in result.interactions:
        caller_eid = entity_id_by_node.get(pi.caller_entity_id)
        callee_eid = entity_id_by_node.get(pi.callee_entity_id)
        anchor = anchor_by_ix_id.get(pi.id)
        if caller_eid is None or callee_eid is None or anchor is None:
            # Endpoint entity did not resolve, or no anchor evidence — skip rather
            # than write a dangling interaction (guarded by tests).
            continue
        key = (anchor.span_id, frozenset((caller_eid, callee_eid)))
        leg = _Leg(
            pi=pi,
            caller_eid=caller_eid,
            callee_eid=callee_eid,
            callee_nk=nk_by_node.get(pi.callee_entity_id, ""),
            req=_ensure_payload(pi.request_payload_hash),
            resp=_ensure_payload(pi.response_payload_hash),
        )
        groups.setdefault(key, []).append(leg)
        group_anchor[key] = anchor.span_id

    # span_id already claimed by an anchor row → synthesize a distinct one for the
    # next co-anchored call so main's UNIQUE(trace_id, span_id) is never violated
    # and main's flush + PK stay untouched ([[project_two_interaction_algorithms]]).
    # Iterate groups in a STABLE order (anchor span id, then callee natural key)
    # so which co-anchored call keeps the real span_id — and thus every derived
    # interaction_spans row — is identical across re-runs, letting the driver's
    # per-span re-derivation collapse on ON CONFLICT instead of accumulating.
    claimed_spans: set[str] = set()

    def _group_sort_key(item: tuple) -> tuple[str, str]:
        key, legs = item
        callee_nk = min(legs, key=lambda leg: leg.pi.order).callee_nk or "unknown"
        return (group_anchor[key], callee_nk)

    for key, legs in sorted(groups.items(), key=_group_sort_key):
        anchor_span_id = group_anchor[key]
        anchor_span = span_by_id.get(anchor_span_id)
        seq = anchor_span.seq if anchor_span is not None else 0
        # trace_id from the anchor's interaction_spans row (always present).
        trace_id = anchor_by_ix_id[legs[0].pi.id].trace_id

        # Request leg = the forward edge, i.e. the lower `order` of the pair.
        req_leg = min(legs, key=lambda leg: leg.pi.order)
        callee_nk = req_leg.callee_nk or "unknown"

        ix_id = procedure._interaction_id(trace_id, f"{anchor_span_id}/{callee_nk}")

        # Union payloads and error across both legs.
        req_hash = next((leg.req for leg in legs if leg.req), None)
        resp_hash = next((leg.resp for leg in legs if leg.resp), None)
        errors = [leg.pi.error for leg in legs]
        error = True if any(e for e in errors) else (
            False if any(e is False for e in errors) else None
        )

        interactions_by_anchor[anchor_span_id if anchor_span_id not in claimed_spans
                               else f"{anchor_span_id}/{callee_nk}"] = (
            procedure.ProtoInteraction(
                id=ix_id,
                trace_id=trace_id,
                parent_interaction_id=None,
                caller_entity_id=req_leg.caller_eid,
                callee_entity_id=req_leg.callee_eid,
                started_at=req_leg.pi.started_at,
                ended_at=req_leg.pi.ended_at,
                error=error,
                request_payload_hash=req_hash,
                response_payload_hash=resp_hash,
                summary=req_leg.pi.summary,
                seq=seq,
                original_seq=seq,
                anchor_rule="graph",
                primary_anchor_span_id=anchor_span_id,
            )
        )

        # One anchor interaction_spans row. Use the real span for the first call on
        # it; a synthetic `<span>#<callee_nk>` id for any co-anchored call after.
        span_for_row = (
            anchor_span_id
            if anchor_span_id not in claimed_spans
            else f"{anchor_span_id}#{callee_nk}"
        )
        claimed_spans.add(anchor_span_id)
        interaction_spans.append(
            procedure.ProtoInteractionSpan(
                interaction_id=ix_id,
                trace_id=trace_id,
                span_id=span_for_row,
                role="anchor",
            )
        )

    # entity_spans: provenance link for every span of every resolved entity.
    for n in entity_graph.nodes:
        eid = entity_id_by_node.get(n.id)
        if eid is None:
            continue
        for sid in n.span_ids:
            s = span_by_id.get(sid)
            if s is None:
                continue
            entity_spans.append(
                procedure.ProtoEntitySpan(
                    entity_id=eid,
                    trace_id=s.trace_id,
                    span_id=sid,
                    role="discovered_via",
                )
            )

    repaired = {r.span_id for r in interaction_spans}

    return ProductionRows(
        entities=entities,
        payloads=payloads,
        interactions_by_anchor=interactions_by_anchor,
        interaction_spans=interaction_spans,
        entity_spans=entity_spans,
        _repaired_span_ids=repaired,
    )
