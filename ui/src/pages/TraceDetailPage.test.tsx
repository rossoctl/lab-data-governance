import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { screen, waitFor, waitForElementToBeRemoved, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Routes, Route, useNavigate } from 'react-router-dom';
import { renderWithProviders } from '../test/renderWithProviders';
import { LocationProbe } from '../test/LocationProbe';
import { TraceDetailPage } from './TraceDetailPage';

// The trace-detail view hosts a two-way switcher: Span tree | Interaction flow.
// The active view is a URL path segment (/traces/{id}/spans | /flow) — the URL is
// the source of truth, so reload/bookmark/back restore the tab. The trace id and
// view both come from the route (:traceId/:view).
//
// The Execution Flow graph is NOT a third segment: it presents the flow view's own
// entities/interactions reads, so it lives inside that view as its third `?legs`
// tab beside Tree and Flat. Its tests are the `?legs=graph` block near the bottom.

// Mount the page under the real nested route so :traceId and :view resolve, and
// include a LocationProbe so tests can assert the URL after a tab click.
function harness() {
  return (
    <>
      <Routes>
        <Route path="/traces/:traceId/:view" element={<TraceDetailPage />} />
      </Routes>
      <LocationProbe />
    </>
  );
}

// A Back affordance for the Back-button test. These tests mount under a
// MemoryRouter, whose history lives in memory rather than on window.history, so
// window.history.back() never reaches it — the router's own navigate(-1) does.
function BackButton() {
  const navigate = useNavigate();
  return (
    <button type="button" onClick={() => navigate(-1)}>
      test-back
    </button>
  );
}

function mockFetch() {
  (fetch as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
    if (url === '/api/traces/T1') {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          trace_id: 'T1',
          listing_root: {
            seq: 1, trace_id: 'T1', span_id: 'root', parent_id: null,
            name: 'root-span', started_at: '2026-05-01T12:00:00Z',
            service_name: 'svc', kind: 'SERVER', error: null, attributes: {},
          },
          counts: { total: 1, in_window: 1, error_count: 0 },
          in_time_window: true,
        }),
      };
    }
    // children / interactions / entities — empty is fine for the switcher test.
    return { ok: true, status: 200, json: async () => ({ spans: [], interactions: [], entities: [] }) };
  });
}

// A mock where the root has one child. Used to prove a ?sel deep link to a
// NON-root span reveals it — the case where the reveal must run against a tree
// that mounts only after the (async) trace load, so a reveal fired before the
// tree exists must be retried once it does.
const CHILD = {
  seq: 2, trace_id: 'T1', span_id: 'child-1', parent_id: 'root',
  name: 'child-span', started_at: '2026-05-01T12:00:01Z',
  service_name: 'svc', kind: 'INTERNAL', error: null, attributes: {},
  observed_at: '2026-05-01T12:00:01Z', arrival_seq: 2, in_time_window: true,
  status_message: null, events: null, links: null, ended_at: null,
  otlp: null, scope: null, resource_attributes: null,
};
function mockFetchWithChild() {
  (fetch as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
    if (url === '/api/traces/T1') {
      return {
        ok: true, status: 200,
        json: async () => ({
          trace_id: 'T1',
          listing_root: {
            seq: 1, trace_id: 'T1', span_id: 'root', parent_id: null,
            name: 'root-span', started_at: '2026-05-01T12:00:00Z',
            service_name: 'svc', kind: 'SERVER', error: null, attributes: {},
          },
          counts: { total: 2, in_window: 2, error_count: 0 }, in_time_window: true,
        }),
      };
    }
    // The reveal walk fetches the target span by id, then pages the root's
    // children so it can splice the child under root.
    if (url === '/api/traces/T1/spans/child-1') {
      return { ok: true, status: 200, json: async () => CHILD };
    }
    if (url.includes('/spans/root/children')) {
      return { ok: true, status: 200, json: async () => ({ spans: [CHILD] }) };
    }
    return { ok: true, status: 200, json: async () => ({ spans: [], interactions: [], entities: [] }) };
  });
}

// A DEEP mock: root → mid → leaf. Revealing `leaf` expands BOTH root and mid.
// On a remount, the auto-expand opens only the root (showing mid), so `leaf`
// surfaces again ONLY if mid's expansion was preserved — the exact state a
// flow-tab round-trip must not lose.
const MID = {
  seq: 2, trace_id: 'T1', span_id: 'mid', parent_id: 'root',
  name: 'mid-span', started_at: '2026-05-01T12:00:01Z',
  service_name: 'svc', kind: 'INTERNAL', error: null, attributes: {},
  observed_at: '2026-05-01T12:00:01Z', arrival_seq: 2, in_time_window: true,
  status_message: null, events: null, links: null, ended_at: null,
  otlp: null, scope: null, resource_attributes: null,
};
const LEAF = { ...MID, seq: 3, span_id: 'leaf', parent_id: 'mid', name: 'leaf-span' };
function mockFetchWithGrandchild() {
  (fetch as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
    if (url === '/api/traces/T1') {
      return {
        ok: true, status: 200,
        json: async () => ({
          trace_id: 'T1',
          listing_root: {
            seq: 1, trace_id: 'T1', span_id: 'root', parent_id: null,
            name: 'root-span', started_at: '2026-05-01T12:00:00Z',
            service_name: 'svc', kind: 'SERVER', error: null, attributes: {},
          },
          counts: { total: 3, in_window: 3, error_count: 0 }, in_time_window: true,
        }),
      };
    }
    if (url.endsWith('/spans/leaf')) return { ok: true, status: 200, json: async () => LEAF };
    if (url.endsWith('/spans/mid')) return { ok: true, status: 200, json: async () => MID };
    if (url.endsWith('/spans/root')) return { ok: true, status: 200, json: async () => ({ ...MID, span_id: 'root', parent_id: null, name: 'root-span', kind: 'SERVER' }) };
    if (url.includes('/spans/root/children')) return { ok: true, status: 200, json: async () => ({ spans: [MID] }) };
    if (url.includes('/spans/mid/children')) return { ok: true, status: 200, json: async () => ({ spans: [LEAF] }) };
    return { ok: true, status: 200, json: async () => ({ spans: [], interactions: [], entities: [] }) };
  });
}

