import { createRef } from 'react';
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

  it('lazy-loads children when the root is expanded', async () => {
    mockChildren(CHILDREN);
    renderWithProviders(
      <SpanTree traceId="T" root={ROOT} pins={new PinStore()} onSelect={() => {}} onPinsChange={() => {}} />,
    );
    await userEvent.click(screen.getByRole('button', { name: /expand root-span/i }));
    await waitFor(() => expect(screen.getByText('ok-child')).toBeInTheDocument());
    expect(screen.getByText('bad-child')).toBeInTheDocument();
  });

  it('shows an error badge on an error span and a descendant badge on its loaded ancestor', async () => {
    mockChildren(CHILDREN);
    renderWithProviders(
      <SpanTree traceId="T" root={ROOT} pins={new PinStore()} onSelect={() => {}} onPinsChange={() => {}} />,
    );
    await userEvent.click(screen.getByRole('button', { name: /expand root-span/i }));
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
    await userEvent.click(screen.getByRole('button', { name: /expand root-span/i }));
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
    await userEvent.click(screen.getByRole('button', { name: /expand root-span/i }));
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
    // Only the root is visible initially; the leaf is inside a collapsed subtree.
    expect(screen.getByText('root-span')).toBeInTheDocument();
    expect(screen.queryByText('leaf-span')).toBeNull();

    await act(async () => {
      await ref.current!.reveal(['leaf']);
    });

    // Ancestors auto-expanded — the leaf row is now rendered, no manual click.
    await waitFor(() => expect(screen.getByText('leaf-span')).toBeInTheDocument());
    expect(screen.getByText('mid-span')).toBeInTheDocument();
  });

  it('reveal() fetches ancestors that were never loaded to resolve the chain', async () => {
    // The tree only knows the root; a caller reveals a grandchild whose parent
    // was never loaded, so reveal must fetch mid + leaf individually to learn
    // the lineage, then expand down.
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
    // Pre-page root once so it has the hi child loaded (partial page).
    await userEvent.click(screen.getByRole('button', { name: /expand root-span/i }));
    await waitFor(() => expect(screen.getByText('hi-child')).toBeInTheDocument());

    await act(async () => {
      await ref.current!.reveal(['target']);
    });
    // The low-seq target is spliced in and renders despite forward-only paging.
    await waitFor(() => expect(screen.getByText('target-child')).toBeInTheDocument());
    expect(screen.getByText('hi-child')).toBeInTheDocument(); // pre-loaded sibling kept
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
