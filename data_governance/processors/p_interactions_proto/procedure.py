"""Sufficiency-gated streaming per-span procedure for P-interactions.

THROWAWAY prototype. The procedure shape is what will run inside one
transaction per span when the streaming driver lands. Per ADR-0012.

**Sufficiency-gated emit-once.** An entity or interaction is materialised
ONLY at the arrival of the span that makes the *arrived-span set* sufficient
to decide it **finally** — no future span can change it. Emit-once,
when-decidable: never an eager emit that is later corrected, never an
end-of-trace pass, no parking, no retraction. (Order-independence is the
acceptance gate: scrambled span order must produce the same graph.)

Key invariant of the prototype: `extract()` feeds `process()` one span at a
time in seq order and the Processor never pre-loads. So at any point during
`process(S)`, `self.spans_by_id` / `self.children` contain EXACTLY the spans
that have arrived up to and including S — every scan is an arrived-only view
for free, with no seq guard. The only conclusions that would need the future
are *negative* ones ("no such span will ever arrive"); those rules are
dropped, not deferred.

Every edge fires on a POSITIVE completing span (the last member of its
sufficient set to arrive); a late member is handled by look-back over
already-arrived spans on the completing span's own arrival:

Emission is driven by `_retry_pending_endpoints`, called on EVERY span arrival
from `_dispatch`: it re-attempts every endpoint that has not yet emitted its
interaction, against the full arrived set. An endpoint's identity (and its
caller's) can be completed by ANY later span — most notably the service's OI
AGENT span, which finalizes near max-seq and so arrives LAST in-order / FIRST
under `--scramble`. Rather than special-case each completing-span kind, all
pending endpoints retry every arrival; emit-once (via `interactions_by_anchor`)
makes the retries idempotent, and the count is trace-bounded.

  - OI LLM / OI TOOL -> `agent → llm` / `agent → tool` (`_emit_oi`). Callee
    from the OI span's own attrs (for an OI TOOL, deployed-vs-in-process is
    decided by `_tool_transport_signal` — a `/mcp` SERVER in the subtree ⟹
    deployed, an A2A sub-agent SERVER ⟹ in-process, neither-yet ⟹ defer);
    caller = enclosing in-process tool or service-agent
    (`_resolve_caller_around_oi_span`, an ancestor walk).
  - SERVER whose in-trace parent is on a DIFFERENT canonical service ->
    cross-service (`_emit_cross_service`); caller/callee are the two services'
    identities. Same-service parent absorbed; deployed-tool callee absorbed
    (ADR-0010, structural).
  - Root SERVER + the service's OI AGENT span -> orphan-server
    (`_emit_orphan_server_on_root`, `client/user → agent`). Caller from the
    root SERVER's own attrs, callee `agent:` from the AGENT span.
  - OI TOOL + a descendant CLIENT POST (non-`/mcp`) to an UNOWNED host ->
    external-http (`_emit_external_http_on_client`, `tool → service:host`),
    anchored on the CLIENT span. A `/mcp` egress is MCP transport, absorbed.
  - CLIENT span on its own -> pure transport, never an endpoint.

Attachment: `interaction_spans` is STORED. On EVERY span arrival,
`_repair_after_arrival` re-derives the two arrival-order-dependent quantities
against the current arrived set — info/connector ownership (each non-anchor
span to its innermost current owner) and `parent_interaction_id` (the ADR-0008
ancestor walk). Both are pure functions of (arrived anchors, ancestry), so
recomputing them per arrival makes the final graph order-independent. Anchor
rows are emit-once and never re-derived, preserving the ADR-0011
`UNIQUE (trace_id, span_id)` invariant. The per-arrival trigger (not merely
per-emit) is required because the root SERVER finalizes near max-seq and so
arrives FIRST under `--scramble`, long before the spans in its territory.

`result()` does NO work — it only returns the accumulated snapshot.

State lives in-memory as Python dicts (the DB stand-in). `_owners_by_span`
is the in-memory facsimile of `UNIQUE (trace_id, span_id)`. `retracted_at` /
`_active_*` survive as schema-faithful scaffolding but, with no retraction
path, every emitted row stays active.
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
        # (trace_id, span_id). Anchor ownership is permanent (emit-once);
        # info/connector ownership is rebuilt by `_repair_after_arrival`.
        self._owners_by_span: dict[str, str] = {}

        # span_id -> role for the span's CURRENT interaction_spans row, so a
        # subtree re-derive can clear only the non-anchor (info/connector) rows
        # it owns and leave anchor rows untouched.
        self._role_by_span: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Lineage walks (the lineage-scoped replacement for trace-global scans).
    #
    # ADR-0012 productization: large traces require the per-arrival recompute
    # to be bounded by S's lineage (ancestors ∪ descendants), never a scan of
    # all arrived spans. The grill proved the driver's reach is EXACTLY
    # ancestors(S) ∪ descendants(S) — no sibling effect — because every
    # service's OI AGENT span nests inside that service's inbound SERVER span
    # (the AGENT-nests-in-SERVER invariant), so "match by service_name" is
    # equivalent to a same-service lineage walk.
    # ------------------------------------------------------------------

    def _parent_of(self, span: Span) -> Span | None:
        if not span.parent_id:
            return None
        return self.spans_by_id.get((span.trace_id, span.parent_id))

    def _ancestors(self, span: Span):
        """Yield span's ancestors, innermost first (parent, grandparent, …),
        among arrived spans. Stops at the first missing parent (orphan)."""
        cur = self._parent_of(span)
        while cur is not None:
            yield cur
            cur = self._parent_of(cur)

    def _crosses_service(self, a: Span, b: Span) -> bool:
        """True iff a and b are on different canonical services — the shared
        boundary predicate used by every lineage walk (identity, territory,
        caller). Mirrors the cross-service guards the trace-global code used
        inline (`_innermost_owner_for`, `_resolve_caller_around_oi_span`)."""
        ca = canonical_service_name(a)
        cb = canonical_service_name(b)
        return bool(ca and cb and ca != cb)

    def _is_service_entry_server(self, server: Span) -> bool:
        """True iff `server` is a service's INBOUND business entry SERVER (an
        agent's `POST /`, an instrumented service's `GET /weather`, …) rather
        than a deployed-tool `/mcp` transport SERVER. The `/mcp` SERVER is the
        transport leg of a deployed-tool call and does NOT start a new sub-agent
        territory; a `POST /`-style entry does. Used by the external-http
        ancestor walk to stop at sub-agent boundaries while still crossing
        `/mcp` transport to reach a deployed tool's owning OI TOOL span."""
        if server.kind != "SERVER":
            return False
        name = (server.name or "").upper()
        return not name.startswith("POST /MCP")

    def _service_entry_server(self, server: Span) -> Span:
        """Climb to the service's INBOUND entry SERVER: the topmost same-service
        SERVER reachable by an unbroken same-service ancestor chain from
        `server`. The AGENT-nests-in-SERVER invariant is that a service's OI
        AGENT span nests under this entry point (the `POST /` handler), not under
        the internal a2a SERVER spans (`_register_producer`, `_run_event_stream`,
        …) that are themselves descendants of it. Resolving identity from any
        internal SERVER must first climb to the entry SERVER so the bounded
        subtree walk starts from the span that actually encloses the AGENT."""
        svc = server.service_name
        entry = server
        for anc in self._ancestors(server):
            if anc.kind == "SERVER" and anc.service_name == svc:
                entry = anc
            elif anc.service_name != svc:
                break  # left the service — entry is the topmost same-service SERVER
        return entry

    def _same_service_agent_in_subtree(self, server: Span) -> Span | None:
        """The service's OI AGENT span, found by walking the service's ENTRY
        SERVER's arrived subtree and stopping descent at the first cross-service
        SERVER boundary (don't claim a sub-agent's AGENT span). Lineage-scoped
        replacement for `_agent_identity_from_agent_span`'s trace-global
        `service_name` scan.

        Asserts the AGENT-nests-in-SERVER invariant: if a same-service AGENT
        span exists that is NOT in the entry server's bounded subtree, it is an
        instrumentation anomaly — surfaced as a note, never silently used
        (fail-closed, matching ADR-0012)."""
        svc = server.service_name
        if not svc:
            return None
        entry = self._service_entry_server(server)
        return self._find_agent_below(entry, svc)
        # NB: a None return here is NOT an invariant violation — under streaming
        # the entry SERVER or the intervening spans may simply not have arrived
        # yet, so the bounded walk transiently misses the AGENT and the
        # per-arrival retry re-attempts. The AGENT-nests-in-SERVER invariant is
        # checked ONCE over the fully-arrived set in `_check_agent_nesting`
        # (called from `result()`), never per-arrival, so transient
        # non-convergence is not misreported as an anomaly.

    def _find_agent_below(self, root: Span, svc: str | None) -> Span | None:
        """Bounded BFS from `root` for a same-service OI AGENT span, pruning at
        cross-service SERVER boundaries. Pure descendant walk. Returns the first
        such AGENT (sufficient for identity — all same-service AGENT spans yield
        the same `agent:(project,canonical)` key)."""
        stack = list(self.children.get(root.span_id, []))
        while stack:
            s = stack.pop()
            if s.kind == "SERVER" and self._crosses_service(root, s):
                continue  # sub-agent territory — its AGENT is not ours
            if _is_oi_kind(s, "AGENT") and s.service_name == svc:
                return s
            stack.extend(self.children.get(s.span_id, []))
        return None

    def _agent_reachable_below(self, root: Span, agent: Span) -> bool:
        """True iff `agent` is in `root`'s bounded subtree (pruning cross-service
        SERVER boundaries). Used by the one-shot invariant check to test a
        SPECIFIC agent span's reachability (not just first-match)."""
        stack = list(self.children.get(root.span_id, []))
        while stack:
            s = stack.pop()
            if s.kind == "SERVER" and self._crosses_service(root, s):
                continue
            if s.span_id == agent.span_id:
                return True
            stack.extend(self.children.get(s.span_id, []))
        return False

    def _check_agent_nesting(self) -> None:
        """One-shot AGENT-nests-in-SERVER invariant check over the FULLY-ARRIVED
        set (called from `result()`, never per-arrival). For every arrived OI
        AGENT span, confirm it is reachable by the bounded subtree walk from its
        service's entry SERVER. A miss here — with all spans present — is a
        genuine instrumentation anomaly: surfaced as an instrumentation-signal
        note, fail-closed (ADR-0012). This is the productization-honest place
        for the assert: in a streaming run a transient miss is normal, only the
        converged final state can witness a real violation."""
        # Index entry servers by service from the arrived set.
        for agent in self.spans_by_id.values():
            if not _is_oi_kind(agent, "AGENT"):
                continue
            svc = agent.service_name
            if not svc:
                continue
            # Find the service's entry SERVER by climbing the AGENT's ancestors.
            entry = None
            for anc in self._ancestors(agent):
                if anc.kind == "SERVER" and anc.service_name == svc:
                    entry = self._service_entry_server(anc)
                    break
            reachable = entry is not None and self._agent_reachable_below(
                entry, agent
            )
            if not reachable:
                # The AGENT is not reachable from any same-service entry SERVER's
                # bounded subtree — invariant violated for this trace.
                if entry is None:
                    detail = "no same-service entry SERVER ancestor"
                else:
                    detail = f"not in bounded subtree of entry SERVER {entry.span_id}"
                note = (
                    f"instrumentation-signal: AGENT span {agent.span_id} on "
                    f"service {svc!r} {detail} (AGENT-nests-in-SERVER invariant "
                    f"violated)"
                )
                if note not in self.notes:
                    self.notes.append(note)

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

        # Sufficiency-gated dispatch: emit every edge this span COMPLETES,
        # reading only the arrived set (= spans_by_id, which holds arrived
        # spans only because we never pre-load). Emit-once, no parking, no
        # flush, no retraction.
        if not is_finalization:
            self._dispatch(span)

        # Payload + aggregate updates are inline in the emit path.

        # Step 9: cursor advance.
        self.last_processed_seq = max(self.last_processed_seq, span.seq)

    # ------------------------------------------------------------------
    # Sufficiency-gated dispatch (replaces the parked-chain model)
    # ------------------------------------------------------------------

    def _dispatch(self, span: Span) -> None:
        """Emit every edge whose sufficient set is now complete, then repair the
        derived tree/attachment against the arrived set.

        Both steps read only the arrived span set (`spans_by_id`, which holds
        arrived spans only — we never pre-load) and are re-run on EVERY arrival.
        An endpoint's identity can be completed by any later span (most notably
        its service's OI AGENT span, which finalizes near max-seq), so rather
        than special-casing each completing-span kind, we retry all pending
        endpoints every time. Emit-once makes retries idempotent; the work is
        trace-bounded (prototype scale). This is what makes the emitted graph
        independent of arrival order — the `--scramble` acceptance gate."""
        # --- Emit: retry every not-yet-emitted endpoint --------------------
        self._retry_pending_endpoints()

        # --- Arrival-driven repair (order-independence) --------------------
        # `parent_interaction_id` and info/connector ownership are pure
        # functions of (arrived anchors, ancestry). A span arriving now may be
        # the missing enclosing anchor of an earlier interaction, or a new
        # member of an existing interaction's territory. Re-derive both against
        # the current arrived set so the result does not depend on whether the
        # enclosing interaction emitted before or after its descendants
        # (the root SERVER, for one, finalizes near max-seq and so arrives
        # FIRST under --scramble — long before the spans in its territory).
        self._repair_after_arrival()

    # ------------------------------------------------------------------
    # Emit paths, one per completing event
    # ------------------------------------------------------------------

    def _emit_oi(self, span: Span) -> None:
        """OI LLM / OI TOOL callee. callee = the llm/tool entity; caller = the
        enclosing in-process tool or service-agent (resolved from arrived
        ancestors). If the callee classification or the caller is not yet
        resolvable (scramble: the agent wrapper or the OI TOOL's transport
        subtree hasn't arrived), emit nothing now — `_retry_pending_endpoints`
        re-attempts on the next arrival."""
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

    def _retry_pending_endpoints(self) -> None:
        """Re-attempt every endpoint that has not yet emitted its interaction,
        against the full arrived set. An endpoint's identity (and its caller's)
        can be completed by ANY later span — most importantly the service's OI
        AGENT span, which finalizes near max-seq and so arrives LAST in-order
        and FIRST under `--scramble`. Rather than special-case each
        completing-span kind, retry all pending endpoints on every arrival;
        emit-once (via `interactions_by_anchor`) makes this idempotent, and the
        endpoint count is trace-bounded (prototype scale)."""
        for (_, _sid), s in list(self.spans_by_id.items()):
            if s.span_id in self.interactions_by_anchor:
                continue  # already emitted on this anchor (emit-once)
            if _is_oi_kind(s, "LLM", "TOOL"):
                self._emit_oi(s)
            elif s.kind == "SERVER":
                if s.parent_id is None:
                    self._emit_orphan_server_on_root(s)
                else:
                    parent = self.spans_by_id.get((s.trace_id, s.parent_id))
                    if parent is not None:
                        self._emit_cross_service(s, parent)
            elif s.kind == "CLIENT":
                # external-http edge (anchored on this CLIENT span): look UP for
                # the owning OI TOOL ancestor and emit if the host qualifies.
                self._emit_external_http_on_client(s)

    def _emit_cross_service(self, span: Span, parent: Span) -> None:
        """SERVER callee whose in-trace parent is on a different canonical
        service. Same-service parent (a2a handler internals, `/mcp` transport)
        is absorbed; a deployed-tool callee is absorbed (ADR-0010, structural)."""
        callee_ident = self._resolve_service_side_identity(span)
        if callee_ident is None:
            return
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
        # SERVER spans resolve to the same deployed-tool callee and carry no
        # payload, so the cross-service edge they would anchor is redundant.
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

    def _emit_orphan_server_on_root(self, root: Span) -> None:
        """Root SERVER (no in-trace parent). callee `agent:` is resolved from
        the service's OI AGENT span; emit only if that span has already
        arrived. Caller (`user:`/`client:`) is from the root's own attrs."""
        if root.span_id in self.interactions_by_anchor:
            return
        callee_ident = self._resolve_service_side_identity(root)
        if callee_ident is None:
            return
        callee_entity = self._upsert_entity(callee_ident, root, "identified_via")
        caller_ident = infer_caller_for_orphan_server(root)
        caller_entity = self._upsert_entity(caller_ident, root, "identified_via")
        self._materialise(
            primary_span=root,
            caller_entity=caller_entity,
            callee_entity=callee_entity,
            anchor_span_ids=(root.span_id,),
            anchor_rule="orphan-server",
        )

    def _emit_external_http_on_client(self, client: Span) -> None:
        """external-http edge anchored on `client`: look UP for the NEAREST
        enclosing OI span and emit `tool → service:<host>` only if that nearest
        OI span is a TOOL. If the nearest enclosing OI span is an LLM, the
        egress is the LLM call's own transport (the `ete-litellm` gateway POST
        is a direct child of its OI LLM span) — absorb, never external-http.

        This nearest-OI-span rule is the lineage-local positive signal that
        replaces the dropped trace-global `_llm_gateway_hosts` exclusion: rather
        than collecting every LLM host trace-wide and excluding it, we observe
        that an LLM egress is *structurally* nested directly under its OI LLM
        span, so stopping the ancestor walk at the first OI span of EITHER kind
        distinguishes the two without a scan. Both `client` and its enclosing OI
        span are in the arrived set when this runs (retried every arrival), so
        it stays order-independent."""
        if self._external_http_host(client) is None:
            return
        for parent in self._ancestors(client):
            # Stop at an inbound `POST /` SERVER — a sub-agent service entry.
            # Crossing it means we have walked OUT of the egress's own service
            # into a PARENT that delegated here, so any OI TOOL above is the
            # delegate primitive, not this egress's owner. This distinguishes:
            #   - research-agent's `ete-litellm` egress, which sits under
            #     research-agent's inbound `POST /` (a sub-agent boundary) — its
            #     own LLM-gateway traffic, NOT the parent's `delegate_*` tool's
            #     egress → absorb; from
            #   - `charge_card`'s `psp-mock` egress, which crosses only a
            #     deployed-tool `/mcp` SERVER (NOT a `POST /` entry) to reach its
            #     owning OI TOOL `charge_card` on payment-agent → genuine
            #     external-http.
            if parent.kind == "SERVER" and self._is_service_entry_server(parent):
                return  # walked into a sub-agent's territory — not our egress
            if _is_oi_kind(parent, "LLM"):
                return  # LLM-gateway transport — absorbed, not external-http
            if _is_oi_kind(parent, "TOOL"):
                self._emit_external_http(client=client, tool_span=parent)
                return

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
        """Resolve a service-owning span's identity from POSITIVE evidence only
        (ADR-0012): a `/mcp` SERVER ⟹ deployed `tool:`; an OI AGENT span owned
        by the service ⟹ `agent:`. A bare SERVER span (no framework span yet
        arrived for its service) is NOT sufficient and returns None — it
        anchors nothing until a positive span arrives. The dropped rungs (the
        CLIENT-only `client:` branch and the bare-host `service:<host>` branch)
        were negative conclusions that needed an end-of-trace flush; ADR-0012
        removes them (the gate trace has no plain-HTTP inbound service)."""
        if not span.service_name:
            return None
        if span.service_name in self._mcp_services:
            return deployed_tool_identity(span)
        # `span` itself carries the framework marker (e.g. resolving a parent
        # OI span's service): its own attrs are sufficient, no walk needed.
        if _is_oi_kind(span, "AGENT", "LLM", "CHAIN", "TOOL"):
            return caller_inference._agent_or_deployed_tool_from_service(span)
        # Otherwise `span` is a bare SERVER (e.g. a `POST /` inbound): a service
        # that owns an OI AGENT span IS an agent, identity sourced from that
        # AGENT span. LINEAGE-SCOPED: walk span's own bounded subtree (stopping
        # at cross-service SERVER boundaries) rather than scanning all spans.
        # If no AGENT span is reachable yet, we cannot finally decide, emit
        # nothing (the per-arrival region recompute re-attempts).
        agent_span = self._same_service_agent_in_subtree(span)
        if agent_span is not None:
            return caller_inference._agent_or_deployed_tool_from_service(agent_span)
        return None

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

    # ------------------------------------------------------------------
    # Step 5c: attach spans
    # ------------------------------------------------------------------

    def _attach_span(self, ix: ProtoInteraction, span_id: str, role: str) -> None:
        """Attach a span to an interaction, maintaining the in-memory
        `UNIQUE (trace_id, span_id)` invariant: each span belongs to at most
        one interaction. ANCHOR attaches are emit-once and permanent.
        INFO/CONNECTOR attaches are owned by `_repair_after_arrival`, which
        clears the span's prior non-anchor row before re-attaching — so a
        re-derive can move a span to a tighter (innermost) owner without a
        steal-refusal.

        Anchor rows are never overwritten: an anchor of one interaction is
        never re-attached to another (the span tree is acyclic, so an anchor's
        innermost owner is itself)."""
        existing = self._role_by_span.get(span_id)
        owner = self._owners_by_span.get(span_id)
        if existing == "anchor" and owner is not None and owner != ix.id:
            # An anchor span of another interaction — never reassign.
            return
        if owner == ix.id and existing == role:
            return  # idempotent
        # Drop any prior row for this span (we are about to (re)write it).
        if owner is not None:
            self._detach_span(span_id)
        self._attached_span_ids.setdefault(ix.id, set()).add(span_id)
        self._owners_by_span[span_id] = ix.id
        self._role_by_span[span_id] = role
        self.interaction_spans.append(
            ProtoInteractionSpan(ix.id, ix.trace_id, span_id, role)
        )

    def _detach_span(self, span_id: str) -> None:
        """Remove a span's current interaction_spans row + ownership (used by
        re-derive before re-attaching to the innermost owner)."""
        owner = self._owners_by_span.pop(span_id, None)
        self._role_by_span.pop(span_id, None)
        if owner is not None:
            self._attached_span_ids.get(owner, set()).discard(span_id)
        self.interaction_spans = [
            r for r in self.interaction_spans if r.span_id != span_id
        ]

    def _innermost_owner_for(self, span: Span) -> ProtoInteraction | None:
        """The interaction whose ANCHOR is the nearest ancestor-or-self of
        `span` and whose canonical service contains `span` — i.e. the innermost
        arrived interaction whose territory holds `span`. Pure function of
        arrived anchors + ancestry, so order-independent."""
        cur: Span | None = span
        while cur is not None:
            ix = self.interactions_by_anchor.get(cur.span_id)
            if ix is not None and self._is_active_interaction(ix):
                # Cross-service boundary: a span only belongs to an interaction
                # on its own canonical service (mirrors the old subtree guard).
                ix_anchor = self._span_by_id(ix.primary_anchor_span_id)
                sp_canon = canonical_service_name(span)
                an_canon = (
                    canonical_service_name(ix_anchor) if ix_anchor else None
                )
                if not (sp_canon and an_canon and sp_canon != an_canon):
                    return ix
            if cur.parent_id is None:
                break
            cur = self.spans_by_id.get((cur.trace_id, cur.parent_id))
        return None

    def _repair_after_arrival(self) -> None:
        """Re-derive the two arrival-order-dependent quantities — `interaction_
        span` ownership (info/connector) and `parent_interaction_id` — against
        the CURRENT arrived set. Both are pure functions of (arrived anchors,
        ancestry), so recomputing them after every span arrival makes the final
        graph independent of arrival order. Anchor rows are emit-once and never
        touched. Bounded by trace size (prototype scale).

        This is the per-arrival convergence trigger: an interaction's territory
        is re-derived whenever a span enters it OR a new enclosing anchor
        appears, not only when the interaction itself emits — necessary because
        the root SERVER finalizes near max-seq and so arrives FIRST under
        `--scramble`, before any of the spans in its territory."""
        # 1. Re-derive non-anchor ownership: every arrived non-root span goes to
        #    its innermost current owner (clears any prior, looser claim).
        for (_, _sid), s in self.spans_by_id.items():
            if s.parent_id is None:
                continue  # trace root never attached
            if self._role_by_span.get(s.span_id) == "anchor":
                continue  # anchors are permanent
            owner = self._innermost_owner_for(s)
            if owner is None:
                if s.span_id in self._owners_by_span:
                    self._detach_span(s.span_id)
                continue
            role = "info" if self._has_payload_or_error(s) else "connector"
            self._attach_span(owner, s.span_id, role)
        # 2. Re-point parent_interaction_id for every active interaction.
        for ix in self.interactions_by_anchor.values():
            if not self._is_active_interaction(ix):
                continue
            ix.parent_interaction_id = self._compute_parent_interaction(
                ix.primary_anchor_span_id
            )
        # 3. Re-aggregate every active interaction over its current attachments.
        for ix in self.interactions_by_anchor.values():
            if self._is_active_interaction(ix):
                self._update_aggregates(ix)

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
    # External-http: re-anchored onto the owning OI TOOL span (ADR-0012).
    # ==================================================================

    def _external_http_host(self, client: Span) -> str | None:
        """The destination host of a CLIENT POST that qualifies as an
        UNINSTRUMENTED external service (`service:<host>`), else None.

        POSITIVE-ANCHORED, LINEAGE-LOCAL gates only (ADR-0012 productization).
        The former design excluded a host if it was owned by *any other entity*
        — a trace-global NEGATIVE that required scanning all LLM/CLIENT spans
        (`_llm_gateway_hosts`, `_non_service_hosts`). The grill proved that on
        the live trace the discriminators are entirely CLIENT-local + direct-
        child, so both global scans are dropped:

          1. the egress URL path is not a well-known non-business convention —
             `/mcp` (deployed-MCP transport) or `/.well-known/*` (agent-card
             discovery probe). Both are read from the CLIENT span's OWN url.
          2. the CLIENT has no in-trace SERVER child (the callee is
             uninstrumented — an instrumented agent/tool/service would emit a
             SERVER child). Direct-children only, already local.
          3. the host resolves via `_http_host`.

        The LLM-gateway exclusion is structurally redundant under the
        OI-TOOL-ancestor requirement in `_emit_external_http_on_client`: an LLM
        egress is a child of an OI LLM span, never of an OI TOOL span, so this
        path never reaches it. The `known_canonicals` / agent-host exclusions
        are subsumed by gate 2 (an instrumented service has a SERVER child).

        Gap accepted (per "let the prototype decide"): a future business egress
        to a host that is ALSO an LLM/instrumented host reached elsewhere in the
        same trace would no longer be excluded. New-fixture requirement when one
        exists — same stance ADR-0012 takes for the dropped bare-SERVER rung.
        """
        # Gate 1: well-known non-business egress paths are never external-http.
        #   - `POST .../mcp`         — deployed-tool transport (absorbed)
        #   - `GET .../.well-known/` — agent-card discovery probe (absorbed;
        #     this is the case the old trace-global `_non_service_hosts` caught,
        #     now a CLIENT-local path signal)
        url = caller_inference._attr(client, "http.url") or caller_inference._attr(
            client, "url.full"
        )
        if isinstance(url, str):
            from urllib.parse import urlparse

            path = (urlparse(url).path or "").rstrip("/")
            if path.endswith("/mcp"):
                return None
            if "/.well-known/" in path + "/" or path.endswith("/.well-known"):
                return None
        # Gate 2: an in-trace SERVER child means the callee is instrumented
        # (agent / deployed-tool / instrumented service) — not external-http.
        children = self.children.get(client.span_id, [])
        if any(c.kind == "SERVER" for c in children):
            return None
        host = caller_inference._http_host(client)
        if not host:
            return None
        return host

    def _emit_external_http(self, client: Span, tool_span: Span) -> None:
        """Emit `tool → service:<host>`. callee = `service:<host>` (the
        uninstrumented destination); caller = the OWNING OI TOOL span's identity
        (the deployed/in-process tool that issued the egress), resolved from the
        tool span — never from the destination host.

        Anchored PRIMARY on the CLIENT POST span (its unique anchor), NOT on the
        OI TOOL span — the OI TOOL span is already the primary anchor of the
        agent→tool edge, and `interactions_by_anchor` keys emit-once on the
        primary. The OI TOOL span is recorded as a secondary anchor for
        traceability. Emit-once on the CLIENT span: whichever of {tool, client}
        arrives second no-ops via `_materialise`. The edge nests under the
        agent→tool interaction via the ADR-0008 parent walk (the CLIENT's
        nearest enclosing anchor is the OI TOOL span)."""
        host = self._external_http_host(client)
        if host is None:
            return
        callee_ident = caller_inference.service_identity_from_client(client)
        if callee_ident is None:
            return
        caller_ident = self._classify_oi_endpoint(tool_span)
        if caller_ident is None:
            return
        if caller_ident.natural_key == callee_ident.natural_key:
            return
        caller_entity = self._upsert_entity(caller_ident, tool_span, "identified_via")
        callee_entity = self._upsert_entity(callee_ident, client, "identified_via")
        self._materialise(
            primary_span=client,
            caller_entity=caller_entity,
            callee_entity=callee_entity,
            anchor_span_ids=(client.span_id,),
            anchor_rule="external-http",
        )

    def _tool_transport_signal(self, tool_span: Span) -> str | None:
        """Classify an OI TOOL span's transport by walking its ARRIVED subtree
        for the first SERVER it reaches — the order-independent deployed-vs-
        in-process discriminator (ADR-0012):

          - a `POST /mcp` SERVER  ⟹ "deployed" (MCP `tools/call` transport);
          - any other SERVER on a DIFFERENT service (a sub-agent's `POST /`,
            reached via the A2A CLIENT) ⟹ "in-process" (a delegate FunctionTool);
          - no SERVER reached yet ⟹ None — undecided, so `_emit_oi` DEFERS and
            the per-arrival retry re-attempts once the transport subtree streams
            in. Both decisive answers are POSITIVE (a SERVER that arrived), so
            the classification converges regardless of arrival order; only the
            "neither has arrived yet" gap defers, never a negative conclusion.
        """
        servers = [
            ev
            for ev in _walk_descendants(self.children, tool_span)
            if ev.span_id != tool_span.span_id and ev.kind == "SERVER"
        ]
        # Deployed takes precedence: a `/mcp` transport SERVER anywhere in the
        # subtree means the tool is a deployed MCP server, regardless of what
        # other SERVERs are also reached.
        if any((s.name or "").upper().startswith("POST /MCP") for s in servers):
            return "deployed"
        # Otherwise a SERVER on a DIFFERENT service is the A2A sub-agent a
        # delegate FunctionTool consulted — positive in-process marker.
        if any(
            s.service_name and s.service_name != tool_span.service_name
            for s in servers
        ):
            return "in-process"
        return None

    def _classify_oi_endpoint(self, span: Span) -> Identity | None:
        """The llm/tool identity an OI LLM/TOOL span presents as a callee. For
        an OI TOOL the deployed-vs-in-process decision is gated on the
        order-independent transport signal (`_tool_transport_signal`); while
        undecided it returns None so `_emit_oi` defers."""
        if _is_oi_kind(span, "LLM"):
            return llm_identity(span)
        if _is_oi_kind(span, "TOOL"):
            signal = self._tool_transport_signal(span)
            if signal == "deployed":
                # LINEAGE-SCOPED: the deployed-tool identity comes from the
                # `/mcp` SERVER in this OI TOOL span's OWN subtree (the same
                # subtree `_tool_transport_signal` already walked to decide
                # "deployed"). The former trace-global `_matching_mcp_tool_span`
                # scan-by-canonical-name is redundant — the transport SERVER is
                # always a descendant of the tool span that drove the egress.
                for ev in _walk_descendants(self.children, span):
                    if ev.kind == "SERVER" and (ev.name or "").upper().startswith(
                        "POST /MCP"
                    ):
                        return deployed_tool_identity(ev)
                return None
            if signal == "in-process":
                owning = self._resolve_caller_around_oi_span(span)
                owning_nk = owning.natural_key if owning is not None else "(unknown)"
                return in_process_tool_identity(span, owning_nk)
            return None  # undecided — defer until the transport subtree arrives
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
        # Ownership guard (emit-once across anchor spans). Only a prior ANCHOR
        # claim blocks — an anchor span already anchoring a DIFFERENT interaction
        # is a genuine emit-once violation. A prior info/connector claim does
        # NOT block: an anchor claim is stronger and supersedes it (`_attach_span`
        # clears the prior non-anchor row). This matters because
        # `_repair_after_arrival` may have attached a cross-service edge's
        # caller-side anchor (a CLIENT span) as info/connector before the edge
        # itself emitted.
        for asid in anchor_span_ids:
            if self._role_by_span.get(asid) == "anchor":
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
        # info/connector attachment, parent re-pointing and aggregation are done
        # by `_repair_after_arrival` at the tail of `_dispatch` (once per span
        # arrival), so the emitted graph is independent of arrival order.
        self._update_aggregates(ix)

    # ------------------------------------------------------------------
    # Result snapshot — NO work (ADR-0012: nothing runs at trace-complete).
    # ------------------------------------------------------------------

    def result(self) -> ExtractResult:
        # One-shot AGENT-nests-in-SERVER invariant check over the fully-arrived
        # set (read-only; not derivation). In production this moves to the
        # AGENT span's own arrival, checked against its then-current lineage.
        self._check_agent_nesting()
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
