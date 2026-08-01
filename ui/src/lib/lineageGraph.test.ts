import { describe, it, expect } from 'vitest';
import { deriveLineageHighlight } from './lineageGraph';
import { deriveGraph } from './graph';
import type { Entity, Interaction, InteractionLeg } from './flow';
import type { DataLineage, DataLineageByLeg } from '../types';

/**
 * The Lineage view's highlight derivation. This is where that view's real coverage
 * lives, for exactly the reason `graph.test.ts` states for its own: jsdom cannot lay
 * out or measure an SVG, so the SHAPE of the answer is proven here as pure logic and
 * the render test is kept to what jsdom can honestly assert (a class, a prop, a
 * word on screen — never a pixel of dimming).
 *
 * The fixtures below are deliberately the SAME shape as `graph.test.ts`'s (`ent`,
 * `leg`, `ix`, `ixReqOnly`), because this module composes with `deriveGraph` rather
 * than reimplementing it: the graph a test derives is the graph the highlight is
 * computed against, so a divergent fixture set would be two different graphs and the
 * composition would go untested.
 *
 * THE DEFINITION under test (see the module's own header for the full justification):
 * the union of `data_sources` over every DERIVED lineage row whose drawn edge
 * TARGETS the selected entity — inbound legs only, direct only, never transitive —
 * resolved natural key → entity id.
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

/** An interaction with BOTH legs — the normal completed shape. */
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
 * A derived lineage triple. `sources` are NATURAL KEYS (that is what the wire
 * carries — ADR-0027), so the fixtures spell them as `agent:(p,<id>)` to match
 * `ent`'s own key shape. Writing an entity ID here would silently make every test
 * pass against a broken bridge, which is the one thing this file must not do.
 */
function lin(sources: string[], over: Partial<DataLineage> = {}): DataLineage {
  return {
    data_sources: sources,
    source_transformations: {},
    entities: [],
    seq: 1,
    ...over,
  };
}

/** The natural key `ent(id)` produces — the lineage's own way of naming that entity. */
const key = (id: string) => `agent:(p,${id})`;

/** A `DataLineageByLeg` from `<interaction>:<leg_type>` → triple (or null). */
function byLegOf(entries: Array<[string, DataLineage | null]>): DataLineageByLeg {
  return new Map(entries);
}

