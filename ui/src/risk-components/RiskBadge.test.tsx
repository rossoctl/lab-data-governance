import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { RiskBadge } from './RiskBadge';

// The AC's headline claim: all six server-known risk levels render with a
// visually distinct PF Label colour (five of the issue's named levels are
// guaranteed distinct by lib/riskLevel.test.ts; this file pins that the
// component actually paints the colour the map picks).

describe('RiskBadge', () => {
  it.each([
    ['critical', 'red'],
    ['high', 'orange'],
    ['medium', 'gold'],
    ['low', 'green'],
    ['none', 'grey'],
    ['unknown', 'grey'],
  ])('renders %s with PF colour %s', (level, color) => {
    render(<RiskBadge level={level} />);
    expect(screen.getByText(level)).toBeInTheDocument();
    const label = screen.getByText(level).closest('.pf-v5-c-label') as HTMLElement;
    if (color === 'grey') {
      // PF's Label maps `grey` to no modifier class at all (colorStyles.grey === '').
      expect(label.className).not.toMatch(/pf-m-(red|orange|gold|green|blue|cyan|purple)\b/);
    } else {
      expect(label).toHaveClass(`pf-m-${color}`);
    }
  });

  it('renders an unlisted level as grey without throwing', () => {
    expect(() => render(<RiskBadge level="brand_new_level" />)).not.toThrow();
    expect(screen.getByText('brand_new_level')).toBeInTheDocument();
  });

  it('carries the shared bordered-label class', () => {
    render(<RiskBadge level="critical" />);
    const label = screen.getByText('critical').closest('.pf-v5-c-label') as HTMLElement;
    expect(label).toHaveClass('dg-ent-pill');
  });
});
