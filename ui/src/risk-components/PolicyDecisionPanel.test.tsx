import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithProviders } from '../test/renderWithProviders';
import { PolicyDecisionPanel } from './PolicyDecisionPanel';
import { LabelTrackContext } from '../components/flow/labelTrack';
import type { Entity } from '../lib/flow';
import type { ForestInteraction, InteractionRisk, RuleListItem } from '../risk-api/types';

/** A minimal but complete `InteractionRisk`, overridable per test. */
function mkRisk(over: Partial<InteractionRisk> = {}): InteractionRisk {
  return {
    interaction_risk_id: 'ir1',
    interaction_id: 'i1',
    trace_id: 't1',
    parent_interaction_id: null,
    caller_entity_id: 'e1',
    callee_entity_id: 'e2',
    version: 1,
    computed_at: '2026-05-01T12:00:00Z',
    risk_level: 'critical',
    enforcement_type: 'block',
    policy_event_count: 1,
    triggered_rule_ids: ['DG-001'],
    classification_summary: null,
    opa_policy_versions_used: ['v1'],
    overall_confidence: 0.9,
    ...over,
  };
}

/** A violation: a `ForestInteraction` whose `risk` is non-null (the only shape this component is ever given — see `violationsOf`). */
function mkViolation(over: Partial<ForestInteraction> = {}, riskOver: Partial<InteractionRisk> = {}): ForestInteraction {
  return {
    interaction_id: 'i1',
    trace_id: 't1',
    parent_interaction_id: null,
    caller_entity_id: 'e1',
    callee_entity_id: 'e2',
    summary: 'agent calls tool',
    legs: [],
    risk: mkRisk(riskOver),
    span_count: 1,
    anchor_count: 1,
    ...over,
  };
}

function mkRule(over: Partial<RuleListItem> = {}): RuleListItem {
  return {
    rule_id: 'DG-001',
    rule_name: 'No PII to external services',
    categories: ['privacy'],
    risk_level: 'critical',
    enforcement: 'block',
    explanation: 'PII must not leave the trust boundary.',
    event_type: 'tool_call',
    data_items: [],
    data_destinations: [{ data_destination_categories: ['external'], data_destination_trust_level: 'UNTRUSTED_EXTERNAL' }],
    allowed_actions: ['redact', 'block'],
    rule_sources: [],
    ...over,
  };
}

const ENTITIES: Entity[] = [
  { id: 'e1', kind: 'agent', natural_key: 'agent:(p,a)', display_name: 'agent-a', detected_from: 'span' },
  { id: 'e2', kind: 'tool', natural_key: 'tool:(p,svc)', display_name: 'search', detected_from: 'span' },
];

function renderPanel(
  violation: ForestInteraction,
  ruleIndex?: ReadonlyMap<string, RuleListItem>,
  entities?: readonly Entity[],
) {
  return renderWithProviders(
    <PolicyDecisionPanel violation={violation} ruleIndex={ruleIndex} entities={entities} />,
  );
}

