import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithProviders } from '../test/renderWithProviders';
import { ExecutionFlowGraph } from './ExecutionFlowGraph';
import type { Entity, Interaction, InteractionLeg } from '../types';

/**
 * Render coverage for the Execution Flow graph, deliberately scoped to what jsdom
 * can honestly assert.
 *
 * WHAT JSDOM CAN DO HERE: PatternFly topology *does* mount — the visualization
 * surface, the SVG, the Dagre layout pass, the EDGE elements (including the
 * arrowhead polygon and the per-edge colour variables) and the zoom control bar's
 * buttons all render, and are asserted below.
 *
 * WHAT IT CANNOT: jsdom implements no SVG layout, so `SVGGraphicsElement.getBBox`
 * does not exist; `src/test/setup.ts` stubs it to a ZERO size (see the long note
 * there for why the absence, not the zeros, is what breaks). PF's `NodeLabel` and
 * its per-edge connector tag both measure themselves with `useSize` → `getBBox()`,
 * and treat a zero measurement as "not laid out yet" and bail out. So each node's
 * `<g data-kind="node">` is emitted but stays EMPTY, and the edge's `seq` tag text
 * is not rendered. Node labels, the seq tag, node geometry and anything about
 * visual layout are therefore not observable here and are NOT asserted — a test
 * claiming to verify them would be lying. They are covered instead by:
 *   - `lib/graph.test.ts` — the node/edge derivation (per-leg direction, seq
 *     labels, error tri-state, every edge case) as pure logic, which is where the
 *     real coverage lives; and
 *   - a Playwright screenshot run against the real browser bundle, which is the
 *     only place arrowheads, seq tags, zoom behaviour and the dark theme can
 *     actually be seen.
 *
 * So the assertions below are: it mounts, it reaches the right STATE
 * (loading/error/empty/graph), the right NUMBER of node and edge elements exist
 * with the right identities, and the controls are present and wired.
 *
 * EDGES ARE PER LEG (ADR-0025): a completed interaction mounts TWO edge elements
 * pointing opposite ways, ided `<interaction>:request` / `<interaction>:response`.
 * The fixtures below therefore carry real `legs` — with `legs: []` an interaction
 * contributes no edges at all, which is correct under this model and was the
 * silent premise the pre-leg version of this file relied on.
 */

const ENTITIES: Entity[] = [
  { id: 'e1', kind: 'agent', natural_key: 'agent:(p,a)', display_name: 'agent-a', detected_from: 'span' },
  { id: 'e2', kind: 'tool', natural_key: 'tool:(p,svc)', display_name: 'search', detected_from: 'span' },
  { id: 'e3', kind: 'llm', natural_key: 'llm:api.example.com/gpt', display_name: 'gpt-4', detected_from: 'span' },
];

/** A leg, defaulting to a successful one. */
function mkLeg(
  legType: 'request' | 'response',
  seq: number,
  error: boolean | null = false,
): InteractionLeg {
  return {
    leg_type: legType,
    occurred_at: '2026-05-01T12:00:00Z',
    payload_hash: null,
    error,
    seq,
  };
}

/**
 * A completed interaction: BOTH legs, so it mounts two opposite-direction edges.
 * `seqBase` gives its request/response legs distinct trace-wide seqs, since the
 * edge ORDER and the edge LABELS are both the seq.
 */
function mkIx(
  over: Partial<Interaction> & Pick<Interaction, 'id'>,
  seqBase = 1,
): Interaction {
  return {
    caller_entity_id: 'e1',
    callee_entity_id: 'e2',
    summary: `summary-${over.id}`,
    parent_interaction_id: null,
    legs: [mkLeg('request', seqBase), mkLeg('response', seqBase + 1)],
    duration_seconds: 1,
    any_error: false,
    span_count: 1,
    anchor_count: 1,
    ...over,
  };
}

/** Stub the two reads the graph makes. `null` for either → that read fails. */
function mockApi(entities: Entity[] | null, interactions: Interaction[] | null) {
  (fetch as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
    if (url.endsWith('/entities')) {
      if (entities === null) return { ok: false, status: 500, json: async () => ({}) };
      return { ok: true, status: 200, json: async () => ({ entities }) };
    }
    if (url.endsWith('/interactions')) {
      if (interactions === null) return { ok: false, status: 500, json: async () => ({}) };
      return { ok: true, status: 200, json: async () => ({ interactions }) };
    }
    return { ok: true, status: 200, json: async () => ({}) };
  });
}

