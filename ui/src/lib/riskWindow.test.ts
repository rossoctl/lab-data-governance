import { describe, it, expect } from 'vitest';
import { RISK_WINDOW_KEYS, DEFAULT_RISK_WINDOW, parseRiskWindow } from './riskWindow';

describe('riskWindow', () => {
  it('pins the three fixed windows the server accepts, default 24h', () => {
    expect(RISK_WINDOW_KEYS).toEqual(['24h', '7d', '30d']);
    expect(DEFAULT_RISK_WINDOW).toBe('24h');
  });

  it.each(RISK_WINDOW_KEYS)('round-trips a valid key %s', (key) => {
    expect(parseRiskWindow(key)).toBe(key);
  });

  it('falls back to the default for null', () => {
    expect(parseRiskWindow(null)).toBe('24h');
  });

  it('falls back to the default for an empty string', () => {
    expect(parseRiskWindow('')).toBe('24h');
  });

  it('falls back to the default for an unsupported window', () => {
    expect(parseRiskWindow('90d')).toBe('24h');
  });

  it('is case-sensitive, matching the server, and falls back on a wrong case', () => {
    expect(parseRiskWindow('24H')).toBe('24h');
  });

  it('falls back to the default for "custom" (out of MVP scope here)', () => {
    expect(parseRiskWindow('custom')).toBe('24h');
  });

  it('falls back to the default for unrelated garbage', () => {
    expect(parseRiskWindow('not-a-window')).toBe('24h');
  });
});
