# In-process and deployed-MCP tools both emit interactions

When an agent calls a tool that is deployed as a separate MCP service,
two anchor rules fire on overlapping spans:

- The **openinference-tool** rule fires on the agent-side TOOL span,
  producing an `agent → tool` (in-process) interaction. Its payload
  is the structured `tool.parameters` / `output.value` on that span.
- The **cross-service** rule fires on the CLIENT POST descendant
  (under the TOOL span on the agent) and the SERVER POST on the
  deployed-tool service, producing a `tool → tool` (cross-service)
  interaction. Its payload is the HTTP body.

The first round of the prototype suppressed one of these — keeping
only the cross-service interaction. We **reverse that decision**:
both interactions are emitted, and the **interaction tree** connects
them (`tool → tool`'s `parent_interaction_id` points at the
`agent → tool` interaction).

## The rule

The openinference-tool rule and the cross-service rule both fire
independently. There is no subtree-suppression heuristic between
them. The interaction tree (per ADR-0008) reflects the resulting
nesting: the agent-side TOOL anchor sits above the cross-service
CLIENT POST anchor, so the cross-service interaction is a child of
the in-process one.

In the entities table:
- The agent-side in-process tool entity exists with natural key
  `tool:<owning_agent_natural_key>:<tool_name>`.
- The deployed-tool entity exists with natural key
  `tool:(<project_name>,<canonical_service_name>)`.
- They are **not** automatically merged in v2. (Future work: a
  v2.x rule that consults a registered-tool inventory and collapses
  the two when they are confirmed to be the same logical tool.)

In the interactions table:
- `agent → tool` interaction: caller = `agent`, callee = in-process
  tool entity. Payload = structured tool args / result.
- `tool → tool` interaction: caller = in-process tool entity,
  callee = deployed-tool entity. Payload = HTTP body.

## Why

- **The two interactions describe distinct levels of abstraction.**
  `agent → tool` captures the agent's *intent* — "the agent decided
  to call `book_flight` with these arguments and got this result".
  `tool → tool` captures the *transport* — "this in-process tool
  call was implemented as an MCP RPC to a deployed tool service".
  Both are real and both are useful; suppressing one flattens the
  call stack and discards intent or transport.
- **The interaction tree (ADR-0008) is exactly what makes
  emitting both clean.** Without a tree, two interactions for "the
  same logical call" would be a dedup hazard. With a tree, they're
  parent and child — naturally distinguished by their position in
  the structure.
- **Different content kinds.** The structured `tool_call_arguments`
  / `tool_call_result` payload (rich, dedup-friendly) lives on the
  in-process interaction. The `http_request_body` /
  `http_response_body` (raw, sometimes redundant) lives on the
  cross-service one. They never compete for the same payload slot.
- **Symmetry with non-MCP tool deployments.** A tool that runs only
  in-process produces just `agent → tool`. A tool that is *only*
  deployed (no in-process companion span) produces just
  `tool → tool` (or `agent → tool` deployed). The MCP case is the
  union — emit both, in the same shape they would have been emitted
  separately.
- **Entity-kind-agnostic anchor rules (ADR-0009) make the
  decomposition free.** No new rule is needed; the openinference-tool
  rule and the cross-service rule were already independent.
- **It opens a path to a future merge.** When a registered-tool
  inventory exists, a v2.x rule can mark the in-process and
  deployed tool entities as the same logical tool. Until then, the
  display-name collision is acceptable (per the round-2 NOTES), and
  consumers who care about logical-call counting can filter to leaf
  interactions or aggregate by entity natural-key prefix.

## Considered alternatives

- **Suppress the in-process interaction when its subtree contains
  a cross-service SERVER on a registered tool service** (the
  round-1/round-2 dedup). Rejected: discards the agent's intent
  payload (the structured args), which is exactly the
  representation downstream analytics will want first. Forces the
  cross-service interaction to carry both intent *and* transport on
  the same payload slot, with no clean canonicalization.
- **Suppress the cross-service interaction.** Rejected: discards
  the wire-level evidence and the deployed-tool entity, breaking
  the "every cross-service hop is a call" invariant.
- **Merge into a single hybrid interaction** (caller =
  in-process-tool entity, callee = deployed-tool entity, but with
  metadata flags on the row indicating it spans two abstraction
  levels). Rejected: introduces a non-orthogonal shape that breaks
  the per-row "one caller, one callee" model and complicates
  consumer queries.

## Consequences

- **The in-process tool entity is *not* a ghost.** It is the callee
  of a real interaction (`agent → tool`) and the caller of another
  (`tool → tool`). It earns its place in `entities`.
- **Per-trace interaction counts roughly double for MCP-heavy
  traces.** The booking-agent demo trace gains four extra
  interactions (one per MCP tool call) compared to the dedup'd
  variant. Consumers that want "logical call count" should filter
  to leaf interactions (no children) or to interactions whose
  callee is *not* itself a caller in the same trace.
- **The display-name collision is preserved** (`book_flight` shows
  twice in entity tables — once per natural key). Documented at the
  `Entity` term in CONTEXT.md.
- **Round-2 NOTES.md's "Problem 1: duplicate counting of
  cross-service tool calls" is reframed.** It was not a duplication
  bug — it was the design *not yet recognising* the two-level
  structure. The fix is structural (interaction tree), not a dedup
  heuristic.
- **The path to entity-merging (round-2 Problem 2) stays open.**
  If a future entity inventory recognises that the deployed tool
  and the in-process tool are the same logical tool, the merge
  collapses two natural keys into one — but does not collapse the
  two interactions, which describe distinct abstraction levels.
