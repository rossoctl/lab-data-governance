import { describe, it, expect } from 'vitest';
import { buildParentIndex, descendantErrorAncestors, spanKey } from './traceTree';
import type { TreeSpan } from './traceTree';

// Ported from the retired Node-driven trace_tree_logic.js tests
// (test_trace_tree_view.py). The descendant-error badge walks up from every
// loaded error span, marking loaded ancestors; error spans inside a collapsed
// (not-yet-loaded) subtree contribute nothing — the v1 limitation.

const span = (
  span_id: string,
  parent_id: string | null,
  error: boolean | null = null,
): TreeSpan => ({ trace_id: 'T', span_id, parent_id, error });

describe('buildParentIndex', () => {
  it('maps (trace_id|span_id) -> parent_id', () => {
    const idx = buildParentIndex([span('root', null), span('child', 'root')]);
    expect(idx['T|root']).toBeNull();
    expect(idx['T|child']).toBe('root');
  });
});

describe('descendantErrorAncestors', () => {
  it('flags a loaded ancestor of an error span', () => {
    // root -> server-handler(error). root is server-handler's loaded ancestor.
    const spans = [span('root', null), span('server-handler', 'root', true)];
    const flagged = descendantErrorAncestors(spans, buildParentIndex(spans));
    expect(flagged.has(spanKey('T', 'root'))).toBe(true);
    // The error span itself never carries the descendant badge.
    expect(flagged.has(spanKey('T', 'server-handler'))).toBe(false);
  });

  it('spreads up the chain as deeper levels load', () => {
    // root -> http-call -> db-query(error). Both root and http-call flagged.
    const spans = [
      span('root', null),
      span('http-call', 'root'),
      span('db-query', 'http-call', true),
    ];
    const flagged = descendantErrorAncestors(spans, buildParentIndex(spans));
    expect(flagged.has(spanKey('T', 'root'))).toBe(true);
    expect(flagged.has(spanKey('T', 'http-call'))).toBe(true);
    expect(flagged.has(spanKey('T', 'db-query'))).toBe(false);
  });

  it('does NOT propagate through a collapsed subtree (v1 limitation)', () => {
    // root -> middle loaded; hidden-error under middle NOT loaded. Neither
    // root nor middle is flagged — no path from any loaded error span.
    const spans = [span('root', null), span('middle', 'root')];
    const flagged = descendantErrorAncestors(spans, buildParentIndex(spans));
    expect(flagged.has(spanKey('T', 'root'))).toBe(false);
    expect(flagged.has(spanKey('T', 'middle'))).toBe(false);
  });
});
