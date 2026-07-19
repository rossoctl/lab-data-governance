# Cross-service caller is the nearest enclosing entity, not the owning service

---
Status: accepted (refines ADR-0010)
---

The **caller** of a cross-service interaction leg is the **nearest enclosing
entity-anchoring span** on the span's same-service ancestor walk — not the
identity of the service that owns the outbound CLIENT span. Concretely, when
`_emit_cross_service` resolves the caller of an A2A/cross-service hop, it walks
up from the outbound CLIENT POST staying on the same canonical service and takes
the innermost enclosing in-process OI `TOOL` span as the caller. If no enclosing
tool is found before the walk reaches the service boundary (or the service's own
`AGENT` frame), it falls back to the **service-side identity** of the outbound
span — the owning **agent**, or a deployed `tool:` (sourced from the service's
`/mcp` SERVER) when that span itself lives on an MCP service (the
`create_booking → payment_agent` case). The two tool flavours therefore reach
the caller field by different paths — the in-framework tool *during* the
same-service walk, the deployed tool *via* the leave-service fall-back — but both
land on a `tool:` caller. An enclosing `llm` span is the call's own gateway
transport and is absorbed by the `agent→llm` edge — an `llm` is never a
cross-service caller.

This is the same nearest-enclosing-entity resolution `_emit_oi` already used
(`_resolve_caller_around_oi_span`); this ADR records extending it to
`_emit_cross_service`, which previously resolved the caller by service identity
(`_resolve_service_side_identity(parent)`) and so skipped the enclosing tool.

## Why

The in-framework delegate primitive (`delegate_to_research_agent`,
`delegate_to_booking_agent`) emits its outbound A2A CLIENT POST as a direct
child of the framework's in-process `TOOL` span — the call physically issues
from inside the tool body. Attributing that leg to the tool, not the enclosing
agent, reflects what actually happened and makes it structurally identical to
the deployed-MCP case (`create_booking → payment_agent`), where the tool's own
service owns the POST. The distinction between the two tool deployments was an
artifact of *where the span lives* (same service vs separate pod), not of *who
made the call*; resolving by lineage rather than service ownership removes that
artifact.

The change also makes the interaction tree self-consistent: each delegate A2A
leg's parent is the `agent → tool:X` intent interaction, so making the child's
caller `tool:X` yields `caller(child) == callee(parent)` — the same parent/child
shape the deployed `create_booking → payment_agent` leg already had.

## Relationship to ADR-0010

ADR-0010 ("In-process and deployed-MCP tools both emit interactions") stands:
both the `agent → tool` intent leg and the cross-service transport leg are still
emitted, and the interaction tree still connects them. This ADR refines only the
**caller attribution of the cross-service leg** for the in-framework case:
ADR-0010 modelled it as `caller = agent`; we now model it as `caller =
in-process tool`. The intent leg (`agent → tool`) is unchanged. Both-emitted,
the tree edge, and the deployed-tool transport absorption are all unchanged.

## Considered alternatives

- **Keep `caller = agent`, rely on the tree edge** to express "through the
  tool" (the A2A leg's `parent_interaction_id` already points at the
  `agent → tool` intent interaction). Rejected: the caller field should name
  who issued the call; deferring that entirely to tree position left the
  in-framework and deployed cases gratuitously asymmetric for the same logical
  pattern.
- **Special-case the delegate primitive** (`kagenti.call.kind =
  agent_consultation`) instead of generalising. Rejected: the nearest-enclosing-
  entity rule is the general principle (`_emit_oi` already followed it); special-
  casing delegate would have been a narrower rule that is harder to justify and
  still needed the same scramble-gate re-validation.

## Consequences

- On the gate trace, exactly three legs change (the two delegate primitives
  across three invocations) from `agent → agent` to `tool → agent`; the deployed
  `create_booking → payment_agent` leg is unchanged and validates the rule.
- `interactions.yaml` is updated in lockstep so the reference fixture matches.
- The `--scramble` order-independence gate must remain byte-identical across
  arrival orders; the resolution walks only same-service lineage (ancestors of
  an arrived span), so it stays lineage-scoped and order-independent.
- `llm` and bare-`agent` enclosing cases are defined but unexercised by the gate
  trace (no cross-service POST has an LLM as its nearest enclosing entity, and
  the bare-agent case collapses to the prior service-side identity).
