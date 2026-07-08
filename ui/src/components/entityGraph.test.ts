import { describe, it, expect } from 'vitest';
import { buildEntityGraph } from './entityGraph';
import type { Entity, Interaction } from '../types';

const ENTITIES: Entity[] = [
  { id: 'e1', kind: 'agent', natural_key: 'agent:(p,a)', display_name: 'agent-a', detected_from: '' },
  { id: 'e2', kind: 'tool', natural_key: 'tool:(p,svc)', display_name: 'search', detected_from: '' },
  { id: 'e3', kind: 'llm', natural_key: 'llm:(gpt)', display_name: 'gpt', detected_from: '' },
];
const ix = (over: Partial<Interaction>): Interaction => ({
  id: 'i', caller_entity_id: 'e1', callee_entity_id: 'e2', started_at: null, ended_at: null,
  error: null, request_payload_hash: null, response_payload_hash: null, summary: null,
  parent_interaction_id: null, span_count: 0, anchor_count: 0, ...over,
});

// The graph view maps entities -> nodes and interactions -> directed edges
// (caller -> callee). It's the NEW view ADR-0019 adds; the layout builder is
// the pure, testable core (dagre positions the ReactFlow render on top).

describe('buildEntityGraph', () => {
  it('produces one node per entity and one edge per interaction', () => {
    const { nodes, edges } = buildEntityGraph(ENTITIES, [
      ix({ id: 'i1', caller_entity_id: 'e1', callee_entity_id: 'e2' }),
      ix({ id: 'i2', caller_entity_id: 'e1', callee_entity_id: 'e3' }),
    ]);
    expect(nodes.map((n) => n.id).sort()).toEqual(['e1', 'e2', 'e3']);
    expect(edges).toHaveLength(2);
    const e1 = edges.find((e) => e.id.includes('i1'))!;
    expect(e1.source).toBe('e1');
    expect(e1.target).toBe('e2');
  });

  it('drops interactions whose caller or callee entity is unknown', () => {
    const { edges } = buildEntityGraph(ENTITIES, [
      ix({ id: 'ok', caller_entity_id: 'e1', callee_entity_id: 'e2' }),
      ix({ id: 'dangling', caller_entity_id: 'e1', callee_entity_id: 'gone' }),
      ix({ id: 'null-caller', caller_entity_id: null, callee_entity_id: 'e2' }),
    ]);
    expect(edges).toHaveLength(1);
    expect(edges[0].id).toContain('ok');
  });

  it('marks error interactions on the edge for styling', () => {
    const { edges } = buildEntityGraph(ENTITIES, [
      ix({ id: 'boom', caller_entity_id: 'e1', callee_entity_id: 'e2', error: true }),
    ]);
    expect(edges[0].data?.error).toBe(true);
  });
});
