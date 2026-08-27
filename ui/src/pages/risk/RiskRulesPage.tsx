import { RiskViewShell } from '../../risk-components/RiskViewShell';

/**
 * Stub landing page for the risk rules catalog (issue #165). No data yet —
 * #168 wires this to `/risk/rules`.
 */
export function RiskRulesPage() {
  return (
    <RiskViewShell
      title="Risk rules"
      isLoading={false}
      isError={false}
      isEmpty={false}
    >
      <p>The risk rules catalog will appear here.</p>
    </RiskViewShell>
  );
}
