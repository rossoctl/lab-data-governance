import { useEffect } from 'react';
import { Routes, Route, Navigate, Link } from 'react-router-dom';
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
          {/* Logo-home convention: the brand always returns to the trace list
              (the canonical /ui/traces, basename-aware via the router).
              Redundant with the trace-detail breadcrumb by design — it's
              muscle memory. */}
          <Link to="/traces" className="dg-brand-link">
            <Title headingLevel="h1" size="lg">
              Data Governance
            </Title>
          </Link>
        </MastheadBrand>
      </MastheadMain>
      <MastheadContent />
    </Masthead>
  );

  return (
    <Page header={masthead}>
      {/* URL = single source of truth for UI state (reload/bookmark/back). The
          list lives at /traces (bare / redirects there); a trace's detail view
          is a path segment — /traces/{id}/spans (span tree) or /flow
          (interaction flow) — and bare /traces/{id} canonicalises to /spans. */}
      <Routes>
        <Route path="/" element={<Navigate to="/traces" replace />} />
        <Route path="/traces" element={<RecentTracesPage />} />
        <Route path="/traces/:traceId" element={<Navigate to="spans" replace />} />
        <Route path="/traces/:traceId/:view" element={<TraceDetailPage />} />
        <Route path="*" element={<Navigate to="/traces" replace />} />
      </Routes>
    </Page>
  );
}
