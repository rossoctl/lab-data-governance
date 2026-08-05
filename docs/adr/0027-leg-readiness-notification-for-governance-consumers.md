---
status: accepted
---

# Leg-readiness notification for governance consumers

A new class of downstream Layer-2 consumer is anticipated over the derived
abstraction layer (entities, interaction legs, payloads, classifications): a
**risk** processor, a **data-lineage** processor, and eventually a **Policy
Decision Point (PDP)** that decides whether an **Interaction leg** may proceed
(the request leg primarily, possibly the response leg). These consumers need to
reliably learn two things: when a new **Entity** is created, and when an
**Interaction leg** is *ready* for a governance decision.

An **Interaction leg** is **ready** when it has been written **and** either it
has no payload (`payload_hash IS NULL` — nothing to classify) **or** its payload
has been classified (a `payload_classifications` row exists for its
`content_hash`). "Ready" is the precondition a governance consumer waits for: a
payload-bearing leg must not be acted on before its **Classification** verdict
exists, since the verdict is the governance input.

## Decision

Add two consumer-facing notification channels, **`dg_entity_ready`** and
**`dg_interaction_leg_ready`**, and make reliability a property of a
**cursor-drained, query-derived readiness view over the existing tables** — not
of the notification itself.

- **Reliability lives in the drain, not the notify.** Consistent with ADR-0015,
  `pg_notify` is **latency-only**: an absent listener misses it, it carries no
  payload, and it cannot be replayed. So it is never the source of truth. Each
  consumer holds a durable cursor (`processor_state`) and, on startup and on
  every wake, pulls all previously-unseen ready items past its cursor by
  re-deriving readiness from the existing tables. A missed or lost notification
  costs latency, never correctness — the same guarantee `dg_spans_inserted` /
  `dg_payloads_inserted` already rely on. **No new stream table is introduced;
  the existing tables are the durable state.**

- **`dg_entity_ready`** — a DB trigger `AFTER INSERT ON entities FOR EACH ROW`.
  First-detection only: an entity's identity is set once at creation and, for
  the current source, never mutated (the `ON CONFLICT DO UPDATE` path only ever
  rewrites identical values — see the **Entity** term). The consumer drains
  `entities` by `seq` and reuses the shared driver
  (`processors/_driver.py`) verbatim.

