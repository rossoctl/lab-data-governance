import { describe, it, expect } from 'vitest';
import { navSectionFor } from './navSection';

describe('navSectionFor', () => {
  it('treats /risk exactly as the risk section', () => {
    expect(navSectionFor('/risk')).toBe('risk');
  });

  it('treats any /risk/... path as the risk section', () => {
    expect(navSectionFor('/risk/traces/abc')).toBe('risk');
    expect(navSectionFor('/risk/rules')).toBe('risk');
  });

  it('treats /traces/... as the traces section', () => {
    expect(navSectionFor('/traces')).toBe('traces');
    expect(navSectionFor('/traces/abc/spans')).toBe('traces');
  });

  it('does not treat a path merely prefixed by "risk" as the risk section', () => {
    // startsWith('/risk') would wrongly match this.
    expect(navSectionFor('/risky-business')).toBe('traces');
  });

  it('defaults unmatched paths to traces', () => {
    expect(navSectionFor('/')).toBe('traces');
    expect(navSectionFor('/nope')).toBe('traces');
  });
});
