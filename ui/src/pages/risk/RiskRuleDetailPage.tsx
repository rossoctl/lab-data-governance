import { Link, useParams } from 'react-router-dom';
import {
  PageSection,
  Title,
  Breadcrumb,
  BreadcrumbItem,
  Spinner,
  EmptyState,
  EmptyStateHeader,
  EmptyStateBody,
  DescriptionList,
  DescriptionListGroup,
  DescriptionListTerm,
  DescriptionListDescription,
  Grid,
  GridItem,
} from '@patternfly/react-core';
import { Table, Thead, Tbody, Tr, Th, Td } from '@patternfly/react-table';
import { RiskBadge } from '../../risk-components/RiskBadge';
import { EnforcementChip } from '../../risk-components/EnforcementChip';
import { useRule } from '../../risk-api/hooks';
import { RiskApiError } from '../../risk-api/client';
import type { RuleDataItem, RuleDataDestination } from '../../risk-api/types';

/** One condition-type/value row for the mockup's Conditions table, derived
 * from the structural match fields `catalog.py` actually returns (no
 * literal `conditions` array on the wire — see `risk-api/types.ts`). */
interface ConditionRow {
  type: string;
  value: string;
}

function describeDataItem(item: RuleDataItem): string[] {
  const parts: string[] = [];
  if (item.regulatory_tags?.length) parts.push(`regulatory tags: ${item.regulatory_tags.join(', ')}`);
  if (item.classification_level) parts.push(`classification level: ${item.classification_level}`);
  return parts.length > 0 ? parts : ['(unspecified)'];
}

function describeDataDestination(dest: RuleDataDestination): string[] {
  const parts: string[] = [];
  if (dest.data_destination_categories?.length) {
    parts.push(`categories: ${dest.data_destination_categories.join(', ')}`);
  }
  if (dest.data_destination_trust_level) parts.push(`trust level: ${dest.data_destination_trust_level}`);
  return parts.length > 0 ? parts : ['(unspecified)'];
}

function buildConditionRows(
  eventType: string | null,
  dataItems: RuleDataItem[],
  dataDestinations: RuleDataDestination[],
): ConditionRow[] {
  const rows: ConditionRow[] = [];
  if (eventType) rows.push({ type: 'Event type', value: eventType });
  for (const item of dataItems) {
    for (const value of describeDataItem(item)) rows.push({ type: 'Data item', value });
  }
  for (const dest of dataDestinations) {
    for (const value of describeDataDestination(dest)) rows.push({ type: 'Data destination', value });
  }
  return rows;
}

/**
 * Rule detail page (issue #171, parent #168) — replaces the #165 stub.
 * `GET /risk/rules/{rule_id}`; a 404 renders an explicit not-found state
 * (`RiskApiError.status === 404`), not a crash, per #113's documented 404
 * behavior and this issue's acceptance criteria.
 *
 * The mockup's Conditions table has no literal backing field — `catalog.py`
 * encodes match criteria structurally (`event_type`/`data_items`/
 * `data_destinations`, presence-as-predicate, see `risk-api/types.ts`'s
 * header) rather than a `conditions` array. `buildConditionRows` flattens
 * those three fields into condition-type/value rows here, in the UI, so the
 * table still matches the mockup's shape without the API inventing a field
 * it doesn't have.
 *
 * Breadcrumb follows `TraceDetailPage`'s two-node convention: crumb 1 links
 * back to the list, crumb 2 is the current item's full id.
 */
