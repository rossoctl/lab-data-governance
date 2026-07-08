import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithProviders } from '../test/renderWithProviders';
import { SpanTree } from './SpanTree';
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
