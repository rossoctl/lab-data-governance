import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { screen } from '@testing-library/react';
import { renderWithProviders } from './test/renderWithProviders';
import App from './App';

// App's own chrome — the masthead brand, the horizontal Nav (issue #165), and
// which nav item is marked current for a given route. No backend is running,
// so every fetch is stubbed to reject; only the shell that renders without
// data is asserted here (data-dependent behaviour belongs to each page's own
// test, per e2e/smoke.spec.ts's existing convention for /traces).

describe('App', () => {
  beforeEach(() => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.reject(new Error('no backend in this test'))),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('renders the brand and both nav items at /traces, with Traces current', () => {
    renderWithProviders(<App />, { route: '/traces' });
    expect(screen.getByText('Data Governance')).toBeInTheDocument();
    const traces = screen.getByRole('link', { name: 'Traces' });
    const risk = screen.getByRole('link', { name: 'Risk' });
    expect(traces).toBeInTheDocument();
    expect(risk).toBeInTheDocument();
    expect(traces).toHaveAttribute('aria-current', 'page');
    expect(risk).not.toHaveAttribute('aria-current');
  });

  it('renders the brand and marks Risk current at /risk', () => {
    renderWithProviders(<App />, { route: '/risk' });
    expect(screen.getByText('Data Governance')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Risk' })).toHaveAttribute('aria-current', 'page');
    expect(screen.getByRole('link', { name: 'Traces' })).not.toHaveAttribute('aria-current');
  });

  it('marks Risk current for any nested /risk/* route', () => {
    renderWithProviders(<App />, { route: '/risk/rules' });
    expect(screen.getByRole('link', { name: 'Risk' })).toHaveAttribute('aria-current', 'page');
  });
});
