import { describe, it, expect } from 'vitest';
import { screen } from '@testing-library/react';
import { renderWithProviders } from '../../test/renderWithProviders';
import { RiskRulesPage } from './RiskRulesPage';

describe('RiskRulesPage', () => {
  it('renders its title without throwing', () => {
    renderWithProviders(<RiskRulesPage />, { route: '/risk/rules' });
    expect(screen.getByRole('heading', { name: /risk rules/i })).toBeInTheDocument();
  });
});
