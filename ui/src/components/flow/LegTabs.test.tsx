import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { renderWithProviders } from '../../test/renderWithProviders';
import { LegTabs, type Leg } from './LegTabs';
import type { DataLineage } from '../../types';

/**
 * The two-level tab set on its own, driven directly rather than through
 * `FlowTables`.
 *
 * The panel-level cases live in `FlowTables.test.tsx`, where the tabs are
 * reached the way a reader reaches them. These are the ones that view cannot
 * express: a leg's Payload tab is active on arrival, so "a leg that never
 * activated a payload-backed section issues no payload read" — the load-bearing
 * half of the ADR-0024 / ADR-0028 D5 fetch split — has to be asserted against
 * the component, by picking Data lineage as the very first interaction on a
 * freshly-mounted leg.
 */

const LINEAGE: DataLineage = {
  data_sources: ['agent-a'],
  source_transformations: { 'agent-a': ['summarization'] },
  entities: ['agent-a'],
  seq: 1,
};

const REQUEST: Leg = {
  label: 'Request',
  hash: 'reqhash0deadbeef',
  lineage: { kind: 'derived', lineage: LINEAGE },
};
const RESPONSE: Leg = {
  label: 'Response',
  hash: 'resphash0feedface',
  lineage: { kind: 'pending' },
};

/** Payload-read calls the mock has seen. */
const payloadCalls = () =>
  (fetch as ReturnType<typeof vi.fn>).mock.calls.filter((c) =>
    String(c[0]).includes('/payloads/'),
  );

function mockPayloads() {
  (fetch as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => ({
    ok: true,
    status: 200,
    json: async () => ({
      content_hash: String(url).split('/').pop(),
      content_kind: 'json',
      content: { q: 'flights' },
      byte_size: 42,
      classification: null,
    }),
  }));
}