describe('deriveLineageHighlight', () => {
  // --- No selection. Nothing asked, so nothing may be claimed OR dimmed.

  it('claims nothing when no entity is selected', () => {
    const h = deriveLineageHighlight({
      entities: [ent('e1'), ent('e2')],
      interactions: [ix('i1', 'e1', 'e2', 1, 2)],
      byLeg: byLegOf([['i1:request', lin([key('e1')])]]),
      selectedEntityId: null,
    });

    expect(h.selectedNodeId).toBeNull();
    expect(h.highlightedNodeIds).toEqual([]);
    expect(h.highlightedEdgeIds).toEqual([]);
    // NOT 'pending': nothing was asked, so there is nothing to wait for. The view
    // renders its "select an entity" instruction off the null selectedNodeId.
    expect(h.state).toBe('no-inbound');
    expect(h.inboundLegs).toBe(0);
    expect(h.derivedLegs).toBe(0);
  });

  it('passes the trace coverage status through even with no selection', () => {
    // The truncation is a fact about the TRACE, so it is knowable before anything is
    // selected and must not be withheld until something is.
    const h = deriveLineageHighlight({
      entities: [ent('e1')],
      interactions: [],
      byLeg: byLegOf([]),
      status: 'partial',
      selectedEntityId: null,
    });

    expect(h.status).toBe('partial');
  });

  it('claims nothing for a stale selection naming no entity in this trace', () => {
    // A `?eid` from another trace, or one the entities read has not returned. There
    // is no node to anchor the question on, so there is no answer — and emphatically
    // no highlight of some arbitrary other node.
    const h = deriveLineageHighlight({
      entities: [ent('e1'), ent('e2')],
      interactions: [ix('i1', 'e1', 'e2', 1, 2)],
      byLeg: byLegOf([['i1:request', lin([key('e1')])]]),
      selectedEntityId: 'ghost',
    });

    expect(h.selectedNodeId).toBeNull();
    expect(h.highlightedNodeIds).toEqual([]);
    expect(h.state).toBe('no-inbound');
  });

  // --- THE happy path: a selected entity with derived sources.

  it('highlights the resolved sources of the legs that deliver to the selected entity', () => {
    // e1 calls e2; the REQUEST leg targets e2, and its lineage says the data came
    // from e1 and e3. Both resolve to nodes, so both are lit.
    const h = deriveLineageHighlight({
      entities: [ent('e1'), ent('e2'), ent('e3')],
      interactions: [ix('i1', 'e1', 'e2', 1, 2)],
      byLeg: byLegOf([['i1:request', lin([key('e1'), key('e3')])]]),
      selectedEntityId: 'e2',
    });

    expect(h.selectedNodeId).toBe('e2');
    expect(h.highlightedNodeIds).toEqual(['e1', 'e3']);
    expect(h.state).toBe('derived');
    expect(h.inboundLegs).toBe(1);
    expect(h.derivedLegs).toBe(1);
    expect(h.unresolved).toEqual([]);
  });

  it('bridges natural key → entity id — a source is NEVER matched by id', () => {
    // THE CRUX. Lineage cites a natural key; the graph is keyed on entity id. Here
    // the two are deliberately unrelated strings, so an implementation that compared
    // the lineage value against `entity.id` would light nothing and one that compared
    // against the key would light the right node.
    const oddball = ent('e1', { natural_key: 'agent:(proj,totally-different)' });
    const h = deriveLineageHighlight({
      entities: [oddball, ent('e2')],
      interactions: [ix('i1', 'e1', 'e2', 1, 2)],
      byLeg: byLegOf([['i1:request', lin(['agent:(proj,totally-different)'])]]),
      selectedEntityId: 'e2',
    });

    expect(h.highlightedNodeIds).toEqual(['e1']);
    expect(h.unresolved).toEqual([]);
  });

  it('highlights the LEGS that carried the data, not every edge touching a source', () => {
    // i1's request delivers to e2 and its lineage names e1. i2 also runs between the
    // same pair but has no derived lineage, so its arrows are NOT part of the answer
    // even though they touch a highlighted node.
    const h = deriveLineageHighlight({
      entities: [ent('e1'), ent('e2')],
      interactions: [ix('i1', 'e1', 'e2', 1, 2), ix('i2', 'e1', 'e2', 3, 4)],
      byLeg: byLegOf([['i1:request', lin([key('e1')])]]),
      selectedEntityId: 'e2',
    });

    expect(h.highlightedEdgeIds).toEqual(['i1:request']);
    expect(h.highlightedNodeIds).toEqual(['e1']);
    // i2's two legs deliver to e2 as well (one each way), so they COUNT as inbound
    // — the answer is partial, not complete — but they are not lit.
    expect(h.inboundLegs).toBe(2); // i1:request + i2:request (both target e2)
    expect(h.derivedLegs).toBe(1);
  });

  it('counts a RESPONSE leg as a delivery to the caller (the per-leg direction)', () => {
    // The substance of ADR-0025 for this view: an agent's data mostly arrives as the
    // responses to its own calls. e1 calls e2, so `i1:response` travels e2 → e1 and
    // is an inbound delivery to e1. A definition keyed on the interaction's fixed
    // caller→callee direction would miss all of it.
    const h = deriveLineageHighlight({
      entities: [ent('e1'), ent('e2'), ent('e3')],
      interactions: [ix('i1', 'e1', 'e2', 1, 2)],
      byLeg: byLegOf([['i1:response', lin([key('e3')])]]),
      selectedEntityId: 'e1',
    });

    expect(h.inboundLegs).toBe(1);
    expect(h.highlightedEdgeIds).toEqual(['i1:response']);
    expect(h.highlightedNodeIds).toEqual(['e3']);
  });

  it('ignores the OUTBOUND legs — the data leaving an entity is not its own lineage', () => {
    // The rejected alternative ("every leg where the entity is a participant") made
    // concrete. `i1:request` leaves e1; its lineage names e9. Asked where e1's data
    // came FROM, e9 is not an answer — it is where e1's own output was sourced for
    // the benefit of e2.
    const h = deriveLineageHighlight({
      entities: [ent('e1'), ent('e2'), ent('e9')],
      interactions: [ixReqOnly('i1', 'e1', 'e2', 1)],
      byLeg: byLegOf([['i1:request', lin([key('e9')])]]),
      selectedEntityId: 'e1',
    });

    // Nothing targets e1 at all, so there is nothing to roll up.
    expect(h.inboundLegs).toBe(0);
    expect(h.state).toBe('no-inbound');
    expect(h.highlightedNodeIds).toEqual([]);
    expect(h.highlightedEdgeIds).toEqual([]);
  });

  it('unions the sources across SEVERAL contributing legs, de-duplicated', () => {
    // Two different interactions both deliver to e3, and both name e1 among their
    // sources. e1 is lit once — the answer is a SET of origins, not a bag.
    const h = deriveLineageHighlight({
      entities: [ent('e1'), ent('e2'), ent('e3')],
      interactions: [ix('i1', 'e1', 'e3', 1, 2), ix('i2', 'e2', 'e3', 3, 4)],
      byLeg: byLegOf([
        ['i1:request', lin([key('e1')])],
        ['i2:request', lin([key('e1'), key('e2')])],
      ]),
      selectedEntityId: 'e3',
    });

    expect(h.highlightedNodeIds).toEqual(['e1', 'e2']);
    // Both legs contributed, in seq order (inherited from deriveGraph's edges).
    expect(h.highlightedEdgeIds).toEqual(['i1:request', 'i2:request']);
    expect(h.inboundLegs).toBe(2);
    expect(h.derivedLegs).toBe(2);
    expect(h.state).toBe('derived');
  });

  it('lists the contributing edges in seq order, not in interaction arrival order', () => {
    // `late` is listed first but happened second. The edge list is the chronology of
    // the deliveries, which is what makes it readable next to the Flat tab.
    const h = deriveLineageHighlight({
      entities: [ent('a'), ent('b'), ent('hub')],
      interactions: [ixReqOnly('late', 'a', 'hub', 5), ixReqOnly('early', 'b', 'hub', 1)],
      byLeg: byLegOf([
        ['late:request', lin([key('a')])],
        ['early:request', lin([key('b')])],
      ]),
      selectedEntityId: 'hub',
    });

    expect(h.highlightedEdgeIds).toEqual(['early:request', 'late:request']);
  });

  it('takes data_sources, NOT the `entities` transit set', () => {
    // `entities` is "entities the data passed THROUGH" — intermediaries, explicitly
    // unordered (ADR-0027). Highlighting them as sources would inflate the answer
    // with transit the backend never called an origin.
    const h = deriveLineageHighlight({
      entities: [ent('e1'), ent('e2'), ent('transit')],
      interactions: [ixReqOnly('i1', 'e1', 'e2', 1)],
      byLeg: byLegOf([
        ['i1:request', lin([key('e1')], { entities: [key('transit'), key('e1')] })],
      ]),
      selectedEntityId: 'e2',
    });

    expect(h.highlightedNodeIds).toEqual(['e1']);
    expect(h.highlightedNodeIds).not.toContain('transit');
  });

  // --- DIRECT ONLY. The claim the backend did not make is not made here.

  it('does NOT walk transitively — a source of a source is not highlighted', () => {
    // e1 → e2 → e3. e3's inbound leg names e2 as its source, and e2's own inbound leg
    // names e1. Asked about e3, only e2 is lit: a transitive claim the backend did
    // not derive would be the UI inventing lineage.
    const h = deriveLineageHighlight({
      entities: [ent('e1'), ent('e2'), ent('e3')],
      interactions: [ixReqOnly('i1', 'e1', 'e2', 1), ixReqOnly('i2', 'e2', 'e3', 2)],
      byLeg: byLegOf([
        ['i1:request', lin([key('e1')])],
        ['i2:request', lin([key('e2')])],
      ]),
      selectedEntityId: 'e3',
    });

    expect(h.highlightedNodeIds).toEqual(['e2']);
    expect(h.highlightedNodeIds).not.toContain('e1');
    // And only the ONE leg that delivered to e3 is lit, not the upstream hop.
    expect(h.highlightedEdgeIds).toEqual(['i2:request']);
  });

  // --- The three absence states, each distinct from the others.

  it('reports `pending` when the contributing leg key is PRESENT with a null lineage', () => {
    // The eventual-consistency window. This must NOT be `derived` with an empty set —
    // that would render "we have not checked" as "we checked and found nothing".
    const h = deriveLineageHighlight({
      entities: [ent('e1'), ent('e2')],
      interactions: [ixReqOnly('i1', 'e1', 'e2', 1)],
      byLeg: byLegOf([['i1:request', null]]),
      selectedEntityId: 'e2',
    });

    expect(h.state).toBe('pending');
    expect(h.highlightedNodeIds).toEqual([]);
    expect(h.highlightedEdgeIds).toEqual([]);
    expect(h.inboundLegs).toBe(1);
    expect(h.derivedLegs).toBe(0);
  });

  it('reports `pending` when the contributing leg key is ABSENT from the map', () => {
    // The other spelling of "no lineage row at all" (e.g. an unmigrated DB). Both
    // mean not-yet-derived, so the state is the same — and it is the same as the null
    // case above, deliberately, because neither may contribute to the union.
    const h = deriveLineageHighlight({
      entities: [ent('e1'), ent('e2')],
      interactions: [ixReqOnly('i1', 'e1', 'e2', 1)],
      byLeg: byLegOf([]),
      selectedEntityId: 'e2',
    });

    expect(h.state).toBe('pending');
    expect(h.derivedLegs).toBe(0);
    expect(h.inboundLegs).toBe(1);
  });

  it('reports `pending` when the whole lineage read is still in flight (byLeg undefined)', () => {
    const h = deriveLineageHighlight({
      entities: [ent('e1'), ent('e2')],
      interactions: [ixReqOnly('i1', 'e1', 'e2', 1)],
      byLeg: undefined,
      selectedEntityId: 'e2',
    });

    expect(h.state).toBe('pending');
    expect(h.highlightedNodeIds).toEqual([]);
  });

  it('reports `derived` for an EMPTY data_sources on a derived row — "originates here"', () => {
    // ADR-0027 D3: an empty triple is a REAL derived value. The third distinct state,
    // and the one an unhighlighted graph cannot show by itself — which is why the
    // view has to say it in words.
    const h = deriveLineageHighlight({
      entities: [ent('e1'), ent('e2')],
      interactions: [ixReqOnly('i1', 'e1', 'e2', 1)],
      byLeg: byLegOf([['i1:request', lin([])]]),
      selectedEntityId: 'e2',
    });

    expect(h.state).toBe('derived');
    expect(h.highlightedNodeIds).toEqual([]);
    expect(h.derivedLegs).toBe(1);
    // The DELIVERY is still lit, even though it produced no source node: the arrow
    // is what makes "this data arrived here, from nowhere upstream" visible at all.
    expect(h.highlightedEdgeIds).toEqual(['i1:request']);
  });

  it('keeps the three absence states mutually distinguishable from the result alone', () => {
    // Stated as one assertion because the whole discipline is that a caller can tell
    // them apart WITHOUT extra context: all three have an empty node set, so the
    // node set can never be the discriminator.
    const base = {
      entities: [ent('e1'), ent('e2')],
      interactions: [ixReqOnly('i1', 'e1', 'e2', 1)],
      selectedEntityId: 'e2',
    };
    const pending = deriveLineageHighlight({ ...base, byLeg: byLegOf([['i1:request', null]]) });
    const derivedEmpty = deriveLineageHighlight({
      ...base,
      byLeg: byLegOf([['i1:request', lin([])]]),
    });
    const noInbound = deriveLineageHighlight({
      ...base,
      selectedEntityId: 'e1', // nothing targets e1 (single request leg, e1 → e2)
      byLeg: byLegOf([['i1:request', lin([])]]),
    });

    expect([pending, derivedEmpty, noInbound].map((h) => h.highlightedNodeIds)).toEqual([
      [],
      [],
      [],
    ]);
    expect(pending.state).toBe('pending');
    expect(derivedEmpty.state).toBe('derived');
    expect(noInbound.state).toBe('no-inbound');
  });

  it('reports `no-inbound`, not `pending`, when nothing delivers to the entity', () => {
    // There is no derivation to wait FOR, so telling the reader to wait would be
    // telling them to wait forever. An isolated entity is the clearest case.
    const h = deriveLineageHighlight({
      entities: [ent('e1'), ent('e2'), ent('lonely')],
      interactions: [ix('i1', 'e1', 'e2', 1, 2)],
      byLeg: byLegOf([]),
      selectedEntityId: 'lonely',
    });

    expect(h.selectedNodeId).toBe('lonely');
    expect(h.state).toBe('no-inbound');
    expect(h.inboundLegs).toBe(0);
  });

  it('reports `no-inbound` for an entity named only by an UNDRAWABLE interaction', () => {
    // The interaction's callee is unresolved, so `deriveGraph` draws no edge for it
    // and there is no visible delivery to roll up. Counting it anyway would claim a
    // route the reader cannot see; `deriveGraph`'s own `dropped` notice is where that
    // interaction is disclosed, and the Lineage tab renders it too.
    const h = deriveLineageHighlight({
      entities: [ent('e1')],
      interactions: [ix('i1', null, 'e1', 1, 2)],
      byLeg: byLegOf([['i1:request', lin([])]]),
      selectedEntityId: 'e1',
    });

    expect(h.state).toBe('no-inbound');
    expect(h.inboundLegs).toBe(0);
  });

  // --- A partial roll-up: some deliveries answered, some not.

  it('reports both leg counts so a partly-answered roll-up can say so', () => {
    // Three legs deliver to `hub`; only one has lineage. `derived` alone would let
    // two thirds of the picture go unmentioned, so the counts are the answer.
    const h = deriveLineageHighlight({
      entities: [ent('a'), ent('b'), ent('c'), ent('hub')],
      interactions: [
        ixReqOnly('i1', 'a', 'hub', 1),
        ixReqOnly('i2', 'b', 'hub', 2),
        ixReqOnly('i3', 'c', 'hub', 3),
      ],
      byLeg: byLegOf([
        ['i1:request', lin([key('a')])],
        ['i2:request', null],
        // i3's key is absent entirely — the third absence spelling.
      ]),
      selectedEntityId: 'hub',
    });

    expect(h.state).toBe('derived');
    expect(h.inboundLegs).toBe(3);
    expect(h.derivedLegs).toBe(1);
    expect(h.highlightedNodeIds).toEqual(['a']);
    expect(h.highlightedEdgeIds).toEqual(['i1:request']);
  });

  // --- EDGE CASE: a source natural key that resolves to no entity.

  it('does not silently drop a source whose natural key matches no entity', () => {
    // A real origin the graph cannot draw — legitimately, e.g. an upstream service
    // the trace never spanned. Reporting "3 sources, 2 nodes lit" without saying so
    // is exactly the silent under-report the graph's own `dropped` notice exists to
    // prevent.
    const h = deriveLineageHighlight({
      entities: [ent('e1'), ent('e2')],
      interactions: [ixReqOnly('i1', 'e1', 'e2', 1)],
      byLeg: byLegOf([
        ['i1:request', lin([key('e1'), 'service:(elsewhere,api)'])],
      ]),
      selectedEntityId: 'e2',
    });

    expect(h.highlightedNodeIds).toEqual(['e1']);
    expect(h.unresolved).toEqual([{ naturalKey: 'service:(elsewhere,api)', legCount: 1 }]);
    // Still a derived answer — the unresolvable source does not make it pending.
    expect(h.state).toBe('derived');
  });

  it('counts how many contributing legs named each unresolvable source', () => {
    // The count is what lets the notice weigh the omission: one stray row and a
    // source cited by every delivery are different problems.
    const h = deriveLineageHighlight({
      entities: [ent('a'), ent('b'), ent('hub')],
      interactions: [ixReqOnly('i1', 'a', 'hub', 1), ixReqOnly('i2', 'b', 'hub', 2)],
      byLeg: byLegOf([
        ['i1:request', lin(['ghost:one', 'ghost:two'])],
        ['i2:request', lin(['ghost:one'])],
      ]),
      selectedEntityId: 'hub',
    });

    expect(h.unresolved).toEqual([
      { naturalKey: 'ghost:one', legCount: 2 },
      { naturalKey: 'ghost:two', legCount: 1 },
    ]);
    expect(h.highlightedNodeIds).toEqual([]);
    // The legs still carried data, so both arrows are lit even though no node is.
    expect(h.highlightedEdgeIds).toEqual(['i1:request', 'i2:request']);
  });

  it('reports unresolvable sources in a deterministic (sorted) order', () => {
    // A reader reloading must see the same notice, not the same set in a different
    // order — the map's insertion order depends on leg order, which a later poll can
    // change.
    const h = deriveLineageHighlight({
      entities: [ent('e1'), ent('e2')],
      interactions: [ixReqOnly('i1', 'e1', 'e2', 1)],
      byLeg: byLegOf([['i1:request', lin(['zzz:key', 'aaa:key', 'mmm:key'])]]),
      selectedEntityId: 'e2',
    });

    expect(h.unresolved.map((u) => u.naturalKey)).toEqual(['aaa:key', 'mmm:key', 'zzz:key']);
  });

  it('skips a blank natural key on an entity rather than letting it answer for anything', () => {
    // A blank key is not an identity. Mapping `''` would let one unrelated entity
    // resolve every unkeyed lineage row — so it is left unresolvable and disclosed.
    const h = deriveLineageHighlight({
      entities: [ent('e1', { natural_key: '' }), ent('e2')],
      interactions: [ixReqOnly('i1', 'e1', 'e2', 1)],
      byLeg: byLegOf([['i1:request', lin([''])]]),
      selectedEntityId: 'e2',
    });

    expect(h.highlightedNodeIds).toEqual([]);
    expect(h.unresolved).toEqual([{ naturalKey: '', legCount: 1 }]);
  });

  // --- EDGE CASE: two entities claiming one natural key.

  it('resolves a duplicated natural key to the FIRST entity and discloses the ambiguity', () => {
    // Should not happen (a natural key is an entity's identity, ADR-0013) — which is
    // why it is detected rather than assumed away. First-wins keeps the map total so
    // a highlight still resolves; `ambiguousKeys` is what stops the arbitrary pick
    // being a silent one.
    const shared = 'agent:(p,twin)';
    const h = deriveLineageHighlight({
      entities: [
        ent('first', { natural_key: shared }),
        ent('second', { natural_key: shared }),
        ent('target'),
      ],
      interactions: [ixReqOnly('i1', 'first', 'target', 1)],
      byLeg: byLegOf([['i1:request', lin([shared])]]),
      selectedEntityId: 'target',
    });

    expect(h.highlightedNodeIds).toEqual(['first']);
    expect(h.ambiguousKeys).toEqual([shared]);
  });

  it('does not report an ambiguity on a key this answer never used', () => {
    // Noise the reader cannot act on: a duplicated key somewhere else in the trace is
    // not a caveat on THIS entity's sources.
    const shared = 'agent:(p,twin)';
    const h = deriveLineageHighlight({
      entities: [
        ent('twinA', { natural_key: shared }),
        ent('twinB', { natural_key: shared }),
        ent('e1'),
        ent('e2'),
      ],
      interactions: [ixReqOnly('i1', 'e1', 'e2', 1)],
      byLeg: byLegOf([['i1:request', lin([key('e1')])]]),
      selectedEntityId: 'e2',
    });

    expect(h.highlightedNodeIds).toEqual(['e1']);
    expect(h.ambiguousKeys).toEqual([]);
  });

  // --- EDGE CASE: an entity that is a source of itself.

  it('keeps an entity that is a source of ITSELF, marking the selection separately', () => {
    // A self-call, or data the entity contributed earlier coming back. The backend
    // derived it, so it is a real origin and filtering it would silently deny one.
    // `selectedNodeId` is what lets the reader still see which node they asked about.
    const h = deriveLineageHighlight({
      entities: [ent('solo')],
      interactions: [ix('i1', 'solo', 'solo', 1, 2)],
      byLeg: byLegOf([['i1:request', lin([key('solo')])]]),
      selectedEntityId: 'solo',
    });

    expect(h.selectedNodeId).toBe('solo');
    expect(h.highlightedNodeIds).toEqual(['solo']);
    // A self-call's BOTH legs target the same entity, so both are inbound; only the
    // derived one is lit.
    expect(h.inboundLegs).toBe(2);
    expect(h.derivedLegs).toBe(1);
    expect(h.highlightedEdgeIds).toEqual(['i1:request']);
  });

  it('keeps a non-self entity as its own source when a normal leg names it', () => {
    // The other route to the same state: e2 receives from e1, and the lineage says
    // some of that data originated at e2 itself (a round trip).
    const h = deriveLineageHighlight({
      entities: [ent('e1'), ent('e2')],
      interactions: [ixReqOnly('i1', 'e1', 'e2', 1)],
      byLeg: byLegOf([['i1:request', lin([key('e1'), key('e2')])]]),
      selectedEntityId: 'e2',
    });

    expect(h.selectedNodeId).toBe('e2');
    expect(h.highlightedNodeIds).toEqual(['e1', 'e2']);
  });

  // --- Trace coverage (ADR-0027 D6).

  it('passes `partial` through untouched rather than folding it into the state', () => {
    // The truncation is a fact about the whole TRACE, not about this entity, so it
    // must not be mistaken for the roll-up's own certainty. A `partial` trace can
    // still have a fully derived answer for one entity — and that answer is still
    // incomplete, which only the status can say.
    const h = deriveLineageHighlight({
      entities: [ent('e1'), ent('e2')],
      interactions: [ixReqOnly('i1', 'e1', 'e2', 1)],
      byLeg: byLegOf([['i1:request', lin([key('e1')])]]),
      status: 'partial',
      selectedEntityId: 'e2',
    });

    expect(h.status).toBe('partial');
    // Still `derived`: every inbound leg here IS derived. The prefix is a separate
    // caveat, which is exactly why it is a separate field.
    expect(h.state).toBe('derived');
    expect(h.derivedLegs).toBe(h.inboundLegs);
    expect(h.highlightedNodeIds).toEqual(['e1']);
  });

  it('defaults the status to null — unknown, never `complete`', () => {
    const h = deriveLineageHighlight({
      entities: [ent('e1')],
      interactions: [],
      byLeg: byLegOf([]),
      selectedEntityId: 'e1',
    });

    expect(h.status).toBeNull();
  });

  // --- Composition with deriveGraph, and purity.

  it('computes against the CALLER\'s graph when one is passed, not a re-derivation', () => {
    // The view derives the graph once and renders it, so the highlight must be
    // computed against that very spec — a highlight naming an element the drawn graph
    // does not contain would be unreachable. Passing the spec in is what makes that a
    // guarantee rather than a coincidence.
    const entities = [ent('e1'), ent('e2')];
    const interactions = [ixReqOnly('i1', 'e1', 'e2', 1)];
    const spec = deriveGraph(entities, interactions);
    const h = deriveLineageHighlight({
      entities,
      interactions,
      byLeg: byLegOf([['i1:request', lin([key('e1')])]]),
      selectedEntityId: 'e2',
      graph: spec,
    });

    // Every id returned is an id that graph actually contains.
    const nodeIds = new Set(spec.nodes.map((n) => n.id));
    const edgeIds = new Set(spec.edges.map((e) => e.id));
    expect(h.highlightedNodeIds.every((id) => nodeIds.has(id))).toBe(true);
    expect(h.highlightedEdgeIds.every((id) => edgeIds.has(id))).toBe(true);
    expect(h.selectedNodeId).not.toBeNull();
    expect(nodeIds.has(h.selectedNodeId!)).toBe(true);
  });

  it('gives the same answer with and without the pre-derived graph', () => {
    // The `graph` argument is an optimisation and an agreement guarantee, never a
    // behaviour switch — so a test may omit it and still be testing the real thing.
    const args = {
      entities: [ent('e1'), ent('e2')],
      interactions: [ixReqOnly('i1', 'e1', 'e2', 1)],
      byLeg: byLegOf([['i1:request', lin([key('e1')])]]),
      selectedEntityId: 'e2',
    };

    expect(deriveLineageHighlight(args)).toEqual(
      deriveLineageHighlight({
        ...args,
        graph: deriveGraph(args.entities, args.interactions),
      }),
    );
  });

  it('is deterministic: the same input yields the same answer every time', () => {
    // A reader reloading the tab must get the same highlight. Nothing may depend on
    // Map/Set iteration order of anything but insertion.
    const args = {
      entities: [ent('z'), ent('y'), ent('x'), ent('hub')],
      interactions: [ixReqOnly('i1', 'z', 'hub', 3), ixReqOnly('i2', 'y', 'hub', 1)],
      byLeg: byLegOf([
        ['i1:request', lin([key('z'), 'ghost:b'])],
        ['i2:request', lin([key('y'), key('x'), 'ghost:a'])],
      ]),
      selectedEntityId: 'hub',
    };

    const first = deriveLineageHighlight(args);
    for (let i = 0; i < 5; i += 1) {
      expect(deriveLineageHighlight(args)).toEqual(first);
    }
    expect(first.highlightedNodeIds).toEqual(['x', 'y', 'z']);
    // Edge order is the seq chronology: i2 (seq 1) before i1 (seq 3).
    expect(first.highlightedEdgeIds).toEqual(['i2:request', 'i1:request']);
    expect(first.unresolved.map((u) => u.naturalKey)).toEqual(['ghost:a', 'ghost:b']);
  });

  it('is a pure function of its inputs — it mutates none of them', () => {
    const entities = [ent('e1'), ent('e2')];
    const interactions = [ix('i1', 'e1', 'e2', 2, 1)];
    const byLeg = byLegOf([['i1:request', lin([key('e1')])]]);
    const entitiesCopy = structuredClone(entities);
    const interactionsCopy = structuredClone(interactions);
    const byLegCopy = new Map(byLeg);

    deriveLineageHighlight({ entities, interactions, byLeg, selectedEntityId: 'e2' });

    expect(entities).toEqual(entitiesCopy);
    expect(interactions).toEqual(interactionsCopy);
    expect(byLeg).toEqual(byLegCopy);
  });

  // --- Empty / degenerate inputs.

  it('returns the no-selection answer for entirely empty input', () => {
    const h = deriveLineageHighlight({
      entities: [],
      interactions: [],
      byLeg: byLegOf([]),
      selectedEntityId: null,
    });

    expect(h).toEqual({
      selectedNodeId: null,
      highlightedNodeIds: [],
      highlightedEdgeIds: [],
      unresolved: [],
      ambiguousKeys: [],
      state: 'no-inbound',
      inboundLegs: 0,
      derivedLegs: 0,
      status: null,
    });
  });

  it('claims nothing when a selection is given but the trace has no entities', () => {
    const h = deriveLineageHighlight({
      entities: [],
      interactions: [ix('i1', 'e1', 'e2', 1, 2)],
      byLeg: byLegOf([['i1:request', lin(['whatever'])]]),
      selectedEntityId: 'e1',
    });

    expect(h.selectedNodeId).toBeNull();
    expect(h.highlightedNodeIds).toEqual([]);
    // Nothing is disclosed either: the question could not be asked, so there is no
    // answer to caveat.
    expect(h.unresolved).toEqual([]);
  });

  it('handles a realistic mixed trace: every case at once', () => {
    // user → agent → tool, a self-call on agent, an in-flight call, an unresolved
    // participant, an isolated entity, an unresolvable source, and a partly-derived
    // roll-up — all against the ONE selected entity `agent`.
    const h = deriveLineageHighlight({
      entities: [
        ent('user', { kind: 'user' }),
        ent('agent'),
        ent('tool', { kind: 'tool' }),
        ent('orphan', { kind: 'external_service' }),
      ],
      interactions: [
        ix('a', 'user', 'agent', 1, 10), // request delivers to agent; response to user
        ix('b', 'agent', 'tool', 2, 3), // response delivers to agent
        ix('d', 'agent', 'agent', 6, 7), // self-call: both legs deliver to agent
        ixReqOnly('e', 'agent', 'tool', 8), // outbound only
        ix('f', 'tool', null, 9, 11), // undrawable — contributes nothing
      ],
      byLeg: byLegOf([
        // Inbound to agent, derived: names user (resolvable) + an outside service.
        ['a:request', lin([key('user'), 'service:(elsewhere,crm)'])],
        // Inbound to agent, derived: the tool's answer came from the tool.
        ['b:response', lin([key('tool')])],
        // Inbound to agent (self-call request), NOT yet derived.
        ['d:request', null],
        // d:response absent from the map entirely.
        // Outbound legs' lineage exists but is irrelevant to this question.
        ['e:request', lin([key('orphan')])],
      ]),
      status: 'partial',
      selectedEntityId: 'agent',
    });

    expect(h.selectedNodeId).toBe('agent');
    // a:request, b:response, d:request, d:response → four inbound legs; two derived.
    expect(h.inboundLegs).toBe(4);
    expect(h.derivedLegs).toBe(2);
    expect(h.state).toBe('derived');
    // The union of the two derived rows' sources, resolved. `orphan` is NOT in it —
    // it is the source of what agent SENT, not of what it received.
    expect(h.highlightedNodeIds).toEqual(['tool', 'user']);
    // The two contributing legs, in seq order (a:request seq 1, b:response seq 3).
    expect(h.highlightedEdgeIds).toEqual(['a:request', 'b:response']);
    // The outside origin is disclosed rather than dropped.
    expect(h.unresolved).toEqual([{ naturalKey: 'service:(elsewhere,crm)', legCount: 1 }]);
    // And the trace-level prefix rides along, unfolded into the state.
    expect(h.status).toBe('partial');
    expect(h.ambiguousKeys).toEqual([]);
  });
});
