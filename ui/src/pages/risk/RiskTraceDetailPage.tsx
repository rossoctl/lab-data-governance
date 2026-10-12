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
  classificationByInteraction,
  violationsOf,
} from '../../lib/riskForestAdapter';
import { parseViolationIndex, stepViolation } from '../../lib/riskViolation';
import { EntityGraph } from '../../components/ExecutionFlowGraph';
import { PolicyDecisionPanel } from '../../risk-components/PolicyDecisionPanel';
import { InteractionPanel } from '../../risk-components/InteractionPanel';
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
 * Flow tab uses — extended additively with optional risk seams rather than
 * forked or replaced: `riskLevelByInteraction` colours each edge by risk
 * level, and `classificationByInteraction` (issue #170 follow-up) replaces
 * the edge's visible seq-number label with that interaction's regulatory
 * tags (full tags + sensitivity level on hover) — see `EntityGraph`'s prop
 * docs for the precedence between that and `hideEdgeLabels` below. Both
 * seams are exclusive to this page; the Execution Flow and Lineage tabs pass
 * neither and are therefore unchanged.
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
 * `lib/riskViolation.ts`'s header for why, and note the open policy event
 * is ALWAYS derived from the URL, never from component state, so a
 * cold-opened URL and the Previous/Next controls can never disagree.
 *
 * CLICKING AN EDGE selects that interaction (issue #295): the
 * `InteractionPanel` under the graph shows its per-leg classification and
 * risk for ANY interaction. The selection is mirrored as `?interaction=`
 * with the same delete-at-default convention as `?violation=` — the default
 * being the open policy event's own interaction. So a click on an edge that
 * triggered a rule writes `?violation=` (moving the stepper, as before) and
 * deletes `?interaction=`; a click on any other edge writes `?interaction=`
 * and pins the open policy event into `?violation=` so it stays where it
 * was. Previous/Next delete `?interaction=`, so stepping always shows the
 * stepped-to interaction. On read, a `?interaction=` that names a policy
 * event takes precedence over `?violation=` (a saved link can start naming
 * one once the engine computes that interaction's risk); one that names
 * nothing in the trace falls back to the open policy event. Like
 * `?violation=`, the selection is derived from the URL, never held in state.
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
  // Regulatory tags + sensitivity level as edge labels (issue #170 follow-up)
  // — same side-channel-map shape as `riskColours` immediately above, see
  // `EntityGraph`'s `classificationByInteraction` prop doc for why this isn't
  // a `GraphEdgeSpec` field.
  const classifications = useMemo(() => classificationByInteraction(forest), [forest]);
  const violations = useMemo(() => violationsOf(forest), [forest]);

  // Maps a clicked edge's interaction id back to its 1-based position in
  // `violations`, the same numbering `?violation=` uses — so a graph click
  // and the stepper always agree on what "violation N" means.
  const violationIndexByInteractionId = useMemo(() => {
    const map = new Map<string, number>();
    violations.forEach((v, i) => map.set(v.interaction_id, i + 1));
    return map;
  }, [violations]);

  // A `?interaction=` that names a policy event wins over `?violation=`: the
  // click path never writes that URL (see header), but a link saved while the
  // interaction's risk was still null can start naming one once it is
  // computed, and the stepper must then show THAT event, not another.
  const requestedInteractionId = searchParams.get('interaction');
  const violationIndex =
    (requestedInteractionId != null ? violationIndexByInteractionId.get(requestedInteractionId) : undefined) ??
    parseViolationIndex(searchParams.get('violation'), violations.length);
  const violation = violationIndex != null ? violations[violationIndex - 1] : null;

  // A click on the graph's background deselects in `EntityGraph` (null) —
  // ignored here rather than clearing the panel, so a stray click never
  // hides what was already open. See the header on which param each kind
  // of edge writes.
  function handleSelectInteraction(interactionId: string | null) {
    if (interactionId == null) return;
    const index = violationIndexByInteractionId.get(interactionId);
    if (index != null) setViolation(index);
    else
      setSearchParams((prev) => {
        const params = new URLSearchParams(prev);
        // The open policy event stays where it is — written out explicitly,
        // because a `?interaction=` naming it may be what held it there.
        writeViolation(params, violationIndex);
        params.set('interaction', interactionId);
        return params;
      });
  }

  // Written by DELETING the param at the default, never by setting it
  // explicitly — the same convention `RiskDashboardPage.tsx` documents for
  // `?window=`, so the canonical URL for violation 1 stays clean.
  function writeViolation(params: URLSearchParams, next: number | null) {
    if (next == null || next === 1) params.delete('violation');
    else params.set('violation', String(next));
  }

  // Also deletes `?interaction=`: the stepped-to policy event's interaction
  // IS the default selection (see header).
  function setViolation(next: number) {
    setSearchParams((prev) => {
      const params = new URLSearchParams(prev);
      writeViolation(params, next);
      params.delete('interaction');
      return params;
    });
  }

  function handleStep(delta: -1 | 1) {
    if (violationIndex == null) return;
    setViolation(stepViolation(violationIndex, delta, violations.length));
  }

  // The open policy event's interaction is the default; a stale
  // `?interaction=` naming no interaction of this trace falls back to it too,
  // so the graph's highlighted edge and the panel never disagree.
  const selected = forest.find((ix) => ix.interaction_id === requestedInteractionId) ?? violation;
  const selectedInteractionId = selected?.interaction_id ?? null;

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
            {violations.length} policy event{violations.length === 1 ? '' : 's'}
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
            classificationByInteraction={classifications}
            hideParallelGroupsNotice
            // STAYS passed alongside `classificationByInteraction`, not
            // superseded by it: the map's presence — not any per-edge hit —
            // already takes precedence over this flag inside `EntityGraph`
            // (see that prop's doc), so a trace with zero classified
            // interactions still draws bare arrows here rather than falling
            // back to seq numbers just because a (empty) map exists.
            hideEdgeLabels
            compactSurface
          />
          {selected && <InteractionPanel interaction={selected} />}

          <Title headingLevel="h3" size="lg" className="pf-v5-u-mt-lg pf-v5-u-mb-sm">
            Policy events
          </Title>
          {violations.length === 0 ? (
            <EmptyState>
              <EmptyStateHeader titleText="No policy events" headingLevel="h4" />
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
