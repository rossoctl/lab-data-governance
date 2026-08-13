import { describe, it, expect } from 'vitest';
import { deriveSequenceDiagram } from './sequenceDiagram';
import type { Entity, Interaction, InteractionLeg } from './flow';

/**
 * The Interaction diagram's lifeline/message derivation. This is where the sequence
 * diagram's real coverage lives: jsdom cannot lay out or measure an SVG, so the
 * SHAPE of the diagram — which lifeline is in which column, which arrow points
 * which way, what is disclosed rather than drawn — is proven here as pure logic and
 * the render test is kept to what jsdom can honestly assert (see
 * InteractionDiagram.test.tsx).
 *
 * Messages are per **LEG**, not per interaction (ADR-0025): a request flows
 * caller → callee and its response flows back callee → caller, so a completed
 * interaction is two opposite-direction arrows at two different `seq`s. Every
 * assertion below is written against that model.
 *
 * The fixture helpers are deliberately identical to `graph.test.ts`'s, because the
 * two derivations consume the same `flatLegRows` rows and any divergence in how
 * they are FED would hide a divergence in how they behave.
 */

function ent(id: string, over: Partial<Entity> = {}): Entity {
  return {
    id,
    kind: 'agent',
    natural_key: `agent:(p,${id})`,
    display_name: `name-${id}`,
    detected_from: 'span',
    ...over,
  };
}

function leg(
  legType: 'request' | 'response',
  seq: number,
  over: Partial<InteractionLeg> = {},
): InteractionLeg {
  return {
    leg_type: legType,
    occurred_at: '2026-05-01T12:00:00Z',
    payload_hash: null,
    error: false,
    seq,
    ...over,
  };
}

/**
 * An interaction with BOTH legs — the normal completed shape. `reqSeq`/`respSeq`
 * are explicit because the trace-wide `seq` ordering drives BOTH the row order and
 * the column assignment, which is what most of these tests are about.
 */
function ix(
  id: string,
  caller: string | null,
  callee: string | null,
  reqSeq: number,
  respSeq: number,
  over: Partial<Interaction> = {},
): Interaction {
  return {
    id,
    caller_entity_id: caller,
    callee_entity_id: callee,
    summary: `summary-${id}`,
    parent_interaction_id: null,
    legs: [leg('request', reqSeq), leg('response', respSeq)],
    duration_seconds: 1,
    any_error: false,
    span_count: 1,
    anchor_count: 1,
    ...over,
  };
}

/** An interaction whose response has not arrived: one request leg only. */
function ixReqOnly(
  id: string,
  caller: string | null,
  callee: string | null,
  reqSeq: number,
  over: Partial<Interaction> = {},
): Interaction {
  return ix(id, caller, callee, reqSeq, reqSeq + 1, {
    legs: [leg('request', reqSeq)],
    duration_seconds: null,
    ...over,
  });
}