// A mock with one interaction + evidence span, so Add-to-highlights has
// something to pin and reveal.
function mockFetchWithFlow() {
  (fetch as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
    if (url === '/api/traces/T1') {
      return {
        ok: true, status: 200,
        json: async () => ({
          trace_id: 'T1',
          listing_root: {
            seq: 1, trace_id: 'T1', span_id: 'root', parent_id: null,
            name: 'root-span', started_at: '2026-05-01T12:00:00Z',
            service_name: 'svc', kind: 'SERVER', error: null, attributes: {},
          },
          counts: { total: 2, in_window: 2, error_count: 0 }, in_time_window: true,
        }),
      };
    }
    if (url.endsWith('/interactions')) {
      return {
        ok: true, status: 200,
        json: async () => ({ interactions: [{
          id: 'i1', caller_entity_id: null, callee_entity_id: null,
          summary: 'the interaction', parent_interaction_id: null,
          legs: [
            { leg_type: 'request', occurred_at: '2026-05-01T12:00:00Z', payload_hash: null, error: false, seq: 1 },
            { leg_type: 'response', occurred_at: '2026-05-01T12:00:01Z', payload_hash: null, error: false, seq: 2 },
          ],
          duration_seconds: 1, any_error: false,
          span_count: 1, anchor_count: 1,
        }] }),
      };
    }
    if (url.endsWith('/entities')) return { ok: true, status: 200, json: async () => ({ entities: [] }) };
    if (url.includes('/interactions/i1/spans')) {
      return { ok: true, status: 200, json: async () => ({ spans: [
        { span_id: 'ev-span', role: 'anchor', parent_id: 'root', kind: 'CLIENT', service_name: 'svc' },
      ] }) };
    }
    return { ok: true, status: 200, json: async () => ({ spans: [] }) };
  });
}

/**
 * Like `mockFetchWithFlow`, but with two ENTITIES and an interaction whose
 * caller/callee both resolve to them — so `deriveGraph` yields real nodes and an
 * edge and the Execution Flow tab renders its surface rather than its empty
 * state. `mockFetchWithFlow` returns no entities at all, which is exactly why it
 * is kept for the empty-state case below.
 */
function mockFetchWithGraph() {
  (fetch as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
    if (url === '/api/traces/T1') {
      return {
        ok: true, status: 200,
        json: async () => ({
          trace_id: 'T1',
          listing_root: {
            seq: 1, trace_id: 'T1', span_id: 'root', parent_id: null,
            name: 'root-span', started_at: '2026-05-01T12:00:00Z',
            service_name: 'svc', kind: 'SERVER', error: null, attributes: {},
          },
          counts: { total: 2, in_window: 2, error_count: 0 }, in_time_window: true,
        }),
      };
    }
    if (url.endsWith('/interactions')) {
      return {
        ok: true, status: 200,
        json: async () => ({ interactions: [{
          id: 'i1', caller_entity_id: 'e1', callee_entity_id: 'e2',
          summary: 'agent calls search', parent_interaction_id: null,
          legs: [
            { leg_type: 'request', occurred_at: '2026-05-01T12:00:00Z', payload_hash: null, error: false, seq: 1 },
            { leg_type: 'response', occurred_at: '2026-05-01T12:00:01Z', payload_hash: null, error: false, seq: 2 },
          ],
          duration_seconds: 1, any_error: false,
          span_count: 1, anchor_count: 1,
        }] }),
      };
    }
    if (url.endsWith('/entities')) {
      return {
        ok: true, status: 200,
        json: async () => ({ entities: [
          { id: 'e1', kind: 'agent', natural_key: 'agent:(p,a)', display_name: 'agent-a', detected_from: 'span' },
          { id: 'e2', kind: 'tool', natural_key: 'tool:(p,svc)', display_name: 'search', detected_from: 'span' },
        ] }),
      };
    }
    if (url.includes('/entities/e1/spans') || url.includes('/interactions/i1/spans')) {
      return { ok: true, status: 200, json: async () => ({ spans: [
        { span_id: 'ev-span', role: 'anchor', parent_id: 'root', kind: 'CLIENT', service_name: 'svc' },
      ] }) };
    }
    // The Lineage tab's two reads (ADR-0028 D14). Answered with well-formed,
    // non-claiming responses rather than left to the `{spans: []}` fallthrough below:
    // this file's cases are about ROUTING (`?legs` / `?eid` params surviving), so the
    // lineage answer must be valid enough not to crash and empty enough not to assert
    // anything. `state: 'no-adjacent'` is the one value that means "complete answer,
    // nothing there" (D15) — the honest choice for a fixture with no lineage seeded.
    if (url.includes('/data-lineage-graph')) {
      return { ok: true, status: 200, json: async () => ({
        direction: url.includes('direction=fanin') ? 'fanin' : 'fanout',
        seed_entity_id: 'e1',
        entities: [], legs: [], state: 'no-adjacent',
        pending_frontier: [], truncated: false,
        status: 'complete', stopped_at_seq: null,
      }) };
    }
    if (url.includes('/data-lineage-summary')) {
      return { ok: true, status: 200, json: async () => ({
        sources: [], destinations: [], status: 'complete', stopped_at_seq: null,
      }) };
    }
    return { ok: true, status: 200, json: async () => ({ spans: [] }) };
  });
}

/**
 * Await the lazily-loaded Execution Flow graph past its Suspense boundary.
 *
 * `findBy*`'s 1000ms default is not enough for the FIRST `?legs=graph` render in a
 * file: `React.lazy(() => import('./ExecutionFlowGraph'))` has to transform and
 * evaluate PF topology (plus d3 / dagre / mobx) before the component exists, which
 * is exactly the ~387kB the production build now keeps out of the main bundle —
 * measured at ~1.6s under Vitest's transform pipeline. This is still `waitFor`
 * polling on the real condition, not a fixed sleep; only the deadline is raised,
 * and it is raised once here so no individual test grows a magic number.
 */
const GRAPH_CHUNK_TIMEOUT = 10_000;
async function findGraph() {
  return screen.findByTestId('execution-flow-graph', undefined, {
    timeout: GRAPH_CHUNK_TIMEOUT,
  });
}

