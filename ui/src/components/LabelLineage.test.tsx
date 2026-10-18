import { describe, it, expect, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { LabelLineage } from './LabelLineage';
import { HopHoverContext } from './flow/hopHover';
import type { LegLabel, LineageHop } from '../types';

/**
 * The chains of a live booking trace's partner request (trace 98134844…),
 * as the labels read serves them: the PID born at create-booking's response
 * reached notify-partner's request twice, straight and through the LLM, and the
 * partner request from there.
 */
const T = '98134844c66d7a7996303b049d6fc350';

function hop(
  seq: number, role: LineageHop['role'], caller: string, callee: string, leg: 'request' | 'response',
  over: Partial<LineageHop> = {},
): LineageHop {
  return {
    interaction_id: `ix-${seq}`, leg_type: leg, seq, trace_id: T, caller, callee,
    text: 'BK-83CE5F', start: 16, end: 25, role, excerpt: null, ...over,
  };
}

const BORN = hop(80565, 'origin', 'booking-agent', 'create-booking', 'response', {
  start: 7, end: 16,
  excerpt: { text: '{"id": "BK-83CE5F", "guest_name": "<name>"}', start: 8, end: 17 },
});
const DIRECT = [BORN, hop(82049, 'hop', 'booking-agent', 'notify-partner', 'request'),
  hop(83053, 'here', 'notify-partner', 'api.travel-partner.example:8000', 'request')];
const VIA_LLM = [BORN, hop(80603, 'hop', 'booking-agent', 'qwen2.5:7b', 'request', { start: 2396, end: 2405 }),
  hop(82082, 'hop', 'booking-agent', 'qwen2.5:7b', 'response', { start: 7, end: 16 }),
  hop(82049, 'hop', 'booking-agent', 'notify-partner', 'request'),
  hop(83053, 'here', 'notify-partner', 'api.travel-partner.example:8000', 'request')];

const PID: LegLabel = {
  text: 'BK-83CE5F', start: 16, end: 25, entity_type: 'PID', sensitivity_level: 'RESTRICTED',
  regulatory_tags: ['PII'], found_here: false,
  carried_in: [{
    origin_interaction_id: 'ix-80565', origin_leg_type: 'response', origin_seq: 80565,
    caller: 'booking-agent', callee: 'create-booking', unit_text: 'BK-83CE5F', unit_value: 'BK-83CE5F',
    match_kind: 'whole', origin_trace_id: null, chains: [DIRECT, VIA_LLM],
  }],
};

const links = { sameTrace: (h: LineageHop) => ({ search: `?iid=${h.interaction_id}` }) };

describe('LabelLineage', () => {
  it('renders nothing for a value with no carried-in origin, and for an origin without chains', () => {
    const { container } = render(
      <MemoryRouter>
        <LabelLineage label={{ ...PID, carried_in: [] }} traceId={T} />
        <LabelLineage label={{ ...PID, carried_in: [{ ...PID.carried_in[0], chains: undefined }] }} traceId={T} />
        <LabelLineage label={{ ...PID, carried_in: [{ ...PID.carried_in[0], chains: [] }] }} traceId={T} />
      </MemoryRouter>,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('shows the first chain open — born, the carriers, here — and folds the rest under a count', async () => {
    render(<MemoryRouter><LabelLineage label={PID} traceId={T} links={links} /></MemoryRouter>);
    const chains = () => screen.getAllByRole('list', { name: 'Lineage chain' });
    expect(chains()).toHaveLength(1);
    const rows = within(chains()[0]).getAllByRole('listitem').map((li) => li.textContent);
    expect(rows[0]).toMatch(/^born #80565 booking-agent → create-booking response/);
    expect(rows[1]).toBe('then #82049 booking-agent → notify-partner request “BK-83CE5F”');
    expect(rows[2]).toBe('here #83053 notify-partner → api.travel-partner.example:8000 request “BK-83CE5F”');

    await userEvent.click(screen.getByRole('button', { name: '1 more chain' }));
    expect(chains()).toHaveLength(2);
    const llm = within(chains()[1]).getAllByRole('listitem').map((li) => li.textContent);
    expect(llm).toHaveLength(5);
    expect(llm[1]).toBe('then #80603 booking-agent → qwen2.5:7b request “BK-83CE5F”');
    await userEvent.click(screen.getByRole('button', { name: 'Fewer chains' }));
    expect(chains()).toHaveLength(1);
  });

  it('shows the origin payload with the unit marked, and links each hop the way the page says', () => {
    render(<MemoryRouter><LabelLineage label={PID} traceId={T} links={links} /></MemoryRouter>);
    const excerpt = screen.getByTestId('origin-excerpt');
    expect(excerpt).toHaveTextContent('{"id": "BK-83CE5F", "guest_name": "<name>"}');
    expect(excerpt.querySelector('mark')).toHaveTextContent('BK-83CE5F');
    expect(screen.getByRole('link', { name: /#82049/ })).toHaveAttribute('href', '/?iid=ix-82049');
  });

  it('renders hops as plain text without links', () => {
    render(<MemoryRouter><LabelLineage label={PID} traceId={T} /></MemoryRouter>);
    expect(screen.queryAllByRole('link')).toHaveLength(0);
    expect(screen.getByText(/#82049 booking-agent/)).toBeInTheDocument();
  });

  it('marks a hop in another trace and links it to that trace, with no boundary on this trace\'s hops', () => {
    const earlier = { ...BORN, trace_id: 'aaaa1111bbbb2222', interaction_id: 'ix-o' };
    const label = { ...PID, carried_in: [{ ...PID.carried_in[0], origin_trace_id: 'aaaa1111bbbb2222',
      chains: [[earlier, DIRECT[1], DIRECT[2]]] }] };
    render(<MemoryRouter><LabelLineage label={label} traceId={T} links={links} /></MemoryRouter>);
    const marks = screen.getAllByTestId('trace-boundary');
    expect(marks).toHaveLength(1);
    expect(marks[0]).toHaveTextContent('in trace aaaa1111…');
    expect(screen.getByRole('link', { name: /#80565/ })).toHaveAttribute('href', '/traces/aaaa1111bbbb2222/flow?iid=ix-o');
    expect(screen.getByRole('link', { name: /#82049/ })).toHaveAttribute('href', '/?iid=ix-82049');
  });

  it('says so when a chain does not reach its origin, never dressing a carrier up as born', () => {
    const label = { ...PID, carried_in: [{ ...PID.carried_in[0], chains: [[DIRECT[1], DIRECT[2]]] }] };
    render(<MemoryRouter><LabelLineage label={label} traceId={T} /></MemoryRouter>);
    const rows = screen.getAllByRole('listitem').map((li) => li.textContent);
    expect(rows[0]).toBe('earlier hops not recorded');
    expect(rows[1]).toMatch(/^then #82049/);
    expect(screen.queryByText(/^born/)).toBeNull();
  });

  it('tells the flow view which interaction the pointer is on, and nothing for a hop in another trace', async () => {
    const setHover = vi.fn();
    const earlier = { ...BORN, trace_id: 'other-trace' };
    const label = { ...PID, carried_in: [{ ...PID.carried_in[0], chains: [[earlier, DIRECT[1], DIRECT[2]]] }] };
    render(
      <MemoryRouter>
        <HopHoverContext.Provider value={setHover}>
          <LabelLineage label={label} traceId={T} />
        </HopHoverContext.Provider>
      </MemoryRouter>,
    );
    await userEvent.hover(screen.getByText(/#82049/));
    expect(setHover).toHaveBeenLastCalledWith('ix-82049');
    await userEvent.unhover(screen.getByText(/#82049/));
    expect(setHover).toHaveBeenLastCalledWith(null);
    setHover.mockClear();
    await userEvent.hover(screen.getByText(/#80565/));
    expect(setHover).not.toHaveBeenCalled();
  });

  it('never says why', () => {
    render(<MemoryRouter><LabelLineage label={PID} traceId={T} /></MemoryRouter>);
    expect(screen.getByTestId('label-lineage')).not.toHaveTextContent(/why|caused|because/i);
  });
});
