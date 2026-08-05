import { describe, it, expect } from 'vitest';
import { deriveGraph } from './graph';
import type { Entity, Interaction, InteractionLeg } from './flow';

/**
 * The Execution Flow graph's node/edge derivation. This is where the graph view's
 * real coverage lives: jsdom cannot lay out or measure an SVG, so the SHAPE of the
 * graph is proven here as pure logic and the render test is kept to what jsdom can
 * honestly assert (see ExecutionFlowGraph.test.tsx).
 *
 * Edges are per **LEG**, not per interaction (ADR-0025): a request flows
 * caller → callee and its response flows back callee → caller, so a completed
 * interaction is two opposite-direction edges at two different `seq`s. Every
 * assertion below is written against that model.
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
 * are explicit because the trace-wide `seq` ordering (and therefore the edge
 * order) is exactly what several of these tests are about.
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

/**
 * An interaction carrying ONLY a response leg — the mirror of `ixReqOnly`, and not a
 * hypothetical: nothing in `flatLegRows` or `legOfType` requires a response's request to
 * be present, so the derivation has to be honest about this shape rather than assume it
 * away. Used to pin that such an interaction still draws an arrow and leaves neither
 * participant flagged isolated.
 */
function ixRespOnly(
  id: string,
  caller: string | null,
  callee: string | null,
  respSeq: number,
  over: Partial<Interaction> = {},
): Interaction {
  return ix(id, caller, callee, respSeq, respSeq + 1, {
    legs: [leg('response', respSeq)],
    duration_seconds: null,
    ...over,
  });
}