describe('TraceDetailPage', () => {
  beforeEach(() => vi.stubGlobal('fetch', vi.fn()));
  afterEach(() => vi.unstubAllGlobals());

  it('shows the trace id and a two-way view switcher, Span tree active for /spans', async () => {
    mockFetch();
    renderWithProviders(harness(), { route: '/traces/T1/spans' });

    // Both switcher tabs are present, and nothing else: the Execution Flow graph
    // is reached through the flow view's `?legs` tabs, not from up here, so on the
    // spans tab there is no graph tab in the document at all.
    await waitFor(() => expect(screen.getByRole('tab', { name: /Span tree/i })).toBeInTheDocument());
    expect(screen.getByRole('tab', { name: /Interaction flow/i })).toBeInTheDocument();
    expect(screen.getAllByRole('tab')).toHaveLength(2);
    expect(screen.queryByRole('tab', { name: /Execution Flow/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: /Graph/i })).not.toBeInTheDocument();

    // The /spans segment drives the active tab.
    expect(screen.getByRole('tab', { name: /Span tree/i })).toHaveAttribute(
      'aria-selected',
      'true',
    );
  });

  it('activates the Interaction flow tab for a /flow deep link', async () => {
    mockFetch();
    renderWithProviders(harness(), { route: '/traces/T1/flow' });

    await waitFor(() =>
      expect(screen.getByRole('tab', { name: /Interaction flow/i })).toHaveAttribute(
        'aria-selected',
        'true',
      ),
    );
  });

  it('renders a breadcrumb back to the recent-traces list and shows the trace id', async () => {
    mockFetch();
    renderWithProviders(harness(), { route: '/traces/T1/spans' });

    // Crumb 1 is a real link back to the list.
    const backLink = await screen.findByRole('link', { name: /Recent traces/i });
    expect(backLink).toHaveAttribute('href', '/traces');
    // Crumb 2 (the "you are here" crumb) carries the full trace id.
    expect(screen.getByText('T1')).toBeInTheDocument();
  });

  it('navigates the URL to /spans when Add-to-highlights is clicked in the flow view', async () => {
    mockFetchWithFlow();
    renderWithProviders(harness(), { route: '/traces/T1/flow' });
    // Select the interaction, add it to highlights.
    await userEvent.click(await screen.findByText(/1 \(1 anchor\)/));
    await userEvent.click(await screen.findByRole('button', { name: /Add to highlights/i }));
    // The view flips back to the Span tree (URL + active tab) so the highlighted
    // spans are revealed.
    await waitFor(() =>
      expect(screen.getByTestId('location')).toHaveTextContent('/traces/T1/spans'),
    );
    expect(screen.getByRole('tab', { name: /Span tree/i })).toHaveAttribute(
      'aria-selected',
      'true',
    );
  });

  it('shows a "highlighting…" spinner while a reveal is in flight, then hides it', async () => {
    // Add-to-highlights kicks off a cross-view reveal (flow → tree, expand the
    // pinned spans' ancestors). While that async walk runs, a spinner sits to
    // the right of the "Span tree" tab caption; it clears once reveal settles.
    mockFetchWithFlow();
    renderWithProviders(harness(), { route: '/traces/T1/flow' });

    await userEvent.click(await screen.findByText(/1 \(1 anchor\)/));
    await userEvent.click(await screen.findByRole('button', { name: /Add to highlights/i }));

    // The spinner appears (labelled for a11y) as soon as the reveal is requested.
    const spinner = await screen.findByLabelText(/highlighting/i);
    expect(spinner).toBeInTheDocument();
    // …and is removed once the reveal completes (ancestors expanded + scrolled).
    await waitForElementToBeRemoved(() => screen.queryByLabelText(/highlighting/i));
  });

  it('drops the highlighting spinner if the user leaves the tree while a reveal is still in flight', async () => {
    // Stuck-spinner guard: Add-to-highlights sets the spinner + navigates to the
    // tree, then the reveal runs an async ancestor walk. If the user clicks back
    // to the flow tab while that walk is still pending, the reveal's target row
    // is gone — its .finally() would clear the spinner eventually, but by then a
    // NEW reveal may own it, so relying on it is wrong. Leaving the tree must
    // clear the spinner directly. Here we HOLD the reveal's first fetch open so
    // it is provably still in flight when we switch tabs.
    let releaseReveal!: () => void;
    const revealGate = new Promise<void>((r) => { releaseReveal = r; });
    (fetch as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
      if (url === '/api/traces/T1') {
        return {
          ok: true, status: 200,
          json: async () => ({
            trace_id: 'T1',
            listing_root: {
              seq: 1, trace_id: 'T1', span_id: 'root', parent_id: null,
              name: 'root-span', started_at: '2026-05-01T12:00:00Z',
              service_name: 'svc', kind: 'SERVER', error: null, attributes: {},
            },
            counts: { total: 2, in_window: 2, error_count: 0 }, in_time_window: true,
          }),
        };
      }
      if (url.endsWith('/interactions')) {
        return {
          ok: true, status: 200,
          json: async () => ({ interactions: [{
            id: 'i1', caller_entity_id: null, callee_entity_id: null,
            summary: 'the interaction', parent_interaction_id: null,
            legs: [
              { leg_type: 'request', occurred_at: '2026-05-01T12:00:00Z', payload_hash: null, error: false, seq: 1 },
              { leg_type: 'response', occurred_at: '2026-05-01T12:00:01Z', payload_hash: null, error: false, seq: 2 },
            ],
            duration_seconds: 1, any_error: false,
            span_count: 1, anchor_count: 1,
          }] }),
        };
      }
      if (url.endsWith('/entities')) return { ok: true, status: 200, json: async () => ({ entities: [] }) };
      if (url.includes('/interactions/i1/spans')) {
        return { ok: true, status: 200, json: async () => ({ spans: [
          { span_id: 'ev-span', role: 'anchor', parent_id: 'root', kind: 'CLIENT', service_name: 'svc' },
        ] }) };
      }
      // reveal()'s first hop: fetch the evidence span by id. Hold it open until
      // the test releases it, so the reveal is unambiguously in flight.
      if (url.endsWith('/spans/ev-span')) {
        await revealGate;
        return { ok: true, status: 200, json: async () => (
          { seq: 2, trace_id: 'T1', span_id: 'ev-span', parent_id: 'root', name: 'ev', attributes: {} }
        ) };
      }
      return { ok: true, status: 200, json: async () => ({ spans: [] }) };
    });

    renderWithProviders(harness(), { route: '/traces/T1/flow' });
    await userEvent.click(await screen.findByText(/1 \(1 anchor\)/));
    await userEvent.click(await screen.findByRole('button', { name: /Add to highlights/i }));
    // Spinner shown while the reveal's fetch is gated open.
    await screen.findByLabelText(/highlighting/i);
    // Bounce back to the flow tab while the reveal is still pending.
    await userEvent.click(screen.getByRole('tab', { name: /Interaction flow/i }));
    // The spinner is cleared by leaving the tree, not left hanging.
    await waitFor(() =>
      expect(screen.queryByLabelText(/highlighting/i)).not.toBeInTheDocument(),
    );
    // Now let the held reveal finish; its late .finally() must not resurrect the
    // spinner (the token was bumped on leave, so it no longer matches).
    releaseReveal();
    await waitFor(() =>
      expect(screen.queryByLabelText(/highlighting/i)).not.toBeInTheDocument(),
    );
  });

  it('writes /flow to the URL when the Interaction flow tab is clicked', async () => {
    mockFetch();
    renderWithProviders(harness(), { route: '/traces/T1/spans' });

    const flowTab = await screen.findByRole('tab', { name: /Interaction flow/i });
    await userEvent.click(flowTab);
    await waitFor(() => expect(flowTab).toHaveAttribute('aria-selected', 'true'));
    // The URL reflects the active tab.
    expect(screen.getByTestId('location')).toHaveTextContent('/traces/T1/flow');
    // Flow view mounted: with no derived data it shows its empty-state hint.
    await waitFor(() =>
      expect(
        screen.getByText(/No interaction data for this trace yet/i),
      ).toBeInTheDocument(),
    );
  });

  it('redirects an unknown view segment to the canonical /spans', async () => {
    mockFetch();
    renderWithProviders(harness(), { route: '/traces/T1/bogus' });

    await waitFor(() =>
      expect(screen.getByTestId('location')).toHaveTextContent('/traces/T1/spans'),
    );
    expect(screen.getByRole('tab', { name: /Span tree/i })).toHaveAttribute(
      'aria-selected',
      'true',
    );
  });

  // --- Selection round-trip: the flow view's selected row is mirrored into the
  // URL (?iid / ?eid), so a deep link / reload restores it.

  // The flow view's Interactions tab (Tree | Flat) is the `?legs` param, so the
  // presentation survives reload/bookmark/back like every other view state.

  it('defaults the Interactions tab to Tree and writes no ?legs param', async () => {
    mockFetchWithFlow();
    renderWithProviders(harness(), { route: '/traces/T1/flow' });

    const tree = await screen.findByRole('tab', { name: 'Tree' });
    expect(tree).toHaveAttribute('aria-selected', 'true');
    // The tree table is showing, not the per-leg one.
    expect(screen.getByLabelText('Interactions')).toBeInTheDocument();
    expect(screen.queryByLabelText('Interactions (flat)')).not.toBeInTheDocument();
    // Canonical URL: the default tab writes nothing.
    expect(screen.getByTestId('location')).not.toHaveTextContent('legs');
  });

  it('writes ?legs=flat when the Flat tab is selected, and swaps the table', async () => {
    mockFetchWithFlow();
    renderWithProviders(harness(), { route: '/traces/T1/flow' });

    await userEvent.click(await screen.findByRole('tab', { name: 'Flat' }));
    await waitFor(() =>
      expect(screen.getByTestId('location')).toHaveTextContent('legs=flat'),
    );
    expect(await screen.findByLabelText('Interactions (flat)')).toBeInTheDocument();
    expect(screen.queryByLabelText('Interactions')).not.toBeInTheDocument();
  });

  it('restores the Flat tab from ?legs=flat on load', async () => {
    mockFetchWithFlow();
    renderWithProviders(harness(), { route: '/traces/T1/flow?legs=flat' });

    expect(await screen.findByLabelText('Interactions (flat)')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Flat' })).toHaveAttribute('aria-selected', 'true');
  });

  it('drops ?legs when switching back to Tree, keeping the URL canonical', async () => {
    mockFetchWithFlow();
    renderWithProviders(harness(), { route: '/traces/T1/flow?legs=flat' });

    await userEvent.click(await screen.findByRole('tab', { name: 'Tree' }));
    await waitFor(() =>
      expect(screen.getByTestId('location')).not.toHaveTextContent('legs'),
    );
    expect(screen.getByLabelText('Interactions')).toBeInTheDocument();
  });

  it('writes ?legs=diagram when the Interaction diagram tab is selected, and swaps the table', async () => {
    // The same URL contract the Flat tab has, restated for the new presentation —
    // the page enumerates no tab values (the read goes through `parseLegViewKey`,
    // the write drops the param iff it is the default), so this pins that the
    // generic path really does carry a value it never mentions by name.
    //
    // `mockFetchWithGraph`, not `mockFetchWithFlow`: the diagram needs both
    // participants RESOLVED to draw a lifeline, and the latter fixture's
    // caller/callee are deliberately null (it is the empty-state fixture).
    mockFetchWithGraph();
    renderWithProviders(harness(), { route: '/traces/T1/flow' });

    await userEvent.click(await screen.findByRole('tab', { name: 'Interaction diagram' }));
    await waitFor(() =>
      expect(screen.getByTestId('location')).toHaveTextContent('legs=diagram'),
    );
    expect(await screen.findByTestId('interaction-diagram')).toBeInTheDocument();
    expect(screen.queryByLabelText('Interactions')).not.toBeInTheDocument();
  });

  it('restores the Interaction diagram tab from ?legs=diagram on load', async () => {
    mockFetchWithGraph();
    renderWithProviders(harness(), { route: '/traces/T1/flow?legs=diagram' });

    expect(await screen.findByTestId('interaction-diagram')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Interaction diagram' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
  });

  it('keeps ?legs=diagram across a row selection (the two params coexist)', async () => {
    // Clicking a diagram message selects its parent INTERACTION and writes ?iid;
    // the tab param must survive that rewrite rather than being dropped.
    mockFetchWithGraph();
    renderWithProviders(harness(), { route: '/traces/T1/flow?legs=diagram' });

    await userEvent.click((await screen.findAllByTestId('dg-seq-message'))[0]);
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('iid=i1'));
    expect(screen.getByTestId('location')).toHaveTextContent('legs=diagram');
  });

  it('keeps ?legs=flat across a row selection (the two params coexist)', async () => {
    mockFetchWithFlow();
    renderWithProviders(harness(), { route: '/traces/T1/flow?legs=flat' });

    // Clicking a leg row selects its parent interaction and writes ?iid; the
    // tab param must survive that rewrite rather than being dropped.
    const flat = await screen.findByLabelText('Interactions (flat)');
    await userEvent.click(within(flat).getAllByText('request')[0]);
    await waitFor(() =>
      expect(screen.getByTestId('location')).toHaveTextContent('iid=i1'),
    );
    expect(screen.getByTestId('location')).toHaveTextContent('legs=flat');
  });

  it('restores the flow interaction selection from ?iid on load', async () => {
    mockFetchWithFlow();
    renderWithProviders(harness(), { route: '/traces/T1/flow?iid=i1' });

    // The interaction row auto-selects (highlighted) once the tables load, and
    // its detail panel shows.
    await waitFor(() =>
      expect(screen.getByText(/the interaction/i)).toBeInTheDocument(),
    );
  });

  it('writes ?iid to the URL when a flow interaction row is selected', async () => {
    mockFetchWithFlow();
    renderWithProviders(harness(), { route: '/traces/T1/flow' });

    await userEvent.click(await screen.findByText(/1 \(1 anchor\)/));
    await waitFor(() =>
      expect(screen.getByTestId('location')).toHaveTextContent('iid=i1'),
    );
  });

  // --- Tree selection round-trip: clicking a span mirrors it into ?sel, and a
  // ?sel deep link reveals + selects that span on load.

  it('writes ?sel to the URL when a span row is clicked in the tree', async () => {
    mockFetch();
    renderWithProviders(harness(), { route: '/traces/T1/spans' });

    // The root span row renders from the listing root; click it to select.
    const rootRow = await screen.findByText('root-span');
    await userEvent.click(rootRow);
    await waitFor(() =>
      expect(screen.getByTestId('location')).toHaveTextContent('sel=root'),
    );
  });

  it('reveals + selects the ?sel span on a spans deep link', async () => {
    mockFetch();
    renderWithProviders(harness(), { route: '/traces/T1/spans?sel=root' });

    await waitFor(() => expect(screen.getByText('root-span')).toBeInTheDocument());
    // The reveal effect selects the root span, so the detail panel leaves its
    // empty state and shows the span's sections (Timing appears only when a
    // span is selected). This proves the ?sel deep link restored the selection.
    await waitFor(() => expect(screen.getByText('Timing')).toBeInTheDocument());
    expect(
      screen.queryByText(/Select a span to view its details/i),
    ).not.toBeInTheDocument();
  });

  it('reveals + selects a NON-root ?sel span on a cold deep link (tree mounts after load)', async () => {
    // Regression guard: the reveal must retry once the tree mounts. On a cold
    // deep link the trace is still loading, so SpanTree is not yet rendered when
    // the effect first fires; a reveal fired against a null treeRef must not be
    // marked done, or the child span never surfaces.
    mockFetchWithChild();
    renderWithProviders(harness(), { route: '/traces/T1/spans?sel=child-1' });

    // The child is only visible if reveal expanded the root under it — it shows
    // in the tree row and, once selected, in the detail panel too (≥1 element).
    await waitFor(() =>
      expect(screen.getAllByText('child-span').length).toBeGreaterThan(0),
    );
    // And it is the selected span: the detail panel shows its span_id "child-1"
    // (only present when a span is selected).
    await waitFor(() => expect(screen.getByText('child-1')).toBeInTheDocument());
  });

  it('keeps the tree expanded state across a flow-tab round-trip', async () => {
    // Regression: after a reveal expands a DEEP span (root → mid → leaf),
    // switching to the flow tab and back must NOT collapse the tree to depth 1.
    // The tree stays mounted (hidden on flow), so mid's expansion survives —
    // rather than unmounting and re-seeding from the root, whose auto-expand
    // opens only the root (showing mid), leaving leaf hidden. A tab click drops
    // ?sel, so nothing could re-reveal leaf; it survives only via kept state.
    const inTreeRow = (name: string) =>
      screen.queryAllByText(name).some((el) => el.closest('[data-testid="span-row"]') !== null);

    mockFetchWithGrandchild();
    renderWithProviders(harness(), { route: '/traces/T1/spans?sel=leaf' });

    // Reveal expanded root + mid — the deep leaf row is present in the tree.
    await waitFor(() => expect(inTreeRow('leaf-span')).toBe(true));

    // Round-trip: to the flow tab, then back to the span tree tab.
    await userEvent.click(screen.getByRole('tab', { name: /Interaction flow/i }));
    await waitFor(() =>
      expect(screen.getByTestId('location')).toHaveTextContent('/traces/T1/flow'),
    );
    await userEvent.click(screen.getByRole('tab', { name: /Span tree/i }));
    await waitFor(() =>
      expect(screen.getByTestId('location')).toHaveTextContent('/traces/T1/spans'),
    );

    // The deep leaf row is STILL rendered in the tree (mid still expanded), not
    // collapsed away to depth 1.
    expect(inTreeRow('leaf-span')).toBe(true);
  });

  it('keeps MANUALLY-expanded nodes across a flow-tab round-trip', async () => {
    // Sibling to the reveal test: a node the user opened BY HAND (clicking its
    // expand triangle, no highlight/reveal, no ?sel) must also survive the
    // round-trip. This is the full-state guarantee — not just reveal-expanded
    // ancestors. Open the page fresh; auto-expand shows root → mid; hand-expand
    // mid to show leaf; round-trip; leaf must remain visible.
    const inTreeRow = (name: string) =>
      screen.queryAllByText(name).some((el) => el.closest('[data-testid="span-row"]') !== null);

    mockFetchWithGrandchild();
    renderWithProviders(harness(), { route: '/traces/T1/spans' });

    // Root auto-expands to show mid; leaf is inside mid's collapsed subtree.
    await waitFor(() => expect(inTreeRow('mid-span')).toBe(true));
    expect(inTreeRow('leaf-span')).toBe(false);

    // MANUALLY expand mid by clicking its triangle (aria-label "expand mid-span").
    await userEvent.click(screen.getByRole('button', { name: /expand mid-span/i }));
    await waitFor(() => expect(inTreeRow('leaf-span')).toBe(true));

    // Round-trip: to the flow tab, then back to the span tree tab.
    await userEvent.click(screen.getByRole('tab', { name: /Interaction flow/i }));
    await waitFor(() =>
      expect(screen.getByTestId('location')).toHaveTextContent('/traces/T1/flow'),
    );
    await userEvent.click(screen.getByRole('tab', { name: /Span tree/i }));
    await waitFor(() =>
      expect(screen.getByTestId('location')).toHaveTextContent('/traces/T1/spans'),
    );

    // The hand-expanded leaf row is STILL rendered — manual expansion survived.
    expect(inTreeRow('leaf-span')).toBe(true);
  });

  // --- Execution Flow: the flow view's THIRD `?legs` tab, beside Tree and Flat.
  //
  // It used to be a top-level view segment (`/traces/{id}/graph`, a peer of
  // /spans and /flow). It draws the same two reads the flow tables list, so it
  // moved inside the flow view as `?legs=graph`. The tests below are the ported
  // form of that segment's contract — deep-linkable, survives reload, works with
  // Back, unrecognised values coerce — restated against the query param, plus the
  // param-specific cases the path segment could not have.
  //
  // The graph is lazy-loaded (React.lazy + Suspense, so PF topology's ~387kB
  // stays out of the main bundle), so every assertion about it has to await the
  // chunk — hence findBy*/waitFor throughout rather than a synchronous getBy.

  it('offers Execution Flow as a third ?legs tab beside Tree and Flat, and NOT as a top-level tab', async () => {
    mockFetchWithFlow();
    renderWithProviders(harness(), { route: '/traces/T1/flow' });

    // Wait for the flow view (and therefore its ?legs tab bar) to be up.
    const legTree = await screen.findByRole('tab', { name: 'Tree' });

    // Two tab bars on screen. PF puts the `aria-label` on the Tabs wrapper
    // (unroled) rather than on the inner `role="tablist"`, so they are told apart
    // by the tabs each one owns rather than by an accessible name.
    const bars = screen.getAllByRole('tablist').map((list) =>
      within(list)
        .getAllByRole('tab')
        .map((t) => t.textContent),
    );
    // The top-level row is back to TWO tabs; Execution Flow is not one of them,
    // and the ?legs row carries it beside Tree, Flat, the Interaction diagram
    // (which sits between Flat and the graph — it renders the Flat list's own rows
    // as a picture, so it belongs next to that tab) and Lineage, which sits
    // immediately after the graph because it IS the graph plus a highlight.
    expect(bars).toContainEqual(['Span tree', 'Interaction flow']);
    expect(bars).toContainEqual([
      'Tree',
      'Flat',
      'Interaction diagram',
      'Execution Flow',
      'Lineage',
    ]);
    expect(bars).toHaveLength(2);

    // The default Tree is active, so the graph tab is not.
    expect(legTree).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tab', { name: /Execution Flow/i })).toHaveAttribute(
      'aria-selected',
      'false',
    );
  });

  it('renders the graph and NOT the interactions table for a ?legs=graph deep link', async () => {
    // Deep-linkable / reload-safe: the URL alone is enough to land on the graph,
    // which is the property the old `/graph` segment carried and this must keep.
    mockFetchWithGraph();
    renderWithProviders(harness(), { route: '/traces/T1/flow?legs=graph' });

    // The lazy chunk resolves and the graph mounts (this trace HAS entities, so
    // it is the real surface, not the empty state).
    expect(await findGraph()).toBeInTheDocument();
    expect(
      screen.getByRole('tab', { name: /Execution Flow/i }),
    ).toHaveAttribute('aria-selected', 'true');
    // The graph stands in for the interactions table — neither presentation of it
    // is rendered alongside.
    expect(screen.queryByLabelText('Interactions')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Interactions (flat)')).not.toBeInTheDocument();
    // …but the Entities table above it stays: it is a fact of the flow VIEW, not
    // part of the interactions table the graph replaced.
    expect(screen.getByLabelText('Entities')).toBeInTheDocument();
  });

  it('still shows the graph empty state through ?legs=graph when nothing is derived', async () => {
    // Ported from the old /graph deep-link test, whose mock returned no entities:
    // an empty derivation must say so rather than render a blank box, and that
    // wiring has to survive the move behind Suspense.
    mockFetchWithFlow();
    renderWithProviders(harness(), { route: '/traces/T1/flow?legs=graph' });

    expect(
      await screen.findByText(/No entities or interactions for this trace yet/i),
    ).toBeInTheDocument();
  });

  it('writes ?legs=graph when the Execution Flow tab is clicked', async () => {
    mockFetchWithGraph();
    renderWithProviders(harness(), { route: '/traces/T1/flow' });

    const graphTab = await screen.findByRole('tab', { name: /Execution Flow/i });
    await userEvent.click(graphTab);
    await waitFor(() =>
      expect(screen.getByTestId('location')).toHaveTextContent('legs=graph'),
    );
    expect(graphTab).toHaveAttribute('aria-selected', 'true');
    // Still the flow view — the path segment does not change, only the param.
    expect(screen.getByTestId('location')).toHaveTextContent('/traces/T1/flow');
    expect(await findGraph()).toBeInTheDocument();
  });

  it('drops ?legs when switching from the graph back to Tree', async () => {
    // The drop-the-default rule, restated for the graph: leaving it for Tree must
    // clear the param rather than write ?legs=tree.
    mockFetchWithGraph();
    renderWithProviders(harness(), { route: '/traces/T1/flow?legs=graph' });

    await findGraph();
    await userEvent.click(screen.getByRole('tab', { name: 'Tree' }));
    await waitFor(() =>
      expect(screen.getByTestId('location')).not.toHaveTextContent('legs'),
    );
    // The interactions table is back and the graph is gone.
    expect(screen.getByLabelText('Interactions')).toBeInTheDocument();
    expect(screen.queryByTestId('execution-flow-graph')).not.toBeInTheDocument();
  });

  it('coerces an unrecognised ?legs value to Tree', async () => {
    // The same coercion `?legs=flat` always had, now that a third value exists:
    // adding `graph` to LegViewKey must not make a typo resolve to anything.
    mockFetchWithGraph();
    renderWithProviders(harness(), { route: '/traces/T1/flow?legs=grapf' });

    const tree = await screen.findByRole('tab', { name: 'Tree' });
    expect(tree).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByLabelText('Interactions')).toBeInTheDocument();
    expect(screen.queryByTestId('execution-flow-graph')).not.toBeInTheDocument();
  });

  it('keeps ?legs=graph across a row selection (the two params coexist)', async () => {
    // The `?legs` + `?iid`/`?eid` coexistence the Flat tab already pins, restated
    // for the graph: the Entities table stays clickable while the graph is up, so
    // selecting an entity must not drop the tab param.
    mockFetchWithGraph();
    renderWithProviders(harness(), { route: '/traces/T1/flow?legs=graph' });

    await findGraph();
    await userEvent.click(within(screen.getByLabelText('Entities')).getByText('agent-a'));
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('eid=e1'));
    expect(screen.getByTestId('location')).toHaveTextContent('legs=graph');
    // And the graph is still the active presentation, not swapped out by the click.
    expect(screen.getByTestId('execution-flow-graph')).toBeInTheDocument();
  });

  it('keeps the graph inside the detail gutter so the panel never overlaps it', async () => {
    // The reason the graph is rendered INSIDE FlowTables' `dg-detail-gutter` div
    // rather than beside it: the detail panel floats fixed over the right of the
    // content, and the gutter is what makes the content shrink out from under it.
    // A graph outside that div would be covered by the panel on every selection.
    mockFetchWithGraph();
    renderWithProviders(harness(), { route: '/traces/T1/flow?legs=graph' });

    const graph = await findGraph();
    // No selection yet → no gutter anywhere (the tables reclaim full width).
    expect(graph.closest('.dg-detail-gutter')).toBeNull();
    // Select an entity to float the panel…
    await userEvent.click(within(screen.getByLabelText('Entities')).getByText('agent-a'));
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('eid=e1'));
    // …and the graph is now a DESCENDANT of the gutter, so it shrinks rather than
    // being overlapped.
    expect(screen.getByTestId('execution-flow-graph').closest('.dg-detail-gutter')).not.toBeNull();
  });

  // --- `?legs=lineage`: the same graph with the selected entity's data sources
  // highlighted. The URL-level contract only — this file owns `?legs`, and the
  // highlight's own logic lives in lib/lineageGraph.test.ts.

  /** The Lineage tab's own surface, past the SAME lazy chunk the graph rides. */
  async function findLineageGraph() {
    return screen.findByTestId('lineage-graph', undefined, { timeout: GRAPH_CHUNK_TIMEOUT });
  }

  it('writes ?legs=lineage when the Lineage tab is clicked, and swaps the table', async () => {
    // The same generic URL path every non-default presentation takes: this page
    // enumerates no tab values (the read goes through `parseLegViewKey`, the write
    // drops the param iff it is the default), so this pins that the generic path
    // really does carry a value the page never names.
    mockFetchWithGraph();
    renderWithProviders(harness(), { route: '/traces/T1/flow' });

    const tab = await screen.findByRole('tab', { name: 'Lineage' });
    await userEvent.click(tab);
    await waitFor(() =>
      expect(screen.getByTestId('location')).toHaveTextContent('legs=lineage'),
    );
    expect(tab).toHaveAttribute('aria-selected', 'true');
    // Still the flow view — the path segment does not change, only the param.
    expect(screen.getByTestId('location')).toHaveTextContent('/traces/T1/flow');
    expect(await findLineageGraph()).toBeInTheDocument();
    expect(screen.queryByLabelText('Interactions')).not.toBeInTheDocument();
    // The Entities table stays, and here it is load-bearing rather than merely kept:
    // it is the only way to select the entity this tab answers about.
    expect(screen.getByLabelText('Entities')).toBeInTheDocument();
  });

  it('restores the Lineage tab from ?legs=lineage on load', async () => {
    mockFetchWithGraph();
    renderWithProviders(harness(), { route: '/traces/T1/flow?legs=lineage' });

    expect(await findLineageGraph()).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Lineage' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    // The Execution Flow tab is NOT also active: they are two tabs over one renderer,
    // not one tab with two names.
    expect(screen.getByRole('tab', { name: /Execution Flow/i })).toHaveAttribute(
      'aria-selected',
      'false',
    );
    expect(screen.queryByTestId('execution-flow-graph')).not.toBeInTheDocument();
  });

  it('drops ?legs when switching from Lineage back to Tree', async () => {
    // The drop-the-default rule, restated for the newest tab.
    mockFetchWithGraph();
    renderWithProviders(harness(), { route: '/traces/T1/flow?legs=lineage' });

    await findLineageGraph();
    await userEvent.click(screen.getByRole('tab', { name: 'Tree' }));
    await waitFor(() =>
      expect(screen.getByTestId('location')).not.toHaveTextContent('legs'),
    );
    expect(screen.getByLabelText('Interactions')).toBeInTheDocument();
    expect(screen.queryByTestId('lineage-graph')).not.toBeInTheDocument();
  });

  it('coerces an unrecognised near-miss of ?legs=lineage to Tree', async () => {
    // Adding `lineage` to LegViewKey must not make a typo resolve to it — nor make
    // the RESOURCE name (`data-lineage`) a valid tab value, which would be an easy
    // thing to get wrong given they sit next to each other in this feature.
    mockFetchWithGraph();
    renderWithProviders(harness(), { route: '/traces/T1/flow?legs=data-lineage' });

    expect(await screen.findByRole('tab', { name: 'Tree' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    expect(screen.getByLabelText('Interactions')).toBeInTheDocument();
    expect(screen.queryByTestId('lineage-graph')).not.toBeInTheDocument();
  });

  it('keeps ?legs=lineage across the entity selection that DRIVES it', async () => {
    // The coexistence case that matters most for this tab: selecting an entity is not
    // an aside here, it is how the tab is used at all. So the `?eid` write must carry
    // the tab param through rather than dropping it — otherwise the act of asking the
    // question would navigate away from the answer.
    mockFetchWithGraph();
    renderWithProviders(harness(), { route: '/traces/T1/flow?legs=lineage' });

    await findLineageGraph();
    await userEvent.click(within(screen.getByLabelText('Entities')).getByText('agent-a'));
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('eid=e1'));
    expect(screen.getByTestId('location')).toHaveTextContent('legs=lineage');
    // Both params in one URL means the whole question is deep-linkable: "this trace,
    // the lineage view, this entity".
    expect(screen.getByTestId('lineage-graph')).toBeInTheDocument();
  });

  it('shows the graph empty state through ?legs=lineage when nothing is derived', async () => {
    // The empty-derivation wiring is SHARED with the Execution Flow tab (one
    // `graphReadState`), and this is the guard that it really is shared: an empty
    // trace must say so on this tab too rather than render a blank box with a
    // "select an entity" instruction over nothing.
    mockFetchWithFlow();
    renderWithProviders(harness(), { route: '/traces/T1/flow?legs=lineage' });

    expect(
      await screen.findByText(/No entities or interactions for this trace yet/i),
    ).toBeInTheDocument();
    expect(screen.queryByTestId('lineage-graph')).not.toBeInTheDocument();
  });

  it('redirects the retired /graph segment to the canonical /spans', async () => {
    // `graph` is no longer a view segment, so an old bookmark to it is now just an
    // unknown segment and takes the same canonicalising redirect as any typo.
    // Called out explicitly because it is a deliberate break of an old deep link,
    // not an accident.
    mockFetch();
    renderWithProviders(harness(), { route: '/traces/T1/graph' });

    await waitFor(() =>
      expect(screen.getByTestId('location')).toHaveTextContent('/traces/T1/spans'),
    );
    expect(screen.getByRole('tab', { name: /Span tree/i })).toHaveAttribute(
      'aria-selected',
      'true',
    );
  });

  it('still redirects an unknown view segment to /spans', async () => {
    // Regression guard on the canonicalising guard itself, kept from when `graph`
    // was a third segment: a typo'd segment must resolve to nothing.
    mockFetch();
    renderWithProviders(harness(), { route: '/traces/T1/graphh' });

    await waitFor(() =>
      expect(screen.getByTestId('location')).toHaveTextContent('/traces/T1/spans'),
    );
    expect(screen.getByRole('tab', { name: /Span tree/i })).toHaveAttribute(
      'aria-selected',
      'true',
    );
  });

  it('round-trips Span tree → Interaction flow → Execution Flow through the URL', async () => {
    // The cross-view round-trip the old test made across three top-level tabs,
    // restated across the two that remain plus the ?legs tab the third became.
    mockFetchWithGraph();
    renderWithProviders(harness(), { route: '/traces/T1/spans' });

    await userEvent.click(await screen.findByRole('tab', { name: /Interaction flow/i }));
    await waitFor(() =>
      expect(screen.getByTestId('location')).toHaveTextContent('/traces/T1/flow'),
    );
    await userEvent.click(await screen.findByRole('tab', { name: /Execution Flow/i }));
    await waitFor(() =>
      expect(screen.getByTestId('location')).toHaveTextContent('legs=graph'),
    );
    expect(await findGraph()).toBeInTheDocument();
    // Back out to the span tree: leaving the flow view unmounts the graph (it
    // holds no state worth keeping — its model re-derives from the cached reads).
    await userEvent.click(screen.getByRole('tab', { name: /Span tree/i }));
    await waitFor(() =>
      expect(screen.getByTestId('location')).toHaveTextContent('/traces/T1/spans'),
    );
    expect(screen.queryByTestId('execution-flow-graph')).not.toBeInTheDocument();
  });

  it('restores the Execution Flow tab on a Back navigation within the flow view', async () => {
    // The URL is the source of truth, so history navigation must restore the tab
    // with no in-component state involved. `BackButton` calls the router's own
    // navigate(-1) — the MemoryRouter these tests use keeps its history in memory
    // rather than on window.history, so a raw window.history.back() would not
    // reach it.
    //
    // NOTE the ?legs write uses `replace: true` (flipping between presentations of
    // one dataset must not stack history entries), so Back out of the graph does
    // NOT walk the leg tabs. What it walks is the entry the top-level tab click
    // pushed — landing back on the graph deep link this test opened on, tab
    // active, exactly as the old /graph segment test asserted.
    mockFetchWithGraph();
    renderWithProviders(
      <>
        {harness()}
        <BackButton />
      </>,
      { route: '/traces/T1/flow?legs=graph' },
    );

    expect(await findGraph()).toBeInTheDocument();
    // Forward to the span tree (a top-level tab click pushes a history entry)…
    await userEvent.click(screen.getByRole('tab', { name: /Span tree/i }));
    await waitFor(() =>
      expect(screen.getByTestId('location')).toHaveTextContent('/traces/T1/spans'),
    );
    // …then Back must land on ?legs=graph with the Execution Flow tab active.
    await userEvent.click(screen.getByRole('button', { name: 'test-back' }));
    await waitFor(() =>
      expect(screen.getByTestId('location')).toHaveTextContent('legs=graph'),
    );
    expect(await findGraph()).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /Execution Flow/i })).toHaveAttribute(
      'aria-selected',
      'true',
    );
  });

  it('does NOT show the highlighting spinner for a ?sel deep-link restore', async () => {
    // A ?sel deep link fires reveal() too, but that is a page-load restore, not
    // a user highlight action — so the "highlighting…" spinner must stay hidden
    // throughout (it belongs to Add-to-highlights / Span-link jumps only).
    mockFetchWithChild();
    renderWithProviders(harness(), { route: '/traces/T1/spans?sel=child-1' });

    // Wait until the deep-link reveal has fully run (child surfaced + selected).
    await waitFor(() => expect(screen.getByText('child-1')).toBeInTheDocument());
    // No spinner was ever shown for this path.
    expect(screen.queryByLabelText(/highlighting/i)).not.toBeInTheDocument();
  });
});
