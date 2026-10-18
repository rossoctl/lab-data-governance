import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

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
  legType: 'request',
  hash: 'reqhash0deadbeef',
  kind: 'agent_request',
  lineage: { kind: 'derived', lineage: LINEAGE },
};
const RESPONSE: Leg = {
  label: 'Response',
  legType: 'response',
  hash: 'resphash0feedface',
  kind: 'agent_response',
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
      content: { q: 'flights' },
      byte_size: 42,
      classification: null,
    }),
  }));
}

/** Labels-read calls the mock has seen. */
const labelsCalls = () =>
  (fetch as ReturnType<typeof vi.fn>).mock.calls.filter((c) => String(c[0]).includes('/labels'));

const CARRIED_LEG = (legType: 'request' | 'response', hash: string) => ({
  leg_type: legType,
  payload_hash: hash,
  seq: legType === 'request' ? 92738 : 92739,
  found: null,
  carried: { max_level: 'RESTRICTED', regulatory_tags: ['PII'], origin_count: 1 },
  labels: [
    {
      text: 'BK-2850EB', start: 16, end: 25, entity_type: 'PID', sensitivity_level: 'RESTRICTED',
      regulatory_tags: ['PII'], found_here: false,
      carried_in: [{
        origin_interaction_id: 'efc92832', origin_leg_type: 'response', origin_seq: 90574,
        caller: 'booking-agent', callee: 'create-booking',
        unit_text: 'BK-2850EB', unit_value: 'BK-2850EB', match_kind: 'whole', origin_trace_id: null,
      }],
    },
  ],
});

/** `CARRIED_LEG` once the classifier has read the payload: the same label is
 *  also found here (trace e06cd67d…, request #92738). */
const FOUND_AND_CARRIED_LEG = (legType: 'request' | 'response', hash: string) => {
  const leg = CARRIED_LEG(legType, hash);
  return {
    ...leg,
    found: { sensitivity_level: 'INTERNAL', regulatory_tags: ['PI'] },
    labels: [{ ...leg.labels[0], found_here: true }],
  };
};

/** The request payload's verdict as the classifier wrote it. */
const CLASSIFIED = {
  sensitivity_level: 'INTERNAL',
  regulatory_tags: ['PI'],
  contains_identity_bundle: false,
  is_personalized: false,
  primary_domain: 'person',
  findings: [{ entity_type: 'OPID', start: 16, end: 25, text: 'BK-2850EB' }],
  model_version: 1,
};

/** Payloads as `mockPayloads` (or with `classification`), and the labels read
 *  answered by `labels` (a status code, or the body). */
