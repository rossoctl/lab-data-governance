import { useMemo } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import {
  PageSection,
  Title,
  Breadcrumb,
  BreadcrumbItem,
  Spinner,
  EmptyState,
  EmptyStateHeader,
  EmptyStateBody,
  Label,
} from '@patternfly/react-core';
import { useEntities } from '../../api/hooks';
import { useTraceRiskDetail, useRuleCatalogIndex } from '../../risk-api/hooks';
import { RiskApiError } from '../../risk-api/client';
import { deriveGraph } from '../../lib/graph';
import {
  toFlowEntities,
  toFlowInteractions,
  riskLevelByInteraction,
  violationsOf,
} from '../../lib/riskForestAdapter';
import { parseViolationIndex, stepViolation } from '../../lib/riskViolation';
import { EntityGraph } from '../../components/ExecutionFlowGraph';
import { PolicyDecisionPanel } from '../../risk-components/PolicyDecisionPanel';
import { ViolationStepper } from '../../risk-components/ViolationStepper';

/**
 * Trace detail — the "Alert Execution" view (issue #170, parent #167): given
 * a `trace_id`, render the interaction forest as an execution-flow graph,
 * with a policy-decision panel that steps through each triggered-rule
 * violation. Read-only, no mutation.
 *
 * REUSE, NOT REBUILD (user instruction: "reuse as much as possible... extend
 * as needed"). The forest from `GET /risk/traces/{id}` is adapted by
 * `lib/riskForestAdapter.ts` into the same `flow.Entity[]`/`flow.Interaction[]`
 * shape `lib/graph.ts`'s `deriveGraph` already consumes, so both the
 * derivation and the renderer (`EntityGraph`) are the SAME code the ordinary
 * Flow tab uses — extended additively with an optional risk-colour seam
 * (`riskLevelByInteraction`) rather than forked or replaced.
 *
 * ONE DIAGRAM, REQUEST ARROWS ONLY: this page shows the execution-flow graph
 * alone (no sequence diagram) and `toFlowInteractions` keeps only each
 * interaction's request leg, so every edge is a single caller→callee call
 * arrow — no response arrow doubling it back. See `riskForestAdapter.ts`'s
 * header for why that needed no change to `graph.ts` itself.
 *
 * Like `RiskRuleDetailPage`, this does NOT use `RiskViewShell`: the
 * breadcrumb must stay visible on every state (loading/404/error), but the
 * shell swaps `children` out entirely on `isLoading`/`isError`, which would
 * hide it. Loading/error/not-found are hand-rendered below instead.
 *
 * `?violation=` is 1-based with the default (1) omitted from the URL — see
 * `lib/riskViolation.ts`'s header for why, and note the selection is ALWAYS
 * derived from that param, never from component state, so a cold-opened
 * `?violation=N` URL and the Previous/Next controls can never disagree.
 *
 * CLICKING AN EDGE selects that interaction's violation the same way the
 * stepper does — both write the same `?violation=` param via `setViolation`,
 * so a graph click and Previous/Next can never disagree either. An edge
 * whose interaction triggered no rule has no violation index to select, so
 * `handleSelectInteraction` no-ops for it rather than clearing whatever
 * violation is currently open.
 */
