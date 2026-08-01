import { describe, it, expect } from 'vitest';
import { deriveSourceHighlight, deriveReachabilityHighlight } from './lineageReachability';
import { deriveGraph } from './graph';
import type { Entity, Interaction, InteractionLeg } from './flow';
import type { LineageReachability, LineageSummary } from '../types';

/**
 * The Lineage tab's highlight derivation from the SERVED reachability reads
 * (ADR-0028 D14/D15). This is where that tab's real coverage lives, for the reason
 * `graph.test.ts` states for its own: jsdom cannot lay out or measure an SVG, so the
 * SHAPE of the answer is proven here as pure logic and the render test is kept to
 * what jsdom can honestly assert (a class, a prop, a word on screen — never a pixel
 * of dimming).
 *
 * Fixtures are the same shape as `graph.test.ts`'s (`ent`, `leg`, `ix`) because this
 * module composes with `deriveGraph` rather than reimplementing it: the graph a test
 * derives is the graph the highlight is computed against, so a divergent fixture set
 * would be two different graphs and the composition would go untested.
 *
 * The response fixtures mirror the REAL wire shape, verified against a live trace —
 * including the fact that fan-in and fan-out of a leaf tool both return a full
 * entity set on a trace derived under the trivial matcher.
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

/** `a -> b -> c`, the standard three-entity chain with both legs per interaction. */
const ENTITIES = [ent('a'), ent('b'), ent('c')];
const INTERACTIONS = [ix('ix1', 'a', 'b', 1, 2), ix('ix2', 'b', 'c', 3, 4)];
const GRAPH = deriveGraph(ENTITIES, INTERACTIONS);

function summary(over: Partial<LineageSummary> = {}): LineageSummary {
  return {
    sources: [],
    destinations: [],
    status: 'complete',
    stoppedAtSeq: null,
    ...over,
  };
}

function reach(
  direction: 'fanin' | 'fanout',
  over: Partial<LineageReachability> = {},
): LineageReachability {
  return {
    direction,
    seed_entity_id: 'b',
    entities: [],
    legs: [],
    state: 'no-adjacent',
    pending_frontier: [],
    truncated: false,
    status: 'complete',
    stopped_at_seq: null,
    ...over,
  };
}

/** A reached-entity view. `hops` is a DISTANCE, never an ordering (D10). */
function reached(id: string, hops: number) {
  return {
    id,
    natural_key: `agent:(p,${id})`,
    kind: 'agent',
    display_name: `name-${id}`,
    hops,
  };
}

/** A traversed leg, in the response's own field names. */
function traversed(
  interactionId: string,
  legType: 'request' | 'response',
  from: string,
  to: string,
  seq: number,
) {
  return {
    interaction_id: interactionId,
    leg_type: legType,
    from_entity_id: from,
    to_entity_id: to,
    seq,
  };
}

const IDLE = { isError: false, isLoading: false };

// ---------------------------------------------------------------------------
// deriveSourceHighlight — the always-on trace source colouring
// ---------------------------------------------------------------------------

