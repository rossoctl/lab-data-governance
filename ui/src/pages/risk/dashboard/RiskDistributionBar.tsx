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
 * Layout: a single-row coloured bar on top, and a separate legend row beneath
 * it — NOT one legend label glued under each chunk.
 *
 * Single-row invariant (issue #255): the bar row is `nowrap` and each chunk
 * is sized by `flexBasis: {pct}%` with shrink allowed and no fixed `min-width`
 * floor on its flex column. A pre-#255 version pinned every non-zero segment
 * to `min-width: 2.5rem`; for an uneven split (e.g. 2.9% vs 97.1%) those
 * floors summed past 100% of the container and the (then wrap-enabled) bar
 * broke onto a second row. Proportional flex-basis with shrink keeps every
 * chunk within 100% regardless of how lopsided the distribution is, so the
 * bar is always a single row. A tiny non-zero chunk stays visible because its
 * coloured block carries a small `min-width`; that floor lives on the block,
 * not the flex column, so it overflows its own column harmlessly instead of
 * forcing a wrap.
 *
 * Legend placement (issue #255 follow-up): the #255 fix originally kept each
 * label glued under its chunk and clipped it (`overflow: hidden`) so text
 * width couldn't widen the column — but that hid the label of any chunk too
 * narrow to hold it (the Critical entry vanished under a 2.9% chunk). The
 * requirement is "under the corresponding part of the bar when there's room,
 * otherwise laid out left-to-right in severity order so every entry stays
 * visible." A separate legend row satisfies both: it is a wrap-enabled flex
 * in severity order, so entries read left-to-right (critical first) and drop
 * to the next line only when the card itself is too narrow — never clipped,
 * never hidden. In the common even-distribution case, both rows being
 * severity-ordered, each legend entry still sits roughly beneath its chunk.
 *
 * Legend content: only levels with a **non-zero count** get a legend entry
 * (per the reported 30-day case — a critical/none-only window should show
 * just those two, not five entries three of which read `: 0 (0%)`). The bar
 * itself still renders all five chunks; a zero-count level is simply a
 * zero-width (invisible) chunk with no legend entry. An all-zero window
 * therefore renders an empty legend row.
 */
export function RiskDistributionBar({ distribution }: RiskDistributionBarProps) {
  const segments = distributionSegments(distribution);

  return (
    <Card isCompact>
      <CardTitle>Risk Distribution</CardTitle>
      <CardBody>
        <Flex
          spaceItems={{ default: 'spaceItemsNone' }}
          alignItems={{ default: 'alignItemsCenter' }}
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
            </FlexItem>
          ))}
        </Flex>
        <Flex
          spaceItems={{ default: 'spaceItemsSm' }}
          alignItems={{ default: 'alignItemsCenter' }}
          className="pf-v5-u-mt-sm"
          data-testid="risk-distribution-legend"
        >
          {segments
            .filter((segment) => segment.count > 0)
            .map((segment) => (
            <FlexItem
              key={segment.level}
              data-testid="risk-distribution-legend-item"
              data-level={segment.level}
            >
              <Label color={colorForRiskLevel(segment.level)} isCompact>
                {LEVEL_TITLE[segment.level]}: {segment.count} ({segment.pct}%)
              </Label>
            </FlexItem>
          ))}
        </Flex>
      </CardBody>
    </Card>
  );
}
