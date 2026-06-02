# Interaction tree per trace is a forest; no synthetic trace-root interaction

`P-interactions` derives a parent-child structure on **Interactions**:
each interaction has a `parent_interaction_id` pointing at the innermost
enclosing interaction whose territory contains its anchor(s). The
question is what sits at the *top* of that structure.

We chose a **forest** — top-level interactions have
`parent_interaction_id IS NULL`, and a single trace may have any
number of top-level interactions (one per turn, one per concurrent
client request). There is **no synthetic "trace root" interaction**.

## The rule

For each interaction `I`:

- Walk up `I`'s outermost anchor span's parent chain.
- The first ancestor span owned by some other interaction `P` is `I`'s
  parent: `I.parent_interaction_id = P.id`.
- If no ancestor is owned by any interaction (the chain reaches the
  trace root or runs out without finding one), `I.parent_interaction_id
  IS NULL`. `I` is a top-level interaction.

A trace's top-level interactions are queried as
`WHERE trace_id = T AND parent_interaction_id IS NULL`. The trace root
**span** itself is not represented as an interaction — it may
contribute to `entity_spans` (e.g., the trace root carries enough
attributes to identify the calling client), but it does not appear in
`interactions`.

## Why

- **Trace roots are often not logical interactions.** The demo-client
  trace's root span (`demo_two_turn_conversation`) is a test-harness
  wrapper — naming a single test case, not describing one logical
  call. Forcing it into an interaction would invent semantics that
  are not in the data.
- **Multi-top-level traces are real.** A two-turn conversation
  produces two top-level `client → agent` interactions, one per
  turn. Both legitimately have no enclosing interaction. A synthetic
  root would either group them under a phantom parent (lying about
  the call structure) or be omitted for them (inconsistent rule).
- **The schema cost of NULL is zero.** `parent_interaction_id` is
  nullable; consumers walking up stop on NULL. A synthetic root
  would require an extra row per trace, an extra interaction-kind
  to denote "synthetic", and special-case handling in queries that
  filter by entity kind.
- **The forest mirrors the call-stack reality.** A trace is a *set*
  of spans sharing `trace_id` (CONTEXT.md `Trace` term) — there is
  no inherent root in the OTEL model either. Both layers
  (spans/interactions) acknowledge that traces can have many or
  zero structural roots.

## Considered alternatives

- **Synthesise a `trace → ?` interaction for every trace.** Rejected:
  invents an entity that has no real-world referent (what `Entity`
  is the caller of `demo_two_turn_conversation`?). The trace root's
  span contributes to entity discovery cleanly via `entity_spans`;
  it does not need to be promoted to an interaction.
- **Anchor every trace on its `Real root` span as an
  interaction.** Rejected: real roots may not be present in the
  trace at all (orphan-listing-root case from ADR-0001) or may be
  test-harness spans with no logical-call meaning. The
  `client → agent` rule already produces a top-level interaction
  for the *real* entry point when one exists.
- **Encode the forest as a flat list with depth-by-position.**
  Rejected: forces consumers to reconstruct the tree from positional
  data. `parent_interaction_id` is a one-column self-reference that
  consumers can walk or join in SQL.

## Consequences

- **Top-level interactions are queried with `parent_interaction_id
  IS NULL`.** Documented at the `Interaction tree` term in
  CONTEXT.md.
- **A trace with zero recognised interactions still has rows in
  `entity_spans` and `entities`** (entity discovery from the trace
  root and any non-anchor spans). Such traces are effectively
  invisible in the interaction graph — which is the right outcome
  for traces that are pure plumbing.
- **The UI's interaction-flow view renders one tree per top-level
  interaction.** Multi-turn traces show multiple sibling trees
  side-by-side; the eye can match them to the trace's natural
  segmentation.
- **`parent_interaction_id` is a foreign key into `interactions`
  itself.** Cascade-on-delete behaviour and the streaming model's
  mutation rules need to keep the forest acyclic — guaranteed by
  construction (parent walking is up the *span* tree, which is
  acyclic).