describe('deriveSourceHighlight', () => {
  it('resolves the summary source natural keys to node ids, with no selection involved', () => {
    // The whole point of the always-on half: nothing is selected and there are
    // still lit nodes, because "these are the trace's data sources" is a standing
    // fact rather than an answer to a click.
    const h = deriveSourceHighlight({
      entities: ENTITIES,
      summary: summary({ sources: ['agent:(p,a)', 'agent:(p,c)'] }),
      graph: GRAPH,
    });
    expect(h.sourceNodeIds).toEqual(['a', 'c']);
    expect(h.unresolved).toEqual([]);
    expect(h.totalSources).toBe(2);
  });

  it('discloses a source whose natural key matches no entity in the trace', () => {
    // A real origin the graph cannot draw (an upstream service the trace never
    // spanned). It must be reported, not dropped, or the lit nodes silently claim
    // to be the full source set.
    const h = deriveSourceHighlight({
      entities: ENTITIES,
      summary: summary({ sources: ['agent:(p,a)', 'svc:external-crm'] }),
      graph: GRAPH,
    });
    expect(h.sourceNodeIds).toEqual(['a']);
    expect(h.unresolved).toEqual([{ ref: 'svc:external-crm' }]);
    // The count is over what the SUMMARY named, so the view can say "1 of 2 shown".
    expect(h.totalSources).toBe(2);
  });

  it('discloses a source whose entity exists but has no drawn node', () => {
    // Resolvable to an entity, but that entity is not in the graph at all — so
    // there is still nothing to light, and pointing the reader at it would point
    // them at nothing.
    const h = deriveSourceHighlight({
      entities: [...ENTITIES, ent('ghost')],
      summary: summary({ sources: ['agent:(p,ghost)'] }),
      graph: GRAPH,
    });
    expect(h.sourceNodeIds).toEqual([]);
    expect(h.unresolved).toEqual([{ ref: 'agent:(p,ghost)' }]);
  });

  it('reports an ambiguous natural key that produced a lit node', () => {
    const dupes = [ent('a'), ent('b'), ent('c'), ent('dup', { natural_key: 'agent:(p,a)' })];
    const h = deriveSourceHighlight({
      entities: dupes,
      summary: summary({ sources: ['agent:(p,a)'] }),
      graph: deriveGraph(dupes, INTERACTIONS),
    });
    // First wins (entityIdsByKey's documented behaviour) and the ambiguity is
    // surfaced so the view can say the pick was arbitrary-but-deterministic.
    expect(h.sourceNodeIds).toEqual(['a']);
    expect(h.ambiguousKeys).toEqual(['agent:(p,a)']);
  });

  it('does not report an ambiguous key that contributed nothing to this answer', () => {
    const dupes = [ent('a'), ent('b'), ent('c'), ent('dup', { natural_key: 'agent:(p,c)' })];
    const h = deriveSourceHighlight({
      entities: dupes,
      summary: summary({ sources: ['agent:(p,a)'] }),
      graph: deriveGraph(dupes, INTERACTIONS),
    });
    // `agent:(p,c)` is ambiguous but is not a source, so it is not a caveat here.
    expect(h.ambiguousKeys).toEqual([]);
  });

  it('an ABSENT summary is an empty highlight and not a claim of "no sources"', () => {
    // In flight, or the read failed. The function cannot tell those apart and does
    // not try — the view distinguishes them from the query state.
    const h = deriveSourceHighlight({ entities: ENTITIES, summary: undefined, graph: GRAPH });
    expect(h.sourceNodeIds).toEqual([]);
    expect(h.totalSources).toBe(0);
  });

  it('an EMPTY sources list yields no lit nodes', () => {
    const h = deriveSourceHighlight({
      entities: ENTITIES,
      summary: summary({ sources: [] }),
      graph: GRAPH,
    });
    expect(h.sourceNodeIds).toEqual([]);
    expect(h.totalSources).toBe(0);
  });

  it('is deterministic: sorted node ids regardless of the summary order', () => {
    const forward = deriveSourceHighlight({
      entities: ENTITIES,
      summary: summary({ sources: ['agent:(p,a)', 'agent:(p,c)'] }),
      graph: GRAPH,
    });
    const reverse = deriveSourceHighlight({
      entities: ENTITIES,
      summary: summary({ sources: ['agent:(p,c)', 'agent:(p,a)'] }),
      graph: GRAPH,
    });
    expect(forward.sourceNodeIds).toEqual(reverse.sourceNodeIds);
  });
});

// ---------------------------------------------------------------------------
// deriveReachabilityHighlight — fan-in AND fan-out for the selection
// ---------------------------------------------------------------------------

