import { describe, it, expect } from 'vitest';
import {
  deriveLabelTrackHighlight,
  namedByRules,
  toGraphHighlight,
  trackKey,
  trackableValues,
  trackedValueOf,
} from './labelTrack';
import type { Interaction, LegLabel, LegLabels, LineageHop } from '../types';
import type { RuleListItem } from '../risk-api/types';

const T = 't1';
const hop = (seq: number, role: LineageHop['role'], iid: string, leg: 'request' | 'response' = 'request', trace = T): LineageHop => ({
  interaction_id: iid, leg_type: leg, seq, trace_id: trace, caller: 'a', callee: 'b', text: 'v', start: 0, end: 1, role, excerpt: null,
});
const origin = (chains: LineageHop[][] | undefined) => ({
  origin_interaction_id: 'o', origin_leg_type: 'response' as const, origin_seq: 1, caller: 'a', callee: 'b',
  unit_text: 'v', unit_value: 'v', match_kind: 'whole', origin_trace_id: null, chains,
});
const label = (over: Partial<LegLabel> = {}): LegLabel => ({
  text: 'BK-1', start: 16, end: 25, entity_type: 'PID', sensitivity_level: 'RESTRICTED', regulatory_tags: ['PII'],
  found_here: false, carried_in: [origin([[hop(1, 'origin', 'o', 'response'), hop(5, 'hop', 'p'), hop(9, 'here', 'x')]])],
  ...over,
});
const leg = (labels: LegLabel[], legType: 'request' | 'response' = 'request'): LegLabels => ({
  leg_type: legType, payload_hash: 'h', seq: 9, found: null, carried: null, labels,
});
const ix = (id: string, caller: string, callee: string): Interaction => ({
  id, caller_entity_id: caller, callee_entity_id: callee, summary: id, parent_interaction_id: null,
  legs: [], duration_seconds: null, any_error: false, span_count: 1, anchor_count: 1,
});

describe('labelTrack', () => {
  it('keys a value by its leg, type and span, so the same text twice on a payload is two values', () => {
    expect(trackKey('x', 'request', label())).toBe('x/request/PID/16-25/BK-1');
    expect(trackKey('x', 'request', label({ start: null, end: null }))).toBe('x/request/PID/x-x/BK-1');
  });

  it('tracks a value only when some origin carries chains; found-here-only and chainless origins are nothing to track', () => {
    expect(trackedValueOf('x', leg([]), label({ carried_in: [] }))).toBeNull();
    expect(trackedValueOf('x', leg([]), label({ carried_in: [origin(undefined)] }))).toBeNull();
    expect(trackedValueOf('x', leg([]), label({ carried_in: [origin([])] }))).toBeNull();
    const v = trackedValueOf('x', leg([]), label())!;
    expect([v.interactionId, v.legType, v.text, v.entityType, v.chains.length]).toEqual(['x', 'request', 'BK-1', 'PID', 1]);
    expect(trackableValues('x', [leg([label(), label({ carried_in: [] })]), leg([label({ entity_type: 'OPID' })], 'response')]).map((v) => v.key))
      .toEqual(['x/request/PID/16-25/BK-1', 'x/response/OPID/16-25/BK-1']);
  });

  it('lights the union of every chain of every tracked value, counts hops in other traces, and names the nodes at the ends', () => {
    const a = trackedValueOf('x', leg([]), label())!;
    const b = trackedValueOf('x', leg([]), label({
      entity_type: 'DATE',
      carried_in: [origin([[hop(2, 'origin', 'q', 'request', 'other-trace'), hop(5, 'hop', 'p'), hop(9, 'here', 'x')],
        [hop(2, 'origin', 'q', 'request', 'other-trace'), hop(3, 'hop', 'r', 'response'), hop(9, 'here', 'x')]])],
    }))!;
    const h = deriveLabelTrackHighlight([a, b], [ix('o', 'e1', 'e2'), ix('p', 'e2', 'e3'), ix('r', 'e4', 'e5'), ix('x', 'e3', 'e6'), ix('z', 'e7', 'e8')], T);
    expect(h.edgeIds).toEqual(['o:response', 'p:request', 'r:response', 'x:request']);
    expect(h.interactionIds).toEqual(['o', 'p', 'r', 'x']);
    expect(h.nodeIds).toEqual(['e1', 'e2', 'e3', 'e4', 'e5', 'e6']);
    expect(h.foreignHops).toBe(2);
    const g = toGraphHighlight(h);
    expect(g).toEqual({ selectedNodeId: null, nodeIds: h.nodeIds, edgeIds: h.edgeIds, isQuestion: true });
  });

  it('names a value by a rule\'s tag or level criteria, and by nothing when a rule has no data-item criteria', () => {
    const rule = (data_items: RuleListItem['data_items']): RuleListItem => ({
      rule_id: 'R', rule_name: null, categories: [], risk_level: null, enforcement: null, explanation: null,
      event_type: null, data_items, data_destinations: [], allowed_actions: [], rule_sources: [],
    });
    const pid = label();
    const date = label({ entity_type: 'DATE', sensitivity_level: 'INTERNAL', regulatory_tags: ['PI'] });
    expect(namedByRules(pid, [rule([{ regulatory_tags: ['PII'] }])])).toBe(true);
    expect(namedByRules(date, [rule([{ regulatory_tags: ['PII'] }])])).toBe(false);
    expect(namedByRules(pid, [rule([{ classification_level: 'RESTRICTED' }])])).toBe(true);
    expect(namedByRules(date, [rule([{ classification_level: 'RESTRICTED' }])])).toBe(false);
    expect(namedByRules(pid, [rule([])])).toBe(false);
    expect(namedByRules(pid, [])).toBe(false);
  });
});
