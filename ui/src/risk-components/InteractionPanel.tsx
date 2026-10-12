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
import { classifiedLegEntries, legLevel, legTags } from '../lib/classificationSummary';
import type { ClassifiedLegSummary } from '../lib/classificationSummary';
import type { ForestInteraction } from '../risk-api/types';

/**
 * `LEVEL · TAG, TAG` for a classified leg (`_verdict_summary` always emits
 * both, so "none" on either side is defensive, not expected). The two other
 * per-leg shapes the wire carries, which `classifiedLegEntries` lets through
 * with neither a level nor tags (see its docstring), are told apart because
 * the reader acts differently on them: "pending" means wait and reload,
 * "no payload" means there is nothing to classify.
 */
function legText(leg: ClassifiedLegSummary): string {
  const level = legLevel(leg);
  const tags = legTags(leg);
  if (level != null || tags.length > 0) return `${level ?? 'none'} · ${joinOrNone(tags)}`;
  return 'classification_pending' in leg ? 'pending' : 'no payload';
}

/**
 * The panel for the interaction a reader clicked on the execution-flow
 * graph (issue #295) — ANY interaction, not only a policy event. It shows
 * what the edge's own label abbreviates: the classification verdict per
 * leg (`risk.classification_summary`, narrowed by `lib/classificationSummary`),
 * the risk level and enforcement when `risk` is present, and the way to the
 * payload — the lineage view's `/traces/{id}/flow?iid=` deep link, which
 * already opens the interaction's legs and payloads. Read-only.
 *
 * Same layout as `PolicyDecisionPanel` (one `Card`, a horizontal
 * `DescriptionList`), deliberately not the same rows: a policy event's
 * rules, reason and destination stay in that panel, which is shown right
 * below this one whenever the selected interaction triggered a rule.
 *
 * `risk == null` is NOT an error — eventual consistency per
 * `ForestInteractionView`'s docstring — so the risk row says so instead of
 * hiding; a leg still being classified reads "pending", one with nothing to
 * classify "no payload", and an interaction with no leg at all "not classified".
 */
export function InteractionPanel({ interaction }: { interaction: ForestInteraction }) {
  const risk = interaction.risk;
  const legs = classifiedLegEntries(risk?.classification_summary ?? null);

  return (
    <Card data-testid="interaction-panel">
      <CardTitle>Interaction</CardTitle>
      <CardBody>
        <DescriptionList isHorizontal>
          <DescriptionListGroup>
            <DescriptionListTerm>Interaction</DescriptionListTerm>
            <DescriptionListDescription>{interaction.summary ?? 'none'}</DescriptionListDescription>
          </DescriptionListGroup>
          {legs.length === 0 ? (
            <DescriptionListGroup>
              <DescriptionListTerm>Classification</DescriptionListTerm>
              <DescriptionListDescription>not classified</DescriptionListDescription>
            </DescriptionListGroup>
          ) : (
            legs.map(([name, leg]) => (
              <DescriptionListGroup key={name}>
                <DescriptionListTerm>{name === 'request' ? 'Request leg' : 'Response leg'}</DescriptionListTerm>
                <DescriptionListDescription>{legText(leg)}</DescriptionListDescription>
              </DescriptionListGroup>
            ))
          )}
          <DescriptionListGroup>
            <DescriptionListTerm>Risk</DescriptionListTerm>
            <DescriptionListDescription>
              {risk == null ? (
                'not yet computed'
              ) : (
                <>
                  <RiskBadge level={risk.risk_level} />{' '}
                  {/* `'none'` for a null `enforcement_type`, as in `PolicyDecisionPanel`. */}
                  <EnforcementChip type={risk.enforcement_type ?? 'none'} />
                </>
              )}
            </DescriptionListDescription>
          </DescriptionListGroup>
          <DescriptionListGroup>
            <DescriptionListTerm>Payload</DescriptionListTerm>
            <DescriptionListDescription>
              <Link to={`/traces/${interaction.trace_id}/flow?iid=${encodeURIComponent(interaction.interaction_id)}`}>
                Open in trace view
              </Link>
            </DescriptionListDescription>
          </DescriptionListGroup>
        </DescriptionList>
      </CardBody>
    </Card>
  );
}

export default InteractionPanel;
