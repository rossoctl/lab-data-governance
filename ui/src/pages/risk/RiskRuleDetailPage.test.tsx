import { describe, it, expect } from 'vitest';
import { screen } from '@testing-library/react';
import { Routes, Route } from 'react-router-dom';
import { renderWithProviders } from '../../test/renderWithProviders';
import { RiskRuleDetailPage } from './RiskRuleDetailPage';

// Mounted under a real :ruleId route so useParams resolves, mirroring
// TraceDetailPage.test.tsx's harness() convention for the same requirement.
function harness() {
  return (
    <Routes>
      <Route path="/risk/rules/:ruleId" element={<RiskRuleDetailPage />} />
    </Routes>
  );
}

describe('RiskRuleDetailPage', () => {
  it('renders its title with the rule id from the route', () => {
    renderWithProviders(harness(), { route: '/risk/rules/r1' });
    expect(screen.getByRole('heading', { name: /risk rule/i })).toBeInTheDocument();
    expect(screen.getByText(/r1/)).toBeInTheDocument();
  });
});
