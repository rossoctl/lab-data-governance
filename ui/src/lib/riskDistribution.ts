import type { RiskDistributionResponse } from '../risk-api/types';

export type RiskDistributionLevel = 'critical' | 'high' | 'medium' | 'low' | 'none';

const LEVEL_ORDER: readonly RiskDistributionLevel[] = ['critical', 'high', 'medium', 'low', 'none'];

export interface DistributionSegment {
  level: RiskDistributionLevel;
  count: number;
  pct: number;
}

/**
 * Turn `GET /risk/metrics/risk-distribution`'s `distribution` object into
 * ordered severity segments for the stacked bar (issue #169's FR-DAS-050
 * card). Every level is retained even at count 0 — a zero-width segment,
 * not a dropped legend entry — and `total: 0` yields every `pct` as `0`
 * rather than dividing by zero (mirroring the server's own
 * `TileCounts.risky_pct`/`EnforcementDistribution.pct` zero-division guard).
 */
export function distributionSegments(
  distribution: RiskDistributionResponse['distribution'],
): DistributionSegment[] {
  const { total } = distribution;
  return LEVEL_ORDER.map((level) => {
    const count = distribution[level];
    const pct = total === 0 ? 0 : Math.round((count / total) * 1000) / 10;
    return { level, count, pct };
  });
}
