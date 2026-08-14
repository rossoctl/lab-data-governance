import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { fireEvent, screen, waitFor, waitForElementToBeRemoved, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Routes, Route, useNavigate } from 'react-router-dom';
import { renderWithProviders } from '../test/renderWithProviders';
import { LocationProbe } from '../test/LocationProbe';
import { TraceDetailPage } from './TraceDetailPage';

// The trace-detail view hosts a FIVE-way switcher: Span tree | Interaction flow |
// Interaction diagram | Execution Flow | Lineage. The active view is a URL path
// segment (/traces/{id}/spans | /flow | /diagram | /graph | /lineage) — the URL is
// the source of truth, so reload/bookmark/back restore the tab. The trace id and
// view both come from the route (:traceId/:view).
//
// THE PROMOTION, and why so many cases in this file were rewritten rather than
// deleted. The Interaction diagram, the Execution Flow graph and the Lineage
// highlight used to be `?legs=diagram|graph|lineage` sub-tabs NESTED inside the flow
// view, on the reasoning that they present the flow view's own two reads
// (entities/interactions) rather than being peer datasets of the span tree. That is
// still true of the DATA and turned out to be the wrong basis for NAVIGATION: three
// of the five readings of a trace were two clicks deep and invisible until you found
// the Interaction flow tab. So they are now top-level views addressed by path
// segment, and every case that asserted "selecting this tab writes ?legs=x" now
// asserts "selecting this tab navigates to /x". The INTENT of each is unchanged —
// deep-linkable, reload-safe, coexists with ?iid/?eid/?src — only the mechanism the
// URL uses to carry the choice moved from the query to the path.
//
// WHAT STAYED NESTED: `Tree` and `Flat` are two renderings of ONE row set, so they
// remain `?legs` sub-tabs under Interaction flow, and `?legs` now carries only those
// two values. Their tests are untouched.
//
// LEGACY `?legs=diagram|graph|lineage` URLs still resolve: the page redirects them to
// the new segment (carrying every other param), so old bookmarks land on the reading
// they named. That redirect has its own block near the bottom.

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
        // `sources` is now ALSO the Lineage tab's choosable source list, so it offers
        // e1's key: with an empty array the tab correctly reports "nothing to trace" and
        // this file's `?src` routing cases would have no source to round-trip. Still
        // non-claiming for the routing cases that ignore it — one source coloured is not
        // an answer about any entity.
        sources: ['agent:(p,a)'], destinations: [], status: 'complete', stopped_at_seq: null,
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

  it('shows the trace id and a five-way view switcher, Span tree active for /spans', async () => {
    mockFetch();
    renderWithProviders(harness(), { route: '/traces/T1/spans' });

    // ALL FIVE readings are offered from up here, IN ORDER, and this is the assertion
    // the promotion exists to satisfy: the diagram, the graph and the Lineage highlight
    // used to be absent from this bar entirely (reachable only via the flow view's
    // `?legs` sub-tabs), which is what made three of the five readings invisible until
    // you had already picked a different one. Asserting the exact list — rather than
    // just "these five exist" — also pins the ORDER: tables first, then the three
    // pictures, with Lineage last because it is the graph plus one more question.
    await waitFor(() => expect(screen.getByRole('tab', { name: /Span tree/i })).toBeInTheDocument());
    expect(screen.getAllByRole('tab').map((t) => t.textContent)).toEqual([
      'Span tree',
      'Interaction flow',
      'Interaction diagram',
      'Execution Flow',
      'Lineage',
    ]);
    // Exactly one tab bar on the spans tab: `FlowTables` (which owns the Tree|Flat
    // sub-tab bar) does not render for the tree view at all, so there is no second
    // tablist to confuse the query above.
    expect(screen.getAllByRole('tablist')).toHaveLength(1);

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

  it('navigates to /diagram when the Interaction diagram tab is selected, and swaps the table', async () => {
    // WAS `writes ?legs=diagram`. Same intent — picking this presentation must be
    // URL-visible so it survives a reload and can be shared — restated for the path
    // segment it is addressed by since the promotion. The choice moved OUT of the query
    // entirely: the tab click is now a real navigation, and `?legs` must not be left
    // behind on the URL as a dead param (the page deletes it for any non-flow target,
    // precisely so the legacy redirect below cannot then bounce off it).
    //
    // `mockFetchWithGraph`, not `mockFetchWithFlow`: the diagram needs both
    // participants RESOLVED to draw a lifeline, and the latter fixture's
    // caller/callee are deliberately null (it is the empty-state fixture).
    mockFetchWithGraph();
    renderWithProviders(harness(), { route: '/traces/T1/flow' });

    await userEvent.click(await screen.findByRole('tab', { name: 'Interaction diagram' }));
    await waitFor(() =>
      expect(screen.getByTestId('location')).toHaveTextContent('/traces/T1/diagram'),
    );
    expect(screen.getByTestId('location')).not.toHaveTextContent('legs');
    expect(await screen.findByTestId('interaction-diagram')).toBeInTheDocument();
    expect(screen.queryByLabelText('Interactions')).not.toBeInTheDocument();
  });

  it('restores the Interaction diagram tab from a /diagram deep link on load', async () => {
    // The reload/bookmark half of the case above: the URL alone lands on the diagram,
    // with the top-level tab active. This is now a PATH segment rather than
    // `?legs=diagram`, so it is also the case that proves the promoted views are
    // first-class routes and not query decorations on the flow route.
    mockFetchWithGraph();
    renderWithProviders(harness(), { route: '/traces/T1/diagram' });

    expect(await screen.findByTestId('interaction-diagram')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Interaction diagram' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    // The Tree|Flat sub-tab bar is GONE on a promoted view: this view IS the
    // presentation, so a bar offering to switch presentation from inside it would be a
    // second control for what the top-level tabs now decide.
    expect(screen.queryByRole('tab', { name: 'Tree' })).not.toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: 'Flat' })).not.toBeInTheDocument();
  });

  it('keeps the /diagram view across a row selection (segment + ?iid coexist)', async () => {
    // WAS `keeps ?legs=diagram across a row selection`. Clicking a diagram message
    // selects its parent INTERACTION and writes ?iid; that write must not disturb the
    // view. Before the promotion the risk was the `?iid` write dropping a sibling
    // param; now it is the reverse — a query write that navigated, or a re-render that
    // lost the segment, would bounce the reader out of the picture they just clicked.
    mockFetchWithGraph();
    renderWithProviders(harness(), { route: '/traces/T1/diagram' });

    await userEvent.click((await screen.findAllByTestId('dg-seq-message'))[0]);
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('iid=i1'));
    expect(screen.getByTestId('location')).toHaveTextContent('/traces/T1/diagram');
    expect(screen.getByTestId('interaction-diagram')).toBeInTheDocument();
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

  // --- Execution Flow: a TOP-LEVEL view again, at `/traces/{id}/graph`.
  //
  // This segment has had all three lives: it was a top-level view, then moved inside
  // the flow view as `?legs=graph` (because it draws the same two reads the flow tables
  // list — a claim about the DATA), and has now been promoted back out to a top-level
  // peer (a claim about NAVIGATION: a reading nobody can find is not a reading). The
  // data argument never changed and is still honoured — all four interaction views
  // still render through one `FlowTables`, so there is still exactly one owner of
  // "selected interaction/entity". Only the address moved.
  //
  // The tests below are that contract — deep-linkable, survives reload, works with
  // Back, coexists with ?iid/?eid — restated against the path segment. They are the
  // `?legs=graph` versions rewritten in place, not new cases, so the guarantees the
  // graph carried through its middle life are the ones still asserted here.
  //
  // The graph is lazy-loaded (React.lazy + Suspense, so PF topology's ~387kB
  // stays out of the main bundle), so every assertion about it has to await the
  // chunk — hence findBy*/waitFor throughout rather than a synchronous getBy.

  it('offers Execution Flow as a TOP-LEVEL tab, with only Tree and Flat left in the ?legs bar', async () => {
    // WAS `offers Execution Flow as a third ?legs tab … and NOT as a top-level tab` —
    // the exact inverse, and deliberately kept as a case rather than deleted, because
    // "which bar owns which tab" is the whole content of the restructure. Asserting
    // BOTH bars' full contents at once is what makes the split unambiguous: three
    // presentations moved up, the two renderings of one row set stayed down.
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
    // The top-level row carries all FIVE readings, the diagram / graph / Lineage among
    // them; the `?legs` row is down to the two renderings of the interactions row set.
    expect(bars).toContainEqual([
      'Span tree',
      'Interaction flow',
      'Interaction diagram',
      'Execution Flow',
      'Lineage',
    ]);
    expect(bars).toContainEqual(['Tree', 'Flat']);
    expect(bars).toHaveLength(2);

    // On the flow view the sub-tab default Tree is active, and the top-level graph tab
    // is not — the two bars track independent choices.
    expect(legTree).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tab', { name: /Execution Flow/i })).toHaveAttribute(
      'aria-selected',
      'false',
    );
  });

  it('renders the graph and NOT the interactions table for a /graph deep link', async () => {
    // Deep-linkable / reload-safe: the URL alone is enough to land on the graph, the
    // property this view has kept across all three of its addresses.
    mockFetchWithGraph();
    renderWithProviders(harness(), { route: '/traces/T1/graph' });

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
    // …AND NEITHER IS THE ENTITIES TABLE, which inverts what this case used to assert.
    // It read the table's survival as "visible evidence the promoted views still render
    // through one `FlowTables`", but the table is now scoped to the two TABLE
    // presentations (Tree|Flat) — on a picture view the graph's nodes ARE the entity
    // presentation, so the table restated them.
    expect(screen.queryByLabelText('Entities')).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Entities' })).not.toBeInTheDocument();
    // NOR IS THERE AN `Interactions` HEADING any more. This case used to read that title
    // as its evidence that the promoted view still renders through one `FlowTables`; the
    // heading was removed from both graph views (a bare "Interactions" over a canvas of
    // entity NODES and leg EDGES named only half of what is drawn). The one-`FlowTables`
    // fact is now asserted through the graph itself, which is the thing that actually
    // proves it — the surface below is mounted by that component.
    expect(screen.queryByRole('heading', { name: 'Interactions' })).not.toBeInTheDocument();
    expect(screen.getByTestId('execution-flow-graph')).toBeInTheDocument();
  });

  it('still shows the graph empty state on /graph when nothing is derived', async () => {
    // Carried across every address this view has had, whose mock returns no entities:
    // an empty derivation must say so rather than render a blank box, and that
    // wiring has to survive both the move behind Suspense and the promotion.
    mockFetchWithFlow();
    renderWithProviders(harness(), { route: '/traces/T1/graph' });

    expect(
      await screen.findByText(/No entities or interactions for this trace yet/i),
    ).toBeInTheDocument();
  });

  it('navigates to /graph when the Execution Flow tab is clicked', async () => {
    // WAS `writes ?legs=graph`. The tab click is a real navigation now, so what is
    // asserted is the path segment; `?legs` must be gone rather than trailing along as
    // a dead param that the legacy redirect would then act on.
    mockFetchWithGraph();
    renderWithProviders(harness(), { route: '/traces/T1/flow' });

    const graphTab = await screen.findByRole('tab', { name: /Execution Flow/i });
    await userEvent.click(graphTab);
    await waitFor(() =>
      expect(screen.getByTestId('location')).toHaveTextContent('/traces/T1/graph'),
    );
    expect(graphTab).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByTestId('location')).not.toHaveTextContent('legs');
    expect(await findGraph()).toBeInTheDocument();
  });

  it('leaves the graph for the tables when Interaction flow is clicked, dropping no query it needs', async () => {
    // WAS `drops ?legs when switching from the graph back to Tree` — a sub-tab click
    // that had to avoid writing `?legs=tree`. The equivalent move is now a TOP-LEVEL
    // click from /graph to /flow, and the property worth pinning shifted with it: the
    // flow view must come up on its own default (Tree, no `?legs` written), so the
    // canonical-URL rule survives the promotion instead of the graph leaving a
    // `?legs=graph` behind for the flow view to misread.
    mockFetchWithGraph();
    renderWithProviders(harness(), { route: '/traces/T1/graph' });

    await findGraph();
    await userEvent.click(screen.getByRole('tab', { name: /Interaction flow/i }));
    await waitFor(() =>
      expect(screen.getByTestId('location')).toHaveTextContent('/traces/T1/flow'),
    );
    expect(screen.getByTestId('location')).not.toHaveTextContent('legs');
    // The interactions table is back on its default presentation and the graph is gone.
    expect(await screen.findByLabelText('Interactions')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Tree' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.queryByTestId('execution-flow-graph')).not.toBeInTheDocument();
  });

  it('coerces an unrecognised ?legs value to Tree', async () => {
    // The coercion `?legs` has always had, now that the param's legal set is back down
    // to `tree|flat`: `parseLegViewKey` accepts only `flat`, so a typo must resolve to
    // the default and NOT be mistaken for one of the three promoted values (which would
    // send it through the legacy redirect to a view the reader never asked for).
    mockFetchWithGraph();
    renderWithProviders(harness(), { route: '/traces/T1/flow?legs=grapf' });

    const tree = await screen.findByRole('tab', { name: 'Tree' });
    expect(tree).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByLabelText('Interactions')).toBeInTheDocument();
    expect(screen.queryByTestId('execution-flow-graph')).not.toBeInTheDocument();
    // Still the flow view: a junk param is coerced, not redirected.
    expect(screen.getByTestId('location')).toHaveTextContent('/traces/T1/flow');
  });

  it('keeps the /graph view while an entity is selected (segment + ?eid coexist)', async () => {
    // WAS `keeps ?legs=graph across a row selection`, and its mechanism changed twice:
    // first `?legs=graph` → the `/graph` segment, and now the entity selection no longer
    // starts with a click on the Entities table, because that table is scoped to the two
    // TABLE presentations and is not on this picture view. So `?eid` is supplied by the
    // URL — a deep link, which is the case this coexistence guarantee exists to serve
    // anyway — and what is verified is unchanged: SEGMENT AND PARAM COEXIST, the page
    // renders the graph for one while honouring the other, and neither is dropped in
    // favour of the other on the round trip through `TraceDetailPage`'s URL mirroring.
    //
    // (A node click is the remaining in-view route to a selection and goes through the
    // same `selectEntity`; a node's `<g>` renders empty in jsdom, so that wiring is
    // asserted at its own seam in ExecutionFlowGraph.test.tsx rather than here.)
    mockFetchWithGraph();
    renderWithProviders(harness(), { route: '/traces/T1/graph?eid=e1' });

    await findGraph();
    // The param really was honoured: the entity's own detail panel is open, so this is a
    // live selection and not just a string sitting in the address bar.
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Entity' })).toBeInTheDocument());
    // …and both halves of the URL survived. `?eid` is not rewritten away by the page's
    // mirror, and the segment is not lost to the selection.
    const location = screen.getByTestId('location');
    expect(location).toHaveTextContent('eid=e1');
    expect(location).toHaveTextContent('/traces/T1/graph');
    // And the graph is still the active presentation, not swapped out by the selection.
    expect(screen.getByTestId('execution-flow-graph')).toBeInTheDocument();
  });

  it('keeps the graph inside the detail gutter so the panel never overlaps it', async () => {
    // The reason the graph is rendered INSIDE FlowTables' `dg-detail-gutter` div
    // rather than beside it: the detail panel floats fixed over the right of the
    // content, and the gutter is what makes the content shrink out from under it.
    // A graph outside that div would be covered by the panel on every selection.
    //
    // TWO MOUNTS RATHER THAN A CLICK: the two halves of the claim are "no selection → no
    // gutter" and "selection → gutter", and the Entities table this used to click for the
    // second half is not on a picture view any more. Each half is therefore its own
    // route — the second seeded with `?eid`, which is how a deep link or a reload arrives
    // at a selected entity. The layout rule asserted does not depend on how the selection
    // was made.
    mockFetchWithGraph();
    const { unmount } = renderWithProviders(harness(), { route: '/traces/T1/graph' });

    const graph = await findGraph();
    // No selection yet → no gutter anywhere (the content reclaims full width).
    expect(graph.closest('.dg-detail-gutter')).toBeNull();
    unmount();

    // With an entity selected, the panel floats…
    mockFetchWithGraph();
    renderWithProviders(harness(), { route: '/traces/T1/graph?eid=e1' });
    await findGraph();
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Entity' })).toBeInTheDocument());
    // …and the graph is a DESCENDANT of the gutter, so it shrinks rather than being
    // overlapped.
    expect(screen.getByTestId('execution-flow-graph').closest('.dg-detail-gutter')).not.toBeNull();
  });

  // --- `/lineage`: the same graph with the selected entity's data sources
  // highlighted. The URL-level contract only — this file owns the view segment and
  // `?src`, and the highlight's own logic lives in lib/lineageReachability.test.ts.

  /** The Lineage tab's own surface, past the SAME lazy chunk the graph rides. */
  async function findLineageGraph() {
    return screen.findByTestId('lineage-graph', undefined, { timeout: GRAPH_CHUNK_TIMEOUT });
  }

  it('navigates to /lineage when the Lineage tab is clicked, and swaps the table', async () => {
    // WAS `writes ?legs=lineage`. Same intent — the choice of presentation must be
    // URL-visible — against the path segment it now lives in.
    mockFetchWithGraph();
    renderWithProviders(harness(), { route: '/traces/T1/flow' });

    const tab = await screen.findByRole('tab', { name: 'Lineage' });
    await userEvent.click(tab);
    await waitFor(() =>
      expect(screen.getByTestId('location')).toHaveTextContent('/traces/T1/lineage'),
    );
    expect(tab).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByTestId('location')).not.toHaveTextContent('legs');
    expect(await findLineageGraph()).toBeInTheDocument();
    expect(screen.queryByLabelText('Interactions')).not.toBeInTheDocument();
    // THE ENTITIES TABLE IS GONE FROM THIS VIEW TOO, inverting what this case used to
    // assert. It called the table "load-bearing rather than merely kept: the only way to
    // select the entity this tab answers about" — true while the graph's nodes were drag
    // surfaces only, and no longer true now `withSelection` routes a node click into the
    // same `selectEntity`. The table is scoped to Tree|Flat, so its absence here is the
    // contract and is pinned rather than dropped.
    //
    // The point the old comment was really making — that the promotion could not have
    // given this view its own component, because the entity selection it depends on is
    // `FlowTables`' state — still holds, and is now shown by `?eid` reaching this view at
    // all (see the coexistence case below).
    expect(screen.queryByLabelText('Entities')).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Entities' })).not.toBeInTheDocument();
    // NOR THE `Interactions` HEADING, removed from both graph views: over a canvas of
    // entity nodes and leg edges it named only half of what is drawn. This case used to
    // read that title as its positive evidence that the shared `FlowTables` mounted; the
    // Lineage graph awaited above is that evidence, and a better one — it is the thing
    // `FlowTables` renders in the interactions table's place.
    expect(screen.queryByRole('heading', { name: 'Interactions' })).not.toBeInTheDocument();
  });

  it('restores the Lineage tab from a /lineage deep link on load', async () => {
    mockFetchWithGraph();
    renderWithProviders(harness(), { route: '/traces/T1/lineage' });

    expect(await findLineageGraph()).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Lineage' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    // The Execution Flow tab is NOT also active: they are two tabs over one renderer,
    // not one tab with two names. Now that both are TOP-LEVEL tabs driven by the same
    // `activeKey`, this is also the guard that the segment→ViewKey mapping is
    // one-to-one rather than two segments collapsing onto one key.
    expect(screen.getByRole('tab', { name: /Execution Flow/i })).toHaveAttribute(
      'aria-selected',
      'false',
    );
    expect(screen.queryByTestId('execution-flow-graph')).not.toBeInTheDocument();
  });

  it('leaves Lineage for the tables when Interaction flow is clicked, on the default presentation', async () => {
    // WAS `drops ?legs when switching from Lineage back to Tree`. Same intent for the
    // equivalent move (this view → the tables) now that it is a top-level hop: the flow
    // view must arrive on its own default with no `?legs` written, keeping the URL
    // canonical rather than inheriting a stale param from the view being left.
    mockFetchWithGraph();
    renderWithProviders(harness(), { route: '/traces/T1/lineage' });

    await findLineageGraph();
    await userEvent.click(screen.getByRole('tab', { name: /Interaction flow/i }));
    await waitFor(() =>
      expect(screen.getByTestId('location')).toHaveTextContent('/traces/T1/flow'),
    );
    expect(screen.getByTestId('location')).not.toHaveTextContent('legs');
    expect(await screen.findByLabelText('Interactions')).toBeInTheDocument();
    expect(screen.queryByTestId('lineage-graph')).not.toBeInTheDocument();
  });

  it('canonicalises a near-miss of the /lineage segment to /spans', async () => {
    // WAS `coerces an unrecognised near-miss of ?legs=lineage to Tree`, and the intent
    // survives the mechanism change: promoting `lineage` to a segment must not make the
    // RESOURCE name (`data-lineage`) a valid address for it, which is an easy thing to
    // get wrong given the two sit next to each other in this feature. As a segment the
    // remedy is the canonicalising redirect rather than a silent coercion to Tree, since
    // an unknown segment has no view to fall back to in place.
    mockFetchWithGraph();
    renderWithProviders(harness(), { route: '/traces/T1/data-lineage' });

    await waitFor(() =>
      expect(screen.getByTestId('location')).toHaveTextContent('/traces/T1/spans'),
    );
    expect(screen.getByRole('tab', { name: /Span tree/i })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    expect(screen.queryByTestId('lineage-graph')).not.toBeInTheDocument();
  });

  it('keeps the /lineage view with the entity selection that DRIVES it', async () => {
    // WAS `keeps ?legs=lineage across the entity selection that DRIVES it`, and both
    // halves of its mechanism have since moved: `?legs=lineage` became the `/lineage`
    // segment, and the entity selection no longer begins with a click on the Entities
    // table — that table is scoped to the two TABLE presentations and is not on this
    // picture view.
    //
    // THE INTENT IS UNCHANGED and is if anything more directly stated by supplying `?eid`
    // in the URL: this is the coexistence case that matters most for this view, because
    // selecting an entity is not an aside here, it is how the view is used at all. The
    // segment and the param have to hold at once — a page that dropped either would
    // either navigate away from the answer or answer about nobody.
    mockFetchWithGraph();
    renderWithProviders(harness(), { route: '/traces/T1/lineage?eid=e1' });

    await findLineageGraph();
    // The param was honoured as a real selection, not merely carried: the entity's detail
    // panel is open, which is `FlowTables`' one selection state having been seeded.
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Entity' })).toBeInTheDocument());
    const location = screen.getByTestId('location');
    expect(location).toHaveTextContent('eid=e1');
    expect(location).toHaveTextContent('/traces/T1/lineage');
    // Segment + param in one URL means the whole question is deep-linkable: "this trace,
    // the lineage view, this entity".
    expect(screen.getByTestId('lineage-graph')).toBeInTheDocument();
  });

  it('restores the traced data source from ?src, and mirrors a change back into it', async () => {
    // `?src` is the Lineage view's traced DATA SOURCE — the second required half of
    // `fanin(entity, source)`. In the URL beside `?eid` for the same reason it is: a
    // reload or a shared link has to restore the whole reading of the trace, and on a
    // governance surface "here is what I was looking at" must be a link rather than a
    // sequence of clicks to reproduce.
    //
    // `?src` is UNAFFECTED by the promotion — it was always a query param and still is;
    // only its companion `?legs=lineage` became a segment. That is why the tail
    // assertion here changed from "the tab param survives the write" to "the segment
    // survives the write": same guarantee, different half of the URL doing the carrying.
    mockFetchWithGraph();
    renderWithProviders(harness(), {
      route: '/traces/T1/lineage?src=agent%3A(p%2Ca)',
    });

    await findLineageGraph();
    // RESTORED: the control shows the source from the URL, without the reader touching
    // it. A chosen source that were not visible would be a highlight with no subject.
    expect(await screen.findByRole('button', { name: /Tracing data source/i })).toHaveTextContent(
      'agent-a',
    );

    // …and a change goes back OUT to the URL, so the two never disagree. `replace`, like
    // `?legs`, so trying several sources does not fill the Back button.
    fireEvent.click(screen.getByRole('button', { name: /Tracing data source/i }));
    fireEvent.click(await screen.findByRole('option', { name: /agent-a/ }));
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('src=agent'));
    // The view survives the write — otherwise choosing a source would navigate away from
    // the view that needs it.
    expect(screen.getByTestId('location')).toHaveTextContent('/traces/T1/lineage');
  });

  it('treats an unknown ?src the way it treats an unknown ?legs — coerced, disclosed, never thrown', async () => {
    // The stale-value contract, matching `parseLegViewKey`'s: a `?src` this trace's
    // roll-up does not contain must not throw and must not be silently blanked. It is
    // named on screen instead, so a reader who followed a link from another trace learns
    // why it did not restore rather than seeing an unexplained bare prompt.
    mockFetchWithGraph();
    renderWithProviders(harness(), {
      route: '/traces/T1/lineage?src=svc%3A(elsewhere%2Cgone)',
    });

    await findLineageGraph();
    await waitFor(() =>
      expect(screen.getByText(/not one of this trace’s sources/i)).toBeInTheDocument(),
    );
    // The page did not rewrite the URL behind the reader's back: the value they asked
    // for is still there to be corrected or shared, exactly as a bad `?legs` is left in
    // place and merely read as the default.
    expect(screen.getByTestId('location')).toHaveTextContent('src=svc');
    // The view still renders — a bad param is a notice, not a crash.
    expect(screen.getByTestId('lineage-graph')).toBeInTheDocument();
  });

  it('shows the graph empty state on /lineage when nothing is derived', async () => {
    // The empty-derivation wiring is SHARED with the Execution Flow view (one
    // `graphReadState`), and this is the guard that it really is shared: an empty
    // trace must say so on this view too rather than render a blank box with a
    // "select an entity" instruction over nothing. Still worth its own case after the
    // promotion for exactly that reason — the sharing is a fact about `FlowTables`, and
    // the promotion moved only which of its presentations the URL selects.
    mockFetchWithFlow();
    renderWithProviders(harness(), { route: '/traces/T1/lineage' });

    expect(
      await screen.findByText(/No entities or interactions for this trace yet/i),
    ).toBeInTheDocument();
    expect(screen.queryByTestId('lineage-graph')).not.toBeInTheDocument();
  });

  // --- LEGACY `?legs=diagram|graph|lineage` DEEP LINKS.
  //
  // These three were URL-visible `?legs` values for their whole middle life, so
  // bookmarks and shared links exist that name them. This codebase's own rule is that
  // "here is what I was looking at" has to be a link, so the promotion redirects them to
  // their new segment rather than letting `parseLegViewKey` coerce them to Tree — which
  // would silently land an old link on a table when it asked for a picture.
  //
  // NEW COVERAGE: the redirect did not exist before the promotion, so nothing below is a
  // rewrite of an older case.

  it('redirects a legacy ?legs=graph deep link to the /graph segment', async () => {
    // The migration path, stated for the value most likely to be bookmarked (the graph
    // was a top-level segment before it was a `?legs` value, so it has had public URLs
    // the longest). `replace`, so the dead URL is not left in history for Back to walk
    // into and bounce off again.
    mockFetchWithGraph();
    renderWithProviders(harness(), { route: '/traces/T1/flow?legs=graph' });

    await waitFor(() =>
      expect(screen.getByTestId('location')).toHaveTextContent('/traces/T1/graph'),
    );
    // Landed on the real view, not merely at the right URL.
    expect(await findGraph()).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /Execution Flow/i })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    // `legs` itself is dropped: carrying it onto a promoted view would leave a dead
    // param that the redirect would then bounce off on every render.
    expect(screen.getByTestId('location')).not.toHaveTextContent('legs');
  });

  it('carries ?src and ?iid across the legacy ?legs=lineage redirect, dropping only legs', async () => {
    // THE POINT OF THE REDIRECT, not an aside. A shared Lineage link names a source and
    // usually a selected entity/interaction; a redirect that kept only the view would
    // answer a DIFFERENT question than the link asked — the reader would arrive at a bare
    // "choose a source" prompt having been sent an answer. So every param except `legs`
    // is carried, and `?src` is the one that proves it (the picker shows the named source
    // without the reader touching it).
    mockFetchWithGraph();
    renderWithProviders(harness(), {
      route: '/traces/T1/flow?legs=lineage&src=agent%3A(p%2Ca)&iid=i1',
    });

    await waitFor(() =>
      expect(screen.getByTestId('location')).toHaveTextContent('/traces/T1/lineage'),
    );
    expect(await findLineageGraph()).toBeInTheDocument();
    const location = screen.getByTestId('location');
    expect(location).toHaveTextContent('src=agent');
    expect(location).toHaveTextContent('iid=i1');
    expect(location).not.toHaveTextContent('legs');
    // Carried as MEANING, not just as text: the source picker restored from the value
    // that survived the redirect.
    expect(await screen.findByRole('button', { name: /Tracing data source/i })).toHaveTextContent(
      'agent-a',
    );
  });

  it('redirects a legacy ?legs=diagram deep link to the /diagram segment', async () => {
    mockFetchWithGraph();
    renderWithProviders(harness(), { route: '/traces/T1/flow?legs=diagram' });

    await waitFor(() =>
      expect(screen.getByTestId('location')).toHaveTextContent('/traces/T1/diagram'),
    );
    expect(await screen.findByTestId('interaction-diagram')).toBeInTheDocument();
    expect(screen.getByTestId('location')).not.toHaveTextContent('legs');
  });

  it('does NOT redirect the surviving ?legs values', async () => {
    // The other side of the redirect, and the reason LEGACY_LEGS_TO_VIEW lists only
    // three keys: `tree` and `flat` are still LIVE values of the flow view's own sub-tab
    // bar. A redirect table that matched them — or a blanket "any ?legs on /flow is
    // legacy" rule — would make the Flat sub-tab unreachable.
    mockFetchWithFlow();
    renderWithProviders(harness(), { route: '/traces/T1/flow?legs=flat' });

    expect(await screen.findByLabelText('Interactions (flat)')).toBeInTheDocument();
    const location = screen.getByTestId('location');
    expect(location).toHaveTextContent('/traces/T1/flow');
    expect(location).toHaveTextContent('legs=flat');
  });

  it('preserves ?src when switching between two promoted views', async () => {
    // NEW COVERAGE, and a real regression risk the promotion created rather than a
    // restatement of anything. While these three were `?legs` sub-tabs, moving between
    // them was a query WRITE that preserved the rest of the query by construction — you
    // could not lose `?src` by switching. Now they are separate routes, so preserving it
    // is something `goToView` has to do ON PURPOSE: without that, Execution Flow →
    // Lineage would silently discard the chosen source and drop the reader from an
    // answer back onto a bare prompt.
    mockFetchWithGraph();
    renderWithProviders(harness(), { route: '/traces/T1/graph?src=agent%3A(p%2Ca)&eid=e1' });

    await findGraph();
    await userEvent.click(screen.getByRole('tab', { name: 'Lineage' }));
    await waitFor(() =>
      expect(screen.getByTestId('location')).toHaveTextContent('/traces/T1/lineage'),
    );
    const location = screen.getByTestId('location');
    expect(location).toHaveTextContent('src=agent');
    expect(location).toHaveTextContent('eid=e1');
    // Carried as MEANING: the Lineage view opens already tracing the source that was
    // chosen on the way in, so the switch continues the reading instead of restarting it.
    expect(await findLineageGraph()).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: /Tracing data source/i })).toHaveTextContent(
      'agent-a',
    );
  });

  it('drops the interaction query when leaving a promoted view for the span tree', async () => {
    // The other half of `goToView`'s rule, and the reason it is a rule rather than
    // "always preserve": the four interaction views are readings of ONE dataset and share
    // `?iid`/`?eid`/`?src`, but the span tree is a different dataset whose only param is
    // `?sel`. Carrying an entity id onto /spans would leave a param nothing there reads,
    // and carrying it BACK later would restore a selection the reader had abandoned.
    mockFetchWithGraph();
    renderWithProviders(harness(), { route: '/traces/T1/lineage?src=agent%3A(p%2Ca)&eid=e1' });

    await findLineageGraph();
    await userEvent.click(screen.getByRole('tab', { name: /Span tree/i }));
    await waitFor(() =>
      expect(screen.getByTestId('location')).toHaveTextContent('/traces/T1/spans'),
    );
    const location = screen.getByTestId('location');
    expect(location).not.toHaveTextContent('src=');
    expect(location).not.toHaveTextContent('eid=');
  });

  it('still redirects an unknown view segment to /spans', async () => {
    // Regression guard on the canonicalising guard itself, and now MORE load-bearing
    // than when the segment set was just /spans and /flow: five legal segments means
    // five near-misses (`graphh`, `diagramm`, `lineages`, …), and the promotion also put
    // a redirect (the legacy `?legs` one) AHEAD of this guard. Neither may swallow a
    // typo — an unrecognised segment resolves to nothing and canonicalises.
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
    // The cross-view round-trip, back to being three TOP-LEVEL tab clicks — which is
    // what it was before the graph became a `?legs` value, and what it is again. Each
    // hop is asserted at the path level, so this is the case that would catch a
    // `goToView` that navigated relative to the wrong base (e.g. appending rather than
    // replacing the segment, giving /traces/T1/flow/graph).
    mockFetchWithGraph();
    renderWithProviders(harness(), { route: '/traces/T1/spans' });

    await userEvent.click(await screen.findByRole('tab', { name: /Interaction flow/i }));
    await waitFor(() =>
      expect(screen.getByTestId('location')).toHaveTextContent('/traces/T1/flow'),
    );
    await userEvent.click(await screen.findByRole('tab', { name: /Execution Flow/i }));
    await waitFor(() =>
      expect(screen.getByTestId('location')).toHaveTextContent('/traces/T1/graph'),
    );
    expect(await findGraph()).toBeInTheDocument();
    // Back out to the span tree: leaving the interaction views unmounts the graph (it
    // holds no state worth keeping — its model re-derives from the cached reads).
    await userEvent.click(screen.getByRole('tab', { name: /Span tree/i }));
    await waitFor(() =>
      expect(screen.getByTestId('location')).toHaveTextContent('/traces/T1/spans'),
    );
    expect(screen.queryByTestId('execution-flow-graph')).not.toBeInTheDocument();
  });

  it('restores the Execution Flow view on a Back navigation', async () => {
    // The URL is the source of truth, so history navigation must restore the view
    // with no in-component state involved. `BackButton` calls the router's own
    // navigate(-1) — the MemoryRouter these tests use keeps its history in memory
    // rather than on window.history, so a raw window.history.back() would not
    // reach it.
    //
    // WAS `…within the flow view`, where the graph was a `?legs` value written with
    // `replace: true` so Back skipped the leg tabs entirely and walked only the
    // top-level entry. Now the graph IS a top-level entry, so Back walks the segment
    // itself — a stronger version of the same guarantee, and the one the original
    // /graph-segment test asserted before the nesting detour.
    mockFetchWithGraph();
    renderWithProviders(
      <>
        {harness()}
        <BackButton />
      </>,
      { route: '/traces/T1/graph' },
    );

    expect(await findGraph()).toBeInTheDocument();
    // Forward to the span tree (a top-level tab click pushes a history entry)…
    await userEvent.click(screen.getByRole('tab', { name: /Span tree/i }));
    await waitFor(() =>
      expect(screen.getByTestId('location')).toHaveTextContent('/traces/T1/spans'),
    );
    // …then Back must land on /graph with the Execution Flow tab active.
    await userEvent.click(screen.getByRole('button', { name: 'test-back' }));
    await waitFor(() =>
      expect(screen.getByTestId('location')).toHaveTextContent('/traces/T1/graph'),
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
