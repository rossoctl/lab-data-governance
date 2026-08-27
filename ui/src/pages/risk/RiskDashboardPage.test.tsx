import { describe, it, expect } from 'vitest';
import { screen } from '@testing-library/react';
import { renderWithProviders } from '../../test/renderWithProviders';
import { RiskDashboardPage } from './RiskDashboardPage';

describe('RiskDashboardPage', () => {
  it('renders its title without throwing', () => {
    renderWithProviders(<RiskDashboardPage />, { route: '/risk' });
    expect(screen.getByRole('heading', { name: /risk dashboard/i })).toBeInTheDocument();
  });
});
