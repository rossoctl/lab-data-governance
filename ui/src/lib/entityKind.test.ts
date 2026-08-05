import { describe, it, expect } from 'vitest';
import { KIND_COLOR, colorForKind, kindColorVar, nodeNeutralColorVar } from './entityKind';

/**
 * The kind → colour map is the single source shared by the flow tables' entity pills
 * and the Interaction diagram's head boxes. These tests pin that contract: each
 * consumer must resolve the same colour for a kind, and must derive the CSS variable
 * from the colour NAME rather than hardcoding a hex value.
 *
 * THE GRAPH IS IN THAT LIST FOR ONE OF ITS TWO TABS. Execution Flow paints its nodes
 * with `kindColorVar` like everything else; the LINEAGE tab paints them with
 * `nodeNeutralColorVar` — one neutral for every kind — because hue there is already
 * carrying the data sources plus two directions. Which tab gets which is decided by
 * `NodeData.kindColoured` in `ExecutionFlowGraph` and asserted in its tests; this file
 * pins only that the two functions exist and differ, since its job is to stop the
 * surfaces drifting by accident.
 */
describe('entityKind', () => {
  it('maps the known kinds to the vanilla .ent-pill palette', () => {
    expect(KIND_COLOR).toEqual({
      user: 'gold',
      external_client: 'purple',
      agent: 'blue',
      tool: 'green',
      external_service: 'red',
      llm: 'blue',
    });
  });

  it('resolves a known kind to its palette colour', () => {
    expect(colorForKind('agent')).toBe('blue');
    expect(colorForKind('tool')).toBe('green');
  });

  it('falls back to grey for a kind the palette does not know', () => {
    // A kind the backend has started emitting that the UI has no entry for yet
    // must still render, and must render the same way in the pill and the graph.
    expect(colorForKind('brand_new_kind')).toBe('grey');
    expect(colorForKind('')).toBe('grey');
  });

  it('builds the CSS variable from the colour name, with no raw hex', () => {
    // Global (`--pf-v5-global--*`, declared at :root) rather than a label
    // COMPONENT variable: PF declares `--pf-v5-c-label--m-blue__content--Color`
    // on the `.pf-v5-c-label` selector itself, so it resolves to nothing inside
    // the graph's SVG — which is exactly how every node once came out the same
    // grey. Globals resolve document-wide and are still dark-theme aware.
    expect(kindColorVar('agent')).toBe(
      'var(--pf-v5-global--primary-color--100, var(--dg-color-label))',
    );
    expect(kindColorVar('tool')).toContain('--pf-v5-global--success-color--100');
    expect(kindColorVar('external_service')).toContain('--pf-v5-global--danger-color--100');
    expect(kindColorVar('agent')).not.toMatch(/#[0-9a-f]{3,6}/i);
    // Never a label component variable, which would silently fail to resolve.
    expect(kindColorVar('agent')).not.toContain('--pf-v5-c-label');
  });

  it('returns a var() reference, not a resolved value, so the dark theme still applies', () => {
    for (const kind of ['user', 'agent', 'tool', 'llm', 'external_service', 'external_client']) {
      expect(kindColorVar(kind)).toMatch(
        /^var\(--pf-v5-global--[\w-]+, var\(--dg-color-label\)\)$/,
      );
    }
  });

  it('uses the repo token as the variable fallback so a node stays visible', () => {
    expect(kindColorVar('user')).toContain('var(--dg-color-label)');
  });

  it('gives an unknown kind the grey variable, not an undefined one', () => {
    expect(kindColorVar('nope')).toBe(
      'var(--pf-v5-global--Color--200, var(--dg-color-label))',
    );
  });

  it('gives visually DISTINCT variables to the kinds a reader must tell apart', () => {
    // The whole point of colouring by kind is that an agent, a tool and a user do
    // not look the same. The bug this guards is all of them resolving to the one
    // fallback, which is what happened with the label component variables.
    const vars = ['user', 'agent', 'tool', 'external_service', 'external_client'].map(kindColorVar);
    expect(new Set(vars).size).toBe(vars.length);
  });

  it('gives the two blue kinds the same colour (agent and llm share it by design)', () => {
    expect(colorForKind('agent')).toBe(colorForKind('llm'));
  });

  describe('nodeNeutralColorVar (the LINEAGE tab node colour)', () => {
    it('is the same neutral for every kind, so no node carries a kind hue', () => {
      // Pinned directly: on the tab that uses this, green and blue (and every other
      // kind hue) are gone. Asserted as "all kinds resolve to ONE value" rather than
      // "agent is not blue", because the latter would still pass if agent had merely
      // been recoloured to some other hue.
      const kinds = ['user', 'agent', 'tool', 'llm', 'external_service', 'external_client', 'nope'];
      const vars = new Set(kinds.map(() => nodeNeutralColorVar()));
      expect(vars.size).toBe(1);
    });

    it('carries neither the blue nor the green kind variable', () => {
      // The two hues named in the request, excluded by name so a regression that
      // reintroduced either would fail here and not merely change a set size.
      expect(nodeNeutralColorVar()).not.toContain('--pf-v5-global--primary-color--100');
      expect(nodeNeutralColorVar()).not.toContain('--pf-v5-global--success-color--100');
    });

    it('is a var() reference with the repo token as fallback, and no raw hex', () => {
      // Same house rules the kind variables are held to: a reference so the dark
      // theme applies at paint time, and no hex anywhere in the chain.
      expect(nodeNeutralColorVar()).toMatch(
        /^var\(--pf-v5-global--[\w-]+, var\(--dg-color-label\)\)$/,
      );
      expect(nodeNeutralColorVar()).not.toMatch(/#[0-9a-f]{3,6}/i);
      expect(nodeNeutralColorVar()).not.toContain('--pf-v5-c-label');
    });

    it('leaves the PILL palette alone — the pills keep their kind hues', () => {
      // The guard on the divergence: adding a neutral must not have reached into the
      // map the tables, the sequence diagram and the Execution Flow graph still paint
      // from.
      expect(kindColorVar('agent')).toContain('--pf-v5-global--primary-color--100');
      expect(kindColorVar('tool')).toContain('--pf-v5-global--success-color--100');
      expect(kindColorVar('agent')).not.toBe(nodeNeutralColorVar());
    });
  });
});
