import { describe, it, expect } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { ClassificationView } from './ClassificationView';
import type { Classification, LabelOrigin, LegLabels } from '../types';

/** A fully-populated verdict fixture; individual tests override slices of it. */
function verdict(over: Partial<Classification> = {}): Classification {
  return {
    sensitivity_level: 'CONFIDENTIAL',
    regulatory_tags: ['PII', 'GDPR'],
    contains_identity_bundle: true,
    is_personalized: true,
    primary_domain: 'person',
    findings: [
      { entity_type: 'EMAIL', start: 5, end: 20, text: 'jo@example.com' },
    ],
    model_version: 1,
    ...over,
  };
}

describe('ClassificationView', () => {
  it('shows the document-level sensitivity level as a badge', () => {
    render(<ClassificationView classification={verdict({ sensitivity_level: 'RESTRICTED' })} />);
    expect(screen.getByText('RESTRICTED')).toBeInTheDocument();
  });

  it('renders a null classification distinctly as "not yet classified", with no sensitivity verdict', () => {
    render(<ClassificationView classification={null} />);
    // The eventual-consistency window (P-classification has not run yet) is
    // shown as a distinct, verdict-less state — not a real PUBLIC verdict.
    expect(screen.getByText(/not yet classified/i)).toBeInTheDocument();
    // Crucially it must NOT read as a PUBLIC / zero-findings verdict: no
    // sensitivity level of any kind is rendered.
    expect(screen.queryByText('PUBLIC')).toBeNull();
    expect(screen.queryByText('RESTRICTED')).toBeNull();
  });

  it('colors the sensitivity badge by level (PUBLIC→green … RESTRICTED→red)', () => {
    const cases: Array<[Classification['sensitivity_level'], string]> = [
      ['PUBLIC', 'pf-m-green'],
      ['INTERNAL', 'pf-m-blue'],
      ['CONFIDENTIAL', 'pf-m-orange'],
      ['RESTRICTED', 'pf-m-red'],
    ];
    for (const [level, colorClass] of cases) {
      const { unmount } = render(
        <ClassificationView classification={verdict({ sensitivity_level: level })} />,
      );
      // The PF Label carries the level-appropriate color modifier so the
      // escalation PUBLIC→RESTRICTED reads at a glance.
      const badge = screen.getByText(level).closest('.pf-v5-c-label');
      expect(badge).toHaveClass(colorClass);
      unmount();
    }
  });

  it('lists the regulatory tags the payload carries', () => {
    render(
      <ClassificationView
        classification={verdict({ regulatory_tags: ['PII', 'GDPR', 'PCI'] })}
      />,
    );
    expect(screen.getByText('PII')).toBeInTheDocument();
    expect(screen.getByText('GDPR')).toBeInTheDocument();
    expect(screen.getByText('PCI')).toBeInTheDocument();
  });

  it('does not render a regulatory-tags section when there are none', () => {
    render(<ClassificationView classification={verdict({ regulatory_tags: [] })} />);
    expect(screen.queryByText(/regulatory tags/i)).toBeNull();
  });

  it('shows an identity-bundle indicator when the payload holds one', () => {
    render(<ClassificationView classification={verdict({ contains_identity_bundle: true })} />);
    expect(screen.getByText(/identity bundle/i)).toBeInTheDocument();
  });

  it('omits the identity-bundle indicator when the payload holds none', () => {
    render(<ClassificationView classification={verdict({ contains_identity_bundle: false })} />);
    expect(screen.queryByText(/identity bundle/i)).toBeNull();
  });

  it('lists each finding with its detected type (NER tag) and flagged text region', () => {
    render(
      <ClassificationView
        classification={verdict({
          findings: [
            { entity_type: 'SSN', start: 10, end: 21, text: '123-45-6789' },
            { entity_type: 'EMAIL', start: 30, end: 44, text: 'jo@example.com' },
          ],
        })}
      />,
    );
    const findings = screen.getByLabelText('Findings');
    // Each finding's detected NER-tag type is shown …
    expect(within(findings).getByText('SSN')).toBeInTheDocument();
    expect(within(findings).getByText('EMAIL')).toBeInTheDocument();
    // … alongside the flagged text region it detected.
    expect(within(findings).getByText('123-45-6789')).toBeInTheDocument();
    expect(within(findings).getByText('jo@example.com')).toBeInTheDocument();
  });

  it('shows a "no findings" note for a real verdict that detected nothing', () => {
    render(<ClassificationView classification={verdict({ findings: [] })} />);
    // A zero-Findings verdict is a real result, distinct from the null "not yet
    // classified" state — say so explicitly rather than rendering nothing.
    expect(screen.getByText(/no findings/i)).toBeInTheDocument();
  });

  it('distinguishes a real PUBLIC / zero-findings verdict from "not yet classified"', () => {
    render(
      <ClassificationView
        classification={verdict({
          sensitivity_level: 'PUBLIC',
          regulatory_tags: [],
          contains_identity_bundle: false,
          findings: [],
        })}
      />,
    );
    // A clean payload is a real PUBLIC verdict (CONTEXT.md), NOT the null state.
    expect(screen.getByText('PUBLIC')).toBeInTheDocument();
    expect(screen.queryByText(/not yet classified/i)).toBeNull();
  });
});

