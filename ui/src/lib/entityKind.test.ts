import { describe, it, expect } from 'vitest';
import {
  KIND_COLOR,
  KIND_SHAPE,
  KIND_SHAPE_FALLBACK,
  colorForKind,
  kindColorVar,
  nodeNeutralColorVar,
  shapeForKind,
} from './entityKind';

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

/**
 * The kind → SHAPE map (issue #218), the second per-kind decision this module owns.
 *
 * A SEPARATE DECISION FROM COLOUR, not a re-expression of it, and the `agent`/`llm`
 * pair is the case that proves it has to be: those two kinds deliberately SHARE a
 * colour (`blue`, asserted above), so on any view that paints by kind they are
 * already indistinguishable by hue. Shape is what separates them — which is the
 * whole reason issue #218 asks for shapes rather than more colours.
 *
 * WHY THE SHAPE NAMES ARE THIS MODULE'S OWN STRINGS and not PF's `NodeShape` enum:
 * this file is in the EAGER bundle (`EntityPill` and `InteractionDiagram` import it
 * unlazily), while every `@patternfly/react-topology` import in the repo is confined
 * to `ExecutionFlowGraph.tsx` precisely so the ~130kB topology chunk stays lazy —
 * see that file's import-order note. `NodeShape` is a runtime enum, not a type, so
 * importing it here would pull that chunk into the eager bundle for every reader of
 * the trace list. The graph translates these names to `NodeShape` at its own
 * boundary, exactly as it already translates a colour NAME to a PF global variable
 * (`PF_COLOR_TO_GLOBAL_VAR`). Same NAME→NAME discipline, same reason.
 */
describe('entityKind shapes (issue #218)', () => {
  it('maps the four kinds the issue names to their requested shapes', () => {
    // Straight from the issue: Agent - hexagon, Tool - rectangle, LLM -
    // diamond/rhombus, User - circle. Asserted as the whole map (not four
    // separate lookups) so an ADDED key has to be a deliberate edit here too.
    expect(KIND_SHAPE).toEqual({
      agent: 'hexagon',
      tool: 'rect',
      llm: 'rhombus',
      user: 'circle',
    });
  });

  it('resolves each named kind to its shape', () => {
    expect(shapeForKind('agent')).toBe('hexagon');
    expect(shapeForKind('tool')).toBe('rect');
    expect(shapeForKind('llm')).toBe('rhombus');
    expect(shapeForKind('user')).toBe('circle');
  });

  it('falls back to the ellipse for a kind the shape map does not name', () => {
    // The issue names four kinds; `KIND_COLOR` knows six, and `Entity.kind` is
    // `string | null` in types.ts — an open set the backend can extend. Everything
    // unnamed keeps the shape every node had BEFORE this change (ellipse), so an
    // unknown kind degrades to the old drawing rather than to a shape that would
    // falsely claim to be one of the four.
    expect(shapeForKind('external_client')).toBe('ellipse');
    expect(shapeForKind('external_service')).toBe('ellipse');
    expect(shapeForKind('brand_new_kind')).toBe('ellipse');
    expect(shapeForKind('')).toBe('ellipse');
  });

  it('exposes the fallback as a named constant, so node and legend cannot disagree', () => {
    // Same reasoning as KIND_COLOR_FALLBACK: two surfaces read this, and each
    // picking its own neutral is the drift the constant exists to stop.
    expect(KIND_SHAPE_FALLBACK).toBe('ellipse');
    expect(shapeForKind('nope')).toBe(KIND_SHAPE_FALLBACK);
  });

  it('gives DISTINCT shapes to the four kinds, so no two are confusable', () => {
    // The point of shaping by kind is that an agent, a tool, an llm and a user do
    // not draw the same. Asserted as a set size so a regression that collapsed two
    // onto one shape fails here rather than merely looking odd on screen.
    const shapes = ['agent', 'tool', 'llm', 'user'].map(shapeForKind);
    expect(new Set(shapes).size).toBe(shapes.length);
  });

  it('separates agent from llm, which COLOUR alone cannot', () => {
    // The specific pair that motivates shapes over more colours: these two share
    // `blue` by design (asserted in the colour block above), so hue cannot tell
    // them apart on a kind-coloured graph. This is the assertion that would fail if
    // someone "simplified" the shape map by deriving it from the colour map.
    expect(colorForKind('agent')).toBe(colorForKind('llm'));
    expect(shapeForKind('agent')).not.toBe(shapeForKind('llm'));
  });

  it('does not import PF NodeShape values — the names are plain repo strings', () => {
    // The eager-bundle guard, as a behavioural assertion rather than a comment:
    // these are lowercase plain strings this module owns. They happen to coincide
    // with PF's `NodeShape` VALUES (which is what makes the graph's translation a
    // one-to-one table), but nothing here reads that enum.
    for (const kind of ['agent', 'tool', 'llm', 'user', 'unknown']) {
      expect(typeof shapeForKind(kind)).toBe('string');
      expect(shapeForKind(kind)).toMatch(/^[a-z]+$/);
    }
  });

  it('leaves the COLOUR map untouched — shape is an addition, not a replacement', () => {
    // Shapes arriving must not have quietly re-pointed any kind's hue: the pills,
    // the sequence diagram and the Execution Flow graph all still paint from
    // KIND_COLOR, and #218 changes none of them.
    expect(KIND_COLOR.agent).toBe('blue');
    expect(KIND_COLOR.tool).toBe('green');
    expect(KIND_COLOR.user).toBe('gold');
    expect(colorForKind('agent')).toBe('blue');
  });
});