export function RiskRuleDetailPage() {
  const { ruleId } = useParams<{ ruleId: string }>();
  const rule = useRule(ruleId);

  const isNotFound = rule.isError && rule.error instanceof RiskApiError && rule.error.status === 404;

  return (
    <PageSection>
      <Breadcrumb>
        <BreadcrumbItem
          render={({ className }) => (
            <Link to="/risk/rules" className={className}>
              Risk rules
            </Link>
          )}
        />
        <BreadcrumbItem isActive className="dg-mono">
          {ruleId}
        </BreadcrumbItem>
      </Breadcrumb>

      <Title headingLevel="h2" size="xl" style={{ marginTop: '0.5rem' }}>
        Risk rule
      </Title>

      {rule.isLoading ? (
        <Spinner aria-label="Loading risk rule" />
      ) : isNotFound ? (
        <EmptyState>
          <EmptyStateHeader titleText="Rule not found" headingLevel="h4" />
          <EmptyStateBody>No rule with id {ruleId} exists in the catalog.</EmptyStateBody>
        </EmptyState>
      ) : rule.isError ? (
        <EmptyState>
          <EmptyStateHeader titleText="Failed to load" headingLevel="h4" />
          <EmptyStateBody>Could not load this rule. Try again later.</EmptyStateBody>
        </EmptyState>
      ) : rule.data ? (
        <>
          <Grid hasGutter className="pf-v5-u-mt-md">
            <GridItem md={6}>
              <Title headingLevel="h3" size="lg" className="pf-v5-u-mb-sm">
                Rule information
              </Title>
              <DescriptionList isHorizontal>
                <DescriptionListGroup>
                  <DescriptionListTerm>Rule</DescriptionListTerm>
                  <DescriptionListDescription>
                    {rule.data.rule_name ?? rule.data.rule_id}
                  </DescriptionListDescription>
                </DescriptionListGroup>
                <DescriptionListGroup>
                  <DescriptionListTerm>Rule ID</DescriptionListTerm>
                  <DescriptionListDescription className="dg-mono">
                    {rule.data.rule_id}
                  </DescriptionListDescription>
                </DescriptionListGroup>
                <DescriptionListGroup>
                  <DescriptionListTerm>Categories</DescriptionListTerm>
                  <DescriptionListDescription>
                    {rule.data.categories.length === 0 ? 'none' : rule.data.categories.join(', ')}
                  </DescriptionListDescription>
                </DescriptionListGroup>
                <DescriptionListGroup>
                  <DescriptionListTerm>Risk level</DescriptionListTerm>
                  <DescriptionListDescription>
                    <RiskBadge level={rule.data.risk_level ?? 'unknown'} />
                  </DescriptionListDescription>
                </DescriptionListGroup>
                <DescriptionListGroup>
                  <DescriptionListTerm>Enforcement suggestion</DescriptionListTerm>
                  <DescriptionListDescription>
                    <EnforcementChip type={rule.data.enforcement ?? 'none'} />
                  </DescriptionListDescription>
                </DescriptionListGroup>
              </DescriptionList>
            </GridItem>

            <GridItem md={6}>
              <Title headingLevel="h3" size="lg" className="pf-v5-u-mb-sm">
                Conditions
              </Title>
              {(() => {
                const rows = buildConditionRows(
                  rule.data.event_type,
                  rule.data.data_items,
                  rule.data.data_destinations,
                );
                return rows.length === 0 ? (
                  <p>none</p>
                ) : (
                  <Table aria-label="Conditions" variant="compact">
                    <Thead>
                      <Tr>
                        <Th>Condition type</Th>
                        <Th>Value</Th>
                      </Tr>
                    </Thead>
                    <Tbody>
                      {rows.map((row, i) => (
                        <Tr key={`${row.type}-${i}`}>
                          <Td dataLabel="Condition type">{row.type}</Td>
                          <Td dataLabel="Value">{row.value}</Td>
                        </Tr>
                      ))}
                    </Tbody>
                  </Table>
                );
              })()}
            </GridItem>
          </Grid>

          <Title headingLevel="h3" size="lg" className="pf-v5-u-mb-sm" style={{ marginTop: '6rem' }}>
            Explanation
          </Title>
          <p>{rule.data.explanation ?? 'No explanation provided.'}</p>

          <Title headingLevel="h3" size="lg" className="pf-v5-u-mb-sm" style={{ marginTop: '6rem' }}>
            Sources
          </Title>
          {rule.data.rule_sources.length === 0 ? (
            <p>none</p>
          ) : (
            <p>
              {rule.data.rule_sources
                .map(
                  (s) =>
                    `${s.document_name} (${s.version}), ${s.section} — ${s['article/clause']}`,
                )
                .join('; ')}
            </p>
          )}

          <Title headingLevel="h3" size="lg" className="pf-v5-u-mb-sm" style={{ marginTop: '6rem' }}>
            Allowed Actions
          </Title>
          <p>{rule.data.allowed_actions.length === 0 ? 'none' : rule.data.allowed_actions.join(', ')}</p>
        </>
      ) : null}
    </PageSection>
  );
}