/**
 * The labels read's request leg from a live run (trace
 * e06cd67d…, notify-partner → api.travel-partner.example:8000, seq 92738), as
 * the server's merge orders it: the carried-only PID above the OPID on the same
 * span, two origins on the first date, one of them a year the classifier marked
 * at get-flights.
 */
const REFERENCE_LEG: LegLabels = {
  leg_type: 'request',
  payload_hash: '464d602179ac81d0d484466b6363aaed4e8b37caca353abbe1a198a43534cc46',
  seq: 92738,
  found: { sensitivity_level: 'INTERNAL', regulatory_tags: ['PI'] },
  carried: { max_level: 'RESTRICTED', regulatory_tags: ['PI', 'PII'], origin_count: 4 },
  labels: [
    {
      text: 'BK-2850EB', start: 16, end: 25, entity_type: 'PID', sensitivity_level: 'RESTRICTED',
      regulatory_tags: ['PII'], found_here: false,
      carried_in: [origin('efc92832', 'response', 90574, 'booking-agent', 'create-booking', 'BK-2850EB')],
    },
    {
      text: 'BK-2850EB', start: 16, end: 25, entity_type: 'OPID', sensitivity_level: 'INTERNAL',
      regulatory_tags: ['PI'], found_here: true,
      carried_in: [origin('c0f9059b', 'response', 91707, 'booking-agent', 'qwen2.5:7b', 'BK-2850EB')],
    },
    {
      text: '2035-11-08', start: 38, end: 48, entity_type: 'DATE', sensitivity_level: 'INTERNAL',
      regulatory_tags: ['PI'], found_here: true,
      carried_in: [
        origin('76099bc3', 'request', 87834, 'demo-client', 'travel-advisor', '2035-11-08'),
        { ...origin('50f2e157', 'response', 87854, 'travel-advisor', 'get-flights', '2035-11-08'), unit_text: '2035' },
      ],
    },
    {
      text: '2035-11-13', start: 52, end: 62, entity_type: 'DATE', sensitivity_level: 'INTERNAL',
      regulatory_tags: ['PI'], found_here: true,
      carried_in: [origin('76099bc3', 'request', 87834, 'demo-client', 'travel-advisor', '2035-11-13')],
    },
  ],
};

function origin(
  iid: string, leg: 'request' | 'response', seq: number, caller: string, callee: string, value: string,
): LabelOrigin {
  return {
    origin_interaction_id: iid, origin_leg_type: leg, origin_seq: seq, caller, callee,
    unit_text: value, unit_value: value, match_kind: 'whole', origin_trace_id: null,
  };
}

