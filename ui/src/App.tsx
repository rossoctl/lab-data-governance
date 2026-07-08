import { useEffect } from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import {
  Page,
  Masthead,
  MastheadMain,
  MastheadBrand,
  MastheadContent,
  Title,
} from '@patternfly/react-core';

import { RecentTracesPage } from './pages/RecentTracesPage';
import { TraceDetailPage } from './pages/TraceDetailPage';

// Lean data-governance shell: a dark-themed PatternFly Page with a simple
// masthead. No auth / namespace / feature-flag chrome (out of scope per
// ADR-0019) — just the two routes the trace-governance UI needs.
export default function App() {
  useEffect(() => {
    document.documentElement.classList.add('pf-v5-theme-dark');
  }, []);

  const masthead = (
    <Masthead>
      <MastheadMain>
        <MastheadBrand>
          <Title headingLevel="h1" size="lg">
            Data Governance
          </Title>
        </MastheadBrand>
      </MastheadMain>
      <MastheadContent />
    </Masthead>
  );

  return (
    <Page header={masthead}>
      <Routes>
        <Route path="/" element={<RecentTracesPage />} />
        <Route path="/traces/:traceId" element={<TraceDetailPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Page>
  );
}