describe('deriveGraph', () => {
  // --- Nodes. Unchanged by the leg model: a node is still an entity.

  it('maps every entity to a node labelled with its display_name', () => {
    const g = deriveGraph([ent('e1'), ent('e2')], [ix('i1', 'e1', 'e2', 1, 2)]);

    expect(g.nodes).toHaveLength(2);
    expect(g.nodes.map((n) => n.id)).toEqual(['e1', 'e2']);
    expect(g.nodes.map((n) => n.label)).toEqual(['name-e1', 'name-e2']);
  });

  it('carries each entity kind and natural key onto its node (the colour/tooltip source)', () => {
    const g = deriveGraph([ent('e1', { kind: 'tool', natural_key: 'tool:(p,svc)' })], []);

    expect(g.nodes[0].kind).toBe('tool');
    expect(g.nodes[0].naturalKey).toBe('tool:(p,svc)');
  });

  it('falls back to the entity id when display_name is blank', () => {
    // A node with no visible text is unusable; the id is the only other field
    // guaranteed to be present.
    const g = deriveGraph([ent('e1', { display_name: '' })], []);

    expect(g.nodes[0].label).toBe('e1');
  });

  // --- THE leg model: one edge per leg, each in its OWN direction.

  it('maps a completed interaction to TWO edges pointing opposite ways', () => {
    // The whole point of the change. The request travels caller → callee; the
    // response travels back callee → caller. One arrow would assert that the
    // response either did not exist or did not travel.
    const g = deriveGraph([ent('e1'), ent('e2')], [ix('i1', 'e1', 'e2', 1, 2)]);

    expect(g.edges).toHaveLength(2);
    expect(g.edges[0]).toMatchObject({
      id: 'i1:request',
      interactionId: 'i1',
      legType: 'request',
      source: 'e1',
      target: 'e2',
      label: '1',
      seq: 1,
    });
    expect(g.edges[1]).toMatchObject({
      id: 'i1:response',
      interactionId: 'i1',
      legType: 'response',
      // Swapped: callee → caller.
      source: 'e2',
      target: 'e1',
      label: '2',
      seq: 2,
    });
  });

  it("labels each edge with its OWN leg's seq, not the interaction's", () => {
    // A leg has exactly one seq, and the two legs of one interaction have
    // different ones — frequently far apart, since other interactions' legs
    // interleave.
    const g = deriveGraph([ent('e1'), ent('e2')], [ix('i1', 'e1', 'e2', 3, 9)]);

    expect(g.edges.map((e) => e.label)).toEqual(['3', '9']);
    expect(g.edges.map((e) => e.seq)).toEqual([3, 9]);
  });

  it('gives each leg a distinct edge id at the (interaction, leg_type) grain', () => {
    // The interaction id alone is no longer unique per edge: a topology model
    // with two edges sharing an id silently drops one of them.
    const g = deriveGraph([ent('e1'), ent('e2')], [ix('i1', 'e1', 'e2', 1, 2)]);

    expect(g.edges.map((e) => e.id)).toEqual(['i1:request', 'i1:response']);
    expect(new Set(g.edges.map((e) => e.id)).size).toBe(2);
  });

  it('orders edges by the trace-wide leg seq, across interactions', () => {
    // Interleaved legs: i1's response lands AFTER i2's whole call. The edge order
    // must be the real chronology, not per-interaction grouping — and it must be
    // the same order the Flat tab lists, since both come from flatLegRows.
    const g = deriveGraph(
      [ent('e1'), ent('e2'), ent('e3')],
      [ix('i1', 'e1', 'e2', 1, 4), ix('i2', 'e2', 'e3', 2, 3)],
    );

    expect(g.edges.map((e) => e.seq)).toEqual([1, 2, 3, 4]);
    expect(g.edges.map((e) => e.id)).toEqual([
      'i1:request',
      'i2:request',
      'i2:response',
      'i1:response',
    ]);
  });

  it('sorts by seq even when the interactions arrive out of order', () => {
    const g = deriveGraph(
      [ent('e1'), ent('e2')],
      [ix('late', 'e1', 'e2', 5, 6), ix('early', 'e1', 'e2', 1, 2)],
    );

    expect(g.edges.map((e) => e.seq)).toEqual([1, 2, 5, 6]);
  });

  it('draws exactly ONE edge for an in-flight interaction with only a request leg', () => {
    // No response leg exists, so no return arrow may be invented: a phantom
    // response edge would assert a reply that has not happened.
    const g = deriveGraph([ent('e1'), ent('e2')], [ixReqOnly('i1', 'e1', 'e2', 1)]);

    expect(g.edges).toHaveLength(1);
    expect(g.edges[0]).toMatchObject({
      id: 'i1:request',
      legType: 'request',
      source: 'e1',
      target: 'e2',
      label: '1',
    });
  });

  it('draws no edges for an interaction with no legs at all', () => {
    // Legs are the edge source now, so an identity row with none yields none —
    // and this is NOT an unresolved-participant drop, so it is not reported there.
    const g = deriveGraph([ent('e1'), ent('e2')], [ix('i1', 'e1', 'e2', 1, 2, { legs: [] })]);

    expect(g.edges).toHaveLength(0);
    expect(g.dropped).toHaveLength(0);
    // Both participants are still named by the identity row, so neither is
    // isolated.
    expect(g.nodes.every((n) => !n.isIsolated)).toBe(true);
  });

  it('tolerates a response leg arriving without a request leg', () => {
    // Defensive: the read is eventually consistent, so leg presence is not
    // guaranteed to be prefix-shaped. The response still draws its own direction.
    const g = deriveGraph(
      [ent('e1'), ent('e2')],
      [ix('i1', 'e1', 'e2', 1, 2, { legs: [leg('response', 2)] })],
    );

    expect(g.edges).toHaveLength(1);
    expect(g.edges[0]).toMatchObject({ source: 'e2', target: 'e1', legType: 'response' });
  });

  // --- The tooltip text: the interaction's summary, now that the label is a seq.

  it("carries the interaction's summary onto every one of its legs as the title", () => {
    const g = deriveGraph([ent('e1'), ent('e2')], [ix('i1', 'e1', 'e2', 1, 2)]);

    expect(g.edges.map((e) => e.title)).toEqual(['summary-i1', 'summary-i1']);
  });

  it('falls back to the interaction id for the title when summary is null', () => {
    const g = deriveGraph(
      [ent('e1'), ent('e2')],
      [ix('i1', 'e1', 'e2', 1, 2, { summary: null })],
    );

    expect(g.edges[0].title).toBe('i1');
    // The LABEL is unaffected — it is the seq, which always exists.
    expect(g.edges[0].label).toBe('1');
  });

  // --- Error colour: the LEG's own error, tri-state.

  it("colours each edge from its OWN leg's error, not the interaction's any_error", () => {
    // An edge represents one leg, so it must show that leg's outcome. `any_error`
    // ORs both legs, so using it would paint the successful request arrow red
    // because the response later failed — reporting a failure at a point in the
    // trace where none had happened yet.
    const g = deriveGraph(
      [ent('e1'), ent('e2')],
      [
        ix('i1', 'e1', 'e2', 1, 2, {
          legs: [leg('request', 1, { error: false }), leg('response', 2, { error: true })],
          any_error: true,
        }),
      ],
    );

    expect(g.edges.find((e) => e.legType === 'request')!.isError).toBe(false);
    expect(g.edges.find((e) => e.legType === 'response')!.isError).toBe(true);
  });

  it('flags a leg as an error only when its error is exactly true — null is not a failure', () => {
    // A null leg error is "not yet aggregated", NOT "failed" — the same
    // never-render-unknown-as-a-verdict rule the lineage status follows.
    const g = deriveGraph(
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

    const by = (id: string) => g.edges.find((e) => e.id === id)!;
    expect(by('i1:request').isError).toBe(false); // null → not an error
    expect(by('i1:response').isError).toBe(false);
    expect(by('i2:request').isError).toBe(true);
    expect(by('i2:response').isError).toBe(false); // null → not an error
  });

  it('ignores a true any_error when the legs themselves did not fail', () => {
    // The aggregate must not be able to redden a leg that reports success: the
    // leg is the finer, truer grain.
    const g = deriveGraph(
      [ent('e1'), ent('e2')],
      [ix('i1', 'e1', 'e2', 1, 2, { any_error: true })],
    );

    expect(g.edges.every((e) => e.isError === false)).toBe(true);
  });

  // --- EDGE CASE: a null caller/callee (unresolved participant).

  it('does not drop an interaction with a null callee silently — it is reported', () => {
    const g = deriveGraph([ent('e1')], [ix('i1', 'e1', null, 1, 2)]);

    expect(g.edges).toHaveLength(0);
    expect(g.dropped).toHaveLength(1);
    expect(g.dropped[0]).toMatchObject({
      id: 'i1',
      label: 'summary-i1',
      missing: 'callee',
      resolvedEntityId: 'e1',
    });
  });

  it('reports an unresolved participant ONCE per interaction, not once per leg', () => {
    // The null callee is one defect on the identity row, shared by both legs.
    // Reporting it twice would inflate the count AND make the number depend on
    // response timing: a one-leg interaction would report 1 and a two-leg one 2
    // for the identical data problem. The leg count is carried as a field instead.
    const g = deriveGraph([ent('e1')], [ix('i1', 'e1', null, 1, 2)]);

    expect(g.dropped).toHaveLength(1);
    expect(g.dropped[0].legCount).toBe(2);
  });

  it('reports the same single entry for a one-leg unresolved interaction, with legCount 1', () => {
    const g = deriveGraph([ent('e1')], [ixReqOnly('i1', 'e1', null, 1)]);

    expect(g.dropped).toHaveLength(1);
    expect(g.dropped[0].legCount).toBe(1);
  });

  it('reports a null caller as missing `caller`, keeping the resolved callee', () => {
    const g = deriveGraph([ent('e2')], [ix('i1', null, 'e2', 1, 2)]);

    expect(g.edges).toHaveLength(0);
    expect(g.dropped[0]).toMatchObject({ missing: 'caller', resolvedEntityId: 'e2' });
  });

  it('reports an interaction with BOTH ends unresolved as missing `both`', () => {
    const g = deriveGraph([ent('e1')], [ix('i1', null, null, 1, 2)]);

    expect(g.dropped[0]).toMatchObject({ missing: 'both', resolvedEntityId: null });
  });

  it('drops (and reports) an interaction naming an entity the entities read does not carry', () => {
    // A dangling endpoint would make the topology model invalid; it is an
    // inconsistency worth surfacing rather than crashing on.
    const g = deriveGraph([ent('e1')], [ix('i1', 'e1', 'ghost', 1, 2)]);

    expect(g.edges).toHaveLength(0);
    expect(g.dropped[0]).toMatchObject({ missing: 'callee', resolvedEntityId: 'e1' });
  });

  it('keeps the drawable interactions when only SOME are unresolved', () => {
    const g = deriveGraph(
      [ent('e1'), ent('e2')],
      [ix('good', 'e1', 'e2', 1, 2), ix('bad', 'e1', null, 3, 4)],
    );

    // Both legs of the good one survive; neither leg of the bad one is drawn.
    expect(g.edges.map((e) => e.id)).toEqual(['good:request', 'good:response']);
    expect(g.dropped.map((d) => d.id)).toEqual(['bad']);
  });

  // --- EDGE CASE: an entity no interaction names (isolated node).

  it('keeps an entity with no interactions as an isolated node rather than dropping it', () => {
    const g = deriveGraph([ent('e1'), ent('e2'), ent('lonely')], [ix('i1', 'e1', 'e2', 1, 2)]);

    expect(g.nodes.map((n) => n.id)).toContain('lonely');
    expect(g.nodes.find((n) => n.id === 'lonely')!.isIsolated).toBe(true);
    expect(g.nodes.find((n) => n.id === 'e1')!.isIsolated).toBe(false);
    expect(g.nodes.find((n) => n.id === 'e2')!.isIsolated).toBe(false);
  });

  it('does not call an entity isolated when its only interaction was dropped for the OTHER end', () => {
    // The interaction could not be drawn, but it still proves e1 participated —
    // so e1 is genuinely not isolated, and saying otherwise would misreport it.
    const g = deriveGraph([ent('e1')], [ix('i1', 'e1', null, 1, 2)]);

    expect(g.nodes[0].isIsolated).toBe(false);
    expect(g.edges).toHaveLength(0);
  });

  it('marks every node isolated when there are no interactions at all', () => {
    const g = deriveGraph([ent('e1'), ent('e2')], []);

    expect(g.nodes.every((n) => n.isIsolated)).toBe(true);
    expect(g.edges).toHaveLength(0);
  });

  // --- First-encounter ORDER (encounterIndex): the slot the view lays each
  // entity out at. Derivation, not layout — the pixels are the component's
  // business, this is "when did the trace first touch this entity".

  it('numbers entities in the order the legs first touch them, source before target', () => {
    // The whole point: slot 0 is where the trace STARTED. Within one edge the
    // source is the earlier sighting, because that is the direction the leg
    // travelled.
    const g = deriveGraph(
      [ent('e1'), ent('e2'), ent('e3')],
      [ix('i1', 'e1', 'e2', 1, 4), ix('i2', 'e2', 'e3', 2, 3)],
    );

    const slot = (id: string) => g.nodes.find((n) => n.id === id)!.encounterIndex;
    expect(slot('e1')).toBe(0);
    expect(slot('e2')).toBe(1);
    expect(slot('e3')).toBe(2);
  });

  it('walks the legs in seq order, not the interactions in arrival order', () => {
    // `late` is listed first but happened second, so its callee must NOT claim an
    // earlier slot than `early`'s. The ordering has to come off the seq-sorted
    // edges or it is a fact about the API's response order instead of the trace.
    const g = deriveGraph(
      [ent('a'), ent('b'), ent('c')],
      [ixReqOnly('late', 'a', 'c', 5), ixReqOnly('early', 'a', 'b', 1)],
    );

    const slot = (id: string) => g.nodes.find((n) => n.id === id)!.encounterIndex;
    expect(slot('a')).toBe(0);
    expect(slot('b')).toBe(1); // seq 1
    expect(slot('c')).toBe(2); // seq 5
  });

  it('gives an entity first seen as a TARGET its slot at that moment', () => {
    // `callee` never calls anything, so its only appearance is as a target. There
    // is nothing earlier to give it, so it takes the slot right after its caller.
    const g = deriveGraph(
      [ent('callee'), ent('caller')],
      [ixReqOnly('i1', 'caller', 'callee', 1)],
    );

    // Note `nodes` is in ENTITIES order — `callee` is first in the array — while
    // the encounter slots are the trace's order. The two are deliberately
    // different things.
    expect(g.nodes.map((n) => n.id)).toEqual(['callee', 'caller']);
    expect(g.nodes.find((n) => n.id === 'caller')!.encounterIndex).toBe(0);
    expect(g.nodes.find((n) => n.id === 'callee')!.encounterIndex).toBe(1);
  });

  it('does not renumber an entity on its second, third or later sighting', () => {
    // First encounter, not latest: an entity that keeps being called must not
    // creep further down the chronology every time it is named.
    const g = deriveGraph(
      [ent('hub'), ent('x'), ent('y')],
      [ix('i1', 'hub', 'x', 1, 2), ix('i2', 'hub', 'y', 3, 4)],
    );

    expect(g.nodes.find((n) => n.id === 'hub')!.encounterIndex).toBe(0);
    expect(g.nodes.find((n) => n.id === 'x')!.encounterIndex).toBe(1);
    expect(g.nodes.find((n) => n.id === 'y')!.encounterIndex).toBe(2);
  });

  it('spends only ONE slot on a self-call, not two', () => {
    // Both ends of a self-edge are the same entity. Consuming two slots would
    // leave a gap in the chronology — an unused slot with nothing at it.
    const g = deriveGraph(
      [ent('solo'), ent('next')],
      [ix('i1', 'solo', 'solo', 1, 2), ixReqOnly('i2', 'solo', 'next', 3)],
    );

    expect(g.nodes.find((n) => n.id === 'solo')!.encounterIndex).toBe(0);
    expect(g.nodes.find((n) => n.id === 'next')!.encounterIndex).toBe(1);
  });

  it('sorts entities the legs never reach LAST, in entities order', () => {
    // An isolated entity was never encountered, so any slot in the middle of the
    // chain would be a fabricated claim about when it appeared. Last also keeps it
    // out of the readable chain, matching its dashed "unconnected" treatment.
    const g = deriveGraph(
      [ent('lonely1'), ent('e1'), ent('lonely2'), ent('e2')],
      [ixReqOnly('i1', 'e1', 'e2', 1)],
    );

    const slot = (id: string) => g.nodes.find((n) => n.id === id)!.encounterIndex;
    expect(slot('e1')).toBe(0);
    expect(slot('e2')).toBe(1);
    // The trailing group keeps `entities` order among itself — the API's order,
    // which is stable for a trace, so a reload redraws the identical picture.
    expect(slot('lonely1')).toBe(2);
    expect(slot('lonely2')).toBe(3);
  });

  it('also sorts last an entity named only by a DROPPED interaction', () => {
    // Not isolated (it demonstrably participated) but it contributes no edge, so
    // the walk never reaches it and there is no encounter to place it at. The two
    // notions are deliberately not each other's negation.
    const g = deriveGraph(
      [ent('halfway'), ent('e1'), ent('e2')],
      [ixReqOnly('i1', 'e1', 'e2', 1), ixReqOnly('bad', 'halfway', null, 2)],
    );

    expect(g.nodes.find((n) => n.id === 'halfway')!.isIsolated).toBe(false);
    expect(g.nodes.find((n) => n.id === 'halfway')!.encounterIndex).toBe(2);
  });

  it('numbers every entity when there are no interactions at all', () => {
    // Nothing was encountered, so the whole set is the trailing group and falls
    // back to entities order — still dense, still deterministic.
    const g = deriveGraph([ent('a'), ent('b'), ent('c')], []);

    expect(g.nodes.map((n) => n.encounterIndex)).toEqual([0, 1, 2]);
  });

  it('assigns a dense, unique slot per node — never a gap or a collision', () => {
    // The invariant the row sort depends on: slots are exactly
    // 0…nodes.length-1. A gap is an empty position, a collision is two nodes drawn
    // on top of each other.
    const g = deriveGraph(
      [ent('a'), ent('b'), ent('c'), ent('d'), ent('orphan')],
      [
        ix('i1', 'b', 'c', 1, 6),
        ix('i2', 'c', 'c', 2, 3),
        ix('i3', 'c', 'a', 4, 5),
        ixReqOnly('i4', 'a', null, 7),
        ixReqOnly('i5', 'd', 'b', 8),
      ],
    );

    const slots = g.nodes.map((n) => n.encounterIndex).sort((x, y) => x - y);
    expect(slots).toEqual([0, 1, 2, 3, 4]);
    const slot = (id: string) => g.nodes.find((n) => n.id === id)!.encounterIndex;
    expect(slot('b')).toBe(0);
    expect(slot('c')).toBe(1);
    expect(slot('a')).toBe(2);
    expect(slot('d')).toBe(3);
    expect(slot('orphan')).toBe(4);
  });

  it('assigns no slots at all for empty input', () => {
    expect(deriveGraph([], []).nodes).toEqual([]);
  });

  it('is deterministic: the same input yields the same slots every time', () => {
    // A reader reloading the tab must get the same picture. Nothing in the walk may
    // depend on Map/Set iteration order of anything but insertion.
    const entities = [ent('z'), ent('y'), ent('x'), ent('w')];
    const interactions = [ix('i1', 'y', 'x', 2, 3), ix('i2', 'x', 'z', 1, 4)];

    const first = deriveGraph(entities, interactions).nodes.map((n) => n.encounterIndex);
    for (let i = 0; i < 5; i += 1) {
      expect(deriveGraph(entities, interactions).nodes.map((n) => n.encounterIndex)).toEqual(first);
    }
    // And it is the seq-ordered walk: i2's request (seq 1) is the first leg, so x
    // starts the chain, then z, then i1's request brings in y, and w is isolated.
    expect(first).toEqual([1, 2, 0, 3]);
  });

  // --- The LAYERED LAYOUT (column = call depth, row = chronology within it).
  //
  // Derivation, not rendering: "how deep in the call tree" and "which sibling came
  // first" are facts about the trace, and the px pitch between cells is the
  // component's business. jsdom cannot measure an SVG, so this is where the layout
  // is actually proven.

  const col = (g: ReturnType<typeof deriveGraph>, id: string) =>
    g.nodes.find((n) => n.id === id)!.column;
  const row = (g: ReturnType<typeof deriveGraph>, id: string) =>
    g.nodes.find((n) => n.id === id)!.row;

  it('puts a caller in a LOWER column than the entity it calls', () => {
    // The user's first requirement, and the whole point of a column: "if A calls B,
    // A can be to the left of B". Left is the smaller column.
    const g = deriveGraph([ent('a'), ent('b')], [ix('i1', 'a', 'b', 1, 2)]);

    expect(col(g, 'a')).toBe(0);
    expect(col(g, 'b')).toBe(1);
    expect(col(g, 'a')).toBeLessThan(col(g, 'b'));
  });

  it("THE USER'S EXAMPLE: A calls B then A calls C puts B ABOVE C in one column", () => {
    // Pinned by name because it is the requirement stated verbatim: "if A calls C
    // later, B can be ABOVE C". Both callees are at depth 1 — the SAME column — and
    // they are separated on the row axis by the order the trace touched them.
    //
    // This is precisely what a flat sequence could not express, and why the diagonal
    // staircase this replaced had to go: with every node on one line there is no
    // cross axis left to stack siblings on.
    const g = deriveGraph(
      [ent('A'), ent('B'), ent('C')],
      [ix('early', 'A', 'B', 1, 2), ix('later', 'A', 'C', 3, 4)],
    );

    // Same column: both are one call deep from A.
    expect(col(g, 'B')).toBe(1);
    expect(col(g, 'C')).toBe(1);
    // Different rows, B first — B is ABOVE C.
    expect(row(g, 'B')).toBe(0);
    expect(row(g, 'C')).toBe(1);
    expect(row(g, 'B')).toBeLessThan(row(g, 'C'));
    // And A is to their left, alone in its own column.
    expect(col(g, 'A')).toBe(0);
    expect(row(g, 'A')).toBe(0);
  });

  it('orders siblings by ENCOUNTER, not by the entities read or by id', () => {
    // The distinguishing case for the row axis. The entities read lists C before B
    // and 'B' < 'C' alphabetically, but the trace calls B first — so B must still be
    // the upper row. If the sort were reading the array index or the id, this is the
    // test that separates them.
    const g = deriveGraph(
      [ent('C'), ent('B'), ent('A')],
      [ixReqOnly('early', 'A', 'B', 1), ixReqOnly('later', 'A', 'C', 2)],
    );

    expect(row(g, 'B')).toBe(0);
    expect(row(g, 'C')).toBe(1);
  });

  it('deepens the column on a REQUEST leg only — a response never pushes anything right', () => {
    // The load-bearing exclusion. A response travels callee → caller, backwards
    // along the call direction, so counting it would push the CALLER right of its
    // own callee — and then the callee right of that, forever. Two entities that
    // complete one interaction must sit in exactly two columns, not four.
    const g = deriveGraph([ent('a'), ent('b')], [ix('i1', 'a', 'b', 1, 2)]);

    expect(g.edges.map((e) => e.legType)).toEqual(['request', 'response']);
    expect(col(g, 'a')).toBe(0);
    expect(col(g, 'b')).toBe(1);
  });

  it('does not ratchet columns when the same pair interacts repeatedly', () => {
    // The same exclusion, stated as the property that actually breaks if it is got
    // wrong: three completed round trips between one pair is still two columns, not
    // six. Under a response-counting depth this is where it would visibly explode.
    const g = deriveGraph(
      [ent('a'), ent('b')],
      [ix('i1', 'a', 'b', 1, 2), ix('i2', 'a', 'b', 3, 4), ix('i3', 'a', 'b', 5, 6)],
    );

    expect(col(g, 'a')).toBe(0);
    expect(col(g, 'b')).toBe(1);
  });

  it('takes the LONGEST request path, so no edge skips backwards', () => {
    // A calls B directly AND calls C which calls B. Shortest-path depth would put B
    // at 1, level with C, and the C→B edge would point backwards (or straight down
    // into its own column). Longest path puts B at 2, so BOTH request edges point
    // rightwards — A→B skipping one column, C→B adjacent.
    const g = deriveGraph(
      [ent('A'), ent('B'), ent('C')],
      [ixReqOnly('ac', 'A', 'C', 1), ixReqOnly('cb', 'C', 'B', 2), ixReqOnly('ab', 'A', 'B', 3)],
    );

    expect(col(g, 'A')).toBe(0);
    expect(col(g, 'C')).toBe(1);
    expect(col(g, 'B')).toBe(2);
  });

  it('assigns a deeper column at each step of a call chain', () => {
    const g = deriveGraph(
      [ent('a'), ent('b'), ent('c'), ent('d')],
      [
        ixReqOnly('i1', 'a', 'b', 1),
        ixReqOnly('i2', 'b', 'c', 2),
        ixReqOnly('i3', 'c', 'd', 3),
      ],
    );

    expect([col(g, 'a'), col(g, 'b'), col(g, 'c'), col(g, 'd')]).toEqual([0, 1, 2, 3]);
    // A chain occupies one node per column, so every row is 0 — nothing to stack.
    expect([row(g, 'a'), row(g, 'b'), row(g, 'c'), row(g, 'd')]).toEqual([0, 0, 0, 0]);
  });

  it('TERMINATES on a request cycle instead of spinning forever', () => {
    // Real data: two agents that request each other (A asks B for a plan, B asks A
    // for context). A "relax until nothing changes" pass would push both one column
    // further right on every iteration and never settle; a recursive longest-path
    // walk would recurse forever. The bounded pass count is what makes this a
    // property of the code.
    //
    // A cycle cannot be drawn strictly left-to-right (that is a property of cycles),
    // so the honest claim is not a particular column but that the answer is finite,
    // TIGHTLY bounded and repeatable. The ceiling is `n - 1` — the longest simple
    // path — so a 2-cycle yields two adjacent columns and not a runaway spread.
    const entities = [ent('A'), ent('B')];
    const interactions = [ixReqOnly('ab', 'A', 'B', 1), ixReqOnly('ba', 'B', 'A', 2)];

    const g = deriveGraph(entities, interactions);
    for (const n of g.nodes) {
      expect(Number.isFinite(n.column)).toBe(true);
      expect(n.column).toBeGreaterThanOrEqual(0);
      expect(n.column).toBeLessThanOrEqual(g.nodes.length - 1);
    }
    // Exactly two columns for two nodes: the pair reads left-to-right on its first
    // request and the return request is the one backwards edge.
    expect(new Set(g.nodes.map((n) => n.column)).size).toBe(2);
    // Repeatable — the ceiling does not make the result depend on anything but input.
    expect(deriveGraph(entities, interactions).nodes.map((n) => n.column)).toEqual(
      g.nodes.map((n) => n.column),
    );
  });

  it('does not drift a cycle rightward as unrelated entities are added', () => {
    // The columns are NORMALISED so the leftmost occupied one is 0, and this is what
    // pins it. `assignColumns`' ceiling is `n - 1` over ALL nodes and a cycle relaxes
    // until it hits that ceiling — so before normalisation, adding entities that
    // participate in no edge whatsoever *moved the cycle*: `A⇄B` alone sat in columns
    // 0 and 1, but `A⇄B` plus eight isolated entities sat in 8 and 9, leaving columns
    // 0-7 empty and the renderer drawing a growing blank left gutter.
    //
    // It also vacated `GraphNodeSpec.column`'s documented contract that `0` means "an
    // entity nothing ever calls". On a wholly cyclic graph NO node held column 0.
    //
    // The sibling cycle tests above pass either way — they assert `column <= n - 1`
    // and a distinct-column count, both true before and after — so `min === 0` is the
    // assertion that actually catches this.
    const cycle = [ixReqOnly('ab', 'A', 'B', 1), ixReqOnly('ba', 'B', 'A', 2)];
    // Explicit lambda, not `.map(ent)`: `ent`'s second parameter is a `Partial<Entity>`
    // override, and a bare reference would hand it the array index.
    const bystanders = ['b1', 'b2', 'b3', 'b4', 'b5', 'b6', 'b7', 'b8'].map((id) => ent(id));

    const bare = deriveGraph([ent('A'), ent('B')], cycle);
    const padded = deriveGraph([ent('A'), ent('B'), ...bystanders], cycle);

    // Someone occupies column 0 in both, regardless of how many bystanders exist.
    expect(Math.min(...bare.nodes.map((n) => n.column))).toBe(0);
    expect(Math.min(...padded.nodes.map((n) => n.column))).toBe(0);
    // And the cycle itself sits in the same two columns either way — the bystanders
    // are parked in a trailing column, they do not push the cycle.
    const colOf = (g: ReturnType<typeof deriveGraph>, id: string) =>
      g.nodes.find((n) => n.id === id)!.column;
    expect(colOf(padded, 'A')).toBe(colOf(bare, 'A'));
    expect(colOf(padded, 'B')).toBe(colOf(bare, 'B'));
  });

  it('does not park a response-only interaction as unconnected', () => {
    // An interaction carrying ONLY a response leg still draws an arrow, so neither of
    // its participants is isolated. The isolated test used to be built from REQUEST
    // edges alone, on the reasoning that a response's source must be some request's
    // callee — which ASSERTS an invariant nothing enforces. `flatLegRows` imposes no
    // such rule, so a response-only interaction put both real participants in the
    // trailing "unconnected" column, dashed as present-but-unconnected, with a drawn
    // edge running between them.
    const g = deriveGraph([ent('A'), ent('B')], [ixRespOnly('ar', 'A', 'B', 1)]);

    expect(g.edges).toHaveLength(1);
    expect(g.nodes.find((n) => n.id === 'A')!.isIsolated).toBe(false);
    expect(g.nodes.find((n) => n.id === 'B')!.isIsolated).toBe(false);
  });

  it('terminates on a THREE-node request cycle, still within n-1 columns', () => {
    // The longer cycle, where a bound derived from the PASS count rather than from
    // the column would let the nodes spread far past the node count. `n - 1` is the
    // tightest bound that cannot distort an acyclic answer.
    const g = deriveGraph(
      [ent('a'), ent('b'), ent('c')],
      [ixReqOnly('i1', 'a', 'b', 1), ixReqOnly('i2', 'b', 'c', 2), ixReqOnly('i3', 'c', 'a', 3)],
    );

    expect(g.nodes.every((n) => Number.isFinite(n.column))).toBe(true);
    expect(g.nodes.every((n) => n.column <= g.nodes.length - 1)).toBe(true);
    // The chain a→b→c still reads left-to-right; c→a is the single backwards edge.
    expect([col(g, 'a'), col(g, 'b'), col(g, 'c')]).toEqual([0, 1, 2]);
  });

  it('can leave two request-connected nodes in ONE column, but only under the ceiling', () => {
    // The residue a cycle leaves, and worth pinning because it is the ONLY way a
    // request edge ends up within a column: the longest-path rule otherwise puts every
    // target strictly right of its source. Here a→c, b→a, c→b relaxes against a ceiling
    // of 2 and leaves b and c sharing a column, so c→b runs inside it.
    //
    // The renderer needs this case to exist (it is what `edgeBendpoints`' same-column
    // branch routes), so the layout must be honest that it can produce it rather than
    // claiming every edge crosses a column boundary.
    //
    // Expected columns are `0, 1, 1` and not the `1, 2, 2` this asserted before the
    // normalisation pass: the relaxation still produces 1/2/2, but columns are now
    // shifted so the leftmost occupied one is 0. Only the RELATIVE column is
    // load-bearing (the renderer multiplies it by a step; edge routing reads the
    // difference), and the shift is what stops a cycle drifting rightward with the count
    // of unrelated entities — see the drift test above. The claim under test is
    // unchanged: two request-connected nodes share one column.
    const g = deriveGraph(
      [ent('a'), ent('b'), ent('c')],
      [ixReqOnly('i1', 'a', 'c', 1), ixReqOnly('i2', 'b', 'a', 2), ixReqOnly('i3', 'c', 'b', 3)],
    );

    expect([col(g, 'a'), col(g, 'b'), col(g, 'c')]).toEqual([0, 1, 1]);
    // The point of the fixture, stated independently of the absolute offset.
    expect(col(g, 'b')).toBe(col(g, 'c'));
    expect(col(g, 'a')).toBeLessThan(col(g, 'b'));
    // Same column, different rows — so they are still two distinct cells and the
    // within-column edge has somewhere to bow to.
    expect(row(g, 'b')).not.toBe(row(g, 'c'));
  });

  it('leaves a SELF-call in its own column rather than marching it rightwards', () => {
    // A self-request cannot be to the right of itself — that constraint is
    // unsatisfiable — so it is skipped and the loop is drawn on the node instead.
    // Without the skip, the capped relaxation would walk this one node `n` columns
    // right for no reason at all.
    const g = deriveGraph(
      [ent('solo'), ent('next')],
      [ix('loop', 'solo', 'solo', 1, 2), ixReqOnly('out', 'solo', 'next', 3)],
    );

    expect(col(g, 'solo')).toBe(0);
    expect(col(g, 'next')).toBe(1);
    expect(g.edges.filter((e) => e.isSelfCall)).toHaveLength(2);
  });

  it('parks an isolated entity in a TRAILING column, off the end of the chain', () => {
    // Column 0 is a CLAIM — "nothing calls this, the flow starts here" — and an
    // entity no leg touches has not earned it. It stays VISIBLE (an entity is a
    // governance fact, and the view discloses these in an alert) but sits past the
    // deepest real column, matching its dashed "present but unconnected" treatment.
    const g = deriveGraph(
      [ent('lonely'), ent('a'), ent('b')],
      [ixReqOnly('i1', 'a', 'b', 1)],
    );

    expect(col(g, 'a')).toBe(0);
    expect(col(g, 'b')).toBe(1);
    expect(col(g, 'lonely')).toBe(2); // one past the deepest reached
    // Not sharing the entry column with `a`, which is the specific confusion the
    // trailing column exists to prevent.
    expect(col(g, 'lonely')).not.toBe(col(g, 'a'));
  });

  it('stacks several isolated entities in the trailing column, in entities order', () => {
    // Their rows have to be deterministic too. No leg encountered them, so their
    // `encounterIndex` falls back to `entities` order — the API's order, stable for
    // a trace — and the row sort follows it.
    const g = deriveGraph(
      [ent('l1'), ent('a'), ent('l2'), ent('b'), ent('l3')],
      [ixReqOnly('i1', 'a', 'b', 1)],
    );

    expect([col(g, 'l1'), col(g, 'l2'), col(g, 'l3')]).toEqual([2, 2, 2]);
    expect([row(g, 'l1'), row(g, 'l2'), row(g, 'l3')]).toEqual([0, 1, 2]);
  });

  it('parks an entity named only by a DROPPED interaction in the trailing column too', () => {
    // Not isolated — it demonstrably participated — but no drawable request leg
    // reaches it, so there is no depth to place it at. The two notions stay separate
    // here exactly as they do for `encounterIndex`.
    const g = deriveGraph(
      [ent('halfway'), ent('a'), ent('b')],
      [ixReqOnly('i1', 'a', 'b', 1), ixReqOnly('bad', 'halfway', null, 2)],
    );

    expect(g.nodes.find((n) => n.id === 'halfway')!.isIsolated).toBe(false);
    expect(col(g, 'halfway')).toBe(2);
  });

  it('puts every entity in column 0 when NO interaction is drawable at all', () => {
    // Nothing is reached, so the trailing column is 0 rather than 1: the unreached
    // set is the whole picture, and pushing it off into empty space beside nothing
    // would leave the graph starting one column in from the margin for no reason.
    const g = deriveGraph([ent('a'), ent('b'), ent('c')], []);

    expect(g.nodes.map((n) => n.column)).toEqual([0, 0, 0]);
    // …stacked down the single column in entities order, so they are all distinct
    // cells and nothing is drawn on top of anything.
    expect(g.nodes.map((n) => n.row)).toEqual([0, 1, 2]);
  });

  it('gives no cells at all for empty input', () => {
    expect(deriveGraph([], []).nodes).toEqual([]);
  });

  it('never puts two nodes in the SAME cell', () => {
    // The invariant the placement depends on: two nodes at one (column, row) are two
    // nodes drawn exactly on top of each other. Rows are dense per column, so this
    // is really "the row sort is a total order within each column".
    const g = deriveGraph(
      [ent('a'), ent('b'), ent('c'), ent('d'), ent('e'), ent('orphan')],
      [
        ixReqOnly('i1', 'a', 'b', 1),
        ixReqOnly('i2', 'a', 'c', 2),
        ixReqOnly('i3', 'a', 'd', 3),
        ixReqOnly('i4', 'b', 'e', 4),
        ix('i5', 'c', 'c', 5, 6),
      ],
    );

    const cells = g.nodes.map((n) => `${n.column},${n.row}`);
    expect(new Set(cells).size).toBe(cells.length);
    // …and the three siblings really did stack rather than pile up: a's three
    // callees share column 1 at rows 0, 1, 2 in call order.
    expect([col(g, 'b'), col(g, 'c'), col(g, 'd')]).toEqual([1, 1, 1]);
    expect([row(g, 'b'), row(g, 'c'), row(g, 'd')]).toEqual([0, 1, 2]);
  });

  it('is deterministic: the same input yields the same cells every time', () => {
    // A reader reloading the tab must get the identical picture. Nothing in the
    // column relaxation or the row sort may depend on Map/Set iteration order of
    // anything but insertion.
    const entities = [ent('z'), ent('y'), ent('x'), ent('w')];
    const interactions = [ix('i1', 'y', 'x', 2, 3), ix('i2', 'x', 'z', 1, 4)];

    const first = deriveGraph(entities, interactions).nodes.map((n) => [n.column, n.row]);
    for (let i = 0; i < 5; i += 1) {
      expect(deriveGraph(entities, interactions).nodes.map((n) => [n.column, n.row])).toEqual(
        first,
      );
    }
    // Spelled out, so a change to the assignment is a change this test notices
    // rather than one it silently re-baselines: i2's request (seq 1) puts x at 0 and
    // z at 1, i1's request (seq 2) puts y left of x — so y is 0, x is 1, z is 2 —
    // and w, unreached, trails at 3. `nodes` is in entities order (z, y, x, w).
    expect(first).toEqual([
      [2, 0], // z
      [0, 0], // y
      [1, 0], // x
      [3, 0], // w — trailing column
    ]);
  });

  // --- EDGE CASE: parallel edges, recounted for the leg model.

  it('does NOT report a normal request/response pair as a parallel channel', () => {
    // The regression this guard exists for: under the leg model a single completed
    // interaction ALWAYS puts two arrows in the same channel, so an edge-counting
    // rule would fire the notice on virtually every trace — noise that is always
    // on carries no information.
    const g = deriveGraph([ent('e1'), ent('e2')], [ix('i1', 'e1', 'e2', 1, 2)]);

    expect(g.edges).toHaveLength(2);
    expect(g.parallelGroups).toEqual([]);
  });

  it('reports a pair as parallel when TWO interactions run between the same entities', () => {
    // The genuinely notable thing the notice was always about (an agent calling
    // the same tool twice). All four edges are listed so the renderer can fan the
    // whole channel out.
    const g = deriveGraph(
      [ent('e1'), ent('e2')],
      [ix('i1', 'e1', 'e2', 1, 2), ix('i2', 'e1', 'e2', 3, 4)],
    );

    expect(g.edges).toHaveLength(4);
    expect(g.parallelGroups).toHaveLength(1);
    expect(g.parallelGroups[0].edgeIds).toEqual([
      'i1:request',
      'i1:response',
      'i2:request',
      'i2:response',
    ]);
  });

  it('keeps each of several interactions between one pair as its own edges', () => {
    // NOT collapsed into a single arrow with a count: each leg of each interaction
    // is its own governance fact, so the arrow count must match the leg count.
    const g = deriveGraph(
      [ent('e1'), ent('e2')],
      [ix('i1', 'e1', 'e2', 1, 2), ix('i2', 'e1', 'e2', 3, 4), ix('i3', 'e1', 'e2', 5, 6)],
    );

    expect(g.edges).toHaveLength(6);
    expect(g.edges.map((e) => e.interactionId)).toEqual(['i1', 'i1', 'i2', 'i2', 'i3', 'i3']);
  });

  it('treats A→B and B→A as the SAME channel for parallel grouping', () => {
    // They occupy the same visual gap between the two nodes, so they overlap just
    // as badly as two A→B edges would. Two DISTINCT interactions here (one each
    // way), so this is a real parallel channel — unlike one interaction's own
    // two legs, which also point opposite ways but are the expected shape.
    const g = deriveGraph(
      [ent('e1'), ent('e2')],
      [ix('there', 'e1', 'e2', 1, 2), ix('back', 'e2', 'e1', 3, 4)],
    );

    expect(g.edges).toHaveLength(4);
    expect(g.parallelGroups).toHaveLength(1);
    expect(g.parallelGroups[0].edgeIds.length).toBe(4);
  });

  it('reports no parallel group for a pair carrying a single one-leg interaction', () => {
    const g = deriveGraph([ent('e1'), ent('e2')], [ixReqOnly('i1', 'e1', 'e2', 1)]);

    expect(g.parallelGroups).toEqual([]);
  });

  // --- EDGE CASE: a self-call (caller === callee).

  it('keeps BOTH legs of a self-call as self-edges and flags each', () => {
    // Swapping caller and callee when they are the same entity is a no-op, so the
    // response leg is a self-edge too and needs the same (dashed) treatment — a
    // straight source-equals-target line is invisible.
    const g = deriveGraph([ent('e1')], [ix('i1', 'e1', 'e1', 1, 2)]);

    expect(g.edges).toHaveLength(2);
    expect(g.edges[0]).toMatchObject({
      id: 'i1:request',
      source: 'e1',
      target: 'e1',
      isSelfCall: true,
      label: '1',
    });
    expect(g.edges[1]).toMatchObject({
      id: 'i1:response',
      source: 'e1',
      target: 'e1',
      isSelfCall: true,
      label: '2',
    });
    expect(g.dropped).toHaveLength(0);
  });

  it('does not flag a normal interaction\'s legs as self-calls', () => {
    const g = deriveGraph([ent('e1'), ent('e2')], [ix('i1', 'e1', 'e2', 1, 2)]);

    expect(g.edges.every((e) => e.isSelfCall === false)).toBe(true);
  });

  it('does not call an entity isolated because its only interaction is a self-call', () => {
    const g = deriveGraph([ent('e1')], [ix('i1', 'e1', 'e1', 1, 2)]);

    expect(g.nodes[0].isIsolated).toBe(false);
  });

  it('does not report a lone self-call as a parallel channel either', () => {
    // Its two legs share the (e1, e1) channel, but they are still one
    // interaction's expected shape.
    const g = deriveGraph([ent('e1')], [ix('i1', 'e1', 'e1', 1, 2)]);

    expect(g.parallelGroups).toEqual([]);
  });

  // --- Empty / degenerate inputs.

  it('returns an empty graph for no entities and no interactions', () => {
    const g = deriveGraph([], []);

    expect(g).toEqual({ nodes: [], edges: [], dropped: [], parallelGroups: [] });
  });

  it('reports interactions but no nodes when entities is empty', () => {
    // Nothing is drawable, and the reason is disclosed rather than swallowed.
    const g = deriveGraph([], [ix('i1', 'e1', 'e2', 1, 2)]);

    expect(g.nodes).toEqual([]);
    expect(g.edges).toEqual([]);
    expect(g.dropped).toHaveLength(1);
    expect(g.dropped[0].missing).toBe('both');
  });

  it('is a pure function of its inputs — it mutates neither argument', () => {
    // Notably including the leg arrays: the seq sort must not reorder the caller's
    // own `legs` in place.
    const entities = [ent('e1'), ent('e2')];
    const interactions = [ix('i1', 'e1', 'e2', 2, 1)];
    const entitiesCopy = structuredClone(entities);
    const interactionsCopy = structuredClone(interactions);

    deriveGraph(entities, interactions);

    expect(entities).toEqual(entitiesCopy);
    expect(interactions).toEqual(interactionsCopy);
  });

  it('handles a realistic mixed trace: every edge case at once', () => {
    // A caller→callee chain + a parallel repeat + a self-call + an in-flight call
    // + an unresolved end + an isolated entity, all in one derivation.
    const g = deriveGraph(
      [
        ent('user', { kind: 'user' }),
        ent('agent', { kind: 'agent' }),
        ent('tool', { kind: 'tool' }),
        ent('orphan', { kind: 'external_service' }),
      ],
      [
        ix('a', 'user', 'agent', 1, 10),
        ix('b', 'agent', 'tool', 2, 3),
        // Parallel with b, and its RESPONSE leg failed (the request did not).
        ix('c', 'agent', 'tool', 4, 5, {
          legs: [leg('request', 4, { error: false }), leg('response', 5, { error: true })],
          any_error: true,
        }),
        ix('d', 'agent', 'agent', 6, 7), // self-call
        ixReqOnly('e', 'agent', 'tool', 8), // response still in flight
        ix('f', 'tool', null, 9, 11), // unresolved callee
      ],
    );

    expect(g.nodes).toHaveLength(4);
    // 2 + 2 + 2 + 2 + 1 = 9 edges; f contributes none. Ordered by seq throughout.
    expect(g.edges.map((x) => x.seq)).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 10]);
    expect(g.edges.map((x) => x.id)).toEqual([
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
    // Reported once, though two of its legs were lost.
    expect(g.dropped.map((x) => x.id)).toEqual(['f']);
    expect(g.dropped[0].legCount).toBe(2);
    expect(g.nodes.filter((n) => n.isIsolated).map((n) => n.id)).toEqual(['orphan']);
    // First-encounter order: a's request (seq 1) starts at user then agent, b's
    // (seq 2) brings in tool, and orphan — never encountered — trails.
    expect(g.nodes.map((n) => [n.id, n.encounterIndex])).toEqual([
      ['user', 0],
      ['agent', 1],
      ['tool', 2],
      ['orphan', 3],
    ]);
    // Only the failed LEG is red, not its sibling.
    expect(g.edges.find((x) => x.id === 'c:response')!.isError).toBe(true);
    expect(g.edges.find((x) => x.id === 'c:request')!.isError).toBe(false);
    // Both legs of the self-call.
    expect(g.edges.filter((x) => x.isSelfCall).map((x) => x.id)).toEqual([
      'd:request',
      'd:response',
    ]);
    // agent↔tool carries three distinct interactions (b, c, e) → one parallel
    // channel. user↔agent carries only a's own two legs, so it is NOT reported.
    expect(g.parallelGroups).toHaveLength(1);
    expect(g.parallelGroups[0].edgeIds).toEqual([
      'b:request',
      'b:response',
      'c:request',
      'c:response',
      'e:request',
    ]);
  });
});
