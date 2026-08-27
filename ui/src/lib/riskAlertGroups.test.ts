import { describe, it, expect } from 'vitest';
import { groupTraceRisksByWorkflow } from './riskAlertGroups';
import type { TraceRiskRecord } from '../risk-api/types';

function record(overrides: Partial<TraceRiskRecord>): TraceRiskRecord {
  return {
    trace_risk_id: 'tr-1',
    trace_id: 't-1',
    version: 1,
    computed_at: '2026-08-01T00:00:00Z',
    trace_risk_level: 'high',
    trace_enforcement_type: 'block',
    risk_compounding_mode: 'max',
    enforcement_aggregation_mode: 'max',
    interaction_count: 1,
    policy_event_count: 1,
    all_entity_ids: [],
    triggered_rule_ids: [],
    overall_confidence: 0.9,
    contributing_interaction_risk_ids: [],
    ...overrides,
  };
}

describe('groupTraceRisksByWorkflow', () => {
  it('returns an empty array for an empty list', () => {
    expect(groupTraceRisksByWorkflow([])).toEqual([]);
  });

  it('groups a single record into a single group', () => {
    const groups = groupTraceRisksByWorkflow([record({ trace_id: 't-1' })]);
    expect(groups).toHaveLength(1);
    expect(groups[0].traceId).toBe('t-1');
    expect(groups[0].rows).toHaveLength(1);
  });

  it('keeps multiple distinct traces as separate groups', () => {
    const groups = groupTraceRisksByWorkflow([
      record({ trace_id: 't-1', trace_risk_level: 'high' }),
      record({ trace_id: 't-2', trace_risk_level: 'critical' }),
    ]);
    expect(groups).toHaveLength(2);
    expect(new Set(groups.map((g) => g.traceId))).toEqual(new Set(['t-1', 't-2']));
  });

  it('collapses several versions of one trace_id into one group summarized by the highest version', () => {
    const groups = groupTraceRisksByWorkflow([
      record({ trace_id: 't-1', version: 1, trace_risk_level: 'low' }),
      record({ trace_id: 't-1', version: 3, trace_risk_level: 'critical' }),
      record({ trace_id: 't-1', version: 2, trace_risk_level: 'medium' }),
    ]);
    expect(groups).toHaveLength(1);
    expect(groups[0].summary.version).toBe(3);
    expect(groups[0].summary.trace_risk_level).toBe('critical');
    expect(groups[0].rows).toHaveLength(3);
  });

  it('orders groups by risk severity (critical first), then recency', () => {
    const groups = groupTraceRisksByWorkflow([
      record({ trace_id: 't-low', trace_risk_level: 'low', computed_at: '2026-08-01T00:00:00Z' }),
      record({
        trace_id: 't-critical-older',
        trace_risk_level: 'critical',
        computed_at: '2026-08-01T00:00:00Z',
      }),
      record({
        trace_id: 't-critical-newer',
        trace_risk_level: 'critical',
        computed_at: '2026-08-02T00:00:00Z',
      }),
    ]);
    expect(groups.map((g) => g.traceId)).toEqual([
      't-critical-newer',
      't-critical-older',
      't-low',
    ]);
  });

  it('is deterministic for identical inputs', () => {
    const input = [
      record({ trace_id: 't-1', trace_risk_level: 'high' }),
      record({ trace_id: 't-2', trace_risk_level: 'high' }),
    ];
    expect(groupTraceRisksByWorkflow(input).map((g) => g.traceId)).toEqual(
      groupTraceRisksByWorkflow(input).map((g) => g.traceId),
    );
  });
});
