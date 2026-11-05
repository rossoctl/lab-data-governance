import { describe, it, expect } from 'vitest';
import {
  RISK_LEVEL_COLOR,
  RISK_LEVEL_COLOR_FALLBACK,
  colorForRiskLevel,
  moreSevereRiskLevel,
} from './riskLevel';

// The server's RISK_LEVEL_ORDER (data_governance/risk/rules/catalog.py) has
// six values — critical/high/medium/low/none/unknown — one more than the
// issue's five ("critical/high/medium/low/none"). This mapping must cover
// all six so an `unknown` record renders instead of falling through to a
// TypeScript-narrowed union that would reject it at the type level while
// the server sends it anyway.

describe('riskLevel', () => {
  it('maps every server risk level to a PF Label colour', () => {
    expect(RISK_LEVEL_COLOR).toEqual({
      critical: 'red',
      high: 'orange',
      medium: 'gold',
      low: 'green',
      none: 'grey',
      unknown: 'grey',
    });
  });

  it('resolves each known level via colorForRiskLevel', () => {
    expect(colorForRiskLevel('critical')).toBe('red');
    expect(colorForRiskLevel('high')).toBe('orange');
    expect(colorForRiskLevel('medium')).toBe('gold');
    expect(colorForRiskLevel('low')).toBe('green');
    expect(colorForRiskLevel('none')).toBe('grey');
    expect(colorForRiskLevel('unknown')).toBe('grey');
  });

  it('falls back to grey for a level not in the map', () => {
    expect(colorForRiskLevel('brand_new_level')).toBe(RISK_LEVEL_COLOR_FALLBACK);
    expect(colorForRiskLevel('')).toBe('grey');
  });

  it('gives the five issue-named levels visually distinct colours', () => {
    // The AC's literal ask. `none` deliberately deviates from the issue's
    // "low/none=green" — see docs/ui-design.md's Risk section — specifically
    // so this holds: five listed levels, five distinct colours.
    const colors = ['critical', 'high', 'medium', 'low', 'none'].map(colorForRiskLevel);
    expect(new Set(colors).size).toBe(5);
  });
});

describe('moreSevereRiskLevel', () => {
  it('orders the five named levels least to most severe', () => {
    expect(moreSevereRiskLevel('low', 'critical')).toBe('critical');
    expect(moreSevereRiskLevel('critical', 'low')).toBe('critical');
    expect(moreSevereRiskLevel('medium', 'high')).toBe('high');
    expect(moreSevereRiskLevel('none', 'low')).toBe('low');
  });

  it('returns the shared level when both sides are equal', () => {
    expect(moreSevereRiskLevel('high', 'high')).toBe('high');
  });

  it('never lets unknown outrank a real verdict, in either position', () => {
    expect(moreSevereRiskLevel('unknown', 'low')).toBe('low');
    expect(moreSevereRiskLevel('low', 'unknown')).toBe('low');
  });

  it('treats an unmapped level like unknown rather than throwing', () => {
    expect(moreSevereRiskLevel('brand_new_level', 'medium')).toBe('medium');
  });
});
