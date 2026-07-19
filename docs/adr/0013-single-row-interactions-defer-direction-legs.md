# Interactions are a single row in v2; the request/response two-leg model is deferred

The CONTEXT.md **Interaction leg** (`direction`) term describes an
**Interaction** as **two rows** keyed `(id, direction)` — a `request` leg
and a `response` leg, each with independent `seq` / `original_seq` /
lifecycle. The verified prototype, ADR-0007 ("an interaction's `error`,
`request_payload_hash`, `response_payload_hash`, `started_at`, `ended_at`
may all be updated" — one mutable row), and ADR-0012 (sufficiency-gated
emission, one interaction materialised per completing span) all model an
**Interaction** as a **single row** with `request_payload_hash` and
`response_payload_hash` side by side. We ship the **single-row** model in
v2 and defer the two-leg `direction` model.

## The rule

The `interactions` table has one row per logical interaction: one `id`
(PK), one `seq` / `original_seq`, one `error`, one `started_at` /
`ended_at`, and nullable `request_payload_hash` / `response_payload_hash`
columns side by side. There is no `direction` column and no `(id,
direction)` composite key.

## Why

- **Productize what is verified.** The two-leg model was authored in the
  glossary but never implemented or exercised by the `--scramble`
  acceptance gate. Shipping it would mean building (and verifying) a
  two-leg writer the prototype never had — scope the migration cannot
  justify on evidence.
- **The single-row model already satisfies every consumer v2 has.** The
  UI reads an interaction's current state; the side-by-side payload hashes
  carry both bodies. Nothing today needs request and response to finalize
  or be governed independently.
- **It leaves the door open.** Splitting one row into two legs later is an
  additive migration (`direction` defaults to `request`, back-fill a
  `response` leg). The identity-level fields (caller, callee, anchor,
  `parent_interaction_id`) and the ADR-0008 tree / ADR-0011
  `(trace_id, span_id)` uniqueness all key on `id`, not on the leg — so a
  future split does not disturb them. Recording the deferral now means the
  glossary term is honest about what ships.

## Consequences

- The CONTEXT.md **Interaction leg** term is marked *deferred — not
  implemented in v2*; it documents the intended-future shape, not the
  shipped schema. The drivers it lists (temporal asymmetry, streaming
  responses, per-leg governance) remain the motivation for the eventual
  split.
- A future trace fixture that genuinely needs independent request/response
  lifecycles (a long-lived streaming response whose request is final while
  the response is still arriving) is the trigger to revisit. None exists
  today.
