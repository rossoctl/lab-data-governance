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
});
