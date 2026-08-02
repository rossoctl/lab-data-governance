import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { runInAction } from 'mobx';
// Same deep-import discipline as the component under test, and for the same
// reason — the `@patternfly/react-topology` barrel drags in subtrees whose own
// dependencies Vitest cannot resolve. See ExecutionFlowGraph.tsx's top comment.
import Point from '@patternfly/react-topology/dist/esm/geom/Point';
import { Visualization } from '@patternfly/react-topology/dist/esm/Visualization';
import { SELECTION_EVENT } from '@patternfly/react-topology/dist/esm/behavior/useSelection';
import { renderWithProviders } from '../test/renderWithProviders';
import { ExecutionFlowGraph, LineageGraph } from './ExecutionFlowGraph';
import type {
  Entity,
  Interaction,
  InteractionLeg,
  LineageGraphEntity,
  LineageReachability,
  LineageStatus,
} from '../types';

/**
 * The summary as the WIRE spells it, which is what a fetch stub must return.
 *
 * Deliberately not `types.LineageSummary`: that is the REDUCED shape
 * `useLineageSummary` produces (`stoppedAtSeq`, camelCase), and stubbing the reduced
 * shape would bypass the reduction and stop testing it. The snake_case here is the
 * server's, verified against a live `data-lineage-summary` response.
 */
interface WireLineageSummary {
  sources: string[];
  destinations: LineageGraphEntity[];
  status: LineageStatus;
  stopped_at_seq: number | null;
}

/**
 * Render coverage for the Execution Flow graph, deliberately scoped to what jsdom
 * can honestly assert.
 *
 * WHAT JSDOM CAN DO HERE: PatternFly topology *does* mount — the visualization
 * surface, the SVG, the node PLACEMENT, the EDGE elements (including the
 * arrowhead polygon and the per-edge colour variables) and the zoom control bar's
 * buttons all render, and are asserted below.
 *
 * NODE POSITIONS *ARE* OBSERVABLE, and are asserted below. PF emits each node's
 * placement as a plain `transform="translate(x, y)"` on its `<g data-kind="node">`
 * — no measurement involved — so the layered cell each node lands in (see
 * lib/graph's `column`/`row` and this component's `gridPosition`) can be checked
 * exactly. That is only true because the layout is fixed arithmetic on the node
 * models' own `x`/`y`; when a layout ALGORITHM owned the positions they were a
 * function of its internals and not worth asserting. EDGE BENDPOINTS are
 * assertable for the same reason — they are derived numbers on the edge model, read
 * back through `getBendpoints()` — but whether two drawn shapes visually OVERLAP is
 * NOT, since that needs real SVG geometry. No test below claims it.
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
 * DRAGGING IS NOT SIMULATED HERE, and nothing below pretends otherwise — nor is
 * the drag behavior's mere ATTACHMENT assertable, because the `dragNodeRef` it
 * supplies never reaches the DOM under jsdom. The long note above that group of
 * tests explains why (node content is view-culled) and says exactly what is
 * asserted instead: that a node moved off its grid cell keeps that position
 * across a model rebuild, loses it on a trace change, and can be put back. That
 * the GESTURE moves a node is PF's own behavior, verified by hand / in Playwright.
 *
 * So the assertions below are: it mounts, it reaches the right STATE
 * (loading/error/empty/graph), the right NUMBER of node and edge elements exist
 * with the right identities, each node sits in its derived layered cell, each edge
 * carries the bendpoints its span calls for, and the controls are present and
 * wired.
 *
 * EDGES ARE PER LEG (ADR-0025): a completed interaction mounts TWO edge elements
 * pointing opposite ways, ided `<interaction>:request` / `<interaction>:response`.
 * The fixtures below therefore carry real `legs` — with `legs: []` an interaction
 * contributes no edges at all, which is correct under this model and was the
 * silent premise the pre-leg version of this file relied on.
 *
 * TWO DESCRIBE BLOCKS, ONE COMPONENT UNDERNEATH. `ExecutionFlowGraph` (the
 * `?legs=graph` tab) and `LineageGraph` (the `?legs=lineage` tab) both render the
 * shared `EntityGraph`, differing only in whether a highlight is passed. The second
 * block's first few cases are therefore anti-FORK guards — same node/edge counts,
 * same ids, same layered cells, same controls — because the whole point of the
 * refactor was that there is one implementation to keep working, not two.
 *
 * The highlight itself is asserted through the EDGES only. Node CONTENT is culled
 * here (see the drag note below), so a node's highlight className never reaches the
 * DOM and asserting on it would pass or fail for the wrong reason; edge content is
 * not culled. The visual DIMMING is not asserted at all — jsdom applies no
 * stylesheet rules to computed style, so that is Playwright / by-hand territory and
 * a test claiming it would be lying. What IS asserted is the class the stylesheet
 * keys on, plus the WORDS each of the three lineage-absence states puts on screen,
 * which is the part a picture cannot carry.
 *
 * EDGE CLICKS ARE GENUINELY TESTABLE HERE, and that is a consequence of the same
 * "edge content is not culled" fact. `DefaultEdge` renders a real
 * `<g data-test-id="edge-handler" onClick={onSelect}>`, so dispatching a click at it
 * runs the actual `withSelection` → `SELECTION_EVENT` → callback path this component
 * wires — no simulation and no stand-in. Three limits are respected below rather
 * than papered over:
 *
 *   - `fireEvent.click`, NOT `userEvent.click`. `userEvent` dispatches a full
 *     pointer sequence including `mousedown`, which reaches the pan/zoom behavior's
 *     d3-zoom listener on the surface — and d3-zoom's `defaultExtent` reads
 *     `svg.width.baseVal`, which jsdom does not implement, so it throws an unhandled
 *     `TypeError` that Vitest reports as an error for the whole FILE. The click is
 *     the only event the edge handler reads (PF binds `onClick`), so dispatching
 *     exactly that is both sufficient and the honest way to avoid an unrelated
 *     library's jsdom gap. (The zoom-button cases above still use `userEvent`
 *     because they click real HTML buttons, nowhere near the SVG surface.)
 *   - THE HIT AREA IS NOT MEASURED. PF gives the edge a
 *     `.pf-topology__edge__background` path at `stroke-width: 10px` with a
 *     transparent stroke, which is what makes a 1.5px arrow clickable in a browser.
 *     jsdom does no hit-testing at all — it dispatches wherever it is told — so a
 *     test here can only assert that the wide path EXISTS, never that a click 4px
 *     off the line lands. The width itself is a Playwright / by-hand fact.
 *   - THE BACKGROUND RECT IS ZERO-SIZED. `GraphComponent` sizes its
 *     click-to-deselect `<rect>` from `graph.getBounds()`, which is zero on an
 *     unmeasured surface. The rect is still in the DOM with its handler attached, and
 *     since jsdom ignores geometry when dispatching, the deselect path IS exercised
 *     below — but only because dispatch is geometry-blind. That a reader can actually
 *     HIT it needs a real layout.
 *
 * The SELECTED treatment is asserted as the class and the model `data`, never as a
 * computed style, for exactly the reason the dimming is not: no stylesheet applies
 * here.
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

/**
 * A node's drawn position, read off the `transform="translate(x, y)"` PF puts on
 * the node's own `<g>`. No measurement, so this is real in jsdom — see the header.
 */
function nodeAt(id: string): { x: number; y: number } | null {
  const t = document.querySelector(`[data-kind="node"][data-id="${id}"]`)?.getAttribute('transform');
  const m = /translate\((-?[\d.]+),\s*(-?[\d.]+)\)/.exec(t ?? '');
  return m ? { x: Number(m[1]), y: Number(m[2]) } : null;
}

/**
 * The bendpoints PF stored on one edge's model, as plain `{x, y}` — the derived
 * ROUTE, read back off the real `Visualization` the component built.
 *
 * A model-level read, deliberately, and the honest limit of what jsdom offers: the
 * bendpoint is a number the component computed, so it IS observable, whereas
 * whether the resulting ARC visually clears a node needs real SVG geometry and is
 * never claimed here (see this file's header).
 *
 * STILL THE RIGHT READ AFTER THE CURVE CHANGE, which is worth saying because the
 * drawn shape is no longer a polyline through these points. The bendpoint remains the
 * one stored notion of curvature — `CurvedEdge` derives its arc from exactly this
 * number and nothing else — so every case below that asserts on `bendsOf` is
 * asserting the same fact it always was. The separate question of whether the arc
 * actually passes through the point (rather than at half the offset, as a naive
 * quadratic would) is a DIFFERENT fact, and is pinned on the emitted `d` by the
 * curved-edge cases rather than here.
 */
function bendsOf(id: string): Array<{ x: number; y: number }> {
  const edge = capturedController?.getEdgeById(id);
  if (!edge) throw new Error(`no edge ${id} on the graph`);
  return edge.getBendpoints().map((p) => ({ x: p.x, y: p.y }));
}

/**
 * Click one edge, the way a reader does: on the `<g>` `DefaultEdge` binds its
 * `onClick` to.
 *
 * `data-test-id="edge-handler"` is PF's own attribute on that group (note the
 * hyphenated spelling — it is not the `data-testid` RTL looks for), so this targets
 * the element that actually carries the handler rather than the outer wrapper, which
 * has none. `fireEvent`, not `userEvent` — see this file's header for the d3-zoom
 * reason.
 */
function clickEdge(id: string) {
  const handler = document.querySelector(`[data-id="${id}"] [data-test-id="edge-handler"]`);
  if (!handler) throw new Error(`no clickable handler on edge ${id}`);
  // Wrapped in `act` because the click writes PF's mobx selection state, which
  // re-renders the observer components PF wraps its edge parts in — an update React
  // otherwise warns was not wrapped. `fireEvent` does batch its own dispatch, but the
  // mobx reaction lands outside that batch.
  act(() => {
    fireEvent.click(handler);
  });
}

/**
 * Click the graph's own background — the deselect target.
 *
 * `GraphComponent` renders it as the first `<rect>` inside the graph element's `<g>`,
 * bound to the graph's own `onSelect`. Zero-sized under jsdom (see the header), which
 * is why this dispatches at it directly instead of clicking at a coordinate.
 */
function clickBackground() {
  const rect = document.querySelector('[data-kind="graph"] > rect');
  if (!rect) throw new Error('no graph background rect to click');
  // `act` for the same reason as `clickEdge` — the mobx selection write re-renders
  // PF's observer components outside `fireEvent`'s own batch.
  act(() => {
    fireEvent.click(rect);
  });
}

/**
 * One edge's `data`, read off the model PF holds — the `isSelected` flag and the
 * highlight role as the component actually baked them in.
 *
 * The model-level companion to the className assertions: the class is what the
 * stylesheet keys on, this is what the component decided. Both are asserted because
 * a bug can live in either — a correct decision emitted under the wrong class name
 * would show here and not there, and vice versa.
 */
function edgeData(id: string): {
  isSelected: boolean;
  highlight: string;
  interactionId: string;
  lineage: { isUpstream: boolean; isDownstream: boolean };
} {
  const edge = capturedController?.getEdgeById(id);
  if (!edge) throw new Error(`no edge ${id} on the graph`);
  return edge.getData() as {
    isSelected: boolean;
    highlight: string;
    interactionId: string;
    lineage: { isUpstream: boolean; isDownstream: boolean };
  };
}

/**
 * One node's `data`, read off the model PF holds.
 *
 * THE ONLY HONEST WAY to assert a node's lineage treatment in jsdom. A node's inner
 * `<g>` renders EMPTY on a zero-size surface — PF culls node content at that scale
 * (edge content is not culled), so the `dg-graph-node--datasource` className the
 * stylesheet keys on is not in the DOM to query. The className is built directly from
 * these fields, so asserting them is asserting the decision; asserting the class
 * would silently pass-or-fail for the wrong reason. The class-to-fact mapping itself
 * is a one-line ternary chain reviewed by eye and covered visually by hand.
 */
function nodeData(id: string): {
  kind: string;
  highlight: string;
  lineage: {
    isDataSource: boolean;
    /** The ONE source being traced — a refinement of `isDataSource`, never a substitute. */
    isChosenSource: boolean;
    isUpstream: boolean;
    isDownstream: boolean;
    isFrontier: boolean;
    hops: number | null;
  };
} {
  const node = capturedController?.getNodeById(id);
  if (!node) throw new Error(`no node ${id} on the graph`);
  return node.getData() as ReturnType<typeof nodeData>;
}

/**
 * The layered grid's own arithmetic, restated for the test at the values the
 * component uses. Deliberately NOT imported from the component — those constants
 * are not exported, and a test that imported them would assert
 * `col * STEP === col * STEP`, i.e. nothing. Hard-coding is what makes a change to
 * the pitch a change this test notices.
 *
 * `cell(column, row)` — column = call DEPTH (across), row = CHRONOLOGY within that
 * depth (down). This replaced a `slotAt(i)` that took one first-encounter index and
 * stepped BOTH axes by it: that put every node on a single diagonal, which is what
 * made column-skipping edges draw through their neighbours and made "B above C"
 * inexpressible. A two-argument helper is the shape of the fix — a test can now
 * say two nodes share a column and differ only in row, which is the user's own
 * requirement, and could not be written against the old one-axis helper at all.
 */
const GRID_ORIGIN = 40;
const STEP_X = 220;
const STEP_Y = 96;
const cell = (column: number, row: number) => ({
  x: GRID_ORIGIN + column * STEP_X,
  y: GRID_ORIGIN + row * STEP_Y,
});

/**
 * A plain 2-D point, for the curve assertions below.
 *
 * Declared here rather than imported from the component, on the same principle as
 * everything else in this file: the tests restate the shapes they expect instead of
 * borrowing them, so a change to the component's own types cannot make an assertion
 * vacuous.
 */
interface XY {
  readonly x: number;
  readonly y: number;
}

/**
 * The `Visualization` the component under test built, captured by spying on the
 * one method it is guaranteed to call on it.
 *
 * The component owns its controller in `useState` and exposes no seam for it —
 * correctly, since nothing in the app needs one and a test-only prop would be a
 * production API existing solely for a test. Spying on the prototype reaches it
 * without adding one, and captures the REAL instance rather than a stand-in, so
 * what the tests then move is the actual graph on screen.
 */
let capturedController: Visualization | null = null;

/**
 * How many times the component has pushed a model, counted by the same `fromModel`
 * spy that captures the controller.
 *
 * Exists for one case — that an unmemoised callback prop does NOT cause a push (see
 * it) — and counted here rather than read off the spy's own `mock.calls`, because
 * `vi.spyOn` is re-installed per test and the mock handle is not in scope where the
 * assertion lives. A plain counter reset in `beforeEach` is the same fact with no
 * reach-through.
 */
let modelPushes = 0;

/**
 * Move a node the way PF's own drag behavior does at the end of a gesture:
 * `Node.setPosition`. This is NOT a simulated drag (see the header) — it is the
 * effect a drag has, applied directly, which is the part this component has to
 * cope with.
 */
function moveNode(id: string, x: number, y: number) {
  const node = capturedController?.getNodeById(id);
  if (!node) throw new Error(`no node ${id} on the graph`);
  // mobx runs in strict mode here, so the mutation has to be inside an action —
  // the same wrapper PF's own drag handler uses.
  runInAction(() => node.setPosition(new Point(x, y)));
}

/**
 * Render the graph with a QueryClient the test can reach, so it can force the two
 * reads to REFETCH.
 *
 * Needed because the interesting re-render is "the same trace's data came back
 * changed" — a poll — and `useEntities`/`useInteractions` are keyed on `traceId`
 * alone. A bare `rerender()` with a different `mockApi` therefore changes nothing:
 * React Query serves the cache, `spec` keeps its identity and the model effect
 * never re-runs, so a test written that way would pass without exercising
 * anything. Invalidating is what actually reproduces the poll.
 *
 * Not folded into the shared `renderWithProviders` — this is the only file that
 * needs the handle, and widening a helper eight other test files use for one
 * caller is the wrong trade.
 */
function renderGraph(traceId: string) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const view = render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ExecutionFlowGraph traceId={traceId} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return {
    ...view,
    /** Re-run both reads against whatever `mockApi` now returns, as a poll would. */
    poll: () => client.invalidateQueries(),
  };
}

