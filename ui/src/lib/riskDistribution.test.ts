import { describe, it, expect } from 'vitest';
import { distributionSegments } from './riskDistribution';

describe('distributionSegments', () => {
  it('orders segments critical -> high -> medium -> low -> none', () => {
    const segments = distributionSegments({
      critical: 1,
      high: 2,
      medium: 3,
      low: 4,
      none: 5,
      total: 15,
    });
    expect(segments.map((s) => s.level)).toEqual(['critical', 'high', 'medium', 'low', 'none']);
  });

  it('computes counts and percentages that sum to 100 for a normal distribution', () => {
    const segments = distributionSegments({
      critical: 10,
      high: 20,
      medium: 30,
      low: 0,
      none: 40,
      total: 100,
    });
    expect(segments.map((s) => s.count)).toEqual([10, 20, 30, 0, 40]);
    expect(segments.map((s) => s.pct)).toEqual([10, 20, 30, 0, 40]);
    expect(segments.reduce((sum, s) => sum + s.pct, 0)).toBeCloseTo(100, 5);
  });

  it('does not divide by zero when total is 0: every pct is 0', () => {
    const segments = distributionSegments({
      critical: 0,
      high: 0,
      medium: 0,
      low: 0,
      none: 0,
      total: 0,
    });
    for (const s of segments) {
      expect(s.pct).toBe(0);
      expect(Number.isNaN(s.pct)).toBe(false);
    }
  });

  it('gives a single non-zero level 100 pct', () => {
    const segments = distributionSegments({
      critical: 0,
      high: 0,
      medium: 5,
      low: 0,
      none: 0,
      total: 5,
    });
    const medium = segments.find((s) => s.level === 'medium');
    expect(medium?.pct).toBe(100);
  });

  it('retains zero-count levels as zero-width segments rather than dropping them', () => {
    const segments = distributionSegments({
      critical: 0,
      high: 10,
      medium: 0,
      low: 0,
      none: 0,
      total: 10,
    });
    expect(segments).toHaveLength(5);
    expect(segments.find((s) => s.level === 'critical')).toMatchObject({ count: 0, pct: 0 });
  });

  it('never lets rounded widths exceed 100 in total', () => {
    // A distribution that would produce repeating-decimal percentages.
    const segments = distributionSegments({
      critical: 1,
      high: 1,
      medium: 1,
      low: 0,
      none: 0,
      total: 3,
    });
    const totalPct = segments.reduce((sum, s) => sum + s.pct, 0);
    expect(totalPct).toBeLessThanOrEqual(100.001);
  });
});
