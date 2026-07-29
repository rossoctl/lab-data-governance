import { useEffect } from 'react';
import { Routes, Route, Navigate, Link } from 'react-router-dom';
import {
  Page,
  Masthead,
  MastheadMain,
  MastheadBrand,
  MastheadContent,
  Title,
  Tooltip,
} from '@patternfly/react-core';
import { InfoCircleIcon } from '@patternfly/react-icons';

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
      {/* Top-right region: a small, unobtrusive build-version stamp. The SHA
          text is always shown; the icon carries a hover/focus tooltip with the
          same version so it's discoverable either way. __APP_VERSION__ is
          injected at build time (see vite.config.ts). */}
      {/* MastheadContent is a flex-1 region, so a full-width flex wrapper that
          pushes its child to the end is what reliably right-aligns the stamp
          (pf-v5-u-ml-auto alone doesn't, without a full-width flex row). */}
      <MastheadContent>
        <div style={{ display: 'flex', width: '100%', justifyContent: 'flex-end' }}>
          <Tooltip content={`Version: ${__APP_VERSION__}`}>
            <span
              className="pf-v5-u-color-200 pf-v5-u-font-size-sm"
              style={{ display: 'inline-flex', alignItems: 'center', gap: '0.25rem' }}
            >
              <InfoCircleIcon />
              {__APP_VERSION__}
            </span>
          </Tooltip>
        </div>
      </MastheadContent>
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