describe('ExecutionFlowGraph', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn());
    capturedController = null;
    modelPushes = 0;
    // `fromModel` is the one method the component always calls on its controller,
    // so it is the reliable capture point. The spy DELEGATES to the real method —
    // it observes which instance was used and changes nothing, so every other
    // assertion in this file is unaffected by its presence.
    //
    // `capture(this)` rather than `capturedController = this`: an assignment from
    // `this` trips `@typescript-eslint/no-this-alias`, and passing the receiver to a
    // named function says what is happening more plainly than a disable comment
    // would.
    const real = Visualization.prototype.fromModel;
    const capture = (vis: Visualization) => {
      capturedController = vis;
      modelPushes += 1;
    };
    vi.spyOn(Visualization.prototype, 'fromModel').mockImplementation(function (
      this: Visualization,
      ...args: Parameters<Visualization['fromModel']>
    ) {
      capture(this);
      return real.apply(this, args);
    });
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

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
    // The drawn PATH is the honest observable: it is `M<start> … L<end>`, and the
    // layered grid really does place the nodes at distinct coordinates under jsdom
    // (only TEXT measurement is unavailable), so the two legs' paths must start
    // and end at swapped points.
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

  /* -------------------------------------------------------------------------
     CURVED EDGES (`CurvedEdge` in the component — the fork of PF's `DefaultEdge`
     that draws a smooth arc instead of a polyline).

     WHAT IS AND IS NOT ASSERTABLE HERE, because the temptation to overclaim is
     strong for a change whose whole point is how something LOOKS. jsdom has no SVG
     layout and does no hit-testing, so:
       - NO test below claims the curve is "smooth", that it looks better, or that it
         visually clears a node. Those are Playwright / by-hand facts.
       - What IS observable is the `d` attribute — a string the component computed —
         and the transform PF puts on the arrowhead. Both are pure arithmetic on
         numbers the model already holds, so they are checked exactly.

     The arithmetic is duplicated in the assertions on purpose (`quadAt` below
     re-derives a Bézier sample rather than importing the component's helper): a test
     that called the same function the component does would pass for any consistent
     pair of bugs. Bernstein coefficients written out independently is the cheapest
     real check available.
     ------------------------------------------------------------------------- */

  /** The `d` of one edge's VISIBLE link. */
  const linkD = (id: string) =>
    document.querySelector(`[data-id="${id}"] .pf-topology__edge__link`)!.getAttribute('d')!;

  /** The `d` of one edge's transparent ~10px HIT BAND. */
  const bandD = (id: string) =>
    document.querySelector(`[data-id="${id}"] .pf-topology__edge__background`)!.getAttribute('d')!;

  /**
   * Parse a one-arc path (`M x y Qcx cy x y`) into its three points.
   *
   * Deliberately strict about the COMMAND LETTERS, not just the numbers: the thing
   * being tested is that a `Q` is emitted at all, so a parser that accepted `L` here
   * would let the whole change regress silently.
   */
  function parseQuad(d: string): { from: XY; control: XY; to: XY } {
    const m =
      /^M(-?[\d.]+) (-?[\d.]+) Q(-?[\d.]+) (-?[\d.]+) (-?[\d.]+) (-?[\d.]+)$/.exec(d.trim());
    if (!m) throw new Error(`not a single-quadratic path: ${d}`);
    const n = m.slice(1).map(Number);
    return {
      from: { x: n[0]!, y: n[1]! },
      control: { x: n[2]!, y: n[3]! },
      to: { x: n[4]!, y: n[5]! },
    };
  }

  /** A quadratic Bézier sampled at `t`, from first principles. */
  const quadAt = (from: XY, c: XY, to: XY, t: number): XY => ({
    x: (1 - t) * (1 - t) * from.x + 2 * (1 - t) * t * c.x + t * t * to.x,
    y: (1 - t) * (1 - t) * from.y + 2 * (1 - t) * t * c.y + t * t * to.y,
  });

  /** The rotation, in degrees, PF baked into one edge's arrowhead transform. */
  function arrowAngle(id: string): number {
    const t = document
      .querySelector(`[data-id="${id}"] .pf-topology-connector-arrow`)!
      .getAttribute('transform')!;
    const m = /rotate\((-?[\d.]+)\)/.exec(t);
    if (!m) throw new Error(`no rotate() in arrow transform: ${t}`);
    return Number(m[1]);
  }

  /** PF's own convention for the angle of `start`→`end`, so the two are comparable. */
  const chordAngle = (from: XY, to: XY) =>
    180 - (Math.atan2(to.y - from.y, from.x - to.x) * 180) / Math.PI;

  it('draws every edge as a CURVE, not a polyline — a Q command, no L segments', async () => {
    // The headline change. PF's `DefaultEdge` emits `M… L… L…` and offers no seam to
    // change it (hence the fork), so the presence of `Q` and the ABSENCE of `L` is
    // exactly the substitution having taken effect — on both legs, since the user's
    // requirement is one renderer for every edge rather than a curve-only-when-bowed
    // hybrid.
    mockApi(ENTITIES, [mkIx({ id: 'i1' })]);
    renderWithProviders(<ExecutionFlowGraph traceId="T1" />);

    await waitFor(() => expect(edgeEls()).toHaveLength(2));
    for (const id of ['i1:request', 'i1:response']) {
      expect(linkD(id)).toContain('Q');
      expect(linkD(id)).not.toContain('L');
    }
  });

  it('starts and ends the curve at the same anchors the straight line used', async () => {
    // The curve must not move the endpoints — an arc that left the node discs would
    // be a different defect from the one being fixed.
    //
    // Bounded by the node RADIUS, not equal to the cell centre, and the distinction is
    // real rather than pedantic: `BaseEdge.getStartPoint` resolves to
    // `sourceAnchor.getLocation(…)`, which puts the endpoint on the node's ELLIPSE
    // BOUNDARY facing the other end — so it is offset from the centre by up to the
    // radius, and on this fixture actually is (the curve leaves at (60,60) from a cell
    // centred at (40,40)). The same bound the existing "keeps both legs attached to a
    // node that has moved" case uses, for the same reason. "The arc still touches the
    // node" is exactly what a radius-bounded check says.
    mockApi(ENTITIES, [mkIx({ id: 'i1', caller_entity_id: 'e1', callee_entity_id: 'e2' })]);
    renderWithProviders(<ExecutionFlowGraph traceId="T1" />);

    await waitFor(() => expect(edgeEls()).toHaveLength(2));
    const req = parseQuad(linkD('i1:request'));
    const resp = parseQuad(linkD('i1:response'));

    const radius = 20; // NODE_DIAMETER / 2
    const onCell = (p: XY, c: { x: number; y: number }) => {
      expect(Math.abs(p.x - c.x)).toBeLessThanOrEqual(radius);
      expect(Math.abs(p.y - c.y)).toBeLessThanOrEqual(radius);
    };
    onCell(req.from, cell(0, 0));
    onCell(req.to, cell(1, 0));

    // The two legs agree EXACTLY about where each node is — the anchor offset is not a
    // per-leg fudge, so the request's end and the response's start are the same point.
    // This is the assertion the radius bound above cannot make, and it is the one that
    // would catch a curve builder that trimmed one end and not the other.
    expect(resp.from).toEqual(req.to);
    expect(resp.to).toEqual(req.from);
  });

  it('bends the curve THROUGH the routed bendpoint, not halfway to it', async () => {
    // THE COMPENSATION, and the one piece of this change that could silently undo an
    // earlier fix. A quadratic does not pass through its control point — at t=0.5 it
    // sits halfway between the chord midpoint and the control — so using the
    // bendpoint AS the control would draw a curve with only HALF the routed
    // clearance, putting a column-skipping edge back over the nodes `edgeBendpoints`
    // exists to dodge. The component doubles the offset to compensate; this pins that
    // it did, by sampling the emitted curve at its apex and requiring the bendpoint.
    //
    // Uses the SKIPPING edge, because that is the case where the clearance is load-
    // bearing rather than merely cosmetic: e1→e3 crosses column 1, where e2 sits.
    mockApi(ENTITIES, [
      mkIx({ id: 'i1', caller_entity_id: 'e1', callee_entity_id: 'e2' }, 1),
      mkIx({ id: 'i2', caller_entity_id: 'e2', callee_entity_id: 'e3' }, 3),
      mkIx({ id: 'i3', caller_entity_id: 'e1', callee_entity_id: 'e3' }, 5),
    ]);
    renderWithProviders(<ExecutionFlowGraph traceId="T1" />);

    await waitFor(() => expect(edgeEls()).toHaveLength(6));

    const { from, control, to } = parseQuad(linkD('i3:request'));
    const apex = quadAt(from, control, to, 0.5);
    const bend = bendsOf('i3:request')[0]!;
    expect(apex.x).toBeCloseTo(bend.x, 6);
    expect(apex.y).toBeCloseTo(bend.y, 6);

    // And the apex really is off the chord — i.e. the clearance is a real detour and
    // not a rounding artefact of a curve that collapsed onto the straight line.
    const chordMidY = (from.y + to.y) / 2;
    expect(Math.abs(apex.y - chordMidY)).toBeGreaterThan(STEP_Y / 2);
  });

  it('aims the arrowhead along the CURVE, not along the straight chord', async () => {
    // The single most likely thing to look wrong on a curved edge, so it is pinned
    // numerically rather than trusted. `ConnectorArrow` derives its own `rotate()`
    // from the two points it is handed and exposes no rotation prop, so the component
    // hands it a point sampled ON the curve near the end — making PF's chord a secant
    // that approximates the tangent.
    //
    // Three angles are compared, which is what makes this a real test: the rendered
    // one must match the curve's own heading at the node and must NOT match the
    // straight start→end chord. On this fixture the chord is exactly horizontal (both
    // nodes are in row 0) while the curve arrives at a clear angle, so an un-aimed
    // arrowhead would read as flat — visible at a glance and caught here.
    mockApi(ENTITIES, [mkIx({ id: 'i1', caller_entity_id: 'e1', callee_entity_id: 'e2' })]);
    renderWithProviders(<ExecutionFlowGraph traceId="T1" />);

    await waitFor(() => expect(edgeEls()).toHaveLength(2));

    const { from, control, to } = parseQuad(linkD('i1:request'));
    const rendered = arrowAngle('i1:request');

    // The EXACT tangent direction of a quadratic at t=1 is `to - control`. The
    // component samples a secant instead (a near-tangent, for reasons in its own
    // note about PF's size back-off), so the two agree to within a couple of degrees
    // rather than exactly — the tolerance is stated as the approximation it is.
    const exactTangent = chordAngle(control, to);
    expect(Math.abs(rendered - exactTangent)).toBeLessThan(3);

    // And it is genuinely NOT the chord: the fixture's chord is flat (0°), the curve
    // arrives well off flat. Without the override PF would have aimed the head from
    // the bendpoint, which is a third, also-wrong angle — so "differs from the chord"
    // alone would not have been enough, hence the tangent check above.
    expect(Math.abs(rendered - chordAngle(from, to))).toBeGreaterThan(10);
  });

  it('makes the ~10px HIT BAND follow the curve, so a click lands where the line is drawn', async () => {
    // Edge selection depends entirely on this band (PF gives it `stroke-width: 10px;
    // stroke: transparent`, which is what makes a 1.5px arrow clickable). If it kept
    // tracing the straight chord while the link curved, every bowed edge would be
    // clickable along a line it is not drawn on and unclickable where the reader can
    // see it.
    //
    // NOT a hit test — jsdom does none, and dispatches wherever it is told (see this
    // file's header). What is asserted is that the band is the SAME ARC as the link:
    // identical control point, and ends that sit ON the link's curve. That is the
    // geometric property "the band follows the curve" means, and it is fully
    // observable as a string.
    mockApi(ENTITIES, [mkIx({ id: 'i1', caller_entity_id: 'e1', callee_entity_id: 'e2' })]);
    renderWithProviders(<ExecutionFlowGraph traceId="T1" />);

    await waitFor(() => expect(edgeEls()).toHaveLength(2));

    const band = parseQuad(bandD('i1:request'));
    const link = parseQuad(linkD('i1:request'));

    // A curve, on the same terms as the link — not a straight `L` fallback.
    expect(bandD('i1:request')).toContain('Q');
    expect(bandD('i1:request')).not.toContain('L');

    // The band's END is pulled back from the link's end (PF does this so the
    // transparent stroke does not overhang the arrowhead) but stays ON the arc: the
    // link's curve passes through it at some t < 1.
    expect(band.to).not.toEqual(link.to);
    const onLinkCurve = (p: XY) => {
      // Find the t whose sample is nearest p, and require the miss to be sub-pixel.
      let best = Infinity;
      for (let t = 0; t <= 1.0001; t += 0.0005) {
        const s = quadAt(link.from, link.control, link.to, t);
        best = Math.min(best, Math.hypot(s.x - p.x, s.y - p.y));
      }
      return best;
    };
    expect(onLinkCurve(band.to)).toBeLessThan(1);

    // The band's own apex tracks the link's, so the CLICKABLE middle of the edge is
    // the drawn middle of the edge — the part a reader actually aims at.
    const bandApex = quadAt(band.from, band.control, band.to, 0.5);
    expect(onLinkCurve(bandApex)).toBeLessThan(1);
  });

  it('keeps the arrowhead and the seq tag on a curved edge', async () => {
    // The fork must not have quietly dropped either of the two things `DefaultEdge`
    // contributed besides the path. The arrowhead renders under jsdom; the tag's TEXT
    // does not (it measures itself via getBBox — see this file's header), so the tag
    // is asserted as its `<g>`, which is what is genuinely observable.
    mockApi(ENTITIES, [mkIx({ id: 'i1' })]);
    renderWithProviders(<ExecutionFlowGraph traceId="T1" />);

    await waitFor(() => expect(edgeEls()).toHaveLength(2));
    for (const id of ['i1:request', 'i1:response']) {
      const el = document.querySelector(`[data-id="${id}"]`)!;
      expect(el.querySelector('.pf-topology-connector-arrow')).not.toBeNull();
      expect(el.querySelector('.pf-topology__edge__tag')).not.toBeNull();
    }
  });

  it("keeps PF's own hover modifier on a curved edge", async () => {
    // `pf-m-hover` is one of the state classes the fork had to reproduce, and it is the
    // one that is easy to lose silently: it comes from PF's `useHover` hook, so a fork
    // that hand-rolled `onMouseEnter` instead would look right and behave differently
    // (PF's version carries 200ms in/out delays that stop the TOP_LAYER hoist
    // flickering). Asserted here because nothing else in this file covered hover, which
    // meant the claim to have preserved it was untested.
    //
    // `useHover` attaches NATIVE listeners via a callback ref, so the event has to be a
    // real `mouseenter` — `fireEvent.mouseOver` does not trigger it. `mouseenter` is
    // also safely away from d3-zoom's `mousedown` listener, so this does not hit the
    // `svg.width.baseVal` gap that rules `userEvent` out for edges (see the header).
    mockApi(ENTITIES, [mkIx({ id: 'i1' })]);
    renderWithProviders(<ExecutionFlowGraph traceId="T1" />);

    await waitFor(() => expect(edgeEls()).toHaveLength(2));
    const handler = document.querySelector(
      '[data-id="i1:request"] [data-test-id="edge-handler"]',
    )!;
    expect(handler.classList.contains('pf-m-hover')).toBe(false);

    await act(async () => {
      fireEvent(handler, new MouseEvent('mouseenter', { bubbles: false }));
      // Past PF's 200ms `delayIn`, so the debounced state change has landed.
      await new Promise((r) => setTimeout(r, 250));
    });

    expect(
      document
        .querySelector('[data-id="i1:request"] [data-test-id="edge-handler"]')!
        .classList.contains('pf-m-hover'),
    ).toBe(true);
  });

  it('falls back to a straight line for a SELF-CALL, which has no bendpoint to curve with', async () => {
    // Self-calls are deliberately LEFT AS THEY WERE by this change, and that is worth
    // a test rather than a comment alone: with one endpoint there is no control
    // geometry, and inventing some (a self-loop arc) would be a second notion of
    // curvature beside the bendpoint — the exact thing this design avoids. So the
    // path degenerates to `M… L…` and PF's `pf-m-dashed` remains the only marker,
    // unchanged from before.
    //
    // This is the ONE place an `L` is still correct, which is why the curve tests
    // above assert `not.toContain('L')` on ordinary edges: the two cases are
    // distinguishable, and a regression that straightened everything would fail them.
    mockApi(
      [ENTITIES[0]!],
      [mkIx({ id: 'self', caller_entity_id: 'e1', callee_entity_id: 'e1' })],
    );
    renderWithProviders(<ExecutionFlowGraph traceId="T1" />);

    await waitFor(() => expect(edgeEls()).toHaveLength(2));
    expect(bendsOf('self:request')).toHaveLength(0);
    expect(linkD('self:request')).toContain('L');
    expect(linkD('self:request')).not.toContain('Q');
    // Still dashed, i.e. the marker that carries the whole meaning survived.
    expect(
      document.querySelector('[data-id="self:request"] .pf-topology__edge__link.pf-m-dashed'),
    ).not.toBeNull();
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

  // --- The layered grid. Genuinely observable here: the position is fixed
  // arithmetic on the node model's x/y (over `lib/graph`'s derived column/row) and
  // PF emits it as a plain `translate()` with no measurement involved (see this
  // file's header). What is NOT observable is whether anything visually OVERLAPS —
  // that needs a real SVG layout, so it is Playwright / by-hand territory and is
  // never claimed below. What is asserted is the cell each node was placed in, and
  // the bendpoint each edge was given, which is the model-level fact the
  // non-overlap follows from.
  //
  // The DERIVATION of the cells is proven in lib/graph.test.ts, not here. These
  // cases are about the component using it — that it reads `column`/`row` rather
  // than inventing a second notion of position.

  it("THE USER'S EXAMPLE: A calls B then A calls C draws B ABOVE C, both right of A", async () => {
    // e1 calls e2, then e1 calls e3. Both callees are one call deep, so they share a
    // COLUMN, and they are separated on the row axis by call order — e2 above e3.
    // e1 is to their left. This is the requirement stated verbatim, as pixels, and
    // it is the specific thing the diagonal staircase this replaced could not do:
    // with all three nodes on one line, e2 and e3 could only ever be at two
    // different depths.
    mockApi(ENTITIES, [
      mkIx({ id: 'i1', caller_entity_id: 'e1', callee_entity_id: 'e2' }, 1),
      mkIx({ id: 'i2', caller_entity_id: 'e1', callee_entity_id: 'e3' }, 3),
    ]);
    renderWithProviders(<ExecutionFlowGraph traceId="T1" />);

    await waitFor(() => expect(nodeEls()).toHaveLength(3));
    expect(nodeAt('e1')).toEqual(cell(0, 0));
    expect(nodeAt('e2')).toEqual(cell(1, 0));
    expect(nodeAt('e3')).toEqual(cell(1, 1));
    // Restated as the two properties a reader actually relies on, so a change to the
    // pitch cannot make this pass for the wrong reason: the callees are in the SAME
    // column (same x) and e2 is strictly ABOVE e3.
    expect(nodeAt('e2')!.x).toBe(nodeAt('e3')!.x);
    expect(nodeAt('e2')!.y).toBeLessThan(nodeAt('e3')!.y);
    // …and the caller is to the LEFT of both.
    expect(nodeAt('e1')!.x).toBeLessThan(nodeAt('e2')!.x);
  });

  it('steps RIGHT with call depth, so a chain reads left-to-right', async () => {
    // e1 calls e2, e2 calls e3 — three depths, three columns, each strictly right of
    // the last. The other half of the user's statement ("if A calls B, A can be to
    // the left of B"), stated as the property rather than the pixel values.
    mockApi(ENTITIES, [
      mkIx({ id: 'i1', caller_entity_id: 'e1', callee_entity_id: 'e2' }, 1),
      mkIx({ id: 'i2', caller_entity_id: 'e2', callee_entity_id: 'e3' }, 3),
    ]);
    renderWithProviders(<ExecutionFlowGraph traceId="T1" />);

    await waitFor(() => expect(nodeEls()).toHaveLength(3));
    const xs = ['e1', 'e2', 'e3'].map((id) => nodeAt(id)!.x);
    expect(xs[1]).toBeGreaterThan(xs[0]);
    expect(xs[2]).toBeGreaterThan(xs[1]);
    // A chain puts one node per column, so all three are on the TOP row — nothing
    // to stack. This is what distinguishes a chain from the sibling case above.
    const ys = ['e1', 'e2', 'e3'].map((id) => nodeAt(id)!.y);
    expect(new Set(ys).size).toBe(1);
  });

  it('does NOT deepen the column on a response leg', async () => {
    // The response travels back e2 → e1. If it counted as depth, e1 would be pushed
    // right of e2 and then e2 right of that, so one completed interaction would take
    // four columns instead of two. Two nodes, exactly two columns.
    mockApi(ENTITIES, [mkIx({ id: 'i1', caller_entity_id: 'e1', callee_entity_id: 'e2' })]);
    renderWithProviders(<ExecutionFlowGraph traceId="T1" />);

    await waitFor(() => expect(edgeEls()).toHaveLength(2));
    expect(nodeAt('e1')).toEqual(cell(0, 0));
    expect(nodeAt('e2')).toEqual(cell(1, 0));
  });

  it('places an entity by its DERIVED cell, not by its position in the entities read', async () => {
    // The distinguishing case. The entities read returns e1, e2, e3, but the trace
    // starts at e3 — so e3 is the column-0 entry point. If the placement were
    // reading the array index, the two would be indistinguishable in every test
    // above and this is the one that separates them.
    mockApi(ENTITIES, [
      mkIx({ id: 'i1', caller_entity_id: 'e3', callee_entity_id: 'e1' }, 1),
      mkIx({ id: 'i2', caller_entity_id: 'e1', callee_entity_id: 'e2' }, 3),
    ]);
    renderWithProviders(<ExecutionFlowGraph traceId="T1" />);

    await waitFor(() => expect(nodeEls()).toHaveLength(3));
    expect(nodeAt('e3')).toEqual(cell(0, 0));
    expect(nodeAt('e1')).toEqual(cell(1, 0));
    expect(nodeAt('e2')).toEqual(cell(2, 0));
  });

  it('puts an isolated entity in a TRAILING column, off the end of the chain', async () => {
    // No leg touches e3, so it has no call depth and has not earned column 0 (which
    // is the "the flow starts here" claim). It is parked one past the deepest real
    // column — still VISIBLE, still disclosed in the alert, but out of the readable
    // chain, matching its dashed treatment.
    mockApi(ENTITIES, [mkIx({ id: 'i1', caller_entity_id: 'e1', callee_entity_id: 'e2' })]);
    renderWithProviders(<ExecutionFlowGraph traceId="T1" />);

    await waitFor(() => expect(nodeEls()).toHaveLength(3));
    expect(nodeAt('e1')).toEqual(cell(0, 0));
    expect(nodeAt('e2')).toEqual(cell(1, 0));
    expect(nodeAt('e3')).toEqual(cell(2, 0)); // isolated → trailing column
    // Explicitly NOT sharing the entry point's column, which is the confusion the
    // trailing column exists to prevent.
    expect(nodeAt('e3')!.x).not.toBe(nodeAt('e1')!.x);
  });

  it('BOWS an adjacent-column request and its response to opposite sides', async () => {
    // THE OVERLAP BUG, pinned. An earlier version returned no bendpoint at all for
    // an adjacent-column edge, reasoning that a straight line crosses no
    // intervening cell. That is true and beside the point: A→B and B→A over one
    // column step share BOTH anchor points, so the request and the response were
    // drawn on top of each other — one line with an arrowhead at each end and the
    // two `seq` tags colliding. The user reported seeing exactly that.
    //
    // This is the COMMON case (a plain call to the entity you call), which is why
    // it mattered more than the skipping cases that were already fanned.
    mockApi(ENTITIES, [mkIx({ id: 'i1', caller_entity_id: 'e1', callee_entity_id: 'e2' })]);
    renderWithProviders(<ExecutionFlowGraph traceId="T1" />);

    await waitFor(() => expect(edgeEls()).toHaveLength(2));
    const req = bendsOf('i1:request')[0];
    const resp = bendsOf('i1:response')[0];

    // Each leg now carries exactly one bend — not zero, which was the bug.
    expect(req).toBeDefined();
    expect(resp).toBeDefined();
    // e1 and e2 are both on row 0, so the direct line is horizontal at that y.
    // One leg bows above it and the other below: two distinguishable arrows.
    const mid = cell(0, 0).y;
    expect(Math.sign(req!.y - mid)).toBe(-Math.sign(resp!.y - mid));
    expect(req!.y).not.toBe(resp!.y);
    // Both bend at the span's midpoint in x — the bow is vertical only, so each
    // leg still reads as the direct connection it is.
    expect(req!.x).toBe(resp!.x);
  });

  it('keeps the adjacent-column bow inside the row gutter', async () => {
    // The bow separates the pair; it must NOT wander so far that it enters the
    // neighbouring row's band, where it would read as pointing at a different
    // entity. Two rows are ROW_STEP_Y apart, so a bow of less than half that stays
    // in its own lane — pinned as a relationship rather than against the literal
    // 18, so retuning the constant cannot silently break the invariant.
    mockApi(ENTITIES, [
      mkIx({ id: 'i1', caller_entity_id: 'e1', callee_entity_id: 'e2' }, 1),
      // A second callee, so there IS a row 1 for the bow to intrude into.
      mkIx({ id: 'i2', caller_entity_id: 'e1', callee_entity_id: 'e3' }, 3),
    ]);
    renderWithProviders(<ExecutionFlowGraph traceId="T1" />);

    await waitFor(() => expect(edgeEls()).toHaveLength(4));
    const rowPitch = cell(0, 1).y - cell(0, 0).y;
    for (const id of ['i1:request', 'i1:response']) {
      const bend = bendsOf(id)[0]!;
      expect(Math.abs(bend.y - cell(0, 0).y)).toBeLessThan(rowPitch / 2);
    }
  });

  it('routes a COLUMN-SKIPPING edge around the cells it would otherwise cross', async () => {
    // THE regression this rewrite is about. e1 calls e2, e2 calls e3, and e1 ALSO
    // calls e3 — so e1 sits at column 0 and e3 at column 2, and the direct e1→e3
    // line passes over column 1 where e2 is. It must be given a bendpoint so it
    // detours instead.
    //
    // Asserted at the MODEL level (`getBendpoints()`), not as a claim about pixels
    // overlapping: jsdom has no SVG layout, so actual visual non-overlap is
    // unobservable here and a test claiming it would be lying. What is observable is
    // that the skipping edge's vertex is off the straight line between its
    // endpoints, and that it detours MUCH further than an adjacent-column leg's
    // separating bow does.
    //
    // Note this used to assert the adjacent legs carried NO vertex. They now carry a
    // small one (see the overlap test above — a straight adjacent pair drew on top of
    // itself), so the distinction between the two cases is the SIZE of the offset,
    // not its presence: the skipping edge clears a whole row band, the adjacent bow
    // only has to separate two arrows.
    mockApi(ENTITIES, [
      mkIx({ id: 'i1', caller_entity_id: 'e1', callee_entity_id: 'e2' }, 1),
      mkIx({ id: 'i2', caller_entity_id: 'e2', callee_entity_id: 'e3' }, 3),
      mkIx({ id: 'i3', caller_entity_id: 'e1', callee_entity_id: 'e3' }, 5),
    ]);
    renderWithProviders(<ExecutionFlowGraph traceId="T1" />);

    await waitFor(() => expect(edgeEls()).toHaveLength(6));
    // The skipping leg (column 0 → column 2) detours.
    expect(bendsOf('i3:request')).toHaveLength(1);
    // …far further than an adjacent leg's separating bow, which is what makes the
    // detour read as routing AROUND something rather than as a pair being fanned.
    const adjacentBow = Math.abs(bendsOf('i1:request')[0]!.y - cell(0, 0).y);
    const skipDetour = Math.abs(bendsOf('i3:request')[0]!.y - cell(0, 0).y);
    expect(skipDetour).toBeGreaterThan(adjacentBow);
    // …and the detour really is OFF the direct line, which is what "routes around"
    // means. e1 and e3 are both on row 0, so the straight line is horizontal at that
    // y — a bend at the same y would be no detour at all.
    expect(bendsOf('i3:request')[0]?.y).not.toBe(cell(0, 0).y);
  });

  it('sends the two legs of one skipping pair to OPPOSITE sides', async () => {
    // Same-pair edges used to draw exactly on top of each other (the staircase drew
    // straight anchor-to-anchor lines and the request/response pair retraced one
    // line). The bendpoint side alternates on the leg's seq parity, so the two halves
    // of a round trip bow apart instead.
    mockApi(ENTITIES, [
      mkIx({ id: 'i1', caller_entity_id: 'e1', callee_entity_id: 'e2' }, 1),
      mkIx({ id: 'i2', caller_entity_id: 'e2', callee_entity_id: 'e3' }, 3),
      // Legs at seq 5 and 6 — opposite parities, so opposite sides.
      mkIx({ id: 'i3', caller_entity_id: 'e1', callee_entity_id: 'e3' }, 5),
    ]);
    renderWithProviders(<ExecutionFlowGraph traceId="T1" />);

    await waitFor(() => expect(edgeEls()).toHaveLength(6));
    const req = bendsOf('i3:request')[0]!;
    const resp = bendsOf('i3:response')[0]!;
    const mid = cell(0, 0).y; // both endpoints are on row 0
    // One above the direct line, one below — so they are two distinguishable arrows.
    expect(Math.sign(req.y - mid)).toBe(-Math.sign(resp.y - mid));
    expect(req.y).not.toBe(resp.y);
  });

  it('routes a SAME-COLUMN edge sideways, not down through the column', async () => {
    // Two entities in one column with an edge between them: a straight vertical line
    // could be drawn over whatever sits between their rows. The gutter here is
    // horizontal, so the bend goes off to the SIDE of the column rather than above or
    // below it.
    //
    // Reaching this case takes a REQUEST CYCLE, and that is a fact about the layout
    // rather than an awkward fixture: the column rule is longest-path, so a plain
    // request edge always lands its target strictly right of its source and can never
    // be within-column. Only where the `n - 1` column ceiling binds — on a cycle —
    // do two request-connected nodes share a column. e1 → e2, e2 → e3, e3 → e1 is a
    // three-node cycle; the ceiling is 2, so e3's request back to e1 cannot deepen
    // e1 and stays inside the span.
    mockApi(ENTITIES, [
      mkIx({ id: 'i1', caller_entity_id: 'e1', callee_entity_id: 'e2' }, 1),
      mkIx({ id: 'i2', caller_entity_id: 'e2', callee_entity_id: 'e3' }, 3),
      mkIx({ id: 'i3', caller_entity_id: 'e3', callee_entity_id: 'e1' }, 5),
    ]);
    renderWithProviders(<ExecutionFlowGraph traceId="T1" />);

    await waitFor(() => expect(edgeEls()).toHaveLength(6));
    // The chain still reads left-to-right; the cycle's closing leg is the backwards
    // one, which is the best a layered layout can do with a cycle.
    expect(nodeAt('e1')).toEqual(cell(0, 0));
    expect(nodeAt('e2')).toEqual(cell(1, 0));
    expect(nodeAt('e3')).toEqual(cell(2, 0));
    // e3 → e1 spans two columns backwards, so it is a SKIPPING edge and detours
    // vertically past e2 rather than being drawn over it.
    const back = bendsOf('i3:request');
    expect(back).toHaveLength(1);
    expect(back[0]?.y).not.toBe(cell(0, 0).y);
  });

  it('bows a WITHIN-COLUMN edge out to the side of its column', async () => {
    // The same-column branch, on the smallest graph that actually reaches it. Two
    // request-connected nodes can only share a column where the `n - 1` ceiling binds,
    // and this cyclic shape is the minimal one: e1 → e3, e2 → e1, e3 → e2 relaxes to
    // columns 1, 2, 2 (the ceiling is 2), so e3 → e2 runs WITHIN column 2.
    //
    // Found by enumerating the small digraphs rather than guessed at, because the
    // longest-path rule means an ordinary request edge NEVER lands within a column —
    // which is itself the point: this branch handles the residue a cycle leaves, not
    // an everyday shape.
    mockApi(ENTITIES, [
      mkIx({ id: 'a', caller_entity_id: 'e1', callee_entity_id: 'e3' }, 1),
      mkIx({ id: 'b', caller_entity_id: 'e2', callee_entity_id: 'e1' }, 3),
      mkIx({ id: 'c', caller_entity_id: 'e3', callee_entity_id: 'e2' }, 5),
    ]);
    renderWithProviders(<ExecutionFlowGraph traceId="T1" />);

    await waitFor(() => expect(edgeEls()).toHaveLength(6));
    // The premise: e2 and e3 share a column, stacked on different rows.
    expect(nodeAt('e2')!.x).toBe(nodeAt('e3')!.x);
    expect(nodeAt('e2')!.y).not.toBe(nodeAt('e3')!.y);

    const bend = bendsOf('c:request');
    expect(bend).toHaveLength(1);
    // Off to the SIDE — the bend leaves the column's own x, so the arrow bows around
    // whatever sits between the two rows instead of running down through it…
    expect(bend[0]?.x).not.toBe(nodeAt('e2')!.x);
    // …and stays BETWEEN the two rows vertically, rather than looping past either end.
    expect(bend[0]?.y).toBe((nodeAt('e2')!.y + nodeAt('e3')!.y) / 2);
  });

  it('leaves a SELF-call unrouted — a bendpoint cannot help a single point', async () => {
    // Both ends are the same node, so there is no line for a detour to leave. The
    // dashed style is what marks it (asserted in the self-call case above).
    mockApi(ENTITIES, [mkIx({ id: 'self', caller_entity_id: 'e1', callee_entity_id: 'e1' })]);
    renderWithProviders(<ExecutionFlowGraph traceId="T1" />);

    await waitFor(() => expect(edgeEls()).toHaveLength(2));
    expect(bendsOf('self:request')).toHaveLength(0);
    expect(bendsOf('self:response')).toHaveLength(0);
  });

  it('redraws the identical picture on a remount — the layout is deterministic', async () => {
    // A reader reloading the tab must not get a different arrangement. Nothing in
    // the placement is random, iteration-order dependent, or time dependent, and
    // this is the guard that keeps it that way.
    mockApi(ENTITIES, [
      mkIx({ id: 'i1', caller_entity_id: 'e2', callee_entity_id: 'e3' }, 1),
      mkIx({ id: 'i2', caller_entity_id: 'e3', callee_entity_id: 'e1' }, 3),
    ]);
    const first = renderWithProviders(<ExecutionFlowGraph traceId="T1" />);
    await waitFor(() => expect(nodeEls()).toHaveLength(3));
    const before = ['e1', 'e2', 'e3'].map((id) => nodeAt(id));
    first.unmount();

    renderWithProviders(<ExecutionFlowGraph traceId="T1" />);
    await waitFor(() => expect(nodeEls()).toHaveLength(3));
    expect(['e1', 'e2', 'e3'].map((id) => nodeAt(id))).toEqual(before);
  });


  // --- Edge selection: clicking an arrow selects its parent INTERACTION.
  //
  // The real path, not a stand-in: PF's `withSelection` binds `onSelect` as the
  // `onClick` of the `<g data-test-id="edge-handler">` `DefaultEdge` renders, and
  // this component subscribes to the `SELECTION_EVENT` that handler fires. Every
  // case below dispatches at that real element (see `clickEdge`) and asserts on the
  // callback, the model `data` and the classNames — never on a computed style or a
  // pixel, since neither exists here (this file's header says exactly which limits
  // apply and why).
  //
  // A LEG'S EDGE SELECTS ITS PARENT INTERACTION, which is FlatLegsTable's contract
  // rather than a new one — legs have no selection of their own — so the callback
  // carries an interaction id and BOTH legs of that interaction take the treatment.

  it('reports the parent INTERACTION when an edge is clicked, not the leg', async () => {
    // The contract. `i1:request` is one LEG; what the reader selected is `i1`, because
    // that is what the detail panel is about and what `?iid` names.
    const onSelect = vi.fn();
    mockApi(ENTITIES, [mkIx({ id: 'i1', caller_entity_id: 'e1', callee_entity_id: 'e2' })]);
    renderWithProviders(<ExecutionFlowGraph traceId="T1" onSelectInteraction={onSelect} />);
    await waitFor(() => expect(edgeEls()).toHaveLength(2));

    clickEdge('i1:request');

    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect).toHaveBeenCalledWith('i1');
  });

  it('reports the SAME interaction from either of its two legs', async () => {
    // The response leg is a separate edge with its own id, and clicking it must not
    // select something different — there is one interaction behind both arrows.
    const onSelect = vi.fn();
    mockApi(ENTITIES, [mkIx({ id: 'i1', caller_entity_id: 'e1', callee_entity_id: 'e2' })]);
    renderWithProviders(<ExecutionFlowGraph traceId="T1" onSelectInteraction={onSelect} />);
    await waitFor(() => expect(edgeEls()).toHaveLength(2));

    clickEdge('i1:response');

    expect(onSelect).toHaveBeenCalledWith('i1');
  });

  it('distinguishes the two interactions in a parallel channel', async () => {
    // Two interactions between the SAME pair put four arrows in one visual channel.
    // The click has to identify which interaction was hit, not merely which pair —
    // otherwise the whole feature collapses on exactly the traces where a reader most
    // needs it.
    const onSelect = vi.fn();
    mockApi(ENTITIES, [
      mkIx({ id: 'i1', caller_entity_id: 'e1', callee_entity_id: 'e2' }, 1),
      mkIx({ id: 'i2', caller_entity_id: 'e1', callee_entity_id: 'e2' }, 3),
    ]);
    renderWithProviders(<ExecutionFlowGraph traceId="T1" onSelectInteraction={onSelect} />);
    await waitFor(() => expect(edgeEls()).toHaveLength(4));

    clickEdge('i2:response');
    expect(onSelect).toHaveBeenLastCalledWith('i2');
    clickEdge('i1:request');
    expect(onSelect).toHaveBeenLastCalledWith('i1');
  });

  it('offers a WIDE hit path on every edge, so a 1.5px arrow is not the click target', async () => {
    // PF's `.pf-topology__edge__background` — a transparent 10px stroke tracing the
    // same route, INSIDE the clickable `<g>`. Its existence is the honest assertable:
    // jsdom does no hit-testing, so this can never claim that a click 4px off the line
    // lands (that is a browser fact — see the header). What it does claim is that the
    // wide band is present on both legs, which is the mechanism, and that we did not
    // hand-roll a second hit target competing with PF's.
    mockApi(ENTITIES, [mkIx({ id: 'i1', caller_entity_id: 'e1', callee_entity_id: 'e2' })]);
    renderWithProviders(<ExecutionFlowGraph traceId="T1" />);
    await waitFor(() => expect(edgeEls()).toHaveLength(2));

    for (const id of ['i1:request', 'i1:response']) {
      const handler = document.querySelector(`[data-id="${id}"] [data-test-id="edge-handler"]`)!;
      // The wide path is a CHILD of the element carrying the click handler, which is
      // what makes a click on it bubble to that handler.
      expect(handler.querySelector('.pf-topology__edge__background')).not.toBeNull();
    }
    // …and no hand-rolled strip of our own, which would be a second, competing target
    // (the sequence diagram needs one only because it is hand-rolled SVG with no such
    // layer of its own).
    expect(document.querySelector('.dg-seq-row-hit')).toBeNull();
  });

  it('marks the edge as selectable so the cursor says it is clickable', async () => {
    // `DefaultEdge` applies `pf-m-selected` for itself but never `pf-m-selectable`
    // (its sibling `TaskEdge` does) — and that class is what flips PF's
    // `--edge--cursor` from `default` to `pointer`. An arrow nobody guesses is a
    // target is a feature nobody uses.
    mockApi(ENTITIES, [mkIx({ id: 'i1', caller_entity_id: 'e1', callee_entity_id: 'e2' })]);
    renderWithProviders(<ExecutionFlowGraph traceId="T1" />);
    await waitFor(() => expect(edgeEls()).toHaveLength(2));

    expect(document.querySelectorAll('.dg-graph-edge.pf-m-selectable')).toHaveLength(2);
  });

  it('gives BOTH legs of the selected interaction the selected treatment', async () => {
    // The selection is the INTERACTION, so lighting only the clicked arrow would tell
    // the reader that legs are separately selectable — which they are not, here or in
    // the Flat table. Same rule the Interaction diagram follows for its two messages.
    mockApi(ENTITIES, [
      mkIx({ id: 'i1', caller_entity_id: 'e1', callee_entity_id: 'e2' }, 1),
      mkIx({ id: 'i2', caller_entity_id: 'e1', callee_entity_id: 'e3' }, 3),
    ]);
    renderWithProviders(<ExecutionFlowGraph traceId="T1" selectedInteractionId="i1" />);
    await waitFor(() => expect(edgeEls()).toHaveLength(4));

    // Both of i1's legs carry the class the stylesheet keys on…
    expect(document.querySelector('[data-id="i1:request"] .dg-graph-edge--selected')).not.toBeNull();
    expect(document.querySelector('[data-id="i1:response"] .dg-graph-edge--selected')).not.toBeNull();
    // …and exactly those two, so the OTHER interaction's arrows are untouched.
    expect(document.querySelectorAll('.dg-graph-edge--selected')).toHaveLength(2);
    expect(document.querySelector('[data-id="i2:request"] .dg-graph-edge--selected')).toBeNull();
    // The model-level decision agrees with the emitted class (see `edgeData`).
    expect(edgeData('i1:request').isSelected).toBe(true);
    expect(edgeData('i1:response').isSelected).toBe(true);
    expect(edgeData('i2:request').isSelected).toBe(false);
  });

  it('brings the selected legs\' seq tags out of the mute with their arrows', async () => {
    // The tag is muted by default because an ordinal is a reference rather than
    // content — but for the one interaction the reader has open, the number is how they
    // cross-reference the arrow against the Flat tab's rows and the panel's fields.
    mockApi(ENTITIES, [mkIx({ id: 'i1', caller_entity_id: 'e1', callee_entity_id: 'e2' })]);
    renderWithProviders(<ExecutionFlowGraph traceId="T1" selectedInteractionId="i1" />);
    await waitFor(() => expect(edgeEls()).toHaveLength(2));

    expect(document.querySelectorAll('.dg-graph-edge-tag--selected')).toHaveLength(2);
  });

  it('does NOT use PF\'s own pf-m-selected, which is per-leg and click-only', async () => {
    // WHY THE OBVIOUS WIRING WAS REJECTED, pinned as a test because the failure mode is
    // silent. `withSelection` injects a `selected` prop and `DefaultEdge` would turn it
    // into `pf-m-selected` — but PF's selection is per-ELEMENT and populated only by its
    // own click handler, so it marks the ONE clicked arrow (not its sibling leg) and is
    // empty for a selection restored from a `?iid` URL. Either would be a treatment that
    // contradicts the per-interaction contract or vanishes on reload.
    //
    // So `DirectedEdge` drops the prop and the treatment comes wholly from `isSelected`
    // on the element `data`. This case is the guard against someone "fixing" that by
    // passing the prop through again.
    mockApi(ENTITIES, [mkIx({ id: 'i1', caller_entity_id: 'e1', callee_entity_id: 'e2' })]);
    renderWithProviders(<ExecutionFlowGraph traceId="T1" selectedInteractionId="i1" />);
    await waitFor(() => expect(edgeEls()).toHaveLength(2));

    // Our own class is on BOTH legs — a selection with no click behind it, exactly the
    // `?iid`-restore case PF's state cannot represent.
    expect(document.querySelectorAll('.dg-graph-edge--selected')).toHaveLength(2);
    // …and PF's modifier is on neither, so there is no second, disagreeing signal.
    expect(document.querySelector('.pf-m-selected')).toBeNull();
  });

  it('still shows no pf-m-selected after a real CLICK, so the two legs never disagree', async () => {
    // The same point from the other direction, and the case that actually exposed it: a
    // click DOES populate PF's `selectedIds`, so if the prop were forwarded, the clicked
    // leg would gain `pf-m-selected` while its sibling — equally part of the selected
    // interaction — would not. One interaction, two arrows, one treatment.
    // Rendered with the selection ALREADY applied, then clicked — which reaches the same
    // state as click-then-owner-feeds-it-back without needing a rerender, and is the
    // steady state a reader is actually in when they click a second arrow.
    mockApi(ENTITIES, [mkIx({ id: 'i1', caller_entity_id: 'e1', callee_entity_id: 'e2' })]);
    renderWithProviders(<ExecutionFlowGraph traceId="T1" selectedInteractionId="i1" />);
    await waitFor(() => expect(edgeEls()).toHaveLength(2));
    // Our per-interaction treatment is on both legs before the click.
    expect(document.querySelectorAll('.dg-graph-edge--selected')).toHaveLength(2);

    // The click populates PF's own `selectedIds` with just this ONE leg's id. If
    // `selected` were forwarded, that leg alone would now gain `pf-m-selected` and its
    // sibling — equally part of the selected interaction — would not.
    clickEdge('i1:request');

    expect(document.querySelector('.pf-m-selected')).toBeNull();
    // …and our own treatment is still on BOTH, unchanged by PF's per-element notion.
    expect(document.querySelectorAll('.dg-graph-edge--selected')).toHaveLength(2);
  });

  it('emits no selected class at all when nothing is selected', async () => {
    // The Execution Flow tab's default. Same discipline as the highlight's `'none'`:
    // "nothing picked" must not pick up a treatment by accident.
    mockApi(ENTITIES, [mkIx({ id: 'i1', caller_entity_id: 'e1', callee_entity_id: 'e2' })]);
    renderWithProviders(<ExecutionFlowGraph traceId="T1" />);
    await waitFor(() => expect(edgeEls()).toHaveLength(2));

    expect(document.querySelector('.dg-graph-edge--selected')).toBeNull();
    expect(document.querySelector('.dg-graph-edge-tag--selected')).toBeNull();
    expect(document.querySelector('.pf-m-selected')).toBeNull();
    expect(edgeData('i1:request').isSelected).toBe(false);
  });

  it('keeps an error leg\'s red AND its selected treatment — independent axes', async () => {
    // A failed leg can be the leg whose interaction is open, and the reader needs both
    // facts. One combined class would make one of them unrepresentable — the same
    // reasoning that already keeps `--error` apart from the highlight role.
    mockApi(ENTITIES, [
      mkIx({
        id: 'i1',
        caller_entity_id: 'e1',
        callee_entity_id: 'e2',
        legs: [mkLeg('request', 1, true), mkLeg('response', 2, false)],
        any_error: true,
      }),
    ]);
    renderWithProviders(<ExecutionFlowGraph traceId="T1" selectedInteractionId="i1" />);
    await waitFor(() => expect(edgeEls()).toHaveLength(2));

    const req = document.querySelector('[data-id="i1:request"]')!;
    expect(req.querySelector('.dg-graph-edge--error')).not.toBeNull();
    expect(req.querySelector('.dg-graph-edge--selected')).not.toBeNull();
    // The error red survives — the selected treatment carries weight and dash, never
    // a repaint that would report a failed leg as healthy.
    expect(req.innerHTML).toContain('var(--dg-color-error)');
    // And its tag keeps the error colour class alongside the selected one.
    expect(req.querySelector('.dg-graph-edge-tag--error')).not.toBeNull();
    expect(req.querySelector('.dg-graph-edge-tag--selected')).not.toBeNull();
  });

  it('encodes the selected treatment in NO raw hex colour', async () => {
    // House rule, and the accessibility one: the distinction is carried by
    // weight/dash/opacity in global.css, so there is nothing hue-shaped inlined on a
    // selected element at all.
    mockApi(ENTITIES, [mkIx({ id: 'i1', caller_entity_id: 'e1', callee_entity_id: 'e2' })]);
    renderWithProviders(<ExecutionFlowGraph traceId="T1" selectedInteractionId="i1" />);
    await waitFor(() => expect(edgeEls()).toHaveLength(2));

    expect(document.querySelector('[data-id="i1:request"]')!.innerHTML).not.toMatch(
      /#[0-9a-f]{3,8}\b/i,
    );
  });

  // Keyboard / a11y. What is achievable on an SVG edge inside PF's rendering, and no
  // more — the component's own note spells out the three limits (tab order is `seq`
  // order with no skip affordance, no keyboard pan to an off-screen focused edge, no
  // announcement of the selection change) rather than pretending to parity with the
  // tables. These cases pin what IS wired, so a half-wired `tabIndex` that does
  // nothing cannot pass for accessibility.

  it('makes each edge a focusable button with a name that identifies the leg', async () => {
    // Reachable AND identifiable: a focusable arrow labelled "edge" would be neither.
    // The label carries the same facts as the hover `<title>` — seq, leg type, summary.
    mockApi(ENTITIES, [mkIx({ id: 'i1', caller_entity_id: 'e1', callee_entity_id: 'e2' })]);
    renderWithProviders(<ExecutionFlowGraph traceId="T1" />);
    await waitFor(() => expect(edgeEls()).toHaveLength(2));

    // Resolved by ROLE + accessible NAME, which is what a screen reader announces —
    // not by a test id, which would prove only that an attribute exists.
    const req = screen.getByRole('button', { name: /seq 1, request: summary-i1/i });
    expect(req).toHaveAttribute('tabindex', '0');
    expect(screen.getByRole('button', { name: /seq 2, response: summary-i1/i })).toBeInTheDocument();
  });

  it('names a failed leg as failed, so the error is not colour-only', async () => {
    // The red stroke is invisible to a screen reader and to a colour-vision-deficient
    // reader; the accessible name is where that fact has to also live.
    mockApi(ENTITIES, [
      mkIx({
        id: 'i1',
        legs: [mkLeg('request', 1, true), mkLeg('response', 2, false)],
        any_error: true,
      }),
    ]);
    renderWithProviders(<ExecutionFlowGraph traceId="T1" />);
    await waitFor(() => expect(edgeEls()).toHaveLength(2));

    expect(screen.getByRole('button', { name: /seq 1, request:.*\(failed\)/i })).toBeInTheDocument();
    // …and the leg that succeeded is not so named.
    expect(screen.getByRole('button', { name: /seq 2, response:/i }).getAttribute('aria-label')).not.toMatch(
      /failed/i,
    );
  });

  it('reports the selection through aria-pressed on BOTH legs', async () => {
    // The state a sighted reader gets from the weight/dash treatment, exposed to a
    // screen reader. Both legs, because the selection is the interaction — the same
    // per-interaction rule the visual treatment follows.
    mockApi(ENTITIES, [
      mkIx({ id: 'i1', caller_entity_id: 'e1', callee_entity_id: 'e2' }, 1),
      mkIx({ id: 'i2', caller_entity_id: 'e1', callee_entity_id: 'e3' }, 3),
    ]);
    renderWithProviders(<ExecutionFlowGraph traceId="T1" selectedInteractionId="i1" />);
    await waitFor(() => expect(edgeEls()).toHaveLength(4));

    expect(screen.getAllByRole('button', { pressed: true })).toHaveLength(2);
    // Both of them are i1's, not one of each interaction.
    for (const el of screen.getAllByRole('button', { pressed: true })) {
      expect(el.closest('[data-id]')?.getAttribute('data-id')).toMatch(/^i1:/);
    }
  });

  it('activates an edge with Enter and with Space', async () => {
    // Both, as a native button would — and through PF's own `onSelect`, so the keyboard
    // path is the identical path a click takes rather than a second one that could
    // drift from it.
    const onSelect = vi.fn();
    mockApi(ENTITIES, [mkIx({ id: 'i1', caller_entity_id: 'e1', callee_entity_id: 'e2' })]);
    renderWithProviders(<ExecutionFlowGraph traceId="T1" onSelectInteraction={onSelect} />);
    await waitFor(() => expect(edgeEls()).toHaveLength(2));
    const req = screen.getByRole('button', { name: /seq 1, request/i });

    act(() => {
      fireEvent.keyDown(req, { key: 'Enter' });
    });
    expect(onSelect).toHaveBeenLastCalledWith('i1');

    // Space on the SAME edge is PF's toggle, so it deselects — which is the click
    // behaviour, faithfully. Asserted as "it fired again", not as a particular value,
    // since the toggle direction is what the click test already pins.
    onSelect.mockClear();
    act(() => {
      fireEvent.keyDown(req, { key: ' ' });
    });
    expect(onSelect).toHaveBeenCalledTimes(1);
  });

  it('ignores other keys, so typing over the graph selects nothing', async () => {
    const onSelect = vi.fn();
    mockApi(ENTITIES, [mkIx({ id: 'i1', caller_entity_id: 'e1', callee_entity_id: 'e2' })]);
    renderWithProviders(<ExecutionFlowGraph traceId="T1" onSelectInteraction={onSelect} />);
    await waitFor(() => expect(edgeEls()).toHaveLength(2));
    const req = screen.getByRole('button', { name: /seq 1, request/i });

    for (const key of ['a', 'Tab', 'ArrowRight', 'Escape']) {
      fireEvent.keyDown(req, { key });
    }
    expect(onSelect).not.toHaveBeenCalled();
  });

  it('deselects on a background click', async () => {
    // The empty canvas is a deselect, matching the panel's own close button and the
    // tables (where selecting nothing is how a reader gets back to no selection).
    //
    // PF reports this as the GRAPH element's own id, NOT as an empty array — the graph
    // is itself selectable and `GraphComponent` binds the backdrop rect to its
    // `onSelect`. That is the specific shape this test pins: a first cut of the handler
    // read an unrecognised id as "ignore" and made background-click a silent no-op.
    const onSelect = vi.fn();
    mockApi(ENTITIES, [mkIx({ id: 'i1', caller_entity_id: 'e1', callee_entity_id: 'e2' })]);
    renderWithProviders(<ExecutionFlowGraph traceId="T1" onSelectInteraction={onSelect} />);
    await waitFor(() => expect(edgeEls()).toHaveLength(2));

    clickEdge('i1:request');
    expect(onSelect).toHaveBeenLastCalledWith('i1');

    clickBackground();
    expect(onSelect).toHaveBeenLastCalledWith(null);
  });

  it('deselects when the already-selected edge is clicked a second time', async () => {
    // PF's own toggle (`useSelection` resolves a re-click of the selected element to an
    // empty `selectedIds`), surfaced as the same `null` a background click gives. So a
    // second click on an open interaction's arrow closes its panel, which is the
    // behaviour a reader gets from the toggle without being told about it.
    const onSelect = vi.fn();
    mockApi(ENTITIES, [mkIx({ id: 'i1', caller_entity_id: 'e1', callee_entity_id: 'e2' })]);
    renderWithProviders(<ExecutionFlowGraph traceId="T1" onSelectInteraction={onSelect} />);
    await waitFor(() => expect(edgeEls()).toHaveLength(2));

    clickEdge('i1:request');
    expect(onSelect).toHaveBeenLastCalledWith('i1');
    clickEdge('i1:request');
    expect(onSelect).toHaveBeenLastCalledWith(null);
  });

  it('does not fire at all when no callback is passed', async () => {
    // The prop is optional and both graph tabs' own tests render without it. A click
    // must be a no-op rather than a crash — nothing here may assume an owner.
    mockApi(ENTITIES, [mkIx({ id: 'i1', caller_entity_id: 'e1', callee_entity_id: 'e2' })]);
    renderWithProviders(<ExecutionFlowGraph traceId="T1" />);
    await waitFor(() => expect(edgeEls()).toHaveLength(2));

    expect(() => clickEdge('i1:request')).not.toThrow();
  });

  // --- Dragging.
  //
  // THE GESTURE IS NOT SIMULATED, and nothing here pretends it is. Two separate
  // jsdom limits stand in the way: react-topology's dnd layer drives d3-drag,
  // which needs `SVGSVGElement.createSVGPoint`/`getScreenCTM` to convert pointer
  // coordinates, and — more fundamentally — PF CULLS node CONTENT that is not "in
  // view" (`Visualization.shouldRenderNode` → `Graph.isNodeInView`). The surface
  // has zero dimensions in jsdom, so nothing is ever in view and every node's
  // `<g data-kind="node">` renders EMPTY. That is why the `dragNodeRef` the drag
  // HOC supplies never reaches the DOM here and there is no attribute, class or
  // listener to assert it by. (It is also the real reason for the empty node
  // groups this file's header describes — the zero-size `getBBox` stub is a
  // second, independent cause of the missing LABEL text.)
  //
  // What IS asserted is the part this component is actually responsible for: that
  // a node moved off its grid cell stays put across a model rebuild, is
  // forgotten when the trace changes, and can be put back. The node is moved with
  // `Node.setPosition` — literally what PF's drag behavior calls at the end of a
  // gesture — so the mechanism under test is the real one even though the gesture
  // that would normally trigger it is not. That the gesture itself moves a node is
  // PF's own behavior and is verify-by-hand / Playwright territory.

  it('keeps a moved node where it was put when the SAME trace is re-read', async () => {
    // THE regression this whole mechanism exists for. A poll returning the same
    // trace's data rebuilds the model; while a layout was registered, `fromModel`
    // re-added every node, `BaseLayout`'s ADD_CHILD listener re-ran the layout a
    // frame later, and every dragged node snapped home — which made the drag
    // feature worthless.
    mockApi(ENTITIES, [mkIx({ id: 'i1', caller_entity_id: 'e1', callee_entity_id: 'e2' })]);
    const { poll } = renderGraph('T1');
    await waitFor(() => expect(nodeAt('e1')).toEqual(cell(0, 0)));

    moveNode('e1', 777, 555);
    await waitFor(() => expect(nodeAt('e1')).toEqual({ x: 777, y: 555 }));

    // A second interaction arrives on the same trace: a genuine data change, so the
    // model IS rebuilt — and the reader's placement must ride through it.
    mockApi(ENTITIES, [
      mkIx({ id: 'i1', caller_entity_id: 'e1', callee_entity_id: 'e2' }, 1),
      mkIx({ id: 'i2', caller_entity_id: 'e1', callee_entity_id: 'e3' }, 3),
    ]);
    await poll();
    await waitFor(() => expect(edgeEls()).toHaveLength(4));

    expect(nodeAt('e1')).toEqual({ x: 777, y: 555 });
    // The nodes the reader did NOT touch still sit on their derived cells, so one
    // drag does not freeze the rest of the layout in place. e3 is now a CALLEE of e1
    // rather than isolated, so it joins e2 in column 1 at the next row down — which
    // also confirms the untouched nodes were re-derived rather than carried over.
    expect(nodeAt('e2')).toEqual(cell(1, 0));
    expect(nodeAt('e3')).toEqual(cell(1, 1));
  });

  it('keeps a moved node where it was put when the SELECTION changes', async () => {
    // The exact counterpart of the Lineage block's highlight-change case, and the
    // regression edge selection could most plausibly have introduced: `isSelected` is
    // baked into the element `data`, so `selectedInteractionId` is a model-effect
    // dependency and changing it rebuilds the model — which is precisely what used to
    // lose the reader's drag. The positions must ride through a selection change the
    // same way they ride through a poll and a highlight change.
    mockApi(ENTITIES, [mkIx({ id: 'i1', caller_entity_id: 'e1', callee_entity_id: 'e2' })]);
    const { rerender } = renderWithProviders(
      <ExecutionFlowGraph traceId="T1" selectedInteractionId={null} />,
    );
    await waitFor(() => expect(nodeAt('e1')).toEqual(cell(0, 0)));

    moveNode('e1', 777, 555);
    await waitFor(() => expect(nodeAt('e1')).toEqual({ x: 777, y: 555 }));

    // Select the interaction — a new `data` flag on both its edges, therefore a push.
    rerender(<ExecutionFlowGraph traceId="T1" selectedInteractionId="i1" />);
    await waitFor(() =>
      expect(document.querySelector('[data-id="i1:request"] .dg-graph-edge--selected')).not.toBeNull(),
    );

    // The drag survived the selection change…
    expect(nodeAt('e1')).toEqual({ x: 777, y: 555 });
    // …and the untouched nodes are still on their derived cells, so one drag did not
    // freeze the rest of the layout. e3 is isolated here → trailing column.
    expect(nodeAt('e2')).toEqual(cell(1, 0));
    expect(nodeAt('e3')).toEqual(cell(2, 0));

    // And it survives DESELECTION too, which is a second push in the other direction —
    // the asymmetry would be easy to miss if only the select half were covered.
    rerender(<ExecutionFlowGraph traceId="T1" selectedInteractionId={null} />);
    await waitFor(() => expect(document.querySelector('.dg-graph-edge--selected')).toBeNull());
    expect(nodeAt('e1')).toEqual({ x: 777, y: 555 });
  });

  it('does NOT rebuild the model for an unmemoised callback prop', async () => {
    // The drag-survival guarantee's real mechanism, asserted at its cause rather than
    // only at its symptom. `onSelectInteraction` is a fresh arrow function on every
    // parent render (which is exactly how `FlowTables` passes it), and if it reached
    // the model effect's dependency list every parent re-render would push a new model
    // — harvesting and re-applying positions each time, and churning the whole graph.
    // The callback is held in a ref precisely so it cannot.
    //
    // Observable as the `fromModel` call COUNT: re-rendering with a brand-new function
    // and otherwise identical props must not add a push.
    mockApi(ENTITIES, [mkIx({ id: 'i1', caller_entity_id: 'e1', callee_entity_id: 'e2' })]);
    const { rerender } = renderWithProviders(
      <ExecutionFlowGraph traceId="T1" onSelectInteraction={() => {}} />,
    );
    await waitFor(() => expect(edgeEls()).toHaveLength(2));
    // `modelPushes` is bumped by the same `beforeEach` spy that captures the
    // controller, so this reads the REAL push count rather than a second stub.
    const pushes = modelPushes;

    // A different function identity, same everything else.
    rerender(<ExecutionFlowGraph traceId="T1" onSelectInteraction={() => {}} />);
    rerender(<ExecutionFlowGraph traceId="T1" onSelectInteraction={() => {}} />);

    expect(modelPushes).toBe(pushes);
    // …and the LATEST callback is still the one that fires, so the ref is kept current
    // rather than capturing the first render's closure — which is the bug the ref
    // pattern invites if the assignment is put inside a `useEffect`.
    const latest = vi.fn();
    rerender(<ExecutionFlowGraph traceId="T1" onSelectInteraction={latest} />);
    clickEdge('i1:request');
    expect(latest).toHaveBeenCalledWith('i1');
  });

  it('does NOT carry a moved position across to a different trace', async () => {
    // The other half of the invariant, and the reason the positions are keyed by
    // trace at all: entities are cross-trace stable (ADR-0013), so two traces
    // routinely share an entity id, and keeping the positions by id alone would
    // silently apply one trace's arrangement to another's.
    mockApi(ENTITIES, [mkIx({ id: 'i1', caller_entity_id: 'e1', callee_entity_id: 'e2' })]);
    const { rerender } = renderWithProviders(<ExecutionFlowGraph traceId="T1" />);
    await waitFor(() => expect(nodeAt('e1')).toEqual(cell(0, 0)));

    moveNode('e1', 777, 555);
    await waitFor(() => expect(nodeAt('e1')).toEqual({ x: 777, y: 555 }));

    mockApi(ENTITIES, [mkIx({ id: 'j1', caller_entity_id: 'e1', callee_entity_id: 'e2' })]);
    rerender(<ExecutionFlowGraph traceId="T2" />);
    await waitFor(() =>
      expect(document.querySelector('[data-id="j1:request"]')).toBeInTheDocument(),
    );

    // Back on its derived cell: a new trace is a new picture.
    expect(nodeAt('e1')).toEqual(cell(0, 0));
  });

  it('puts every moved node back on its grid cell when Reset View is clicked', async () => {
    // Reset View used to call `graph.layout()`. With no layout registered that is a
    // silent no-op — a button that looks like it works and does nothing — so the
    // affordance is re-implemented against the derived cells instead.
    mockApi(ENTITIES, [mkIx({ id: 'i1', caller_entity_id: 'e1', callee_entity_id: 'e2' })]);
    const { poll } = renderGraph('T1');
    await waitFor(() => expect(nodeAt('e1')).toEqual(cell(0, 0)));

    moveNode('e1', 777, 555);
    moveNode('e2', -30, 12);
    await waitFor(() => expect(nodeAt('e2')).toEqual({ x: -30, y: 12 }));

    await userEvent.click(screen.getByRole('button', { name: /^Reset View$/i }));

    await waitFor(() => expect(nodeAt('e1')).toEqual(cell(0, 0)));
    expect(nodeAt('e2')).toEqual(cell(1, 0));

    // …and it STUCK. Reset has to forget the drags, not merely move the nodes: if
    // it only moved them, the very next model push would re-apply the remembered
    // positions and Reset would silently undo itself a poll later.
    mockApi(ENTITIES, [
      mkIx({ id: 'i1', caller_entity_id: 'e1', callee_entity_id: 'e2' }, 1),
      mkIx({ id: 'i2', caller_entity_id: 'e1', callee_entity_id: 'e3' }, 3),
    ]);
    await poll();
    await waitFor(() => expect(edgeEls()).toHaveLength(4));
    expect(nodeAt('e1')).toEqual(cell(0, 0));
    expect(nodeAt('e2')).toEqual(cell(1, 0));
  });

  it('keeps both legs attached to a node that has moved', async () => {
    // Confirmed rather than assumed, since "do the edges follow?" is the obvious
    // way a fixed-position layout could have gone wrong. An edge here stores no
    // geometry of its own (no bendpoints, no start/end points), so
    // `BaseEdge.getStartPoint`/`getEndPoint` fall through to the source/target
    // ANCHORS, which are computed from the live node positions — and those are
    // mobx-observable, so moving a node re-renders the edge. None of that involved
    // the layout, which is why removing the layout could not break it.
    mockApi(ENTITIES, [mkIx({ id: 'i1', caller_entity_id: 'e1', callee_entity_id: 'e2' })]);
    renderWithProviders(<ExecutionFlowGraph traceId="T1" />);
    await waitFor(() => expect(edgeEls()).toHaveLength(2));

    const pathOf = (id: string) =>
      document.querySelector(`[data-id="${id}"] .pf-topology__edge__link`)!.getAttribute('d') ?? '';
    /** Every coordinate pair on the drawn link, in order. */
    const pts = (id: string) =>
      [...pathOf(id).matchAll(/(-?[\d.]+)\s+(-?[\d.]+)/g)].map((m) => ({
        x: Number(m[1]),
        y: Number(m[2]),
      }));

    const before = pathOf('i1:request');
    moveNode('e1', 777, 555);
    await waitFor(() => expect(pathOf('i1:request')).not.toBe(before));

    // The endpoint is the node's ANCHOR, not its centre: PF puts it on the ellipse
    // boundary facing the other end, so it is offset from the centre by up to the
    // node's radius and must NOT be asserted as equal to the centre. What it must
    // be is ON the node — hence the radius-bounded check, which is exactly the
    // property "the arrow still touches the node" means.
    const radius = 20; // NODE_DIAMETER / 2
    const onE1 = (p: { x: number; y: number }) => {
      expect(Math.abs(p.x - 777)).toBeLessThanOrEqual(radius);
      expect(Math.abs(p.y - 555)).toBeLessThanOrEqual(radius);
    };
    // The request leg STARTS at e1 and the response leg ENDS at e1 — both followed,
    // so the two legs did not diverge onto different ideas of where e1 is.
    onE1(pts('i1:request')[0]);
    onE1(pts('i1:response').at(-1)!);

    // And the OTHER end did not drift: e2 was not touched, so the request's target
    // anchor is still on e2's grid cell.
    const e2 = cell(1, 0);
    const reqEnd = pts('i1:request').at(-1)!;
    expect(Math.abs(reqEnd.x - e2.x)).toBeLessThanOrEqual(radius);
    expect(Math.abs(reqEnd.y - e2.y)).toBeLessThanOrEqual(radius);
  });
});

