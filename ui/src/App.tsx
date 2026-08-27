import { useEffect } from 'react';
import { Routes, Route, Navigate, Link, useLocation } from 'react-router-dom';
import {
  Page,
  Masthead,
  MastheadMain,
  MastheadBrand,
  MastheadContent,
  Nav,
  NavList,
  NavItem,
  Title,
  Tooltip,
} from '@patternfly/react-core';
import { InfoCircleIcon } from '@patternfly/react-icons';

import { RecentTracesPage } from './pages/RecentTracesPage';
import { TraceDetailPage } from './pages/TraceDetailPage';
import { RiskDashboardPage } from './pages/risk/RiskDashboardPage';
import { RiskTraceDetailPage } from './pages/risk/RiskTraceDetailPage';
import { RiskRulesPage } from './pages/risk/RiskRulesPage';
import { RiskRuleDetailPage } from './pages/risk/RiskRuleDetailPage';
import { navSectionFor } from './lib/navSection';

// Lean data-governance shell: a dark-themed PatternFly Page with a simple
// masthead. No auth / namespace / feature-flag chrome (out of scope per
// ADR-0019) — just the routes the trace-governance and risk UIs need.
export default function App() {
  useEffect(() => {
    document.documentElement.classList.add('pf-v5-theme-dark');
  }, []);

  const location = useLocation();
  const activeSection = navSectionFor(location.pathname);

  const masthead = (
    <Masthead>
      <MastheadMain>
        <MastheadBrand>
          {/* Logo-home convention: the brand always returns to the trace list
              (the canonical /ui/traces, basename-aware via the router).
              Redundant with the trace-detail breadcrumb and the Traces nav
              item by design — it's muscle memory. */}
          <Link to="/traces" className="dg-brand-link">
            <Title headingLevel="h1" size="lg">
              Data Governance
            </Title>
          </Link>
        </MastheadBrand>
      </MastheadMain>
      {/* Top-left region: the top-level section switcher (issue #165). A
          horizontal Nav renders role="link" items, not role="tab" — chosen so
          it can't collide with TraceDetailPage's own role="tab" switcher,
          which most of e2e/smoke.spec.ts already selects on. */}
      <MastheadContent>
        <div style={{ display: 'flex', width: '100%', alignItems: 'center' }}>
          <Nav variant="horizontal" aria-label="Primary">
            <NavList>
              {/* NavItem clones a single element child rather than rendering
                  its own <a>, which is how it picks up a router-aware <Link>
                  (basename-aware) instead of the plain href="/traces" a bare
                  `to` prop would render — that lost the /ui basename on click. */}
              <NavItem itemId="traces" isActive={activeSection === 'traces'}>
                <Link to="/traces">Traces</Link>
              </NavItem>
              <NavItem itemId="risk" isActive={activeSection === 'risk'}>
                <Link to="/risk">Risk</Link>
              </NavItem>
            </NavList>
          </Nav>
          {/* Top-right region: a small, unobtrusive build-version stamp. The
              SHA text is always shown; the icon carries a hover/focus tooltip
              with the same version so it's discoverable either way.
              __APP_VERSION__ is injected at build time (see vite.config.ts). */}
          <div style={{ display: 'flex', flex: 1, justifyContent: 'flex-end' }}>
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
        </div>
      </MastheadContent>
    </Masthead>
  );

  return (
    <Page header={masthead}>
      {/* URL = single source of truth for UI state (reload/bookmark/back). The
          trace list lives at /traces (bare / redirects there); a trace's
          detail view is a path segment — /traces/{id}/spans (span tree) or
          /flow (interaction flow) — and bare /traces/{id} canonicalises to
          /spans. The risk section mirrors this under /risk. */}
      <Routes>
        <Route path="/" element={<Navigate to="/traces" replace />} />
        <Route path="/traces" element={<RecentTracesPage />} />
        <Route path="/traces/:traceId" element={<Navigate to="spans" replace />} />
        <Route path="/traces/:traceId/:view" element={<TraceDetailPage />} />
        <Route path="/risk" element={<RiskDashboardPage />} />
        <Route path="/risk/traces/:traceId" element={<RiskTraceDetailPage />} />
        <Route path="/risk/rules" element={<RiskRulesPage />} />
        <Route path="/risk/rules/:ruleId" element={<RiskRuleDetailPage />} />
        <Route path="*" element={<Navigate to="/traces" replace />} />
      </Routes>
    </Page>
  );
}
