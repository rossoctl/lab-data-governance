import { useCallback } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  Toolbar,
  ToolbarContent,
  ToolbarItem,
  FormSelect,
  FormSelectOption,
} from '@patternfly/react-core';
import { Table, Thead, Tbody, Tr, Th, Td } from '@patternfly/react-table';
import { RiskViewShell } from '../../risk-components/RiskViewShell';
import { RiskBadge } from '../../risk-components/RiskBadge';
import { EnforcementChip } from '../../risk-components/EnforcementChip';
import { CursorPagination } from '../../risk-components/CursorPagination';
import { useRulesInfinite, useRuleCategories } from '../../risk-api/hooks';
import { joinOrNone } from '../../lib/joinOrNone';

/**
 * Rules catalog table (issue #171, parent #168) — replaces the #165 stub.
 * `GET /risk/rules` with `category`/`risk_level` filters, both reflected in
 * the URL (`?category=`/`?risk_level=`) per the app's URL-as-source-of-truth
 * convention (`RiskDashboardPage`'s `?window=`, `RecentTracesPage`'s
 * `?window=`). Both filters are sent straight through to the server (#113's
 * `_rules_list_handler`) rather than fetched-then-filtered client-side, so
 * the rendered set always matches a direct `GET /risk/rules?category=...`
 * call (AC-DAS-030).
 *
 * The category/risk-level filters are passed as `RiskViewShell`'s `toolbar`
 * (issue #215), so they stay on screen in the loading, error and empty states
 * as well as the content one — an over-narrow filter has to be correctable in
 * place, and a failed load must not strand the user with no controls. This
 * replaces an earlier workaround that pinned `isEmpty={false}` and rendered an
 * inline empty state purely to keep the filters visible; the shell now owns the
 * empty branch again, so both risk views get their empty state from one place.
 * The user-visible message is unchanged ("No rules match the current filters."),
 * passed as `emptyBody`.
 *
 * The mockup's Source column is the rule's regulatory/framework citations
 * (`rule_sources`); a rule can carry more than one, so the cell joins every
 * `document_name` rather than picking one arbitrarily.
 */
export function RiskRulesPage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const category = searchParams.get('category') ?? '';
  const riskLevel = searchParams.get('risk_level') ?? '';

  const setFilter = useCallback(
    (key: 'category' | 'risk_level', value: string) => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (value === '') next.delete(key);
          else next.set(key, value);
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );

  const categories = useRuleCategories();
  const rules = useRulesInfinite({
    category: category || undefined,
    riskLevel: riskLevel || undefined,
  });

  const items = rules.data ? rules.data.pages.flatMap((page) => page.items) : [];
  const isLoading = rules.isLoading;
  const isError = rules.isError;
  const isEmpty = !isLoading && !isError && items.length === 0;

  return (
    <RiskViewShell
      title="Risk rules"
      isLoading={isLoading}
      isError={isError}
      isEmpty={isEmpty}
      emptyBody="No rules match the current filters."
      toolbar={
        <Toolbar>
          <ToolbarContent>
            <ToolbarItem>
              <FormSelect
                aria-label="Category"
                value={category}
                onChange={(_e, v) => setFilter('category', v)}
                style={{ width: 200 }}
              >
                <FormSelectOption value="" label="All categories" />
                {(categories.data?.items ?? []).map((c) => (
                  <FormSelectOption key={c.category} value={c.category} label={c.category} />
                ))}
              </FormSelect>
            </ToolbarItem>
            <ToolbarItem>
              <FormSelect
                aria-label="Risk level"
                value={riskLevel}
                onChange={(_e, v) => setFilter('risk_level', v)}
                style={{ width: 160 }}
              >
                <FormSelectOption value="" label="All risk levels" />
                <FormSelectOption value="critical" label="Critical" />
                <FormSelectOption value="high" label="High" />
                <FormSelectOption value="medium" label="Medium" />
                <FormSelectOption value="low" label="Low" />
                <FormSelectOption value="none" label="None" />
              </FormSelect>
            </ToolbarItem>
          </ToolbarContent>
        </Toolbar>
      }
    >
      <Table aria-label="Risk rules" variant="compact">
        <Thead>
          <Tr>
            <Th>Rule</Th>
            <Th>Categories</Th>
            <Th>Risk</Th>
            <Th>Enforcement</Th>
            <Th>Source</Th>
          </Tr>
        </Thead>
        <Tbody>
          {items.map((rule) => (
            <Tr
              key={rule.rule_id}
              isClickable
              onRowClick={() => navigate(`/risk/rules/${encodeURIComponent(rule.rule_id)}`)}
            >
              <Td dataLabel="Rule">
                {rule.rule_name ?? rule.rule_id}
                <div className="dg-mono pf-v5-u-font-size-sm pf-v5-u-color-200">
                  {rule.rule_id}
                </div>
              </Td>
              <Td dataLabel="Categories">{joinOrNone(rule.categories)}</Td>
              <Td dataLabel="Risk">
                <RiskBadge level={rule.risk_level ?? 'unknown'} />
              </Td>
              <Td dataLabel="Enforcement">
                <EnforcementChip type={rule.enforcement ?? 'none'} />
              </Td>
              <Td dataLabel="Source">
                {joinOrNone(rule.rule_sources.map((s) => s.document_name))}
              </Td>
            </Tr>
          ))}
        </Tbody>
      </Table>
      <CursorPagination
        hasNextPage={rules.hasNextPage ?? false}
        isFetchingNextPage={rules.isFetchingNextPage}
        onNextPage={() => rules.fetchNextPage()}
      />
    </RiskViewShell>
  );
}
