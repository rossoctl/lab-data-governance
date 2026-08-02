import { describe, it, expect } from 'vitest';
import {
  toolSubtype,
  computeInteractionDepths,
  durationMs,
  roleMeta,
  legDirection,
  parseLegViewKey,
  parseLineageSource,
} from './flow';
import type { Entity, Interaction } from './flow';

// Ported from execution_flow_logic.js: tool subtype-by-natural-key-shape, the
// parent-walk depth computation (order-independent, cycle-guarded), and the
// interaction/span duration formula.

const entity = (kind: string, natural_key: string): Entity =>
  ({ id: 'e', kind, natural_key, display_name: natural_key, detected_from: '' });

/**
 * The per-leg direction rule (ADR-0025), shared by the Flat table's
 * Caller/Callee columns and the Execution Flow graph's edge direction. Tested here
 * rather than only through either consumer because it is the single statement of
 * the rule both depend on: when it was written twice, a response row could read
 * `A → B` in the table while the graph drew `B → A` for the same leg.
 */
/**
 * The `?legs` coercion. Tested here, at the single definition, rather than only
 * through TraceDetailPage's URL round-trips: the page's read and the tab bar's
 * `onSelect` both call this, so a value one accepts and the other does not would be
 * a tab that activates but never survives a reload.
 */
describe('parseLegViewKey', () => {
  it('accepts every non-default presentation verbatim', () => {
    expect(parseLegViewKey('flat')).toBe('flat');
    expect(parseLegViewKey('diagram')).toBe('diagram');
    expect(parseLegViewKey('graph')).toBe('graph');
    expect(parseLegViewKey('lineage')).toBe('lineage');
  });

  it('reads absent, empty and unrecognised values as the default tree', () => {
    // Coerces rather than throwing, matching parseWindowKey's treatment of
    // `?window`. `diagra`/`Diagram` and `lineag`/`Lineage` specifically: adding a
    // value to the union must not make a typo or the wrong case resolve to anything.
    expect(parseLegViewKey(null)).toBe('tree');
    expect(parseLegViewKey(undefined)).toBe('tree');
    expect(parseLegViewKey('')).toBe('tree');
    expect(parseLegViewKey('tree')).toBe('tree');
    expect(parseLegViewKey('diagra')).toBe('tree');
    expect(parseLegViewKey('Diagram')).toBe('tree');
    expect(parseLegViewKey('lineag')).toBe('tree');
    expect(parseLegViewKey('Lineage')).toBe('tree');
    // Not the neighbouring word either: `lineage` is a tab, `data-lineage` is the
    // resource it reads, and the two must not be interchangeable in a URL.
    expect(parseLegViewKey('data-lineage')).toBe('tree');
  });
});

/**
 * The `?src` coercion — the Lineage tab's traced data source.
 *
 * Tested at the single definition for the same reason `parseLegViewKey` is: the page
 * reads the param and the tab's picker writes it, so a value one accepts and the other
 * does not would be a choice that applies and then vanishes on reload.
 *
 * Note what is deliberately NOT tested here, because it is not this function's job: a
 * source that no longer exists in the TRACE. This function has no access to the trace's
 * roll-up, so the semantic check lives in `lineageReachability.resolveSourceChoice`
 * (which reports it as its own `'stale'` state). Splitting them is what keeps a bad
 * `?src` behaving like a bad `?legs` — coerced, never thrown, never sent to the server.
 */
describe('parseLineageSource', () => {
  it('passes a natural key through verbatim, including its punctuation', () => {
    // A qualified key carries colons, parentheses and commas by design; none of them
    // may be normalised away, because the key IS the source's identity on the wire.
    expect(parseLineageSource('agent:(prod,travel-advisor)')).toBe('agent:(prod,travel-advisor)');
    expect(
      parseLineageSource('tool:agent:(travel_advisor,travel-advisor):search_destinations'),
    ).toBe('tool:agent:(travel_advisor,travel-advisor):search_destinations');
  });

  it('reads absent, empty and whitespace-only values as no choice at all', () => {
    // `?src=` must not become a request for a source NAMED empty string: the parameter
    // is required by the server, so an empty one is a 400 rather than a wildcard.
    expect(parseLineageSource(null)).toBeNull();
    expect(parseLineageSource(undefined)).toBeNull();
    expect(parseLineageSource('')).toBeNull();
    expect(parseLineageSource('   ')).toBeNull();
  });

  it('trims surrounding whitespace a hand-edited or wrapped URL can introduce', () => {
    expect(parseLineageSource('  agent:(p,a)  ')).toBe('agent:(p,a)');
  });
});

