import { Label } from '@patternfly/react-core';
import type { LiveMode } from '../api/live';

/**
 * The one visible fact about liveness (ADR-0034): whether the page is on the
 * trace's change stream or on its polling fallback. Not a control — there is
 * no way to switch it off, by design. Renders nothing while `off` (no stream
 * yet, or no `EventSource` at all). Shared by the trace page and the risk
 * trace page, so the two say it the same way.
 */
export function LiveModeLabel({ mode }: { mode: LiveMode }) {
  if (mode === 'off') return null;
  return (
    <Label
      isCompact
      color={mode === 'stream' ? 'green' : 'gold'}
      data-testid="live-mode"
      style={{ marginLeft: '0.75rem', verticalAlign: 'middle' }}
      title={
        mode === 'stream'
          ? 'Updates arrive as the trace is derived'
          : 'The change stream is down; re-reading every few seconds until it returns'
      }
    >
      {mode === 'stream' ? 'Live' : 'Polling'}
    </Label>
  );
}
