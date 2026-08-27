import { useParams } from 'react-router-dom';
import { RiskViewShell } from '../../risk-components/RiskViewShell';

/**
 * Stub detail page for a single risk rule (issue #165). No data yet — #168
 * wires this to `/risk/rules/:ruleId`.
 */
export function RiskRuleDetailPage() {
  const { ruleId } = useParams<{ ruleId: string }>();

  return (
    <RiskViewShell
      title="Risk rule"
      isLoading={false}
      isError={false}
      isEmpty={false}
    >
      <p>Details for rule {ruleId} will appear here.</p>
    </RiskViewShell>
  );
}