describe('legDirection', () => {
  const ix = { caller_entity_id: 'A', callee_entity_id: 'B' };

  it('leaves a request leg as caller → callee', () => {
    expect(legDirection(ix, { leg_type: 'request' })).toEqual({ from: 'A', to: 'B' });
  });

  it('SWAPS a response leg to callee → caller', () => {
    // The response travels back to whoever asked; this swap is the whole reason
    // one interaction draws two opposite arrows.
    expect(legDirection(ix, { leg_type: 'response' })).toEqual({ from: 'B', to: 'A' });
  });

  it('passes null ids straight through, on either leg', () => {
    // An unresolved participant stays unresolved whichever end of the leg it is
    // on; the callers decide what to do (blank cell / dropped edge).
    const half = { caller_entity_id: 'A', callee_entity_id: null };
    expect(legDirection(half, { leg_type: 'request' })).toEqual({ from: 'A', to: null });
    expect(legDirection(half, { leg_type: 'response' })).toEqual({ from: null, to: 'A' });
  });

  it('is a no-op for a self-call, so both its legs are self-edges', () => {
    const self = { caller_entity_id: 'A', callee_entity_id: 'A' };
    expect(legDirection(self, { leg_type: 'request' })).toEqual({ from: 'A', to: 'A' });
    expect(legDirection(self, { leg_type: 'response' })).toEqual({ from: 'A', to: 'A' });
  });
});

describe('toolSubtype', () => {
  it('reads deployed vs in-framework from the natural-key shape', () => {
    // Deployed tool = its own MCP service: exactly tool:(<project>,<service>).
    expect(toolSubtype(entity('tool', 'tool:(proj,svc)'))).toBe('deployed');
    // In-framework tool hosted by an agent: trailing :<name> segment.
    expect(toolSubtype(entity('tool', 'tool:agent:(a,b):search'))).toBe('in-framework');
    // tool:(unknown):name is in-framework, NOT deployed (trailing :name).
    expect(toolSubtype(entity('tool', 'tool:(unknown):search'))).toBe('in-framework');
    // Non-tool entities have no subtype.
    expect(toolSubtype(entity('agent', 'agent:(a,b)'))).toBeNull();
  });
});

describe('computeInteractionDepths', () => {
  it('resolves depth by walking parent links, independent of row order', () => {
    // child sorts BEFORE its parent (parent has a later/na started_at) — a
    // forward pass keyed on "parent already seen" would misplace it at 0.
    const ix: Interaction[] = [
      { id: 'child', parent_interaction_id: 'parent' } as Interaction,
      { id: 'parent', parent_interaction_id: null } as Interaction,
      { id: 'grandchild', parent_interaction_id: 'child' } as Interaction,
    ];
    const depth = computeInteractionDepths(ix);
    expect(depth.get('parent')).toBe(0);
    expect(depth.get('child')).toBe(1);
    expect(depth.get('grandchild')).toBe(2);
  });

  it('guards against cycles and missing parents (depth 0)', () => {
    const ix: Interaction[] = [
      { id: 'a', parent_interaction_id: 'b' } as Interaction,
      { id: 'b', parent_interaction_id: 'a' } as Interaction, // cycle
      { id: 'orphan', parent_interaction_id: 'gone' } as Interaction, // missing
    ];
    const depth = computeInteractionDepths(ix);
    // Cycle is broken (no infinite recursion); orphan whose parent is absent
    // is depth 0.
    expect(depth.get('orphan')).toBe(0);
    expect(depth.get('a')).toBeGreaterThanOrEqual(0);
  });
});

describe('roleMeta', () => {
  it('gives the key glyph to both creating-evidence roles (anchor + discovered_via)', () => {
    expect(roleMeta('anchor')).toEqual({ label: 'anchor', icon: 'key' });
    expect(roleMeta('discovered_via')).toEqual({ label: 'discovered via', icon: 'key' });
  });

  it('maps the lower-weight roles to distinct glyphs', () => {
    // info → content carried; identified_via → repeat sighting (dot);
    // connector → contributed nothing (thin dash).
    expect(roleMeta('info')).toEqual({ label: 'info', icon: 'info' });
    expect(roleMeta('identified_via')).toEqual({ label: 'identified via', icon: 'dot' });
    expect(roleMeta('connector')).toEqual({ label: 'connector', icon: 'minus' });
  });

  it('falls through to the raw value with no glyph for an unknown role', () => {
    expect(roleMeta('something_new')).toEqual({ label: 'something_new', icon: 'none' });
  });
});

describe('durationMs', () => {
  it('returns (ended - started) in ms to 3 decimals, or null when unpaired', () => {
    expect(durationMs('2026-05-01T12:00:00.000Z', '2026-05-01T12:00:05.000Z')).toBe(
      '5000.000',
    );
    expect(durationMs(null, '2026-05-01T12:00:05.000Z')).toBeNull();
    expect(durationMs('2026-05-01T12:00:00.000Z', null)).toBeNull();
  });
});
