import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { SpanDetailPanel } from './SpanDetailPanel';
import type { Span } from '../types';

const span = (over: Partial<Span>): Span => ({
  seq: 7, trace_id: 'T', span_id: 's1', parent_id: null, name: 'the-span',
  started_at: '2026-05-01T12:00:00.000Z', attributes: { req_id: 'xyz' },
  observed_at: '2026-05-01T12:00:00Z', arrival_seq: 7, in_time_window: true,
  service_name: 'svc', kind: 'SERVER', error: null, status_message: null,
  events: null, links: null, ended_at: null, otlp: null, scope: null,
  resource_attributes: null, ...over,
});

describe('SpanDetailPanel', () => {
  it('renders the nine section headings, with Identity renamed to Span', () => {
    render(<SpanDetailPanel span={span({})} onRefresh={() => {}} />);
    for (const h of ['Span', 'Timing', 'Status', 'Resource', 'Scope', 'OTLP envelope', 'Attributes', 'Events', 'Links']) {
      expect(screen.getByText(h)).toBeInTheDocument();
    }
    // The old section name is gone.
    expect(screen.queryByText('Identity')).not.toBeInTheDocument();
  });

  it('titles the panel "Details" (not "Span detail")', () => {
    render(<SpanDetailPanel span={span({})} onRefresh={() => {}} />);
    expect(screen.getByRole('heading', { name: 'Details' })).toBeInTheDocument();
    expect(screen.queryByText('Span detail')).not.toBeInTheDocument();
  });

  it('renders a placeholder and a DISABLED Refresh when no span is selected', () => {
    render(<SpanDetailPanel span={null} onRefresh={() => {}} />);
    // Header survives, but Refresh is disabled (nothing to re-fetch → no dead
    // affordance).
    expect(screen.getByRole('heading', { name: 'Details' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Refresh/i })).toBeDisabled();
    // A hint stands in for the section body; no span fields.
    expect(screen.getByText(/Select a span/i)).toBeInTheDocument();
    expect(screen.queryByText('Timing')).not.toBeInTheDocument();
  });

  it('shows the tri-state status: Error/OK/Unset', () => {
    const { rerender } = render(
      <SpanDetailPanel span={span({ error: true, status_message: 'boom' })} onRefresh={() => {}} />,
    );
    expect(screen.getByText(/Error: boom/)).toBeInTheDocument();
    rerender(<SpanDetailPanel span={span({ error: false })} onRefresh={() => {}} />);
    expect(screen.getByText('OK')).toBeInTheDocument();
    rerender(<SpanDetailPanel span={span({ error: null })} onRefresh={() => {}} />);
    expect(screen.getByText('Unset')).toBeInTheDocument();
  });

  it('falls back to (not finalized) for a null ended_at and computes duration otherwise', () => {
    const { rerender } = render(<SpanDetailPanel span={span({ ended_at: null })} onRefresh={() => {}} />);
    expect(screen.getByText('(not finalized)')).toBeInTheDocument();
    rerender(
      <SpanDetailPanel
        span={span({ started_at: '2026-05-01T12:00:00.000Z', ended_at: '2026-05-01T12:00:05.000Z' })}
        onRefresh={() => {}}
      />,
    );
    expect(screen.getByText(/5000\.000 ms/)).toBeInTheDocument();
  });

  it('fires onRefresh when the Refresh button is clicked', async () => {
    const onRefresh = vi.fn();
    render(<SpanDetailPanel span={span({})} onRefresh={onRefresh} />);
    await userEvent.click(screen.getByRole('button', { name: /Refresh/i }));
    expect(onRefresh).toHaveBeenCalledOnce();
  });
});
