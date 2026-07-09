import { useRef, useState, useCallback, useEffect } from 'react';
import { useParams, Link } from 'react-router-dom';
import {
  PageSection,
  Title,
  Tabs,
  Tab,
  TabTitleText,
  Spinner,
  Split,
  SplitItem,
  Alert,
  Breadcrumb,
  BreadcrumbItem,
} from '@patternfly/react-core';

import { useTrace } from '../api/hooks';
import { PinStore } from '../lib/pins';
import { fetchJson } from '../api/client';
import { SpanTree, type SpanTreeHandle } from '../components/SpanTree';
import { SpanDetailPanel } from '../components/SpanDetailPanel';
import { FlowTables } from '../components/FlowTables';
import { HighlightLegend } from '../components/HighlightLegend';
import type { Span } from '../types';

type ViewKey = 'tree' | 'flow';

/**
 * Trace-detail view: a two-way switcher (Span tree | Interaction flow) over
 * one trace. Seeds from the cold-open `useTrace` listing root (deep-link /
 * paste path). The Tree and Flow views share a highlight PinStore — pinning
 * an interaction/entity's spans in Flow stripes their rows in the tree,
 * mirroring the vanilla TraceTreeNav.
 */
export function TraceDetailPage() {
  const { traceId = '' } = useParams<{ traceId: string }>();
  const [view, setView] = useState<ViewKey>('tree');
  const [selectedSpan, setSelectedSpan] = useState<Span | null>(null);

  // One PinStore for the page; a version counter forces re-render on mutation
  // (the store is mutable by design, shared across Tree + Flow).
  const pinsRef = useRef(new PinStore());
  const [pinVersion, setPinVersion] = useState(0);
  const bumpPins = useCallback(() => setPinVersion((v) => v + 1), []);
  const pins = pinsRef.current;

  // Imperative handle to the tree, plus a pending reveal request. Switching to
  // the tree view may mount SpanTree fresh, so we stash the span ids and fire
  // reveal from an effect once the tree view is active and the ref is present.
  const treeRef = useRef<SpanTreeHandle>(null);
  const [pendingReveal, setPendingReveal] = useState<string[] | null>(null);

  const { data: entry, isLoading, isError } = useTrace(traceId);
  const root = entry?.listing_root ?? null;

  // Ask the tree to reveal a set of spans: switch to the tree view; the effect
  // below runs reveal once the tree is mounted.
  const revealInTree = useCallback((spanIds: string[]) => {
    if (spanIds.length === 0) return;
    setView('tree');
    setPendingReveal(spanIds);
  }, []);

  useEffect(() => {
    if (view !== 'tree' || !pendingReveal) return;
    // Defer to the next tick so the freshly-switched tree has mounted and its
    // imperative handle is attached.
    const id = requestAnimationFrame(() => {
      treeRef.current?.reveal(pendingReveal);
      setPendingReveal(null);
    });
    return () => cancelAnimationFrame(id);
  }, [view, pendingReveal]);

  // Refresh a span in place (SpanDetailPanel's Refresh button).
  const refreshSelected = useCallback(async () => {
    if (!selectedSpan) return;
    const fresh = await fetchJson<Span>(
      `/traces/${traceId}/spans/${selectedSpan.span_id}`,
    ).catch(() => null);
    if (fresh) setSelectedSpan(fresh);
  }, [traceId, selectedSpan]);

  // Jump from a flow-view span link to that span in the tree: switch to the
  // tree view and reveal the span (auto-expanding its ancestors); the tree's
  // reveal() also selects it, which flows back through onSelect into the
  // detail panel.
  const navigateToSpan = useCallback(
    (spanId: string) => {
      revealInTree([spanId]);
    },
    [revealInTree],
  );

  // Esc clears every highlight set while the tree view is active (the vanilla
  // clearAllPins keyboard shortcut).
  useEffect(() => {
    if (view !== 'tree') return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        pins.clearAll();
        bumpPins();
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [view, pins, bumpPins]);

  // The PinStore is mutable by design; bumpPins (via pinVersion state) forces a
  // re-render on every add/remove, so reading getPins() directly on render
  // always reflects the current pins. Referencing pinVersion keeps the read
  // after the state bump that triggered the render.
  void pinVersion;
  const pinViews = pins.getPins();

  return (
    <PageSection>
      {/* Two-node hierarchy (list → one trace): the breadcrumb is the
          in-page back-affordance and the "you are here" indicator. Crumb 1
          links to the list root via the router; crumb 2 is the full trace id
          (the one place the complete id lives, as a copy target). */}
      <Breadcrumb>
        <BreadcrumbItem
          render={({ className }) => (
            <Link to="/" className={className}>
              Recent traces
            </Link>
          )}
        />
        <BreadcrumbItem isActive className="dg-mono">
          {traceId}
        </BreadcrumbItem>
      </Breadcrumb>

      <Title headingLevel="h2" size="lg" style={{ marginTop: '0.5rem' }}>
        Trace detail
      </Title>

      <Tabs
        activeKey={view}
        onSelect={(_e, key) => setView(key as ViewKey)}
        aria-label="Trace views"
      >
        <Tab eventKey="tree" title={<TabTitleText>Span tree</TabTitleText>} />
        <Tab eventKey="flow" title={<TabTitleText>Interaction flow</TabTitleText>} />
      </Tabs>

      {isLoading ? (
        <Spinner aria-label="Loading trace" />
      ) : isError || !root ? (
        <Alert variant="warning" title="Trace not found" isInline>
          This trace has no spans (it may still be draining, or the id is wrong).
        </Alert>
      ) : (
        <div style={{ marginTop: '1rem' }}>
          {view === 'tree' && (
            <Split hasGutter>
              <SplitItem isFilled>
                <HighlightLegend pins={pinViews} onRemove={(k) => { pins.removePin(k); bumpPins(); }} />
                <SpanTree
                  ref={treeRef}
                  traceId={traceId}
                  root={root}
                  pins={pins}
                  onSelect={setSelectedSpan}
                  onPinsChange={bumpPins}
                />
              </SplitItem>
              {/* Fixed 30% width, non-resizable; the panel always renders
                  (its own empty state stands in when nothing is selected). */}
              <SplitItem style={{ flex: '0 0 30%', minWidth: 0 }}>
                <SpanDetailPanel span={selectedSpan} onRefresh={refreshSelected} />
              </SplitItem>
            </Split>
          )}

          {view === 'flow' && (
            <FlowTables
              traceId={traceId}
              pins={pins}
              onPinsChange={bumpPins}
              onNavigateToSpan={navigateToSpan}
              onRevealSpans={revealInTree}
            />
          )}
        </div>
      )}
    </PageSection>
  );
}
