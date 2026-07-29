import { describe, it, expect } from 'vitest';
import {
  toolSubtype,
  computeInteractionDepths,
  durationMs,
  roleMeta,
  isInfrastructure,
} from './flow';
import type { Entity, Interaction } from './flow';

// Ported from execution_flow_logic.js: tool subtype-by-natural-key-shape, the
// parent-walk depth computation (order-independent, cycle-guarded), and the
// interaction/span duration formula.

const entity = (kind: string, natural_key: string): Entity =>
  ({ id: 'e', kind, natural_key, display_name: natural_key, detected_from: '' });

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

describe('isInfrastructure', () => {
  const withKind = (request_content_kind: string): Pick<Interaction, 'kinds'> => ({
    kinds: {
      protocol: 'mcp',
      mcp_method: null,
      request_content_kind,
      response_content_kind: 'x',
    },
  });

  it('flags the server classifier\'s MCP plumbing kinds (lifecycle + discovery)', () => {
    expect(isInfrastructure(withKind('mcp_lifecycle_request'))).toBe(true);
    expect(isInfrastructure(withKind('tool_discovery_request'))).toBe(true);
  });

  it('never flags real work or rows without kinds (missing anchor)', () => {
    expect(isInfrastructure(withKind('tool_call_request'))).toBe(false);
    expect(isInfrastructure(withKind('agent_request'))).toBe(false);
    expect(isInfrastructure({ kinds: null })).toBe(false);
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
