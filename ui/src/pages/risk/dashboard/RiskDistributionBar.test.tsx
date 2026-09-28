import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { RiskDistributionBar } from './RiskDistributionBar';
import type { RiskDistributionResponse } from '../../../risk-api/types';

function distribution(
  overrides: Partial<RiskDistributionResponse['distribution']> = {},
): RiskDistributionResponse['distribution'] {
  return {
    critical: 2,
    high: 3,
    medium: 5,
    low: 10,
    none: 80,
    total: 100,
    ...overrides,
  };
}

describe('RiskDistributionBar', () => {
  it('renders a card title "Risk Distribution"', () => {
    render(<RiskDistributionBar distribution={distribution()} />);
    expect(screen.getByText('Risk Distribution')).toBeInTheDocument();
  });

  it('renders five segments in severity order (critical first, none last)', () => {
    render(<RiskDistributionBar distribution={distribution()} />);
    const labels = screen.getAllByTestId('risk-distribution-segment').map((el) => el.dataset.level);
    expect(labels).toEqual(['critical', 'high', 'medium', 'low', 'none']);
  });

  it("nests each segment's legend label directly under that segment's bar chunk", () => {
    render(<RiskDistributionBar distribution={distribution()} />);
    const [criticalSegment] = screen.getAllByTestId('risk-distribution-segment');
    const label = screen.getByText(/Critical: 2 \(2%\)/);
    expect(criticalSegment).toContainElement(label);
  });

  it('gives each segment an accessible label carrying both count and percentage', () => {
    render(<RiskDistributionBar distribution={distribution()} />);
    expect(screen.getByLabelText(/critical: 2 \(2%\)/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/high: 3 \(3%\)/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/medium: 5 \(5%\)/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/low: 10 \(10%\)/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/none: 80 \(80%\)/i)).toBeInTheDocument();
  });

  it('renders an all-zero distribution without a zero-division artefact', () => {
    render(
      <RiskDistributionBar
        distribution={distribution({ critical: 0, high: 0, medium: 0, low: 0, none: 0, total: 0 })}
      />,
    );
    expect(screen.queryByText(/NaN/)).not.toBeInTheDocument();
    expect(screen.getByLabelText(/critical: 0 \(0%\)/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/none: 0 \(0%\)/i)).toBeInTheDocument();
  });

  // Regression: issue #255. A highly uneven distribution (one tiny segment,
  // one dominant one) used to wrap onto a second row because the flex row
  // could wrap and each non-zero segment carried a fixed `min-width` floor
  // that, summed, overflowed 100% of the container. The bar must stay a
  // single row for ANY distribution.
  it('keeps the bar on a single row (never wraps) even for an uneven distribution', () => {
    render(
      <RiskDistributionBar
        // 2.9% critical vs 97.1% none — the exact shape from issue #255.
        distribution={distribution({ critical: 29, high: 0, medium: 0, low: 0, none: 971, total: 1000 })}
      />,
    );
    const bar = screen.getByTestId('risk-distribution-bar');
    // PatternFly renders `flexWrap={{ default: 'nowrap' }}` as `pf-m-nowrap`.
    expect(bar.className).toContain('pf-m-nowrap');
  });

  it('does not force a per-segment min-width that can overflow the row', () => {
    render(
      <RiskDistributionBar
        distribution={distribution({ critical: 29, high: 0, medium: 0, low: 0, none: 971, total: 1000 })}
      />,
    );
    // No segment column may pin an absolute minimum width (a `rem`/`px`/`em`
    // floor) — that floor is what pushed the summed widths past 100% and
    // wrapped the row. A `min-width` of 0 is fine (it only *permits* shrink).
    for (const segment of screen.getAllByTestId('risk-distribution-segment')) {
      const minWidth = segment.style.minWidth;
      expect(minWidth).not.toMatch(/\d\s*(rem|px|em)/);
    }
  });

  it('sizes each column by its percentage share via flex-basis', () => {
    render(
      <RiskDistributionBar
        distribution={distribution({ critical: 29, high: 0, medium: 0, low: 0, none: 971, total: 1000 })}
      />,
    );
    const [critical, , , , none] = screen.getAllByTestId('risk-distribution-segment');
    expect(critical.style.flexBasis).toBe('2.9%');
    expect(none.style.flexBasis).toBe('97.1%');
    // Columns must be allowed to shrink so the summed basis never overflows.
    expect(critical.style.flexShrink).toBe('1');
  });
});
