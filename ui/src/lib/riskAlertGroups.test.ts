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

  // Issue #214: only traces with a non-none risk level belong in the alerts table.

  it('drops traces whose current risk level is none', () => {
    const groups = groupTraceRisksByWorkflow([
      record({ trace_id: 't-none', trace_risk_level: 'none' }),
      record({ trace_id: 't-low', trace_risk_level: 'low' }),
    ]);
    expect(groups.map((g) => g.traceId)).toEqual(['t-low']);
  });

  it('keeps every risk level from low upwards', () => {
    const groups = groupTraceRisksByWorkflow([
      record({ trace_id: 't-critical', trace_risk_level: 'critical' }),
      record({ trace_id: 't-high', trace_risk_level: 'high' }),
      record({ trace_id: 't-medium', trace_risk_level: 'medium' }),
      record({ trace_id: 't-low', trace_risk_level: 'low' }),
    ]);
    expect(groups.map((g) => g.traceId)).toEqual([
      't-critical',
      't-high',
      't-medium',
      't-low',
    ]);
  });

  it('returns an empty array when every trace is none-risk', () => {
    expect(
      groupTraceRisksByWorkflow([
        record({ trace_id: 't-1', trace_risk_level: 'none' }),
        record({ trace_id: 't-2', trace_risk_level: 'none' }),
      ]),
    ).toEqual([]);
  });

  it('judges none-ness by the current version, dropping a trace downgraded to none', () => {
    // An older version carried real risk, but the latest recompute cleared it:
    // the trace is no longer an alert.
    const groups = groupTraceRisksByWorkflow([
      record({ trace_id: 't-1', version: 1, trace_risk_level: 'critical' }),
      record({ trace_id: 't-1', version: 2, trace_risk_level: 'none' }),
    ]);
    expect(groups).toEqual([]);
  });

  it('judges none-ness by the current version, keeping a trace upgraded off none', () => {
    const groups = groupTraceRisksByWorkflow([
      record({ trace_id: 't-1', version: 1, trace_risk_level: 'none' }),
      record({ trace_id: 't-1', version: 2, trace_risk_level: 'medium' }),
    ]);
    expect(groups).toHaveLength(1);
    expect(groups[0].summary.trace_risk_level).toBe('medium');
    // Filtering removes whole groups, never rows within a kept group's history.
    expect(groups[0].rows).toHaveLength(2);
  });

  it('keeps a trace whose risk level is unrecognised rather than silently hiding it', () => {
    // An OPA-sourced level this UI does not know about is not "no risk" —
    // dropping it would hide a real alert (risk_level is TEXT, not an enum).
    const groups = groupTraceRisksByWorkflow([
      record({ trace_id: 't-weird', trace_risk_level: 'catastrophic' }),
    ]);
    expect(groups.map((g) => g.traceId)).toEqual(['t-weird']);
  });

  it('is case-insensitive when recognising none', () => {
    expect(
      groupTraceRisksByWorkflow([record({ trace_id: 't-1', trace_risk_level: 'NONE' })]),
    ).toEqual([]);
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
