"""Adapt the graph algorithm's ``ExtractResult`` to the production schema.

The batch graph algorithm (``processors.interactions.graph``) derives the same
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

``parent_interaction_id`` is derived by :func:`_compute_parents` — the same
ADR-0008 forest walk the streaming algorithm uses, so both algorithms produce an
equivalent interaction tree.

``interaction_spans`` carry the full ADR-0008 territory: the ``anchor`` row plus
``info`` / ``connector`` rows for every non-anchor span attached to its innermost
enclosing interaction (see :func:`_innermost_owner`, mirroring the streaming
algorithm's ``_innermost_owner_for``). Coverage is naturally sparser than the
streaming algorithm's because the graph anchors interactions on the callee-side
LEAF span (its subtree is small), whereas the streaming algorithm anchors higher
on the agent/CHAIN wrapper. The rule is identical; the anchor depth differs. The
transport/framework spans the graph collapses to Teal in Step 3 are intentionally
not attributed to any interaction.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from data_governance.retrieval import Span

from . import caller_inference as ci
from . import procedure
from .graph.extractor import ExtractResult
from .graph.graph import EntityNode


# ---------------------------------------------------------------------------
# The duck-typed holder state.flush consumes
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class LegRow:
    """One ``interaction_legs`` row the graph adapter supplies EXPLICITLY, so the
    response leg carries its OWN edge's timing/payload/error/order rather than
    being derived from the request edge's ``ended_at`` (ADR-0025).

    The graph algorithm forms a bidirectional interaction per call (a request
    edge S→D and a structurally-reconstructed response edge D→S); each edge has
    its own anchor span and its own global ``order``. The streaming algorithm has
    no such per-leg data — it does NOT populate ``legs_by_ix`` and ``state.flush``
    falls back to its ``_legs_of`` derived-leg projection, so streaming output is
    byte-identical. See ``ProductionRows.legs_by_ix``."""

    leg_type: str  # 'request' | 'response'
    occurred_at: Any  # request leg = request edge started_at; response = its own ended_at
    payload_hash: str | None
    error: bool | None
    seq: int  # the edge's global execution ordinal (`order`) — request < its response
    original_seq: int


@dataclasses.dataclass
class ProductionRows:
    """The attributes ``state.flush(tx, proc, span)`` reads off its ``proc``
    argument (see ``state.py``). ``adapt`` fills these; ``flush`` never
    isinstance-checks ``proc``, so this stands in for a real ``Processor``.

    ``legs_by_ix`` is an OPT-IN channel unique to the graph algorithm: when
    present, ``state.flush`` writes these leg rows verbatim (each leg's own
    ``occurred_at``/``payload_hash``/``error``/``seq``) instead of projecting them
    from the single ``ProtoInteraction`` via ``_legs_of``. ``procedure.Processor``
    never sets it, so ``getattr(proc, "legs_by_ix", None)`` is ``None`` on the
    streaming path and the derived-leg projection runs unchanged."""

    entities: dict[str, procedure.ProtoEntity]  # natural_key -> entity
    payloads: dict[str, procedure.ProtoPayload]  # content_hash -> payload
    interactions_by_anchor: dict[str, procedure.ProtoInteraction]  # anchor span id -> ix
    interaction_spans: list[procedure.ProtoInteractionSpan]
    entity_spans: list[procedure.ProtoEntitySpan]
    _repaired_span_ids: set[str]
    legs_by_ix: dict[str, list[LegRow]] | None = None  # interaction id -> its legs


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


def _has_payload_or_error(span: Span) -> bool:
    """A non-anchor span is ``info`` (carries payload/error/exception evidence)
    vs ``connector`` (territory only). Mirrors the streaming algorithm's
    ``procedure._has_payload_or_error`` so the role split matches."""
    if span.error is not None:
        return True
    for k in span.attributes or {}:
        if k.startswith("llm.input_messages.") or k.startswith("llm.output_messages."):
            return True
        if k in ("input.value", "output.value", "http.request.body", "http.response.body"):
            return True
    return False


def _innermost_owner(
    span: Span,
    span_by_id: dict[str, Span],
    ix_by_anchor: dict[str, procedure.ProtoInteraction],
) -> procedure.ProtoInteraction | None:
    """The interaction whose anchor is the nearest ancestor-or-self of *span* on
    the same canonical service — its innermost enclosing territory. Mirrors
    ``procedure._innermost_owner_for`` (ADR-0008), so graph territory ownership
    matches the streaming algorithm's."""
    sp_canon = ci.canonical_service_name(span)
    cur: Span | None = span
    while cur is not None:
        ix = ix_by_anchor.get(cur.span_id)
        if ix is not None:
            an = span_by_id.get(ix.primary_anchor_span_id)
            an_canon = ci.canonical_service_name(an) if an is not None else None
            # A span only belongs to an interaction on its own canonical service.
            if not (sp_canon and an_canon and sp_canon != an_canon):
                return ix
        if cur.parent_id is None:
            break
        cur = span_by_id.get(cur.parent_id)
    return None


