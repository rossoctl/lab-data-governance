import { describe, it, expect } from 'vitest';
import {
  toFlowInteractions,
  toFlowEntities,
  unresolvedEntityIds,
  riskLevelByInteraction,
  violationsOf,
} from './riskForestAdapter';
import { deriveGraph } from './graph';
import type { ForestInteraction, InteractionRisk } from '../risk-api/types';
import type { Entity as FlowEntity } from './flow';

function leg(overrides: Partial<import('../risk-api/types').ForestLeg> = {}) {
  return {
    leg_type: 'request' as const,
    occurred_at: '2026-01-01T00:00:00Z',
    payload_hash: null,
    error: null,
    ...overrides,
  };
}

function risk(overrides: Partial<InteractionRisk> = {}): InteractionRisk {
  return {
    interaction_risk_id: 'ir1',
    interaction_id: 'ix1',
    trace_id: 't1',
    parent_interaction_id: null,
    caller_entity_id: 'e1',
    callee_entity_id: 'e2',
    version: 1,
    computed_at: '2026-01-01T00:00:00Z',
    risk_level: 'high',
    enforcement_type: 'block',
    policy_event_count: 1,
    triggered_rule_ids: ['DG-001'],
    classification_summary: null,
    opa_policy_versions_used: [],
    overall_confidence: 0.9,
    ...overrides,
  };
}

function forestInteraction(overrides: Partial<ForestInteraction> = {}): ForestInteraction {
  return {
    interaction_id: 'ix1',
    trace_id: 't1',
    parent_interaction_id: null,
    caller_entity_id: 'e1',
    callee_entity_id: 'e2',
    summary: 'agent calls tool',
    legs: [leg({ leg_type: 'request' }), leg({ leg_type: 'response', occurred_at: '2026-01-01T00:00:01Z' })],
    risk: null,
    span_count: 2,
    anchor_count: 1,
    ...overrides,
  };
}

function flowEntity(overrides: Partial<FlowEntity> = {}): FlowEntity {
  return {
    id: 'e1',
    kind: 'agent',
    natural_key: 'agent:travel_advisor',
    display_name: 'Travel Advisor',
    detected_from: 'span',
    ...overrides,
  };
}