const nodeEls = () => document.querySelectorAll('[data-kind="node"]');
const edgeEls = () => document.querySelectorAll('[data-kind="edge"]');

describe('ExecutionFlowGraph', () => {
  beforeEach(() => vi.stubGlobal('fetch', vi.fn()));
  afterEach(() => vi.unstubAllGlobals());

  // --- Loading / error / empty, matching the sibling FlowTables conventions.

  it('shows a spinner while the entities/interactions reads are in flight', () => {
    // Never-resolving fetch: the view is unambiguously still loading.
    (fetch as ReturnType<typeof vi.fn>).mockImplementation(() => new Promise(() => {}));
    renderWithProviders(<ExecutionFlowGraph traceId="T1" />);

    expect(screen.getByLabelText(/Loading execution flow graph/i)).toBeInTheDocument();
    expect(screen.queryByTestId('execution-flow-graph')).not.toBeInTheDocument();
  });

  it('renders an empty state — not a blank box — when the trace has no entities', async () => {
    mockApi([], []);
    renderWithProviders(<ExecutionFlowGraph traceId="T1" />);

    await waitFor(() => expect(screen.getByText(/No execution flow/i)).toBeInTheDocument());
    expect(screen.getByText(/nothing to graph/i)).toBeInTheDocument();
    // The same "may still be draining" tone the Interaction flow tab uses.
    expect(screen.getByText(/may still be draining/i)).toBeInTheDocument();
    // No surface at all, rather than an empty one.
    expect(screen.queryByTestId('execution-flow-graph')).not.toBeInTheDocument();
  });

  it('reports a failed read as an error, NOT as an empty trace', async () => {
    // "No entities" and "we could not ask" demand different actions (wait vs
    // retry); conflating them would tell a reader to wait for an answer that is
    // never coming.
    mockApi(null, []);
    renderWithProviders(<ExecutionFlowGraph traceId="T1" />);

    await waitFor(() =>
      expect(screen.getByText(/Could not load the execution flow/i)).toBeInTheDocument(),
    );
    expect(screen.getByText(/failed request, not an empty trace/i)).toBeInTheDocument();
    expect(screen.queryByText(/No execution flow/i)).not.toBeInTheDocument();
  });

  it('reports a failed INTERACTIONS read as an error too', async () => {
    mockApi(ENTITIES, null);
    renderWithProviders(<ExecutionFlowGraph traceId="T1" />);

    await waitFor(() =>
      expect(screen.getByText(/Could not load the execution flow/i)).toBeInTheDocument(),
    );
  });

  // --- The mounted graph: element counts and identities.

  it('mounts a topology surface with one node per entity and one edge per LEG', async () => {
    mockApi(ENTITIES, [
      mkIx({ id: 'i1', caller_entity_id: 'e1', callee_entity_id: 'e2' }, 1),
      mkIx({ id: 'i2', caller_entity_id: 'e1', callee_entity_id: 'e3' }, 3),
    ]);
    renderWithProviders(<ExecutionFlowGraph traceId="T1" />);

    await waitFor(() => expect(screen.getByTestId('execution-flow-graph')).toBeInTheDocument());
    await waitFor(() => expect(nodeEls()).toHaveLength(3));
    // Two completed interactions → FOUR edges, not two.
    expect(edgeEls()).toHaveLength(4);
    // Element identity is the entity id for a node and the (interaction, leg)
    // key for an edge, so either can be traced back to its row in the other tabs.
    expect([...nodeEls()].map((n) => n.getAttribute('data-id')).sort()).toEqual(['e1', 'e2', 'e3']);
    expect([...edgeEls()].map((e) => e.getAttribute('data-id')).sort()).toEqual([
      'i1:request',
      'i1:response',
      'i2:request',
      'i2:response',
    ]);
  });

  it('mounts the two legs of one interaction as edges pointing OPPOSITE ways', async () => {
    // The substance of the leg model, at the RENDERED level: the request goes
    // e1 → e2 and the response comes back e2 → e1.
    //
    // PF emits only `data-id` / `data-kind` on an edge element (const.js) — there
    // is no `data-source-id` to read, and asserting on absent attributes would
    // vacuously compare null to null and pass no matter which way the arrow went.
    // The drawn PATH is the honest observable: it is `M<start> … L<end>`, and Dagre
    // does assign coordinates under jsdom (only TEXT measurement is unavailable),
    // so the two legs' paths must start and end at swapped points.
    mockApi(ENTITIES, [mkIx({ id: 'i1', caller_entity_id: 'e1', callee_entity_id: 'e2' })]);
    renderWithProviders(<ExecutionFlowGraph traceId="T1" />);

    await waitFor(() => expect(edgeEls()).toHaveLength(2));
    // First and last coordinate pair of the edge's own link path.
    const ends = (id: string) => {
      const d = document
        .querySelector(`[data-id="${id}"] .pf-topology__edge__link`)!
        .getAttribute('d')!;
      const pts = [...d.matchAll(/-?[\d.]+\s+-?[\d.]+/g)].map((m) => m[0]);
      return [pts[0], pts[pts.length - 1]];
    };
    const [reqFrom, reqTo] = ends('i1:request');
    const [respFrom, respTo] = ends('i1:response');
    // Not degenerate: the layout really placed the two nodes apart.
    expect(reqFrom).not.toEqual(reqTo);
    // The response retraces the request backwards — start and end swapped.
    expect(respFrom).toEqual(reqTo);
    expect(respTo).toEqual(reqFrom);
  });

  it('mounts exactly one edge for an in-flight interaction with only a request leg', async () => {
    // No phantom response arrow for a call that has not been answered.
    mockApi(ENTITIES, [
      mkIx({ id: 'i1', legs: [mkLeg('request', 1)], duration_seconds: null }),
    ]);
    renderWithProviders(<ExecutionFlowGraph traceId="T1" />);

    await waitFor(() => expect(edgeEls()).toHaveLength(1));
    expect(document.querySelector('[data-id="i1:request"]')).toBeInTheDocument();
    expect(document.querySelector('[data-id="i1:response"]')).not.toBeInTheDocument();
  });

  it('draws an arrowhead on each edge so each leg\'s direction is visible', async () => {
    // The arrow terminal DOES render under jsdom (it needs no text measurement),
    // so the per-leg direction cue is genuinely assertable here. One head per LEG.
    mockApi(ENTITIES, [mkIx({ id: 'i1' })]);
    renderWithProviders(<ExecutionFlowGraph traceId="T1" />);

    await waitFor(() => expect(edgeEls()).toHaveLength(2));
    expect(document.querySelectorAll('.pf-topology-connector-arrow').length).toBe(2);
  });

  it("colours an edge from its OWN leg's error, leaving its sibling leg uncoloured", async () => {
    // One interaction whose RESPONSE failed. Only the response edge may be red:
    // the request genuinely succeeded, and reddening it would report a failure at
    // a point in the trace where none had happened.
    mockApi(ENTITIES, [
      mkIx({
        id: 'i1',
        legs: [mkLeg('request', 1, false), mkLeg('response', 2, true)],
        any_error: true,
      }),
    ]);
    renderWithProviders(<ExecutionFlowGraph traceId="T1" />);

    await waitFor(() => expect(edgeEls()).toHaveLength(2));
    const bad = document.querySelector('[data-id="i1:response"]')!.innerHTML;
    const ok = document.querySelector('[data-id="i1:request"]')!.innerHTML;
    // The --dg-* token, never a raw hex value.
    expect(bad).toContain('var(--dg-color-error)');
    expect(bad).not.toMatch(/#[0-9a-f]{6}/i);
    expect(ok).not.toContain('var(--dg-color-error)');
    // Exactly one error-classed edge, so the class tracks the leg not the parent.
    expect(document.querySelectorAll('.dg-graph-edge--error')).toHaveLength(1);
  });

  it('leaves a null leg error uncoloured — unknown is not a failure', async () => {
    mockApi(ENTITIES, [
      mkIx({ id: 'i1', legs: [mkLeg('request', 1, null), mkLeg('response', 2, null)] }),
    ]);
    renderWithProviders(<ExecutionFlowGraph traceId="T1" />);

    await waitFor(() => expect(edgeEls()).toHaveLength(2));
    expect(document.querySelector('.dg-graph-edge--error')).not.toBeInTheDocument();
  });

  // --- The edge cases, as they surface in the UI (not merely in a comment).

  it('discloses an interaction with an unresolved participant instead of dropping it silently', async () => {
    mockApi(ENTITIES, [
      mkIx({ id: 'good' }, 1),
      mkIx({ id: 'orphan', callee_entity_id: null, summary: 'calls the unknown' }, 3),
    ]);
    renderWithProviders(<ExecutionFlowGraph traceId="T1" />);

    await waitFor(() =>
      expect(screen.getByText(/1 interaction not shown as edges/i)).toBeInTheDocument(),
    );
    // Counted ONCE for the interaction even though BOTH its legs were lost — the
    // unresolved participant is a single defect on the shared identity row. The
    // leg count is spelled out separately so the arrow arithmetic still adds up.
    expect(
      screen.getByText(/calls the unknown \(missing callee, 2 legs\)/i),
    ).toBeInTheDocument();
    // …and both legs of the drawable one are still drawn.
    expect(edgeEls()).toHaveLength(2);
  });

  it('says nothing about unresolved participants when there are none', async () => {
    mockApi(ENTITIES, [mkIx({ id: 'i1' })]);
    renderWithProviders(<ExecutionFlowGraph traceId="T1" />);

    await waitFor(() => expect(edgeEls()).toHaveLength(2));
    expect(screen.queryByText(/not shown as edges/i)).not.toBeInTheDocument();
  });

  it('renders an isolated entity as a node and discloses that it has no edges', async () => {
    // e3 is named by no interaction. It must still appear — an entity is a
    // governance fact on its own — and the reader must be told why it is bare.
    mockApi(ENTITIES, [mkIx({ id: 'i1', caller_entity_id: 'e1', callee_entity_id: 'e2' })]);
    renderWithProviders(<ExecutionFlowGraph traceId="T1" />);

    await waitFor(() => expect(nodeEls()).toHaveLength(3));
    expect(screen.getByText(/1 isolated entity/i)).toBeInTheDocument();
    expect(screen.getByText(/dashed outline/i)).toBeInTheDocument();
  });

  it('pluralises and counts several isolated entities', async () => {
    mockApi(ENTITIES, []);
    renderWithProviders(<ExecutionFlowGraph traceId="T1" />);

    await waitFor(() => expect(nodeEls()).toHaveLength(3));
    expect(screen.getByText(/3 isolated entities/i)).toBeInTheDocument();
  });

  it('keeps parallel interactions between one pair as separate edges and says so', async () => {
    mockApi(ENTITIES, [
      mkIx({ id: 'i1', caller_entity_id: 'e1', callee_entity_id: 'e2' }, 1),
      mkIx({ id: 'i2', caller_entity_id: 'e1', callee_entity_id: 'e2' }, 3),
    ]);
    renderWithProviders(<ExecutionFlowGraph traceId="T1" />);

    // Four arrows for two interactions' four legs — nothing merged with a count.
    await waitFor(() => expect(edgeEls()).toHaveLength(4));
    expect(screen.getByText(/1 entity pair with multiple interactions/i)).toBeInTheDocument();
    expect(screen.getByText(/arrow count matches the leg count/i)).toBeInTheDocument();
  });

  it('does NOT claim a parallel channel for a single interaction\'s request/response pair', async () => {
    // The regression guard for the notice's own usefulness: one completed
    // interaction always puts two arrows between the same two nodes, so an
    // edge-counting rule would fire this on virtually every trace and the reader
    // would learn to ignore it.
    mockApi(ENTITIES, [mkIx({ id: 'i1', caller_entity_id: 'e1', callee_entity_id: 'e2' })]);
    renderWithProviders(<ExecutionFlowGraph traceId="T1" />);

    await waitFor(() => expect(edgeEls()).toHaveLength(2));
    expect(screen.queryByText(/with multiple interactions/i)).not.toBeInTheDocument();
  });

  it('renders BOTH legs of a self-call as their own edges', async () => {
    // Swapping caller and callee when they are the same entity is a no-op, so the
    // response leg is a self-edge too — and gets the same dashed treatment.
    mockApi(ENTITIES, [mkIx({ id: 'self', caller_entity_id: 'e1', callee_entity_id: 'e1' })]);
    renderWithProviders(<ExecutionFlowGraph traceId="T1" />);

    await waitFor(() => expect(edgeEls()).toHaveLength(2));
    expect(document.querySelector('[data-id="self:request"]')).toBeInTheDocument();
    expect(document.querySelector('[data-id="self:response"]')).toBeInTheDocument();
    // Both drawn dashed, since a zero-length straight line is invisible.
    expect(document.querySelectorAll('.pf-topology__edge__link.pf-m-dashed')).toHaveLength(2);
    // Not reported as unresolved, and its entity is not called isolated.
    expect(screen.queryByText(/not shown as edges/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/1 isolated entit/i)).not.toBeInTheDocument();
  });

  // --- The zoom controls (task 2). Presence and wiring only: jsdom has no SVG
  // layout, so actual zoom GEOMETRY is unobservable and is asserted in the browser
  // instead. Claiming otherwise here would be a lie (see this file's header).

  it('renders the four zoom controls as real buttons with accessible names', async () => {
    mockApi(ENTITIES, [mkIx({ id: 'i1' })]);
    renderWithProviders(<ExecutionFlowGraph traceId="T1" />);

    await waitFor(() => expect(screen.getByTestId('execution-flow-graph')).toBeInTheDocument());
    // PF's control bar puts each label in a `pf-v5-screen-reader` span, so these
    // resolve by ROLE + NAME — the accessible name a screen reader would announce,
    // not merely some text on the page.
    for (const name of [/^Zoom In$/i, /^Zoom Out$/i, /^Fit to Screen$/i, /^Reset View$/i]) {
      expect(screen.getByRole('button', { name })).toBeEnabled();
    }
  });

  it('omits the control bar\'s Legend button, which this view has nothing to open', async () => {
    // PF offers it by default; a button that does nothing is worse than none.
    mockApi(ENTITIES, [mkIx({ id: 'i1' })]);
    renderWithProviders(<ExecutionFlowGraph traceId="T1" />);

    await waitFor(() => expect(screen.getByRole('button', { name: /^Zoom In$/i })).toBeInTheDocument());
    expect(screen.queryByRole('button', { name: /legend/i })).not.toBeInTheDocument();
  });

  it('wires Zoom In to the visualization\'s own scale, so the surface really rescales', async () => {
    // The wiring, not the pixels: clicking must change the surface's applied
    // transform. That is the observable that proves the callback reached
    // `Graph.scaleBy` rather than being a decorative button.
    mockApi(ENTITIES, [mkIx({ id: 'i1' })]);
    renderWithProviders(<ExecutionFlowGraph traceId="T1" />);

    await waitFor(() => expect(edgeEls()).toHaveLength(2));
    // The surface's own pan/zoom group, which GraphComponent renders as
    // `translate(x, y) scale(s)` from `graphElement.getScale()`.
    const layer = () => document.querySelector('[data-surface="true"]');
    const before = layer()?.getAttribute('transform');

    await userEvent.click(screen.getByRole('button', { name: /^Zoom In$/i }));

    await waitFor(() => expect(layer()?.getAttribute('transform')).not.toBe(before));
    // Zoomed IN: the scale factor in the transform grew.
    const scaleOf = (t: string | null | undefined) =>
      Number(/scale\(([\d.]+)/.exec(t ?? '')?.[1] ?? '1');
    expect(scaleOf(layer()?.getAttribute('transform'))).toBeGreaterThan(scaleOf(before));
  });

  it('makes Zoom Out the exact inverse of Zoom In', async () => {
    // One in then one out returns to the starting scale, so the two buttons cannot
    // drift the view a little further every time they are used in pairs.
    mockApi(ENTITIES, [mkIx({ id: 'i1' })]);
    renderWithProviders(<ExecutionFlowGraph traceId="T1" />);

    await waitFor(() => expect(edgeEls()).toHaveLength(2));
    const layer = () => document.querySelector('[data-surface="true"]');
    const scaleNow = () =>
      Number(/scale\(([\d.]+)/.exec(layer()?.getAttribute('transform') ?? '')?.[1] ?? '1');
    const start = scaleNow();

    await userEvent.click(screen.getByRole('button', { name: /^Zoom In$/i }));
    await waitFor(() => expect(scaleNow()).toBeGreaterThan(start));
    await userEvent.click(screen.getByRole('button', { name: /^Zoom Out$/i }));

    await waitFor(() => expect(scaleNow()).toBeCloseTo(start, 5));
  });

  it('re-derives the model when the data changes rather than accumulating stale nodes', async () => {
    // fromModel(…, false) replaces rather than merges, so a node from a previous
    // render must not survive into the next.
    mockApi(ENTITIES, [mkIx({ id: 'i1' })]);
    const { rerender } = renderWithProviders(<ExecutionFlowGraph traceId="T1" />);
    await waitFor(() => expect(nodeEls()).toHaveLength(3));

    mockApi([ENTITIES[0]], []);
    rerender(<ExecutionFlowGraph traceId="T2" />);
    await waitFor(() => expect(nodeEls()).toHaveLength(1));
    expect(edgeEls()).toHaveLength(0);
  });
});
