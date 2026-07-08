import { useRef, useState, useCallback, useEffect } from 'react';
import { useParams } from 'react-router-dom';
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
} from '@patternfly/react-core';

import { useTrace } from '../api/hooks';
import { PinStore } from '../lib/pins';
import { fetchJson } from '../api/client';
import { SpanTree } from '../components/SpanTree';
import { SpanDetailPanel } from '../components/SpanDetailPanel';
import { FlowTables } from '../components/FlowTables';
import { EntityInteractionGraph } from '../components/EntityInteractionGraph';
import { HighlightLegend } from '../components/HighlightLegend';
import type { Span } from '../types';

type ViewKey = 'tree' | 'flow' | 'graph';

/**
 * Trace-detail view: a three-way switcher (Span tree | Interaction flow |
 * Graph) over one trace. Seeds from the cold-open `useTrace` listing root
 * (deep-link / paste path). The Tree and Flow views share a highlight
 * PinStore — pinning an interaction/entity's spans in Flow stripes their rows
 * in the tree, mirroring the vanilla TraceTreeNav.
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

  const { data: entry, isLoading, isError } = useTrace(traceId);
  const root = entry?.listing_root ?? null;

  // Refresh a span in place (SpanDetailPanel's Refresh button).
  const refreshSelected = useCallback(async () => {
    if (!selectedSpan) return;
    const fresh = await fetchJson<Span>(
      `/traces/${traceId}/spans/${selectedSpan.span_id}`,
    ).catch(() => null);
    if (fresh) setSelectedSpan(fresh);
  }, [traceId, selectedSpan]);

  // Jump from a flow-view span link to that span in the tree: switch to the
  // tree view and load + select the span into the detail panel (the vanilla
  // cross-view navigateToSpan). Full ancestor auto-expand in the tree is a
  // follow-up; this restores the view switch + detail selection.
  const navigateToSpan = useCallback(
    async (spanId: string) => {
      setView('tree');
      const span = await fetchJson<Span>(
        `/traces/${traceId}/spans/${spanId}`,
      ).catch(() => null);
      if (span) setSelectedSpan(span);
    },
    [traceId],
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
      <Split hasGutter>
        <SplitItem isFilled>
          <Title headingLevel="h2" size="lg">
            Trace <span className="dg-mono">{traceId}</span>
          </Title>
        </SplitItem>
      </Split>

      <Tabs
        activeKey={view}
        onSelect={(_e, key) => setView(key as ViewKey)}
        aria-label="Trace views"
      >
        <Tab eventKey="tree" title={<TabTitleText>Span tree</TabTitleText>} />
        <Tab eventKey="flow" title={<TabTitleText>Interaction flow</TabTitleText>} />
        <Tab eventKey="graph" title={<TabTitleText>Graph</TabTitleText>} />
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
                  traceId={traceId}
                  root={root}
                  pins={pins}
                  onSelect={setSelectedSpan}
                  onPinsChange={bumpPins}
                />
              </SplitItem>
              <SplitItem style={{ minWidth: 340 }}>
                {selectedSpan ? (
                  <SpanDetailPanel span={selectedSpan} onRefresh={refreshSelected} />
                ) : (
                  <div style={{ color: '#888', fontStyle: 'italic' }}>
                    Select a span to view its details.
                  </div>
                )}
              </SplitItem>
            </Split>
          )}

          {view === 'flow' && (
            <FlowTables
              traceId={traceId}
              pins={pins}
              onPinsChange={bumpPins}
              onNavigateToSpan={navigateToSpan}
            />
          )}

          {view === 'graph' && <EntityInteractionGraph traceId={traceId} />}
        </div>
      )}
    </PageSection>
  );
}