/** The classifier's own verdict on the reference payload. */
const REFERENCE_VERDICT = verdict({
  sensitivity_level: 'INTERNAL',
  regulatory_tags: ['PI'],
  contains_identity_bundle: false,
  findings: [
    { entity_type: 'OPID', start: 16, end: 25, text: 'BK-2850EB' },
    { entity_type: 'DATE', start: 38, end: 48, text: '2035-11-08' },
    { entity_type: 'DATE', start: 52, end: 62, text: '2035-11-13' },
  ],
});

const rowTexts = () =>
  within(screen.getByLabelText('Labels'))
    .getAllByRole('row')
    .slice(1)
    .map((r) => r.textContent ?? '');

describe('ClassificationView — found here and carried in', () => {
  it('splits the verdict into the classifier line and the label-lineage line', () => {
    render(
      <ClassificationView
        classification={REFERENCE_VERDICT}
        labels={{ leg: REFERENCE_LEG, lineageStatus: 'complete' }}
      />,
    );
    const found = screen.getByText('Found here (classifier)').parentElement!;
    expect(within(found).getByText('INTERNAL')).toBeInTheDocument();
    expect(found).toHaveTextContent('PI');
    const carried = screen.getByText('Carried in (data lineage)').parentElement!;
    expect(within(carried).getByText('RESTRICTED')).toBeInTheDocument();
    expect(carried).toHaveTextContent('PI, PII');
    // Origin legs, never the edge count (5 on the record).
    expect(carried).toHaveTextContent('from 4 earlier steps');
    expect(screen.getByText(/this same text had this label earlier in the conversation/)).toBeInTheDocument();
    // The trace page states facts; it never speaks of a decision.
    expect(screen.queryByText(/used/i)).toBeNull();
    // The Findings table gives way to the Labels table.
    expect(screen.queryByLabelText('Findings')).toBeNull();
  });

  it('lists one row per span and type, the carried-only PID above the OPID', () => {
    render(
      <ClassificationView
        classification={REFERENCE_VERDICT}
        labels={{ leg: REFERENCE_LEG, lineageStatus: 'complete' }}
      />,
    );
    const rows = rowTexts();
    expect(rows).toHaveLength(4);
    expect(rows[0]).toMatch(/^BK-2850EBPID.*RESTRICTED.*PII/);
    expect(rows[1]).toMatch(/^BK-2850EBOPID/);
    const [pid, opid] = within(screen.getByLabelText('Labels')).getAllByRole('row').slice(1);
    // The PID was only carried in; the OPID was both found here and carried in.
    expect(within(pid).queryByText('Found here')).toBeNull();
    expect(within(pid).getByText('Carried in')).toBeInTheDocument();
    expect(within(opid).getByText('Found here')).toBeInTheDocument();
    expect(within(opid).getByText('Carried in')).toBeInTheDocument();
    expect(pid).toHaveTextContent('booking-agent → create-booking, response #90574');
  });

  it('shows every origin of a label, and what the classifier marked there when it differs', () => {
    render(
      <ClassificationView
        classification={REFERENCE_VERDICT}
        labels={{ leg: REFERENCE_LEG, lineageStatus: 'complete' }}
      />,
    );
    const date = rowTexts()[2];
    expect(date).toContain('demo-client → travel-advisor, request #87834');
    expect(date).toContain('travel-advisor → get-flights, response #87854 (classifier marked “2035” there)');
    // The other origins hold the value as marked: no note.
    expect(rowTexts()[3]).not.toContain('classifier marked');
  });

  it('keeps the classifier-only view, unchanged, when nothing was carried in', () => {
    const quiet: LegLabels = { ...REFERENCE_LEG, carried: null, labels: [] };
    render(<ClassificationView classification={REFERENCE_VERDICT} labels={{ leg: quiet, lineageStatus: 'complete' }} />);
    expect(screen.getByLabelText('Findings')).toBeInTheDocument();
    expect(screen.queryByText(/Carried in/)).toBeNull();
    expect(screen.queryByText(/not derived/)).toBeNull();
  });

  it('says data lineage is not derived only when the read says so', () => {
    const { unmount } = render(
      <ClassificationView classification={REFERENCE_VERDICT} labels={{ leg: null, lineageStatus: null }} />,
    );
    expect(screen.getByText('Data lineage: not derived for this trace')).toBeInTheDocument();
    expect(screen.getByLabelText('Findings')).toBeInTheDocument();
    unmount();
    // No answered read (loading, failed, or none made): no claim either way.
    render(<ClassificationView classification={REFERENCE_VERDICT} />);
    expect(screen.queryByText(/not derived/)).toBeNull();
  });

  it('links an origin in this trace to its interaction, and leaves one in another trace unlinked', () => {
    const [pid, ...rest] = REFERENCE_LEG.labels;
    const elsewhere = { ...pid, carried_in: [{ ...pid.carried_in[0], origin_trace_id: 'an-earlier-trace' }] };
    render(
      <MemoryRouter initialEntries={['/traces/e06cd67d/flow?iid=f48567d1']}>
        <ClassificationView
          classification={REFERENCE_VERDICT}
          labels={{ leg: { ...REFERENCE_LEG, labels: [elsewhere, ...rest] }, lineageStatus: 'complete' }}
        />
      </MemoryRouter>,
    );
    const [first, second] = within(screen.getByLabelText('Labels')).getAllByRole('row').slice(1);
    expect(within(first).queryByRole('link')).toBeNull();
    expect(first).toHaveTextContent('booking-agent → create-booking, response #90574');
    expect(within(second).getByRole('link')).toHaveAttribute('href', expect.stringContaining('iid=c0f9059b'));
  });

  it('lists what was carried into a payload the classifier has not read yet', () => {
    render(
      <ClassificationView
        classification={null}
        labels={{ leg: { ...REFERENCE_LEG, found: null }, lineageStatus: 'partial' }}
      />,
    );
    expect(screen.getByText(/not yet classified/i)).toBeInTheDocument();
    expect(screen.queryByText('Found here (classifier)')).toBeNull();
    expect(screen.getByText('Carried in (data lineage)')).toBeInTheDocument();
    expect(screen.getByLabelText('Labels')).toBeInTheDocument();
  });
});

