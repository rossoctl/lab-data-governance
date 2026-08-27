import { Grid, GridItem, Card, CardTitle, CardBody } from '@patternfly/react-core';
import type { RiskSummary, TileCounts } from '../../../risk-api/types';

interface SummaryTilesProps {
  summary: RiskSummary;
}

function Tile({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <GridItem span={12} md={6} lg={2}>
      <Card isCompact>
        <CardTitle>{title}</CardTitle>
        <CardBody>{children}</CardBody>
      </Card>
    </GridItem>
  );
}

function riskyLine(tile: TileCounts): string {
  return `${tile.risky} risky (${tile.risky_pct}%)`;
}

/**
 * FR-DAS-050a's five dashboard summary tiles (issue #169). `rules_fired`
 * has a distinct shape from the other four (`RulesFiredCounts`: total +
 * critical, no `risky`/`risky_pct`) — `aggregate.SummaryMetrics` keeps it
 * separate from `TileCounts` deliberately, so it's rendered with its own
 * line rather than forced into the same "risky (%)" phrasing.
 */
export function SummaryTiles({ summary }: SummaryTilesProps) {
  return (
    <Grid hasGutter>
      <Tile title="Agents">
        <div>{summary.agents.total} total</div>
        <div>{riskyLine(summary.agents)}</div>
      </Tile>
      <Tile title="Users">
        <div>{summary.users.total} total</div>
        <div>{riskyLine(summary.users)}</div>
      </Tile>
      <Tile title="Workflows">
        <div>{summary.workflows.total} total</div>
        <div>{riskyLine(summary.workflows)}</div>
      </Tile>
      <Tile title="Evaluated actions">
        <div>{summary.evaluated_interactions.total} total</div>
        <div>{riskyLine(summary.evaluated_interactions)}</div>
      </Tile>
      <Tile title="Rules fired">
        <div>{summary.rules_fired.total} total</div>
        <div>{summary.rules_fired.critical} critical</div>
      </Tile>
    </Grid>
  );
}
