/**
 * THE single source of the enforcement-type → colour decision (issue #165).
 *
 * Mirrors `riskLevel.ts`/`entityKind.ts`'s map+fallback+resolver pattern.
 * The server's `ENFORCEMENT_ORDER` (`data_governance/risk/rules/catalog.py`)
 * has 13 values, more than double the issue's six named types — every value
 * is banded here by severity (most restrictive first) into PF's eight
 * `Label` colours, so an enforcement type the issue doesn't mention still
 * renders instead of falling back for most of the real catalog.
 */
import type { LabelProps } from '@patternfly/react-core';

export type EnforcementColor = NonNullable<LabelProps['color']>;

export const ENFORCEMENT_COLOR: Record<string, EnforcementColor> = {
  block: 'red',
  quarantine: 'orange',
  require_approval: 'gold',
  redact: 'purple',
  mask: 'purple',
  anonymize: 'purple',
  encrypt: 'cyan',
  escalate: 'orange',
  notify: 'cyan',
  warn: 'gold',
  log_only: 'blue',
  audit: 'blue',
  allow: 'green',
};

/** The fallback for an enforcement type not in {@link ENFORCEMENT_COLOR}. */
export const ENFORCEMENT_COLOR_FALLBACK: EnforcementColor = 'grey';

/** The PF `Label` colour for an enforcement type, fallback included. */
export function colorForEnforcement(type: string): EnforcementColor {
  return ENFORCEMENT_COLOR[type] ?? ENFORCEMENT_COLOR_FALLBACK;
}
