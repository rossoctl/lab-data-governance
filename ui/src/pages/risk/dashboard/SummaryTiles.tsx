import { Grid, GridItem, Card, CardTitle, CardBody } from '@patternfly/react-core';
import type { RiskSummary, TileCounts } from '../../../risk-api/types';

interface SummaryTilesProps {
  summary: RiskSummary;
}

function Tile({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <GridItem span={12} md={6} lg={3}>
      <Card isCompact>
        <CardTitle>{title}</CardTitle>
        <CardBody>{children}</CardBody>
      </Card>
    </GridItem>
  );
}

/** The bottom "risky"/"critical" line of a tile — always the danger colour, since it's a count worth drawing attention to regardless of tile. */
function DangerLine({ children }: { children: React.ReactNode }) {
  return <div style={{ color: 'var(--pf-v5-global--danger-color--100)' }}>{children}</div>;
}

function riskyLine(tile: TileCounts): string {
  return `${tile.risky} risky (${tile.risky_pct}%)`;
}

/**
 * FR-DAS-050a's dashboard summary tiles (issue #169). `rules_fired` has a
 * distinct shape from the other three (`RulesFiredCounts`: total + critical,
 * no `risky`/`risky_pct`) — `aggregate.SummaryMetrics` keeps it separate from
 * `TileCounts` deliberately, so it's rendered with its own line rather than
 * forced into the same "risky (%)" phrasing.
 *
 * The `Users` tile is deliberately omitted (issue #169 follow-up) — agents
 * and traces are the risk surfaces this dashboard's audience acts on;
 * per-user counts weren't judged worth a tile's worth of space here.
 *
 * Labels follow issue #222's decided vocabulary: "Monitored interactions"
 * (the summary-tile form of the shorter "Interactions" used in table
 * columns), "Rules triggered" (never "fired"), and "Traces" (never
 * "Workflows" — temporary until the backend supports workflows). The API
 * field names deliberately keep their server-side spelling
 * (`workflows`, `evaluated_interactions`, `rules_fired`): renaming a
 * rendered label is a UI change, renaming a response field is a breaking API
 * change, so the two are allowed to diverge here.
 */
export function SummaryTiles({ summary }: SummaryTilesProps) {
  return (
    <Grid hasGutter>
      <Tile title="Agents">
        <div>{summary.agents.total} total</div>
        <DangerLine>{riskyLine(summary.agents)}</DangerLine>
      </Tile>
      <Tile title="Traces">
        <div>{summary.workflows.total} total</div>
        <DangerLine>{riskyLine(summary.workflows)}</DangerLine>
      </Tile>
      <Tile title="Monitored interactions">
        <div>{summary.evaluated_interactions.total} total</div>
        <DangerLine>{riskyLine(summary.evaluated_interactions)}</DangerLine>
      </Tile>
      <Tile title="Rules triggered">
        <div>{summary.rules_fired.total} total</div>
        <DangerLine>{summary.rules_fired.critical} critical</DangerLine>
      </Tile>
    </Grid>
  );
}
