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
