import { useRef, useState, useCallback, useEffect } from 'react';
import { useParams, useSearchParams, useNavigate, Navigate, Link } from 'react-router-dom';
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
import { PinStore, colorForSlot } from '../lib/pins';
import { fetchJson } from '../api/client';
import { SpanTree, type SpanTreeHandle } from '../components/SpanTree';
import { SpanDetailPanel } from '../components/SpanDetailPanel';
import { FlowTables, type FlowSelection } from '../components/FlowTables';
import { HighlightLegend } from '../components/HighlightLegend';
import type { Span } from '../types';

// The active view is a URL path segment: `spans` (span tree) or `flow`
// (interaction flow). We keep an internal ViewKey for render branches, mapped
// from/to the URL word so the URL stays the source of truth.
type ViewKey = 'tree' | 'flow';
const VIEW_TO_URL: Record<ViewKey, string> = { tree: 'spans', flow: 'flow' };
const URL_TO_VIEW: Record<string, ViewKey> = { spans: 'tree', flow: 'flow' };

/**
 * Trace-detail view: a two-way switcher (Span tree | Interaction flow) over
 * one trace. The active tab, the tree's selected span (`?sel`), and the flow's
 * selected interaction/entity (`?iid` / `?eid`) all live in the URL, so
 * reload / bookmark / back restore exactly what's on screen. Seeds from the
 * cold-open `useTrace` listing root (deep-link / paste path). The Tree and Flow
 * views share a highlight PinStore — pinning an interaction/entity's spans in
 * Flow stripes their rows in the tree, mirroring the vanilla TraceTreeNav.
 */