/**
 * The **Lineage** tab, which is the SAME component with a highlight — `LineageGraph`
 * renders `EntityGraph` exactly as `ExecutionFlowGraph` does and differs only in the
 * `highlight` prop and its own notices. That is what these cases pin: the sharing is
 * real (no forked graph), and the four things a highlight has to be able to SAY are
 * said in words rather than left to the picture.
 *
 * WHAT IS AND IS NOT ASSERTED, restated for the highlight because it is easy to
 * overclaim here. jsdom applies no stylesheet rules to computed style and PF CULLS
 * node CONTENT on a zero-size surface (see this file's header), so:
 *   - the visual DIMMING is not observable and is never asserted — that is a
 *     Playwright / by-hand fact;
 *   - a NODE's highlight className is not observable either, because the node's `<g>`
 *     renders empty;
 *   - an EDGE's className IS observable (edge content is not culled), so the highlight
 *     is asserted through the edges, which is also where the reader's eye actually
 *     follows the answer.
 * Asserting a node class here would silently pass-or-fail for the wrong reason, so it
 * is deliberately not attempted.
 */

/**
 * A reachability response, as the wire spells it — verified against a live trace.
 *
 * Defaults to the non-claiming `no-adjacent`, so a test that cares about one
 * direction says so and the other stays explicitly empty rather than accidentally
 * asserting something.
 */
