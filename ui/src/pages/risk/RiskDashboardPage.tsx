import { RiskViewShell } from '../../risk-components/RiskViewShell';

/**
 * Stub landing page for the Risk tab (issue #165). No data yet — #166 wires
 * this to the `/risk/metrics/*` aggregates. Exists so `/risk` has somewhere
 * to land and the nav/routing shell is exercised end to end.
 */
export function RiskDashboardPage() {
  return (
    <RiskViewShell
      title="Risk dashboard"
      isLoading={false}
      isError={false}
      isEmpty={false}
    >
      <p>Risk metrics will appear here.</p>
    </RiskViewShell>
  );
}
