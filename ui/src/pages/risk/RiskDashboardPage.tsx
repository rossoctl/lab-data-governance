import { useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  ToggleGroup,
  ToggleGroupItem,
  Grid,
  GridItem,
  Title,
  Stack,
  StackItem,
} from '@patternfly/react-core';
import { RiskViewShell } from '../../risk-components/RiskViewShell';
import {
  useRiskSummary,
  useRiskDistribution,
  useRiskByCategory,
  useTopRules,
  useRiskTracesInfinite,
  RISK_PAGE_SIZE,
} from '../../risk-api/hooks';
import { RISK_WINDOW_KEYS, DEFAULT_RISK_WINDOW, parseRiskWindow } from '../../lib/riskWindow';
import { SummaryTiles } from './dashboard/SummaryTiles';
import { RiskDistributionBar } from './dashboard/RiskDistributionBar';
import { RiskByCategoryCard } from './dashboard/RiskByCategoryCard';
import { TopRulesCard } from './dashboard/TopRulesCard';
import { AlertsCard } from './dashboard/AlertsCard';

const WINDOW_LABEL: Record<string, string> = {
  '24h': 'Last 24 hours',
  '7d': 'Last 7 days',
  '30d': 'Last 30 days',
};

/**
 * FR-DAS-050a-c Risk dashboard (issue #169, parent #166) — replaces the
 * #165 stub. Window state lives in the URL via `?window=`, following
 * `RecentTracesPage`'s idiom: the default (`24h`, matching the server's own
 * `http.DEFAULT_WINDOW`) is written by DELETING the param, never by setting
 * it explicitly, so the canonical URL carries no `?window=24h`.
 *
 * One `windowKey` derived from the URL is passed to every hook below, so all
 * five cards' query keys re-key together on a window change — there is no
 * per-card window state that could drift out of sync with another card.
 * Window options are exactly the three fixed windows the server supports
 * (`http.WINDOW_DELTAS`). A 1-hour and an "all time" window were requested
 * as follow-up, but the server has no matching entry, so adding them here
 * would either 400 or require faking a range client-side; that remains a
 * backend follow-up rather than something shown (even disabled) in this UI.
 *
 * The two panes are purely a layout grouping (issue #169 follow-up), equal
 * width: "Risk Analysis" (distribution, by-category) on the left, "Policies
 * and Alerts" (top rules, then the alerts/incidents table) on the right.
 * Neither pane title is itself a query key or URL state — just a heading
 * over its cards.
 *
 * The window selector is passed as the shell's `toolbar`, not rendered among
 * `children` (issue #215). The shell's loading, error and empty branches each
 * replace `children` wholesale, so a selector living there vanished in exactly
 * the states where it is most needed: the default 24h window on a quiet system
 * loads empty, and with no visible selector there was no way to widen it short
 * of hand-editing the URL. The toggle depends only on `windowKey` — URL state,
 * never fetched data — so it renders correctly whether or not any query has
 * resolved. The cards stay in `children`, so an empty window still reads as
 * empty rather than as a wall of zeroes.
 *
 * Each group's expand/collapse state (inside `AlertsCard`) is deliberately
 * NOT reflected in the URL. The URL-as-truth convention exists to restore
 * *view identity* on reload/bookmark/back — which window, which trace, which
 * tab — not every transient UI toggle. Putting N group ids in the query
 * string would churn browser history on every chevron click and produce
 * unshareably long URLs for no benefit: a collapsed disclosure is not
 * something a bookmark needs to remember.
 */
export function RiskDashboardPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const windowKey = parseRiskWindow(searchParams.get('window'));

  const setWindowKey = useCallback(
    (key: string) => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (key === DEFAULT_RISK_WINDOW) next.delete('window');
          else next.set('window', key);
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );

  const summary = useRiskSummary(windowKey);
  const distribution = useRiskDistribution(windowKey);
  const byCategory = useRiskByCategory(windowKey);
  const topRules = useTopRules(windowKey, RISK_PAGE_SIZE);
  const traces = useRiskTracesInfinite(windowKey);

  const isLoading = summary.isLoading;
  const isError = summary.isError;
  const isEmpty = summary.data != null && summary.data.evaluated_interactions.total === 0;

  const traceItems = traces.data ? traces.data.pages.flatMap((page) => page.items) : [];

  return (
    <RiskViewShell
      title="Risk dashboard"
      isLoading={isLoading}
      isError={isError}
      isEmpty={isEmpty}
      emptyBody="No monitored interactions in this window."
      toolbar={
        <ToggleGroup aria-label="Time window" className="pf-v5-u-mb-md">
          {RISK_WINDOW_KEYS.map((key) => (
            <ToggleGroupItem
              key={key}
              text={WINDOW_LABEL[key]}
              buttonId={key}
              isSelected={windowKey === key}
              onChange={() => setWindowKey(key)}
            />
          ))}
        </ToggleGroup>
      }
    >
      {summary.data && <SummaryTiles summary={summary.data} />}

      <Grid hasGutter className="pf-v5-u-mt-md">
        <GridItem span={12} lg={6}>
          <Title headingLevel="h3" size="lg" className="pf-v5-u-mb-md" style={{ marginTop: '3rem' }}>
            Risk Analysis
          </Title>
          <Stack hasGutter>
            <StackItem>
              {distribution.data && (
                <RiskDistributionBar distribution={distribution.data.distribution} />
              )}
            </StackItem>
            <StackItem>
              <RiskByCategoryCard items={byCategory.data?.items ?? []} />
            </StackItem>
          </Stack>
        </GridItem>
        <GridItem span={12} lg={6}>
          <Title headingLevel="h3" size="lg" className="pf-v5-u-mb-md" style={{ marginTop: '3rem' }}>
            Policies and Alerts
          </Title>
          <Stack hasGutter>
            <StackItem>
              <TopRulesCard items={topRules.data?.items ?? []} />
            </StackItem>
            <StackItem>
              <AlertsCard
                items={traceItems}
                hasNextPage={traces.hasNextPage ?? false}
                isFetchingNextPage={traces.isFetchingNextPage}
                onNextPage={() => traces.fetchNextPage()}
              />
            </StackItem>
          </Stack>
        </GridItem>
      </Grid>
    </RiskViewShell>
  );
}