describe('deriveReachabilityHighlight', () => {
  it('returns the non-claiming empty answer when nothing is selected', () => {
    const h = deriveReachabilityHighlight({
      selectedEntityId: null,
      fanin: { data: undefined, ...IDLE },
      fanout: { data: undefined, ...IDLE },
      graph: GRAPH,
    });
    expect(h.selectedNodeId).toBeNull();
    expect(h.litNodeIds).toEqual([]);
    expect(h.hasAnswer).toBe(false);
  });

  it('treats a selection that names no drawn node as no selection', () => {
    // A stale `?eid` from another trace. There is no node to anchor the question
    // on, so no answer — and emphatically not an answer of "nothing found".
    const h = deriveReachabilityHighlight({
      selectedEntityId: 'not-in-this-trace',
      fanin: { data: reach('fanin', { entities: [reached('a', 1)] }), ...IDLE },
      fanout: { data: undefined, ...IDLE },
      graph: GRAPH,
    });
    expect(h.selectedNodeId).toBeNull();
    expect(h.litNodeIds).toEqual([]);
  });

  it('maps FAN-IN only: upstream nodes, the traversed legs, and hops', () => {
    const h = deriveReachabilityHighlight({
      selectedEntityId: 'b',
      fanin: {
        data: reach('fanin', {
          entities: [reached('a', 1)],
          legs: [traversed('ix1', 'request', 'a', 'b', 1)],
          state: 'derived',
        }),
        ...IDLE,
      },
      fanout: { data: reach('fanout', { state: 'no-adjacent' }), ...IDLE },
      graph: GRAPH,
    });
    expect(h.fanin.nodeIds).toEqual(['a']);
    // THE ROUTE, as a drawn edge id — the reason the endpoint returns legs at all.
    expect(h.fanin.edgeIds).toEqual(['ix1:request']);
    expect(h.fanin.hopsByNodeId.get('a')).toBe(1);
    expect(h.fanin.state).toBe('derived');
    // The other direction stays its own, empty, answer — not merged in.
    expect(h.fanout.nodeIds).toEqual([]);
    // The seed is always lit: dimming the node just clicked would be absurd.
    expect(h.litNodeIds).toEqual(['a', 'b']);
    expect(h.hasAnswer).toBe(true);
  });

  it('maps FAN-OUT only, keeping it separate from fan-in', () => {
    const h = deriveReachabilityHighlight({
      selectedEntityId: 'b',
      fanin: { data: reach('fanin', { state: 'no-adjacent' }), ...IDLE },
      fanout: {
        data: reach('fanout', {
          entities: [reached('c', 1)],
          legs: [traversed('ix2', 'request', 'b', 'c', 3)],
          state: 'derived',
        }),
        ...IDLE,
      },
      graph: GRAPH,
    });
    expect(h.fanout.nodeIds).toEqual(['c']);
    expect(h.fanout.edgeIds).toEqual(['ix2:request']);
    expect(h.fanin.nodeIds).toEqual([]);
    expect(h.litNodeIds).toEqual(['b', 'c']);
  });

  it('keeps BOTH directions distinguishable when an entity is upstream AND downstream', () => {
    // The ordinary shape of every tool call: the request leg puts `c` downstream
    // and its response leg puts `c` upstream. A single "related" set would lose
    // which is which — the exact blend this shape exists to prevent.
    const h = deriveReachabilityHighlight({
      selectedEntityId: 'b',
      fanin: {
        data: reach('fanin', {
          entities: [reached('c', 1)],
          legs: [traversed('ix2', 'response', 'c', 'b', 4)],
          state: 'derived',
        }),
        ...IDLE,
      },
      fanout: {
        data: reach('fanout', {
          entities: [reached('c', 1)],
          legs: [traversed('ix2', 'request', 'b', 'c', 3)],
          state: 'derived',
        }),
        ...IDLE,
      },
      graph: GRAPH,
    });
    // `c` appears in both answers, and each answer still names its own leg.
    expect(h.fanin.nodeIds).toEqual(['c']);
    expect(h.fanout.nodeIds).toEqual(['c']);
    expect(h.fanin.edgeIds).toEqual(['ix2:response']);
    expect(h.fanout.edgeIds).toEqual(['ix2:request']);
    // The union lights the node once and both legs.
    expect(h.litNodeIds).toEqual(['b', 'c']);
    expect(h.litEdgeIds).toEqual(['ix2:request', 'ix2:response']);
  });

  it('grades MULTI-HOP distance off the response hops, nearer being smaller', () => {
    const h = deriveReachabilityHighlight({
      selectedEntityId: 'a',
      fanin: { data: reach('fanin', { state: 'no-adjacent' }), ...IDLE },
      fanout: {
        data: reach('fanout', {
          seed_entity_id: 'a',
          entities: [reached('b', 1), reached('c', 2)],
          legs: [
            traversed('ix1', 'request', 'a', 'b', 1),
            traversed('ix2', 'request', 'b', 'c', 3),
          ],
          state: 'derived',
        }),
        ...IDLE,
      },
      graph: GRAPH,
    });
    expect(h.fanout.hopsByNodeId.get('b')).toBe(1);
    expect(h.fanout.hopsByNodeId.get('c')).toBe(2);
    // Both hops of the route are lit, so the reader sees the path and not just the
    // endpoints.
    expect(h.fanout.edgeIds).toEqual(['ix1:request', 'ix2:request']);
  });

  it('keeps an entity that is its OWN source (a cycle returning to the seed)', () => {
    // A genuine fact the server derived — the flow came back. It is kept rather
    // than filtered, and `selectedNodeId` still marks which node was asked about,
    // so the reader can tell "you clicked this" from "and it is also upstream".
    const h = deriveReachabilityHighlight({
      selectedEntityId: 'b',
      fanin: {
        data: reach('fanin', {
          entities: [reached('b', 2), reached('c', 1)],
          legs: [
            traversed('ix2', 'response', 'c', 'b', 4),
            traversed('ix2', 'request', 'b', 'c', 3),
          ],
          state: 'derived',
        }),
        ...IDLE,
      },
      fanout: { data: reach('fanout', { state: 'no-adjacent' }), ...IDLE },
      graph: GRAPH,
    });
    expect(h.fanin.nodeIds).toContain('b');
    expect(h.fanin.hopsByNodeId.get('b')).toBe(2);
    expect(h.selectedNodeId).toBe('b');
  });

  it('passes DERIVED-but-empty through without inventing a different state', () => {
    // A real answer: the walk followed derived hops and found nothing further. Must
    // not be rebranded as `no-adjacent`, which is a different claim.
    const h = deriveReachabilityHighlight({
      selectedEntityId: 'b',
      fanin: { data: reach('fanin', { entities: [], legs: [], state: 'derived' }), ...IDLE },
      fanout: { data: reach('fanout', { state: 'no-adjacent' }), ...IDLE },
      graph: GRAPH,
    });
    expect(h.fanin.state).toBe('derived');
    expect(h.fanin.nodeIds).toEqual([]);
  });

  it('surfaces PENDING with its frontier, never as an empty "nothing flowed"', () => {
    const h = deriveReachabilityHighlight({
      selectedEntityId: 'b',
      fanin: {
        data: reach('fanin', {
          entities: [],
          legs: [],
          state: 'pending',
          pending_frontier: ['a'],
        }),
        ...IDLE,
      },
      fanout: { data: reach('fanout', { state: 'no-adjacent' }), ...IDLE },
      graph: GRAPH,
    });
    expect(h.fanin.state).toBe('pending');
    expect(h.fanin.nodeIds).toEqual([]);
    // The frontier is what makes "not yet" visible rather than looking like a dead
    // end, so it must survive as its own set.
    expect(h.fanin.pendingFrontierNodeIds).toEqual(['a']);
    // And it counts as something worth drawing, even with no reached nodes.
    expect(h.hasAnswer).toBe(true);
  });

  it('discloses a frontier entity with no drawn node instead of dropping it', () => {
    const h = deriveReachabilityHighlight({
      selectedEntityId: 'b',
      fanin: {
        data: reach('fanin', { state: 'pending', pending_frontier: ['a', 'phantom'] }),
        ...IDLE,
      },
      fanout: { data: reach('fanout', { state: 'no-adjacent' }), ...IDLE },
      graph: GRAPH,
    });
    expect(h.fanin.pendingFrontierNodeIds).toEqual(['a']);
    expect(h.fanin.unresolvedFrontier).toEqual([{ ref: 'phantom' }]);
  });

  it('reports NO-ADJACENT as its own state — the one complete empty answer', () => {
    const h = deriveReachabilityHighlight({
      selectedEntityId: 'b',
      fanin: { data: reach('fanin', { state: 'no-adjacent' }), ...IDLE },
      fanout: { data: reach('fanout', { state: 'no-adjacent' }), ...IDLE },
      graph: GRAPH,
    });
    expect(h.fanin.state).toBe('no-adjacent');
    expect(h.fanout.state).toBe('no-adjacent');
    expect(h.hasAnswer).toBe(false);
  });

  it('carries TRUNCATED separately from the frontier — they are different claims', () => {
    // ADR-0028 D15: the frontier means "ask again later", truncated means "derived
    // but not all returned". Merging them would send a reader to poll for something
    // only a wider bound produces.
    const h = deriveReachabilityHighlight({
      selectedEntityId: 'b',
      fanin: {
        data: reach('fanin', {
          entities: [reached('a', 1)],
          state: 'derived',
          truncated: true,
          pending_frontier: [],
        }),
        ...IDLE,
      },
      fanout: { data: reach('fanout', { state: 'no-adjacent' }), ...IDLE },
      graph: GRAPH,
    });
    expect(h.fanin.truncated).toBe(true);
    expect(h.fanin.pendingFrontierNodeIds).toEqual([]);
  });

  it('a FAILED read is a fourth state and does not erase the other direction', () => {
    // The reason the two directions keep their own error flags: "we could not ask
    // downstream" must not look like "we know nothing at all".
    const h = deriveReachabilityHighlight({
      selectedEntityId: 'b',
      fanin: {
        data: reach('fanin', {
          entities: [reached('a', 1)],
          legs: [traversed('ix1', 'request', 'a', 'b', 1)],
          state: 'derived',
        }),
        ...IDLE,
      },
      fanout: { data: undefined, isError: true, isLoading: false },
      graph: GRAPH,
    });
    expect(h.fanout.isError).toBe(true);
    expect(h.fanout.nodeIds).toEqual([]);
    // The good half survives intact.
    expect(h.fanin.state).toBe('derived');
    expect(h.fanin.nodeIds).toEqual(['a']);
    expect(h.litNodeIds).toEqual(['a', 'b']);
  });

  it('a LOADING direction is neither an answer nor an error', () => {
    const h = deriveReachabilityHighlight({
      selectedEntityId: 'b',
      fanin: { data: undefined, isError: false, isLoading: true },
      fanout: { data: undefined, isError: false, isLoading: true },
      graph: GRAPH,
    });
    expect(h.fanin.isLoading).toBe(true);
    expect(h.fanin.isError).toBe(false);
    expect(h.hasAnswer).toBe(false);
  });

  it('skips a traversed leg the graph did not draw rather than inventing an edge', () => {
    const h = deriveReachabilityHighlight({
      selectedEntityId: 'b',
      fanin: {
        data: reach('fanin', {
          entities: [reached('a', 1)],
          legs: [
            traversed('ix1', 'request', 'a', 'b', 1),
            traversed('ix-undrawn', 'request', 'a', 'b', 9),
          ],
          state: 'derived',
        }),
        ...IDLE,
      },
      fanout: { data: reach('fanout', { state: 'no-adjacent' }), ...IDLE },
      graph: GRAPH,
    });
    expect(h.fanin.edgeIds).toEqual(['ix1:request']);
  });

  it('skips a reached entity the graph has no node for', () => {
    const h = deriveReachabilityHighlight({
      selectedEntityId: 'b',
      fanin: {
        data: reach('fanin', {
          entities: [reached('a', 1), reached('phantom', 2)],
          state: 'derived',
        }),
        ...IDLE,
      },
      fanout: { data: reach('fanout', { state: 'no-adjacent' }), ...IDLE },
      graph: GRAPH,
    });
    expect(h.fanin.nodeIds).toEqual(['a']);
    expect(h.fanin.hopsByNodeId.has('phantom')).toBe(false);
    // The server's own verdict is untouched by a drawability drop.
    expect(h.fanin.state).toBe('derived');
  });

  it('de-duplicates a leg the walk reported twice, preserving response order', () => {
    const h = deriveReachabilityHighlight({
      selectedEntityId: 'a',
      fanin: { data: reach('fanin', { state: 'no-adjacent' }), ...IDLE },
      fanout: {
        data: reach('fanout', {
          seed_entity_id: 'a',
          entities: [reached('b', 1)],
          legs: [
            traversed('ix2', 'request', 'b', 'c', 3),
            traversed('ix1', 'request', 'a', 'b', 1),
            traversed('ix2', 'request', 'b', 'c', 3),
          ],
          state: 'derived',
        }),
        ...IDLE,
      },
      graph: GRAPH,
    });
    expect(h.fanout.edgeIds).toEqual(['ix2:request', 'ix1:request']);
  });

  it('does not trim a large fanout, even though it may only reflect a trivial matcher', () => {
    // Verified against a live trace: fan-out of a LEAF TOOL reached every entity,
    // because its response delivers data back to its caller (ADR-0028 D15). If that
    // surprises a reader the UI must not "helpfully" hide it.
    const h = deriveReachabilityHighlight({
      selectedEntityId: 'c',
      fanin: { data: reach('fanin', { state: 'no-adjacent' }), ...IDLE },
      fanout: {
        data: reach('fanout', {
          seed_entity_id: 'c',
          entities: [reached('b', 1), reached('a', 2)],
          legs: [
            traversed('ix2', 'response', 'c', 'b', 4),
            traversed('ix1', 'response', 'b', 'a', 2),
          ],
          state: 'derived',
        }),
        ...IDLE,
      },
      graph: GRAPH,
    });
    expect(h.fanout.nodeIds).toEqual(['a', 'b']);
    expect(h.fanout.edgeIds).toHaveLength(2);
  });
});