describe('deriveSequenceDiagram', () => {
  // --- Lifelines: one per PARTICIPATING entity, columned by first encounter.

  it('gives each participating entity a lifeline carrying its label, kind and natural key', () => {
    const d = deriveSequenceDiagram(
      [ent('e1', { kind: 'user', natural_key: 'user:(p,u)' }), ent('e2', { kind: 'tool' })],
      [ix('i1', 'e1', 'e2', 1, 2)],
    );

    expect(d.lifelines).toHaveLength(2);
    expect(d.lifelines[0]).toEqual({
      id: 'e1',
      label: 'name-e1',
      kind: 'user',
      naturalKey: 'user:(p,u)',
      index: 0,
    });
    expect(d.lifelines[1]).toMatchObject({ id: 'e2', kind: 'tool', index: 1 });
  });

  it('falls back to the entity id when display_name is blank', () => {
    // A head box with no visible text is unusable; the id is the only other field
    // guaranteed to be present.
    const d = deriveSequenceDiagram(
      [ent('e1', { display_name: '' }), ent('e2')],
      [ix('i1', 'e1', 'e2', 1, 2)],
    );

    expect(d.lifelines[0].label).toBe('e1');
  });

  it('columns lifelines in FIRST-ENCOUNTER order by leg seq, not by the entities read', () => {
    // THE substance of this module. The entities arrive in an order the API chose
    // (here: reverse of the call order), and column assignment must ignore it — the
    // initiator belongs on the left so the arrows read like the call flowed.
    const d = deriveSequenceDiagram(
      [ent('tool'), ent('agent'), ent('user')],
      [ix('a', 'user', 'agent', 1, 6), ix('b', 'agent', 'tool', 2, 3)],
    );

    expect(d.lifelines.map((l) => l.id)).toEqual(['user', 'agent', 'tool']);
    expect(d.lifelines.map((l) => l.index)).toEqual([0, 1, 2]);
  });

  it('claims the SOURCE column before the target within a leg', () => {
    // A target-first order would put the very first callee in column 0 and make
    // every subsequent arrow point backwards for no reason.
    const d = deriveSequenceDiagram([ent('a'), ent('b')], [ix('i1', 'a', 'b', 1, 2)]);

    expect(d.lifelines.map((l) => l.id)).toEqual(['a', 'b']);
  });

  it('assigns columns by leg seq even when the interactions arrive out of order', () => {
    // `flatLegRows` sorts, so the encounter order is the real chronology rather
    // than the array order the read happened to return.
    const d = deriveSequenceDiagram(
      [ent('x'), ent('y'), ent('z')],
      [ix('late', 'y', 'z', 5, 6), ix('early', 'x', 'y', 1, 2)],
    );

    expect(d.lifelines.map((l) => l.id)).toEqual(['x', 'y', 'z']);
  });

  it('gives an entity exactly ONE lifeline however many messages touch it', () => {
    const d = deriveSequenceDiagram(
      [ent('a'), ent('b'), ent('c')],
      [ix('i1', 'a', 'b', 1, 2), ix('i2', 'a', 'c', 3, 4), ix('i3', 'b', 'a', 5, 6)],
    );

    expect(d.lifelines.map((l) => l.id)).toEqual(['a', 'b', 'c']);
    expect(new Set(d.lifelines.map((l) => l.id)).size).toBe(3);
  });

  it('keeps a lifeline array position equal to its own index', () => {
    // The invariant the renderer relies on when it maps position → column x.
    const d = deriveSequenceDiagram(
      [ent('a'), ent('b'), ent('c'), ent('d')],
      [ix('i1', 'd', 'c', 1, 2), ix('i2', 'c', 'b', 3, 4), ix('i3', 'b', 'a', 5, 6)],
    );

    d.lifelines.forEach((l, i) => expect(l.index).toBe(i));
  });

  // --- Messages: one per leg, each in its OWN direction.

  it('maps a completed interaction to TWO messages pointing opposite ways', () => {
    // The request travels caller → callee; the response travels back. One arrow
    // would assert the response either did not exist or did not travel.
    const d = deriveSequenceDiagram([ent('e1'), ent('e2')], [ix('i1', 'e1', 'e2', 1, 2)]);

    expect(d.messages).toHaveLength(2);
    expect(d.messages[0]).toMatchObject({
      key: 'i1:request',
      interactionId: 'i1',
      legType: 'request',
      fromIndex: 0,
      toIndex: 1,
      seq: 1,
      label: '1',
    });
    expect(d.messages[1]).toMatchObject({
      key: 'i1:response',
      legType: 'response',
      // Swapped: the response arrow points back LEFT.
      fromIndex: 1,
      toIndex: 0,
      seq: 2,
      label: '2',
    });
  });

  it('points a response leg back toward a LOWER column index than its request', () => {
    // Stated as an inequality, not as fixed indices: the "arrows come back
    // leftwards" reading is the whole reason first-encounter ordering exists, and
    // it must hold for a mid-chain interaction too.
    const d = deriveSequenceDiagram(
      [ent('a'), ent('b'), ent('c')],
      [ix('outer', 'a', 'b', 1, 4), ix('inner', 'b', 'c', 2, 3)],
    );

    for (const m of d.messages.filter((x) => x.legType === 'response')) {
      expect(m.toIndex).toBeLessThan(m.fromIndex);
    }
  });

  it('gives each leg a distinct key at the (interaction, leg_type) grain', () => {
    // The interaction id alone is not unique per message: its two legs are two
    // rows, and a duplicate React key would drop one.
    const d = deriveSequenceDiagram([ent('e1'), ent('e2')], [ix('i1', 'e1', 'e2', 1, 2)]);

    expect(d.messages.map((m) => m.key)).toEqual(['i1:request', 'i1:response']);
    expect(new Set(d.messages.map((m) => m.key)).size).toBe(2);
  });

  it('orders messages by the trace-wide leg seq, across interactions', () => {
    // Interleaved legs: i1's response lands AFTER i2's whole call. The row order
    // must be the real chronology, not per-interaction grouping — and it must be
    // the same order the Flat tab lists, since both come from flatLegRows.
    const d = deriveSequenceDiagram(
      [ent('e1'), ent('e2'), ent('e3')],
      [ix('i1', 'e1', 'e2', 1, 4), ix('i2', 'e2', 'e3', 2, 3)],
    );

    expect(d.messages.map((m) => m.seq)).toEqual([1, 2, 3, 4]);
    expect(d.messages.map((m) => m.key)).toEqual([
      'i1:request',
      'i2:request',
      'i2:response',
      'i1:response',
    ]);
  });

  it('draws exactly ONE message for an in-flight interaction with only a request leg', () => {
    // No response leg exists, so no return arrow may be invented: a phantom
    // response would assert a reply that has not happened.
    const d = deriveSequenceDiagram([ent('e1'), ent('e2')], [ixReqOnly('i1', 'e1', 'e2', 1)]);

    expect(d.messages).toHaveLength(1);
    expect(d.messages[0]).toMatchObject({ key: 'i1:request', fromIndex: 0, toIndex: 1 });
    // Both participants still get a lifeline — the request arrow lands on one.
    expect(d.lifelines).toHaveLength(2);
  });

  it('draws no messages for an interaction with no legs at all', () => {
    // Legs are the message source, so an identity row with none yields none — and
    // this is NOT an unresolved-participant drop, so it is not reported there.
    const d = deriveSequenceDiagram(
      [ent('e1'), ent('e2')],
      [ix('i1', 'e1', 'e2', 1, 2, { legs: [] })],
    );

    expect(d.messages).toHaveLength(0);
    expect(d.dropped).toHaveLength(0);
    // And no lifelines: nothing touches either entity. They are not `isolated`
    // either — the identity row names them — so the `dropped`/`isolated` split does
    // not account for this one, which is why the empty state says "no interaction
    // has two resolved participants … yet" rather than blaming a defect.
    expect(d.lifelines).toHaveLength(0);
    expect(d.isolated).toHaveLength(0);
  });

  it('tolerates a response leg arriving without a request leg', () => {
    // Defensive: the read is eventually consistent, so leg presence is not
    // guaranteed to be prefix-shaped. The response still draws its own direction —
    // and note the CALLEE therefore takes column 0, because it is genuinely the
    // first participant encountered.
    const d = deriveSequenceDiagram(
      [ent('e1'), ent('e2')],
      [ix('i1', 'e1', 'e2', 1, 2, { legs: [leg('response', 2)] })],
    );

    expect(d.messages).toHaveLength(1);
    expect(d.messages[0]).toMatchObject({ legType: 'response', fromIndex: 0, toIndex: 1 });
    expect(d.lifelines.map((l) => l.id)).toEqual(['e2', 'e1']);
  });

  // --- The tooltip text: the interaction's summary, now that the label is a seq.

  it("carries the interaction's summary onto every one of its legs as the title", () => {
    const d = deriveSequenceDiagram([ent('e1'), ent('e2')], [ix('i1', 'e1', 'e2', 1, 2)]);

    expect(d.messages.map((m) => m.title)).toEqual(['summary-i1', 'summary-i1']);
  });

  it('falls back to the interaction id for the title when summary is null', () => {
    const d = deriveSequenceDiagram(
      [ent('e1'), ent('e2')],
      [ix('i1', 'e1', 'e2', 1, 2, { summary: null })],
    );

    expect(d.messages[0].title).toBe('i1');
    // The LABEL is unaffected — it is the seq, which always exists.
    expect(d.messages[0].label).toBe('1');
  });

  // --- Error colour: the LEG's own error, tri-state.

  it("flags each message from its OWN leg's error, not the interaction's any_error", () => {
    // A message is one leg, so it must show that leg's outcome. `any_error` ORs
    // both legs, so using it would redden the successful request arrow because the
    // response later failed — reporting a failure ABOVE where it happened, in a
    // view whose vertical axis IS time.
    const d = deriveSequenceDiagram(
      [ent('e1'), ent('e2')],
      [
        ix('i1', 'e1', 'e2', 1, 2, {
          legs: [leg('request', 1, { error: false }), leg('response', 2, { error: true })],
          any_error: true,
        }),
      ],
    );

    expect(d.messages.find((m) => m.legType === 'request')!.isError).toBe(false);
    expect(d.messages.find((m) => m.legType === 'response')!.isError).toBe(true);
  });

  it('flags a leg as an error only when its error is exactly true — null is not a failure', () => {
    // A null leg error is "not yet aggregated", NOT "failed" — the same
    // never-render-unknown-as-a-verdict rule the graph's edges and the lineage
    // status follow.
    const d = deriveSequenceDiagram(
      [ent('e1'), ent('e2')],
      [
        ix('i1', 'e1', 'e2', 1, 2, {
          legs: [leg('request', 1, { error: null }), leg('response', 2, { error: false })],
        }),
        ix('i2', 'e1', 'e2', 3, 4, {
          legs: [leg('request', 3, { error: true }), leg('response', 4, { error: null })],
        }),
      ],
    );

    const by = (k: string) => d.messages.find((m) => m.key === k)!;
    expect(by('i1:request').isError).toBe(false); // null → not an error
    expect(by('i1:response').isError).toBe(false);
    expect(by('i2:request').isError).toBe(true);
    expect(by('i2:response').isError).toBe(false); // null → not an error
  });

  it('ignores a true any_error when the legs themselves did not fail', () => {
    // The aggregate must not be able to redden a leg that reports success: the leg
    // is the finer, truer grain.
    const d = deriveSequenceDiagram(
      [ent('e1'), ent('e2')],
      [ix('i1', 'e1', 'e2', 1, 2, { any_error: true })],
    );

    expect(d.messages.every((m) => m.isError === false)).toBe(true);
  });

  // --- EDGE CASE: a null / dangling caller or callee (unresolved participant).

  it('does not drop an interaction with a null callee silently — it is reported', () => {
    const d = deriveSequenceDiagram([ent('e1')], [ix('i1', 'e1', null, 1, 2)]);

    expect(d.messages).toHaveLength(0);
    expect(d.dropped).toHaveLength(1);
    expect(d.dropped[0]).toMatchObject({
      id: 'i1',
      label: 'summary-i1',
      missing: 'callee',
      resolvedEntityId: 'e1',
    });
  });

  it('reports an unresolved participant ONCE per interaction, not once per leg', () => {
    // The null callee is one defect on the identity row, shared by both legs.
    // Reporting it twice would inflate the count AND make the number depend on
    // response timing: a one-leg interaction would report 1 and a two-leg one 2 for
    // the identical data problem. The leg count is carried as a field instead.
    const d = deriveSequenceDiagram([ent('e1')], [ix('i1', 'e1', null, 1, 2)]);

    expect(d.dropped).toHaveLength(1);
    expect(d.dropped[0].legCount).toBe(2);
  });

  it('reports the same single entry for a one-leg unresolved interaction, with legCount 1', () => {
    const d = deriveSequenceDiagram([ent('e1')], [ixReqOnly('i1', 'e1', null, 1)]);

    expect(d.dropped).toHaveLength(1);
    expect(d.dropped[0].legCount).toBe(1);
  });

  it('reports a null caller as missing `caller`, keeping the resolved callee', () => {
    const d = deriveSequenceDiagram([ent('e2')], [ix('i1', null, 'e2', 1, 2)]);

    expect(d.messages).toHaveLength(0);
    expect(d.dropped[0]).toMatchObject({ missing: 'caller', resolvedEntityId: 'e2' });
  });

  it('reports an interaction with BOTH ends unresolved as missing `both`', () => {
    const d = deriveSequenceDiagram([ent('e1')], [ix('i1', null, null, 1, 2)]);

    expect(d.dropped[0]).toMatchObject({ missing: 'both', resolvedEntityId: null });
  });

  it('drops (and reports) an interaction naming an entity the entities read does not carry', () => {
    // A dangling endpoint has no lifeline to attach to; the inconsistency is worth
    // surfacing rather than pointing an arrow at column `undefined`.
    const d = deriveSequenceDiagram([ent('e1')], [ix('i1', 'e1', 'ghost', 1, 2)]);

    expect(d.messages).toHaveLength(0);
    expect(d.dropped[0]).toMatchObject({ missing: 'callee', resolvedEntityId: 'e1' });
  });

  it('keeps the drawable interactions when only SOME are unresolved', () => {
    const d = deriveSequenceDiagram(
      [ent('e1'), ent('e2')],
      [ix('good', 'e1', 'e2', 1, 2), ix('bad', 'e1', null, 3, 4)],
    );

    expect(d.messages.map((m) => m.key)).toEqual(['good:request', 'good:response']);
    expect(d.dropped.map((x) => x.id)).toEqual(['bad']);
  });

  it('gives an entity reachable ONLY through a dropped interaction no lifeline', () => {
    // No message can land on it, so a column for it would be an empty one — and a
    // sequence diagram's columns assert participation in the drawn exchange. It is
    // not reported as `isolated` either (the identity row does name it); the
    // `dropped` notice is what accounts for its absence, and double-counting it
    // under two explanations would misdescribe one defect as two.
    const d = deriveSequenceDiagram(
      [ent('lonely'), ent('a'), ent('b')],
      [ix('good', 'a', 'b', 1, 2), ix('bad', 'lonely', null, 3, 4)],
    );

    expect(d.lifelines.map((l) => l.id)).toEqual(['a', 'b']);
    expect(d.isolated).toEqual([]);
    expect(d.dropped.map((x) => x.id)).toEqual(['bad']);
  });

  it('does not shift the columns of the drawable participants when an interaction is dropped', () => {
    // The claim pass walks drawable rows only, so a dropped interaction's seqs
    // cannot silently claim a column ahead of a real participant.
    const d = deriveSequenceDiagram(
      [ent('a'), ent('b'), ent('c')],
      [ix('bad', 'c', null, 1, 2), ix('good', 'a', 'b', 3, 4)],
    );

    expect(d.lifelines.map((l) => l.id)).toEqual(['a', 'b']);
    expect(d.messages[0]).toMatchObject({ fromIndex: 0, toIndex: 1 });
  });

  // --- EDGE CASE: an entity no interaction names (isolated) — omitted, disclosed.

  it('omits an entity no interaction names, and reports it in `isolated`', () => {
    // The deliberate DIVERGENCE from the graph, which draws such an entity as a
    // bare dashed node. A lifeline exists to be the thing arrows land on: an
    // untouched one adds a column, pushes every real participant sideways, and
    // asserts a participation that did not happen. Omitted — but never silently.
    const d = deriveSequenceDiagram(
      [ent('e1'), ent('e2'), ent('lonely')],
      [ix('i1', 'e1', 'e2', 1, 2)],
    );

    expect(d.lifelines.map((l) => l.id)).toEqual(['e1', 'e2']);
    expect(d.isolated).toEqual([{ id: 'lonely', label: 'name-lonely' }]);
  });

  it('reports every entity as isolated when there are no interactions at all', () => {
    const d = deriveSequenceDiagram([ent('e1'), ent('e2')], []);

    expect(d.lifelines).toEqual([]);
    expect(d.messages).toEqual([]);
    expect(d.isolated.map((e) => e.id)).toEqual(['e1', 'e2']);
  });

  it('falls back to the id for an isolated entity with a blank display_name', () => {
    const d = deriveSequenceDiagram([ent('lonely', { display_name: '' })], []);

    expect(d.isolated).toEqual([{ id: 'lonely', label: 'lonely' }]);
  });

  it('does not call an entity isolated merely because it has no lifeline', () => {
    // The two absences mean different things and are disclosed differently:
    // `isolated` is "no interaction names it", which is a fact about the trace;
    // "has no lifeline" also covers "its interaction was dropped", which is a fact
    // about the data quality and is reported as such.
    const d = deriveSequenceDiagram([ent('e1')], [ix('i1', 'e1', null, 1, 2)]);

    expect(d.lifelines).toEqual([]);
    expect(d.isolated).toEqual([]);
    expect(d.dropped).toHaveLength(1);
  });

  // --- EDGE CASE: a self-call (caller === callee).

  it('keeps BOTH legs of a self-call as messages and flags each', () => {
    // A straight horizontal arrow between one lifeline and itself has zero length
    // and is invisible, so the flag is what tells the renderer to draw the loop.
    // Swapping caller and callee when they are the same entity is a no-op, so the
    // response leg is a self-message too and needs the same treatment.
    const d = deriveSequenceDiagram([ent('e1')], [ix('i1', 'e1', 'e1', 1, 2)]);

    expect(d.messages).toHaveLength(2);
    expect(d.messages[0]).toMatchObject({
      key: 'i1:request',
      fromIndex: 0,
      toIndex: 0,
      isSelfCall: true,
      label: '1',
    });
    expect(d.messages[1]).toMatchObject({
      key: 'i1:response',
      fromIndex: 0,
      toIndex: 0,
      isSelfCall: true,
      label: '2',
    });
    expect(d.dropped).toHaveLength(0);
  });

  it('gives a self-caller exactly one lifeline, not two', () => {
    // The claim pass sees the same id as source and as target; a naive
    // push-per-endpoint would produce a duplicate column and a diagram that
    // implied two participants.
    const d = deriveSequenceDiagram([ent('e1')], [ix('i1', 'e1', 'e1', 1, 2)]);

    expect(d.lifelines).toHaveLength(1);
    expect(d.lifelines[0]).toMatchObject({ id: 'e1', index: 0 });
  });

  it('does not flag a normal interaction\'s legs as self-calls', () => {
    const d = deriveSequenceDiagram([ent('e1'), ent('e2')], [ix('i1', 'e1', 'e2', 1, 2)]);

    expect(d.messages.every((m) => m.isSelfCall === false)).toBe(true);
  });

  it('does not report an entity as isolated because its only interaction is a self-call', () => {
    const d = deriveSequenceDiagram([ent('e1')], [ix('i1', 'e1', 'e1', 1, 2)]);

    expect(d.isolated).toEqual([]);
  });

  // --- Empty / degenerate inputs.

  it('returns an empty diagram for no entities and no interactions', () => {
    const d = deriveSequenceDiagram([], []);

    expect(d).toEqual({ lifelines: [], messages: [], dropped: [], isolated: [] });
  });

  it('reports interactions but no lifelines when entities is empty', () => {
    // Nothing is drawable, and the reason is disclosed rather than swallowed.
    const d = deriveSequenceDiagram([], [ix('i1', 'e1', 'e2', 1, 2)]);

    expect(d.lifelines).toEqual([]);
    expect(d.messages).toEqual([]);
    expect(d.dropped).toHaveLength(1);
    expect(d.dropped[0].missing).toBe('both');
    expect(d.isolated).toEqual([]);
  });

  it('is a pure function of its inputs — it mutates neither argument', () => {
    // Notably including the leg arrays: the seq sort must not reorder the caller's
    // own `legs` in place.
    const entities = [ent('e1'), ent('e2')];
    const interactions = [ix('i1', 'e1', 'e2', 2, 1)];
    const entitiesCopy = structuredClone(entities);
    const interactionsCopy = structuredClone(interactions);

    deriveSequenceDiagram(entities, interactions);

    expect(entities).toEqual(entitiesCopy);
    expect(interactions).toEqual(interactionsCopy);
  });

  it('handles a realistic mixed trace: every edge case at once', () => {
    // A caller→callee chain + a repeat call + a self-call + an in-flight call + an
    // unresolved end + an isolated entity, all in one derivation.
    const d = deriveSequenceDiagram(
      [
        // Deliberately NOT in call order, so the column assignment is doing real
        // work rather than echoing the read.
        ent('orphan', { kind: 'external_service' }),
        ent('tool', { kind: 'tool' }),
        ent('agent', { kind: 'agent' }),
        ent('user', { kind: 'user' }),
      ],
      [
        ix('a', 'user', 'agent', 1, 10),
        ix('b', 'agent', 'tool', 2, 3),
        ix('c', 'agent', 'tool', 4, 5, {
          legs: [leg('request', 4, { error: false }), leg('response', 5, { error: true })],
          any_error: true,
        }),
        ix('d', 'agent', 'agent', 6, 7), // self-call
        ixReqOnly('e', 'agent', 'tool', 8), // response still in flight
        ix('f', 'tool', null, 9, 11), // unresolved callee
      ],
    );

    // Columns: user (seq 1 source), agent (seq 1 target), tool (seq 2 target).
    // `orphan` gets none — nothing names it.
    expect(d.lifelines.map((l) => l.id)).toEqual(['user', 'agent', 'tool']);
    // 2 + 2 + 2 + 2 + 1 = 9 messages; f contributes none. Ordered by seq.
    expect(d.messages.map((m) => m.seq)).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 10]);
    expect(d.messages.map((m) => m.key)).toEqual([
      'a:request',
      'b:request',
      'b:response',
      'c:request',
      'c:response',
      'd:request',
      'd:response',
      'e:request',
      'a:response',
    ]);
    // The chain reads left-to-right on the way out and back again on the return.
    expect(d.messages[0]).toMatchObject({ fromIndex: 0, toIndex: 1 }); // user → agent
    expect(d.messages[1]).toMatchObject({ fromIndex: 1, toIndex: 2 }); // agent → tool
    expect(d.messages[2]).toMatchObject({ fromIndex: 2, toIndex: 1 }); // tool → agent
    expect(d.messages[8]).toMatchObject({ fromIndex: 1, toIndex: 0 }); // agent → user
    // Reported once, though two of its legs were lost.
    expect(d.dropped.map((x) => x.id)).toEqual(['f']);
    expect(d.dropped[0].legCount).toBe(2);
    // Omitted from the columns, disclosed here instead.
    expect(d.isolated.map((x) => x.id)).toEqual(['orphan']);
    // Only the failed LEG is flagged, not its sibling.
    expect(d.messages.find((m) => m.key === 'c:response')!.isError).toBe(true);
    expect(d.messages.find((m) => m.key === 'c:request')!.isError).toBe(false);
    // Both legs of the self-call, both on the same column.
    const selfies = d.messages.filter((m) => m.isSelfCall);
    expect(selfies.map((m) => m.key)).toEqual(['d:request', 'd:response']);
    expect(selfies.every((m) => m.fromIndex === 1 && m.toIndex === 1)).toBe(true);
  });
});