function mkReach(
  direction: 'fanin' | 'fanout',
  over: Partial<LineageReachability> = {},
): LineageReachability {
  return {
    direction,
    seed_entity_id: 'e2',
    entities: [],
    legs: [],
    state: 'no-adjacent',
    pending_frontier: [],
    truncated: false,
    status: 'complete',
    stopped_at_seq: null,
    ...over,
  };
}

/** One reached entity. `hops` is a DISTANCE, never an ordering (ADR-0028 D10). */
function mkReached(id: string, hops: number) {
  return { id, natural_key: `nk:${id}`, kind: 'agent', display_name: id, hops };
}

/** One traversed leg — the ROUTE, in the response's own field names. */
function mkTraversed(
  interactionId: string,
  legType: 'request' | 'response',
  from: string,
  to: string,
  seq: number,
) {
  return {
    interaction_id: interactionId,
    leg_type: legType,
    from_entity_id: from,
    to_entity_id: to,
    seq,
  };
}

/**
 * The natural key of `e1` in the `ENTITIES` fixture, and the DEFAULT traced source
 * for every case below that has an answer to assert.
 *
 * A default in the harness rather than a per-case argument, because `source` is now
 * REQUIRED by the read (`fanin(entity, source)` — `docs/data_lineage_alg.md`'s
 * `## API`): a case that omitted it would be exercising the no-source-chosen state by
 * accident rather than the answer it means to assert. The cases that DO mean to
 * exercise the unchosen state pass `lineageSource: null` explicitly.
 *
 * Note this also has to be a member of the summary's `sources`, or
 * `resolveSourceChoice` reports it `'stale'` and asks nothing — which is why
 * `renderLineage` defaults the summary to contain it (see there).
 */