export function TraceDetailPage() {
  const { traceId = '', view: viewParam } = useParams<{ traceId: string; view: string }>();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();

  // An unknown view segment canonicalises to /spans (guards typo'd deep links).
  // Rendered before any state hooks below run for the bad value; the hooks
  // still run (Rules of Hooks) but their output is discarded by the redirect.
  const view: ViewKey | null = viewParam ? (URL_TO_VIEW[viewParam] ?? null) : null;

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

  // "highlighting…" spinner: true while a user-initiated cross-view reveal
  // (Add-to-highlights / an evidence Span-link) is in flight — from the moment
  // it is requested (covering the flow→tree tab switch + tree mount) until
  // reveal() settles (ancestors expanded, first target scrolled into view). A
  // monotonic token guards overlapping reveals: only the latest clears it, so a
  // fast earlier reveal can't hide the spinner while a later one still runs. The
  // ?sel deep-link/reload reveal does NOT set this — it is a page-load restore,
  // not a highlight action, so it shows no spinner.
  const [isRevealing, setIsRevealing] = useState(false);
  const revealTokenRef = useRef(0);

  const { data: entry, isLoading, isError } = useTrace(traceId);
  const root = entry?.listing_root ?? null;

  const selParam = searchParams.get('sel');

  // Navigate to a sibling tab, dropping the query (a ?sel from the tree is
  // meaningless in flow, and vice versa). Relative to the current path so the
  // trace id is preserved.
  const goToView = useCallback(
    (v: ViewKey) => {
      navigate(`../${VIEW_TO_URL[v]}`, { relative: 'path' });
    },
    [navigate],
  );

  // Ask the tree to reveal a set of spans: navigate to the spans tab with the
  // first target as ?sel (the deep-link contract), and stash the full set so
  // the reveal effect expands every evidence span's ancestors — not just the
  // one the URL names. Used by the flow → tree span-link jump and
  // Add-to-highlights.
  const revealInTree = useCallback(
    (spanIds: string[]) => {
      if (spanIds.length === 0) return;
      // Show the spinner from the instant the reveal is requested, so it covers
      // the tab switch + tree (re)mount latency, not just the async walk. The
      // token marks THIS request as the latest; the reveal effect clears the
      // spinner only if its token still matches when reveal() settles.
      revealTokenRef.current += 1;
      setIsRevealing(true);
      setPendingReveal(spanIds);
      navigate(`../spans?sel=${encodeURIComponent(spanIds[0])}`, { relative: 'path' });
    },
    [navigate],
  );

  // URL → tree: when the spans tab is active and ?sel names a span, reveal it.
  // If a multi-span reveal is pending (Add-to-highlights), reveal the whole set;
  // otherwise reveal just the ?sel span (deep-link / reload / back restore).
  // revealedSelRef records the sel the CURRENTLY MOUNTED tree already shows, so
  // re-renders don't re-fire reveal — but it is cleared whenever we leave the
  // tree view, because SpanTree unmounts there (view-gated below) and loses all
  // its expand/select state. Without the reset, tab→flow→Back to the same ?sel
  // would find the marker still set against a freshly-remounted empty tree and
  // skip the reveal, leaving the span collapsed and unselected.
  //
  // The effect also depends on `root`: on a cold deep-link the trace is still
  // loading, so SpanTree (gated on `root`) is not yet mounted and treeRef is
  // null; we must re-run once the tree mounts. We only advance revealedSelRef /
  // clear pendingReveal AFTER reveal actually ran against a mounted tree — a
  // no-op reveal (treeRef null) must not mark the target done, or it would
  // block the retry and the span would never surface.
  const revealedSelRef = useRef<string | null>(null);
  useEffect(() => {
    if (view !== 'tree') {
      revealedSelRef.current = null; // tree unmounted → forget what it showed
      // Leaving the tree cancels any in-flight reveal (its target row is gone),
      // so drop the "highlighting…" spinner rather than let it hang. Bump the
      // token so a superseded reveal's late .finally() can't clear a spinner a
      // FUTURE reveal turned back on.
      revealTokenRef.current += 1;
      setIsRevealing(false);
      return;
    }
    if (!selParam || !root) return;
    const spanIds = pendingReveal && pendingReveal[0] === selParam ? pendingReveal : [selParam];
    if (revealedSelRef.current === selParam && !pendingReveal) return;
    // Defer to the next tick so the freshly-mounted tree's imperative handle is
    // attached before we call it.
    const id = requestAnimationFrame(() => {
      const tree = treeRef.current;
      if (!tree) return; // not mounted yet — a later render (root/view) retries
      // Snapshot the current reveal token; clear the "highlighting…" spinner
      // only if no newer reveal was requested by the time this one settles
      // (latest-reveal-wins). reveal() always settles (best-effort, no throw
      // path) and now resolves AFTER its scroll rAF, so the spinner stays up
      // until the revealed row is painted and scrolled — then hides.
      const token = revealTokenRef.current;
      void tree.reveal(spanIds).finally(() => {
        if (revealTokenRef.current === token) setIsRevealing(false);
      });
      revealedSelRef.current = selParam;
      setPendingReveal(null);
    });
    return () => cancelAnimationFrame(id);
  }, [view, selParam, pendingReveal, root]);

  // Tree → URL: a row click (via SpanTree.onSelect) mirrors the selected span
  // into ?sel. `replace` so row-to-row clicks don't spam the history stack.
  // Also keeps the detail panel's selectedSpan in sync.
  const handleSpanSelect = useCallback(
    (span: Span) => {
      setSelectedSpan(span);
      revealedSelRef.current = span.span_id; // came from the tree; don't re-reveal
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          // ?sel belongs to /spans; drop any flow params so the URL stays
          // canonical (mirrors handleFlowSelectionChange dropping ?sel).
          next.delete('iid');
          next.delete('eid');
          next.set('sel', span.span_id);
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );

  // Refresh a span in place (SpanDetailPanel's Refresh button).
  const refreshSelected = useCallback(async () => {
    if (!selectedSpan) return;
    const fresh = await fetchJson<Span>(
      `/traces/${traceId}/spans/${selectedSpan.span_id}`,
    ).catch(() => null);
    if (fresh) setSelectedSpan(fresh);
  }, [traceId, selectedSpan]);

  // Jump from a flow-view span link to that span in the tree: navigate to the
  // spans tab and reveal the span (auto-expanding its ancestors); the tree's
  // reveal() also selects it, which flows back through onSelect into the detail
  // panel and ?sel.
  const navigateToSpan = useCallback(
    (spanId: string) => {
      revealInTree([spanId]);
    },
    [revealInTree],
  );

  // Flow selection ↔ URL (?iid / ?eid). The parent owns the URL mirror;
  // FlowTables owns its internal selection (evidence fetch + panel).
  const flowSelection: FlowSelection = {
    iid: searchParams.get('iid') ?? undefined,
    eid: searchParams.get('eid') ?? undefined,
  };
  const handleFlowSelectionChange = useCallback(
    (sel: FlowSelection | null) => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          next.delete('iid');
          next.delete('eid');
          if (sel?.iid) next.set('iid', sel.iid);
          else if (sel?.eid) next.set('eid', sel.eid);
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams],
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

  // Unknown view segment → canonical spans tab (after the hooks above, per the
  // Rules of Hooks; their results are simply discarded by this redirect).
  if (view === null) {
    return <Navigate to="../spans" relative="path" replace />;
  }

  return (
    <PageSection>
      {/* Two-node hierarchy (list → one trace): the breadcrumb is the
          in-page back-affordance and the "you are here" indicator. Crumb 1
          links to the list at /traces via the router; crumb 2 is the full trace
          id (the one place the complete id lives, as a copy target). */}
      <Breadcrumb>
        <BreadcrumbItem
          render={({ className }) => (
            <Link to="/traces" className={className}>
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
        activeKey={VIEW_TO_URL[view]}
        onSelect={(_e, key) => goToView(URL_TO_VIEW[String(key)] ?? 'tree')}
        aria-label="Trace views"
      >
        <Tab
          eventKey="spans"
          title={
            <TabTitleText>
              Span tree
              {isRevealing && (
                <Spinner
                  size="sm"
                  aria-label="highlighting…"
                  // Color the wheel with the first highlight-set stripe color
                  // (slot 0) so it reads clearly on the dark tab bar and ties
                  // visually to what's being highlighted. PF draws the spinner
                  // stroke from --pf-v5-c-spinner--Color, so we set that.
                  style={
                    {
                      marginLeft: '0.5rem',
                      verticalAlign: 'middle',
                      '--pf-v5-c-spinner--Color': colorForSlot(0),
                    } as React.CSSProperties
                  }
                />
              )}
            </TabTitleText>
          }
        />
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
                  onSelect={handleSpanSelect}
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
              initialSelection={flowSelection}
              onSelectionChange={handleFlowSelectionChange}
            />
          )}
        </div>
      )}
    </PageSection>
  );
}
