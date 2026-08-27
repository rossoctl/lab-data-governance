/**
 * THE single source of the risk-level → colour decision (issue #165).
 *
 * Mirrors `entityKind.ts`'s `KIND_COLOR`/`colorForKind` pattern: lives in
 * `lib/` (render-free) so it stays unit-testable and importable from a
 * `.tsx` component without tripping react-refresh's only-export-components
 * rule, and every risk view (#166 dashboard, #167 trace detail, #168 rules)
 * reads from here rather than restating the mapping.
 *
 * The server's `RISK_LEVEL_ORDER` (`data_governance/risk/rules/catalog.py`)
 * has six values — this issue's five (critical/high/medium/low/none) plus
 * `unknown` — so the map covers all six.
 *
 * Colour choices: PF 5's `Label` colour union has no `yellow`, so `medium`
 * uses `gold` (PF's yellow, already this repo's warning colour — see
 * `RecentTracesPage`'s "Missing parent" label). `none` uses `grey` rather
 * than the issue's literal "low/none=green" — a deliberate deviation so all
 * five issue-named levels resolve to five distinct colours, which is what
 * the acceptance criteria actually asks for; recorded in docs/ui-design.md.
 */
import type { LabelProps } from '@patternfly/react-core';

export type RiskLevelColor = NonNullable<LabelProps['color']>;

export const RISK_LEVEL_COLOR: Record<string, RiskLevelColor> = {
  critical: 'red',
  high: 'orange',
  medium: 'gold',
  low: 'green',
  none: 'grey',
  unknown: 'grey',
};

/** The fallback for a risk level not in {@link RISK_LEVEL_COLOR}. */
export const RISK_LEVEL_COLOR_FALLBACK: RiskLevelColor = 'grey';

/** The PF `Label` colour for a risk level, fallback included. */
export function colorForRiskLevel(level: string): RiskLevelColor {
  return RISK_LEVEL_COLOR[level] ?? RISK_LEVEL_COLOR_FALLBACK;
}