const SOURCE_E1 = 'agent:(p,a)';

/** A summary whose `sources` contains {@link SOURCE_E1}, so a choice can resolve. */
const SUMMARY_WITH_SOURCE: WireLineageSummary = {
  sources: [SOURCE_E1],
  destinations: [],
  status: 'complete',
  stopped_at_seq: null,
};

/**
 * Stub the four reads the Lineage tab makes: entities, interactions, the
 * sources/destinations summary and the two directions of reachability.
 *
 * The reachability stubs are keyed on the `direction` query parameter, because that
 * parameter is REQUIRED and single-valued on the wire (ADR-0028 D14) — which is the
 * whole reason "both directions" is two requests. A test that stubs only one and
 * lets the other 404 would be testing the failed-read path by accident.
 *
 * THE `source` PARAMETER IS ASSERTED, NOT MERELY TOLERATED. It is equally required
 * (`docs/data_lineage_alg.md`'s `## API`), so a request that arrives without one is
 * answered with a FAILURE here rather than with data — mirroring the server's 400.
 * Without that, a regression that dropped the parameter would still see green tests
 * while shipping a tab that 400s on every read.
 *
 * `null` for any of them means "make that read fail", which is how the fourth state
 * (a failed read, distinct from the server's three) is exercised.
 */
function mockLineageApi(opts: {
  entities?: Entity[] | null;
  interactions?: Interaction[] | null;
  summary?: WireLineageSummary | null;
  fanin?: LineageReachability | null;
  fanout?: LineageReachability | null;
}) {
  const entities = opts.entities === undefined ? ENTITIES : opts.entities;
  const interactions =
    opts.interactions === undefined
      ? [mkIx({ id: 'i1', caller_entity_id: 'e1', callee_entity_id: 'e2' }, 1)]
      : opts.interactions;
  const fail = { ok: false, status: 500, json: async () => ({}) };
  const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body });

  (fetch as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
    if (url.includes('/data-lineage-graph')) {
      // A source-less reachability request is a 400 on the real server, so it is a
      // failure here too — see this function's note.
      if (!/[?&]source=/.test(url)) return { ok: false, status: 400, json: async () => ({}) };
      // The direction is read off the query string, exactly as the server requires it.
      const wantFanin = url.includes('direction=fanin');
      const chosen = wantFanin ? opts.fanin : opts.fanout;
      if (chosen === null) return fail;
      return ok(chosen ?? mkReach(wantFanin ? 'fanin' : 'fanout'));
    }
    if (url.includes('/data-lineage-summary')) {
      if (opts.summary === null) return fail;
      return ok(
        opts.summary ?? { sources: [], destinations: [], status: 'complete', stopped_at_seq: null },
      );
    }
    if (url.endsWith('/entities')) return entities === null ? fail : ok({ entities });
    if (url.endsWith('/interactions')) return interactions === null ? fail : ok({ interactions });
    return ok({});
  });
}

/** Render the Lineage tab over the standard fixture set, with overridable bits. */
function renderLineage(
  over: {
    selectedEntityId?: string | null;
    /**
     * The traced source (`?src`). Defaults to {@link SOURCE_E1} — a real member of the
     * default summary — so a case asserting an ANSWER gets one. Pass `null` explicitly
     * for the no-source-chosen state.
     */
    lineageSource?: string | null;
    onLineageSourceChange?: (source: string) => void;
    selectedInteractionId?: string | null;
    onSelectInteraction?: (id: string | null) => void;
    onSelectEntity?: (id: string) => void;
    isLineageError?: boolean;
    status?: 'complete' | 'partial' | null;
    interactions?: Interaction[];
    entities?: Entity[];
    summary?: WireLineageSummary | null;
    fanin?: LineageReachability | null;
    fanout?: LineageReachability | null;
  } = {},
) {
  const entities = over.entities ?? ENTITIES;
  const interactions = over.interactions ?? [
    mkIx({ id: 'i1', caller_entity_id: 'e1', callee_entity_id: 'e2' }, 1),
  ];
  // The summary defaults to one that CONTAINS the default source: with the reads gated
  // on `resolveSourceChoice`, a summary that did not offer the chosen source would make
  // every answer-asserting case silently exercise the `'stale'` state instead.
  const summary = 'summary' in over ? over.summary : SUMMARY_WITH_SOURCE;
  mockLineageApi({
    entities,
    interactions,
    summary,
    fanin: over.fanin,
    fanout: over.fanout,
  });
  return renderWithProviders(
    <LineageGraph
      traceId="T1"
      entities={entities}
      interactions={interactions}
      status={over.status ?? 'complete'}
      isLineageError={over.isLineageError ?? false}
      selectedEntityId={over.selectedEntityId ?? null}
      lineageSource={'lineageSource' in over ? over.lineageSource : SOURCE_E1}
      onLineageSourceChange={over.onLineageSourceChange}
      selectedInteractionId={over.selectedInteractionId ?? null}
      onSelectInteraction={over.onSelectInteraction}
      onSelectEntity={over.onSelectEntity}
    />,
  );
}

/** A fan-in answer over the standard fixture: e1 → e2's request leg is the route. */
const FANIN_E1 = mkReach('fanin', {
  entities: [mkReached('e1', 1)],
  legs: [mkTraversed('i1', 'request', 'e1', 'e2', 1)],
  state: 'derived',
});

/** A fan-out answer over the same fixture, on the OTHER leg. */
const FANOUT_E1 = mkReach('fanout', {
  entities: [mkReached('e1', 1)],
  legs: [mkTraversed('i1', 'response', 'e2', 'e1', 2)],
  state: 'derived',
});

