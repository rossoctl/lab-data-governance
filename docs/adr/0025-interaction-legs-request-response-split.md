---
status: accepted; supersedes ADR-0013
---

# Interactions split into a parent identity row + request/response legs

ADR-0013 shipped the single-row `interactions` model and *deferred* the
two-leg `direction` split (the CONTEXT.md **Interaction leg** term), on the
principle that the two-leg writer was never exercised by the `--scramble`
gate and "scope the migration cannot justify on evidence." A new upstream
source is now anticipated that emits a **request span and a response span as
two distinct `(trace_id, span_id)` rows arriving at different times**, sharing
an **exchange id** — genuine Case-Y (independent request/response lifecycles),
which is exactly the revisit trigger ADR-0013 named. We un-defer the split,
but as a **two-table** model rather than the glossary's original `(id,
direction)` single-table shape:

- **`interactions`** — one row per logical call, holding only identity that is
  identical across both legs: `id`, `trace_id`, `parent_interaction_id`,
  `caller_entity_id`, `callee_entity_id`, `summary`.
- **`interaction_legs`** — one row per temporal half, PK `(interaction_id,
  leg_type)` with `leg_type ∈ {request, response}`, holding the leg-dependent
  fields: `occurred_at`, `payload_hash`, `error`, `seq`. (As accepted this also listed
  `original_seq`; migration `0011_drop_leg_original_seq` removed it — issue #133, and
  see the note under *Per-leg `seq`* below.)

## Why two tables instead of `(id, direction)`

- **Shared identity becomes a structural guarantee, not a convention.** The
  glossary insists both legs carry the *same* caller/callee (a response is the
  return of the caller→callee call, not a new callee→caller edge). Storing
  `caller`/`callee`/`parent_interaction_id` once, on the parent, makes leg
  disagreement *inexpressible* — the single-table model could only *assert* it.
- **No stored `exchange_id` column.** Legs attach to their parent by
  `interaction_id`; the correlation key is the parent PK. `exchange_id` is the
  *future writer's* input (how it decides two spans belong to one interaction),
  not a stored column — so the current source needs no synthetic exchange id.
- **Per-leg `seq` is the independent-lifecycle mechanism.** A response leg
  finalizing later advances *its own* `seq` without touching the request leg —
  which is the entire point of the split (a stream consumer sees "response
  landed" as a distinct `seq` event). This is why `seq` moves to
  the leg. (`original_seq` moved here too, and was later dropped: a leg's `seq` is
  DB-owned and never mutates on re-derive, so the frozen-vs-mutating comparison the
  column existed for is inert for legs — migration `0011_drop_leg_original_seq`, issue
  #133. `entities.original_seq` is unaffected.) Consequently the parent `interactions` has **no `seq`** and is not
  independently cursorable: identity is immutable once decided, so all the
  time-varying, independently-finalizing state — and therefore the cursor —
  lives on `interaction_legs` (`interactions_seq` is retired in favour of
  `interaction_legs_seq`). The parent is a join target for identity, not a
  stream.
- **`error` per leg.** A request can succeed while the response errors
  (timeout, malformed stream) — a headline Case-Y scenario the single `error`
  column could not represent. Parent-level "any leg errored" is computed on read.
- **Duration is computed on read, never stored** (`response.occurred_at −
  request.occurred_at`), **null when the response leg is absent** — that null is
  the "response in flight" signal, which a stored duration would be forced to lie
  about (ADR-0007: no field a later event can falsify).

## The current algorithm does NOT change — the split is a boundary projection

The verified, `--scramble`-gated `procedure.py` is untouched. It still reasons
about **one** logical interaction per anchor (one `interactions_by_anchor`
entry, one emit-once key, one territory it re-aggregates). The split into a
parent + two legs happens only at the **write boundary** (`state.flush`), which
projects that one internal interaction into 1 parent row + 2 leg rows:

- **request leg** — `payload_hash = request_payload_hash`, `occurred_at =
  started_at`, `error = span.error`, its own `seq`.
- **response leg** — `payload_hash = response_payload_hash`, `occurred_at =
  ended_at`, `error = span.error`, its own `seq`.

Both legs of the current source derive from the **same one span**, so they are
**derived legs**: a synchronous call's request and response bracketed at
`started_at → ended_at`. This is honest (those timestamps genuinely bound the
call) but distinct from the future source's **observed legs**, whose two spans
carry independent timing and finalize independently. The distinction is recorded
at the CONTEXT.md **Interaction leg** / **Leg provenance** terms.

## Deliberately out of scope

The future Case-Y **algorithm** (the anchor rule that mints an interaction `id`
from `exchange_id`, sets `leg_type` per span, and attaches the request span and
response span to distinct legs) and its `--scramble` verification are **not**
built here — they are blocked on a captured trace from the new source, per
ADR-0013's own evidence bar. This ADR ships only the **schema + API + UI** shape
so that writer lands as an additive, already-verified-shape increment.

## Consequences

- `interaction_spans` gains a `leg_type` column so span evidence attributes to a
  specific leg (the future source's response span belongs to the response leg,
  not merely "the interaction"). For the current source both legs cite the same
  one span; `interaction_spans` PK stays `(trace_id, span_id)` — ADR-0011's
  one-span-one-interaction invariant is untouched, since both legs share one
  `interaction_id`.
- A parent `interactions` row is **never leg-less**: it is created together with
  its request leg (current source: 1 parent + 2 legs atomically; future source:
  parent + request leg together, response leg appended later). Every interaction
  has at least a request leg.
- The API `GET /api/traces/{tid}/interactions` returns each interaction with its
  legs nested (or a joined leg view); the UI renders one arrow per leg — the
  sequence-diagram / lineage benefit that motivated the change.
