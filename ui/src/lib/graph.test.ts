import { describe, it, expect } from 'vitest';
import { deriveGraph } from './graph';
import type { Entity, Interaction } from './flow';

/**
 * The Execution Flow graph's node/edge derivation. This is where the graph view's
 * real coverage lives: jsdom cannot lay out or measure an SVG, so the SHAPE of the
 * graph is proven here as pure logic and the render test is kept to what jsdom can
 * honestly assert (see ExecutionFlowGraph.test.tsx).
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

function ix(id: string, caller: string | null, callee: string | null, over: Partial<Interaction> = {}): Interaction {
  return {
    id,
    caller_entity_id: caller,
    callee_entity_id: callee,
    summary: `summary-${id}`,
    parent_interaction_id: null,
    legs: [],
    duration_seconds: 1,
    any_error: false,
    span_count: 1,
    anchor_count: 1,
    ...over,
  };
}

describe('deriveGraph', () => {
  it('maps every entity to a node labelled with its display_name', () => {
    const g = deriveGraph([ent('e1'), ent('e2')], [ix('i1', 'e1', 'e2')]);

    expect(g.nodes).toHaveLength(2);
    expect(g.nodes.map((n) => n.id)).toEqual(['e1', 'e2']);
    expect(g.nodes.map((n) => n.label)).toEqual(['name-e1', 'name-e2']);
  });

  it('carries each entity kind and natural key onto its node (the colour/tooltip source)', () => {
    const g = deriveGraph(
      [ent('e1', { kind: 'tool', natural_key: 'tool:(p,svc)' })],
      [],
    );

    expect(g.nodes[0].kind).toBe('tool');
    expect(g.nodes[0].naturalKey).toBe('tool:(p,svc)');
  });

  it('falls back to the entity id when display_name is blank', () => {
    // A node with no visible text is unusable; the id is the only other field
    // guaranteed to be present.
    const g = deriveGraph([ent('e1', { display_name: '' })], []);

    expect(g.nodes[0].label).toBe('e1');
  });

  it('maps an interaction to a directed edge, caller → callee', () => {
    const g = deriveGraph([ent('e1'), ent('e2')], [ix('i1', 'e1', 'e2')]);

    expect(g.edges).toHaveLength(1);
    expect(g.edges[0]).toMatchObject({
      id: 'i1',
      source: 'e1',
      target: 'e2',
      label: 'summary-i1',
    });
  });

  it('falls back to the interaction id when summary is null', () => {
    const g = deriveGraph([ent('e1'), ent('e2')], [ix('i1', 'e1', 'e2', { summary: null })]);

    expect(g.edges[0].label).toBe('i1');
  });

  // --- any_error → the error-coloured edge.

  it('flags an edge as an error only when any_error is exactly true', () => {
    const g = deriveGraph(
      [ent('e1'), ent('e2')],
      [
        ix('ok', 'e1', 'e2', { any_error: false }),
        ix('bad', 'e1', 'e2', { any_error: true }),
        // A null any_error is "not yet aggregated", NOT "failed" — the same
        // never-render-unknown-as-a-verdict rule the lineage status follows.
        ix('unknown', 'e1', 'e2', { any_error: null }),
      ],
    );

    expect(g.edges.find((e) => e.id === 'ok')!.isError).toBe(false);
    expect(g.edges.find((e) => e.id === 'bad')!.isError).toBe(true);
    expect(g.edges.find((e) => e.id === 'unknown')!.isError).toBe(false);
  });

  // --- EDGE CASE: a null caller/callee (unresolved participant).

  it('does not drop an interaction with a null callee silently — it is reported', () => {
    const g = deriveGraph([ent('e1')], [ix('i1', 'e1', null)]);

    expect(g.edges).toHaveLength(0);
    expect(g.dropped).toHaveLength(1);
    expect(g.dropped[0]).toMatchObject({
      id: 'i1',
      label: 'summary-i1',
      missing: 'callee',
      resolvedEntityId: 'e1',
    });
  });

  it('reports a null caller as missing `caller`, keeping the resolved callee', () => {
    const g = deriveGraph([ent('e2')], [ix('i1', null, 'e2')]);

    expect(g.edges).toHaveLength(0);
    expect(g.dropped[0]).toMatchObject({ missing: 'caller', resolvedEntityId: 'e2' });
  });

  it('reports an interaction with BOTH ends unresolved as missing `both`', () => {
    const g = deriveGraph([ent('e1')], [ix('i1', null, null)]);

    expect(g.dropped[0]).toMatchObject({ missing: 'both', resolvedEntityId: null });
  });

  it('drops (and reports) an interaction naming an entity the entities read does not carry', () => {
    // A dangling endpoint would make the topology model invalid; it is an
    // inconsistency worth surfacing rather than crashing on.
    const g = deriveGraph([ent('e1')], [ix('i1', 'e1', 'ghost')]);

    expect(g.edges).toHaveLength(0);
    expect(g.dropped[0]).toMatchObject({ missing: 'callee', resolvedEntityId: 'e1' });
  });

  it('keeps the drawable interactions when only SOME are unresolved', () => {
    const g = deriveGraph(
      [ent('e1'), ent('e2')],
      [ix('good', 'e1', 'e2'), ix('bad', 'e1', null)],
    );

    expect(g.edges.map((e) => e.id)).toEqual(['good']);
    expect(g.dropped.map((d) => d.id)).toEqual(['bad']);
  });

  // --- EDGE CASE: an entity no interaction names (isolated node).

  it('keeps an entity with no interactions as an isolated node rather than dropping it', () => {
    const g = deriveGraph([ent('e1'), ent('e2'), ent('lonely')], [ix('i1', 'e1', 'e2')]);

    expect(g.nodes.map((n) => n.id)).toContain('lonely');
    expect(g.nodes.find((n) => n.id === 'lonely')!.isIsolated).toBe(true);
    expect(g.nodes.find((n) => n.id === 'e1')!.isIsolated).toBe(false);
    expect(g.nodes.find((n) => n.id === 'e2')!.isIsolated).toBe(false);
  });

  it('does not call an entity isolated when its only interaction was dropped for the OTHER end', () => {
    // The interaction could not be drawn, but it still proves e1 participated —
    // so e1 is genuinely not isolated, and saying otherwise would misreport it.
    const g = deriveGraph([ent('e1')], [ix('i1', 'e1', null)]);

    expect(g.nodes[0].isIsolated).toBe(false);
    expect(g.edges).toHaveLength(0);
  });

  it('marks every node isolated when there are no interactions at all', () => {
    const g = deriveGraph([ent('e1'), ent('e2')], []);

    expect(g.nodes.every((n) => n.isIsolated)).toBe(true);
    expect(g.edges).toHaveLength(0);
  });

  // --- EDGE CASE: multiple interactions between the same pair (parallel edges).

  it('keeps each of several interactions between one pair as its own edge', () => {
    const g = deriveGraph(
      [ent('e1'), ent('e2')],
      [ix('i1', 'e1', 'e2'), ix('i2', 'e1', 'e2'), ix('i3', 'e1', 'e2')],
    );

    // NOT collapsed into a single arrow with a count: each interaction is its own
    // governance fact, so the arrow count must match the interaction count.
    expect(g.edges).toHaveLength(3);
    expect(g.edges.map((e) => e.id)).toEqual(['i1', 'i2', 'i3']);
  });

  it('groups parallel edges by their unordered endpoint pair', () => {
    const g = deriveGraph(
      [ent('e1'), ent('e2')],
      [ix('i1', 'e1', 'e2'), ix('i2', 'e1', 'e2')],
    );

    expect(g.parallelGroups).toHaveLength(1);
    expect(g.parallelGroups[0].edgeIds).toEqual(['i1', 'i2']);
  });

  it('treats A→B and B→A as the SAME channel for parallel grouping', () => {
    // They occupy the same visual gap between the two nodes, so they overlap just
    // as badly as two A→B edges would.
    const g = deriveGraph(
      [ent('e1'), ent('e2')],
      [ix('there', 'e1', 'e2'), ix('back', 'e2', 'e1')],
    );

    expect(g.edges).toHaveLength(2);
    expect(g.parallelGroups).toHaveLength(1);
    expect(g.parallelGroups[0].edgeIds.sort()).toEqual(['back', 'there']);
  });

  it('reports no parallel group for a pair with a single edge', () => {
    const g = deriveGraph([ent('e1'), ent('e2')], [ix('i1', 'e1', 'e2')]);

    expect(g.parallelGroups).toEqual([]);
  });

  // --- EDGE CASE: a self-call (caller === callee).

  it('keeps a self-call as a real edge and flags it', () => {
    const g = deriveGraph([ent('e1')], [ix('i1', 'e1', 'e1')]);

    expect(g.edges).toHaveLength(1);
    expect(g.edges[0]).toMatchObject({ source: 'e1', target: 'e1', isSelfCall: true });
    expect(g.dropped).toHaveLength(0);
  });

  it('does not flag a normal edge as a self-call', () => {
    const g = deriveGraph([ent('e1'), ent('e2')], [ix('i1', 'e1', 'e2')]);

    expect(g.edges[0].isSelfCall).toBe(false);
  });

  it('does not call an entity isolated because its only interaction is a self-call', () => {
    const g = deriveGraph([ent('e1')], [ix('i1', 'e1', 'e1')]);

    expect(g.nodes[0].isIsolated).toBe(false);
  });

  // --- Empty / degenerate inputs.

  it('returns an empty graph for no entities and no interactions', () => {
    const g = deriveGraph([], []);

    expect(g).toEqual({ nodes: [], edges: [], dropped: [], parallelGroups: [] });
  });

  it('reports interactions but no nodes when entities is empty', () => {
    // Nothing is drawable, and the reason is disclosed rather than swallowed.
    const g = deriveGraph([], [ix('i1', 'e1', 'e2')]);

    expect(g.nodes).toEqual([]);
    expect(g.edges).toEqual([]);
    expect(g.dropped).toHaveLength(1);
    expect(g.dropped[0].missing).toBe('both');
  });

  it('is a pure function of its inputs — it mutates neither argument', () => {
    const entities = [ent('e1'), ent('e2')];
    const interactions = [ix('i1', 'e1', 'e2')];
    const entitiesCopy = structuredClone(entities);
    const interactionsCopy = structuredClone(interactions);

    deriveGraph(entities, interactions);

    expect(entities).toEqual(entitiesCopy);
    expect(interactions).toEqual(interactionsCopy);
  });

  it('handles a realistic mixed trace: every edge case at once', () => {
    // caller→callee chain + a parallel repeat + a self-call + an unresolved end
    // + an isolated entity, all in one derivation.
    const g = deriveGraph(
      [
        ent('user', { kind: 'user' }),
        ent('agent', { kind: 'agent' }),
        ent('tool', { kind: 'tool' }),
        ent('orphan', { kind: 'external_service' }),
      ],
      [
        ix('a', 'user', 'agent'),
        ix('b', 'agent', 'tool'),
        ix('c', 'agent', 'tool', { any_error: true }), // parallel with b, and failed
        ix('d', 'agent', 'agent'), // self-call
        ix('e', 'tool', null), // unresolved callee
      ],
    );

    expect(g.nodes).toHaveLength(4);
    expect(g.edges.map((x) => x.id)).toEqual(['a', 'b', 'c', 'd']);
    expect(g.dropped.map((x) => x.id)).toEqual(['e']);
    expect(g.nodes.filter((n) => n.isIsolated).map((n) => n.id)).toEqual(['orphan']);
    expect(g.edges.find((x) => x.id === 'c')!.isError).toBe(true);
    expect(g.edges.find((x) => x.id === 'd')!.isSelfCall).toBe(true);
    expect(g.parallelGroups).toHaveLength(1);
    expect(g.parallelGroups[0].edgeIds).toEqual(['b', 'c']);
  });
});