describe('PolicyDecisionPanel', () => {
  it('renders a triggered rule id as a link to its catalog page, labelled with the catalog rule_name', () => {
    renderPanel(mkViolation(), new Map([['DG-001', mkRule()]]), ENTITIES);

    const link = screen.getByRole('link', { name: 'No PII to external services' });
    expect(link).toHaveAttribute('href', '/risk/rules/DG-001');
  });

  it('still links a rule id missing from the catalog index, labelled with the bare id', () => {
    // Catalog beyond one page, or a stale/renamed id — the NAVIGATION must
    // still work even when the label degrades (`useRuleCatalogIndex`'s
    // documented fallback for the same case).
    renderPanel(mkViolation(), new Map(), ENTITIES);

    const link = screen.getByRole('link', { name: 'DG-001' });
    expect(link).toHaveAttribute('href', '/risk/rules/DG-001');
  });

  it('still links every rule id when no catalog index was supplied at all', () => {
    renderPanel(mkViolation(), undefined, ENTITIES);

    expect(screen.getByRole('link', { name: 'DG-001' })).toHaveAttribute('href', '/risk/rules/DG-001');
  });

  it('renders "none" for classification_summary: null, without throwing', () => {
    renderPanel(mkViolation({}, { classification_summary: null }), new Map([['DG-001', mkRule()]]), ENTITIES);

    // Both the Regulatory tags and Classification rows fall back to "none".
    const nones = screen.getAllByText('none');
    expect(nones.length).toBeGreaterThanOrEqual(2);
  });

  it('renders "none" for a classification_summary shape it does not recognise, without throwing', () => {
    // Neither a classified verdict, `classification_pending`, nor `payload:
    // null` — an unanticipated shape must still degrade to "none" rather
    // than crash the panel.
    renderPanel(
      mkViolation({}, { classification_summary: { request: 'not-an-object' } }),
      new Map([['DG-001', mkRule()]]),
      ENTITIES,
    );

    expect(screen.getAllByText('none').length).toBeGreaterThanOrEqual(2);
  });

  it('extracts regulatory tags and sensitivity level from a classified leg', () => {
    renderPanel(
      mkViolation(
        {},
        {
          classification_summary: {
            request: { sensitivity_level: 'RESTRICTED', regulatory_tags: ['PII', 'GDPR'] },
          },
        },
      ),
      new Map([['DG-001', mkRule()]]),
      ENTITIES,
    );

    expect(screen.getByText('PII, GDPR')).toBeInTheDocument();
    expect(screen.getByText('RESTRICTED')).toBeInTheDocument();
  });

  it('ignores a pending or payload-less leg rather than crashing or fabricating tags', () => {
    renderPanel(
      mkViolation(
        {},
        {
          classification_summary: {
            request: { classification_pending: true },
            response: { payload: null },
          },
        },
      ),
      new Map([['DG-001', mkRule()]]),
      ENTITIES,
    );

    expect(screen.getAllByText('none').length).toBeGreaterThanOrEqual(2);
  });

  it('renders a "none" enforcement chip when enforcement_type is null', () => {
    // `classification_summary` also defaults to `null` in `mkRisk`, so
    // "none" appears more than once (Regulatory tags, Classification) — this
    // asserts specifically on the Enforcement row's chip, not just that the
    // word "none" appears somewhere on the page.
    renderPanel(mkViolation({}, { enforcement_type: null }), new Map([['DG-001', mkRule()]]), ENTITIES);

    const term = screen.getByText('Enforcement');
    const row = term.closest('.pf-v5-c-description-list__group') as HTMLElement;
    expect(within(row).getByText('none')).toBeInTheDocument();
  });

  it("shows the triggered rule's explanation as the Reason", () => {
    renderPanel(mkViolation(), new Map([['DG-001', mkRule({ explanation: 'PII must not leave the trust boundary.' })]]), ENTITIES);

    expect(screen.getByText('PII must not leave the trust boundary.')).toBeInTheDocument();
  });

  it("shows the triggered rule's data destination categories and trust level", () => {
    renderPanel(mkViolation(), new Map([['DG-001', mkRule()]]), ENTITIES);

    expect(screen.getByText('external, UNTRUSTED_EXTERNAL')).toBeInTheDocument();
  });

  it("shows the triggered rule's allowed actions, deduplicated across rules", () => {
    renderPanel(
      mkViolation({ risk: mkRisk({ triggered_rule_ids: ['DG-001', 'DG-002'] }) }),
      new Map([
        ['DG-001', mkRule({ allowed_actions: ['redact', 'block'] })],
        ['DG-002', mkRule({ rule_id: 'DG-002', allowed_actions: ['block'] })],
      ]),
      ENTITIES,
    );

    expect(screen.getByText('redact, block')).toBeInTheDocument();
  });

  it("resolves the caller and callee's entity kinds from the entities list", () => {
    renderPanel(mkViolation(), new Map([['DG-001', mkRule()]]), ENTITIES);

    expect(screen.getByText('agent, tool')).toBeInTheDocument();
  });

  it('degrades an entity id the entities list does not resolve to "unknown" rather than throwing', () => {
    renderPanel(mkViolation(), new Map([['DG-001', mkRule()]]), []);

    expect(screen.getByText('unknown')).toBeInTheDocument();
  });

  it('degrades every entity type to "unknown" when no entities list is supplied at all', () => {
    renderPanel(mkViolation(), new Map([['DG-001', mkRule()]]), undefined);

    expect(screen.getByText('unknown')).toBeInTheDocument();
  });

  it("renders the violation's summary as the Interaction", () => {
    renderPanel(mkViolation({ summary: 'agent calls search with a customer record' }), new Map([['DG-001', mkRule()]]), ENTITIES);

    expect(screen.getByText('agent calls search with a customer record')).toBeInTheDocument();
  });

  // Issue #222 terminology: the card is one "Policy event" and its first row
  // is the "Interaction" it describes. Both term labels were previously
  // uncovered (only the row's *value* was asserted), so these pin the labels
  // themselves and assert the retired wording is gone — "Action" is checked
  // with an exact-match matcher so it does not accidentally pass on the
  // unrelated "Allowed actions" row, which keeps its name.
  it('titles the card "Policy event" and labels the first row "Interaction"', () => {
    renderPanel(mkViolation(), new Map([['DG-001', mkRule()]]), ENTITIES);

    expect(screen.getByText('Policy event')).toBeInTheDocument();
    expect(screen.getByText('Interaction')).toBeInTheDocument();
    expect(screen.queryByText('Policy decision')).not.toBeInTheDocument();
    expect(screen.queryByText('Action', { exact: true })).not.toBeInTheDocument();
  });

  it('keeps the unrelated "Allowed actions" row, which issue #222 does not rename', () => {
    renderPanel(mkViolation(), new Map([['DG-001', mkRule()]]), ENTITIES);

    expect(screen.getByText('Allowed actions')).toBeInTheDocument();
  });

  it("renders the risk level as a RiskBadge", () => {
    renderPanel(mkViolation({}, { risk_level: 'critical' }), new Map([['DG-001', mkRule()]]), ENTITIES);

    expect(screen.getByText('critical')).toBeInTheDocument();
  });

  // Label lineage: the live partner policy event (trace e06cd67d…,
  // notify-partner → api.travel-partner.example:8000) fired DG-001 (PII) and
  // DG-004 (RESTRICTED) while the classifier read this payload as PI /
  // INTERNAL; the PII and RESTRICTED came in with the text, in `inherited`.
  describe('Carried in row', () => {
    const INHERITED = { count: 5, kinds: ['whole'], max_level: 'RESTRICTED', recombined: false, regulatory_tags: ['PI', 'PII'] };
    const PARTNER = {
      request: { sensitivity_level: 'INTERNAL', regulatory_tags: ['PI'], finding_count: 3, inherited: INHERITED },
      response: { sensitivity_level: 'INTERNAL', regulatory_tags: ['PI'], finding_count: 3, inherited: INHERITED },
    };

    function row(term: string) {
      return screen.getByText(term, { exact: true }).closest('.pf-v5-c-description-list__group') as HTMLElement;
    }

    it('shows the highest carried level, the carried tags and "used in this decision", beside the classifier rows left as they were', () => {
      renderPanel(
        mkViolation({}, { triggered_rule_ids: ['DG-001', 'DG-004'], classification_summary: PARTNER }),
        new Map([['DG-001', mkRule()]]),
        ENTITIES,
      );

      expect(row('Regulatory tags')).toHaveTextContent(/^Regulatory tagsPI$/);
      expect(row('Classification')).toHaveTextContent(/^ClassificationINTERNAL$/);
      const carried = row('Carried in');
      expect(within(carried).getByText('RESTRICTED')).toBeInTheDocument();
      expect(carried).toHaveTextContent('RESTRICTED PI, PII — used in this decision');
      // The edge count never reaches the screen.
      expect(carried).not.toHaveTextContent('5');
    });

    it('has no Carried in row without inherited, and never says "none carried"', () => {
      renderPanel(
        mkViolation({}, { classification_summary: { request: { sensitivity_level: 'INTERNAL', regulatory_tags: ['PI'] } } }),
        new Map([['DG-001', mkRule()]]),
        ENTITIES,
      );

      expect(screen.queryByText('Carried in')).not.toBeInTheDocument();
      expect(screen.queryByText(/used in this decision/)).not.toBeInTheDocument();
    });

    it('shows what a pending leg carried in, beside "none" from the classifier', () => {
      renderPanel(
        mkViolation({}, { classification_summary: { request: { classification_pending: true, inherited: INHERITED } } }),
        new Map([['DG-001', mkRule()]]),
        ENTITIES,
      );

      expect(row('Regulatory tags')).toHaveTextContent(/^Regulatory tagsnone$/);
      expect(row('Carried in')).toHaveTextContent('RESTRICTED PI, PII — used in this decision');
    });

    it('prints no "none" for a carried level without tags', () => {
      renderPanel(
        mkViolation({}, { classification_summary: { request: { sensitivity_level: 'INTERNAL', regulatory_tags: ['PI'], inherited: { max_level: 'RESTRICTED', regulatory_tags: [] } } } }),
        new Map([['DG-001', mkRule()]]),
        ENTITIES,
      );

      // The row's text, before its toggle to the values panel.
      expect(row('Carried in')).toHaveTextContent(/^Carried inRESTRICTED — used in this decisionShow the values/);
    });
  });
});

