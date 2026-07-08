import { useParams } from 'react-router-dom';
import { PageSection, Title } from '@patternfly/react-core';

// Placeholder — the three-way view switcher (Tree | Flow | Graph) lands in the
// components/pages slice. The route param proves the deep-link contract:
// /ui/traces/:traceId resolves here client-side (basename="/ui").
export function TraceDetailPage() {
  const { traceId } = useParams<{ traceId: string }>();
  return (
    <PageSection>
      <Title headingLevel="h2" size="xl">
        Trace {traceId}
      </Title>
    </PageSection>
  );
}