describe('LineageGraph', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn());
    // The SAME controller-capture spy the graph tab's block installs, and needed for
    // the same reason: `moveNode` below reaches the real `Visualization` through it.
    // Duplicated rather than hoisted to a shared `beforeEach` because each describe
    // owns its own lifecycle, and a spy installed for a block that does not use it is
    // a hidden dependency between the two.
    capturedController = null;
    modelPushes = 0;
    const real = Visualization.prototype.fromModel;
    const capture = (vis: Visualization) => {
      capturedController = vis;
      modelPushes += 1;
    };
    vi.spyOn(Visualization.prototype, 'fromModel').mockImplementation(function (
      this: Visualization,
      ...args: Parameters<Visualization['fromModel']>
    ) {
      capture(this);
      return real.apply(this, args);
    });
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  // --- The sharing itself: one graph, two tabs.

  it('draws the SAME nodes and edges the Execution Flow tab draws', async () => {
    // The anti-fork guard. If these ever diverge from the Execution Flow tab's own
    // counts, ids and positions, the two tabs have stopped being one component —
    // which is the specific regression the shared-renderer refactor exists to prevent.
    renderLineage();
    await waitFor(() => expect(screen.getByTestId('lineage-graph')).toBeInTheDocument());
    await waitFor(() => expect(nodeEls()).toHaveLength(3));
    // One completed interaction → two opposite-direction edges, per leg (ADR-0025),
    // ided exactly as the graph tab's are.
    expect(edgeEls()).toHaveLength(2);
    expect([...edgeEls()].map((e) => e.getAttribute('data-id')).sort()).toEqual([
      'i1:request',
      'i1:response',
    ]);
    // …in the same layered cells, so the two tabs are literally the same picture and
    // a reader switching between them does not lose their place. e3 is isolated in
    // this fixture, so it sits in the trailing column.
    expect(nodeAt('e1')).toEqual(cell(0, 0));
    expect(nodeAt('e2')).toEqual(cell(1, 0));
    expect(nodeAt('e3')).toEqual(cell(2, 0));
  });

  it('carries its own testid, so the two tabs stay separately addressable', async () => {
    renderLineage();
    await waitFor(() => expect(screen.getByTestId('lineage-graph')).toBeInTheDocument());
    expect(screen.queryByTestId('execution-flow-graph')).not.toBeInTheDocument();
  });

  it('offers the same zoom controls — the shared surface, not a reduced copy', async () => {
    renderLineage();
    await waitFor(() => expect(screen.getByTestId('lineage-graph')).toBeInTheDocument());
    for (const name of [/^Zoom In$/i, /^Zoom Out$/i, /^Fit to Screen$/i, /^Reset View$/i]) {
      expect(screen.getByRole('button', { name })).toBeEnabled();
    }
  });

  it("still discloses the graph's OWN edge cases, not only the lineage ones", async () => {
    // The three standing notices (dropped / isolated / parallel) belong to the graph,
    // so they must not have been lost by moving the tab-level chrome around.
    renderLineage();
    await waitFor(() => expect(nodeEls()).toHaveLength(3));
    // e3 is named by no interaction in this fixture.
    expect(screen.getByText(/1 isolated entity/i)).toBeInTheDocument();
  });

  // --- JOB 1: the trace's data sources, coloured with NO selection.

  it('renders the legend unconditionally, so a coloured node is never unexplained', async () => {
    // A colour with no key is a puzzle. The legend is on from first paint because the
    // source colouring is too.
    renderLineage({ selectedEntityId: null });
    await waitFor(() => expect(screen.getByTestId('lineage-graph')).toBeInTheDocument());

    const legend = screen.getByRole('group', { name: /Lineage graph legend/i });
    expect(legend).toBeInTheDocument();
    expect(legend).toHaveTextContent(/Data source for this trace/i);
    expect(legend).toHaveTextContent(/Upstream of selection/i);
    expect(legend).toHaveTextContent(/Downstream of selection/i);
    expect(legend).toHaveTextContent(/Not derived yet/i);
  });

  it('marks the trace data sources with NOTHING selected', async () => {
    // JOB 1, and the point of it: the sources are a standing fact about the trace, so
    // they paint before any question is asked. Asserted on the MODEL because a node's
    // `<g>` renders empty on a zero-size surface (see the file header) — the class is
    // built from exactly this `data`.
    renderLineage({
      selectedEntityId: null,
      summary: {
        sources: ['agent:(p,a)', 'llm:api.example.com/gpt'],
        destinations: [],
        status: 'complete',
        stopped_at_seq: null,
      },
    });
    await waitFor(() => expect(nodeEls()).toHaveLength(3));
    await waitFor(() => expect(nodeData('e1').lineage.isDataSource).toBe(true));

    expect(nodeData('e3').lineage.isDataSource).toBe(true);
    // e2's key is not in the summary, so it is NOT a source — the marking is driven by
    // the roll-up, not by kind.
    expect(nodeData('e2').lineage.isDataSource).toBe(false);
  });

  it('drives the source marking from `list sources`, not from the entity kind', async () => {
    // The trap ADR-0028 D14 names explicitly: `list sources` is the union of derived
    // `data_sources`, NOT "entities whose kind is declared a source". e2 is a `tool`
    // (the kind that IS the v1 source default) and is still not marked, because the
    // roll-up did not name it.
    renderLineage({
      selectedEntityId: null,
      summary: {
        sources: ['agent:(p,a)'],
        destinations: [],
        status: 'complete',
        stopped_at_seq: null,
      },
    });
    await waitFor(() => expect(nodeEls()).toHaveLength(3));
    await waitFor(() => expect(nodeData('e1').lineage.isDataSource).toBe(true));

    expect(nodeData('e2').kind).toBe('tool');
    expect(nodeData('e2').lineage.isDataSource).toBe(false);
  });

  it('says how many sources are marked, and that they are derived origins', async () => {
    renderLineage({
      selectedEntityId: null,
      summary: {
        sources: ['agent:(p,a)', 'llm:api.example.com/gpt'],
        destinations: [],
        status: 'complete',
        stopped_at_seq: null,
      },
    });
    await waitFor(() =>
      expect(screen.getByText(/2 of 2 data sources marked on the graph/i)).toBeInTheDocument(),
    );
    // The wording refuses the taxonomy reading out loud.
    expect(screen.getByText(/not a list of entities declared to be sources/i)).toBeInTheDocument();
  });

  it('discloses a source natural key that matches no node, by count and by key', async () => {
    // "8 sources, 6 marked" with no notice is exactly the silent under-report a
    // governance reader must never have to discover for themselves. A legitimate cause
    // is an origin outside the trace's own entity set.
    renderLineage({
      selectedEntityId: null,
      summary: {
        sources: ['agent:(p,a)', 'service:(elsewhere,crm)'],
        destinations: [],
        status: 'complete',
        stopped_at_seq: null,
      },
    });
    await waitFor(() =>
      expect(screen.getByText(/1 data source not shown as nodes/i)).toBeInTheDocument(),
    );
    expect(screen.getByText(/service:\(elsewhere,crm\)/)).toBeInTheDocument();
    // Stated as still REAL, so the reader knows the marked nodes are not the full set.
    expect(screen.getByText(/not the full set/i)).toBeInTheDocument();
    // …and the count of marked ones is honest about being a subset.
    expect(screen.getByText(/1 of 2 data sources marked/i)).toBeInTheDocument();
  });

  it('reports a failed SOURCES read as unknown, never as "no sources"', async () => {
    // The fourth state for job 1. With nothing retrieved, an unmarked graph means
    // nothing at all — and must not read as "this trace has no origins".
    renderLineage({ selectedEntityId: null, summary: null });
    await waitFor(() =>
      expect(screen.getByText(/data sources could not be loaded/i)).toBeInTheDocument(),
    );
    expect(screen.getByText(/unknown/i)).toBeInTheDocument();
    // Not confused with the genuinely-empty roll-up below.
    expect(screen.queryByText(/No data sources attributed/i)).toBeNull();
  });

  it('distinguishes an EMPTY source roll-up from a failed one', async () => {
    renderLineage({
      selectedEntityId: null,
      summary: { sources: [], destinations: [], status: 'complete', stopped_at_seq: null },
    });
    await waitFor(() =>
      expect(screen.getByText(/No data sources attributed in this trace yet/i)).toBeInTheDocument(),
    );
    // Still hedged against being read as a settled "there are none".
    expect(screen.getByText(/check the coverage note above/i)).toBeInTheDocument();
    expect(screen.queryByText(/could not be loaded/i)).toBeNull();
  });

  // --- THE SOURCE CHOICE: the second required half of the question.
  //
  // The read is `fanin(entity, source)` / `fanout(entity, source)` with `source`
  // REQUIRED (docs/data_lineage_alg.md's `## API`), so an entity selection alone is no
  // longer a complete question. These cases pin the four states the choice can be in,
  // that they stay apart from the states DirectionNotice words, and — the one a
  // "nothing rendered" assertion could not catch — that NO REQUEST is fired without a
  // source.

  /** Every URL `fetch` was called with, for the "was the read fired?" assertions. */
  const fetchedUrls = () =>
    (fetch as ReturnType<typeof vi.fn>).mock.calls.map((c) => String(c[0]));

  /**
   * The `source=…` query fragment as `apiPath` would actually write it.
   *
   * Built with `URLSearchParams` because that is what `apiPath` uses, and the two common
   * encoders disagree on precisely the characters a qualified natural key is made of:
   * `encodeURIComponent` leaves `(`, `)`, `,` and `!` literal while `URLSearchParams`
   * percent-escapes them. Asserting against the wrong one fails for an encoding reason
   * that says nothing about whether the parameter was sent.
   */
  const sourceParam = (source: string) => new URLSearchParams({ source }).toString();

  it('fires NO reachability read at all while no source is chosen', async () => {
    // ASSERTED ON THE REQUESTS, not on the absence of a highlight — the distinction the
    // requirement insists on. An ungated pair would 400 on every first paint (the
    // parameter is required), and the tab would then have to render that as the
    // failed-read state: telling the reader to retry something that cannot succeed
    // until they choose. So the gate is `useLineageGraph`'s `enabled`, and this is what
    // proves it holds.
    renderLineage({ selectedEntityId: 'e2', lineageSource: null });
    await waitFor(() => expect(screen.getByTestId('lineage-graph')).toBeInTheDocument());
    // The SUMMARY is ungated — the trace's sources are a standing fact and the picker's
    // options come from it, so it must have been read.
    await waitFor(() =>
      expect(fetchedUrls().some((u) => u.includes('/data-lineage-summary'))).toBe(true),
    );

    // …and the two directional reads were NOT made, despite an entity being selected.
    expect(fetchedUrls().some((u) => u.includes('/data-lineage-graph'))).toBe(false);
  });

  it('sends the chosen source on BOTH directional reads', async () => {
    renderLineage({ selectedEntityId: 'e2', lineageSource: SOURCE_E1, fanin: FANIN_E1 });
    await waitFor(() =>
      expect(fetchedUrls().some((u) => u.includes('direction=fanin'))).toBe(true),
    );

    // One source for both halves of one question: fan-in and fan-out about DIFFERENT
    // sources would be a graph whose two halves were about different things.
    //
    // Encoded through `URLSearchParams`, not `encodeURIComponent`: that is what
    // `apiPath` uses, and the two DIFFER on exactly the characters a qualified natural
    // key is full of (`(`, `)`, `,` are left literal by one and escaped by the other).
    // A hand-rolled expectation would fail for an encoding reason that has nothing to
    // do with whether the parameter was sent.
    const encoded = sourceParam(SOURCE_E1);
    expect(
      fetchedUrls().some((u) => u.includes('direction=fanin') && u.includes(encoded)),
    ).toBe(true);
    expect(
      fetchedUrls().some((u) => u.includes('direction=fanout') && u.includes(encoded)),
    ).toBe(true);
  });

  it('REFETCHES on a source change rather than serving the previous source’s answer', async () => {
    // The cache-key case, and the worst available failure if it regresses: the same
    // entity and direction have a DIFFERENT answer per source, so a query key without
    // `source` would paint source A's graph under source B's label. Asserted as a real
    // second request carrying the new source.
    const twoSources: WireLineageSummary = {
      sources: [SOURCE_E1, 'llm:api.example.com/gpt'],
      destinations: [],
      status: 'complete',
      stopped_at_seq: null,
    };
    const { rerender } = renderLineage({
      selectedEntityId: 'e2',
      lineageSource: SOURCE_E1,
      summary: twoSources,
      fanin: FANIN_E1,
    });
    await waitFor(() =>
      expect(fetchedUrls().some((u) => u.includes(sourceParam(SOURCE_E1)))).toBe(true),
    );

    rerender(
      <LineageGraph
        traceId="T1"
        entities={ENTITIES}
        interactions={[mkIx({ id: 'i1', caller_entity_id: 'e1', callee_entity_id: 'e2' }, 1)]}
        status="complete"
        isLineageError={false}
        selectedEntityId="e2"
        lineageSource="llm:api.example.com/gpt"
      />,
    );

    // A NEW request for the new source — not a cache hit on the old one.
    await waitFor(() =>
      expect(
        fetchedUrls().some(
          (u) =>
            u.includes('direction=fanin') && u.includes(sourceParam('llm:api.example.com/gpt')),
        ),
      ).toBe(true),
    );
  });

  it('says "choose a data source" as an INSTRUCTION, distinct from every absence state', async () => {
    // Not an empty result, and not one of the four states DirectionNotice words. With no
    // source there is no question, so none of those four can even apply — and the
    // entity prompt is withheld too, so the reader is asked for one missing half at a
    // time rather than shown two simultaneous prompts.
    renderLineage({ selectedEntityId: 'e2', lineageSource: null });
    await waitFor(() =>
      expect(screen.getByText(/Choose a data source to trace/i)).toBeInTheDocument(),
    );

    expect(screen.queryByText(/Upstream lineage could not be loaded/i)).toBeNull();
    expect(screen.queryByText(/not yet computed/i)).toBeNull();
    expect(screen.queryByText(/derived, not missing/i)).toBeNull();
    expect(screen.queryByText(/Nothing upstream of this entity/i)).toBeNull();
    expect(screen.queryByText(/Select an entity to trace/i)).toBeNull();
  });

  it('dims nothing while no source is chosen, even with an entity selected', async () => {
    // The same "no question asked is not an empty answer" rule as the no-selection case,
    // now reachable from the OTHER missing half. A selected entity with no source must
    // not dim the graph: that would paint "not part of the answer" over a question
    // nobody asked.
    renderLineage({ selectedEntityId: 'e2', lineageSource: null });
    await waitFor(() => expect(edgeEls()).toHaveLength(2));

    expect(document.querySelector('.dg-graph-edge--dimmed')).toBeNull();
    expect(document.querySelector('.dg-graph-edge--carrier')).toBeNull();
    expect(nodeData('e2').highlight).toBe('none');
  });

  it('offers the trace’s sources in the picker, labelled friendly with the key reachable', async () => {
    // `lineageLabel`'s contract, reused rather than reimplemented: the friendly
    // `display_name` is the label and the QUALIFIED natural key stays reachable, because
    // two agents' tools can share a display name and a governance surface must not make
    // two distinct sources look identical.
    renderLineage({ selectedEntityId: null, lineageSource: null, summary: SUMMARY_WITH_SOURCE });
    const toggle = await screen.findByRole('button', { name: /Tracing data source/i });
    // Nothing chosen → the toggle says so rather than naming an arbitrary source.
    expect(toggle).toHaveTextContent(/Choose a data source/i);

    fireEvent.click(toggle);
    // e1's display_name in the ENTITIES fixture. The option is named by it…
    const option = await screen.findByRole('option', { name: /agent-a/ });
    // …and shows the QUALIFIED key as its description, so two same-named sources are
    // visibly two options rather than one apparently-duplicated row.
    //
    // Asserted on the DESCRIPTION rather than on a `title` attribute: PF's
    // `SelectOption` is a `MenuItem` and does not forward `title` to the DOM (verified —
    // the attribute is absent), so a `toHaveAttribute('title', …)` assertion would fail
    // for a PF-internals reason rather than tell us anything. The description is what
    // actually reaches a reader's eyes, which is the honest thing to pin.
    expect(option).toHaveTextContent(SOURCE_E1);
  });

  it('makes the chosen source VISIBLE on the closed control, not merely implicit', async () => {
    // The governance requirement: an unlabelled highlight is a claim without a subject.
    // A reader looking at a lit graph must be able to read WHICH source's flow it is
    // without opening a menu.
    renderLineage({ selectedEntityId: 'e2', lineageSource: SOURCE_E1, fanin: FANIN_E1 });
    const toggle = await screen.findByRole('button', { name: /Tracing data source/i });

    expect(toggle).toHaveTextContent('agent-a');
    // The qualified key on the toggle too, since the friendly label alone can name two
    // different sources identically.
    expect(toggle).toHaveAttribute('title', SOURCE_E1);
  });

  it('reports the chosen source through onLineageSourceChange, so the parent owns the URL', async () => {
    // One owner of `?src`, matching `?eid` / `?legs`: the control reports the choice and
    // the page mirrors it. This component never touches `useSearchParams`.
    const onLineageSourceChange = vi.fn();
    renderLineage({
      selectedEntityId: null,
      lineageSource: null,
      summary: SUMMARY_WITH_SOURCE,
      onLineageSourceChange,
    });
    fireEvent.click(await screen.findByRole('button', { name: /Tracing data source/i }));
    fireEvent.click(await screen.findByRole('option', { name: /agent-a/ }));

    // The natural KEY, not the display name — that is what the endpoint's `source`
    // parameter takes.
    expect(onLineageSourceChange).toHaveBeenCalledWith(SOURCE_E1);
  });

  it('names a STALE ?src as not one of this trace’s sources, and asks nothing', async () => {
    // A bookmark from another trace, or a source that has dropped out of this trace's
    // roll-up. Handled the way `parseLegViewKey` handles a bad `?legs` — coerced, never
    // thrown — but DISCLOSED rather than silently blanked, so the reader learns why
    // their link did not restore instead of seeing a bare prompt.
    renderLineage({
      selectedEntityId: 'e2',
      lineageSource: 'svc:(elsewhere,from-another-trace)',
      summary: SUMMARY_WITH_SOURCE,
    });
    await waitFor(() =>
      expect(
        screen.getByText(/not one of this trace’s sources/i),
      ).toBeInTheDocument(),
    );

    // The requested value is named, so the reader can recognise their own link.
    expect(screen.getByText(/svc:\(elsewhere,from-another-trace\)/)).toBeInTheDocument();
    // NOTHING was asked of the server, because the source is unanswerable here.
    expect(fetchedUrls().some((u) => u.includes('/data-lineage-graph'))).toBe(false);
    // Not confused with "no sources": this trace HAS one, it is just not that one.
    expect(screen.queryByText(/No data sources attributed/i)).toBeNull();
  });

  it('says a ZERO-SOURCE trace has nothing to trace, exactly once and not as a failure', async () => {
    // A real state of its own — nothing derived to trace — and distinct from loading and
    // from a failed read. Stated ONCE: the picker renders nothing in this state and the
    // roll-up alert speaks for both consequences (nothing marked, nothing to trace), so
    // two alerts cannot word one fact two ways.
    renderLineage({
      selectedEntityId: 'e2',
      lineageSource: null,
      summary: { sources: [], destinations: [], status: 'complete', stopped_at_seq: null },
    });
    await waitFor(() =>
      expect(screen.getByText(/No data sources attributed in this trace yet/i)).toBeInTheDocument(),
    );

    expect(screen.getByText(/nothing to trace/i)).toBeInTheDocument();
    expect(screen.getByText(/neither a failed read nor a pending one/i)).toBeInTheDocument();
    // No picker to operate, and no "choose a source" instruction — there is nothing to
    // choose, so an instruction to choose would be a dead end.
    expect(screen.queryByRole('button', { name: /Tracing data source/i })).toBeNull();
    expect(screen.queryByText(/Choose a data source to trace/i)).toBeNull();
    // Emphatically not the failed-read or loading wording.
    expect(screen.queryByText(/could not be loaded/i)).toBeNull();
    expect(screen.queryByText(/Loading the trace’s data sources/i)).toBeNull();
    // And nothing was asked.
    expect(fetchedUrls().some((u) => u.includes('/data-lineage-graph'))).toBe(false);
  });

  it('keeps a FAILED summary read distinct from a zero-source trace, and asks nothing', async () => {
    // The read failed, so which sources exist is UNKNOWN — not none. That means there is
    // also nothing choosable, but for a completely different reason, and the two must not
    // be worded alike.
    renderLineage({ selectedEntityId: 'e2', lineageSource: SOURCE_E1, summary: null });
    await waitFor(() =>
      expect(screen.getByText(/data sources could not be loaded/i)).toBeInTheDocument(),
    );

    expect(screen.queryByText(/No data sources attributed/i)).toBeNull();
    // No reachability read either: with no roll-up, the requested source cannot be
    // confirmed to belong to this trace, so nothing is asked. Deliberately NOT
    // optimistic — asking anyway would risk a 400 the reader would read as a lineage
    // failure rather than as the sources read failing.
    expect(fetchedUrls().some((u) => u.includes('/data-lineage-graph'))).toBe(false);
  });

  // --- The CHOSEN source, distinguished from the trace's other source nodes.

  it('marks the chosen source distinctly WITHOUT unmarking the trace’s other sources', async () => {
    // Requirement 3: the source colouring is a standing fact and stays unconditional,
    // while the chosen one is separately identifiable. Asserted on the MODEL `data` the
    // classes are built from, because PF culls node content on a zero-size surface (see
    // this block's header) — the visual double-ring is manually-verify-only.
    renderLineage({
      selectedEntityId: 'e2',
      lineageSource: SOURCE_E1,
      summary: {
        // e1 and e3's keys, both sources; e1 is the one being traced.
        sources: [SOURCE_E1, 'llm:api.example.com/gpt'],
        destinations: [],
        status: 'complete',
        stopped_at_seq: null,
      },
      fanin: FANIN_E1,
    });
    await waitFor(() => expect(nodeEls()).toHaveLength(3));
    await waitFor(() => expect(nodeData('e1').lineage.isChosenSource).toBe(true));

    // BOTH facts on the chosen node: still one of the trace's sources, AND the subject.
    expect(nodeData('e1').lineage.isDataSource).toBe(true);
    // The OTHER source keeps its standing mark and is NOT claimed as the chosen one —
    // which is the whole distinction.
    expect(nodeData('e3').lineage).toMatchObject({ isDataSource: true, isChosenSource: false });
  });

  it('claims no chosen source node while none is chosen', async () => {
    renderLineage({
      selectedEntityId: null,
      lineageSource: null,
      summary: SUMMARY_WITH_SOURCE,
    });
    await waitFor(() => expect(nodeData('e1').lineage.isDataSource).toBe(true));

    // Coloured as a source, not claimed as the traced one — nothing is being traced.
    expect(nodeData('e1').lineage.isChosenSource).toBe(false);
  });

  it('explains the chosen-source treatment in the legend, and not by hue alone', async () => {
    // A treatment with no key is a puzzle, and the legend entry is rendered
    // unconditionally for the same reason the others are. The distinguishing channel is
    // named in WORDS ("double ring"), so it is not carried by colour.
    renderLineage({ selectedEntityId: null, lineageSource: null });
    const legend = await screen.findByRole('group', { name: /Lineage graph legend/i });

    expect(legend).toHaveTextContent(/Data source for this trace/i);
    expect(legend).toHaveTextContent(/The source being traced/i);
    expect(legend).toHaveTextContent(/double ring/i);
  });

  it('names the traced source in every direction verdict, so a verdict has a subject', async () => {
    // "Nothing upstream of this entity" is a much stronger claim than the read supports:
    // the walk is source-scoped, so each verdict is true OF ONE SOURCE. The
    // qualification is appended to the state's own sentence, so the four states stay
    // distinct and only their scope is corrected.
    renderLineage({
      selectedEntityId: 'e2',
      lineageSource: SOURCE_E1,
      fanin: mkReach('fanin', { state: 'no-adjacent' }),
      fanout: FANOUT_E1,
    });
    // The `no-adjacent` verdict keeps its own title AND gains the scope.
    await waitFor(() =>
      expect(screen.getByText(/Nothing upstream of this entity in this trace/i)).toBeInTheDocument(),
    );
    expect(
      screen.getByText(/scoped to the data source agent-a/i),
    ).toBeInTheDocument();
    // …and the derived verdict names it too, by the friendly label.
    expect(screen.getByText(/went to for the data source agent-a/i)).toBeInTheDocument();
  });

  // --- No selection: an instruction, and NOTHING claimed or dimmed.

  it('instructs the reader to select an entity when none is selected', async () => {
    // WORDING CHANGED, and faithfully: the prompt now says "trace THIS SOURCE's data"
    // because the walk is `fanin(entity, source)` and the answer is source-relative. It
    // is also now gated on a source having been chosen — hence the explicit
    // `lineageSource` here, without which the picker's own "choose a data source" alert
    // is what is on screen instead (asserted separately below).
    renderLineage({ selectedEntityId: null, lineageSource: SOURCE_E1 });
    await waitFor(() => expect(screen.getByTestId('lineage-graph')).toBeInTheDocument());

    expect(
      screen.getByText(/Select an entity to trace this source’s data in and out/i),
    ).toBeInTheDocument();
    // Names BOTH controls, since nodes are click targets now.
    expect(screen.getByText(/Click a node on the graph, or a row in the Entities table/i)).toBeInTheDocument();
  });

  it('dims nothing at all while no entity is selected', async () => {
    // "No question asked" must never be painted as "not part of the answer" — the
    // reason `'none'` and `'dimmed'` are distinct HighlightRole values. This is now
    // load-bearing in a NEW way: the tab passes a highlight even with no selection (it
    // carries the always-on source marks), so only `roleOf`'s `selectedNodeId === null`
    // guard stands between that and a wholly dimmed graph.
    renderLineage({
      selectedEntityId: null,
      summary: {
        sources: ['agent:(p,a)'],
        destinations: [],
        status: 'complete',
        stopped_at_seq: null,
      },
    });
    await waitFor(() => expect(edgeEls()).toHaveLength(2));
    // The premise: a highlight IS active (a source is marked) …
    await waitFor(() => expect(nodeData('e1').lineage.isDataSource).toBe(true));

    // … and yet nothing is dimmed.
    expect(document.querySelector('.dg-graph-edge--dimmed')).toBeNull();
    expect(document.querySelector('.dg-graph-edge--carrier')).toBeNull();
    expect(document.querySelector('.dg-graph-edge-tag--dimmed')).toBeNull();
    expect(nodeData('e2').highlight).toBe('none');
  });

  it('treats a selection that names no node in this trace as no selection', async () => {
    // A stale `?eid`. There is no node to anchor the question on, so the tab says so
    // instead of silently highlighting nothing and letting that read as "nothing flowed".
    renderLineage({ selectedEntityId: 'ghost' });
    await waitFor(() => expect(screen.getByTestId('lineage-graph')).toBeInTheDocument());

    expect(screen.getByText(/not in this trace’s entity set/i)).toBeInTheDocument();
    expect(document.querySelector('.dg-graph-edge--dimmed')).toBeNull();
  });

  // --- JOB 2: fan-in AND fan-out, with the traversed LEGS lit.

  it('lights the fan-in route and dims the rest', async () => {
    // e1 → e2's request leg is the upstream route. The response leg is on neither
    // route here, so it dims.
    renderLineage({ selectedEntityId: 'e2', fanin: FANIN_E1 });
    await waitFor(() => expect(edgeEls()).toHaveLength(2));
    await waitFor(() =>
      expect(document.querySelector('[data-id="i1:request"] .dg-graph-edge--upstream')).not.toBeNull(),
    );

    expect(document.querySelector('[data-id="i1:response"] .dg-graph-edge--dimmed')).not.toBeNull();
    // The node is marked upstream, and NOT downstream — the two claims stay apart.
    expect(nodeData('e1').lineage).toMatchObject({ isUpstream: true, isDownstream: false });
    expect(nodeData('e1').lineage.hops).toBe(1);
  });

  it('lights the fan-out route, distinguishably from fan-in', async () => {
    renderLineage({ selectedEntityId: 'e2', fanout: FANOUT_E1 });
    await waitFor(() => expect(edgeEls()).toHaveLength(2));
    await waitFor(() =>
      expect(
        document.querySelector('[data-id="i1:response"] .dg-graph-edge--downstream'),
      ).not.toBeNull(),
    );

    // The OTHER leg is the one lit, with the OTHER class — so the two directions are
    // not interchangeable in the DOM.
    expect(document.querySelector('[data-id="i1:response"] .dg-graph-edge--upstream')).toBeNull();
    expect(nodeData('e1').lineage).toMatchObject({ isUpstream: false, isDownstream: true });
  });

  it('shows BOTH directions at once, keeping each identifiable', async () => {
    // The core of the "show both, do not blend" decision. e1 is upstream AND
    // downstream (data went out and came back), and both facts survive on one node —
    // a single-valued role would have had to discard one.
    renderLineage({ selectedEntityId: 'e2', fanin: FANIN_E1, fanout: FANOUT_E1 });
    await waitFor(() => expect(edgeEls()).toHaveLength(2));
    await waitFor(() => expect(nodeData('e1').lineage.isUpstream).toBe(true));

    expect(nodeData('e1').lineage.isDownstream).toBe(true);
    // Each route keeps its own leg and its own class.
    expect(document.querySelector('[data-id="i1:request"] .dg-graph-edge--upstream')).not.toBeNull();
    expect(
      document.querySelector('[data-id="i1:response"] .dg-graph-edge--downstream'),
    ).not.toBeNull();
    // Nothing is dimmed now — both legs are on a route.
    expect(document.querySelector('.dg-graph-edge--dimmed')).toBeNull();
  });

  it('marks a leg on BOTH routes with both classes', async () => {
    // One leg claimed by both walks — a genuine cycle. Both classes land, so the
    // stylesheet's combined rule has something to key on.
    const bothLegs = [mkTraversed('i1', 'request', 'e1', 'e2', 1)];
    renderLineage({
      selectedEntityId: 'e2',
      fanin: mkReach('fanin', {
        entities: [mkReached('e1', 1)],
        legs: bothLegs,
        state: 'derived',
      }),
      fanout: mkReach('fanout', {
        entities: [mkReached('e1', 1)],
        legs: bothLegs,
        state: 'derived',
      }),
    });
    await waitFor(() => expect(edgeEls()).toHaveLength(2));
    await waitFor(() =>
      expect(document.querySelector('[data-id="i1:request"] .dg-graph-edge--upstream')).not.toBeNull(),
    );

    const req = document.querySelector('[data-id="i1:request"]')!;
    expect(req.querySelector('.dg-graph-edge--downstream')).not.toBeNull();
    expect(edgeData('i1:request').lineage).toMatchObject({ isUpstream: true, isDownstream: true });
  });

  it('grades multi-hop distance off the response `hops`', async () => {
    renderLineage({
      selectedEntityId: 'e2',
      fanin: mkReach('fanin', {
        entities: [mkReached('e1', 1), mkReached('e3', 2)],
        legs: [mkTraversed('i1', 'request', 'e1', 'e2', 1)],
        state: 'derived',
      }),
    });
    await waitFor(() => expect(nodeEls()).toHaveLength(3));
    await waitFor(() => expect(nodeData('e1').lineage.hops).toBe(1));

    expect(nodeData('e3').lineage.hops).toBe(2);
  });

  it('carries every lineage claim on the node data the hover text is built from', async () => {
    // The accessibility half of the treatment: hue is never the only carrier — the
    // node's `<title>` names each claim in words (see `nodeTitle`).
    //
    // Asserted on the DATA, not on the rendered `<title>`, because the title lives
    // inside the node content jsdom culls (see `nodeData`'s note — the node's `<g>` is
    // verifiably childless here). `nodeTitle` is a pure function of exactly these
    // fields, so this pins the facts reaching it. The node title's WORDING is
    // MANUALLY-VERIFY-ONLY for that reason; the equivalent wording on an EDGE is
    // asserted for real in the next case, since edge content is not culled.
    renderLineage({
      selectedEntityId: 'e2',
      fanin: FANIN_E1,
      fanout: FANOUT_E1,
      summary: {
        sources: ['agent:(p,a)'],
        destinations: [],
        status: 'complete',
        stopped_at_seq: null,
      },
    });
    await waitFor(() => expect(nodeEls()).toHaveLength(3));
    await waitFor(() => expect(nodeData('e1').lineage.isDataSource).toBe(true));

    // All three claims at once on one node — the co-occurrence a single-valued role
    // could not have represented.
    expect(nodeData('e1').lineage).toMatchObject({
      isDataSource: true,
      isUpstream: true,
      isDownstream: true,
      hops: 1,
    });
  });

  it('names the route in the edge accessible name too', async () => {
    renderLineage({ selectedEntityId: 'e2', fanin: FANIN_E1 });
    await waitFor(() => expect(edgeEls()).toHaveLength(2));
    await waitFor(() =>
      expect(document.querySelector('[data-id="i1:request"] .dg-graph-edge--upstream')).not.toBeNull(),
    );

    expect(
      screen.getByRole('button', { name: /on the upstream route/i }),
    ).toBeInTheDocument();
  });

  it('encodes the highlight in NO raw hex colour', async () => {
    // House rule, and also the accessibility one: every colour is a `--dg-*` token
    // reference, so there is nothing hue-shaped inlined on a highlighted element.
    renderLineage({ selectedEntityId: 'e2', fanin: FANIN_E1 });
    await waitFor(() => expect(edgeEls()).toHaveLength(2));

    const carrier = document.querySelector('[data-id="i1:request"]')!.innerHTML;
    expect(carrier).not.toMatch(/#[0-9a-f]{3,8}\b/i);
  });

  it("keeps an error leg's red AND its route class — the two are independent", async () => {
    // A failed leg can be the leg that carried the data, and a reader needs both
    // facts. One combined class would make one of them unrepresentable.
    renderLineage({
      selectedEntityId: 'e2',
      interactions: [
        mkIx({
          id: 'i1',
          caller_entity_id: 'e1',
          callee_entity_id: 'e2',
          legs: [mkLeg('request', 1, true), mkLeg('response', 2, false)],
          any_error: true,
        }),
      ],
      fanin: FANIN_E1,
    });
    await waitFor(() => expect(edgeEls()).toHaveLength(2));
    await waitFor(() =>
      expect(document.querySelector('[data-id="i1:request"] .dg-graph-edge--upstream')).not.toBeNull(),
    );

    const req = document.querySelector('[data-id="i1:request"]')!;
    expect(req.querySelector('.dg-graph-edge--error')).not.toBeNull();
    expect(req.innerHTML).toContain('var(--dg-color-error)');
  });

  // --- Edge selection COMPOSED with the lineage highlight. Two different questions,
  // both allowed to be active at once (see EdgeData): the highlight is "what carried
  // the selected entity's data", the selection is "whose detail panel is open".
  // Neither may silently mask the other, and the cases below are what pins that.

  it('offers edge selection on THIS tab too, not only on Execution Flow', async () => {
    // The anti-fork guard for the click, matching the block's other sharing cases: the
    // two tabs are one graph, so an arrow that opened a panel on one and did nothing on
    // the other would be exactly the divergence the shared renderer exists to prevent.
    const onSelect = vi.fn();
    renderLineage({ onSelectInteraction: onSelect });
    await waitFor(() => expect(edgeEls()).toHaveLength(2));

    clickEdge('i1:response');
    expect(onSelect).toHaveBeenCalledWith('i1');
  });

  it('keeps BOTH class axes on one edge that is on a route AND selected', async () => {
    // The composition, at its most load-bearing: the request leg is on the upstream
    // route and its interaction is the open one. Both classes must be on the element —
    // collapsing them would lose one of two independent facts the reader is told.
    renderLineage({ selectedEntityId: 'e2', selectedInteractionId: 'i1', fanin: FANIN_E1 });
    await waitFor(() => expect(edgeEls()).toHaveLength(2));
    await waitFor(() =>
      expect(document.querySelector('[data-id="i1:request"] .dg-graph-edge--upstream')).not.toBeNull(),
    );

    const req = document.querySelector('[data-id="i1:request"]')!;
    expect(req.querySelector('.dg-graph-edge--selected')).not.toBeNull();
    // The model agrees: the axes live on separate fields, not one squashed enum.
    expect(edgeData('i1:request')).toMatchObject({ highlight: 'carrier', isSelected: true });
    expect(edgeData('i1:request').lineage).toMatchObject({ isUpstream: true });
  });

  it('keeps a DIMMED edge selectable, and marks it selected while still dimmed', async () => {
    // THE case the composition has to get right. With e2 selected for lineage, the
    // response leg is outside the answer and therefore `--dimmed`. A reader may still
    // click it — dimming is a de-emphasis, not a disablement, and PF puts no
    // `pointer-events` gate on a dimmed edge (the only such rule in its stylesheet is
    // `pointer-events: none` while `.pf-m-dragging`). Making a dimmed arrow inert would
    // mean an active lineage question silently removed most of the graph's click
    // targets, which is a far worse surprise than a dim arrow that responds.
    //
    // So BOTH classes land on the same element: `--dimmed` still states the lineage
    // fact, `--selected` states the selection, and the stylesheet's
    // `.dg-graph-edge--dimmed.dg-graph-edge--selected { opacity: 1 }` is what stops the
    // selection from being invisible. THAT rule's effect is a computed style and is NOT
    // asserted here (no stylesheet applies in jsdom — see the file header); what is
    // asserted is the class pair it keys on, which is the part a unit test can honestly
    // own.
    const onSelect = vi.fn();
    renderLineage({ selectedEntityId: 'e2', fanin: FANIN_E1, onSelectInteraction: onSelect });
    await waitFor(() => expect(edgeEls()).toHaveLength(2));
    // The premise: the response leg really is dimmed.
    await waitFor(() =>
      expect(document.querySelector('[data-id="i1:response"] .dg-graph-edge--dimmed')).not.toBeNull(),
    );

    // A dimmed edge is still a click target.
    clickEdge('i1:response');
    expect(onSelect).toHaveBeenCalledWith('i1');
  });

  it('shows the selected treatment on a dimmed edge rather than losing it to the dim', async () => {
    // The other half of the case above, now with the selection actually applied: the
    // reader clicked a leg that the lineage highlight had dimmed, so the arrow they
    // picked must carry the selected marks WHILE keeping its dimmed class. If the
    // treatment were suppressed here, clicking a dimmed arrow would open a panel with
    // no visible sign of which arrow it belonged to — the exact feedback gap this whole
    // treatment exists to close.
    renderLineage({ selectedEntityId: 'e2', selectedInteractionId: 'i1', fanin: FANIN_E1 });
    await waitFor(() => expect(edgeEls()).toHaveLength(2));
    await waitFor(() =>
      expect(document.querySelector('[data-id="i1:response"] .dg-graph-edge--dimmed')).not.toBeNull(),
    );

    const resp = document.querySelector('[data-id="i1:response"]')!;
    // Both, on one element — neither erased by the other.
    expect(resp.querySelector('.dg-graph-edge--selected')).not.toBeNull();
    // The tag pair too, which is what the opacity override in global.css also covers —
    // a selected-but-dimmed leg whose seq number stayed faded would be the one number
    // the reader wants and cannot read.
    expect(resp.querySelector('.dg-graph-edge-tag--dimmed')).not.toBeNull();
    expect(resp.querySelector('.dg-graph-edge-tag--selected')).not.toBeNull();
    expect(edgeData('i1:response')).toMatchObject({ highlight: 'dimmed', isSelected: true });
  });

  it('does not let a selection dim anything on its own', async () => {
    // Selection is not a question about lineage, so selecting an interaction with NO
    // entity selected must leave every other arrow at full strength. Otherwise the
    // selected treatment would quietly acquire the highlight's de-emphasis semantics
    // and "I clicked an arrow" would start reading as "everything else is not the
    // answer" — the same conflation `'none'` vs `'dimmed'` exists to prevent.
    renderLineage({ selectedEntityId: null, selectedInteractionId: 'i1' });
    await waitFor(() => expect(edgeEls()).toHaveLength(2));

    expect(document.querySelector('.dg-graph-edge--dimmed')).toBeNull();
    expect(document.querySelector('.dg-graph-edge--carrier')).toBeNull();
    // …while the selection itself IS shown.
    expect(document.querySelectorAll('.dg-graph-edge--selected')).toHaveLength(2);
  });

  // --- NODE CLICK → the same entity selection the Entities table makes.

  /**
   * WHY THESE FIRE THE EVENT INSTEAD OF CLICKING THE NODE. A node's `<g>` renders
   * COMPLETELY EMPTY in jsdom — verified, not assumed: the element is
   * `<g data-id="e2" data-kind="node" transform="translate(260, 40)"></g>` with no
   * children at all, because PF culls node CONTENT on a zero-size surface (edge
   * content is not culled, which is why `clickEdge` works). The `onClick` PF binds
   * lives on an inner `<g>` that therefore does not exist, so a `fireEvent.click` on
   * the outer element reaches no handler and would fail for a reason that has nothing
   * to do with this wiring.
   *
   * So the test is taken to the honest seam: PF's own `SELECTION_EVENT`, fired with
   * the payload `withSelection` would fire. That covers everything this component
   * actually owns — the id→entity resolution and the routing to the right callback.
   * What it does NOT cover is whether the click reaches `onSelect` at all, which is
   * PF's binding plus d3-drag's click suppression; that is
   * MANUALLY-VERIFY-ONLY in a real browser (and is why
   * `DraggableKindColouredNode`'s note cites the library sources rather than a test).
   */
  function fireSelection(ids: string[]) {
    act(() => {
      capturedController!.fireEvent(SELECTION_EVENT, ids);
    });
  }

  it('routes a NODE selection to onSelectEntity', async () => {
    // The user-facing ask: "click on any other entity". Coexists with the drag because
    // d3-drag suppresses a moved gesture's trailing click and passes a stationary one
    // through — see DraggableKindColouredNode's note for the library evidence.
    const onSelectEntity = vi.fn();
    renderLineage({ onSelectEntity });
    await waitFor(() => expect(nodeEls()).toHaveLength(3));

    fireSelection(['e2']);
    expect(onSelectEntity).toHaveBeenCalledWith('e2');
  });

  it('does not report an entity selection as an interaction selection', async () => {
    // The two callbacks are different questions. A node selection must not reach
    // `onSelectInteraction` and close/replace the open interaction panel by accident.
    const onSelectInteraction = vi.fn();
    const onSelectEntity = vi.fn();
    renderLineage({ onSelectInteraction, onSelectEntity });
    await waitFor(() => expect(nodeEls()).toHaveLength(3));

    fireSelection(['e1']);
    expect(onSelectEntity).toHaveBeenCalledWith('e1');
    expect(onSelectInteraction).not.toHaveBeenCalled();
  });

  it('still routes an EDGE selection to onSelectInteraction, not to the entity callback', async () => {
    // The other half: adding the node arm must not have stolen the edge's own routing.
    const onSelectInteraction = vi.fn();
    const onSelectEntity = vi.fn();
    renderLineage({ onSelectInteraction, onSelectEntity });
    await waitFor(() => expect(edgeEls()).toHaveLength(2));

    fireSelection(['i1:request']);
    expect(onSelectInteraction).toHaveBeenCalledWith('i1');
    expect(onSelectEntity).not.toHaveBeenCalled();
  });

  it('ignores a selection id that names neither a drawn node nor a drawn edge', async () => {
    // Doing nothing is the honest response to an id we cannot interpret — reading it
    // as "no interaction" would close the reader's open panel for no visible reason.
    const onSelectInteraction = vi.fn();
    const onSelectEntity = vi.fn();
    renderLineage({ onSelectInteraction, onSelectEntity });
    await waitFor(() => expect(nodeEls()).toHaveLength(3));

    fireSelection(['no-such-element']);
    expect(onSelectInteraction).not.toHaveBeenCalled();
    expect(onSelectEntity).not.toHaveBeenCalled();
  });

  // --- The three server states plus the failed read, kept apart ON SCREEN, PER DIRECTION.

  it('says "not yet computed" for a PENDING direction, and names the frontier', async () => {
    // The eventual-consistency window: adjacency EXISTS but carries no derived row.
    // An empty highlight with no words would read as "we checked and found nothing".
    renderLineage({
      selectedEntityId: 'e2',
      fanin: mkReach('fanin', { state: 'pending', pending_frontier: ['e1'] }),
    });
    await waitFor(() =>
      expect(screen.getByText(/Upstream lineage not yet computed/i)).toBeInTheDocument(),
    );

    // Explicitly says this is not the "nothing flowed" verdict — the one sentence the
    // whole tri-state discipline hangs on.
    expect(screen.getByText(/this is not "nothing flowed"/i)).toBeInTheDocument();
    // The frontier is named, so "not yet" is visible rather than looking like a dead end.
    expect(screen.getByText(/1 entity is on the pending frontier/i)).toBeInTheDocument();
    // The frontier node is MARKED, and deliberately not dimmed.
    await waitFor(() => expect(nodeData('e1').lineage.isFrontier).toBe(true));
  });

  it('says "derived, not missing" for a DERIVED but empty direction', async () => {
    // ADR-0028 D3/D15: a real derived answer, and the state a graph cannot show by
    // itself — an unhighlighted picture looks identical to the pending case above.
    renderLineage({
      selectedEntityId: 'e2',
      fanin: mkReach('fanin', { state: 'derived', entities: [], legs: [] }),
    });
    await waitFor(() =>
      expect(screen.getByText(/No upstream entities — derived, not missing/i)).toBeInTheDocument(),
    );

    expect(screen.getByText(/derived answer, not a missing one/i)).toBeInTheDocument();
    expect(screen.queryByText(/Upstream lineage not yet computed/i)).toBeNull();
  });

  it('says NO-ADJACENT as its own state — the one complete empty answer', async () => {
    // Structurally distinct from pending: there is no leg to wait FOR, so telling the
    // reader to wait would be telling them to wait forever.
    renderLineage({
      selectedEntityId: 'e2',
      fanin: mkReach('fanin', { state: 'no-adjacent' }),
    });
    await waitFor(() =>
      expect(screen.getByText(/Nothing upstream of this entity in this trace/i)).toBeInTheDocument(),
    );

    // BOTH directions are `no-adjacent` in this fixture, so BOTH say so — which is the
    // point rather than a nuisance: each direction reports its own outcome, and one
    // silently standing in for the other is what `DirectionNotice`-rendered-twice
    // prevents. Hence `getAllByText` and an explicit count.
    expect(screen.getByText(/Nothing downstream of this entity in this trace/i)).toBeInTheDocument();
    expect(screen.getAllByText(/not a derivation still pending/i)).toHaveLength(2);
    expect(screen.queryByText(/Upstream lineage not yet computed/i)).toBeNull();
  });

  it('reports a failed DIRECTION read as unknown, without erasing the other', async () => {
    // The fourth state, and the reason the two directions keep their own error flags:
    // "we could not ask downstream" must not look like "we know nothing at all".
    renderLineage({ selectedEntityId: 'e2', fanin: FANIN_E1, fanout: null });
    await waitFor(() =>
      expect(screen.getByText(/Downstream lineage could not be loaded/i)).toBeInTheDocument(),
    );

    expect(screen.getByText(/unknown/i)).toBeInTheDocument();
    // The good half survived and is still on screen.
    expect(screen.getByText(/1 upstream entity, over 1 interaction leg/i)).toBeInTheDocument();
    await waitFor(() => expect(nodeData('e1').lineage.isUpstream).toBe(true));
  });

  it('keeps TRUNCATED separate from the pending frontier', async () => {
    // ADR-0028 D15: the frontier says "ask again later", truncation says "derived, but
    // this answer declined to return it all". Merging them would send a reader to poll
    // for something only a wider bound produces.
    renderLineage({
      selectedEntityId: 'e2',
      fanin: mkReach('fanin', {
        entities: [mkReached('e1', 1)],
        legs: [mkTraversed('i1', 'request', 'e1', 'e2', 1)],
        state: 'derived',
        truncated: true,
      }),
    });
    await waitFor(() =>
      expect(screen.getByText(/Upstream answer is truncated/i)).toBeInTheDocument(),
    );

    expect(screen.getByText(/waiting will not complete this/i)).toBeInTheDocument();
    // Not reported as a frontier, which would be the wrong instruction.
    expect(screen.queryByText(/on the pending frontier/i)).toBeNull();
  });

  it('reports a frontier ALONGSIDE a derived answer — both can be true', async () => {
    renderLineage({
      selectedEntityId: 'e2',
      fanin: mkReach('fanin', {
        entities: [mkReached('e1', 1)],
        legs: [mkTraversed('i1', 'request', 'e1', 'e2', 1)],
        state: 'derived',
        pending_frontier: ['e3'],
      }),
    });
    await waitFor(() =>
      expect(screen.getByText(/Upstream answer may grow/i)).toBeInTheDocument(),
    );

    expect(screen.getByText(/not yet known/i)).toBeInTheDocument();
    // The derived part is still stated as an answer.
    expect(screen.getByText(/1 upstream entity, over 1 interaction leg/i)).toBeInTheDocument();
  });

  it('does not editorialise a large answer as thorough tracing', async () => {
    // ADR-0028 D14: these reads inherit matcher quality, so a full fanout must not be
    // read as evidence provenance was exhaustively traced. The wording says so.
    renderLineage({ selectedEntityId: 'e2', fanout: FANOUT_E1 });
    await waitFor(() =>
      expect(screen.getByText(/1 downstream entity, over 1 interaction leg/i)).toBeInTheDocument(),
    );

    expect(screen.getByText(/not evidence of thorough tracing/i)).toBeInTheDocument();
  });

  it('says nothing about either direction while no entity is selected', async () => {
    // No question has been asked, so there is no answer for anything to qualify.
    renderLineage({ selectedEntityId: null });
    await waitFor(() => expect(screen.getByTestId('lineage-graph')).toBeInTheDocument());

    expect(screen.queryByText(/Upstream lineage/i)).toBeNull();
    expect(screen.queryByText(/Downstream lineage/i)).toBeNull();
    // Same faithful re-wording as the case above.
    expect(
      screen.getByText(/Select an entity to trace this source’s data in and out/i),
    ).toBeInTheDocument();
  });

  // --- Trace-level coverage.

  it('surfaces the trace-level partial coverage beside the answer it qualifies', async () => {
    // Reused verbatim from LineageCoverageAlert rather than reworded: the truncation
    // is a fact about the whole trace, so this tab must not invent a second phrasing.
    renderLineage({ selectedEntityId: 'e2', status: 'partial', fanin: FANIN_E1 });
    await waitFor(() =>
      expect(screen.getByText(/not the complete set of data sources/i)).toBeInTheDocument(),
    );
  });

  it('shows no coverage banner on a complete trace, or while the status is unknown', async () => {
    // Silence IS the no-truncation statement (LineageCoverageAlert's own rule), and a
    // `null` status warns about nothing because no truncation has been established.
    const complete = renderLineage({ selectedEntityId: 'e2', status: 'complete', fanin: FANIN_E1 });
    await waitFor(() => expect(screen.getByTestId('lineage-graph')).toBeInTheDocument());
    expect(screen.queryByText(/not the complete set of data sources/i)).toBeNull();
    complete.unmount();

    renderLineage({ selectedEntityId: 'e2', status: null, fanin: FANIN_E1 });
    await waitFor(() => expect(screen.getByTestId('lineage-graph')).toBeInTheDocument());
    expect(screen.queryByText(/not the complete set of data sources/i)).toBeNull();
  });

  // --- Loading / error / empty: the SHARED read states.

  it('shows the same spinner as the graph tab while the reads are in flight', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockImplementation(() => new Promise(() => {}));
    renderWithProviders(
      <LineageGraph
        traceId="T1"
        entities={[]}
        interactions={[]}
        status={null}
        isLineageError={false}
        selectedEntityId={null}
      />,
    );

    expect(screen.getByLabelText(/Loading execution flow graph/i)).toBeInTheDocument();
    expect(screen.queryByTestId('lineage-graph')).not.toBeInTheDocument();
  });

  it('reports a failed ENTITIES read as an error, not as an empty graph', async () => {
    // Shared `graphReadState`, so the wording is the graph tab's — two tabs over one
    // dataset must not disagree about what a failed read looks like.
    mockLineageApi({ entities: null, interactions: [] });
    renderWithProviders(
      <LineageGraph
        traceId="T1"
        entities={[]}
        interactions={[]}
        status={null}
        isLineageError={false}
        selectedEntityId={null}
      />,
    );

    await waitFor(() =>
      expect(screen.getByText(/Could not load the execution flow/i)).toBeInTheDocument(),
    );
    expect(screen.queryByTestId('lineage-graph')).not.toBeInTheDocument();
  });

  it('renders the shared empty state — not a "select an entity" prompt over nothing', async () => {
    renderLineage({ entities: [], interactions: [] });

    await waitFor(() => expect(screen.getByText(/No execution flow/i)).toBeInTheDocument());
    expect(screen.queryByTestId('lineage-graph')).not.toBeInTheDocument();
    expect(screen.queryByText(/Select an entity to trace/i)).toBeNull();
    // …and no source picker either: with no graph there is nothing to trace a source
    // through, so the whole notices strip is short-circuited by `graphReadState`.
    expect(screen.queryByText(/Choose a data source to trace/i)).toBeNull();
  });

  // --- The drag lifecycle, unchanged by the refactor.

  it('keeps a moved node where it was put when the highlight CHANGES', async () => {
    // The regression the refactor could most plausibly have introduced: the highlight
    // is baked into the element `data`, so changing it rebuilds the model — and a
    // rebuild is exactly what used to lose the reader's drag. The positions must ride
    // through a highlight change the same way they ride through a poll.
    const { rerender } = renderLineage({ selectedEntityId: null });
    await waitFor(() => expect(nodeAt('e1')).toEqual(cell(0, 0)));

    moveNode('e1', 777, 555);
    await waitFor(() => expect(nodeAt('e1')).toEqual({ x: 777, y: 555 }));

    // Select an entity — a new highlight, therefore a model push. `lineageSource` is
    // carried through both renders: the answer is source-relative now, so without it the
    // rerender would have no question to ask and no highlight to survive.
    mockLineageApi({
      entities: ENTITIES,
      interactions: [mkIx({ id: 'i1', caller_entity_id: 'e1', callee_entity_id: 'e2' }, 1)],
      summary: SUMMARY_WITH_SOURCE,
      fanin: FANIN_E1,
    });
    rerender(
      <LineageGraph
        traceId="T1"
        entities={ENTITIES}
        interactions={[mkIx({ id: 'i1', caller_entity_id: 'e1', callee_entity_id: 'e2' }, 1)]}
        status="complete"
        isLineageError={false}
        selectedEntityId="e2"
        lineageSource={SOURCE_E1}
      />,
    );
    await waitFor(() =>
      expect(document.querySelector('[data-id="i1:request"] .dg-graph-edge--upstream')).not.toBeNull(),
    );

    // The drag survived the highlight change…
    expect(nodeAt('e1')).toEqual({ x: 777, y: 555 });
    // …and the untouched nodes are still on their derived cells, so one drag did not
    // freeze the rest of the layout. e3 is isolated here → trailing column.
    expect(nodeAt('e2')).toEqual(cell(1, 0));
    expect(nodeAt('e3')).toEqual(cell(2, 0));
  });

  it('keeps a moved node where it was put when the SELECTION changes', async () => {
    // The drag-survival invariant on the tab that also has a highlight active, so the
    // model push is driven by two dependencies at once rather than one. Same
    // guarantee, harder setup — this is where a re-introduced rebuild-per-render would
    // show up first.
    const { rerender } = renderLineage({
      selectedEntityId: 'e2',
      selectedInteractionId: null,
      fanin: FANIN_E1,
    });
    await waitFor(() => expect(nodeAt('e1')).toEqual(cell(0, 0)));

    moveNode('e1', 777, 555);
    await waitFor(() => expect(nodeAt('e1')).toEqual({ x: 777, y: 555 }));

    rerender(
      <LineageGraph
        traceId="T1"
        entities={ENTITIES}
        interactions={[mkIx({ id: 'i1', caller_entity_id: 'e1', callee_entity_id: 'e2' }, 1)]}
        status="complete"
        isLineageError={false}
        selectedEntityId="e2"
        lineageSource={SOURCE_E1}
        selectedInteractionId="i1"
      />,
    );
    await waitFor(() =>
      expect(document.querySelector('[data-id="i1:request"] .dg-graph-edge--selected')).not.toBeNull(),
    );

    expect(nodeAt('e1')).toEqual({ x: 777, y: 555 });
    // The highlight was NOT lost by the selection push — both are still baked in.
    expect(edgeData('i1:request')).toMatchObject({ highlight: 'carrier', isSelected: true });
    expect(edgeData('i1:request').lineage).toMatchObject({ isUpstream: true });
    expect(nodeAt('e2')).toEqual(cell(1, 0));
  });

  it('puts moved nodes back on their grid cells when Reset View is clicked', async () => {
    // The affordance the layout rewrite re-implemented, confirmed to still work on the
    // tab that did not exist when it was written.
    renderLineage({ selectedEntityId: 'e2', fanin: FANIN_E1 });
    await waitFor(() => expect(nodeAt('e1')).toEqual(cell(0, 0)));

    moveNode('e1', 777, 555);
    await waitFor(() => expect(nodeAt('e1')).toEqual({ x: 777, y: 555 }));

    await userEvent.click(screen.getByRole('button', { name: /^Reset View$/i }));

    await waitFor(() => expect(nodeAt('e1')).toEqual(cell(0, 0)));
  });
});