- **`dg_interaction_leg_ready`** — fired from **processor code**, not a DB
  trigger, because readiness is a **cross-processor join completion** the
  `interaction_legs` insert cannot see: the leg and its payload are written by
  `P-interactions` in one transaction, but the payload's **Classification** is
  written *later* by the separate **P-classification** processor. No single
  table write coincides with "leg became ready." Both processors therefore tap
  the channel with a **blind, payload-less** `pg_notify` ("something may have
  become ready, go look"): P-classification after each classification write,
  and P-interactions after writing a leg that is ready at write time
  (no-payload, or a payload whose `content_hash` was already classified). Both
  taps are pure latency optimization and carry no correctness weight.

- **The readiness drain uses a contiguous-prefix cursor over a plain `seq`, not
  an unconditional `seq > cursor` max-advance.** The readiness predicate breaks
  the monotonicity the standard cursor assumes: a low-`seq` leg with an
  unclassified payload can sit behind a high-`seq` ready leg. Advancing the
  cursor to the max seq seen would strand the low-`seq` leg forever. So the
  readiness cursor advances only across the leading unbroken run of ready legs
  and stops at the first unready one (head-of-line blocking: one slow
  classification holds the watermark). This is the **one place the shared
  `_driver.drain` is not reused verbatim** — it needs custom stop-at-first-unready
  advance logic. The `entities` stream, by contrast, has no readiness gate and
  reuses `_driver` as-is.

- **Intra-interaction ordering falls out of the legs' distinct `seq`s — no
  tiebreaker.** The required ordering is: within one interaction the **request**
  leg is delivered ready before the **response** leg; across different
  interactions no order is required. Each leg carries its **own** DB-owned `seq`
  from `nextval('interaction_legs_seq')`, and `state.flush` inserts the request
  leg first, so the request leg always has the lower `seq`. Delivery therefore
  follows a plain `ORDER BY seq` and request-before-response is implied by the
  seq order itself — the `(seq, leg_type)` composite watermark and the explicit
  `leg_type` tiebreaker earlier drafts needed for shared-seq legs are **retired**
  (see the Reversal note below). A plain single-`BIGINT` watermark round-trips
  through `processor_state.last_processed_seq` directly, which the future Case-Y
  source (response leg readying later, at its own higher `seq`) also satisfies.

## Why not the alternatives

- **A DB trigger on `interaction_legs` (mirror `dg_payloads_inserted`).** The
  payload is unclassified at leg-insert time, so the trigger would fire before
  readiness — it cannot express the classification precondition.

- **A DB trigger on `payload_classifications`.** That table is keyed by
  `content_hash` and does not know which legs reference it; and a no-payload leg
  never produces a classification row, so it would never notify.

- **A materialized `dg_leg_ready` stream (a table or a `ready_seq` column
  stamped on the readiness transition, with its own sequence and trigger).**
  Most robust and most consistent with the spans/payloads stream shape, but the
  heaviest: it re-introduces "who stamps readiness" as a transactional write and
  a truncate/reset coupling. Rejected because the query-derived view already
  gives full replayability with no new schema — the tables *are* the durable
  state.

- **Per-leg `seq` from `nextval('interaction_legs_seq')`** (to sequence the two
  legs independently). *Originally rejected, now the chosen path — see the
  Reversal note below.* The original objection was replay-determinism: `nextval`
  is assigned in arrival order, and the P-interactions design guarantees
  cursor-reset re-drains reproduce identical derived rows (the `--scramble`
  order-independence invariant), so a `nextval` per leg looked like it would
  reassign leg seqs on any upstream replay (ADR-0007 crash recovery, ADR-0024
  re-classification recovery) and make already-processed legs reappear under new
  seqs. That objection does **not** hold: dropping `seq` from the leg's
  `ON CONFLICT ... DO UPDATE SET` makes a re-derive **preserve** the once-assigned
  seq (only cosmetic sequence *gaps* from the discarded insert attempt, never a
  changed stored value), and the `--scramble` byte gate already **excludes** leg
  `seq`/`original_seq` from comparison as arrival-order-dependent passenger
  fields. So the composite tiebreaker was solving a problem the shared seq
  *created*, not an intrinsic one.

## Consequences

- **Two consumer-facing `_ready` channels join the two internal `_inserted`
  channels.** The naming convention deliberately diverges: `_inserted` names a
  physical table write (an internal drain wake); `_ready` names a semantic
  governance event (the consumer-facing signal that a thing may be acted on).
  For entities the two coincide (first-detection has no further precondition);
  for legs they do not.

- **The readiness drain is new loop logic**, not a new `StreamSpec`. It is the
  only deviation from the "reuse `_driver`" grain established by
  P-classification.

- **The `state.flush` write path gains DB-owned leg seq (see Reversal).** The
  streaming branch stops stamping `ix.seq` on the legs and lets the column's
  `nextval('interaction_legs_seq')` DEFAULT (migration 0009 — already present, so
  no new migration) assign each leg its own seq; `seq` is dropped from the leg
  `DO UPDATE SET` so re-derives preserve it. The graph branch keeps supplying its
  own explicit per-edge seqs. Everything else is a read-side + notify-side
  addition.

## Reversal (implementation finding, issue #123)

The **premise that both legs share one deterministic `seq` is false for the
current source.** A leg's readiness is *not* all-or-nothing per seq: the request
and response legs carry **independent payload hashes**
(`state._legs_of` → `ix.request_payload_hash` / `ix.response_payload_hash`),
classified in **separate** P-classification transactions. So at one shared seq the
request leg can be ready (its payload classified) while the response leg is not — a
genuinely reachable half-ready seq. The #122 contiguous-prefix primitive therefore
returned a composite `(seq, leg_ordinal)` watermark, which the single-`BIGINT`
`processor_state.last_processed_seq` cannot losslessly persist (persist the seq and
strand the held sibling leg; persist one lower and re-deliver the delivered leg —
you cannot have both "not stranded" and "exactly-once").

**Resolution:** give each leg its **own** DB-owned `seq` (the rejected `nextval`
alternative above), decoupling leg seq from the in-memory `procedure.Interaction.seq`
(which is pure plumbing — `procedure.py` orders/cursors/dedups on `span.seq`, never
`ix.seq`, and is untouched, ADR-0025). The original replay-determinism objection
does not apply: re-derive preserves seq once it is dropped from the leg
`DO UPDATE SET` (cosmetic gaps only), and the `--scramble` byte gate already
excludes leg seq as an arrival-order-dependent passenger field. Consequences: leg
seq is now **arrival-order-dependent** (already documented as such in the scramble
conftest), and the composite cursor + `leg_type` tiebreaker are retired in favour
of a plain single-seq watermark. This is why the status is now **accepted**: the
design is proven in code.

**Follow-up (issue #133): `interaction_legs.original_seq` removed.** This reversal
also made the leg's `original_seq` inert. Its ADR-0004 purpose was to freeze a
first-emission value against a *mutating* `seq`; but a leg's `seq` is now DB-owned
and, like the frozen value, never mutates on re-derive (it is dropped from the leg
`DO UPDATE SET`), so `seq == original_seq` forever and the pair distinguishes
nothing. The field was write-once at INSERT and read by no consumer, so migration
`0011_drop_leg_original_seq` drops it (legs only — entity `original_seq` is still
read into `first_seen_seq` and stays). Reversible via `downgrade()`.

## Deliberately out of scope (evidence-bar posture, per ADR-0013 / ADR-0025)

- **Case-Y observed legs** — a future source emitting request and response as
  distinct spans with distinct payloads arriving apart. The distinct per-leg
  `seq` (each leg readying at its own seq) is the forward-compatible shape — the
  response leg simply readies later at its own higher seq, held by the
  contiguous-prefix cursor until then; the Case-Y *writer* is not built.

- **Entity mutation/retarget wakes.** The current source never mutates an
  entity's identity, so `dg_entity_ready` is first-detection only. When a
  mutation writer lands (ADR-0011 retarget), an `AFTER UPDATE WHEN NEW.seq IS
  DISTINCT FROM OLD.seq` trigger is an additive migration — exactly how
  migration 0005 → 0006 evolved the spans notify.

- **Re-classification un-readiness.** Readiness is a **latch** today because
  classifications are write-once (ADR-0024). The deferred re-classification hook
  (a `model_version` bump → truncate `payload_classifications` + reset the
  P-classification cursor) would transiently un-ready already-ready legs. If it
  ever lands, the readiness consumers' cursors must be reset together with that
  truncate. Documented here; not built.
