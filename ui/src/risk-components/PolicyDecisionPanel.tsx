import { Link } from 'react-router-dom';
import {
  Card,
  CardBody,
  CardTitle,
  DescriptionList,
  DescriptionListGroup,
  DescriptionListTerm,
  DescriptionListDescription,
} from '@patternfly/react-core';
import { RiskBadge } from './RiskBadge';
import { EnforcementChip } from './EnforcementChip';
import { joinOrNone } from '../lib/joinOrNone';
import type { Entity } from '../lib/flow';
import type { ForestInteraction, RuleListItem } from '../risk-api/types';

/**
 * One classified LEG's shape inside `InteractionRisk.classification_summary`
 * when a payload was actually classified — `data_governance/risk/engine/
 * utils.py`'s `_verdict_summary`. The other two possible per-leg shapes are
 * `{classification_pending: true}` (still running) and `{payload: null}`
 * (nothing to classify); neither carries tags or a sensitivity level, so
 * both fall through this narrowing to the "none" default below.
 */
interface ClassifiedLegSummary {
  sensitivity_level?: unknown;
  regulatory_tags?: unknown;
}

/**
 * `classification_summary` is typed `Record<string, unknown> | null` on the
 * wire (`risk-api/types.ts`'s `InteractionRisk`) because the API guarantees
 * only "a JSON object or null" — the per-leg value can be a classified
 * verdict, `{classification_pending: true}`, or `{payload: null}` (see
 * `utils.py`'s `classification_summary()`), and a leg absent from the object
 * entirely is a fourth, silent case. Rather than typing an optimistic
 * interface that a pending/no-payload/absent leg would violate at runtime,
 * this narrows defensively and falls back to "none"/`[]` for anything that
 * doesn't look like a classified verdict — never throws.
 */
function classifiedLegs(summary: Record<string, unknown> | null): ClassifiedLegSummary[] {
  if (summary == null || typeof summary !== 'object') return [];
  return (['request', 'response'] as const)
    .map((leg) => summary[leg])
    .filter((v): v is ClassifiedLegSummary => typeof v === 'object' && v !== null);
}

/** Every `regulatory_tags` string across whichever legs were actually classified, deduplicated. */
function regulatoryTagsOf(summary: Record<string, unknown> | null): string[] {
  const tags = new Set<string>();
  for (const leg of classifiedLegs(summary)) {
    if (Array.isArray(leg.regulatory_tags)) {
      for (const tag of leg.regulatory_tags) if (typeof tag === 'string') tags.add(tag);
    }
  }
  return [...tags];
}

/** Every `sensitivity_level` string across whichever legs were actually classified, deduplicated. */
function classificationLevelsOf(summary: Record<string, unknown> | null): string[] {
  const levels = new Set<string>();
  for (const leg of classifiedLegs(summary)) {
    if (typeof leg.sensitivity_level === 'string') levels.add(leg.sensitivity_level);
  }
  return [...levels];
}

/** The rule ids resolved through the catalog index, each rendered as a link. */
function RuleLinks({ ruleIds, ruleIndex }: { ruleIds: readonly string[]; ruleIndex: ReadonlyMap<string, RuleListItem> | undefined }) {
  if (ruleIds.length === 0) return <>none</>;
  return (
    <>
      {ruleIds.map((id, i) => (
        <span key={id}>
          {i > 0 && ', '}
          {/* A rule id missing from the index (catalog beyond one page, or a
              stale/renamed id) still LINKS — the navigation always works —
              just labelled with the bare id instead of its catalog name.
              See `useRuleCatalogIndex`'s documented degrade. */}
          <Link to={`/risk/rules/${id}`}>{ruleIndex?.get(id)?.rule_name ?? id}</Link>
        </span>
      ))}
    </>
  );
}

/** This violation's triggered rules, resolved through the catalog index — `[]` for an id the index doesn't cover. */
function triggeredRules(
  ruleIds: readonly string[],
  ruleIndex: ReadonlyMap<string, RuleListItem> | undefined,
): RuleListItem[] {
  return ruleIds.map((id) => ruleIndex?.get(id)).filter((r): r is RuleListItem => r !== undefined);
}

/** `entity_id -> kind`, resolved for whichever of the violation's caller/callee `entities` actually covers. */
function entityKindsOf(violation: ForestInteraction, entities: readonly Entity[] | undefined): string[] {
  const byId = new Map((entities ?? []).map((e) => [e.id, e.kind]));
  const kinds = new Set<string>();
  for (const id of [violation.caller_entity_id, violation.callee_entity_id]) {
    if (id != null) kinds.add(byId.get(id) ?? 'unknown');
  }
  return [...kinds];
}

