import { Label } from '@patternfly/react-core';

// A leaf of its own, not an export of ClassificationView: the risk policy-event
// panel shows a carried-in level with the same badge, and importing it from the
// trace page's view would pull that whole view (and its router use) into the
// risk panel.

/**
 * The escalation-ordered sensitivity palette (CONTEXT.md **Classification**):
 * PUBLIC (calm green) → INTERNAL (blue) → CONFIDENTIAL (orange) → RESTRICTED
 * (alarm red). An unknown level falls back to grey so a taxonomy the UI hasn't
 * caught up with (the model-derived set churns — issue #78) still renders.
 */
const SENSITIVITY_COLOR: Record<string, React.ComponentProps<typeof Label>['color']> = {
  PUBLIC: 'green',
  INTERNAL: 'blue',
  CONFIDENTIAL: 'orange',
  RESTRICTED: 'red',
};

/** A sensitivity level as a color-escalated badge. */
export function SensitivityBadge({ level }: { level: string }) {
  return (
    <Label isCompact color={SENSITIVITY_COLOR[level] ?? 'grey'}>
      {level}
    </Label>
  );
}