describe('riskForestAdapter', () => {
  describe('toFlowInteractions', () => {
    it('synthesizes seq from array position: 1, 2, 3', () => {
      const forest = [
        forestInteraction({ interaction_id: 'ix1' }),
        forestInteraction({ interaction_id: 'ix2' }),
        forestInteraction({ interaction_id: 'ix3' }),
      ];
      const flow = toFlowInteractions(forest);
      expect(flow[0].legs.map((l) => l.seq)).toEqual([1]);
      expect(flow[1].legs.map((l) => l.seq)).toEqual([2]);
      expect(flow[2].legs.map((l) => l.seq)).toEqual([3]);
    });

    it('keeps only the request leg — the execution-flow graph draws request arrows only', () => {
      const forest = [
        forestInteraction({
          legs: [
            leg({ leg_type: 'request', occurred_at: '2026-01-01T00:00:00.000Z' }),
            leg({ leg_type: 'response', occurred_at: '2026-01-01T00:00:02.500Z' }),
          ],
        }),
      ];
      const flow = toFlowInteractions(forest);
      expect(flow[0].legs).toHaveLength(1);
      expect(flow[0].legs[0].leg_type).toBe('request');
      expect(flow[0].legs[0].seq).toBe(1);
    });

    it('gives a request-only interaction (no response leg to begin with) exactly one leg', () => {
      const forest = [forestInteraction({ legs: [leg({ leg_type: 'request' })] })];
      const flow = toFlowInteractions(forest);
      expect(flow[0].legs).toHaveLength(1);
      expect(flow[0].legs[0].seq).toBe(1);
    });

    it('duration_seconds is always null — there is no response leg to measure a delta against', () => {
      const forest = [
        forestInteraction({
          legs: [
            leg({ leg_type: 'request', occurred_at: '2026-01-01T00:00:00.000Z' }),
            leg({ leg_type: 'response', occurred_at: '2026-01-01T00:00:02.500Z' }),
          ],
        }),
      ];
      const flow = toFlowInteractions(forest);
      expect(flow[0].duration_seconds).toBeNull();
    });

    it("any_error reflects only the request leg's own outcome", () => {
      const noErrorReported = toFlowInteractions([
        forestInteraction({ legs: [leg({ leg_type: 'request', error: null })] }),
      ]);
      expect(noErrorReported[0].any_error).toBeNull();

      const errored = toFlowInteractions([
        forestInteraction({
          legs: [leg({ leg_type: 'request', error: true }), leg({ leg_type: 'response', error: null })],
        }),
      ]);
      expect(errored[0].any_error).toBe(true);

      const notErrored = toFlowInteractions([
        forestInteraction({ legs: [leg({ leg_type: 'request', error: false })] }),
      ]);
      expect(notErrored[0].any_error).toBe(false);
    });

    it('passes a null participant straight through', () => {
      const forest = [forestInteraction({ caller_entity_id: null })];
      const flow = toFlowInteractions(forest);
      expect(flow[0].caller_entity_id).toBeNull();
      expect(flow[0].callee_entity_id).toBe('e2');
    });

    it('carries id, parent_interaction_id, summary, span_count, anchor_count through unchanged', () => {
      const forest = [
        forestInteraction({
          interaction_id: 'ix9',
          parent_interaction_id: 'ix0',
          summary: 'hello',
          span_count: 3,
          anchor_count: 2,
        }),
      ];
      const flow = toFlowInteractions(forest);
      expect(flow[0]).toMatchObject({
        id: 'ix9',
        parent_interaction_id: 'ix0',
        summary: 'hello',
        span_count: 3,
        anchor_count: 2,
      });
    });
  });

  describe('toFlowEntities', () => {
    it('prefers real entity rows when present', () => {
      const forest = [forestInteraction()];
      const entities = [flowEntity({ id: 'e1' }), flowEntity({ id: 'e2', kind: 'tool', display_name: 'Search' })];
      const result = toFlowEntities(forest, entities);
      expect(result).toEqual(expect.arrayContaining(entities));
      expect(result).toHaveLength(2);
    });

    it('synthesizes kind: unknown for ids useEntities did not return', () => {
      const forest = [forestInteraction({ caller_entity_id: 'e1', callee_entity_id: 'e2' })];
      const entities = [flowEntity({ id: 'e1' })]; // e2 missing
      const result = toFlowEntities(forest, entities);
      const synthesized = result.find((e) => e.id === 'e2');
      expect(synthesized).toBeDefined();
      expect(synthesized?.kind).toBe('unknown');
      expect(synthesized?.display_name).toBe('e2');
    });

    it('synthesizes every entity when entities is undefined', () => {
      const forest = [forestInteraction({ caller_entity_id: 'e1', callee_entity_id: 'e2' })];
      const result = toFlowEntities(forest, undefined);
      expect(result.map((e) => e.id).sort()).toEqual(['e1', 'e2']);
      expect(result.every((e) => e.kind === 'unknown')).toBe(true);
    });

    it('does not synthesize an entity for a null participant id', () => {
      const forest = [forestInteraction({ caller_entity_id: null, callee_entity_id: 'e2' })];
      const result = toFlowEntities(forest, undefined);
      expect(result.map((e) => e.id)).toEqual(['e2']);
    });
  });

  describe('unresolvedEntityIds', () => {
    it('lists ids the forest references that useEntities did not resolve', () => {
      const forest = [forestInteraction({ caller_entity_id: 'e1', callee_entity_id: 'e2' })];
      const entities = [flowEntity({ id: 'e1' })];
      expect(unresolvedEntityIds(forest, entities)).toEqual(['e2']);
    });

    it('is empty when entities resolves everything', () => {
      const forest = [forestInteraction({ caller_entity_id: 'e1', callee_entity_id: 'e2' })];
      const entities = [flowEntity({ id: 'e1' }), flowEntity({ id: 'e2' })];
      expect(unresolvedEntityIds(forest, entities)).toEqual([]);
    });

    it('lists every referenced id when entities is undefined', () => {
      const forest = [forestInteraction({ caller_entity_id: 'e1', callee_entity_id: 'e2' })];
      expect(unresolvedEntityIds(forest, undefined).sort()).toEqual(['e1', 'e2']);
    });
  });

  describe('riskLevelByInteraction', () => {
    it('maps interaction id to risk_level when risk is present', () => {
      const forest = [
        forestInteraction({ interaction_id: 'ix1', risk: risk({ risk_level: 'critical' }) }),
        forestInteraction({ interaction_id: 'ix2', risk: risk({ risk_level: 'low' }) }),
      ];
      const map = riskLevelByInteraction(forest);
      expect(map.get('ix1')).toBe('critical');
      expect(map.get('ix2')).toBe('low');
    });

    it('omits interactions whose risk is null (not yet computed)', () => {
      const forest = [forestInteraction({ interaction_id: 'ix1', risk: null })];
      const map = riskLevelByInteraction(forest);
      expect(map.has('ix1')).toBe(false);
    });
  });

  describe('violationsOf', () => {
    it('filters to interactions with a non-empty triggered_rule_ids, preserving forest order', () => {
      const forest = [
        forestInteraction({ interaction_id: 'ix1', risk: risk({ triggered_rule_ids: ['DG-001'] }) }),
        forestInteraction({ interaction_id: 'ix2', risk: null }),
        forestInteraction({ interaction_id: 'ix3', risk: risk({ triggered_rule_ids: [] }) }),
        forestInteraction({ interaction_id: 'ix4', risk: risk({ triggered_rule_ids: ['DG-002'] }) }),
      ];
      const violations = violationsOf(forest);
      expect(violations.map((v) => v.interaction_id)).toEqual(['ix1', 'ix4']);
    });

    it('excludes risk: null', () => {
      const forest = [forestInteraction({ risk: null })];
      expect(violationsOf(forest)).toEqual([]);
    });

    it('excludes an empty triggered_rule_ids', () => {
      const forest = [forestInteraction({ risk: risk({ triggered_rule_ids: [] }) })];
      expect(violationsOf(forest)).toEqual([]);
    });

    it('returns an empty array when there are no interactions', () => {
      expect(violationsOf([])).toEqual([]);
    });
  });

  describe('adapter output feeds the existing derivation (integration)', () => {
    it('deriveGraph accepts the widened output: one request-only edge per call, in forest order, null-participant dropped', () => {
      const forest = [
        forestInteraction({
          interaction_id: 'ix1',
          caller_entity_id: 'e1',
          callee_entity_id: 'e2',
          legs: [
            leg({ leg_type: 'request', occurred_at: '2026-01-01T00:00:00Z' }),
            leg({ leg_type: 'response', occurred_at: '2026-01-01T00:00:01Z' }),
          ],
        }),
        forestInteraction({
          interaction_id: 'ix2',
          caller_entity_id: null,
          callee_entity_id: 'e2',
          legs: [leg({ leg_type: 'request', occurred_at: '2026-01-01T00:00:02Z' })],
        }),
      ];
      const flowInteractions = toFlowInteractions(forest);
      const flowEntities = toFlowEntities(forest, [flowEntity({ id: 'e1' }), flowEntity({ id: 'e2' })]);

      const graph = deriveGraph(flowEntities, flowInteractions);
      // ix1 contributes exactly one edge (its request leg only) — no response
      // arrow doubling it back, even though the forest interaction has a
      // response leg.
      expect(graph.edges.map((e) => e.seq)).toEqual([1]);
      expect(graph.edges[0]).toMatchObject({ source: 'e1', target: 'e2', legType: 'request' });
      // ix2 has a null caller -> dropped, not drawn.
      expect(graph.dropped.map((d) => d.id)).toEqual(['ix2']);
    });
  });
});
