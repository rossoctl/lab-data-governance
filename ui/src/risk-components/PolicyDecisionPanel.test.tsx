import { describe, it, expect } from 'vitest';
import { screen, within } from '@testing-library/react';
import { renderWithProviders } from '../test/renderWithProviders';
import { PolicyDecisionPanel } from './PolicyDecisionPanel';
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
});
