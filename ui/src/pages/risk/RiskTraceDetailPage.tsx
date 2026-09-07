import { useParams } from 'react-router-dom';
import { RiskViewShell } from '../../risk-components/RiskViewShell';

/**
 * Stub detail page for a single risk trace (issue #165). No data yet — #167
 * wires this to `/risk/traces/:traceId`.
 */
export function RiskTraceDetailPage() {
  const { traceId } = useParams<{ traceId: string }>();

  return (
    <RiskViewShell
      title="Risk trace"
      isLoading={false}
      isError={false}
      isEmpty={false}
    >
      <p>Risk detail for trace {traceId} will appear here.</p>
    </RiskViewShell>
  );
}
