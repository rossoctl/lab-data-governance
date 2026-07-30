import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { screen, waitFor, waitForElementToBeRemoved, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Routes, Route, useNavigate } from 'react-router-dom';
import { renderWithProviders } from '../test/renderWithProviders';
import { LocationProbe } from '../test/LocationProbe';
import { TraceDetailPage } from './TraceDetailPage';

// The trace-detail view hosts a three-way switcher: Span tree | Interaction flow
// | Execution Flow. The active view is a URL path segment
// (/traces/{id}/spans | /flow | /graph) — the URL is the source of truth, so
// reload/bookmark/back restore the tab. The trace id and view both come from the
// route (:traceId/:view).

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

describe('TraceDetailPage', () => {
  beforeEach(() => vi.stubGlobal('fetch', vi.fn()));
  afterEach(() => vi.unstubAllGlobals());

  it('shows the trace id and a two-way view switcher, Span tree active for /spans', async () => {
    mockFetch();
    renderWithProviders(harness(), { route: '/traces/T1/spans' });

    // Both switcher tabs are present, and no Graph tab remains.
    await waitFor(() => expect(screen.getByRole('tab', { name: /Span tree/i })).toBeInTheDocument());
    expect(screen.getByRole('tab', { name: /Interaction flow/i })).toBeInTheDocument();
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

  // --- The third top-level tab: Execution Flow at the `graph` URL segment. Same
  // contract as its two siblings — the URL path segment is the single source of
  // truth, so the tab is deep-linkable, survives reload, and works with Back.

  it('offers Execution Flow as a third tab alongside Span tree and Interaction flow', async () => {
    mockFetch();
    renderWithProviders(harness(), { route: '/traces/T1/spans' });

    await waitFor(() =>
      expect(screen.getByRole('tab', { name: /Execution Flow/i })).toBeInTheDocument(),
    );
    // All three, and the spans segment still drives which is active.
    expect(screen.getAllByRole('tab')).toHaveLength(3);
    expect(screen.getByRole('tab', { name: /Execution Flow/i })).toHaveAttribute(
      'aria-selected',
      'false',
    );
  });

  it('activates the Execution Flow tab and renders the graph for a /graph deep link', async () => {
    // Deep-linkable: the URL alone is enough to land on the graph.
    mockFetchWithFlow();
    renderWithProviders(harness(), { route: '/traces/T1/graph' });

    await waitFor(() =>
      expect(screen.getByRole('tab', { name: /Execution Flow/i })).toHaveAttribute(
        'aria-selected',
        'true',
      ),
    );
    // mockFetchWithFlow returns no entities, so there is nothing to graph at all
    // — the view must say so rather than render a blank box.
    await waitFor(() =>
      expect(screen.getByText(/No entities or interactions for this trace yet/i)).toBeInTheDocument(),
    );
  });

  it('writes /graph to the URL when the Execution Flow tab is clicked', async () => {
    mockFetch();
    renderWithProviders(harness(), { route: '/traces/T1/spans' });

    const graphTab = await screen.findByRole('tab', { name: /Execution Flow/i });
    await userEvent.click(graphTab);
    await waitFor(() => expect(graphTab).toHaveAttribute('aria-selected', 'true'));
    expect(screen.getByTestId('location')).toHaveTextContent('/traces/T1/graph');
    // With no derived data the graph shows its own empty state.
    await waitFor(() =>
      expect(screen.getByText(/No entities or interactions for this trace yet/i)).toBeInTheDocument(),
    );
  });

  it('still redirects an unknown view segment to /spans now that a third view exists', async () => {
    // Regression guard on the canonicalising guard: adding `graph` to
    // URL_TO_VIEW must not make a typo'd segment resolve to anything.
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

  it('round-trips Span tree → Execution Flow → Interaction flow through the URL', async () => {
    mockFetchWithFlow();
    renderWithProviders(harness(), { route: '/traces/T1/spans' });

    await userEvent.click(await screen.findByRole('tab', { name: /Execution Flow/i }));
    await waitFor(() =>
      expect(screen.getByTestId('location')).toHaveTextContent('/traces/T1/graph'),
    );
    await userEvent.click(screen.getByRole('tab', { name: /Interaction flow/i }));
    await waitFor(() =>
      expect(screen.getByTestId('location')).toHaveTextContent('/traces/T1/flow'),
    );
    // Leaving the graph unmounts it (it holds no state worth keeping).
    expect(screen.queryByTestId('execution-flow-graph')).not.toBeInTheDocument();
  });

  it('restores the Execution Flow tab on a Back navigation from a sibling tab', async () => {
    // The URL is the source of truth, so history navigation must restore the tab
    // with no in-component state involved. `BackButton` calls the router's own
    // navigate(-1) — the MemoryRouter these tests use keeps its history in memory
    // rather than on window.history, so a raw window.history.back() would not
    // reach it.
    mockFetchWithFlow();
    renderWithProviders(
      <>
        {harness()}
        <BackButton />
      </>,
      { route: '/traces/T1/graph' },
    );

    await waitFor(() =>
      expect(screen.getByRole('tab', { name: /Execution Flow/i })).toHaveAttribute(
        'aria-selected',
        'true',
      ),
    );
    // Forward to the flow tab (a tab click pushes a history entry)…
    await userEvent.click(screen.getByRole('tab', { name: /Interaction flow/i }));
    await waitFor(() =>
      expect(screen.getByTestId('location')).toHaveTextContent('/traces/T1/flow'),
    );
    // …then Back must land on /graph with the Execution Flow tab active again.
    await userEvent.click(screen.getByRole('button', { name: 'test-back' }));
    await waitFor(() =>
      expect(screen.getByTestId('location')).toHaveTextContent('/traces/T1/graph'),
    );
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
