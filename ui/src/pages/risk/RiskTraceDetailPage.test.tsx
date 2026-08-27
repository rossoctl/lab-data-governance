import { describe, it, expect } from 'vitest';
import { screen } from '@testing-library/react';
import { Routes, Route } from 'react-router-dom';
import { renderWithProviders } from '../../test/renderWithProviders';
import { RiskTraceDetailPage } from './RiskTraceDetailPage';

// Mounted under a real :traceId route so useParams resolves, mirroring
// TraceDetailPage.test.tsx's harness() convention for the same requirement.
function harness() {
  return (
    <Routes>
      <Route path="/risk/traces/:traceId" element={<RiskTraceDetailPage />} />
    </Routes>
  );
}

describe('RiskTraceDetailPage', () => {
  it('renders its title with the trace id from the route', () => {
    renderWithProviders(harness(), { route: '/risk/traces/abc123' });
    expect(screen.getByRole('heading', { name: /risk trace/i })).toBeInTheDocument();
    expect(screen.getByText(/abc123/)).toBeInTheDocument();
  });
});
