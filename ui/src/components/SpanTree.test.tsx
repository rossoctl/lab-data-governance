import { createRef, useState } from 'react';
import { act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithProviders } from '../test/renderWithProviders';
import { SpanTree, type SpanTreeHandle } from './SpanTree';
import { PinStore } from '../lib/pins';
import type { Span } from '../types';

const span = (over: Partial<Span>): Span => ({
  seq: 1, trace_id: 'T', span_id: 'x', parent_id: null, name: 'x',
  started_at: '2026-05-01T12:00:00Z', attributes: {}, observed_at: '2026-05-01T12:00:00Z',
  arrival_seq: 1, in_time_window: true, service_name: 'svc', kind: 'INTERNAL',
  error: null, status_message: null, events: null, links: null, ended_at: null,
  otlp: null, scope: null, resource_attributes: null, ...over,
});

const ROOT = span({ span_id: 'root', name: 'root-span', kind: 'SERVER' });

// children of root: one normal, one error leaf.
const CHILDREN = [
  span({ seq: 2, span_id: 'ok-child', name: 'ok-child', parent_id: 'root' }),
  span({ seq: 3, span_id: 'bad-child', name: 'bad-child', parent_id: 'root', error: true, status_message: 'boom' }),
];

function mockChildren(children: Span[]) {
  (fetch as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
    if (url.includes('/spans/root/children')) {
      return { ok: true, status: 200, json: async () => ({ spans: children }) };
    }
    return { ok: true, status: 200, json: async () => ({ spans: [] }) };
  });
}

