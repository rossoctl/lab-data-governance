"""Per-span 9-step procedure for P-interactions.

THROWAWAY prototype (still batch — consumes a list of spans in seq order).
The procedure shape is what will run inside one transaction per span when
the streaming driver lands.

Per Q14 / ADR-0007, the per-span work is:

  1. Fetch the span (provided as input here).
  2. Resolve in-trace parent from local state (None if late).
  3. Identify entities the span evidences (callee for SERVER, caller for
     CLIENT, the LLM/tool entity for OI spans, etc.). Upsert into the
     entity index; record entity_spans rows.
  4. Fire all five anchor rules; gather AnchorDecisions.
  5. For each decision, build or mutate an interaction:
       a. Resolve caller + callee identities.
       b. Compute parent_interaction_id by walking the primary anchor's
          parent chain (ADR-0008).
       c. Attach all spans the rule covers as interaction_spans (anchor /
          info / connector roles).
  6. Late-parent re-evaluation: if this span is the parent of a previously-
     anchored orphan-server interaction, re-fire cross-service on the child
     (the orphan's anchor span) and demote/swap the interaction's caller
     entity if a better one is now derivable.
  6b. Interaction reconciliation (ADR-0011): pair newly-arrived OI LLM
      spans with descendant CLIENT POSTs, or newly-arrived CLIENT POSTs
      with ancestor OI LLM interactions. Sibling-case fallback when
      no descendant pairing matches.
  7. Aggregate updates: started_at / ended_at / error / payload hashes on
     interactions whose evidence set changed.
  8. Payload extraction: per attached span, pull request/response payloads
     (LLM messages, tool input.value/output.value, HTTP bodies). Hash and
     dedup.
  9. Cursor advance (for the prototype: just record last seq seen).

State lives in-memory as Python dicts. The CLI driver flushes at the end.

ADR-0011 model (in-memory facsimile of the production schema):

  - `retracted_at` is a tombstone column on ProtoInteraction and ProtoEntity.
    Default views filter `retracted_at IS None`. `_active_*` helpers are
    the prototype's stand-in for the default-view query.
  - `original_seq` is preserved on creation; mirrors ADR-0004's
    `arrival_seq` one layer up.
  - `_owners_by_span` is the in-memory facsimile of the
    `UNIQUE (trace_id, span_id)` constraint on `interaction_spans`. Each
    (trace_id, span_id) maps to at most one non-retracted interaction.
    Anchor-rule firing checks this before emitting; reconciliation
    transfers ownership in the same transaction it retracts.
  - Reconciliation queries the in-memory state (which would be the
    durable `interactions`/`spans` tables in production) per span
    arrival; the lookup shape is per-span, not scan-all.
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import hashlib
import json
import uuid
from typing import Any

from data_governance.retrieval import Span

from . import anchor_rules, caller_inference
from .anchor_rules import AnchorDecision
from .caller_inference import (
    Identity,
    canonical_service_name,
    deployed_tool_identity,
    in_process_tool_identity,
    infer_caller_for_orphan_server,
    llm_identity,
    service_identity_from_client,
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

        # SERVER spans whose parent_id is set but parent hasn't arrived yet.
        # Re-processed when the parent shows up; if still unresolvable at
        # `result()` time, they're treated as genuine orphan-server anchors.
        self._deferred_spans: set[str] = set()

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

        # Step 3: lightweight discovery only — no automatic agent/tool
        # registration. Entities are pulled in by anchor handlers.

        # Step 4: fire anchor rules. Don't fire orphan-server speculatively
        # for SERVER spans whose parent_id is set but parent is not yet seen.
        in_trace_children = list(self.children.get(span.span_id, []))
        if (
            span.kind == "SERVER"
            and span.parent_id is not None
            and parent is None
            and not is_finalization
        ):
            self._deferred_spans.add(span.span_id)
        else:
            decisions = anchor_rules.fire_all(
                span, parent, in_trace_children, self.known_canonicals
            )
            for d in decisions:
                self._handle_decision(d, span, parent)

        # Step 6: late-parent re-evaluation.
        self._reevaluate_late_children(span)
        self._reprocess_resolved_deferred(span)

        # Step 6b: ADR-0011 reconciliation — per-newly-arrived-span pairing
        # search (D15). Replaces the old scan-all-every-tick approach.
        self._reconcile_on_span_arrival(span)

        # Step 7-8: aggregate updates + payloads — inline.

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
            # Reviving a previously-retracted entity (e.g. unresolved-LLM
            # entity that gets a new attached interaction). Production
            # would create a fresh row; here we lift the tombstone and
            # bump seq.
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
    # ADR-0011 reconciliation: per-newly-arrived-span pairing (D15)
    # ------------------------------------------------------------------

    def _reconcile_on_span_arrival(self, span: Span) -> None:
        """ADR-0011 §2 LLM/HTTP-transport pairing.

        Two structural triggers, dispatched by the just-arrived span's shape:

          - CLIENT POST: walk ancestors. If exactly one ancestor anchors an
            unresolved (or already-host-known) OI LLM interaction, pair.
          - OI LLM: scan already-arrived descendant spans for CLIENT POSTs
            anchoring an `external-http` interaction. If exactly one, pair.
          - Sibling-case fallback: when no descendant pairing matches and
            the just-arrived span is one of the two shapes above.

        Pairing is structural-first; the sibling case is consulted only
        when no descendant match exists. On multi-match in the sibling
        case, fail closed.

        ADR-0011 §6: lookups go against the durable `interactions`/`spans`
        tables. The prototype's in-memory dicts (`interactions_by_anchor`,
        `spans_by_id`) are the in-memory facsimile.
        """
        from .caller_inference import _http_host

        # CLIENT POST trigger
        if span.kind == "CLIENT" and _http_host(span):
            self._reconcile_client_post(span)
            return

        # OI LLM trigger
        if _is_oi_kind(span, "LLM"):
            self._reconcile_oi_llm(span)
            return

    def _reconcile_client_post(self, client_span: Span) -> None:
        """CLIENT POST arrived. Walk ancestors; pair against an OI LLM
        interaction anchored on an ancestor span."""
        from .caller_inference import _http_host

        host = _http_host(client_span)
        if not host:
            return

        # Walk ancestors collecting OI LLM interactions.
        candidates: list[ProtoInteraction] = []
        cur = client_span
        while cur.parent_id:
            parent = self.spans_by_id.get((cur.trace_id, cur.parent_id))
            if parent is None:
                break
            ix = self.interactions_by_anchor.get(parent.span_id)
            if (
                ix is not None
                and ix.anchor_rule == "openinference-llm"
                and self._is_active_interaction(ix)
            ):
                candidates.append(ix)
            cur = parent

        if len(candidates) == 1:
            self._pair_llm_with_client(candidates[0], client_span, host)
            return
        if len(candidates) > 1:
            # Multiple OI LLM ancestors — fail closed.
            return

        # No descendant/ancestor structural match; try sibling fallback.
        self._reconcile_sibling_fallback(client_span, host)

    def _reconcile_oi_llm(self, llm_span: Span) -> None:
        """OI LLM arrived. Scan already-arrived descendants for a CLIENT
        POST anchoring an `external-http` interaction."""
        from .caller_inference import _http_host

        ix = self.interactions_by_anchor.get(llm_span.span_id)
        if ix is None or not self._is_active_interaction(ix):
            return
        if ix.anchor_rule != "openinference-llm":
            return

        # Descendant scan
        candidates: list[tuple[Span, str]] = []
        for ev in _walk_descendants(self.children, llm_span):
            if ev.span_id == llm_span.span_id:
                continue
            if ev.kind != "CLIENT":
                continue
            host = _http_host(ev)
            if not host:
                continue
            ext_ix = self.interactions_by_anchor.get(ev.span_id)
            if (
                ext_ix is not None
                and ext_ix.anchor_rule == "external-http"
                and self._is_active_interaction(ext_ix)
            ):
                candidates.append((ev, host))

        if len(candidates) == 1:
            client_span, host = candidates[0]
            self._pair_llm_with_client(ix, client_span, host)
            return
        if len(candidates) > 1:
            # Ambiguous descendant — fail closed.
            return

        # No descendant match; try sibling fallback for *this* OI LLM.
        self._reconcile_sibling_fallback_for_llm(ix, llm_span)

    def _reconcile_sibling_fallback(self, client_span: Span, host: str) -> None:
        """A CLIENT POST arrived without a structural OI LLM ancestor.
        Look for an OI LLM on the same canonical-service whose time-window
        uniquely contains the CLIENT.

        On multi-match (parallel calls), fail closed.
        """
        if client_span.started_at is None or client_span.ended_at is None:
            return
        svc = client_span.service_name
        if not svc:
            return

        containing_llms: list[ProtoInteraction] = []
        for ix in self._active_interactions():
            if ix.anchor_rule != "openinference-llm":
                continue
            llm_span = self._span_by_id(ix.primary_anchor_span_id)
            if llm_span is None or llm_span.service_name != svc:
                continue
            if llm_span.started_at is None or llm_span.ended_at is None:
                continue
            if (
                client_span.started_at >= llm_span.started_at
                and client_span.ended_at <= llm_span.ended_at
            ):
                containing_llms.append(ix)
        if len(containing_llms) != 1:
            return
        # Uniqueness check across other LLMs on the same service.
        winner = containing_llms[0]
        winner_span = self._span_by_id(winner.primary_anchor_span_id)
        if winner_span is None:
            return
        rivals = 0
        for ix in self._active_interactions():
            if ix.id == winner.id:
                continue
            if ix.anchor_rule != "openinference-llm":
                continue
            other_span = self._span_by_id(ix.primary_anchor_span_id)
            if other_span is None or other_span.service_name != svc:
                continue
            if other_span.started_at is None or other_span.ended_at is None:
                continue
            if (
                client_span.started_at >= other_span.started_at
                and client_span.ended_at <= other_span.ended_at
            ):
                rivals += 1
        if rivals > 0:
            return
        self._pair_llm_with_client(winner, client_span, host)

    def _reconcile_sibling_fallback_for_llm(
        self, ix: ProtoInteraction, llm_span: Span
    ) -> None:
        """An OI LLM arrived. Look for a sibling CLIENT POST on the same
        canonical-service whose time-window is uniquely contained in this
        LLM's window, and that no other in-service LLM also contains.
        """
        from .caller_inference import _http_host

        if llm_span.started_at is None or llm_span.ended_at is None:
            return
        svc = llm_span.service_name
        if not svc:
            return

        # Candidate CLIENT POSTs on this service that are contained in our window.
        contained: list[tuple[Span, str]] = []
        for s in self.spans_by_id.values():
            if s.kind != "CLIENT" or s.service_name != svc:
                continue
            if s.started_at is None or s.ended_at is None:
                continue
            host = _http_host(s)
            if not host:
                continue
            if s.started_at >= llm_span.started_at and s.ended_at <= llm_span.ended_at:
                contained.append((s, host))
        if len(contained) != 1:
            return
        client_span, host = contained[0]

        # Uniqueness: no other in-service LLM also contains this CLIENT.
        rivals = 0
        for other_ix in self._active_interactions():
            if other_ix.id == ix.id:
                continue
            if other_ix.anchor_rule != "openinference-llm":
                continue
            other = self._span_by_id(other_ix.primary_anchor_span_id)
            if other is None or other.service_name != svc:
                continue
            if other.started_at is None or other.ended_at is None:
                continue
            if (
                client_span.started_at >= other.started_at
                and client_span.ended_at <= other.ended_at
            ):
                rivals += 1
        if rivals > 0:
            return
        self._pair_llm_with_client(ix, client_span, host)

    # ------------------------------------------------------------------
    # Pairing: the actual mutation
    # ------------------------------------------------------------------

    def _pair_llm_with_client(
        self,
        llm_ix: ProtoInteraction,
        client_span: Span,
        host: str,
    ) -> None:
        """Implement ADR-0011 §2 steps 1-5 for a confirmed pairing.

          1. Retarget the OI LLM's callee onto `llm:<host>/<model>`.
          2. Destructively retract the `external-http` interaction
             anchored on the CLIENT POST.
          3. Transfer the CLIENT POST span ownership to the surviving OI
             LLM interaction. Role from `_has_payload_or_error()` (D5).
          4. Repair the interaction tree for children of the retracted
             external-http interaction (D4).
          5. GC orphaned entities: the now-empty unresolved LLM entity,
             the orphan `service:<host>` (D7 universal retract).
        """
        if self._owners_by_span.get(client_span.span_id) == llm_ix.id:
            return  # ADR-0011: already paired this OI LLM with this CLIENT POST

        # Step 1: per-interaction retarget (D2)
        callee_entity = self._entity_by_id(llm_ix.callee_entity_id)
        if callee_entity is None:
            return

        unknown_nk_prefix = "llm:(unknown)/"
        is_unknown = callee_entity.natural_key.startswith(unknown_nk_prefix)
        if is_unknown:
            model = callee_entity.natural_key[len(unknown_nk_prefix):]
            target_nk = f"llm:{host}/{model}"
            target = self._upsert_or_revive_llm_entity(
                target_nk, host, model, client_span
            )
            self._retarget_interaction_callee(llm_ix, target, client_span)
        else:
            target = callee_entity
            # entity_spans: record the CLIENT as identified_via on the
            # already-resolved LLM entity.
            self.entity_spans.append(
                ProtoEntitySpan(
                    target.id, client_span.trace_id, client_span.span_id, "identified_via"
                )
            )

        # Step 2: retract the external-http interaction on this CLIENT span.
        ext_ix = self.interactions_by_anchor.get(client_span.span_id)
        external_callee_entity_id: str | None = None
        children_to_repair: list[ProtoInteraction] = []
        if ext_ix is not None and self._is_active_interaction(ext_ix):
            external_callee_entity_id = ext_ix.callee_entity_id
            children_to_repair = [
                ix
                for ix in self.interactions_by_anchor.values()
                if ix.parent_interaction_id == ext_ix.id
                and self._is_active_interaction(ix)
            ]
            self._retract_interaction(ext_ix, client_span.seq)

        # Step 3: transfer the CLIENT POST onto the surviving LLM interaction
        # with role derived from payload/error presence (D5).
        role = "info" if self._has_payload_or_error(client_span) else "connector"
        self._attach_span(llm_ix, client_span.span_id, role)

        # Step 4: tree repair (D4).
        for child in children_to_repair:
            child.parent_interaction_id = self._compute_parent_interaction(
                child.primary_anchor_span_id
            )
            child.seq = max(child.seq, client_span.seq)

        # Step 5: GC orphaned entities (D7 universal destructive retract).
        if is_unknown:
            self._gc_entity_if_orphan(callee_entity, client_span.seq)
        if external_callee_entity_id is not None:
            ext_callee = self._entity_by_id(external_callee_entity_id)
            if ext_callee is not None:
                self._gc_entity_if_orphan(ext_callee, client_span.seq)

        self._update_aggregates(llm_ix)
        self.notes.append(
            f"reconciled OI LLM (anchor {llm_ix.primary_anchor_span_id}) with "
            f"CLIENT {client_span.span_id} -> {target.natural_key}"
        )

    def _upsert_or_revive_llm_entity(
        self, target_nk: str, host: str, model: str, source_span: Span
    ) -> ProtoEntity:
        target = self.entities.get(target_nk)
        if target is None:
            target = ProtoEntity(
                id=str(uuid.uuid4()),
                kind="llm",
                natural_key=target_nk,
                display_name=f"{model} @ {host}",
                project_name=None,
                detected_from="resolved via paired CLIENT POST (ADR-0011)",
                first_seen_seq=source_span.seq,
                seq=source_span.seq,
                original_seq=source_span.seq,
            )
            self.entities[target_nk] = target
            self.entity_spans.append(
                ProtoEntitySpan(
                    target.id, source_span.trace_id, source_span.span_id, "discovered_via"
                )
            )
            self._entity_first_span.add(target.id)
        elif target.retracted_at is not None:
            target.retracted_at = None
            target.seq = max(target.seq, source_span.seq)
        return target

    def _retarget_interaction_callee(
        self,
        ix: ProtoInteraction,
        new_callee: ProtoEntity,
        evidence_span: Span,
    ) -> None:
        """ADR-0011 §2 step 1: per-interaction retarget. Mutates ix's
        callee_entity_id only — does NOT loop over other interactions.
        """
        if ix.callee_entity_id == new_callee.id:
            return
        ix.callee_entity_id = new_callee.id
        ix.seq = max(ix.seq, evidence_span.seq)
        caller = self._entity_by_id(ix.caller_entity_id)
        if caller is not None:
            ix.summary = f"{caller.display_name} → {new_callee.display_name}"
        # entity_spans on the new callee — the CLIENT span carried the host.
        self.entity_spans.append(
            ProtoEntitySpan(
                new_callee.id,
                evidence_span.trace_id,
                evidence_span.span_id,
                "identified_via",
            )
        )

    def _retract_interaction(self, ix: ProtoInteraction, retract_seq: int) -> None:
        """ADR-0011 §3: tombstone retract. Mutates `retracted_at`, advances
        `seq`. Releases (trace_id, span_id) ownership for spans that were
        owned by this interaction (D12: caller is responsible for
        re-attaching them in the same transaction).
        """
        if ix.retracted_at is not None:
            return
        ix.retracted_at = _dt.datetime.now(tz=_dt.timezone.utc)
        ix.seq = max(ix.seq, retract_seq) + 1  # advance for the retract event
        # Release span ownership for spans that this interaction owned.
        attached = self._attached_span_ids.get(ix.id, set())
        for sid in list(attached):
            owner = self._owners_by_span.get(sid)
            if owner == ix.id:
                del self._owners_by_span[sid]
        # Drop interaction_spans rows (the surviving interaction will
        # re-attach what it claims).
        self.interaction_spans = [
            r for r in self.interaction_spans if r.interaction_id != ix.id
        ]
        self._attached_span_ids.pop(ix.id, None)

    def _gc_entity_if_orphan(self, entity: ProtoEntity, retract_seq: int) -> None:
        """D7 universal destructive retract. Tombstone the entity if no
        active (non-retracted) interaction references it as caller or
        callee. In production this is a cross-trace check; the prototype
        is single-trace so the in-memory check suffices.
        """
        if entity.retracted_at is not None:
            return
        for ix in self._active_interactions():
            if ix.caller_entity_id == entity.id or ix.callee_entity_id == entity.id:
                return
        entity.retracted_at = _dt.datetime.now(tz=_dt.timezone.utc)
        entity.seq = max(entity.seq, retract_seq) + 1

    # ------------------------------------------------------------------
    # Step 5: handle a single anchor decision
    # ------------------------------------------------------------------

    def _handle_decision(
        self,
        d: AnchorDecision,
        span: Span,
        parent: Span | None,
    ) -> None:
        existing = self.interactions_by_anchor.get(d.primary_anchor_span_id)

        # ADR-0011 §5: pre-emission ownership check (D9, D10).
        # If a non-retracted interaction already owns any proposed anchor
        # span, skip emission.
        if existing is None or not self._is_active_interaction(existing):
            # ADR-0011 §5: widened from primary to all anchor spans (cross-service has 2)
            check_span_ids = d.anchor_span_ids or (d.primary_anchor_span_id,)
            for asid in check_span_ids:
                owner = self._owners_by_span.get(asid)
                if owner is not None:
                    # A different interaction owns this span (e.g. external-http
                    # CLIENT POST that reconciliation already transferred onto
                    # an OI LLM interaction; or a cross-service secondary anchor
                    # — the parent CLIENT — already owned by another interaction).
                    # Suppress.
                    self.notes.append(
                        f"suppress {d.rule} on {asid}: "
                        f"already owned by interaction {owner}"
                    )
                    return

        # Resolve caller + callee per rule.
        caller: Identity | None
        callee: Identity | None
        anchor_rule = d.rule

        if anchor_rule == "cross-service":
            assert parent is not None
            caller = self._resolve_service_side_identity(parent)
            callee = self._resolve_service_side_identity(span)
        elif anchor_rule == "orphan-server":
            caller = infer_caller_for_orphan_server(span)
            callee = self._resolve_service_side_identity(span)
        elif anchor_rule == "openinference-llm":
            caller = self._resolve_caller_around_oi_span(span)
            # D3: drop fire-time descendant lookup. Always emit unresolved
            # if the OI LLM span doesn't itself carry the host; let
            # reconciliation resolve.
            callee = llm_identity(span)
        elif anchor_rule == "openinference-tool":
            caller = self._resolve_caller_around_oi_span(span)
            mcp_server = self._matching_mcp_tool_span(span)
            if mcp_server is not None:
                callee = deployed_tool_identity(mcp_server)
            elif span.name in self._mcp_tool_names:
                # Tool name was advertised via an `mcp_tools` CHAIN span on
                # this trace — the tool is deployed-MCP even though no
                # in-trace SERVER span proves the transport (openai_agents'
                # MCPServerStreamableHttp path doesn't produce HTTPX CLIENT
                # spans, so the deployed-tool service never gets a parent
                # context to start a SERVER span on).
                proj = self._mcp_tool_names.get(span.name) or ""
                callee = Identity(
                    kind="tool",
                    natural_key=f"tool:({proj},{span.name})",
                    display_name=span.name,
                    project_name=proj or None,
                    detected_from="advertised by mcp_tools span",
                )
            else:
                owning_nk = caller.natural_key if caller is not None else "(unknown)"
                callee = in_process_tool_identity(span, owning_nk)
        elif anchor_rule == "external-http":
            caller = self._resolve_caller_around_oi_span(span)
            callee = service_identity_from_client(span)
        else:
            self.notes.append(f"unknown rule {anchor_rule}")
            return

        if caller is None or callee is None:
            self.notes.append(
                f"SKIP {anchor_rule} on {span.span_id}: caller={caller is not None}, callee={callee is not None}"
            )
            return

        caller_entity = self._upsert_entity(caller, span, "identified_via")
        callee_entity = self._upsert_entity(callee, span, "identified_via")

        if anchor_rule == "openinference-tool":
            self._tool_anchor_entity_by_span[span.span_id] = callee_entity.id

        if existing is None or not self._is_active_interaction(existing):
            ix = ProtoInteraction(
                id=str(uuid.uuid4()),
                trace_id=span.trace_id,
                parent_interaction_id=None,  # filled below
                caller_entity_id=caller_entity.id,
                callee_entity_id=callee_entity.id,
                started_at=span.started_at,
                ended_at=span.ended_at,
                error=span.error,
                request_payload_hash=None,
                response_payload_hash=None,
                summary=f"{caller_entity.display_name} → {callee_entity.display_name}",
                seq=span.seq,
                original_seq=span.seq,
                anchor_rule=anchor_rule,
                primary_anchor_span_id=d.primary_anchor_span_id,
            )
            ix.parent_interaction_id = self._compute_parent_interaction(
                d.primary_anchor_span_id
            )
            self.interactions_by_anchor[d.primary_anchor_span_id] = ix
            self._attached_span_ids[ix.id] = set()
            for asid in d.anchor_span_ids:
                self._attach_span(ix, asid, "anchor")
            self._extract_payloads(ix, span)
            self._update_aggregates(ix)
        else:
            # ADR-0011 §4: identity invariant. Anchor rules set identity on
            # creation only; re-fires on Finalization do not re-assert.
            # Identity mutates only by a more-informed anchor (cross-service
            # late-parent retarget) or by reconciliation. For same-rule
            # re-fire (D13) we just re-attach span(s) idempotently and
            # re-aggregate.
            for asid in d.anchor_span_ids:
                self._attach_span(existing, asid, "anchor")
            # Late caller retarget on cross-service (more-informed parent).
            if anchor_rule == "cross-service" and existing.caller_entity_id != caller_entity.id:
                existing.caller_entity_id = caller_entity.id
                existing.summary = f"{caller_entity.display_name} → {callee_entity.display_name}"
                existing.seq = max(existing.seq, span.seq)
            self._update_aggregates(existing)

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
        return caller_inference._agent_or_deployed_tool_from_service(span)

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
        for s in self.spans_by_id.values():
            if (
                s.kind == "SERVER"
                and s.service_name
                and s.service_name in self._mcp_services
                and canonical_service_name(s) == (span.name or None)
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

    def _entity_by_id(self, entity_id: str) -> ProtoEntity | None:
        for e in self.entities.values():
            if e.id == entity_id:
                return e
        return None

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
    # Step 6: late-parent re-evaluation
    # ------------------------------------------------------------------

    def _reprocess_resolved_deferred(self, parent_now: Span) -> None:
        for child in list(self.children.get(parent_now.span_id, [])):
            if child.span_id not in self._deferred_spans:
                continue
            self._deferred_spans.discard(child.span_id)
            in_trace_children = list(self.children.get(child.span_id, []))
            decisions = anchor_rules.fire_all(
                child, parent_now, in_trace_children, self.known_canonicals
            )
            for d in decisions:
                self._handle_decision(d, child, parent_now)

    def _reevaluate_late_children(self, parent_now: Span) -> None:
        children_of_parent = self.children.get(parent_now.span_id, [])
        for child in children_of_parent:
            existing = self.interactions_by_anchor.get(child.span_id)
            if existing is None or not self._is_active_interaction(existing):
                continue
            if existing.anchor_rule != "orphan-server":
                continue
            d = anchor_rules.cross_service(child, parent_now)
            if d is None:
                continue
            new_caller = self._resolve_service_side_identity(parent_now)
            if new_caller is None:
                continue
            new_caller_entity = self._upsert_entity(new_caller, parent_now, "identified_via")
            existing.caller_entity_id = new_caller_entity.id
            existing.anchor_rule = "cross-service"
            existing.seq = max(existing.seq, parent_now.seq)
            self._attach_span(existing, parent_now.span_id, "anchor")
            existing.parent_interaction_id = self._compute_parent_interaction(
                existing.primary_anchor_span_id
            )
            self.notes.append(
                f"late-parent: orphan-server {child.span_id} demoted to cross-service "
                f"({new_caller.display_name} → ...)"
            )

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

    # ------------------------------------------------------------------
    # Misfired-external-http batch fixup
    # ------------------------------------------------------------------

    def _retract_misfired_external_http(self) -> None:
        """Streaming external-http misfires: a CLIENT span whose host had no
        matching canonical-service entity at fire time, but a later-arriving
        span on that host registers it as an agent/tool. The Interaction
        becomes wrong (callee = service:host instead of agent:(...,host)).

        For the prototype we fix this up at flush time. Per ADR-0011 §3,
        retract by tombstone, not hard delete.
        """
        canonicals = self.known_canonicals
        bad_keys: set[str] = set()
        for nk, e in list(self.entities.items()):
            if e.kind != "service" or e.retracted_at is not None:
                continue
            host = nk[len("service:") :]
            if host in canonicals:
                bad_keys.add(nk)
        if not bad_keys:
            return
        bad_eids = {self.entities[nk].id for nk in bad_keys}
        retract_seq = self.last_processed_seq
        # Collect anchor span_ids of retracted external-http interactions so
        # we can re-evaluate cross-service on their SERVER children once
        # ownership is released.
        released_client_span_ids: set[str] = set()
        for ix in list(self.interactions_by_anchor.values()):
            if not self._is_active_interaction(ix):
                continue
            if ix.callee_entity_id in bad_eids or ix.caller_entity_id in bad_eids:
                if ix.anchor_rule == "external-http":
                    released_client_span_ids.add(ix.primary_anchor_span_id)
                self._retract_interaction(ix, retract_seq)
        for nk in bad_keys:
            e = self.entities[nk]
            self._gc_entity_if_orphan(e, retract_seq)
        # Re-fire anchor rules on SERVER children of released CLIENT spans.
        # The cross-service rule was suppressed earlier (D9 widening) because
        # the CLIENT parent was owned by the now-retracted external-http
        # interaction; with that ownership released, cross-service should now
        # produce the proper agent→agent / agent→tool interaction.
        for client_sid in released_client_span_ids:
            for child in self.children.get(client_sid, []):
                if child.kind != "SERVER":
                    continue
                parent = self.spans_by_id.get((child.trace_id, child.parent_id)) if child.parent_id else None
                in_trace_children = list(self.children.get(child.span_id, []))
                decisions = anchor_rules.fire_all(
                    child, parent, in_trace_children, self.known_canonicals,
                )
                for d in decisions:
                    self._handle_decision(d, child, parent)
        self.notes.append(
            f"retracted {len(bad_keys)} misfired external-http service entities "
            f"(matched a known canonical service): {sorted(bad_keys)}"
        )

    # ------------------------------------------------------------------
    # Result snapshot
    # ------------------------------------------------------------------

    def result(self) -> ExtractResult:
        # Flush remaining deferred spans.
        for sid in list(self._deferred_spans):
            for (_, k), s in self.spans_by_id.items():
                if k != sid:
                    continue
                in_trace_children = list(self.children.get(s.span_id, []))
                decisions = anchor_rules.fire_all(
                    s, None, in_trace_children, self.known_canonicals,
                    parent_id_known_unresolvable=True,
                )
                for d in decisions:
                    self._handle_decision(d, s, None)
                break
        self._deferred_spans.clear()

        self._retract_misfired_external_http()

        # After all spans, attach descendants of every active anchor and
        # (for cross-service) the caller-side connector chain.
        for ix in list(self.interactions_by_anchor.values()):
            if not self._is_active_interaction(ix):
                continue
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
