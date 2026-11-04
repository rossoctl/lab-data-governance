import { describe, expect, it } from 'vitest';
import type { Entity, Interaction } from './flow';
import { filterTrace, isFilterActive, NO_GRAPH_FILTER } from './graphFilter';

const ent = (id: string): Entity => ({ id, kind: 'agent', natural_key: id, display_name: id, detected_from: 'test' });
const ix = (id: string, caller: string, callee: string, parent: string | null = null): Interaction => ({
  id,
  caller_entity_id: caller,
  callee_entity_id: callee,
  summary: `${caller} → ${callee}`,
  parent_interaction_id: parent,
  legs: [],
  duration_seconds: null,
  any_error: null,
  span_count: 2,
  anchor_count: 1,
});

// client → hub; the hub calls tool-a (which calls db), tool-b and an LLM.
//   i1 client→hub
//     i2 hub→tool-a
//       i3 tool-a→db
//     i4 hub→tool-b
//     i5 hub→llm
const entities = ['client', 'hub', 'tool-a', 'db', 'tool-b', 'llm', 'lonely'].map(ent);
const interactions = [
  ix('i1', 'client', 'hub'),
  ix('i2', 'hub', 'tool-a', 'i1'),
  ix('i3', 'tool-a', 'db', 'i2'),
  ix('i4', 'hub', 'tool-b', 'i1'),
  ix('i5', 'hub', 'llm', 'i1'),
];
const ids = (xs: readonly { id: string }[]) => xs.map((x) => x.id).sort();

describe('filterTrace', () => {
  it('keeps everything with no filter', () => {
    const out = filterTrace(entities, interactions, NO_GRAPH_FILTER);
    expect(ids(out.interactions)).toEqual(['i1', 'i2', 'i3', 'i4', 'i5']);
    expect(out.entities).toHaveLength(entities.length);
    expect(isFilterActive(NO_GRAPH_FILTER)).toBe(false);
  });

  it('entity: the calls that led to it and what it set off — not its siblings', () => {
    const out = filterTrace(entities, interactions, { entityId: 'tool-a', minRiskLevel: null });
    expect(ids(out.interactions)).toEqual(['i1', 'i2', 'i3']);
    expect(ids(out.entities)).toEqual(['client', 'db', 'hub', 'tool-a']);
  });

  it('entity: a hub keeps every path through it', () => {
    const out = filterTrace(entities, interactions, { entityId: 'hub', minRiskLevel: null });
    expect(ids(out.interactions)).toEqual(['i1', 'i2', 'i3', 'i4', 'i5']);
  });

  it('entity: one with no interactions stays drawn, alone', () => {
    const out = filterTrace(entities, interactions, { entityId: 'lonely', minRiskLevel: null });
    expect(out.interactions).toEqual([]);
    expect(ids(out.entities)).toEqual(['lonely']);
  });

  it('risk: the interactions at or above the level, with the calls that led there', () => {
    const levels = new Map([
      ['i1', 'none'],
      ['i3', 'critical'],
      ['i4', 'medium'],
      ['i5', 'low'],
    ]);
    const critical = filterTrace(entities, interactions, { entityId: null, minRiskLevel: 'critical' }, levels);
    expect(ids(critical.interactions)).toEqual(['i1', 'i2', 'i3']);
    const medium = filterTrace(entities, interactions, { entityId: null, minRiskLevel: 'medium' }, levels);
    expect(ids(medium.interactions)).toEqual(['i1', 'i2', 'i3', 'i4']);
  });

  it('risk: an interaction with no computed level never passes', () => {
    const out = filterTrace(entities, interactions, { entityId: null, minRiskLevel: 'low' }, new Map());
    expect(out.interactions).toEqual([]);
    expect(out.entities).toEqual([]);
  });

  it('both filters intersect', () => {
    const levels = new Map([['i3', 'critical'], ['i4', 'critical']]);
    const out = filterTrace(entities, interactions, { entityId: 'tool-b', minRiskLevel: 'critical' }, levels);
    expect(ids(out.interactions)).toEqual(['i1', 'i4']);
  });

  it('both: a risky call on a sibling branch does not keep the shared ancestor', () => {
    const levels = new Map([['i4', 'critical']]);
    const out = filterTrace(entities, interactions, { entityId: 'tool-a', minRiskLevel: 'critical' }, levels);
    expect(out.interactions).toEqual([]);
  });

  it('survives a parent cycle', () => {
    const cyclic = [ix('a', 'client', 'hub', 'b'), ix('b', 'hub', 'tool-a', 'a')];
    const out = filterTrace(entities, cyclic, { entityId: 'tool-a', minRiskLevel: null });
    expect(ids(out.interactions)).toEqual(['a', 'b']);
  });
});
