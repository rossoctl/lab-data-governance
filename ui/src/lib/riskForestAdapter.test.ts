import { describe, it, expect } from 'vitest';
import {
  riskLevelByInteraction,
  classificationByInteraction,
  violationsOf,
} from './riskForestAdapter';
import type { ForestInteraction, InteractionRisk } from '../risk-api/types';

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

describe('riskForestAdapter', () => {
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

  // Mirrors `riskLevelByInteraction` immediately above but for classification
  // data (issue #170 follow-up: regulatory tags as edge labels). The
  // wire-shape narrowing itself (pending/payload-less/absent legs, non-string
  // members, etc.) is covered directly in `classificationSummary.test.ts` —
  // these tests cover only what this adapter layer adds: the map-presence
  // rule and interaction-id keying.
  describe('classificationByInteraction', () => {
    it('maps interaction id to its tags and levels when classified', () => {
      const forest = [
        forestInteraction({
          interaction_id: 'ix1',
          risk: risk({
            classification_summary: {
              request: { sensitivity_level: 'RESTRICTED', regulatory_tags: ['PII', 'GDPR'] },
            },
          }),
        }),
      ];
      const map = classificationByInteraction(forest);
      expect(map.get('ix1')).toEqual({ tags: ['PII', 'GDPR'], levels: ['RESTRICTED'] });
    });

    it('unions tags and levels across both legs of the same interaction', () => {
      const forest = [
        forestInteraction({
          interaction_id: 'ix1',
          risk: risk({
            classification_summary: {
              request: { sensitivity_level: 'INTERNAL', regulatory_tags: ['PII'] },
              response: { sensitivity_level: 'RESTRICTED', regulatory_tags: ['GDPR'] },
            },
          }),
        }),
      ];
      const map = classificationByInteraction(forest);
      expect(map.get('ix1')).toEqual({ tags: ['PII', 'GDPR'], levels: ['INTERNAL', 'RESTRICTED'] });
    });

    it('is present for a classified verdict with a level but no regulatory tags', () => {
      // A genuine PUBLIC-with-no-tags verdict must still be visible to the
      // graph's hover text — see `classificationByInteraction`'s docstring
      // for why this is "present when EITHER is non-empty", not "present
      // only when tags exist".
      const forest = [
        forestInteraction({
          interaction_id: 'ix1',
          risk: risk({
            classification_summary: { request: { sensitivity_level: 'PUBLIC', regulatory_tags: [] } },
          }),
        }),
      ];
      const map = classificationByInteraction(forest);
      expect(map.get('ix1')).toEqual({ tags: [], levels: ['PUBLIC'] });
    });

    it('omits an interaction whose classification_summary is null', () => {
      const forest = [
        forestInteraction({ interaction_id: 'ix1', risk: risk({ classification_summary: null }) }),
      ];
      const map = classificationByInteraction(forest);
      expect(map.has('ix1')).toBe(false);
    });

    it('omits an interaction whose risk is null (not yet computed)', () => {
      const forest = [forestInteraction({ interaction_id: 'ix1', risk: null })];
      const map = classificationByInteraction(forest);
      expect(map.has('ix1')).toBe(false);
    });

    it('omits an interaction whose legs are only pending/payload-less', () => {
      const forest = [
        forestInteraction({
          interaction_id: 'ix1',
          risk: risk({
            classification_summary: {
              request: { classification_pending: true },
              response: { payload: null },
            },
          }),
        }),
      ];
      const map = classificationByInteraction(forest);
      expect(map.has('ix1')).toBe(false);
    });

    it('keys strictly by interaction_id across a mixed forest', () => {
      const forest = [
        forestInteraction({
          interaction_id: 'ix1',
          risk: risk({
            classification_summary: { request: { sensitivity_level: 'RESTRICTED', regulatory_tags: ['PII'] } },
          }),
        }),
        forestInteraction({ interaction_id: 'ix2', risk: risk({ classification_summary: null }) }),
        forestInteraction({ interaction_id: 'ix3', risk: null }),
      ];
      const map = classificationByInteraction(forest);
      expect(map.size).toBe(1);
      expect(map.get('ix1')).toEqual({ tags: ['PII'], levels: ['RESTRICTED'] });
      expect(map.has('ix2')).toBe(false);
      expect(map.has('ix3')).toBe(false);
    });

    // Label lineage's `inherited` block: the classifier's own tags and level
    // stay as they were; what was carried in rides beside them as `carried`.
    it('adds carried when a leg has inherited labels, leaving tags and levels the classifier\'s', () => {
      const inherited = { count: 5, kinds: ['whole'], max_level: 'RESTRICTED', recombined: false, regulatory_tags: ['PI', 'PII'] };
      const forest = [
        forestInteraction({
          interaction_id: 'ix1',
          risk: risk({
            classification_summary: {
              request: { sensitivity_level: 'INTERNAL', regulatory_tags: ['PI'], inherited },
              response: { sensitivity_level: 'INTERNAL', regulatory_tags: ['PI'], inherited },
            },
          }),
        }),
      ];
      const map = classificationByInteraction(forest);
      expect(map.get('ix1')).toEqual({
        tags: ['PI'],
        levels: ['INTERNAL'],
        carried: { tags: ['PI', 'PII'], level: 'RESTRICTED' },
      });
    });

    it('makes an entry for an interaction whose only content is carried in (pending/payload-less legs)', () => {
      const forest = [
        forestInteraction({
          interaction_id: 'ix1',
          risk: risk({
            classification_summary: {
              request: { classification_pending: true, inherited: { regulatory_tags: ['PII'], max_level: 'RESTRICTED' } },
              response: { payload: null },
            },
          }),
        }),
      ];
      const map = classificationByInteraction(forest);
      expect(map.get('ix1')).toEqual({ tags: [], levels: [], carried: { tags: ['PII'], level: 'RESTRICTED' } });
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
});
