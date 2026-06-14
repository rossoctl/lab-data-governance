"""Pure-streaming per-span procedure for P-interactions.

THROWAWAY prototype (still batch — consumes a list of spans in seq order).
The procedure shape is what will run inside one transaction per span when
the streaming driver lands.

An interaction is the **span chain between two entity-bearing spans**,
materialised the instant BOTH endpoints exist. The processor relies solely on
streaming: emit-on-each-span, park until the second endpoint arrives, emit-once,
**no end-of-trace flush fixups and no retraction**. (Order-independence is the
acceptance gate: scrambled span order must produce the same active graph.)

Per-span work (`process` -> `_process_chain`):

  1. Store the span; index it under its parent_id.
  2. Bookkeeping: track canonical service names, `/mcp` SERVER services
     (deployed tools), and `mcp_tools`-advertised tool names.
  3. Classify the span as a *callee endpoint* and emit its chain, by kind:
       - OI LLM / OI TOOL  -> caller = enclosing in-process tool, else the
         service-agent (`_resolve_caller_around_oi_span`); single anchor.
       - SERVER, parent on a different canonical service -> cross-service;
         caller = the parent's service identity; two anchors. Same-service
         parent (a2a handler internals, `/mcp` transport) is absorbed. A
         deployed-tool callee is absorbed (the openinference-tool edge already
         represents it — ADR-0010 collapse, structural).
       - SERVER, no in-trace parent -> orphan-server.
       - CLIENT -> pure transport, never an endpoint.
     A SERVER whose cross-service parent hasn't arrived yet is parked
     (`_pending_callees`) and retried when the ancestor streams in; any still
     parked at end-of-trace are emitted as orphan-server.
  4. Payload extraction + started_at/ended_at/error aggregation are inline on
     the emitted interaction.

`result()` does NO correction — only streaming-compatible stabilisation: flush
still-parked SERVER callees as orphan-server, roll up info/connector descendants,
and recompute the ADR-0008 interaction tree.

State lives in-memory as Python dicts. `_owners_by_span` is the in-memory
facsimile of the `UNIQUE (trace_id, span_id)` constraint on `interaction_spans`
(each span belongs to at most one interaction) and enforces emit-once.
`retracted_at` / `_active_*` survive as schema-faithful scaffolding but, with no
retraction path, every emitted row stays active.
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import hashlib
import json
import uuid
from typing import Any

from data_governance.retrieval import Span

from . import caller_inference
from .caller_inference import (
    Identity,
    canonical_service_name,
    deployed_tool_identity,
    in_process_tool_identity,
    infer_caller_for_orphan_server,
    llm_identity,
)


# ---------------------------------------------------------------------------
# Output dataclasses (mirror the proto_* schema in cli.py)
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class ProtoEntity:
    id: str
    kind: str
    natural_key: str
    display_name: str
    project_name: str | None
    detected_from: str
    first_seen_seq: int  # for ordering; not in the production schema
    seq: int  # advances on every mutation (incl. retract)
    original_seq: int  # preserved at creation; mirrors ADR-0004 arrival_seq
    retracted_at: _dt.datetime | None = None  # ADR-0011 tombstone


@dataclasses.dataclass
class ProtoEntitySpan:
    entity_id: str
    trace_id: str
    span_id: str
    role: str  # discovered_via | identified_via


@dataclasses.dataclass
class ProtoInteraction:
    id: str
    trace_id: str
    parent_interaction_id: str | None
    caller_entity_id: str
    callee_entity_id: str
    started_at: Any
    ended_at: Any
    error: bool | None
    request_payload_hash: str | None
    response_payload_hash: str | None
    summary: str
    seq: int  # advances on each mutation (incl. retract)
    original_seq: int  # preserved at creation; mirrors ADR-0004 arrival_seq
    anchor_rule: str
    primary_anchor_span_id: str  # not in production schema; for tree-walking
    retracted_at: _dt.datetime | None = None  # ADR-0011 tombstone


@dataclasses.dataclass
class ProtoInteractionSpan:
    interaction_id: str
    trace_id: str
    span_id: str
    role: str  # anchor | info | connector


@dataclasses.dataclass
class ProtoPayload:
    content_hash: str
    content_kind: str
    content: Any
    byte_size: int


@dataclasses.dataclass
class ExtractResult:
    entities: list[ProtoEntity]
    entity_spans: list[ProtoEntitySpan]
    interactions: list[ProtoInteraction]
    interaction_spans: list[ProtoInteractionSpan]
    payloads: list[ProtoPayload]
    notes: list[str]
    last_processed_seq: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _attr(span: Span, key: str) -> Any:
    return (span.attributes or {}).get(key)


def _is_oi_kind(span: Span, *kinds: str) -> bool:
    return _attr(span, "openinference.span.kind") in kinds


def _tool_logical_name(span: Span) -> str | None:
    """The tool's logical name, preferring the `tool.name` attribute over the
    span name. Frameworks decorate the span name (e.g. google_sdk emits
    `execute_tool <name>`); `tool.name` carries the undecorated name. Used so
    deployed-MCP matching and in-process tool keys stay clean regardless of
    framework span-name conventions."""
    name = _attr(span, "tool.name")
    if isinstance(name, str) and name:
        return name
    return span.name


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, default=str).encode("utf-8")


def _hash_payload(canonical: bytes) -> str:
    return hashlib.sha256(canonical).hexdigest()


def _walk_descendants(by_parent: dict[str | None, list[Span]], root: Span):
    """DFS yielding root and all descendants currently known."""
    stack = [root]
    while stack:
        s = stack.pop()
        yield s
        for c in by_parent.get(s.span_id, []):
            stack.append(c)


def _extract_llm_messages(span: Span, prefix: str) -> list[dict[str, Any]] | None:
    attrs = span.attributes or {}
    msgs: dict[int, dict[str, Any]] = {}
    full_prefix = f"{prefix}."
    for key, value in attrs.items():
        if not key.startswith(full_prefix):
            continue
        rest = key[len(full_prefix) :]
        parts = rest.split(".", 1)
        if len(parts) != 2 or not parts[0].isdigit():
            continue
        idx = int(parts[0])
        sub = parts[1]
        msgs.setdefault(idx, {})[sub] = value
    if not msgs:
        return None
    return [msgs[i] for i in sorted(msgs)]


def _payload_for_llm(span: Span) -> tuple[ProtoPayload | None, ProtoPayload | None]:
    req_msgs = _extract_llm_messages(span, "llm.input_messages")
    resp_msgs = _extract_llm_messages(span, "llm.output_messages")
    req = resp = None
    if req_msgs is not None:
        canon = _canonical_bytes({"messages": req_msgs})
        req = ProtoPayload(_hash_payload(canon), "llm_chat_prompt", {"messages": req_msgs}, len(canon))
    if resp_msgs is not None:
        canon = _canonical_bytes({"messages": resp_msgs})
        resp = ProtoPayload(_hash_payload(canon), "llm_completion", {"messages": resp_msgs}, len(canon))
    return req, resp


def _payload_for_tool(span: Span) -> tuple[ProtoPayload | None, ProtoPayload | None]:
    iv = _attr(span, "input.value")
    ov = _attr(span, "output.value")
    req = resp = None
    if iv is not None:
        canon = _canonical_bytes(iv)
        req = ProtoPayload(_hash_payload(canon), "tool_call_arguments", iv, len(canon))
    if ov is not None:
        canon = _canonical_bytes(ov)
        resp = ProtoPayload(_hash_payload(canon), "tool_call_result", ov, len(canon))
    return req, resp


def _aggregate_error(spans_in_subtree: list[Span]) -> bool | None:
    saw_true = saw_false = False
    for s in spans_in_subtree:
        if s.error is True:
            saw_true = True
        elif s.error is False:
            saw_false = True
    if saw_true:
        return True
    if saw_false:
        return False
    return None


# ---------------------------------------------------------------------------
# The processor (in-memory state machine)
# ---------------------------------------------------------------------------


class Processor:
    """Per-span procedure executor.

    Spans are fed in via `process()` in increasing seq order. State is
    accumulated in-memory; `result()` returns a snapshot.
    """

    def __init__(self) -> None:
        # All spans seen so far, by composite id.
        self.spans_by_id: dict[tuple[str, str], Span] = {}
        # Children index, by parent_id (None bucket = roots).
        self.children: dict[str | None, list[Span]] = {}

        # Entities by natural_key (deduped across the trace).
        self.entities: dict[str, ProtoEntity] = {}
        # entity_spans rows.
        self.entity_spans: list[ProtoEntitySpan] = []
        # Track which entities have a discovered_via row already.
        self._entity_first_span: set[str] = set()

        # Interactions, keyed by primary_anchor_span_id (so late-parent
        # re-eval can find the existing interaction to mutate).
        self.interactions_by_anchor: dict[str, ProtoInteraction] = {}
        self.interaction_spans: list[ProtoInteractionSpan] = []
        self.payloads: dict[str, ProtoPayload] = {}

        # Service-canonicals seen so far (for external-http rule).
        self.known_canonicals: set[str] = set()
        # Services with a /mcp SERVER span (deployed-tool services).
        self._mcp_services: set[str] = set()
        # Tool names we've seen advertised via an `mcp_tools` CHAIN span's
        # output.value. Maps tool_name -> project (the project of the agent
        # whose mcp_tools span advertised it). Used to classify an OI TOOL
        # span as deployed even when the underlying HTTP transport produces
        # no in-trace SERVER span (openai_agents' MCPServerStreamableHttp
        # path on travel-advisor).
        self._mcp_tool_names: dict[str, str | None] = {}

        # Tools we've seen as openinference-tool anchors — span_id -> tool entity_id
        # (so the cross-service rule can recognise an MCP tool sitting under a
        # tool-anchor span and resolve its caller correctly).
        self._tool_anchor_entity_by_span: dict[str, str] = {}

        # Interactions touched per span (for aggregate update).
        self.notes: list[str] = []
        self.last_processed_seq: int = 0

        # interaction_id -> set of attached span_ids (for de-duped attachment)
        self._attached_span_ids: dict[str, set[str]] = {}

        # ADR-0011 schema invariant facsimile: each (trace_id, span_id) maps
        # to at most one non-retracted interaction. In production this is
        # `UNIQUE (trace_id, span_id)` on `interaction_spans`.
        # Key is span_id (single-trace prototype); production keys on
        # (trace_id, span_id).
        self._owners_by_span: dict[str, str] = {}

        # Pure-streaming parking: awaited_parent_span_id -> callee span_ids held
        # waiting for that ancestor to stream in. A SERVER callee whose
        # cross-service parent hasn't arrived yet is parked here and drained in
        # `_reattempt_parked_on_new_ancestor` when the ancestor arrives; any
        # still parked at end-of-trace are emitted as orphan-server in
        # `result()` (their caller proved unobservable).
        self._pending_callees: dict[str, list[str]] = {}

        # CLIENT spans deferred to flush for the external-http decision. The
        # exclusion gates depend on the full span set, so we collect candidate
        # CLIENT span_ids here and resolve them once in `result()`.
        self._pending_external_http: list[str] = []

    # ------------------------------------------------------------------
    # Visibility helpers (default-view query facsimile)
    # ------------------------------------------------------------------

    def _active_interactions(self) -> list[ProtoInteraction]:
        """`SELECT ... FROM interactions WHERE retracted_at IS NULL`."""
        return [ix for ix in self.interactions_by_anchor.values() if ix.retracted_at is None]

    def _is_active_interaction(self, ix: ProtoInteraction) -> bool:
        return ix.retracted_at is None

    # ------------------------------------------------------------------
    # Public entry
    # ------------------------------------------------------------------

    def process(self, span: Span) -> None:
        """Step 1-9 for a single span. Idempotent on re-process (finalization)."""
        # Step 1: fetch (already done — caller passes the Span).
        key = (span.trace_id, span.span_id)
        is_finalization = key in self.spans_by_id
        self.spans_by_id[key] = span
        if not is_finalization:
            self.children.setdefault(span.parent_id, []).append(span)

        # Step 2: resolve parent
        parent = (
            self.spans_by_id.get((span.trace_id, span.parent_id))
            if span.parent_id
            else None
        )

        # Track canonicals + mcp services for the external-http rule.
        canon = canonical_service_name(span)
        if canon:
            self.known_canonicals.add(canon)
        if span.kind == "SERVER" and (span.name or "").upper().startswith("POST /MCP"):
            if span.service_name:
                self._mcp_services.add(span.service_name)
        # `mcp_tools` CHAIN span advertises tool names from a remote MCP server.
        # Records the tool names so OI TOOL spans on that agent are classified
        # as deployed even when the transport produces no in-trace SERVER span.
        if span.name == "mcp_tools" and caller_inference._is_oi_kind(span, "CHAIN"):
            ov = caller_inference._attr(span, "output.value")
            tool_names: list[str] = []
            if isinstance(ov, str):
                try:
                    parsed = json.loads(ov)
                    if isinstance(parsed, list):
                        tool_names = [t for t in parsed if isinstance(t, str)]
                except Exception:  # noqa: BLE001
                    pass
            elif isinstance(ov, list):
                tool_names = [t for t in ov if isinstance(t, str)]
            project = caller_inference.project_name_of(span)
            for tn in tool_names:
                self._mcp_tool_names.setdefault(tn, project)

        # Pure-streaming chain emit: classify this span as a call-graph
        # endpoint; if it is, resolve its caller (by callee kind) and emit the
        # chain the instant both endpoints exist, else park until the awaited
        # ancestor streams in. Emit-once, no flush, no retraction.
        if not is_finalization:
            self._process_chain(span)

        # Payload + aggregate updates are inline in the emit path.

        # Step 9: cursor advance.
        self.last_processed_seq = max(self.last_processed_seq, span.seq)

    # ------------------------------------------------------------------
    # Step 3: entity evidence
    # ------------------------------------------------------------------

    def _upsert_entity(
        self, identity: Identity, span: Span, role: str
    ) -> ProtoEntity:
        e = self.entities.get(identity.natural_key)
        if e is None:
            e = ProtoEntity(
                id=str(uuid.uuid4()),
                kind=identity.kind,
                natural_key=identity.natural_key,
                display_name=identity.display_name,
                project_name=identity.project_name,
                detected_from=identity.detected_from,
                first_seen_seq=span.seq,
                seq=span.seq,
                original_seq=span.seq,
            )
            self.entities[identity.natural_key] = e
        elif e.retracted_at is not None:
            # Reviving a previously-retracted entity. Production would create a
            # fresh row; here we lift the tombstone and bump seq.
            e.retracted_at = None
            e.seq = max(e.seq, span.seq)
        # entity_spans (only on the active row)
        if e.id not in self._entity_first_span:
            self.entity_spans.append(
                ProtoEntitySpan(e.id, span.trace_id, span.span_id, "discovered_via")
            )
            self._entity_first_span.add(e.id)
        elif role == "identified_via":
            self.entity_spans.append(
                ProtoEntitySpan(e.id, span.trace_id, span.span_id, "identified_via")
            )
        return e

    # ------------------------------------------------------------------
    # Step 5a helpers: identity resolution at the service / OI-span level
    # ------------------------------------------------------------------

    def _resolve_service_side_identity(self, span: Span) -> Identity | None:
        if not span.service_name:
            return None
        if span.service_name in self._mcp_services:
            return deployed_tool_identity(span)
        kinds = {
            s.kind for s in self.spans_by_id.values() if s.service_name == span.service_name
        }
        if "SERVER" not in kinds and span.service_name:
            project = caller_inference.project_name_of(span)
            canon = caller_inference.canonical_service_name(span)
            if canon:
                return Identity(
                    kind="client",
                    natural_key=f"client:{canon}",
                    display_name=canon,
                    project_name=project,
                    detected_from="service emits only CLIENT/INTERNAL spans",
                )
        # A service that owns an OpenInference AGENT-kind span IS an agent, and
        # its identity must be sourced from that AGENT span — not from whatever
        # span happened to trigger this resolution. The orphan-server edge for
        # an agent anchors on the bare `POST /` root SERVER span (no
        # openinference.span.kind, and missing the openinference.project.name
        # resource attr the AGENT span carries). Building the agent Identity off
        # that root makes the project/canonical (and hence natural_key) depend
        # on which span streamed in first, and the bare-host `service` rung
        # below would mint a stray `service:travel-advisor` whenever the AGENT
        # span hasn't streamed in yet. Resolve the AGENT span authoritatively
        # first so `agent:(project,service)` is deterministic regardless of
        # arrival order.
        agent_ident = self._agent_identity_from_agent_span(span.service_name)
        if agent_ident is not None:
            return agent_ident
        # A service reached via a SERVER span but running no agent framework
        # (no OpenInference AGENT/LLM/CHAIN/TOOL span anywhere across the
        # trace) is a plain HTTP service, not an agent. Key it on the host it
        # was reached at (per CONTEXT.md `service:<hostname>`), which its OWN
        # SERVER span carries on its HTTP attributes (http.server_name /
        # http.url) — the caller-facing DNS name, not the bare service.name.
        #
        # Restricted to SERVER spans: a plain service is only ever identified
        # from its own inbound SERVER span. On a CLIENT span `_http_host`
        # returns the *destination* host, which is not this span-owner's
        # identity — so never apply this rung to the caller side.
        if span.kind == "SERVER" and not self._service_emits_oi_framework_span(
            span.service_name
        ):
            host = caller_inference._http_host(span)
            if host:
                return Identity(
                    kind="service",
                    natural_key=f"service:{host}",
                    display_name=host,
                    project_name=None,
                    detected_from="HTTP service (no agent-framework spans)",
                )
        return caller_inference._agent_or_deployed_tool_from_service(span)

    def _agent_identity_from_agent_span(self, service_name: str) -> Identity | None:
        """If `service_name` owns an OpenInference AGENT-kind span anywhere in
        the trace, build its `agent:(project,service)` Identity from that span.
        Order-independent: scans all spans and the result is sourced from the
        AGENT span, not from the span that triggered resolution."""
        for s in self.spans_by_id.values():
            if s.service_name != service_name:
                continue
            if _is_oi_kind(s, "AGENT"):
                return caller_inference._agent_or_deployed_tool_from_service(s)
        return None

    def _service_emits_oi_framework_span(self, service_name: str) -> bool:
        """True if any span owned by `service_name` carries an OpenInference
        framework span kind (the structural marker of an agent runtime). Plain
        HTTP fixtures emit only SERVER/CLIENT/INTERNAL spans with no
        `openinference.span.kind`."""
        for s in self.spans_by_id.values():
            if s.service_name != service_name:
                continue
            if _is_oi_kind(s, "AGENT", "LLM", "CHAIN", "TOOL"):
                return True
        return False

    def _resolve_caller_around_oi_span(self, span: Span) -> Identity | None:
        cur = span
        while cur.parent_id:
            parent = self.spans_by_id.get((cur.trace_id, cur.parent_id))
            if parent is None:
                break
            if parent.service_name != span.service_name:
                break
            tool_eid = self._tool_anchor_entity_by_span.get(parent.span_id)
            if tool_eid is not None:
                for e in self.entities.values():
                    if e.id == tool_eid:
                        return Identity(
                            kind=e.kind,
                            natural_key=e.natural_key,
                            display_name=e.display_name,
                            project_name=e.project_name,
                            detected_from="enclosing in-process tool span",
                        )
            cur = parent
        return self._resolve_service_side_identity(span)

    def _matching_mcp_tool_span(self, span: Span) -> Span | None:
        logical = _tool_logical_name(span)
        for s in self.spans_by_id.values():
            if (
                s.kind == "SERVER"
                and s.service_name
                and s.service_name in self._mcp_services
                and canonical_service_name(s) == (logical or None)
            ):
                return s
        return None

    # ------------------------------------------------------------------
    # Step 5b: interaction-tree parent (ADR-0008)
    # ------------------------------------------------------------------

    def _compute_parent_interaction(self, anchor_span_id: str) -> str | None:
        """ADR-0008 walk. Skip retracted anchors (D4 / ADR-0011 §2 step 4).
        First non-retracted ancestor that is the primary anchor of a
        different interaction → that's the parent.
        """
        anchor = self._span_by_id(anchor_span_id)
        if anchor is None:
            return None
        primary_to_active_ix: dict[str, str] = {
            asid: ix.id
            for asid, ix in self.interactions_by_anchor.items()
            if asid != anchor_span_id and self._is_active_interaction(ix)
        }
        cur = anchor
        while cur.parent_id:
            parent = self.spans_by_id.get((cur.trace_id, cur.parent_id))
            if parent is None:
                break
            other_ix_id = primary_to_active_ix.get(parent.span_id)
            if other_ix_id is not None:
                return other_ix_id
            cur = parent
        return None

    def _span_by_id(self, span_id: str) -> Span | None:
        for (_, sid), s in self.spans_by_id.items():
            if sid == span_id:
                return s
        return None

    def _span_depth(self, span_id: str) -> int:
        """Number of in-trace ancestors of `span_id` (root = 0). Used to order
        interactions innermost-first so subtree-territory claims are
        arrival-order-independent."""
        s = self._span_by_id(span_id)
        depth = 0
        while s is not None and s.parent_id is not None:
            s = self.spans_by_id.get((s.trace_id, s.parent_id))
            depth += 1
        return depth

    # ------------------------------------------------------------------
    # Step 5c: attach spans
    # ------------------------------------------------------------------

    def _attach_span(self, ix: ProtoInteraction, span_id: str, role: str) -> None:
        """Attach a span to an interaction. Enforces the in-memory
        `UNIQUE (trace_id, span_id)` invariant (D10): each span belongs
        to at most one non-retracted interaction. If already owned by
        a *different* interaction, the caller violated the invariant —
        log and skip rather than corrupt state.

        Same-interaction re-attach is idempotent (D13). Role can be
        promoted (connector → info) but not demoted.
        """
        owner = self._owners_by_span.get(span_id)
        if owner is not None and owner != ix.id:
            self.notes.append(
                f"INVARIANT: span {span_id} already owned by {owner}, "
                f"refused attach to {ix.id} as {role}"
            )
            return
        attached = self._attached_span_ids.setdefault(ix.id, set())
        if span_id in attached:
            # Promote role if needed (connector → info or anchor).
            self._maybe_promote_role(ix.id, span_id, role)
            return
        attached.add(span_id)
        self._owners_by_span[span_id] = ix.id
        self.interaction_spans.append(
            ProtoInteractionSpan(ix.id, ix.trace_id, span_id, role)
        )

    def _maybe_promote_role(
        self, interaction_id: str, span_id: str, new_role: str
    ) -> None:
        rank = {"connector": 0, "info": 1, "anchor": 2}
        for r in self.interaction_spans:
            if r.interaction_id == interaction_id and r.span_id == span_id:
                if rank.get(new_role, -1) > rank.get(r.role, -1):
                    r.role = new_role
                return

    def _attach_descendants_as_info_or_connector(
        self, ix: ProtoInteraction, root: Span
    ) -> None:
        """Attach the anchor's subtree, classifying each as info (carries
        payload/error attrs) or connector (otherwise). Stops descending
        into spans owned by other interactions (innermost-territory)."""
        for ev in _walk_descendants(self.children, root):
            if ev.span_id == root.span_id:
                continue
            owner = self._owners_by_span.get(ev.span_id)
            if owner is not None and owner != ix.id:
                continue
            ev_canon = canonical_service_name(ev)
            root_canon = canonical_service_name(root)
            if ev_canon and root_canon and ev_canon != root_canon:
                continue
            role = "info" if self._has_payload_or_error(ev) else "connector"
            self._attach_span(ix, ev.span_id, role)

    def _attach_caller_side_connectors(
        self, ix: ProtoInteraction, caller_anchor: Span
    ) -> None:
        """D11: walk *up* from a cross-service interaction's caller-side
        anchor (Sp), attaching ancestors as connectors until we hit a span
        already attached to ANY interaction in ANY role (not just primary
        anchor). This stops the agent-loop wrapper span from being attached
        to a cross-service interaction when it's already a connector
        elsewhere — preventing `min(started_at)` distortions from
        wrapper-attach.

        The trace root span itself is NOT attached (per ADR-0008).
        """
        cur = caller_anchor
        while cur.parent_id:
            parent = self.spans_by_id.get((cur.trace_id, cur.parent_id))
            if parent is None:
                break
            # D11: stop when parent is already owned by ANY interaction.
            owner = self._owners_by_span.get(parent.span_id)
            if owner is not None and owner != ix.id:
                break
            par_canon = canonical_service_name(parent)
            ca_canon = canonical_service_name(caller_anchor)
            if par_canon and ca_canon and par_canon != ca_canon:
                break
            if parent.parent_id is None:
                # Trace root — never part of an interaction (ADR-0008).
                break
            self._attach_span(ix, parent.span_id, "connector")
            cur = parent

    def _has_payload_or_error(self, span: Span) -> bool:
        attrs = span.attributes or {}
        if span.error is not None:
            return True
        for k in attrs:
            if k.startswith("llm.input_messages.") or k.startswith("llm.output_messages."):
                return True
            if k in ("input.value", "output.value", "http.request.body", "http.response.body"):
                return True
        return False

    # ------------------------------------------------------------------
    # Step 7: aggregate updates
    # ------------------------------------------------------------------

    def _update_aggregates(self, ix: ProtoInteraction) -> None:
        attached_ids = self._attached_span_ids.get(ix.id, set())
        attached_spans = [
            s for (_, sid), s in self.spans_by_id.items() if sid in attached_ids
        ]
        if not attached_spans:
            return
        ix.started_at = min((s.started_at for s in attached_spans), default=ix.started_at)
        ended = [s.ended_at for s in attached_spans if s.ended_at is not None]
        ix.ended_at = max(ended) if ended else ix.ended_at
        ix.error = _aggregate_error(attached_spans)

    # ------------------------------------------------------------------
    # Step 8: payload extraction
    # ------------------------------------------------------------------

    def _extract_payloads(self, ix: ProtoInteraction, anchor: Span) -> None:
        req = resp = None
        if ix.anchor_rule == "openinference-llm":
            req, resp = _payload_for_llm(anchor)
        elif ix.anchor_rule == "openinference-tool":
            req, resp = _payload_for_tool(anchor)
        if req is not None:
            self.payloads.setdefault(req.content_hash, req)
            ix.request_payload_hash = req.content_hash
        if resp is not None:
            self.payloads.setdefault(resp.content_hash, resp)
            ix.response_payload_hash = resp.content_hash

    # ==================================================================
    # Pure-streaming "span chain between two entities" emit.
    #
    # An interaction is the span chain between two entity-bearing spans,
    # materialised the instant BOTH endpoints exist. Emit-on-each-span, park
    # until the second endpoint arrives, emit-once, no retraction, no
    # end-of-trace fixups. See ~/.claude/plans/pure-streaming-chain-rewrite.md.
    # ==================================================================

    def _process_chain(self, span: Span) -> None:
        """The per-span chain emit. Called from `process()`.

        A span anchors an interaction only as a *callee endpoint*, and the
        caller is resolved by callee kind (not by "nearest endpoint ancestor" —
        an LLM is a leaf that never calls a tool, so the caller of a tool nested
        under an LLM is still the enclosing agent, not the LLM):

          - OI LLM / OI TOOL span -> caller = enclosing in-process tool, else
            the service-agent it runs on (`_resolve_caller_around_oi_span`).
            Single anchor (the OI span). Mirrors the legacy openinference-* rules.
          - SERVER span whose in-trace parent is on a DIFFERENT canonical
            service -> cross-service: caller = the parent's service identity.
            Two anchors (parent + callee). Mirrors the legacy cross-service rule.
          - SERVER span with no in-trace parent (root, or unresolvable) ->
            orphan-server: caller synthesised from the SERVER span itself.
          - CLIENT span -> pure transport, never an endpoint (no edge).

        Parking: a SERVER callee whose cross-service parent hasn't streamed in
        yet is held in `_pending_callees`, keyed on the awaited parent span_id,
        and retried when that ancestor arrives (step C).
        """
        # --- SERVER callee: cross-service or orphan-server -----------------
        if span.kind == "SERVER":
            if span.parent_id is None:
                # Orphan-server (trace root). Park to flush rather than emitting
                # eagerly: the callee identity is resolved at flush time, when
                # all spans are present, so a root that is really an agent (it
                # owns an OI AGENT span elsewhere in the trace) is classified
                # from that AGENT span regardless of arrival order. Emitting
                # eagerly here would, in some arrival orders, resolve the bare
                # `POST /` root to a stray `service:<host>` before the AGENT
                # span streamed in. Park under the span's own id (nothing
                # arrives on it) so `result()` flushes it once. Use a sentinel
                # awaited-key no real span_id can match, so the step-C reattempt
                # at the end of this method does not immediately un-park it.
                self._park_pending_callee(span.span_id, "__orphan_root__")
            else:
                parent = self.spans_by_id.get((span.trace_id, span.parent_id))
                if parent is None:
                    # Parent not yet arrived — park (generalises _deferred_spans).
                    self._park_pending_callee(span.span_id, span.parent_id)
                else:
                    self._emit_chain_server(span, parent=parent, orphan=False)

        # --- OI LLM / OI TOOL callee: agent->llm / agent->tool -------------
        elif _is_oi_kind(span, "LLM", "TOOL"):
            self._emit_chain_oi(span)

        # --- CLIENT callee: external-http to an UNINSTRUMENTED service -----
        # A CLIENT POST to a host that has no in-trace SERVER child and is not
        # otherwise represented (own service / weather / LLM-gateway / agent
        # host) is the only window onto an uninstrumented external service
        # (e.g. charge_card -> psp-mock). All other childless HTTP CLIENTs in
        # this trace are destinations ALREADY owned by another entity — the
        # 11 LLM-gateway POSTs (openinference-llm edges) and the agent-card
        # probe to the known payment-agent — and are excluded in
        # `_emit_chain_external_http`. Instrumented callees are reached via
        # their SERVER span (cross-service); the redundant 2nd weather edge the
        # 2026-06-08 blanket-drop targeted is still suppressed because
        # weather-service is in `known_canonicals`.
        elif span.kind == "CLIENT":
            # Park to flush: the exclusion gates (LLM-gateway hosts, agent/MCP
            # hosts, known_canonicals) depend on spans that may not have
            # streamed in yet, so resolving eagerly would, in some arrival
            # orders, leak a gateway host before its OI LLM span arrived.
            # Defer to `result()`, when all spans are present, so the decision
            # is arrival-order-independent. Sentinel awaited-key (no real span
            # arrives on it) so step C never un-parks it early.
            self._park_external_http_client(span.span_id)

        # C. this span may unblock callees parked on it as their ancestor.
        self._reattempt_parked_on_new_ancestor(span)

    # ------------------------------------------------------------------
    # Emit paths, one per callee kind
    # ------------------------------------------------------------------

    def _emit_chain_oi(self, span: Span) -> None:
        """OI LLM / OI TOOL callee. callee = the llm/tool entity; caller = the
        enclosing in-process tool or service-agent."""
        callee_ident = self._classify_oi_endpoint(span)
        if callee_ident is None:
            return
        callee_entity = self._upsert_entity(callee_ident, span, "identified_via")
        if _is_oi_kind(span, "TOOL"):
            self._tool_anchor_entity_by_span[span.span_id] = callee_entity.id
        caller_ident = self._resolve_caller_around_oi_span(span)
        if caller_ident is None:
            return
        caller_entity = self._upsert_entity(caller_ident, span, "identified_via")
        rule = "openinference-llm" if _is_oi_kind(span, "LLM") else "openinference-tool"
        self._materialise(
            primary_span=span,
            caller_entity=caller_entity,
            callee_entity=callee_entity,
            anchor_span_ids=(span.span_id,),
            anchor_rule=rule,
        )

    def _emit_chain_server(
        self, span: Span, parent: Span | None, orphan: bool
    ) -> None:
        """SERVER callee. Cross-service when the in-trace parent is on a
        different canonical service; orphan-server when there is no in-trace
        parent. A same-service parent (a2a handler internals, `POST /mcp`
        transport SERVER under its own OI TOOL) is absorbed — no edge."""
        callee_ident = self._resolve_service_side_identity(span)
        if callee_ident is None:
            return

        if orphan or parent is None:
            callee_entity = self._upsert_entity(callee_ident, span, "identified_via")
            caller_ident = infer_caller_for_orphan_server(span)
            caller_entity = self._upsert_entity(caller_ident, span, "identified_via")
            self._materialise(
                primary_span=span,
                caller_entity=caller_entity,
                callee_entity=callee_entity,
                anchor_span_ids=(span.span_id,),
                anchor_rule="orphan-server",
            )
            return

        # Cross-service only fires across a canonical-service boundary.
        sp_canon = canonical_service_name(parent)
        sn_canon = canonical_service_name(span)
        if sp_canon is None or sn_canon is None or sp_canon == sn_canon:
            return  # same-service parent — absorbed, no edge

        caller_ident = self._resolve_service_side_identity(parent)
        if caller_ident is None:
            return
        # Self-edge guard: a deployed-tool SERVER under its own OI TOOL/CLIENT
        # transport resolves to the same entity as the caller side — absorb.
        if caller_ident.natural_key == callee_ident.natural_key:
            return
        # Deployed-tool transport absorb (ADR-0010 collapse, structural form):
        # a deployed-MCP tool is legitimately reached only via its
        # payload-bearing openinference-tool edge. Its `POST /mcp` transport
        # SERVER spans — both the `tools/call` (nested under the OI TOOL span)
        # and the `tools/list` (nested under the agent's `mcp_tools` CHAIN) —
        # resolve to the same deployed-tool callee and carry no payload, so the
        # cross-service edge they would anchor is redundant. Suppress it. The
        # callee is a deployed tool exactly when its service has a `/mcp` SERVER
        # (`_resolve_service_side_identity` returns kind=tool via `_mcp_services`).
        # Order-independent (no edge-existence lookup). Structural replacement
        # for `_collapse_deployed_tool_transport`.
        if callee_ident.kind == "tool" and callee_ident.natural_key.startswith(
            "tool:("
        ):
            return
        callee_entity = self._upsert_entity(callee_ident, span, "identified_via")
        caller_entity = self._upsert_entity(caller_ident, parent, "identified_via")
        self._materialise(
            primary_span=span,
            caller_entity=caller_entity,
            callee_entity=callee_entity,
            anchor_span_ids=(parent.span_id, span.span_id),
            anchor_rule="cross-service",
        )

    def _emit_chain_external_http(self, span: Span) -> None:
        """CLIENT callee -> external `service` edge to an UNINSTRUMENTED host.

        Fires ONLY when every gate holds (so the 11 LLM-gateway POSTs and the
        agent-card probe to the known payment-agent do NOT mint stray
        `service:` entities — those hosts are destinations owned by an llm /
        agent entity, never a span's own `service.name`, so `known_canonicals`
        alone would not exclude them):

          1. no in-trace SERVER child (the callee is uninstrumented),
          2. host resolves via `_http_host`, and
          3. host is not already owned by another entity:
               - not in `known_canonicals` (our own services / weather / the
                 agent-card probe whose host == the agent's canonical name),
               - not an LLM-gateway host seen on any OI LLM span,
               - not a known agent / `/mcp` deployed-tool host (guard; those
                 reached over HTTP carry a SERVER child so gate #1 already
                 covers them).

        callee = `service:<host>` (psp-mock); caller = the CLIENT span's OWNER
        service identity (the `charge_card` deployed tool), resolved from the
        span's service_name — NOT from `_http_host`, which on a CLIENT span is
        the DESTINATION. Single anchor (the CLIENT span), emit-once, no
        retraction. Nests under the owning openinference-tool edge via the
        ADR-0008 parent walk.
        """
        # Gate 1: no in-trace SERVER child.
        children = self.children.get(span.span_id, [])
        if any(c.kind == "SERVER" for c in children):
            return
        # Gate 2: destination host resolves.
        host = caller_inference._http_host(span)
        if not host:
            return
        # Gate 3: host not already represented by another entity.
        if host in self.known_canonicals:
            return
        if host in self._llm_gateway_hosts():
            return
        if host in self._non_service_hosts():
            return
        callee_ident = caller_inference.service_identity_from_client(span)
        if callee_ident is None:
            return
        # Caller = the CLIENT span's OWNER service identity (deployed tool /
        # agent that owns the span), resolved from service_name — never from
        # the destination host.
        caller_ident = self._resolve_service_side_identity(span)
        if caller_ident is None:
            return
        if caller_ident.natural_key == callee_ident.natural_key:
            return
        caller_entity = self._upsert_entity(caller_ident, span, "identified_via")
        callee_entity = self._upsert_entity(callee_ident, span, "identified_via")
        self._materialise(
            primary_span=span,
            caller_entity=caller_entity,
            callee_entity=callee_entity,
            anchor_span_ids=(span.span_id,),
            anchor_rule="external-http",
        )

    def _park_external_http_client(self, span_id: str) -> None:
        self._pending_external_http.append(span_id)

    def _llm_gateway_hosts(self) -> set[str]:
        """Hosts seen on any OI LLM span (the LLM gateway). These are owned by
        an `llm:` entity, never a span's own `service.name`, so they are NOT in
        `known_canonicals`; exclude them explicitly from external-http."""
        hosts: set[str] = set()
        for s in self.spans_by_id.values():
            if _is_oi_kind(s, "LLM"):
                h = caller_inference._llm_host_from_invocation(s)
                if h:
                    hosts.add(h)
        return hosts

    def _non_service_hosts(self) -> set[str]:
        """Destination hosts owned by an agent / deployed-tool entity (the
        agent-card probe target, A2A peers, `/mcp` transports). A service that
        emits an OI framework span is an agent/tool; its caller-facing host is
        the canonical name a CLIENT reaches it at. Excluded explicitly because
        the destination host is never the span-owner's own `service.name`, so
        `known_canonicals` may not cover it (e.g. agent-card probe)."""
        hosts: set[str] = set()
        for s in self.spans_by_id.values():
            if s.kind != "CLIENT":
                continue
            child_ids = self.children.get(s.span_id, [])
            if not any(c.kind == "SERVER" for c in child_ids):
                continue
            # This CLIENT reaches an in-trace SERVER (an instrumented agent /
            # tool). Its destination host names that entity — register it.
            h = caller_inference._http_host(s)
            if h:
                hosts.add(h)
        return hosts

    def _classify_oi_endpoint(self, span: Span) -> Identity | None:
        """The llm/tool identity an OI LLM/TOOL span presents as a callee."""
        if _is_oi_kind(span, "LLM"):
            return llm_identity(span)
        if _is_oi_kind(span, "TOOL"):
            logical_name = _tool_logical_name(span)
            mcp_server = self._matching_mcp_tool_span(span)
            if mcp_server is not None:
                return deployed_tool_identity(mcp_server)
            if logical_name in self._mcp_tool_names:
                proj = self._mcp_tool_names.get(logical_name) or ""
                return Identity(
                    kind="tool",
                    natural_key=f"tool:({proj},{logical_name})",
                    display_name=logical_name,
                    project_name=proj or None,
                    detected_from="advertised by mcp_tools span",
                )
            owning = self._resolve_caller_around_oi_span(span)
            owning_nk = owning.natural_key if owning is not None else "(unknown)"
            return in_process_tool_identity(span, owning_nk)
        return None

    def _materialise(
        self,
        primary_span: Span,
        caller_entity: ProtoEntity,
        callee_entity: ProtoEntity,
        anchor_span_ids: tuple[str, ...],
        anchor_rule: str,
    ) -> None:
        """Create (or idempotently re-touch) the interaction anchored on
        `primary_span`. Emit-once: if the primary anchor is already owned by an
        active interaction, re-attach anchors and re-aggregate; never duplicate."""
        existing = self.interactions_by_anchor.get(primary_span.span_id)
        if existing is not None and self._is_active_interaction(existing):
            for asid in anchor_span_ids:
                self._attach_span(existing, asid, "anchor")
            self._update_aggregates(existing)
            return
        # Ownership guard (emit-once across anchor spans).
        for asid in anchor_span_ids:
            owner = self._owners_by_span.get(asid)
            if owner is not None:
                return

        ix = ProtoInteraction(
            id=str(uuid.uuid4()),
            trace_id=primary_span.trace_id,
            parent_interaction_id=None,
            caller_entity_id=caller_entity.id,
            callee_entity_id=callee_entity.id,
            started_at=primary_span.started_at,
            ended_at=primary_span.ended_at,
            error=primary_span.error,
            request_payload_hash=None,
            response_payload_hash=None,
            summary=f"{caller_entity.display_name} → {callee_entity.display_name}",
            seq=primary_span.seq,
            original_seq=primary_span.seq,
            anchor_rule=anchor_rule,
            primary_anchor_span_id=primary_span.span_id,
        )
        self.interactions_by_anchor[primary_span.span_id] = ix
        self._attached_span_ids[ix.id] = set()
        for asid in anchor_span_ids:
            self._attach_span(ix, asid, "anchor")
        self._extract_payloads(ix, primary_span)
        ix.parent_interaction_id = self._compute_parent_interaction(
            primary_span.span_id
        )
        self._update_aggregates(ix)

    def _park_pending_callee(
        self, callee_span_id: str, awaited_parent_span_id: str
    ) -> None:
        self._pending_callees.setdefault(awaited_parent_span_id, []).append(
            callee_span_id
        )

    def _reattempt_parked_on_new_ancestor(self, span: Span) -> None:
        """`span` just arrived; any callee parked on it can now resume. Currently
        only SERVER callees park (on their cross-service parent)."""
        parked = self._pending_callees.pop(span.span_id, [])
        for callee_span_id in parked:
            callee_span = self._span_by_id(callee_span_id)
            if callee_span is None or callee_span.kind != "SERVER":
                continue
            parent = (
                self.spans_by_id.get((callee_span.trace_id, callee_span.parent_id))
                if callee_span.parent_id
                else None
            )
            if parent is None:
                if callee_span.parent_id is not None:
                    self._park_pending_callee(callee_span_id, callee_span.parent_id)
                else:
                    self._emit_chain_server(callee_span, parent=None, orphan=True)
            else:
                self._emit_chain_server(callee_span, parent=parent, orphan=False)

    # ------------------------------------------------------------------
    # Result snapshot
    # ------------------------------------------------------------------

    def result(self) -> ExtractResult:
        # A SERVER callee still parked at end-of-trace has a parent_id that
        # refers to a span not in the trace (the caller is genuinely
        # unobservable) — emit it as orphan-server, mirroring the legacy
        # `_deferred_spans` flush. This is the one streaming-compatible flush:
        # it materialises chains whose awaited ancestor proved never to arrive,
        # not an after-the-fact correction.
        for callee_span_ids in list(self._pending_callees.values()):
            for callee_span_id in callee_span_ids:
                callee_span = self._span_by_id(callee_span_id)
                if callee_span is None or callee_span.kind != "SERVER":
                    continue
                if self._owners_by_span.get(callee_span.span_id) is not None:
                    continue
                callee_ident = self._resolve_service_side_identity(callee_span)
                if callee_ident is None:
                    continue
                callee_entity = self._upsert_entity(
                    callee_ident, callee_span, "identified_via"
                )
                self._emit_chain_server(callee_span, parent=None, orphan=True)
        self._pending_callees.clear()

        # External-http: now that all spans are present, the exclusion gates
        # (known_canonicals, LLM-gateway hosts, agent/MCP hosts) are complete,
        # so the decision is arrival-order-independent. Emit-once via
        # `_materialise`.
        for client_span_id in self._pending_external_http:
            client_span = self._span_by_id(client_span_id)
            if client_span is None or client_span.kind != "CLIENT":
                continue
            self._emit_chain_external_http(client_span)
        self._pending_external_http.clear()

        # After all spans, attach descendants of every active anchor and
        # (for cross-service) the caller-side connector chain.
        #
        # Process innermost-first (deepest primary-anchor span first) so an
        # inner interaction claims its subtree territory before an enclosing
        # one — making `_attach_descendants_as_info_or_connector`'s
        # "stop at spans owned by other interactions" boundary, and the error
        # aggregation that follows, INDEPENDENT of span arrival order.
        ordered_ix = sorted(
            (ix for ix in self.interactions_by_anchor.values() if self._is_active_interaction(ix)),
            key=lambda ix: self._span_depth(ix.primary_anchor_span_id),
            reverse=True,
        )
        for ix in ordered_ix:
            anchor_span = self._span_by_id(ix.primary_anchor_span_id)
            if anchor_span is None:
                continue
            self._attach_descendants_as_info_or_connector(ix, anchor_span)
            if ix.anchor_rule == "cross-service":
                caller_anchor_id = None
                for r in self.interaction_spans:
                    if (
                        r.interaction_id == ix.id
                        and r.role == "anchor"
                        and r.span_id != ix.primary_anchor_span_id
                    ):
                        caller_anchor_id = r.span_id
                        break
                if caller_anchor_id:
                    caller_anchor = self._span_by_id(caller_anchor_id)
                    if caller_anchor is not None:
                        self._attach_caller_side_connectors(ix, caller_anchor)
            self._update_aggregates(ix)

        # Recompute interaction tree (now that all anchors and attachments are known).
        for ix in self.interactions_by_anchor.values():
            if not self._is_active_interaction(ix):
                continue
            ix.parent_interaction_id = self._compute_parent_interaction(
                ix.primary_anchor_span_id
            )

        active_ix = self._active_interactions()
        active_entities = [e for e in self.entities.values() if e.retracted_at is None]
        self.notes.append(
            f"produced {len(active_ix)} active interactions "
            f"(+{len(self.interactions_by_anchor) - len(active_ix)} retracted), "
            f"{len(self.interaction_spans)} interaction_spans rows, "
            f"{len(active_entities)} active entities "
            f"(+{len(self.entities) - len(active_entities)} retracted), "
            f"{len(self.entity_spans)} entity_spans rows, "
            f"{len(self.payloads)} unique payloads"
        )
        # Return the FULL set (active + retracted) so the durable writer can
        # commit tombstoned rows alongside active ones, keeping the
        # entity_spans / interaction_spans FK references valid. Display
        # consumers (e.g. _print_report) filter retracted_at IS None for the
        # default view.
        return ExtractResult(
            entities=list(self.entities.values()),
            entity_spans=list(self.entity_spans),
            interactions=list(self.interactions_by_anchor.values()),
            interaction_spans=list(self.interaction_spans),
            payloads=list(self.payloads.values()),
            notes=list(self.notes),
            last_processed_seq=self.last_processed_seq,
        )


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------


def extract(spans: list[Span], scramble_for_late_parent: bool = False) -> ExtractResult:
    """Run the per-span procedure over a list of spans.

    spans must be in the order the streaming consumer would see them — for
    the batch driver this is seq order. With `scramble_for_late_parent=True`
    the order is rearranged so children arrive before their parents,
    exercising the late-parent re-eval path (M8 simulation).
    """
    ordered = sorted(spans, key=lambda s: s.seq)
    if scramble_for_late_parent:
        ordered = list(reversed(ordered))
    proc = Processor()
    for s in ordered:
        proc.process(s)
    return proc.result()