describe('ClassificationView — the chains live on the Data lineage tab', () => {
  it('adds no row under a value for its chains, even when the read carries them', () => {
    const hop = (seq: number, role: 'origin' | 'hop' | 'here', iid: string) => ({
      interaction_id: iid, leg_type: 'response' as const, seq, trace_id: 'e06cd67d', caller: 'booking-agent',
      callee: 'create-booking', text: 'BK-2850EB', start: 0, end: 9, role, excerpt: null,
    });
    const withChains: LegLabels = {
      ...REFERENCE_LEG,
      labels: [
        {
          ...REFERENCE_LEG.labels[0],
          carried_in: [{
            ...REFERENCE_LEG.labels[0].carried_in[0],
            chains: [[hop(90574, 'origin', 'efc92832'), hop(92738, 'here', 'f48567d1')]],
          }],
        },
        REFERENCE_LEG.labels[1],
      ],
    };
    render(
      <MemoryRouter>
        <ClassificationView classification={REFERENCE_VERDICT} labels={{ leg: withChains, lineageStatus: 'complete' }} />
      </MemoryRouter>,
    );
    expect(within(screen.getByLabelText('Labels')).getAllByRole('row').slice(1)).toHaveLength(2);
    expect(screen.queryByTestId('label-lineage')).toBeNull();
    expect(screen.queryByText(/^born/)).toBeNull();
  });
});
