import { Label } from '@patternfly/react-core';
import { colorForRiskLevel } from '../lib/riskLevel';

/**
 * A pill labelled with a risk level, coloured via `lib/riskLevel`'s single
 * source of truth. Reuses `dg-ent-pill` (global.css) — the same bordered
 * treatment `EntityPill` uses — rather than adding a near-identical class,
 * so the Risk UI stays visually indistinguishable from the rest of the app.
 */
export function RiskBadge({ level }: { level: string }) {
  return (
    <Label isCompact color={colorForRiskLevel(level)} className="dg-ent-pill">
      {level}
    </Label>
  );
}