describe('LegTabs', () => {
  beforeEach(() => vi.stubGlobal('fetch', vi.fn()));
  afterEach(() => vi.unstubAllGlobals());

  it('issues no payload read for a leg whose payload-backed tabs are never activated', async () => {
    // The strict form of the ADR-0028 D5 half of the split: lineage arrives on
    // the trace-scoped read the panel already holds, so a reader who only ever
    // looks at provenance pays for no body. A `usePayload` that fired on mount —
    // or a gate keyed on "the pane exists" rather than on which section has been
    // activated — would break this silently and invisibly.
    //
    // `Payload` is a leg's default tab, so the leg that can state this is the
    // NON-default one: the Response leg's inner tabs are never activated at all
    // while Request is showing, and its hash is therefore never requested.
    mockPayloads();
    renderWithProviders(<LegTabs legs={[REQUEST, RESPONSE]} />);
    await userEvent.click(screen.getByRole('tab', { name: 'Request: Data lineage' }));
    await waitFor(() => expect(screen.getByLabelText('Data sources')).toBeInTheDocument());
    // Exactly one read, for the one leg whose Payload tab was ever active — the
    // lineage tab added none, and the untouched leg cost nothing.
    expect(payloadCalls()).toHaveLength(1);
    expect(payloadCalls()[0][0]).toContain('reqhash0deadbeef');
  });

  it('does not refetch the payload when the reader tabs away to lineage and back', async () => {
    // The body is read once per leg and reused across all three of its panes:
    // walking Payload → Data lineage → Payload → Classification must not pay for
    // it again. This is why `usePayload` is not gated on the active section — a
    // gate that closed on Data lineage would flip `enabled` off and on again, and
    // a query re-enabled after its data went stale refetches. Since exactly one
    // section is always active and Payload is the default, such a gate could
    // never be false on a mounted pane anyway; laziness is the active-leg-only
    // mount instead (the two tests either side of this one).
    mockPayloads();
    renderWithProviders(<LegTabs legs={[REQUEST]} />);
    await waitFor(() => expect(screen.getByText(/"flights"/)).toBeInTheDocument());
    expect(payloadCalls()).toHaveLength(1);
    await userEvent.click(screen.getByRole('tab', { name: 'Request: Data lineage' }));
    await waitFor(() => expect(screen.getByLabelText('Data sources')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('tab', { name: /Request: Payload reqhash0/ }));
    await waitFor(() => expect(screen.getByText(/"flights"/)).toBeInTheDocument());
    await userEvent.click(screen.getByRole('tab', { name: 'Request: Classification' }));
    await waitFor(() => expect(screen.getByText(/not yet classified/i)).toBeInTheDocument());
    expect(payloadCalls()).toHaveLength(1);
  });

  it('never reads the payload of a leg that is not the active leg', async () => {
    // The regression tabs introduce over the collapsibles they replaced: PF keeps
    // inactive tab content mounted-but-hidden, and a mounted leg pane runs its
    // own `usePayload`. LegTabs renders ONLY the active leg's pane for exactly
    // this reason, so the Response body is never dragged over the wire for a
    // reader looking at the Request.
    mockPayloads();
    renderWithProviders(<LegTabs legs={[REQUEST, RESPONSE]} />);
    await waitFor(() => expect(payloadCalls()).toHaveLength(1));
    expect(payloadCalls()[0][0]).toContain('reqhash0deadbeef');
    // Every inner tab of the active leg, and still nothing for the other leg.
    await userEvent.click(screen.getByRole('tab', { name: 'Request: Classification' }));
    await userEvent.click(screen.getByRole('tab', { name: 'Request: Data lineage' }));
    await waitFor(() => expect(screen.getByLabelText('Data sources')).toBeInTheDocument());
    expect(payloadCalls().some((c) => String(c[0]).includes('resphash0feedface'))).toBe(false);
    // Only selecting the Response leg reads its body.
    await userEvent.click(screen.getByRole('tab', { name: 'Response' }));
    await waitFor(() =>
      expect(payloadCalls().some((c) => String(c[0]).includes('resphash0feedface'))).toBe(true),
    );
  });

  it('activates the leg that is present when the default one is absent', async () => {
    // A response-only interaction: `Request` is the preferred default but has no
    // payload, so it contributes no tab — and the remaining leg must be ACTIVE,
    // not leave the pane blank.
    mockPayloads();
    renderWithProviders(<LegTabs legs={[RESPONSE]} />);
    expect(screen.queryByRole('tab', { name: 'Request' })).toBeNull();
    expect(screen.getByRole('tab', { name: 'Response' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    expect(screen.getByRole('tab', { name: /Response: Payload resphash/ })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    await waitFor(() => expect(screen.getByText(/"flights"/)).toBeInTheDocument());
  });

  it('renders nothing when no leg carried a payload', () => {
    // A payload-less leg gets no tab, deliberately including no lineage-only tab:
    // P-data-lineage derives from payload content, so such a tab would sit on
    // "not yet computed" forever, promising a derivation that is never coming.
    mockPayloads();
    const { container } = renderWithProviders(<LegTabs legs={[]} />);
    expect(container).toBeEmptyDOMElement();
    expect(payloadCalls()).toHaveLength(0);
  });

  it('keeps the two tab levels apart in the accessible tree', async () => {
    // `Payload` appears once per leg, so an unqualified accessible name would be
    // ambiguous the moment both legs are reachable — the inner tabs are
    // leg-qualified (`Request: Payload …`), matching the qualification the
    // disclosures they replaced carried, while the outer tabs keep the bare leg
    // name a reader navigates by.
    mockPayloads();
    renderWithProviders(<LegTabs legs={[REQUEST, RESPONSE]} />);
    expect(screen.getByRole('tab', { name: 'Request' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Response' })).toBeInTheDocument();
    // Exactly one tab answers to each inner name — no collision with the other
    // leg's identically-labelled tab.
    expect(screen.getAllByRole('tab', { name: /: Payload / })).toHaveLength(1);
    expect(screen.getAllByRole('tab', { name: /: Classification$/ })).toHaveLength(1);
    expect(screen.getAllByRole('tab', { name: /: Data lineage$/ })).toHaveLength(1);
    await userEvent.click(screen.getByRole('tab', { name: 'Response' }));
    expect(screen.getAllByRole('tab', { name: /: Payload / })).toHaveLength(1);
    expect(screen.getByRole('tab', { name: /Response: Payload resphash/ })).toBeInTheDocument();
  });
});
