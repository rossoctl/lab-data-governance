import { describe, it, expect } from 'vitest';
import { QueryClient } from '@tanstack/react-query';
import { RISK_QUERY_KEY_ROOT, RISK_PAGE_SIZE, riskQueryKey, riskTraceKeys } from './hooks';

// Scaffold only, no hooks — issue #165 explicitly forbids pre-building hooks
// for endpoints no issue yet consumes. This pins the query-key convention
// #166/#167/#168 will all build hooks against, so the risk cache subtree
// stays invalidatable as a whole (`queryClient.invalidateQueries({queryKey:
// RISK_QUERY_KEY_ROOT})`) rather than each issue picking its own root.

describe('risk-api hooks scaffold', () => {
  it('namespaces every key under the shared root', () => {
    expect(riskQueryKey('traces')).toEqual([...RISK_QUERY_KEY_ROOT, 'traces']);
  });

  it('appends every extra segment given', () => {
    expect(riskQueryKey('traces', 'abc123')).toEqual([...RISK_QUERY_KEY_ROOT, 'traces', 'abc123']);
    expect(riskQueryKey('rules', 'r1', 'history')).toEqual([
      ...RISK_QUERY_KEY_ROOT,
      'rules',
      'r1',
      'history',
    ]);
  });

  it('gives differing params different keys', () => {
    const a = riskQueryKey('traces', { window: '24h' });
    const b = riskQueryKey('traces', { window: '7d' });
    expect(a).not.toEqual(b);
  });

  it('is stable for identical inputs', () => {
    expect(riskQueryKey('traces', 'abc')).toEqual(riskQueryKey('traces', 'abc'));
  });

  it('pins the shared dashboard page size to a compact-card limit', () => {
    // Deliberate UI-side override, smaller than the server's own
    // API_RISK_{RULES,INTERACTIONS,TRACES}_DEFAULT_LIMIT (50) — the
    // dashboard's cards are compact summaries, not full listings.
    expect(RISK_PAGE_SIZE).toBe(10);
  });

  it('exposes the root as a readonly tuple so a caller can spread it into a queryKey', () => {
    expect(RISK_QUERY_KEY_ROOT).toEqual(['risk']);
  });

  it("keeps a trace's detail key disjoint from the traces list key (issue #170)", () => {
    // useTraceRiskDetail('t1') vs useRiskTracesInfinite({window}) both nest
    // under riskQueryKey('traces', ...) — the 'detail' tail is what stops
    // them from colliding in react-query's cache.
    const detailKey = riskQueryKey('traces', 't1', 'detail');
    const listKey = riskQueryKey('traces', { window: '24h' });
    expect(detailKey).not.toEqual(listKey);
  });
});

describe('riskTraceKeys', () => {
  it("covers one trace's risk detail and alerts, and nothing else in the risk cache", async () => {
    // Matched the way the live stream invalidates them (prefix, partial objects),
    // against the keys the hooks really use. The dashboard's list, another
    // trace's reads and the rule catalog must stay untouched by a change to t1.
    const client = new QueryClient();
    const keys = {
      detail: riskQueryKey('traces', 't1', 'detail'),
      alerts: riskQueryKey('alerts', { trace_id: 't1' }),
      list: riskQueryKey('traces', { window: '24h' }),
      otherDetail: riskQueryKey('traces', 't2', 'detail'),
      otherAlerts: riskQueryKey('alerts', { trace_id: 't2' }),
      catalog: riskQueryKey('rules', 'catalog-index'),
    };
    for (const key of Object.values(keys)) client.setQueryData(key, {});
    for (const queryKey of riskTraceKeys('t1')) {
      await client.invalidateQueries({ queryKey, refetchType: 'none' });
    }
    const invalidated = Object.entries(keys)
      .filter(([, key]) => client.getQueryState(key)?.isInvalidated)
      .map(([name]) => name);
    expect(invalidated).toEqual(['detail', 'alerts']);
  });
});
