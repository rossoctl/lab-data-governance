import { Card, CardTitle, CardBody, Flex, FlexItem, Label } from '@patternfly/react-core';
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
 *
 * Each segment is its own column — the coloured bar chunk on top, that
 * segment's legend label directly beneath it — rather than one bar row with
 * a separate legend row below, so a label always sits under the chunk it
 * describes instead of in an unrelated flex-wrapped line.
 */
export function RiskDistributionBar({ distribution }: RiskDistributionBarProps) {
  const segments = distributionSegments(distribution);

  return (
    <Card isCompact>
      <CardTitle>Risk Distribution</CardTitle>
      <CardBody>
        <Flex spaceItems={{ default: 'spaceItemsNone' }} alignItems={{ default: 'alignItemsFlexStart' }}>
          {segments.map((segment) => (
            <FlexItem
              key={segment.level}
              data-testid="risk-distribution-segment"
              data-level={segment.level}
              style={{ width: `${segment.pct}%`, minWidth: segment.pct > 0 ? '2.5rem' : undefined }}
            >
              <div
                role="img"
                aria-label={`${LEVEL_TITLE[segment.level]}: ${segment.count} (${segment.pct}%)`}
                style={{ height: '1rem', borderRadius: '4px', backgroundColor: riskLevelColorVar(segment.level) }}
              />
              <Label color={colorForRiskLevel(segment.level)} isCompact className="pf-v5-u-mt-sm">
                {LEVEL_TITLE[segment.level]}: {segment.count} ({segment.pct}%)
              </Label>
            </FlexItem>
          ))}
        </Flex>
      </CardBody>
    </Card>
  );
}