def _compute_parents(
    interactions: list[procedure.ProtoInteraction],
    span_by_id: dict[str, Span],
) -> None:
    """Fill each interaction's ``parent_interaction_id`` in place, per ADR-0008.

    Same forest rule as the streaming algorithm's ``_compute_parent_interaction``:
    for interaction ``I``, walk up ``I``'s primary anchor span's ``parent_id`` chain;
    the first ANCESTOR span that is the primary anchor of a *different* interaction
    is ``I``'s parent. No enclosing anchor → NULL (a top-level interaction). This
    gives the graph algorithm the same interaction forest the streaming algorithm
    produces, so consumers walking ``parent_interaction_id`` behave identically
    regardless of ``INTERACTIONS_ALGORITHM``.

    The walk starts at the anchor's ``parent_id`` (ancestors only), so co-anchored
    interactions (an LLM call + a tool call inferred from that LLM's ``tool_calls``,
    sharing one real anchor span) are never each other's parent — they are siblings.
    When several interactions share one anchor span, the map resolves to a single
    deterministic representative (lowest id) so the ancestor lookup is stable.
    """
    # real anchor span id -> the interaction anchored there (deterministic pick).
    ix_by_anchor: dict[str, procedure.ProtoInteraction] = {}
    for ix in interactions:
        prior = ix_by_anchor.get(ix.primary_anchor_span_id)
        if prior is None or ix.id < prior.id:
            ix_by_anchor[ix.primary_anchor_span_id] = ix

    for ix in interactions:
        anchor = span_by_id.get(ix.primary_anchor_span_id)
        parent_id = None
        cur = anchor
        while cur is not None and cur.parent_id:
            parent = span_by_id.get(cur.parent_id)
            if parent is None:
                break
            owner = ix_by_anchor.get(parent.span_id)
            if owner is not None and owner.id != ix.id:
                parent_id = owner.id
                break
            cur = parent
        ix.parent_interaction_id = parent_id


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
    legs_by_ix: dict[str, list[LegRow]] = {}  # interaction id -> its request/response legs

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
    # reconstructed response edge (callee→caller), EACH with its own global
    # `order` and its own anchor span. The production schema (ADR-0025) is a parent
    # identity row oriented caller→callee plus one request + one response
    # `interaction_legs` row. So the two directed edges of one call collapse into
    # ONE parent, oriented by the REQUEST edge (the lower `order`), with each edge
    # supplying its own leg (own occurred_at / payload / error / `order`→`seq`).
    #
    # PAIRING a request edge with ITS response edge cannot key on the anchor span:
    # for an A2A delegation the two edges anchor on DIFFERENT spans (request on the
    # caller's `delegate_*` TOOL span, response on the responding agent's own
    # wrapper span — Step 3.b point 2). Instead, within each unordered entity pair,
    # match edges by a stack over `order`: the walk emits a call's request before
    # its response and fully unwinds nested calls (LIFO), so forward edges (request
    # direction) "open" and reverse edges (response direction) "close" — popping the
    # nearest open request. This pairs `(20→27)` and `(30→47)` for two nested
    # `travel-advisor↔booking_agent` delegations correctly, where anchor-keying
    # would split each edge into its own (wrong) interaction.

    @dataclasses.dataclass
    class _Leg:
        pi: object
        caller_eid: str
        callee_eid: str
        callee_nk: str
        anchor_span_id: str
        req: str | None
        resp: str | None

    # Directed edges grouped by unordered entity pair, so request/response of one
    # call meet. `entity_pair -> [_Leg sorted by order]`.
    by_pair: dict[frozenset, list[_Leg]] = {}
    for pi in result.interactions:
        caller_eid = entity_id_by_node.get(pi.caller_entity_id)
        callee_eid = entity_id_by_node.get(pi.callee_entity_id)
        anchor = anchor_by_ix_id.get(pi.id)
        if caller_eid is None or callee_eid is None or anchor is None:
            # Endpoint entity did not resolve, or no anchor evidence — skip rather
            # than write a dangling interaction (guarded by tests).
            continue
        by_pair.setdefault(frozenset((caller_eid, callee_eid)), []).append(
            _Leg(
                pi=pi,
                caller_eid=caller_eid,
                callee_eid=callee_eid,
                callee_nk=nk_by_node.get(pi.callee_entity_id, ""),
                anchor_span_id=anchor.span_id,
                req=_ensure_payload(pi.request_payload_hash),
                resp=_ensure_payload(pi.response_payload_hash),
            )
        )

    # Stack-match each pair into (request_leg, response_leg) calls. The request
    # direction is the one carrying the lowest-`order` edge of the pair; a reverse
    # edge pops the nearest unclosed request. A lone edge (one-sided chain, no
    # response reconstructed) pairs with itself so the parent is never leg-less.
    calls: list[tuple[_Leg, _Leg]] = []
    for pair_legs in by_pair.values():
        pair_legs.sort(key=lambda leg: leg.pi.order)
        fwd = (pair_legs[0].caller_eid, pair_legs[0].callee_eid)
        stack: list[_Leg] = []
        for leg in pair_legs:
            if (leg.caller_eid, leg.callee_eid) == fwd:
                stack.append(leg)
            elif stack:
                calls.append((stack.pop(), leg))  # (request, response)
            else:
                calls.append((leg, leg))  # unbalanced reverse — degenerate, self-pair
        for leftover in stack:
            calls.append((leftover, leftover))  # request with no observed response

    # span_id already claimed by an anchor row → synthesize a distinct one for the
    # next co-anchored call so main's UNIQUE(trace_id, span_id) is never violated
    # and main's flush + PK stay untouched ([[project_two_interaction_algorithms]]).
    # Iterate calls in a STABLE order (request anchor span id, then callee natural
    # key) so which co-anchored call keeps the real span_id — and thus every
    # derived interaction_spans row — is identical across re-runs, letting the
    # driver's per-span re-derivation collapse on ON CONFLICT instead of accumulating.
    claimed_spans: set[str] = set()

    def _call_sort_key(call: tuple[_Leg, _Leg]) -> tuple[str, str]:
        req_leg, _ = call
        return (req_leg.anchor_span_id, req_leg.callee_nk or "unknown")

    for req_leg, resp_leg in sorted(calls, key=_call_sort_key):
        anchor_span_id = req_leg.anchor_span_id
        anchor_span = span_by_id.get(anchor_span_id)
        seq = anchor_span.seq if anchor_span is not None else 0
        # trace_id from the request edge's anchor interaction_spans row.
        trace_id = anchor_by_ix_id[req_leg.pi.id].trace_id

        callee_nk = req_leg.callee_nk or "unknown"
        # The id folds in the request edge's global `order`, not just the anchor +
        # callee: two DISTINCT calls of the same pair can share one anchor span and
        # callee (e.g. an agent dispatching `get_weather` twice — both request edges
        # anchor on the one agent span). `order` is globally unique per edge, so it
        # is the stable discriminator that keeps such calls separate across re-runs
        # (deterministic-id contract). Co-anchored calls with a DIFFERENT callee (an
        # LLM call + an inferred tool call on one LLM span) already differ by
        # callee_nk; folding order in as well is harmless there.
        ix_id = procedure._interaction_id(
            trace_id, f"{anchor_span_id}/{callee_nk}/{req_leg.pi.order}"
        )

        # Each leg keeps its OWN edge's payload; error unions the pair (either side
        # erroring marks the call errored — extractor's whole-pair rule).
        req_hash = req_leg.req
        resp_hash = resp_leg.resp
        errors = [req_leg.pi.error, resp_leg.pi.error]
        error = True if any(e for e in errors) else (
            False if any(e is False for e in errors) else None
        )

        interactions_by_anchor[anchor_span_id if anchor_span_id not in claimed_spans
                               else f"{anchor_span_id}/{callee_nk}/{req_leg.pi.order}"] = (
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

        # Per-leg rows (ADR-0025): each leg carries its OWN edge's data, so the
        # response leg reflects the responding endpoint's own anchor span — its
        # `ended_at` (genuinely distinct from the request edge's for an A2A
        # delegation, where the two legs split anchors), its own payload/error,
        # and its own global `order` as the leg `seq`/`original_seq`. This is what
        # distinguishes the two legs when they SHARE an anchor span (the ordinary
        # case: identical timing, so `leg_type` + the order-derived `seq` is the
        # only signal). `state.flush` writes these instead of deriving legs from
        # the collapsed ProtoInteraction's started_at/ended_at.
        legs_by_ix[ix_id] = [
            LegRow(
                leg_type="request",
                occurred_at=req_leg.pi.started_at,
                payload_hash=req_hash,
                error=req_leg.pi.error,
                seq=req_leg.pi.order,
                original_seq=req_leg.pi.order,
            ),
            LegRow(
                leg_type="response",
                occurred_at=resp_leg.pi.ended_at,
                payload_hash=resp_hash,
                error=resp_leg.pi.error,
                seq=resp_leg.pi.order,
                original_seq=resp_leg.pi.order,
            ),
        ]

        # One anchor interaction_spans row. Use the real span for the first call on
        # it; a synthetic `<span>#<callee_nk>#<order>` id for any co-anchored call
        # after, so main's UNIQUE(trace_id, span_id) holds even when the SAME callee
        # is invoked twice on one anchor span (order is the per-edge discriminator).
        span_for_row = (
            anchor_span_id
            if anchor_span_id not in claimed_spans
            else f"{anchor_span_id}#{callee_nk}#{req_leg.pi.order}"
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

    # parent_interaction_id: the ADR-0008 forest, derived once every interaction
    # exists (the walk needs the full anchor→interaction map).
    all_interactions = list(interactions_by_anchor.values())
    _compute_parents(all_interactions, span_by_id)

    # interaction_spans territory (info/connector): every non-anchor span in the
    # trace goes to its innermost enclosing interaction (ADR-0008), mirroring the
    # streaming algorithm's _repair_after_arrival. This gives each interaction the
    # per-span evidence list the anchor-only first cut lacked. The real anchor spans
    # are already represented by anchor rows, so they are excluded here — that keeps
    # UNIQUE(trace_id, span_id) intact (each span → exactly one interaction_spans
    # row). Deterministic pick when several interactions share one anchor span.
    ix_by_anchor: dict[str, procedure.ProtoInteraction] = {}
    for ix in all_interactions:
        prior = ix_by_anchor.get(ix.primary_anchor_span_id)
        if prior is None or ix.id < prior.id:
            ix_by_anchor[ix.primary_anchor_span_id] = ix
    anchor_span_ids = {ix.primary_anchor_span_id for ix in all_interactions}
    for s in span_by_id.values():
        if s.parent_id is None or s.span_id in anchor_span_ids:
            continue  # trace root never attached; anchors are emit-once
        owner = _innermost_owner(s, span_by_id, ix_by_anchor)
        if owner is None:
            continue
        role = "info" if _has_payload_or_error(s) else "connector"
        interaction_spans.append(
            procedure.ProtoInteractionSpan(
                interaction_id=owner.id,
                trace_id=s.trace_id,
                span_id=s.span_id,
                role=role,
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
        legs_by_ix=legs_by_ix,
    )
