# Anchor rules fire on span structure; entity-kind resolution is a separate ladder

`P-interactions` recognises six interaction shapes:
`client → agent`, `agent → agent`, `agent → tool` (in-process),
`agent → tool` (deployed MCP), `agent → llm`, `agent → service`. The
naive design would have one *anchor rule* per shape — six rules, each
hard-coded to its specific entity kinds.

We chose a **structural / kind-agnostic** decomposition: the **anchor
rules** decide *what creates an interaction* based on span structure
(span kind, parent presence, attribute markers). The **caller inference
rule** then independently decides *what kinds of entities* sit at
either end. Adding `tool → agent`, `tool → tool`, `tool → service`, or
`user → agent` interactions does not require new anchor rules — only
extensions to the entity-kind ladder.

## The rule

There are **five** anchor rules, each defined purely on span structure:

| anchor rule | trigger | anchors |
|---|---|---|
| **cross-service** | span Sn has parent Sp on a different canonical service | both Sp and Sn |
| **orphan-server** | SERVER span Sn has no in-trace parent | Sn |
| **openinference-llm** | span Sn has `openinference.span.kind = LLM` | Sn |
| **openinference-tool** | span Sn has `openinference.span.kind = TOOL` and `kagenti.call.kind ≠ agent_consultation` | Sn |
| **external-http** | CLIENT span Sn whose host is not in the entity inventory and has no in-trace SERVER child | Sn |

The interaction-shape table above (`client → agent` etc.) is what falls
out when these rules fire on real spans:

- **cross-service** → produces `agent → agent`, `agent → tool`
  (deployed), `tool → agent`, `tool → tool`, etc., depending on which
  entity kinds the caller-inference rule resolves on either side.
- **orphan-server** → produces `client → agent` if the calling-side
  attributes resolve to a `client` entity, or `user → agent` if
  `kagenti.user.id` is present.
- **openinference-llm** → `agent → llm` (or `tool → llm` if the LLM
  call comes from an in-process tool span).
- **openinference-tool** → `agent → tool` (in-process); the
  `agent_consultation` exclusion keeps framework delegation primitives
  off the entity graph.
- **external-http** → `agent → service` or `tool → service`.

## Why

- **The structural/semantic split keeps the rule count bounded.**
  Six anchor shapes (today) × seven entity kinds × two roles
  (caller, callee) = 84 potential combinations. Without the split,
  adding one entity kind multiplies anchor rules. With it, a new
  entity kind extends the caller-inference ladder by one rule
  while leaving the five anchor rules unchanged.
- **The anchor decision and the identity decision use different
  evidence.** Anchor rules look at span kind and structural
  relationships (parent, descendants). Identity rules look at
  semantic attributes (`service.name`, `client.address`,
  `peer.service`, `kagenti.user.id`). Coupling them would force
  every anchor rule to inspect every identity-shaped attribute.
- **It admits new shapes for free.** `user → agent` was raised
  late in design as a future case; under this decomposition it
  required no new anchor rule — just an entry in the caller-inference
  ladder mapping `kagenti.user.id` to `user` entity kind. Same for
  `tool → agent` (cross-service rule + tool kind on the caller side).
- **Anchor rules are testable independently of entity-kind
  resolution.** Test fixtures for anchor rules need only minimal
  spans (the kinds and parents that trigger the rule); identity-side
  tests use a separate fixture set with rich attributes. Without
  the split, every anchor test would have to set up entity kinds.

## Considered alternatives

- **One rule per (caller_kind, callee_kind) pair.** Rejected: 21
  distinct rules for the kinds we have, growing combinatorially. Most
  share identical span-structural conditions; the duplication would
  be enormous.
- **Anchor rules as conditional on kind.** A `cross-service-agent-
  to-agent` rule that fires only when both endpoints resolve to
  `agent`. Rejected: forces the anchor decision to depend on the
  identity decision, but the identity decision itself depends on the
  span structure (which spans we attach as evidence). Circular.
- **Single mega-rule that classifies every span.** Rejected:
  unmaintainable; the five-rule split mirrors how span tooling
  itself groups spans (OTEL kind, OpenInference kind, HTTP semantic
  conventions).

## Consequences

- **Adding a new entity kind requires one ladder entry, not a new
  anchor rule.** Documented at the `Caller inference rule` term in
  CONTEXT.md.
- **Adding a new interaction shape does not require a new anchor
  rule** unless the shape's *structural* trigger is genuinely new
  (e.g., a hypothetical "WebSocket message" rule that watches for
  bidirectional CLIENT-SERVER message frames). Most shape additions
  are entity-kind ladder extensions.
- **The five anchor rules are stable across v2.** Future additions
  expected only when new instrumentation conventions emerge
  (e.g., a rossoctl-stamped `kagenti.span.kind = anchor` would be a
  sixth rule).
- **The MCP-deployed-tool + in-process-tool case is naturally
  expressible** — the cross-service rule and the openinference-tool
  rule both fire, on different spans, producing two interactions
  connected by the interaction tree. See ADR-0010.
- **Test files split cleanly into `test_anchor_rules.py` (structural
  fixtures) and `test_caller_inference.py` (attribute-rich
  fixtures).**
