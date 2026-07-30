import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { renderWithProviders } from '../test/renderWithProviders';
import { ExecutionFlowGraph } from './ExecutionFlowGraph';
import type { Entity, Interaction } from '../types';

/**
 * Render coverage for the Execution Flow graph, deliberately scoped to what jsdom
 * can honestly assert.
 *
 * WHAT JSDOM CAN DO HERE: PatternFly topology *does* mount — the visualization
 * surface, the SVG, the Dagre layout pass and the EDGE elements (including the
 * arrowhead polygon and the per-edge colour variables) all render, and are
 * asserted below.
 *
 * WHAT IT CANNOT: PF's `NodeLabel` measures its own text with `useSize` →
 * `SVGGraphicsElement.getBBox()`, which jsdom stubs to zeros. A zero-sized label
 * makes the node renderer bail out, so each node's `<g data-kind="node">` is
 * emitted but stays EMPTY. That means node labels, node geometry and anything
 * about visual layout are not observable here and are NOT asserted — a test
 * claiming to verify them would be lying. They are covered instead by:
 *   - `lib/graph.test.ts` — the node/edge derivation (labels, kinds, every edge
 *     case) as pure logic, which is where the real coverage lives; and
 *   - a Playwright screenshot run against the real browser bundle, which is the
 *     only place arrowheads, labels and the dark theme can actually be seen.
 *
 * So the assertions below are: it mounts, it reaches the right STATE
 * (loading/error/empty/graph), and the right NUMBER of node and edge elements
 * exist with the right identities.
 */

const ENTITIES: Entity[] = [
  { id: 'e1', kind: 'agent', natural_key: 'agent:(p,a)', display_name: 'agent-a', detected_from: 'span' },
  { id: 'e2', kind: 'tool', natural_key: 'tool:(p,svc)', display_name: 'search', detected_from: 'span' },
  { id: 'e3', kind: 'llm', natural_key: 'llm:api.example.com/gpt', display_name: 'gpt-4', detected_from: 'span' },
];

function mkIx(over: Partial<Interaction> & Pick<Interaction, 'id'>): Interaction {
  return {
    caller_entity_id: 'e1',
    callee_entity_id: 'e2',
    summary: `summary-${over.id}`,
    parent_interaction_id: null,
    legs: [],
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

  it('mounts a topology surface with one node per entity and one edge per interaction', async () => {
    mockApi(ENTITIES, [
      mkIx({ id: 'i1', caller_entity_id: 'e1', callee_entity_id: 'e2' }),
      mkIx({ id: 'i2', caller_entity_id: 'e1', callee_entity_id: 'e3' }),
    ]);
    renderWithProviders(<ExecutionFlowGraph traceId="T1" />);

    await waitFor(() => expect(screen.getByTestId('execution-flow-graph')).toBeInTheDocument());
    await waitFor(() => expect(nodeEls()).toHaveLength(3));
    expect(edgeEls()).toHaveLength(2);
    // Element identity is the entity/interaction id, so a node can be traced
    // back to its row in the other tabs.
    expect([...nodeEls()].map((n) => n.getAttribute('data-id')).sort()).toEqual(['e1', 'e2', 'e3']);
    expect([...edgeEls()].map((e) => e.getAttribute('data-id')).sort()).toEqual(['i1', 'i2']);
  });

  it('draws an arrowhead on each edge so the direction is visible', async () => {
    // The arrow terminal DOES render under jsdom (it needs no text measurement),
    // so the caller → callee direction cue is genuinely assertable here.
    mockApi(ENTITIES, [mkIx({ id: 'i1' })]);
    renderWithProviders(<ExecutionFlowGraph traceId="T1" />);

    await waitFor(() => expect(edgeEls()).toHaveLength(1));
    expect(document.querySelectorAll('.pf-topology-connector-arrow').length).toBe(1);
  });

  it('colours an errored edge with the dg error token and a normal one without it', async () => {
    mockApi(ENTITIES, [
      mkIx({ id: 'ok', any_error: false }),
      mkIx({ id: 'bad', callee_entity_id: 'e3', any_error: true }),
    ]);
    renderWithProviders(<ExecutionFlowGraph traceId="T1" />);

    await waitFor(() => expect(edgeEls()).toHaveLength(2));
    const bad = document.querySelector('[data-id="bad"]')!.innerHTML;
    const ok = document.querySelector('[data-id="ok"]')!.innerHTML;
    // The --dg-* token, never a raw hex value.
    expect(bad).toContain('var(--dg-color-error)');
    expect(bad).not.toMatch(/#[0-9a-f]{6}/i);
    expect(ok).not.toContain('var(--dg-color-error)');
    expect(document.querySelector('.dg-graph-edge--error')).toBeInTheDocument();
  });

  // --- The edge cases, as they surface in the UI (not merely in a comment).

  it('discloses an interaction with an unresolved participant instead of dropping it silently', async () => {
    mockApi(ENTITIES, [
      mkIx({ id: 'good' }),
      mkIx({ id: 'orphan', callee_entity_id: null, summary: 'calls the unknown' }),
    ]);
    renderWithProviders(<ExecutionFlowGraph traceId="T1" />);

    await waitFor(() =>
      expect(screen.getByText(/1 interaction not shown as edges/i)).toBeInTheDocument(),
    );
    // The notice names the interaction and which end was missing, so the reader
    // can find it on the Interaction flow tab.
    expect(screen.getByText(/calls the unknown \(missing callee\)/i)).toBeInTheDocument();
    // …and the drawable one is still drawn.
    expect(edgeEls()).toHaveLength(1);
  });

  it('says nothing about unresolved participants when there are none', async () => {
    mockApi(ENTITIES, [mkIx({ id: 'i1' })]);
    renderWithProviders(<ExecutionFlowGraph traceId="T1" />);

    await waitFor(() => expect(edgeEls()).toHaveLength(1));
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
      mkIx({ id: 'i1', caller_entity_id: 'e1', callee_entity_id: 'e2' }),
      mkIx({ id: 'i2', caller_entity_id: 'e1', callee_entity_id: 'e2' }),
    ]);
    renderWithProviders(<ExecutionFlowGraph traceId="T1" />);

    // Two arrows for two interactions — not one merged arrow with a count.
    await waitFor(() => expect(edgeEls()).toHaveLength(2));
    expect(screen.getByText(/1 entity pair with multiple interactions/i)).toBeInTheDocument();
    expect(screen.getByText(/arrow count matches the interaction count/i)).toBeInTheDocument();
  });

  it('renders a self-call as its own edge', async () => {
    mockApi(ENTITIES, [mkIx({ id: 'self', caller_entity_id: 'e1', callee_entity_id: 'e1' })]);
    renderWithProviders(<ExecutionFlowGraph traceId="T1" />);

    await waitFor(() => expect(edgeEls()).toHaveLength(1));
    expect(document.querySelector('[data-id="self"]')).toBeInTheDocument();
    // Not reported as unresolved, and its entity is not called isolated.
    expect(screen.queryByText(/not shown as edges/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/1 isolated entit/i)).not.toBeInTheDocument();
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
