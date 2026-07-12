import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { screen, waitFor, waitForElementToBeRemoved } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Routes, Route } from 'react-router-dom';
import { renderWithProviders } from '../test/renderWithProviders';
import { LocationProbe } from '../test/LocationProbe';
import { TraceDetailPage } from './TraceDetailPage';

// The trace-detail view hosts a two-way switcher: Span tree | Interaction flow.
// The active view is a URL path segment (/traces/{id}/spans | /flow) — the URL
// is the source of truth, so reload/bookmark/back restore the tab. The trace id
// and view both come from the route (:traceId/:view).

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
          started_at: '2026-05-01T12:00:00Z', ended_at: '2026-05-01T12:00:01Z',
          error: false, request_payload_hash: null, response_payload_hash: null,
          summary: 'the interaction', parent_interaction_id: null,
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
            started_at: '2026-05-01T12:00:00Z', ended_at: '2026-05-01T12:00:01Z',
            error: false, request_payload_hash: null, response_payload_hash: null,
            summary: 'the interaction', parent_interaction_id: null,
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
      screen.getAllByText(name).some((el) => el.closest('[data-testid="span-row"]') !== null);

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
