import { describe, it, expect } from 'vitest';
import {
  ENFORCEMENT_COLOR,
  ENFORCEMENT_COLOR_FALLBACK,
  colorForEnforcement,
} from './riskEnforcement';

// The server's ENFORCEMENT_ORDER (data_governance/risk/rules/catalog.py) has
// 13 values, more than double the issue's six ("block/quarantine/
// require_approval/redact/notify/allow"). Every value must resolve to a PF
// Label colour with no `undefined` leaking through — PF's Label throws on an
// invalid `color` prop, so a gap here is a render crash, not a cosmetic miss.

const ALL_ENFORCEMENT_TYPES = [
  'block',
  'quarantine',
  'require_approval',
  'redact',
  'mask',
  'anonymize',
  'encrypt',
  'escalate',
  'notify',
  'warn',
  'log_only',
  'audit',
  'allow',
];

describe('riskEnforcement', () => {
  it('maps every server enforcement type to a PF Label colour', () => {
    for (const type of ALL_ENFORCEMENT_TYPES) {
      expect(ENFORCEMENT_COLOR[type]).toBeDefined();
    }
    expect(Object.keys(ENFORCEMENT_COLOR).sort()).toEqual([...ALL_ENFORCEMENT_TYPES].sort());
  });

  it('resolves every value with no undefined leak', () => {
    for (const type of ALL_ENFORCEMENT_TYPES) {
      expect(colorForEnforcement(type)).toBeTypeOf('string');
    }
  });

  it('maps the issue-named six to sensible severity colours', () => {
    expect(colorForEnforcement('block')).toBe('red');
    expect(colorForEnforcement('quarantine')).toBe('orange');
    expect(colorForEnforcement('require_approval')).toBe('gold');
    expect(colorForEnforcement('redact')).toBe('purple');
    expect(colorForEnforcement('notify')).toBe('cyan');
    expect(colorForEnforcement('allow')).toBe('green');
  });

  it('falls back to grey for a type not in the map', () => {
    expect(colorForEnforcement('brand_new_type')).toBe(ENFORCEMENT_COLOR_FALLBACK);
    expect(colorForEnforcement('')).toBe('grey');
  });
});