describe('SpanTree', () => {
  beforeEach(() => vi.stubGlobal('fetch', vi.fn()));
  afterEach(() => vi.unstubAllGlobals());

  it('renders the root span name', () => {
    mockChildren([]);
    renderWithProviders(
      <SpanTree traceId="T" root={ROOT} pins={new PinStore()} onSelect={() => {}} onPinsChange={() => {}} />,
    );
    expect(screen.getByText('root-span')).toBeInTheDocument();
  });

  it('auto-expands the root on mount so the first level is visible without a click', async () => {
    mockChildren(CHILDREN);
    renderWithProviders(
      <SpanTree traceId="T" root={ROOT} pins={new PinStore()} onSelect={() => {}} onPinsChange={() => {}} />,
    );
    // No expand click — the root auto-expands (vanilla renderRoot parity).
    await waitFor(() => expect(screen.getByText('ok-child')).toBeInTheDocument());
    expect(screen.getByText('bad-child')).toBeInTheDocument();
  });

  it('lazy-loads a non-root node only when it is expanded', async () => {
    // Root auto-expands and loads its children; a child's OWN subtree stays
    // lazy — its grandchildren load only on that child's expand click.
    const grandchild = span({ seq: 4, span_id: 'gc', name: 'grand-child', parent_id: 'ok-child' });
    (fetch as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
      if (url.includes('/spans/root/children')) return { ok: true, status: 200, json: async () => ({ spans: CHILDREN }) };
      if (url.includes('/spans/ok-child/children')) return { ok: true, status: 200, json: async () => ({ spans: [grandchild] }) };
      return { ok: true, status: 200, json: async () => ({ spans: [] }) };
    });
    renderWithProviders(
      <SpanTree traceId="T" root={ROOT} pins={new PinStore()} onSelect={() => {}} onPinsChange={() => {}} />,
    );
    await waitFor(() => expect(screen.getByText('ok-child')).toBeInTheDocument());
    // The grandchild is NOT loaded until ok-child is expanded.
    expect(screen.queryByText('grand-child')).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: /expand ok-child/i }));
    await waitFor(() => expect(screen.getByText('grand-child')).toBeInTheDocument());
  });

  it('shows an error badge on an error span and a descendant badge on its loaded ancestor', async () => {
    mockChildren(CHILDREN);
    renderWithProviders(
      <SpanTree traceId="T" root={ROOT} pins={new PinStore()} onSelect={() => {}} onPinsChange={() => {}} />,
    );
    // Root auto-expands, so its children (incl. the error leaf) load on mount.
    await waitFor(() => expect(screen.getByText('bad-child')).toBeInTheDocument());
    // The error leaf carries the per-span Error badge.
    const badRow = screen.getByText('bad-child').closest('[data-testid="span-row"]')!;
    expect(within(badRow as HTMLElement).getByText('Error')).toBeInTheDocument();
    // root is a loaded ancestor of the error leaf → Child error badge.
    const rootRow = screen.getByText('root-span').closest('[data-testid="span-row"]')!;
    expect(within(rootRow as HTMLElement).getByText(/Child error/i)).toBeInTheDocument();
  });

  it('renders a "Load more children" affordance for a full page and pages forward', async () => {
    // First page returns exactly PAGE_SIZE (50) children → more may exist.
    const page1 = Array.from({ length: 50 }, (_, i) =>
      span({ seq: 100 + i, span_id: `c${i}`, name: `child-${i}`, parent_id: 'root' }),
    );
    const page2 = [span({ seq: 200, span_id: 'c50', name: 'child-50', parent_id: 'root' })];
    let call = 0;
    (fetch as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
      if (url.includes('/spans/root/children')) {
        call += 1;
        return { ok: true, status: 200, json: async () => ({ spans: call === 1 ? page1 : page2 }) };
      }
      return { ok: true, status: 200, json: async () => ({ spans: [] }) };
    });
    renderWithProviders(
      <SpanTree traceId="T" root={ROOT} pins={new PinStore()} onSelect={() => {}} onPinsChange={() => {}} />,
    );
    // Auto-expand loads the first (full) page of root's children on mount.
    await waitFor(() => expect(screen.getByText('child-0')).toBeInTheDocument());
    // A full first page → "Load more children" is offered.
    const more = await screen.findByRole('button', { name: /Load more children/i });
    await userEvent.click(more);
    // The next page's child appears, and (short page) the affordance disappears.
    await waitFor(() => expect(screen.getByText('child-50')).toBeInTheDocument());
    expect(screen.queryByRole('button', { name: /Load more children/i })).toBeNull();
  });

  it('does not infinitely recurse on a self-referential parent_id (cycle guard)', async () => {
    // root's child is root itself (self-loop) — render must terminate.
    const selfChild = span({ seq: 2, span_id: 'root', name: 'root-span', parent_id: 'root' });
    (fetch as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
      if (url.includes('/spans/root/children')) {
        return { ok: true, status: 200, json: async () => ({ spans: [selfChild] }) };
      }
      return { ok: true, status: 200, json: async () => ({ spans: [] }) };
    });
    renderWithProviders(
      <SpanTree traceId="T" root={ROOT} pins={new PinStore()} onSelect={() => {}} onPinsChange={() => {}} />,
    );
    // Root auto-expands and loads its self-referential child.
    // The cycle is rendered as a terminal "(cycle)" node, not an infinite tree.
    await waitFor(() => expect(screen.getByText(/\(cycle\)/)).toBeInTheDocument());
  });

  it('reveal() auto-expands ancestor chains so a deep span becomes visible', async () => {
    // root → mid → leaf. Children served per-parent; nothing expanded yet.
    const mid = span({ seq: 2, span_id: 'mid', name: 'mid-span', parent_id: 'root' });
    const leaf = span({ seq: 3, span_id: 'leaf', name: 'leaf-span', parent_id: 'mid' });
    (fetch as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
      if (url.includes('/spans/root/children')) return { ok: true, status: 200, json: async () => ({ spans: [mid] }) };
      if (url.includes('/spans/mid/children')) return { ok: true, status: 200, json: async () => ({ spans: [leaf] }) };
      // single-span fetches for ancestor-chain resolution
      if (url.endsWith('/spans/leaf')) return { ok: true, status: 200, json: async () => leaf };
      if (url.endsWith('/spans/mid')) return { ok: true, status: 200, json: async () => mid };
      if (url.endsWith('/spans/root')) return { ok: true, status: 200, json: async () => ROOT };
      return { ok: true, status: 200, json: async () => ({ spans: [] }) };
    });
    const ref = createRef<SpanTreeHandle>();
    renderWithProviders(
      <SpanTree ref={ref} traceId="T" root={ROOT} pins={new PinStore()} onSelect={() => {}} onPinsChange={() => {}} />,
    );
    // Root auto-expands to show mid; the leaf is still inside mid's collapsed
    // subtree (auto-expand is one level, not recursive).
    await waitFor(() => expect(screen.getByText('mid-span')).toBeInTheDocument());
    expect(screen.queryByText('leaf-span')).toBeNull();

    await act(async () => {
      await ref.current!.reveal(['leaf']);
    });

    // Ancestors auto-expanded — the leaf row is now rendered, no manual click.
    await waitFor(() => expect(screen.getByText('leaf-span')).toBeInTheDocument());
    expect(screen.getByText('mid-span')).toBeInTheDocument();
  });

  it('reveal() fetches ancestors that were never loaded to resolve the chain', async () => {
    // A caller reveals a grandchild (leaf) that was never loaded — auto-expand
    // only paged in root's direct children (mid), not the leaf — so reveal must
    // fetch leaf individually to learn its lineage, then expand down.
    const mid = span({ seq: 2, span_id: 'mid', name: 'mid-span', parent_id: 'root' });
    const leaf = span({ seq: 3, span_id: 'leaf', name: 'leaf-span', parent_id: 'mid' });
    const singleFetches: string[] = [];
    (fetch as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
      if (url.includes('/spans/root/children')) return { ok: true, status: 200, json: async () => ({ spans: [mid] }) };
      if (url.includes('/spans/mid/children')) return { ok: true, status: 200, json: async () => ({ spans: [leaf] }) };
      if (url.endsWith('/spans/leaf')) { singleFetches.push('leaf'); return { ok: true, status: 200, json: async () => leaf }; }
      if (url.endsWith('/spans/mid')) { singleFetches.push('mid'); return { ok: true, status: 200, json: async () => mid }; }
      if (url.endsWith('/spans/root')) return { ok: true, status: 200, json: async () => ROOT };
      return { ok: true, status: 200, json: async () => ({ spans: [] }) };
    });
    const ref = createRef<SpanTreeHandle>();
    renderWithProviders(
      <SpanTree ref={ref} traceId="T" root={ROOT} pins={new PinStore()} onSelect={() => {}} onPinsChange={() => {}} />,
    );
    await act(async () => {
      await ref.current!.reveal(['leaf']);
    });
    await waitFor(() => expect(screen.getByText('leaf-span')).toBeInTheDocument());
    // The chain was resolved by fetching the unloaded ancestor span(s).
    expect(singleFetches).toContain('leaf');
  });

  it('reveal() surfaces a lower-seq target child that forward-paging skipped', async () => {
    // root has been partially paged to a high-seq child already; the reveal
    // target is a LOWER-seq child the keyset (cursor=max seq) can't page back
    // to. reveal must still list it (splice fallback) so the row renders.
    const hi = span({ seq: 900, span_id: 'hi', name: 'hi-child', parent_id: 'root' });
    const target = span({ seq: 5, span_id: 'target', name: 'target-child', parent_id: 'root' });
    (fetch as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
      // The children endpoint (keyed by cursor) only ever returns the hi child
      // — forward paging never yields the low-seq target.
      if (url.includes('/spans/root/children')) return { ok: true, status: 200, json: async () => ({ spans: [hi] }) };
      if (url.endsWith('/spans/target')) return { ok: true, status: 200, json: async () => target };
      if (url.endsWith('/spans/root')) return { ok: true, status: 200, json: async () => ROOT };
      return { ok: true, status: 200, json: async () => ({ spans: [] }) };
    });
    const ref = createRef<SpanTreeHandle>();
    renderWithProviders(
      <SpanTree ref={ref} traceId="T" root={ROOT} pins={new PinStore()} onSelect={() => {}} onPinsChange={() => {}} />,
    );
    // Auto-expand pre-pages root once, loading the hi child (a partial page).
    await waitFor(() => expect(screen.getByText('hi-child')).toBeInTheDocument());

    await act(async () => {
      await ref.current!.reveal(['target']);
    });
    // The low-seq target is spliced in and renders despite forward-only paging.
    await waitFor(() => expect(screen.getByText('target-child')).toBeInTheDocument());
    expect(screen.getByText('hi-child')).toBeInTheDocument(); // pre-loaded sibling kept
  });

  it('reveal() resolves only after a scroll frame has run (spinner stays up until then)', async () => {
    // The parent brackets a "highlighting…" spinner around reveal(); the promise
    // must not resolve until reveal has waited a frame to scroll the revealed
    // row into view, or the spinner would clear before the row is on screen.
    const mid = span({ seq: 2, span_id: 'mid', name: 'mid-span', parent_id: 'root' });
    const leaf = span({ seq: 3, span_id: 'leaf', name: 'leaf-span', parent_id: 'mid' });
    (fetch as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
      if (url.includes('/spans/root/children')) return { ok: true, status: 200, json: async () => ({ spans: [mid] }) };
      if (url.includes('/spans/mid/children')) return { ok: true, status: 200, json: async () => ({ spans: [leaf] }) };
      if (url.endsWith('/spans/leaf')) return { ok: true, status: 200, json: async () => leaf };
      if (url.endsWith('/spans/mid')) return { ok: true, status: 200, json: async () => mid };
      if (url.endsWith('/spans/root')) return { ok: true, status: 200, json: async () => ROOT };
      return { ok: true, status: 200, json: async () => ({ spans: [] }) };
    });
    // Track that the reveal's scroll frame ran before its promise resolved.
    // (jsdom's scrollIntoView is a no-op stub and the revealed row may not be
    // committed when the frame fires, so we assert on the frame itself — the
    // real "wait a frame before resolving" guarantee — not on scrollIntoView.)
    const realRaf = window.requestAnimationFrame;
    let framesRun = 0;
    const rafSpy = vi
      .spyOn(window, 'requestAnimationFrame')
      .mockImplementation((cb) => realRaf(() => { framesRun += 1; cb(performance.now()); }));
    const ref = createRef<SpanTreeHandle>();
    renderWithProviders(
      <SpanTree ref={ref} traceId="T" root={ROOT} pins={new PinStore()} onSelect={() => {}} onPinsChange={() => {}} />,
    );
    await waitFor(() => expect(screen.getByText('mid-span')).toBeInTheDocument());
    const framesBefore = framesRun;

    await act(async () => {
      await ref.current!.reveal(['leaf']);
      // The promise settled — reveal awaited at least one frame before resolving.
      expect(framesRun).toBeGreaterThan(framesBefore);
    });
    rafSpy.mockRestore();
  });

  // --- Service filter: a client-side multi-select over the service_name
  // values present in loaded spans (?svc= on the page, `serviceFilter` here).

  // Root + one child per service — the two-service shape the filter narrows.
  const AB_ROOT = span({ span_id: 'root', name: 'root-span', kind: 'SERVER', service_name: 'authbridge' });
  const MIXED_KIDS = [
    span({ seq: 2, span_id: 'c-side', name: 'sidecar-child', parent_id: 'root', service_name: 'authbridge' }),
    span({ seq: 3, span_id: 'c-app', name: 'app-child', parent_id: 'root', service_name: 'weather-service' }),
  ];

  it('prunes deselected services\' rows (and their branches) from the tree', async () => {
    mockChildren(MIXED_KIDS);
    renderWithProviders(
      <SpanTree
        traceId="T" root={AB_ROOT} pins={new PinStore()} onSelect={() => {}} onPinsChange={() => {}}
        serviceFilter={['authbridge']}
      />,
    );
    await waitFor(() => expect(screen.getByText('sidecar-child')).toBeInTheDocument());
    // The deselected service's span row is gone; the selected one remains.
    expect(screen.queryByText('app-child')).toBeNull();
    expect(screen.getByText('root-span')).toBeInTheDocument();
  });

  it('offers a checkbox per loaded service and reports toggles (sorted list, null = all)', async () => {
    mockChildren(MIXED_KIDS);
    const onServiceFilterChange = vi.fn();
    // A host owning the filter, as TraceDetailPage does (mirroring ?svc=).
    function Host() {
      const [filter, setFilter] = useState<string[] | null>(null);
      onServiceFilterChange.mockImplementation(setFilter);
      return (
        <SpanTree
          traceId="T" root={AB_ROOT} pins={new PinStore()} onSelect={() => {}} onPinsChange={() => {}}
          serviceFilter={filter} onServiceFilterChange={onServiceFilterChange}
        />
      );
    }
    renderWithProviders(<Host />);
    await waitFor(() => expect(screen.getByText('app-child')).toBeInTheDocument());
    // Unchecking a service narrows the tree and reports the remaining selection.
    await userEvent.click(screen.getByRole('checkbox', { name: 'weather-service' }));
    expect(onServiceFilterChange).toHaveBeenCalledWith(['authbridge']);
    await waitFor(() => expect(screen.queryByText('app-child')).toBeNull());
    expect(screen.getByText('sidecar-child')).toBeInTheDocument();
    // Re-checking the last box collapses back to null (all → param-less URL).
    await userEvent.click(screen.getByRole('checkbox', { name: 'weather-service' }));
    expect(onServiceFilterChange).toHaveBeenLastCalledWith(null);
    await waitFor(() => expect(screen.getByText('app-child')).toBeInTheDocument());
  });

  it('renders no Services control while only one service is loaded', async () => {
    mockChildren(CHILDREN); // root + children all carry service 'svc'
    renderWithProviders(
      <SpanTree traceId="T" root={ROOT} pins={new PinStore()} onSelect={() => {}} onPinsChange={() => {}} />,
    );
    await waitFor(() => expect(screen.getByText('ok-child')).toBeInTheDocument());
    expect(screen.queryByText('Services:')).toBeNull();
    expect(screen.queryByRole('checkbox')).toBeNull();
  });

  it('calls onSelect with the span when a row is clicked', async () => {
    mockChildren([]);
    const onSelect = vi.fn();
    renderWithProviders(
      <SpanTree traceId="T" root={ROOT} pins={new PinStore()} onSelect={onSelect} onPinsChange={() => {}} />,
    );
    await userEvent.click(screen.getByText('root-span'));
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ span_id: 'root' }));
  });
});
