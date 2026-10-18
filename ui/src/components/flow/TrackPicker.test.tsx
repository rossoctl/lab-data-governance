import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithProviders } from '../../test/renderWithProviders';
import { TrackPicker } from './TrackPicker';
import { LabelTrackContext } from './labelTrack';
import type { TrackedValue } from '../../lib/labelTrack';

/** A live booking trace's partner call, cut down: the PID on the request and its echo on the response, a DATE found here only. */
const hop = (seq: number, role: string, iid: string, leg: string) => ({
  interaction_id: iid, leg_type: leg, seq, trace_id: 'T1', caller: 'a', callee: 'b', text: 'BK-83CE5F', start: 16, end: 25, role, excerpt: null,
});
const pid = (leg: string, seq: number) => ({
  text: 'BK-83CE5F', start: 16, end: 25, entity_type: 'PID', sensitivity_level: 'RESTRICTED', regulatory_tags: ['PII'], found_here: false,
  carried_in: [{ origin_interaction_id: 'i0', origin_leg_type: 'response', origin_seq: 0, caller: 'a', callee: 'b',
    unit_text: 'BK-83CE5F', unit_value: 'BK-83CE5F', match_kind: 'whole', origin_trace_id: null,
    chains: [[hop(0, 'origin', 'i0', 'response'), hop(seq, 'here', 'i1', leg)]] }],
});
const LABELS = {
  lineage_status: 'complete',
  legs: [
    { leg_type: 'request', payload_hash: 'h1', seq: 83053, found: null, carried: { max_level: 'RESTRICTED', regulatory_tags: ['PII'], origin_count: 1 },
      labels: [pid('request', 83053), { text: '2033-03-29', start: 38, end: 48, entity_type: 'DATE', sensitivity_level: 'INTERNAL', regulatory_tags: ['PI'], found_here: true, carried_in: [] }] },
    { leg_type: 'response', payload_hash: 'h2', seq: 83054, found: null, carried: { max_level: 'RESTRICTED', regulatory_tags: ['PII'], origin_count: 1 },
      labels: [pid('response', 83054)] },
  ],
};

function renderPicker(tracked: Map<string, TrackedValue> = new Map(), legType: 'request' | 'response' = 'request') {
  const set = vi.fn();
  const replace = vi.fn();
  const onClose = vi.fn();
  renderWithProviders(
    <LabelTrackContext.Provider value={{ available: true, tracked, set, replace }}>
      <TrackPicker traceId="T1" target={{ interactionId: 'i1', legType, at: { x: 10, y: 20 } }} onClose={onClose} />
    </LabelTrackContext.Provider>,
  );
  return { set, replace, onClose };
}

describe('TrackPicker', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (String(url).includes('/labels')) return { ok: true, status: 200, json: async () => LABELS };
      throw new Error(`unexpected fetch: ${url}`);
    }));
  });
  afterEach(() => vi.unstubAllGlobals());

  it('lists both legs, the pointed-at one open and the other folded, one tick box per carried-in value', async () => {
    renderPicker();
    const dialog = await screen.findByRole('dialog', { name: 'Track values on the graph' });
    await waitFor(() => expect(within(dialog).getByTestId('track-group-request')).toHaveTextContent('Request #83053'));
    expect(within(dialog).getByTestId('track-group-request')).toHaveTextContent('0 of 1');
    expect(within(dialog).getByTestId('track-group-response')).toHaveTextContent('Response #83054');
    // The request group is open: its one carried-in value (the DATE found here only is not offered).
    expect(within(dialog).getAllByRole('checkbox')).toHaveLength(1);
    expect(within(dialog).getByRole('checkbox', { name: 'Track BK-83CE5F (PID) on the graph' })).not.toBeChecked();
    // The response group folds open on demand.
    await userEvent.click(within(within(dialog).getByTestId('track-group-response')).getByRole('button', { name: /Response/ }));
    expect(within(dialog).getAllByRole('checkbox')).toHaveLength(2);
    expect(dialog).toHaveStyle({ position: 'fixed' });
  });

  it('ticks a value in and out, and "all" / "none" replace the leg\'s values keeping the rest', async () => {
    const other: TrackedValue = { key: 'z/request/DATE/0-1/w', interactionId: 'z', legType: 'request', text: 'w', entityType: 'DATE', chains: [] };
    const { set, replace } = renderPicker(new Map([[other.key, other]]));
    const dialog = await screen.findByRole('dialog');
    const box = await within(dialog).findByRole('checkbox', { name: 'Track BK-83CE5F (PID) on the graph' });
    await userEvent.click(box);
    expect(set).toHaveBeenCalledWith('i1/request/PID/16-25/BK-83CE5F', expect.objectContaining({ text: 'BK-83CE5F' }));
    await userEvent.click(within(within(dialog).getByTestId('track-group-request')).getByRole('button', { name: 'all' }));
    expect(replace.mock.calls[0][0].map((v: TrackedValue) => v.key)).toEqual([other.key, 'i1/request/PID/16-25/BK-83CE5F']);
    await userEvent.click(within(within(dialog).getByTestId('track-group-request')).getByRole('button', { name: 'none' }));
    expect(replace.mock.calls[1][0].map((v: TrackedValue) => v.key)).toEqual([other.key]);
  });

  it('shows a tracked value ticked and the count, and closes on Done, Escape and a click outside', async () => {
    const v: TrackedValue = { key: 'i1/request/PID/16-25/BK-83CE5F', interactionId: 'i1', legType: 'request', text: 'BK-83CE5F', entityType: 'PID', chains: [] };
    const { onClose } = renderPicker(new Map([[v.key, v]]));
    const dialog = await screen.findByRole('dialog');
    await waitFor(() => expect(within(dialog).getByRole('checkbox', { name: /BK-83CE5F/ })).toBeChecked());
    expect(within(dialog).getByTestId('track-group-request')).toHaveTextContent('1 of 1');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Done' }));
    expect(onClose).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(2);
    fireEvent.mouseDown(document.body);
    expect(onClose).toHaveBeenCalledTimes(3);
    fireEvent.mouseDown(dialog);
    expect(onClose).toHaveBeenCalledTimes(3);
  });

  it('says so when nothing on the interaction was carried in', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ lineage_status: 'complete', legs: [{ ...LABELS.legs[0], labels: [LABELS.legs[0].labels[1]] }] }) })));
    renderPicker();
    expect(await screen.findByText('No value on this interaction was carried in from an earlier step.')).toBeInTheDocument();
    expect(screen.queryByRole('checkbox')).toBeNull();
  });
});
