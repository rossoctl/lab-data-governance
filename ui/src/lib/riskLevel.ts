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

/**
 * Each PF label colour → the PF GLOBAL palette variable that renders it —
 * the same NAME→NAME table as `entityKind.ts`'s `PF_COLOR_TO_GLOBAL_VAR`,
 * duplicated rather than imported because that map is private to its file
 * and the two colour spaces (entity kind, risk level) are independent
 * decisions that happen to share a rendering trick. See that file's
 * docstring for why component-scoped label variables don't work outside
 * a `<Label>` element and the global variables are used instead.
 */
const PF_COLOR_TO_GLOBAL_VAR: Partial<Record<RiskLevelColor, string>> = {
  red: '--pf-v5-global--danger-color--100',
  orange: '--pf-v5-global--palette--orange-300',
  gold: '--pf-v5-global--palette--gold-400',
  green: '--pf-v5-global--success-color--100',
  grey: '--pf-v5-global--Color--200',
};
const PF_COLOR_TO_GLOBAL_VAR_FALLBACK = '--pf-v5-global--Color--200';

/**
 * The CSS variable reference a risk level's colour should be painted with —
 * a `var(…)` REFERENCE (never a resolved value) so the dark theme's own
 * overrides apply at paint time, usable anywhere in the document (e.g. the
 * dashboard's stacked distribution bar, not just `<Label>` elements).
 *
 * `PF_COLOR_TO_GLOBAL_VAR` only covers the 5 colours {@link RISK_LEVEL_COLOR}
 * actually uses (of PF `Label`'s 8), so an unmapped colour falls back to the
 * same grey global variable `colorForRiskLevel` itself falls back to —
 * belt-and-suspenders, since `colorForRiskLevel` can't currently return
 * anything outside those 5, but the type doesn't guarantee that statically.
 */
export function riskLevelColorVar(level: string): string {
  const globalVar = PF_COLOR_TO_GLOBAL_VAR[colorForRiskLevel(level)] ?? PF_COLOR_TO_GLOBAL_VAR_FALLBACK;
  return `var(${globalVar}, var(--dg-color-label))`;
}

/**
 * Severity order, least to most severe — mirrors the server's own
 * `RISK_LEVEL_ORDER` (`data_governance/risk/rules/catalog.py`). `unknown` sits
 * beside `none` at the bottom: neither is a claim that something IS safe, so
 * neither should out-rank a level that is an actual verdict.
 */
const RISK_LEVEL_SEVERITY: Record<string, number> = {
  unknown: 0,
  none: 0,
  low: 1,
  medium: 2,
  high: 3,
  critical: 4,
};

/**
 * The more severe of two risk levels, `unknown` losing every tie so a real
 * verdict is never masked by an absent one.
 *
 * STOPGAP (issue #170): the risk API reports risk per INTERACTION, not per
 * entity — there is no first-class "this entity's risk level" anywhere in the
 * system. `ExecutionFlowGraph`'s node colouring uses this to roll up the
 * levels of a node's incident edges into one node-level colour, purely so the
 * node reads as "was this entity involved in the worst violation on screen".
 * If the risk model ever gains a genuine per-entity level, that should
 * replace this roll-up rather than sit beside it.
 */
export function moreSevereRiskLevel(a: string, b: string): string {
  const sa = RISK_LEVEL_SEVERITY[a] ?? RISK_LEVEL_SEVERITY.unknown;
  const sb = RISK_LEVEL_SEVERITY[b] ?? RISK_LEVEL_SEVERITY.unknown;
  return sb > sa ? b : a;
}