export function RiskTraceDetailPage() {
  const { traceId = '' } = useParams<{ traceId: string }>();
  const [searchParams, setSearchParams] = useSearchParams();

  const detail = useTraceRiskDetail(traceId);
  const entities = useEntities(traceId);
  const catalog = useRuleCatalogIndex();

  // Memoized so its identity is stable across renders when detail.data is
  // unchanged — `?? []` would otherwise hand every useMemo below a fresh
  // array reference each render, invalidating them for no reason.
  const forest = useMemo(() => detail.data?.interactions ?? [], [detail.data]);

  // The graph derivation runs on every render (not just once violations
  // exist) so the diagram always renders, even when there are zero
  // violations — AC #5's "diagram renders, stepper absent" case.
  const flowEntities = useMemo(
    () => toFlowEntities(forest, entities.data),
    [forest, entities.data],
  );
  const flowInteractions = useMemo(() => toFlowInteractions(forest), [forest]);
  const graphSpec = useMemo(
    () => deriveGraph(flowEntities, flowInteractions),
    [flowEntities, flowInteractions],
  );
  const riskColours = useMemo(() => riskLevelByInteraction(forest), [forest]);
  const violations = useMemo(() => violationsOf(forest), [forest]);

  const violationIndex = parseViolationIndex(searchParams.get('violation'), violations.length);
  const violation = violationIndex != null ? violations[violationIndex - 1] : null;

  // Maps a clicked edge's interaction id back to its 1-based position in
  // `violations`, the same numbering `?violation=` uses — so a graph click
  // and the stepper always agree on what "violation N" means.
  const violationIndexByInteractionId = useMemo(() => {
    const map = new Map<string, number>();
    violations.forEach((v, i) => map.set(v.interaction_id, i + 1));
    return map;
  }, [violations]);

  // Clicking an edge whose interaction triggered no rule has no policy
  // decision to show — left as a no-op rather than clearing the current
  // selection, since a click that can't show anything shouldn't hide what
  // was already open.
  function handleSelectInteraction(interactionId: string | null) {
    if (interactionId == null) return;
    const index = violationIndexByInteractionId.get(interactionId);
    if (index != null) setViolation(index);
  }

  // Written by DELETING the param at the default, never by setting it
  // explicitly — the same convention `RiskDashboardPage.tsx` documents for
  // `?window=`, so the canonical URL for violation 1 stays clean.
  function setViolation(next: number) {
    setSearchParams((prev) => {
      const params = new URLSearchParams(prev);
      if (next === 1) params.delete('violation');
      else params.set('violation', String(next));
      return params;
    });
  }

  function handleStep(delta: -1 | 1) {
    if (violationIndex == null) return;
    setViolation(stepViolation(violationIndex, delta, violations.length));
  }

  const selectedInteractionId = violation?.interaction_id ?? null;

  const isNotFound =
    detail.isError && detail.error instanceof RiskApiError && detail.error.status === 404;

  return (
    <PageSection>
      <Breadcrumb>
        <BreadcrumbItem
          render={({ className }) => (
            <Link to="/risk" className={className}>
              Risk dashboard
            </Link>
          )}
        />
        <BreadcrumbItem isActive className="dg-mono">
          {traceId}
        </BreadcrumbItem>
      </Breadcrumb>

      <Title headingLevel="h2" size="xl" style={{ marginTop: '0.5rem' }}>
        Risk trace{' '}
        {detail.data && (
          <Label isCompact className="pf-v5-u-ml-sm">
            {violations.length} violation{violations.length === 1 ? '' : 's'}
          </Label>
        )}
      </Title>

      {detail.isLoading ? (
        <Spinner aria-label="Loading risk trace" />
      ) : isNotFound ? (
        <EmptyState>
          <EmptyStateHeader titleText="Trace not found" headingLevel="h4" />
          <EmptyStateBody>No trace with id {traceId} has a risk record.</EmptyStateBody>
        </EmptyState>
      ) : detail.isError ? (
        <EmptyState>
          <EmptyStateHeader titleText="Failed to load" headingLevel="h4" />
          <EmptyStateBody>Could not load this trace. Try again later.</EmptyStateBody>
        </EmptyState>
      ) : detail.data ? (
        <>
          <Title headingLevel="h3" size="lg" className="pf-v5-u-mt-lg pf-v5-u-mb-sm">
            Execution flow
          </Title>
          <EntityGraph
            traceId={traceId}
            spec={graphSpec}
            selectedInteractionId={selectedInteractionId}
            onSelectInteraction={handleSelectInteraction}
            riskLevelByInteraction={riskColours}
            hideParallelGroupsNotice
            hideEdgeLabels
            compactSurface
          />

          <Title headingLevel="h3" size="lg" className="pf-v5-u-mt-lg pf-v5-u-mb-sm">
            Policy decisions
          </Title>
          {violations.length === 0 ? (
            <EmptyState>
              <EmptyStateHeader titleText="No policy violations" headingLevel="h4" />
              <EmptyStateBody>
                No rule was triggered by any interaction in this trace.
              </EmptyStateBody>
            </EmptyState>
          ) : violation && violationIndex != null ? (
            <>
              <ViolationStepper
                position={violationIndex}
                count={violations.length}
                onStep={handleStep}
              />
              <PolicyDecisionPanel
                violation={violation}
                ruleIndex={catalog.data}
                entities={flowEntities}
              />
            </>
          ) : null}
        </>
      ) : null}
    </PageSection>
  );
}

export default RiskTraceDetailPage;