/**
 * The values on the decided payload and where each came from, opened
 * from the Carried in row. The labels read is the trace page's (`useLegLabels`),
 * stubbed here with a live booking trace's partner request (trace 98134844…): the PID
 * born at create-booking's response, carried through notify-partner's request.
 */
describe('PolicyDecisionPanel — values on this payload', () => {
  const INHERITED = { count: 5, kinds: ['whole'], max_level: 'RESTRICTED', recombined: false, regulatory_tags: ['PI', 'PII'] };
  const SUMMARY = { request: { sensitivity_level: 'INTERNAL', regulatory_tags: ['PI'], finding_count: 3, inherited: INHERITED } };
  const hop = (seq: number, role: 'origin' | 'hop' | 'here', iid: string, caller: string, callee: string, trace = 't1') => ({
    interaction_id: iid, leg_type: role === 'origin' ? 'response' : 'request', seq, trace_id: trace, caller, callee,
    text: 'BK-83CE5F', start: 16, end: 25, role,
    excerpt: role === 'origin' ? { text: '{"id": "BK-83CE5F", "guest_name": "<name>"}', start: 8, end: 17 } : null,
  });
  const LABELS = {
    lineage_status: 'complete',
    legs: [{
      leg_type: 'request', payload_hash: 'h1', seq: 83053,
      found: { sensitivity_level: 'INTERNAL', regulatory_tags: ['PI'] },
      carried: { max_level: 'RESTRICTED', regulatory_tags: ['PI', 'PII'], origin_count: 1 },
      labels: [{
        text: 'BK-83CE5F', start: 16, end: 25, entity_type: 'PID', sensitivity_level: 'RESTRICTED',
        regulatory_tags: ['PII'], found_here: false,
        carried_in: [{
          origin_interaction_id: 'i-cb', origin_leg_type: 'response', origin_seq: 80565,
          caller: 'booking-agent', callee: 'create-booking', unit_text: 'BK-83CE5F', unit_value: 'BK-83CE5F',
          match_kind: 'whole', origin_trace_id: null,
          chains: [[hop(80565, 'origin', 'i-cb', 'booking-agent', 'create-booking'),
            hop(82049, 'hop', 'i-np', 'booking-agent', 'notify-partner'),
            hop(83053, 'here', 'i1', 'notify-partner', 'partner')]],
        }],
      }],
    }],
  };
  const labelsCalls = () =>
    (fetch as ReturnType<typeof vi.fn>).mock.calls.filter((c) => String(c[0]).includes('/labels'));

  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (String(url).includes('/labels')) return { ok: true, status: 200, json: async () => LABELS };
      throw new Error(`unexpected fetch: ${url}`);
    }));
  });
  afterEach(() => vi.unstubAllGlobals());

  const RULES = new Map([
    ['DG-001', mkRule({ rule_id: 'DG-001', data_items: [{ regulatory_tags: ['PII'] }] })],
    ['DG-004', mkRule({ rule_id: 'DG-004', rule_name: 'restricted_external_sharing', data_items: [{ classification_level: 'RESTRICTED' }] })],
  ]);

  it('opens from the Carried in row, reads the labels only then, and shows the rules\' criteria above the values', async () => {
    renderWithProviders(
      <PolicyDecisionPanel
        violation={mkViolation({}, { triggered_rule_ids: ['DG-001', 'DG-004'], classification_summary: SUMMARY })}
        ruleIndex={RULES}
        entities={ENTITIES}
      />,
      { route: '/risk/traces/t1?violation=2' },
    );
    expect(screen.queryByTestId('values-on-payload')).toBeNull();
    expect(labelsCalls()).toHaveLength(0);

    await userEvent.click(screen.getByRole('button', { name: 'Show the values and where each came from' }));
    const panel = await screen.findByTestId('values-on-payload');
    expect(labelsCalls()).toHaveLength(1);
    expect(labelsCalls()[0][0]).toBe('/api/traces/t1/interactions/i1/labels');
    expect(within(panel).getByRole('heading', { name: 'Values on this payload and where each came from' })).toBeInTheDocument();

    // The fired rules and what each looks for, from the catalog.
    const rules = within(panel).getByRole('grid', { name: 'Fired rules and their criteria' });
    const ruleRows = within(rules).getAllByRole('row').slice(1).map((r) => r.textContent);
    expect(ruleRows).toEqual(['DG-001tag PIIexternal, UNTRUSTED_EXTERNAL', 'DG-004level RESTRICTEDexternal, UNTRUSTED_EXTERNAL']);

    // The leg, its lineage table: born at, the previous hop, the chains on demand.
    await waitFor(() => expect(within(panel).getByRole('heading', { name: 'Request #83053' })).toBeInTheDocument());
    const [row] = within(panel).getAllByTestId('lineage-value');
    expect(row).toHaveTextContent('BK-83CE5F');
    expect(within(row).getByRole('link', { name: /#80565/ })).toHaveAttribute('href', '/risk/traces/t1?violation=2&interaction=i-cb');
    // A hop selects its interaction on this page, the other params kept.
    expect(within(row).getByRole('link', { name: /#82049/ })).toHaveAttribute('href', '/risk/traces/t1?violation=2&interaction=i-np');
    await userEvent.click(within(row).getByRole('button', { name: 'Show chain' }));
    const lineage = within(panel).getByTestId('label-lineage');
    expect(lineage).toHaveTextContent('born #80565 booking-agent → create-booking response');
    expect(within(lineage).getByTestId('origin-excerpt').querySelector('mark')).toHaveTextContent('BK-83CE5F');
    // Never a claim of cause.
    expect(panel).not.toHaveTextContent(/why|caused|because/i);

    await userEvent.click(screen.getByRole('button', { name: 'Hide the values' }));
    expect(screen.queryByTestId('values-on-payload')).toBeNull();
  });

  it('offers to track the values whose labels the rules name, and only where a page provides tracking', async () => {
    const DATE = {
      text: '2033-03-29', start: 38, end: 48, entity_type: 'DATE', sensitivity_level: 'INTERNAL', regulatory_tags: ['PI'],
      found_here: true,
      carried_in: [{ ...LABELS.legs[0].labels[0].carried_in[0], origin_interaction_id: 'i-user', origin_seq: 78059,
        chains: [[hop(78059, 'origin', 'i-user', 'client', 'advisor'), hop(83053, 'here', 'i1', 'notify-partner', 'partner')]] }],
    };
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, status: 200, json: async () => ({
      ...LABELS, legs: [{ ...LABELS.legs[0], labels: [...LABELS.legs[0].labels, DATE] }],
    }) })));
    const replace = vi.fn();
    const violation = mkViolation({}, { triggered_rule_ids: ['DG-001', 'DG-004'], classification_summary: SUMMARY });
    const { unmount } = renderWithProviders(<PolicyDecisionPanel violation={violation} ruleIndex={RULES} entities={ENTITIES} />);
    await userEvent.click(screen.getByRole('button', { name: /Show the values/ }));
    await screen.findByRole('heading', { name: 'Request #83053' });
    expect(screen.queryByRole('button', { name: /whose labels the rules name/ })).toBeNull();
    expect(screen.queryByRole('checkbox')).toBeNull();
    unmount();

    renderWithProviders(
      <LabelTrackContext.Provider value={{ available: true, tracked: new Map(), set: vi.fn(), replace }}>
        <PolicyDecisionPanel violation={violation} ruleIndex={RULES} entities={ENTITIES} />
      </LabelTrackContext.Provider>,
    );
    await userEvent.click(screen.getByRole('button', { name: /Show the values/ }));
    const button = await screen.findByRole('button', { name: 'Track on the graph the 1 value whose labels the rules name' });
    expect(screen.getAllByRole('checkbox')).toHaveLength(2);
    await userEvent.click(button);
    // The PID (PII, RESTRICTED) is named by DG-001 and DG-004; the date (PI, INTERNAL) by neither.
    expect(replace.mock.calls[0][0].map((v: { text: string; entityType: string }) => `${v.text} ${v.entityType}`)).toEqual(['BK-83CE5F PID']);
  });

  it('has no toggle when nothing was carried in', () => {
    renderWithProviders(
      <PolicyDecisionPanel
        violation={mkViolation({}, { classification_summary: { request: { sensitivity_level: 'INTERNAL', regulatory_tags: ['PI'] } } })}
        ruleIndex={RULES}
        entities={ENTITIES}
      />,
    );
    expect(screen.queryByRole('button', { name: /Show the values/ })).toBeNull();
    expect(labelsCalls()).toHaveLength(0);
  });

  it('says when the read failed, and never shows an empty chain for a value without one', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 500, json: async () => ({ error: 'down' }) })));
    renderWithProviders(
      <PolicyDecisionPanel
        violation={mkViolation({}, { triggered_rule_ids: ['DG-001'], classification_summary: SUMMARY })}
        ruleIndex={RULES}
        entities={ENTITIES}
      />,
    );
    await userEvent.click(screen.getByRole('button', { name: /Show the values/ }));
    expect(await screen.findByText('The labels read failed.')).toBeInTheDocument();
    expect(screen.queryByTestId('label-lineage')).toBeNull();
  });
});
