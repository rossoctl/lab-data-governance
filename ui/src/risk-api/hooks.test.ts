import { describe, it, expect } from 'vitest';
import { RISK_QUERY_KEY_ROOT, RISK_PAGE_SIZE, riskQueryKey } from './hooks';

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

  it('pins the shared page size to the server default', () => {
    // data_governance/risk/config.py's API_RISK_{RULES,INTERACTIONS,TRACES}_
    // DEFAULT_LIMIT are uniformly 50.
    expect(RISK_PAGE_SIZE).toBe(50);
  });

  it('exposes the root as a readonly tuple so a caller can spread it into a queryKey', () => {
    expect(RISK_QUERY_KEY_ROOT).toEqual(['risk']);
  });
});
