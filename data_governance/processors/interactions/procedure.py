"""Sufficiency-gated streaming per-span procedure for P-interactions.

Ported verbatim from the (now-removed) P-interactions prototype ``procedure``
module (the verified classification — the ``--scramble`` order-independence gate
validated it). The
ONLY changes from the prototype are state-layer ones: row ids are deterministic
(``_entity_id`` / ``_interaction_id``) so the DB-backed re-derive is idempotent.
Every ``_emit_*`` / ``_classify_*`` / ``_repair_after_arrival`` method is
unchanged — do not redesign them. The driver (``driver.py``) constructs a
``Processor`` per arriving span, seeds its in-memory dicts from the span's DB
lineage (``state.py``), runs ``process(S)``, then flushes the mutated dicts back
to the database. Per ADR-0012.

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
    cross-service (`_emit_cross_service`); callee = the callee service's
    identity, caller = the NEAREST ENCLOSING ENTITY on the caller-side
    (`_resolve_cross_service_caller`: an enclosing in-process tool, else the
    owning agent — ADR-0016, not merely the owning service). Same-service
    parent absorbed; deployed-tool callee absorbed (ADR-0010, structural).
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
# Deterministic ids (the one state-layer change to the verified algorithm).
# ---------------------------------------------------------------------------
#
# The prototype minted ``uuid4()`` per run, which is fine for an in-memory
# single pass but breaks the DB-backed re-derive: re-processing a span must
# write the SAME row id so ``ON CONFLICT`` upserts collapse to one row and the
# cursor-reset re-run is byte-identical. We derive ids from the natural
# identity instead:
#   - an entity from its ``natural_key`` (entities are cross-trace-stable),
#   - an interaction from ``trace_id`` + its primary anchor span id (the
#     emit-once key; interactions are trace-scoped).
# Same logical row → same id, every run, every process.
_NS_ENTITY = uuid.UUID("6f8d1c2e-0a3b-4d5e-8f90-1a2b3c4d5e6f")
_NS_INTERACTION = uuid.UUID("9c7e2b4a-1d6f-4a8b-9c0d-2e3f4a5b6c7d")

# Well-known liveness/readiness probe path segments. A CLIENT egress whose final
# path segment is one of these is an infrastructure probe, not a business
# external-http interaction — excluded in `_external_http_host` gate 1 alongside
# `/mcp` and `/.well-known/*`. Matched on the exact final segment (not a
# substring) so a business path like `/healthcheckups` is not mis-declined.
_PROBE_PATHS = frozenset(
    {"healthz", "readyz", "livez", "healthcheck", "health", "ping"}
)


def _entity_id(natural_key: str) -> str:
    return str(uuid.uuid5(_NS_ENTITY, natural_key))


def _interaction_id(trace_id: str, primary_anchor_span_id: str) -> str:
    return str(uuid.uuid5(_NS_INTERACTION, f"{trace_id}/{primary_anchor_span_id}"))


# ---------------------------------------------------------------------------
# Output dataclasses (mirror the proto_* schema in cli.py)
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class Entity:
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
class EntitySpan:
    entity_id: str
    trace_id: str
    span_id: str
    role: str  # discovered_via | identified_via


@dataclasses.dataclass
class Interaction:
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
class InteractionSpan:
    interaction_id: str
    trace_id: str
    span_id: str
    role: str  # anchor | info | connector


@dataclasses.dataclass
class Payload:
    content_hash: str
    content_kind: str
    content: Any
    byte_size: int


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


def _payload_for_llm(span: Span) -> tuple[Payload | None, Payload | None]:
    req_msgs = _extract_llm_messages(span, "llm.input_messages")
    resp_msgs = _extract_llm_messages(span, "llm.output_messages")
    req = resp = None
    if req_msgs is not None:
        canon = _canonical_bytes({"messages": req_msgs})
        req = Payload(_hash_payload(canon), "llm_chat_prompt", {"messages": req_msgs}, len(canon))
    if resp_msgs is not None:
        canon = _canonical_bytes({"messages": resp_msgs})
        resp = Payload(_hash_payload(canon), "llm_completion", {"messages": resp_msgs}, len(canon))
    return req, resp


def _payload_for_tool(span: Span) -> tuple[Payload | None, Payload | None]:
    iv = _attr(span, "input.value")
    ov = _attr(span, "output.value")
    req = resp = None
    if iv is not None:
        canon = _canonical_bytes(iv)
        req = Payload(_hash_payload(canon), "tool_call_arguments", iv, len(canon))
    if ov is not None:
        canon = _canonical_bytes(ov)
        resp = Payload(_hash_payload(canon), "tool_call_result", ov, len(canon))
    return req, resp


def _aggregate_error(
    spans_in_subtree: list[Span], seed: bool | None = None
) -> bool | None:
    # ``seed`` folds in an already-persisted aggregate (see _update_aggregates):
    # error is monotonic under precedence True > False > None, so a partial
    # re-aggregate over a lineage-scoped subset can never lose a True/False the
    # full territory already established.
    saw_true = seed is True
    saw_false = seed is False
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
        # span_id -> Span index (single-trace prototype). Makes `_span_by_id` an
        # O(1) point-lookup; in production this is `WHERE trace_id=? AND span_id=?`.
        self._span_by_id_index: dict[str, Span] = {}
        # Children index, by parent_id (None bucket = roots).
        self.children: dict[str | None, list[Span]] = {}

        # Entities by natural_key (deduped across the trace).
        self.entities: dict[str, Entity] = {}
        # entity_spans rows.
        self.entity_spans: list[EntitySpan] = []
        # Track which entities have a discovered_via row already.
        self._entity_first_span: set[str] = set()

        # Interactions, keyed by primary_anchor_span_id (so late-parent
        # re-eval can find the existing interaction to mutate).
        self.interactions_by_anchor: dict[str, Interaction] = {}
        self.interaction_spans: list[InteractionSpan] = []
        self.payloads: dict[str, Payload] = {}

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

        # State-layer instrumentation (NOT classification): span_ids whose
        # info/connector ownership `_repair_after_arrival` re-derived during this
        # process() call. The DB flush deletes+reinserts exactly this set so the
        # delete-scope matches the in-memory write-scope (issue #70 risk R1) —
        # deleting a broader lineage region would clobber rows a higher-seq span
        # legitimately owns when re-processing an early span against a populated DB.
        self._repaired_span_ids: set[str] = set()

    # ------------------------------------------------------------------
    # Visibility helpers (default-view query facsimile)
    # ------------------------------------------------------------------

    def _is_active_interaction(self, ix: Interaction) -> bool:
        # The production schema has no `retracted_at` (ADR-0012 emit-once-final
        # writes no tombstone), so every emitted interaction is active. Kept as
        # a method so the verbatim classification callers are unchanged.
        return True

    # ------------------------------------------------------------------
    # Public entry
    # ------------------------------------------------------------------

    def process(self, span: Span) -> None:
        """Step 1-9 for a single span. Idempotent on re-process (finalization)."""
        # Step 1: fetch (already done — caller passes the Span).
        key = (span.trace_id, span.span_id)
        is_finalization = key in self.spans_by_id
        self.spans_by_id[key] = span
        self._span_by_id_index[span.span_id] = span
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
        """Emit every edge `span`'s arrival now completes, then repair the
        derived tree/attachment — both SCOPED TO `span`'s LINEAGE (its ancestors,
        itself, and its descendants), never the whole arrived trace.

        Lineage-scoped recompute (ADR-0012, the entity-bounded-region driver). A
        span S's arrival can only change derived state along S's own lineage:

          - EMISSION. S completes a pending endpoint E iff S is a member of E's
            sufficient set, and every sufficient set is {the anchor} ∪
            {ancestors and/or descendants of the anchor}. So S can only complete
            an endpoint anchored at S, at an ancestor of S, or at a descendant of
            S — never a sibling. We retry pending endpoints over exactly that
            lineage set.
          - ATTACHMENT / PARENT. A span's innermost owner and an interaction's
            `parent_interaction_id` are both "nearest enclosing anchor on the
            ancestor chain". S can change those only for spans/interactions in
            S's SUBTREE (S may be a new enclosing anchor for them), plus S itself
            (which needs an owner). Re-derive over S's descendants ∪ {S}.

        This replaces the former full-`spans_by_id` scan on every arrival. The
        reach is exactly ancestors(S) ∪ descendants(S) (no sibling effect — see
        the grill analysis); convergence is preserved because a closer enclosing
        anchor emitting LATER re-derives its own (smaller) region on ITS arrival.
        The root SERVER arriving FIRST under --scramble only owns its topmost
        region; it does not re-scan the trace."""
        lineage = self._lineage_spans(span)
        # --- Emit: retry pending endpoints anchored within `span`'s lineage ---
        anchors_before = set(self.interactions_by_anchor)
        self._retry_pending_endpoints(lineage)
        new_anchor_ids = set(self.interactions_by_anchor) - anchors_before
        # --- Arrival-driven repair ----------------------------------------
        # Repair the union of (a) `span`'s own subtree — where `span` may be a new
        # enclosing anchor — and (b) the subtree of every interaction that JUST
        # emitted in this dispatch. (b) is required because an interaction can be
        # anchored on an ANCESTOR of `span` (e.g. `span` is the OI AGENT span that
        # completes its service's orphan-server / cross-service edge; the edge's
        # anchor is the enclosing root/SERVER). That interaction's territory spans
        # are below ITS anchor, not necessarily below `span`, so re-deriving only
        # `span`'s subtree would leave them unattached under --scramble.
        repair_roots = [span]
        for asid in new_anchor_ids:
            anchor_span = self._span_by_id(asid)
            if anchor_span is not None and anchor_span.span_id != span.span_id:
                repair_roots.append(anchor_span)
        self._repair_after_arrival(repair_roots)

    def _lineage_spans(self, span: Span) -> list[Span]:
        """`span`'s lineage among arrived spans: its ancestor chain, itself, and
        its whole subtree. The set over which `span`'s arrival can complete a
        pending endpoint (a sufficient set is always anchor ∪ ancestors/descendants
        of the anchor)."""
        seen: dict[str, Span] = {}
        # Ancestors + self.
        cur: Span | None = span
        while cur is not None:
            seen[cur.span_id] = cur
            if cur.parent_id is None:
                break
            cur = self.spans_by_id.get((cur.trace_id, cur.parent_id))
        # Descendants (subtree).
        for s in _walk_descendants(self.children, span):
            seen[s.span_id] = s
        return list(seen.values())

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

    def _retry_pending_endpoints(self, candidates: list[Span]) -> None:
        """Re-attempt every not-yet-emitted endpoint among `candidates` — the
        arriving span's lineage (ancestors ∪ self ∪ descendants), NOT the full
        arrived set. An endpoint's sufficient set is always {anchor} ∪
        {ancestors/descendants of the anchor}, so the just-arrived span can only
        complete an endpoint anchored within its own lineage; a sibling endpoint
        is untouched by this arrival and will be (re)tried when one of ITS own
        lineage spans arrives. Emit-once (via `interactions_by_anchor`) makes the
        retry idempotent."""
        for s in candidates:
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
        caller_ident = self._resolve_cross_service_caller(parent)
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

    def _resolve_cross_service_caller(self, parent: Span) -> Identity | None:
        """Caller of a cross-service leg = the NEAREST ENCLOSING ENTITY on
        `parent`'s SAME-SERVICE ancestor walk, not the identity of the service
        that owns `parent` (the outbound CLIENT span). Per ADR-0016 (refining
        ADR-0010): the in-framework delegate's outbound A2A CLIENT POST is a
        child of the delegate's in-process OI TOOL span, so the call issues from
        inside the tool — caller = that tool, exactly as the deployed-MCP tool's
        outbound call (whose CLIENT POST's own service IS the deployed tool)
        already resolved to that deployed tool.

        The walk:
          - climbs `parent`'s ancestors while they stay on `parent`'s service;
          - an enclosing OI TOOL span ⟹ caller = that tool's identity, built
            STRUCTURALLY via `_classify_oi_endpoint` (the same construction
            `_emit_oi` uses), NOT a lookup in `_tool_anchor_entity_by_span` —
            so the result does not depend on whether the tool's own
            `agent→tool` edge has emitted yet (order-independent). If that
            classification is still undecided (the tool's transport subtree
            hasn't arrived), return None to DEFER — the per-arrival retry
            re-emits once it decides, rather than freezing an agent-caller;
          - an enclosing OI LLM span ⟹ the egress is the LLM's own gateway
            transport (absorbed by the `agent→llm` edge); an `llm` is never a
            cross-service caller, so stop climbing and fall back to the
            service-side identity (the owning agent);
          - leaving `parent`'s service (a cross-service boundary upward) ends
            the walk: fall back to the service-side identity of `parent` (the
            owning agent, or a deployed `tool:` when `parent` lives on an mcp
            service — the latter is the create_booking→payment case, preserved).
        """
        cur = parent
        while cur.parent_id:
            anc = self.spans_by_id.get((cur.trace_id, cur.parent_id))
            if anc is None:
                break
            if anc.service_name != parent.service_name:
                break  # left this service — fall back to service-side identity
            if _is_oi_kind(anc, "TOOL"):
                # Defer (None) while undecided rather than freeze an agent-caller.
                return self._classify_oi_endpoint(anc)
            if _is_oi_kind(anc, "LLM"):
                break  # LLM-gateway transport — never a cross-service caller
            cur = anc
        return self._resolve_service_side_identity(parent)

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
        """external-http edge anchored on `client`: look UP for the INNERMOST
        enclosing OI endpoint and, if it is a TOOL (and the destination host
        qualifies), emit `tool → service:<host>`. Both `client` and its OI TOOL
        ancestor are in the arrived set when this runs (retried every arrival),
        so it is order-independent — no separate on-tool entry needed.

        The egress belongs to its innermost enclosing OI endpoint: if that is an
        OI LLM span, the CLIENT is the LLM call's own gateway transport (e.g. the
        `ete-litellm` `/v1/chat/completions` egress under a `generation` LLM
        span) — absorbed by the agent→llm edge, NOT external-http. Stopping at the
        nearest OI LLM/TOOL ancestor is a LINEAGE-LOCAL replacement for the former
        trace-global LLM-gateway host exclusion: an LLM-gateway egress is
        recognised by its enclosing LLM span, not by collecting every LLM host in
        the trace."""
        if self._external_http_host(client) is None:
            return
        cur = client
        while cur.parent_id:
            parent = self.spans_by_id.get((cur.trace_id, cur.parent_id))
            if parent is None:
                break
            if _is_oi_kind(parent, "TOOL"):
                self._emit_external_http(client=client, tool_span=parent)
                return
            if _is_oi_kind(parent, "LLM"):
                # Innermost OI endpoint is an LLM — this egress is LLM-gateway
                # transport, absorbed. Do not climb past it to an outer TOOL.
                return
            # Stop at an A2A SUB-AGENT boundary, but NOT at a `/mcp` tool-transport
            # boundary. Climbing must reach the OI TOOL that OWNS this egress:
            #   - A genuine external-http egress originates inside a DEPLOYED MCP
            #     tool's own service (e.g. `charge_card`) and its owning OI TOOL
            #     span lives in the CALLING agent's service (`payment-agent`),
            #     reached UP THROUGH the `/mcp` transport SERVER. That boundary
            #     crossing is legitimate — keep climbing.
            #   - A sub-agent's OWN egress (e.g. research-agent's `ete-litellm`
            #     LLM call) crosses an A2A `POST /` SERVER into the parent service;
            #     climbing past it would wrongly attribute the egress to the
            #     parent's delegate tool. Stop there.
            # Discriminator: we are about to climb OUT of `cur` into `parent` on a
            # different service. If `cur` is an A2A sub-agent inbound SERVER (a
            # non-`/mcp` `POST /`), that crossing is a sub-agent boundary → stop.
            # A `/mcp` transport SERVER crossing (the deployed-tool case) is fine.
            cur_canon = canonical_service_name(cur)
            par_canon = canonical_service_name(parent)
            crossing_service = (
                cur_canon is not None
                and par_canon is not None
                and cur_canon != par_canon
            )
            if (
                crossing_service
                and cur.kind == "SERVER"
                and not (cur.name or "").upper().startswith("POST /MCP")
            ):
                return
            cur = parent

    # ------------------------------------------------------------------
    # Step 3: entity evidence
    # ------------------------------------------------------------------

    def _upsert_entity(
        self, identity: Identity, span: Span, role: str
    ) -> Entity:
        e = self.entities.get(identity.natural_key)
        if e is None:
            e = Entity(
                id=_entity_id(identity.natural_key),
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
                EntitySpan(e.id, span.trace_id, span.span_id, "discovered_via")
            )
            self._entity_first_span.add(e.id)
        elif role == "identified_via":
            self.entity_spans.append(
                EntitySpan(e.id, span.trace_id, span.span_id, "identified_via")
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
        # A service that owns an OpenInference AGENT-kind span IS an agent, and
        # its identity is sourced from that AGENT span. Lineage-scoped: the AGENT
        # span is a DESCENDANT of this service's inbound SERVER span (the agent
        # framework runs inside the request handler), so we walk `span`'s subtree
        # rather than scanning all arrived spans. If no AGENT span has arrived in
        # the subtree yet, we cannot finally decide the identity, so emit nothing.
        agent_ident = self._agent_identity_in_lineage(span)
        if agent_ident is not None:
            return agent_ident
        # `span` itself carries the framework marker (e.g. resolving a parent
        # OI span's service): fall through to the canonical agent/tool identity.
        if _is_oi_kind(span, "AGENT", "LLM", "CHAIN", "TOOL"):
            return caller_inference._agent_or_deployed_tool_from_service(span)
        return None

    def _agent_identity_in_lineage(self, span: Span) -> Identity | None:
        """Build the `agent:(project,service)` Identity for `span`'s service from
        the OI AGENT span on the SAME service found in `span`'s LINEAGE — its own
        subtree (the callee-SERVER case: the agent framework runs inside the
        inbound request handler, so its AGENT span is a descendant) or its
        ancestor chain (the caller case: a CLIENT egress is enclosed by its
        caller agent's AGENT span). Lineage-scoped, order-independent: the result
        is sourced from the AGENT span, not from the triggering span.

        Both walks stay on the same service and stop at a cross-service SERVER
        boundary, so a nested sub-agent's AGENT span is never claimed. Per the
        AGENT-in-lineage structural invariant on the gate trace: a service's
        AGENT span is always an ancestor-or-descendant of any same-service span
        that needs the service's identity.

        Returns None when no same-service AGENT is reachable in `span`'s lineage
        yet; the per-arrival retry re-attempts once the connecting spans arrive.
        We deliberately do NOT assert the invariant here — "no AGENT in this
        lineage" is an absence conclusion, undecidable per-arrival under streaming
        (an entry SERVER can arrive before the spans connecting it to an
        already-arrived AGENT), and asserting it re-introduces the order-dependent
        negative conclusion ADR-0012 removes."""
        found = self._first_same_service_agent_in_subtree(span)
        if found is None:
            found = self._first_same_service_agent_in_ancestors(span)
        if found is not None:
            return caller_inference._agent_or_deployed_tool_from_service(found)
        # No same-service AGENT reachable in `span`'s lineage YET. We deliberately
        # emit NO instrumentation-signal here: "no AGENT will ever appear in this
        # lineage" is a NEGATIVE/absence conclusion with no triggering event, and
        # under streaming it is genuinely undecidable per-arrival — an entry SERVER
        # can arrive before the intermediate spans that connect it to an
        # already-arrived AGENT, transiently breaking the lineage path without any
        # invariant being violated. Asserting it here re-introduces exactly the
        # order-dependent negative conclusion ADR-0012 removes (the --scramble gate
        # flags it immediately). The per-arrival retry re-attempts once the
        # connecting spans arrive; the structural output is the proof the walk is
        # correct on the complete trace.
        return None

    def _first_same_service_agent_in_subtree(self, root: Span) -> Span | None:
        """First OI AGENT span on `root`'s service within `root`'s subtree, not
        descending past a cross-service SERVER boundary."""
        root_canon = canonical_service_name(root)
        stack = list(self.children.get(root.span_id, []))
        while stack:
            s = stack.pop()
            # Stop descent at a nested sub-agent's own territory.
            if (
                s.kind == "SERVER"
                and canonical_service_name(s) not in (None, root_canon)
            ):
                continue
            if s.service_name == root.service_name and _is_oi_kind(s, "AGENT"):
                return s
            stack.extend(self.children.get(s.span_id, []))
        return None

    def _first_same_service_agent_in_ancestors(self, span: Span) -> Span | None:
        """First OI AGENT span on `span`'s service found by walking `span`'s
        ancestor chain, stopping when the chain leaves `span`'s canonical service
        (a cross-service boundary upward)."""
        span_canon = canonical_service_name(span)
        cur: Span | None = span
        while cur is not None:
            if cur.service_name == span.service_name and _is_oi_kind(cur, "AGENT"):
                return cur
            if cur.parent_id is None:
                break
            parent = self.spans_by_id.get((cur.trace_id, cur.parent_id))
            if parent is None:
                break
            # Stop at the boundary where the chain leaves this service.
            p_canon = canonical_service_name(parent)
            if p_canon is not None and span_canon is not None and p_canon != span_canon:
                break
            cur = parent
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

    def _matching_mcp_tool_span_in_subtree(self, span: Span) -> Span | None:
        """The `/mcp` SERVER in `span`'s OWN subtree whose canonical service name
        matches this tool's logical name. Lineage-scoped replacement for the
        former trace-global scan: a deployed-MCP tool's `/mcp` transport SERVER is
        reached only via the CLIENT egress beneath its own OI TOOL span, so the
        matching SERVER is always a descendant of `span`."""
        logical = _tool_logical_name(span)
        for s in _walk_descendants(self.children, span):
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
        return self._span_by_id_index.get(span_id)

    # ------------------------------------------------------------------
    # Step 5c: attach spans
    # ------------------------------------------------------------------

    def _attach_span(self, ix: Interaction, span_id: str, role: str) -> None:
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
            InteractionSpan(ix.id, ix.trace_id, span_id, role)
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

    def _innermost_owner_for(self, span: Span) -> Interaction | None:
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

    def _repair_after_arrival(self, roots: list[Span]) -> None:
        """Re-derive the two arrival-order-dependent quantities — `interaction_
        span` ownership (info/connector) and `parent_interaction_id` — SCOPED to
        the union of `roots`' SUBTREES, not the whole trace. `roots` is the
        arriving span plus the anchors of any interactions that just emitted in
        this dispatch (see `_dispatch`).

        Both quantities are "nearest enclosing anchor on the ancestor chain", so
        an arrival can only change them for spans/interactions in the subtree of
        the arriving span (a new enclosing anchor) or in the subtree of a
        just-emitted interaction's anchor (its territory). A span outside every
        such subtree has the same ancestor chain it had before, so its owner and
        any interaction it anchors are unchanged — no need to re-touch them.

        Convergence under --scramble is preserved without a full scan: if a span
        later gets a CLOSER enclosing anchor (an inner interaction emitting
        afterwards), that inner interaction's anchor is a root in ITS dispatch and
        re-derives its (smaller) subtree region, stealing the span to the tighter
        owner. Anchor rows are emit-once and never touched."""
        # 1. Re-derive non-anchor ownership for the union of the roots' subtrees:
        #    each span goes to its innermost current owner (clears any prior,
        #    looser claim).
        seen: dict[str, Span] = {}
        for root in roots:
            for s in _walk_descendants(self.children, root):
                seen[s.span_id] = s
        subtree = list(seen.values())
        # Record the re-derived region for the DB flush (state-layer only — the
        # set this pass may reassign info/connector ownership over).
        self._repaired_span_ids.update(seen.keys())
        for s in subtree:
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
        # 2. Re-point parent_interaction_id for interactions anchored in `span`'s
        #    subtree (where `span` may be a new enclosing parent). Collect the
        #    touched interactions for the aggregate pass.
        subtree_ids = {s.span_id for s in subtree}
        touched: list[Interaction] = []
        for asid, ix in self.interactions_by_anchor.items():
            if not self._is_active_interaction(ix):
                continue
            if asid in subtree_ids:
                ix.parent_interaction_id = self._compute_parent_interaction(
                    ix.primary_anchor_span_id
                )
                touched.append(ix)
        # 3. Re-aggregate the interactions whose territory may have changed:
        #    those anchored in the subtree, plus the owners of every span we just
        #    (re)attached above.
        for s in subtree:
            owner_id = self._owners_by_span.get(s.span_id)
            if owner_id is not None:
                ix = self._interaction_by_id(owner_id)
                if ix is not None and ix not in touched:
                    touched.append(ix)
        for ix in touched:
            if self._is_active_interaction(ix):
                self._update_aggregates(ix)

    def _interaction_by_id(self, ix_id: str) -> Interaction | None:
        for ix in self.interactions_by_anchor.values():
            if ix.id == ix_id:
                return ix
        return None

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

    def _update_aggregates(self, ix: Interaction) -> None:
        attached_ids = self._attached_span_ids.get(ix.id, set())
        # Read only this interaction's attached spans (bounded by its territory),
        # via the span_id index — not a scan of the whole arrived trace.
        #
        # FOLD, don't recompute. Under the DB-backed driver the span index holds
        # only the arriving span's lineage (state.rehydrate), while attached_ids
        # is the interaction's FULL territory — so any territory span outside the
        # current lineage is absent from the index here. Recomputing min/max/error
        # from just the visible subset would NARROW the persisted window (and could
        # flip error True->False) purely by arrival order. Instead we fold the
        # visible spans INTO ix's already-persisted started_at/ended_at/error, so an
        # out-of-lineage span is represented by the value the DB already holds. The
        # fold is monotonic and idempotent: the in-memory (single-pass) drain, where
        # every attached span is present, converges to the same result.
        attached_spans = [
            s
            for sid in attached_ids
            if (s := self._span_by_id_index.get(sid)) is not None
        ]
        starts = [s.started_at for s in attached_spans if s.started_at is not None]
        if ix.started_at is not None:
            starts.append(ix.started_at)
        if starts:
            ix.started_at = min(starts)
        ended = [s.ended_at for s in attached_spans if s.ended_at is not None]
        if ix.ended_at is not None:
            ended.append(ix.ended_at)
        if ended:
            ix.ended_at = max(ended)
        # error precedence True > False > None, seeded with the persisted value so
        # a later partial re-aggregate can never un-set a True observed earlier.
        ix.error = _aggregate_error(attached_spans, seed=ix.error)

    # ------------------------------------------------------------------
    # Step 8: payload extraction
    # ------------------------------------------------------------------

    def _extract_payloads(self, ix: Interaction, anchor: Span) -> None:
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

        POSITIVE-ANCHORED (ADR-0012): every gate is a CLIENT-LOCAL or
        DIRECT-CHILD signal — none scans the whole trace for a negative
        ("this host is owned by no other entity"). This is correct because the
        edge is anchored on the OI TOOL span, and a leaf tool egress can have no
        future SERVER child, so the arrived-set view is final at the CLIENT
        span's arrival:

          1. the egress URL path is not a well-known non-business convention —
             `/mcp` (deployed-tool MCP transport) or `.well-known/*` (agent-card
             probe and friends). Both are CLIENT-local URL signals.
          2. no in-trace SERVER child (the callee is uninstrumented — a direct-
             child check; an instrumented agent/tool/service egress has a SERVER
             child and is absorbed as a cross-service edge instead).
          3. host resolves via `_http_host`.

        The former trace-global owned-host exclusions (`_llm_gateway_hosts`,
        `_non_service_hosts`, and the `known_canonicals` membership test) are
        DROPPED: gate 1 + gate 2 already absorb every non-business egress on the
        gate trace. The LLM-gateway exclusion is structurally redundant —
        external-http only fires under an OI TOOL ancestor
        (`_emit_external_http_on_client`), but an LLM-gateway egress is a child
        of an OI LLM span, never an OI TOOL span, so it is never reached here.
        See `proto-lineage-scoped-rewrite-target` and the exclusion-gap caveat.
        """
        # Gate 1: well-known non-business egress paths are never external-http.
        # `/mcp` is the deployed-tool MCP transport (absorbed by the OI TOOL
        # edge); `.well-known/*` is the agent-card probe (and similar discovery
        # endpoints), which has no SERVER child so gate 2 cannot catch it.
        url = caller_inference._attr(client, "http.url") or caller_inference._attr(
            client, "url.full"
        )
        if isinstance(url, str):
            from urllib.parse import urlparse

            path = (urlparse(url).path or "").rstrip("/")
            if path.endswith("/mcp") or "/.well-known/" in (path + "/"):
                return None
            # Liveness/readiness probes are infrastructure noise, not business
            # interactions — but the external-http rule fires once per CLIENT
            # egress, so an unfiltered `GET /healthz` to a host surfaces as a
            # duplicate `tool → service` interaction alongside the real business
            # call to the same host. Exclude the well-known probe paths by their
            # OWN final path segment — a POSITIVE, CLIENT-LOCAL signal in the
            # same family as the `/mcp` and `/.well-known/*` gates above, so it
            # keeps the order-independence guarantee (decided from the egress's
            # own URL, never from a sibling's presence/absence). Exact segment
            # match (not substring) so a business path like `/healthcheckups`
            # is not mis-declined. Exclusion-gap caveat (ADR-0012 stance): a real
            # business endpoint genuinely mounted at one of these paths would be
            # mis-declined; revisit if such a fixture appears.
            last_segment = path.rsplit("/", 1)[-1]
            if last_segment in _PROBE_PATHS:
                return None
            # Gate 0 (A2A agent-call shape): a CLIENT POST to the bare root path
            # `/` is an A2A sub-agent invocation (the a2a SDK posts JSON-RPC to
            # the agent's root), never an uninstrumented external HTTP service.
            # It is the cross-service rule's job (its `POST /` SERVER child
            # resolves to an `agent:`), so external-http must decline it. This is
            # a POSITIVE, CLIENT-LOCAL signal: it decides on the egress's OWN URL,
            # not on the ABSENCE of a (possibly late-arriving) SERVER child — so
            # it is order-independent, where gate 2 alone is not. Without it, an
            # A2A egress whose SERVER child has not yet arrived (e.g.
            # `create_booking → payment-agent:8080/` originating inside an MCP
            # tool's service, reached up through the `/mcp` boundary) wrongly
            # fires external-http under emit-once-final and never re-derives.
            # Exclusion-gap caveat (same stance as ADR-0012's dropped rungs): a
            # genuine external service mounted at the bare root path would be
            # mis-declined here; revisit when such a fixture exists. The `method`
            # check keeps GET-root probes from matching the POST-only convention.
            method = (caller_inference._attr(client, "http.method") or "").upper()
            if path == "" and method == "POST":
                return None
        # Gate 2: an instrumented callee (agent / tool / service) emits a SERVER
        # span as a direct child of this CLIENT egress — absorbed as cross-service.
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
        """Classify an OI TOOL span's transport by the NEAREST SERVER it reaches
        on each downward branch — the order-independent deployed-vs-in-process
        discriminator (ADR-0012):

          - a `POST /mcp` SERVER  ⟹ "deployed" (MCP `tools/call` transport);
          - any other SERVER on a DIFFERENT service (a sub-agent's `POST /`,
            reached via the A2A CLIENT) ⟹ "in-process" (a delegate FunctionTool);
          - no SERVER reached yet ⟹ None — undecided, so `_emit_oi` DEFERS and
            the per-arrival retry re-attempts once the transport subtree streams
            in. Both decisive answers are POSITIVE (a SERVER that arrived), so
            the classification converges regardless of arrival order; only the
            "neither has arrived yet" gap defers, never a negative conclusion.

        The walk stops descending at the FIRST SERVER on each branch — the tool's
        OWN transport boundary. It must NOT look deeper: the nearest SERVER is the
        tool's transport, but the spans BELOW it belong to the callee's territory.
        A delegate FunctionTool's nearest SERVER is its A2A sub-agent's `POST /`
        (in-process); that sub-agent then makes its OWN downstream `/mcp` tool
        calls, whose `POST /mcp` SERVERs sit DEEPER in the subtree. The previous
        "a `/mcp` SERVER anywhere in the subtree ⟹ deployed (deployed wins)" rule
        mis-attributed those downstream `/mcp` calls to the delegate tool, so
        `delegate_to_booking_agent` flipped to a deployed `create_booking`
        depending on which SERVERs had arrived — an order-dependence the
        --scramble gate catches. Classifying on the FRONTIER (nearest) SERVERs
        alone separates the two cleanly without a global precedence rule."""
        # Frontier SERVERs: the nearest SERVER on each downward branch. Stop
        # descending once a branch hits a SERVER (everything below it is the
        # callee's territory, not this tool's transport).
        frontier: list[Span] = []
        stack = list(self.children.get(tool_span.span_id, []))
        while stack:
            s = stack.pop()
            if s.kind == "SERVER":
                frontier.append(s)
                continue  # do not descend past the transport boundary
            stack.extend(self.children.get(s.span_id, []))
        # A `/mcp` frontier SERVER is this tool's own MCP transport ⟹ deployed.
        if any((s.name or "").upper().startswith("POST /MCP") for s in frontier):
            return "deployed"
        # A frontier SERVER on a DIFFERENT service is the A2A sub-agent a delegate
        # FunctionTool consulted — positive in-process marker.
        if any(
            s.service_name and s.service_name != tool_span.service_name
            for s in frontier
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
                # Source the deployed-tool identity from the `/mcp` SERVER in
                # this OI TOOL span's OWN subtree (lineage-scoped). Prefer the
                # SERVER whose canonical name matches this tool's logical name
                # (`_matching_mcp_tool_span` did this trace-globally; the deployed
                # tool's `/mcp` transport is always under its own OI TOOL span, so
                # the subtree walk finds the same SERVER); fall back to the first
                # `/mcp` SERVER in the subtree.
                mcp_server = self._matching_mcp_tool_span_in_subtree(span)
                if mcp_server is not None:
                    return deployed_tool_identity(mcp_server)
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
        caller_entity: Entity,
        callee_entity: Entity,
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

        ix = Interaction(
            id=_interaction_id(primary_span.trace_id, primary_span.span_id),
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