/**
 * The policy-decision panel for one violation (issue #170's Alert Execution
 * view): a `ForestInteraction` whose `risk.triggered_rule_ids` is non-empty
 * (see `lib/riskForestAdapter.ts`'s `violationsOf`). Read-only, no mutation —
 * this is a step-through disclosure of a decision the policy engine already
 * made, not a form.
 *
 * `violation.risk` is guaranteed non-null by the caller (only a
 * `violationsOf` result reaches this component), so it is read directly
 * rather than re-guarded with an empty/loading branch here.
 *
 * `entities` is additional to the plan's original `{violation, ruleIndex}`
 * signature: the "Entity types" row needs the caller/callee `kind`, which —
 * like every other participant label in this view — is not on the risk
 * record at all (`riskForestAdapter.ts`'s "NO ENTITY METADATA" gap) and
 * comes from the same `useEntities`-backed entity list the diagrams already
 * consume. Optional because a degraded (or still-loading) entities read must
 * not block the panel — `entityKindsOf` falls back to `'unknown'` per id,
 * the same convention `toFlowEntities` uses for the diagrams.
 */
export function PolicyDecisionPanel({
  violation,
  ruleIndex,
  entities,
}: {
  violation: ForestInteraction;
  ruleIndex: ReadonlyMap<string, RuleListItem> | undefined;
  entities?: readonly Entity[];
}) {
  const risk = violation.risk;
  if (risk == null) return null; // See doc comment: callers only pass a `violationsOf` result.

  const rules = triggeredRules(risk.triggered_rule_ids, ruleIndex);
  const reasons = rules.map((r) => r.explanation).filter((e): e is string => !!e);
  const destinations = rules.flatMap((r) => r.data_destinations);
  const destinationLabels = destinations.flatMap((d) => [
    ...(d.data_destination_categories ?? []),
    ...(d.data_destination_trust_level ? [d.data_destination_trust_level] : []),
  ]);
  const allowedActions = [...new Set(rules.flatMap((r) => r.allowed_actions))];

  return (
    <Card data-testid="policy-decision-panel">
      <CardTitle>Policy decision</CardTitle>
      <CardBody>
        <DescriptionList isHorizontal>
          <DescriptionListGroup>
            <DescriptionListTerm>Action</DescriptionListTerm>
            <DescriptionListDescription>{violation.summary ?? 'none'}</DescriptionListDescription>
          </DescriptionListGroup>
          <DescriptionListGroup>
            <DescriptionListTerm>Risk level</DescriptionListTerm>
            <DescriptionListDescription>
              <RiskBadge level={risk.risk_level} />
            </DescriptionListDescription>
          </DescriptionListGroup>
          <DescriptionListGroup>
            <DescriptionListTerm>Enforcement</DescriptionListTerm>
            <DescriptionListDescription>
              {/* `'none'` when `enforcement_type` is null, per
                  `RiskRuleDetailPage`'s existing fallback for the same field
                  on a rule — one convention, two places it applies. */}
              <EnforcementChip type={risk.enforcement_type ?? 'none'} />
            </DescriptionListDescription>
          </DescriptionListGroup>
          <DescriptionListGroup>
            <DescriptionListTerm>Rules</DescriptionListTerm>
            <DescriptionListDescription>
              <RuleLinks ruleIds={risk.triggered_rule_ids} ruleIndex={ruleIndex} />
            </DescriptionListDescription>
          </DescriptionListGroup>
          <DescriptionListGroup>
            <DescriptionListTerm>Reason</DescriptionListTerm>
            <DescriptionListDescription>{joinOrNone(reasons, ' ')}</DescriptionListDescription>
          </DescriptionListGroup>
          <DescriptionListGroup>
            <DescriptionListTerm>Destination</DescriptionListTerm>
            <DescriptionListDescription>{joinOrNone(destinationLabels)}</DescriptionListDescription>
          </DescriptionListGroup>
          <DescriptionListGroup>
            <DescriptionListTerm>Entity types</DescriptionListTerm>
            <DescriptionListDescription>{joinOrNone(entityKindsOf(violation, entities))}</DescriptionListDescription>
          </DescriptionListGroup>
          <DescriptionListGroup>
            <DescriptionListTerm>Regulatory tags</DescriptionListTerm>
            <DescriptionListDescription>{joinOrNone(regulatoryTagsOf(risk.classification_summary))}</DescriptionListDescription>
          </DescriptionListGroup>
          <DescriptionListGroup>
            <DescriptionListTerm>Classification</DescriptionListTerm>
            <DescriptionListDescription>{joinOrNone(classificationLevelsOf(risk.classification_summary))}</DescriptionListDescription>
          </DescriptionListGroup>
          <DescriptionListGroup>
            <DescriptionListTerm>Allowed actions</DescriptionListTerm>
            <DescriptionListDescription>{joinOrNone(allowedActions)}</DescriptionListDescription>
          </DescriptionListGroup>
        </DescriptionList>
      </CardBody>
    </Card>
  );
}

export default PolicyDecisionPanel;
