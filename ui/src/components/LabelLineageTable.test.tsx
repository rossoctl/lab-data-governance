import { describe, it, expect } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { LabelLineageTable } from './LabelLineageTable';
import type { LegLabels, LineageHop } from '../types';

const T = 't1';
const hop = (seq: number, role: LineageHop['role'], iid: string, trace = T): LineageHop => ({
  interaction_id: iid, leg_type: 'request', seq, trace_id: trace, caller: 'a', callee: 'b',
  text: 'v', start: 0, end: 1, role, excerpt: null,
});
const LEG: LegLabels = {
  leg_type: 'request', payload_hash: 'h', seq: 9,
  found: { sensitivity_level: 'INTERNAL', regulatory_tags: ['PI'] },
  carried: { max_level: 'RESTRICTED', regulatory_tags: ['PII'], origin_count: 1 },
  labels: [
    {
      text: 'v', start: 0, end: 1, entity_type: 'PID', sensitivity_level: 'RESTRICTED', regulatory_tags: ['PII'],
      found_here: false,
      carried_in: [{
        origin_interaction_id: 'o', origin_leg_type: 'response', origin_seq: 1, caller: 'a', callee: 'b',
        unit_text: 'v', unit_value: 'v', match_kind: 'whole', origin_trace_id: null,
        chains: [
          [hop(1, 'origin', 'o'), hop(5, 'hop', 'p'), hop(9, 'here', 'x')],
          [hop(1, 'origin', 'o'), hop(3, 'hop', 'q'), hop(9, 'here', 'x')],
          [hop(1, 'origin', 'o'), hop(2, 'hop', 'r'), hop(5, 'hop', 'p'), hop(9, 'here', 'x')],
        ],
      }],
    },
    { text: 'w', start: 2, end: 3, entity_type: 'DATE', sensitivity_level: 'INTERNAL', regulatory_tags: ['PI'], found_here: true, carried_in: [] },
  ],
};
const links = { sameTrace: (h: LineageHop) => ({ search: `?iid=${h.interaction_id}` }) };

describe('LabelLineageTable', () => {
  it('lists carried-in values only, with where each was born and every previous hop once, in seq order', async () => {
    render(<MemoryRouter><LabelLineageTable leg={LEG} lineageStatus="complete" traceId={T} links={links} /></MemoryRouter>);
    const rows = within(screen.getByRole('list', { name: 'Label lineage' })).getAllByTestId('lineage-value');
    expect(rows).toHaveLength(1);
    expect(rows[0]).toHaveTextContent('PID');
    expect(within(rows[0]).getByRole('link', { name: /#1 a → b request/ })).toHaveAttribute('href', '/?iid=o');
    const prev = within(rows[0]).getAllByRole('link').slice(1).map((a) => a.textContent);
    expect(prev).toEqual(['#3 a → b request', '#5 a → b request']);
    expect(screen.getByText(/1 more value the classifier found here only/)).toBeInTheDocument();
    // The chains open on demand, all three.
    await userEvent.click(within(rows[0]).getByRole('button', { name: 'Show 3 chains' }));
    expect(screen.getByTestId('label-lineage')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Hide chains' }));
    expect(screen.queryByTestId('label-lineage')).toBeNull();
  });

  it('says so when nothing was carried in, and when lineage is not derived', () => {
    render(<MemoryRouter><LabelLineageTable leg={{ ...LEG, carried: null, labels: [LEG.labels[1]] }} lineageStatus={null} traceId={T} /></MemoryRouter>);
    expect(screen.getByText('No value on this payload was carried in from an earlier step.')).toBeInTheDocument();
    expect(screen.getByText('Label lineage: not derived for this trace')).toBeInTheDocument();
    expect(screen.queryByRole('list', { name: 'Label lineage' })).toBeNull();
  });

  it('shows the origin from its own fields, without a chains button, when the read carried no chains', () => {
    const origin = { ...LEG.labels[0].carried_in[0], chains: undefined };
    render(<MemoryRouter><LabelLineageTable leg={{ ...LEG, labels: [{ ...LEG.labels[0], carried_in: [origin] }] }} lineageStatus="complete" traceId={T} /></MemoryRouter>);
    expect(screen.getByText('#1 a → b response')).toBeInTheDocument();
    expect(screen.getByText('—')).toBeInTheDocument();
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('marks a previous hop in another trace and links it to that trace', () => {
    const origin = { ...LEG.labels[0].carried_in[0], origin_trace_id: 't0', chains: [[hop(1, 'origin', 'o', 't0'), hop(9, 'here', 'x')]] };
    render(<MemoryRouter><LabelLineageTable leg={{ ...LEG, labels: [{ ...LEG.labels[0], carried_in: [origin] }] }} lineageStatus="complete" traceId={T} links={links} /></MemoryRouter>);
    expect(screen.getAllByTestId('trace-boundary')).toHaveLength(2); // born at and previous hop are the same leg
    expect(screen.getAllByRole('link', { name: /#1 a → b/ })[0]).toHaveAttribute('href', '/traces/t0/flow?iid=o');
  });
});
