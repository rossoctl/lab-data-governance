import { Button } from '@patternfly/react-core';

/** Truncated, clickable span-id cell (Span + Parent columns share this). */
export function SpanLink({
  spanId,
  onNavigate,
}: {
  spanId: string | null;
  onNavigate?: (spanId: string) => void;
}) {
  if (!spanId) return <>—</>;
  return (
    <Button
      variant="link"
      isInline
      onClick={() => onNavigate?.(spanId)}
      className="dg-mono"
    >
      {spanId.length > 16 ? `${spanId.slice(0, 16)}…` : spanId}
    </Button>
  );
}
