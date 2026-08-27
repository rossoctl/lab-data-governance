import { Flex, FlexItem, Label } from '@patternfly/react-core';
import { distributionSegments } from '../../../lib/riskDistribution';
import { colorForRiskLevel, riskLevelColorVar } from '../../../lib/riskLevel';
import type { RiskDistributionResponse } from '../../../risk-api/types';

interface RiskDistributionBarProps {
  distribution: RiskDistributionResponse['distribution'];
}

const LEVEL_TITLE: Record<string, string> = {
  critical: 'Critical',
  high: 'High',
  medium: 'Medium',
  low: 'Low',
  none: 'None',
};

/**
 * FR-DAS-050's stacked risk-distribution bar (issue #169). Segment widths
 * come from `distributionSegments` (already zero-division-guarded), so an
 * all-zero window renders five zero-width segments rather than `NaN%`.
 */
export function RiskDistributionBar({ distribution }: RiskDistributionBarProps) {
  const segments = distributionSegments(distribution);

  return (
    <div>
      <div
        style={{ display: 'flex', width: '100%', height: '1rem', overflow: 'hidden', borderRadius: '4px' }}
      >
        {segments.map((segment) => (
          <div
            key={segment.level}
            data-testid="risk-distribution-segment"
            data-level={segment.level}
            role="img"
            aria-label={`${LEVEL_TITLE[segment.level]}: ${segment.count} (${segment.pct}%)`}
            style={{ width: `${segment.pct}%`, backgroundColor: riskLevelColorVar(segment.level) }}
          />
        ))}
      </div>
      <Flex spaceItems={{ default: 'spaceItemsMd' }} className="pf-v5-u-mt-sm">
        {segments.map((segment) => (
          <FlexItem key={segment.level}>
            <Label color={colorForRiskLevel(segment.level)} isCompact>
              {LEVEL_TITLE[segment.level]}: {segment.count} ({segment.pct}%)
            </Label>
          </FlexItem>
        ))}
      </Flex>
    </div>
  );
}
