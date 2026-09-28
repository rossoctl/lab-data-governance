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
 *
 * Single-row invariant (issue #255): the row is `nowrap` and each column is
 * sized by `flexBasis: {pct}%` with shrink allowed and no fixed `min-width`
 * floor. A previous version pinned every non-zero segment to `min-width:
 * 2.5rem`; for an uneven split (e.g. 2.9% vs 97.1%) those floors summed past
 * 100% of the container, and — the row being wrap-enabled — the bar broke
 * onto a second row. Proportional flex-basis with shrink keeps every column
 * within 100% regardless of how lopsided the distribution is, so the bar is
 * always a single row. A tiny non-zero segment stays visible because its
 * coloured chunk carries a small `min-width`; that floor lives on the chunk,
 * not the flex column, so it can overflow its own column harmlessly instead
 * of widening the column and forcing a wrap. The legend label is clipped
 * within its column (`overflow: hidden`) so its intrinsic text width never
 * dictates the column width either.
 */
export function RiskDistributionBar({ distribution }: RiskDistributionBarProps) {
  const segments = distributionSegments(distribution);

  return (
    <Card isCompact>
      <CardTitle>Risk Distribution</CardTitle>
      <CardBody>
        <Flex
          spaceItems={{ default: 'spaceItemsNone' }}
          alignItems={{ default: 'alignItemsFlexStart' }}
          flexWrap={{ default: 'nowrap' }}
          fullWidth={{ default: 'fullWidth' }}
          data-testid="risk-distribution-bar"
        >
          {segments.map((segment) => (
            <FlexItem
              key={segment.level}
              data-testid="risk-distribution-segment"
              data-level={segment.level}
              style={{
                flexBasis: `${segment.pct}%`,
                flexGrow: 0,
                flexShrink: 1,
                minWidth: 0,
                overflow: 'hidden',
              }}
            >
              <div
                role="img"
                aria-label={`${LEVEL_TITLE[segment.level]}: ${segment.count} (${segment.pct}%)`}
                style={{
                  height: '1rem',
                  borderRadius: '4px',
                  backgroundColor: riskLevelColorVar(segment.level),
                  minWidth: segment.pct > 0 ? '0.25rem' : undefined,
                }}
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