function mockPayloadsAndLabels(labels: number | object, classification: object | null = null) {
  (fetch as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
    if (String(url).includes('/labels')) {
      return typeof labels === 'number'
        ? { ok: false, status: labels, json: async () => ({ error: 'db down' }) }
        : { ok: true, status: 200, json: async () => labels };
    }
    return {
      ok: true,
      status: 200,
      json: async () => ({
        content_hash: String(url).split('/').pop(),
        content: { q: 'flights' },
        byte_size: 42,
        classification,
      }),
    };
  });
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

  it('reads the labels once per interaction, only once a Classification pane opens, shared by both legs', async () => {
    mockPayloadsAndLabels({
      lineage_status: 'complete',
      legs: [CARRIED_LEG('request', REQUEST.hash), CARRIED_LEG('response', RESPONSE.hash)],
    });
    // The app's own freshness window (main.tsx): within it a cached entry is
    // served to a newly mounted reader; the test helper's zero would refetch.
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 30_000 } } });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <LegTabs legs={[REQUEST, RESPONSE]} traceId="T1" interactionId="ix-1" />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await waitFor(() => expect(screen.getByText(/"flights"/)).toBeInTheDocument());
    await userEvent.click(screen.getByRole('tab', { name: 'Request: Data lineage' }));
    expect(labelsCalls()).toHaveLength(0);

    await userEvent.click(screen.getByRole('tab', { name: 'Request: Classification' }));
    await waitFor(() => expect(screen.getByText('Carried in (label lineage)')).toBeInTheDocument());
    expect(labelsCalls()).toHaveLength(1);
    expect(labelsCalls()[0][0]).toContain('/traces/T1/interactions/ix-1/labels');

    // The other leg reads the same cached response: its own row, no second read.
    await userEvent.click(screen.getByRole('tab', { name: 'Response' }));
    await userEvent.click(screen.getByRole('tab', { name: 'Response: Classification' }));
    await waitFor(() => expect(screen.getByText('Carried in (label lineage)')).toBeInTheDocument());
    expect(labelsCalls()).toHaveLength(1);
  });

  it('never reads a failed labels read as "nothing carried in"', async () => {
    mockPayloadsAndLabels(500);
    renderWithProviders(<LegTabs legs={[REQUEST]} traceId="T1" interactionId="ix-1" />);
    await userEvent.click(screen.getByRole('tab', { name: 'Request: Classification' }));
    await waitFor(() => expect(labelsCalls()).toHaveLength(1));
    await waitFor(() => expect(screen.getByText(/not yet classified/i)).toBeInTheDocument());
    // The classifier's view stands alone: no carried-in claim, and no claim that
    // lineage was not derived — the read never answered.
    expect(screen.queryByText(/Carried in/)).toBeNull();
    expect(screen.queryByText(/not derived/)).toBeNull();
  });

  it('narrows a body without legs to nothing', async () => {
    // The shared mock answers every URL with a payload; the labels hook must not
    // mistake that for labels.
    mockPayloads();
    renderWithProviders(<LegTabs legs={[REQUEST]} traceId="T1" interactionId="ix-1" />);
    await userEvent.click(screen.getByRole('tab', { name: 'Request: Classification' }));
    await waitFor(() => expect(labelsCalls()).toHaveLength(1));
    await waitFor(() => expect(screen.getByText(/not yet classified/i)).toBeInTheDocument());
    expect(screen.queryByText(/Carried in/)).toBeNull();
    // Nor a claim that lineage was not derived: that body answered nothing.
    expect(screen.queryByText(/not derived/)).toBeNull();
  });

  it("never sets another payload's labels beside this payload's verdict", async () => {
    // The labels read is at the leg's current payload; the pane shows an older one.
    mockPayloadsAndLabels({ lineage_status: 'complete', legs: [CARRIED_LEG('request', 'a-newer-payload')] });
    renderWithProviders(<LegTabs legs={[REQUEST]} traceId="T1" interactionId="ix-1" />);
    await userEvent.click(screen.getByRole('tab', { name: 'Request: Classification' }));
    await waitFor(() => expect(labelsCalls()).toHaveLength(1));
    await waitFor(() => expect(screen.getByText(/not yet classified/i)).toBeInTheDocument());
    expect(screen.queryByText(/Carried in/)).toBeNull();
    expect(screen.queryByText(/not derived/)).toBeNull();
  });

  it('never sets "Not yet classified" beside labels found here', async () => {
    // The labels read is live and newer: the classifier has since read the
    // payload. The payload read still has no verdict.
    mockPayloadsAndLabels({ lineage_status: 'complete', legs: [FOUND_AND_CARRIED_LEG('request', REQUEST.hash)] });
    renderWithProviders(<LegTabs legs={[REQUEST]} traceId="T1" interactionId="ix-1" />);
    await userEvent.click(screen.getByRole('tab', { name: 'Request: Classification' }));
    await waitFor(() => expect(labelsCalls()).toHaveLength(1));
    await waitFor(() => expect(screen.getByText(/not yet classified/i)).toBeInTheDocument());
    expect(screen.queryByText(/Found here/)).toBeNull();
    expect(screen.queryByText(/Carried in/)).toBeNull();
  });

  it("keeps the classifier's findings when the labels read predates its verdict", async () => {
    // The reverse: the payload read has the verdict, the labels read was made
    // before it (found null). The classifier's view stands alone, findings included.
    mockPayloadsAndLabels({ lineage_status: 'partial', legs: [CARRIED_LEG('request', REQUEST.hash)] }, CLASSIFIED);
    renderWithProviders(<LegTabs legs={[REQUEST]} traceId="T1" interactionId="ix-1" />);
    await userEvent.click(screen.getByRole('tab', { name: 'Request: Classification' }));
    await waitFor(() => expect(labelsCalls()).toHaveLength(1));
    await waitFor(() => expect(screen.getByRole('grid', { name: 'Findings' })).toBeInTheDocument());
    expect(screen.queryByText(/Found here/)).toBeNull();
    expect(screen.queryByText(/Carried in/)).toBeNull();
  });

  it('makes no labels read without the interaction it belongs to', async () => {
    mockPayloads();
    renderWithProviders(<LegTabs legs={[REQUEST]} />);
    await userEvent.click(screen.getByRole('tab', { name: 'Request: Classification' }));
    await waitFor(() => expect(screen.getByText(/not yet classified/i)).toBeInTheDocument());
    expect(labelsCalls()).toHaveLength(0);
  });
});
