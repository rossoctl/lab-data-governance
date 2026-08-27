import { Label } from '@patternfly/react-core';
import { colorForEnforcement } from '../lib/riskEnforcement';

/**
 * A pill labelled with an enforcement type, coloured via
 * `lib/riskEnforcement`'s single source of truth. Same `dg-ent-pill`
 * treatment as {@link RiskBadge} and `EntityPill`.
 */
export function EnforcementChip({ type }: { type: string }) {
  return (
    <Label isCompact color={colorForEnforcement(type)} className="dg-ent-pill">
      {type}
    </Label>
  );
}
