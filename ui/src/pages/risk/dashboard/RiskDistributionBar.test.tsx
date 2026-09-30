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

  it('renders five bar chunks in severity order (critical first, none last)', () => {
    render(<RiskDistributionBar distribution={distribution()} />);
    const levels = screen.getAllByTestId('risk-distribution-segment').map((el) => el.dataset.level);
    expect(levels).toEqual(['critical', 'high', 'medium', 'low', 'none']);
  });

  it('renders a legend entry only for each non-zero level, in severity order', () => {
    // Fixture is all non-zero, so all five appear, critical first.
    render(<RiskDistributionBar distribution={distribution()} />);
    const levels = screen.getAllByTestId('risk-distribution-legend-item').map((el) => el.dataset.level);
    expect(levels).toEqual(['critical', 'high', 'medium', 'low', 'none']);
  });

  it('omits legend entries for zero-count levels (only non-zero levels shown)', () => {
    // The reported 30-day case: only critical and none have any records.
    render(
      <RiskDistributionBar
        distribution={distribution({ critical: 29, high: 0, medium: 0, low: 0, none: 971, total: 1000 })}
      />,
    );
    const levels = screen.getAllByTestId('risk-distribution-legend-item').map((el) => el.dataset.level);
    expect(levels).toEqual(['critical', 'none']);
    // The zero-count levels are absent from the legend entirely.
    expect(screen.queryByText(/High: 0/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Medium: 0/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Low: 0/)).not.toBeInTheDocument();
  });

  it('renders no legend entries at all for an all-zero distribution', () => {
    render(
      <RiskDistributionBar
        distribution={distribution({ critical: 0, high: 0, medium: 0, low: 0, none: 0, total: 0 })}
      />,
    );
    expect(screen.queryAllByTestId('risk-distribution-legend-item')).toHaveLength(0);
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
    // No bar chunk may pin an absolute minimum width (a `rem`/`px`/`em` floor)
    // on its flex column — that floor is what pushed the summed widths past
    // 100% and wrapped the row. A `min-width` of 0 is fine (it only *permits*
    // shrink).
    for (const segment of screen.getAllByTestId('risk-distribution-segment')) {
      const minWidth = segment.style.minWidth;
      expect(minWidth).not.toMatch(/\d\s*(rem|px|em)/);
    }
  });

  it('sizes each bar chunk by its percentage share via flex-basis', () => {
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

  // Issue #255 follow-up: with the bar clipped to a single row, a tiny chunk
  // (e.g. 2.9% critical) is too narrow to hold its own legend text, so a
  // legend glued *under* each chunk clipped the Critical entry out of sight.
  // The legend must instead be its own row where every entry stays fully
  // visible, laid out left-to-right in severity order, so nothing is lost no
  // matter how narrow a chunk is.
  it('keeps every legend entry visible (never clipped) for an uneven distribution', () => {
    render(
      <RiskDistributionBar
        distribution={distribution({ critical: 29, high: 0, medium: 0, low: 0, none: 971, total: 1000 })}
      />,
    );
    // The Critical legend entry — under the 2.9% chunk it would be clipped —
    // is present with its full text, and its container is not overflow-hidden.
    const critical = screen
      .getAllByTestId('risk-distribution-legend-item')
      .find((el) => el.dataset.level === 'critical');
    expect(critical).toBeDefined();
    expect(critical).toHaveTextContent(/Critical: 29/);
    expect(critical!.style.overflow).not.toBe('hidden');
  });

  it('lays the legend out as its own wrap-enabled row, decoupled from chunk widths', () => {
    render(
      <RiskDistributionBar
        distribution={distribution({ critical: 29, high: 0, medium: 0, low: 0, none: 971, total: 1000 })}
      />,
    );
    const legend = screen.getByTestId('risk-distribution-legend');
    // The legend row wraps (default PF Flex wrap) rather than clipping to one
    // line, so entries flow left-to-right and drop to the next line only when
    // the card itself is too narrow — never hidden.
    expect(legend.className).not.toContain('pf-m-nowrap');
    // No legend entry is sized by the bar's percentages; each is content-sized.
    for (const item of screen.getAllByTestId('risk-distribution-legend-item')) {
      expect(item.style.flexBasis).toBe('');
      expect(item.style.width).toBe('');
    }
  });
});
