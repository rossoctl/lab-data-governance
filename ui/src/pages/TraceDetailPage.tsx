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
import { parseLegViewKey, parseLineageSource, type LegViewKey } from '../lib/flow';
import { HighlightLegend } from '../components/HighlightLegend';
import type { Span } from '../types';

// The active view is a URL path segment. We keep an internal ViewKey for render
// branches, mapped from/to the URL word so the URL stays the source of truth. The two
// maps are inverses; an unknown segment resolves to null and is canonicalised to /spans
// by the guard below.
//
// FIVE PEER VIEWS, which REVERSES an earlier decision recorded here. The Interaction
// diagram, the Execution Flow graph and the Lineage highlight used to be nested INSIDE
// the flow view as its `?legs=diagram` / `?legs=graph` / `?legs=lineage` sub-tabs, on
// the reasoning that they are presentations of the flow view's own two reads rather
// than peer datasets of the span tree. That reasoning is still true as a statement
// about the DATA — and it turned out to be the wrong basis for the NAVIGATION: three
// of the five readings of a trace were two clicks deep and invisible until you found
// the Interaction flow tab, so the requirement is that they sit beside Span tree and
// Interaction flow as equals.
//
// WHAT STAYED NESTED, and why the nesting did not simply disappear: `Tree` and `Flat`
// are two renderings of ONE table (the same rows, indented vs flattened), so they
// remain `?legs` sub-tabs under Interaction flow. Promoting those two as well would put
// a tab called "Tree" beside one called "Span tree" as if they were peers of comparable
// weight, which they are not.
//
// `/traces/{id}/graph` is a real segment again (it was one historically, then was
// removed when the graph moved into `?legs`). Old `?legs=` deep links still resolve —
// see LEGACY_LEGS_TO_VIEW and the redirect effect.
type ViewKey = 'tree' | 'flow' | 'diagram' | 'graph' | 'lineage';
const VIEW_TO_URL: Record<ViewKey, string> = {
  tree: 'spans',
  flow: 'flow',
  diagram: 'diagram',
  graph: 'graph',
  lineage: 'lineage',
};
const URL_TO_VIEW: Record<string, ViewKey> = {
  spans: 'tree',
  flow: 'flow',
  diagram: 'diagram',
  graph: 'graph',
  lineage: 'lineage',
};

/**
 * Old `?legs=` values that are now their own path segment → the view they became.
 *
 * BOOKMARKS AND SHARED LINKS ARE THE POINT. `?legs=graph` and `?legs=lineage` were
 * URL-visible for their whole life, and this codebase's own rule is that "here is what
 * I was looking at" has to be a link (see the `?src` note below). Silently landing an
 * old link on the default tab would break exactly the deep-linking the params exist to
 * provide.
 *
 * `tree` and `flat` are deliberately ABSENT: they are still real `?legs` values under
 * the flow view, so they must not be redirected anywhere.
 */
const LEGACY_LEGS_TO_VIEW: Record<string, ViewKey> = {
  diagram: 'diagram',
  graph: 'graph',
  lineage: 'lineage',
};

/**
 * Trace-detail view: a FIVE-way switcher over one trace — Span tree | Interaction flow
 * | Interaction diagram | Execution Flow | Lineage. The first is the spans; the other
 * four are readings of the same entities/interactions pair of reads, which is why they
 * all render through one `FlowTables` (see the ViewKey note above for what moved up here
 * and why the tables' own Tree|Flat stayed nested).
 *
 * The active view (a PATH SEGMENT), the tree's selected span (`?sel`), the flow's
 * selected interaction/entity (`?iid` / `?eid`), the flow view's own Tree|Flat choice
 * (`?legs`) and the Lineage view's traced data source (`?src`) all
 * live in the URL, so reload / bookmark / back restore exactly what's on screen.
 * Seeds from the cold-open `useTrace` listing root (deep-link / paste path). The
 * Tree and Flow views share a highlight PinStore — pinning an
 * interaction/entity's spans in Flow stripes their rows in the tree, mirroring
 * the vanilla TraceTreeNav.
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
  // Navigate to a sibling tab. The QUERY IS DROPPED for the span tree (a `?sel` from
  // the tree is meaningless in the other views, and vice versa) but the four
  // INTERACTION views — flow, diagram, graph, lineage — are readings of ONE dataset and
  // share their params, so switching among them carries `?iid` / `?eid` / `?src`.
  //
  // This matters concretely: the three promoted views used to be sub-tabs, where
  // switching between them was a `?legs` write that preserved the rest of the query by
  // construction. Now they are path segments, so preserving it is a thing this function
  // has to do on purpose — otherwise moving Execution Flow → Lineage would silently
  // discard the chosen source and the selected entity, and the reader would arrive at a
  // bare prompt having just been looking at an answer.
  const goToView = useCallback(
    (v: ViewKey) => {
      const isInteractionView = (k: ViewKey) => k !== 'tree';
      const keep = isInteractionView(v) && view !== null && isInteractionView(view);
      const carried = new URLSearchParams(keep ? searchParams : undefined);
      // `?legs` belongs to the flow view's own sub-tabs; carrying it onto a promoted
      // view would leave a dead param that the legacy redirect above would then bounce.
      if (v !== 'flow') carried.delete('legs');
      const qs = carried.toString();
      navigate(`../${VIEW_TO_URL[v]}${qs ? `?${qs}` : ''}`, { relative: 'path' });
    },
    [navigate, searchParams, view],
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
  // revealedSelRef records the sel the tree already revealed, so re-renders
  // don't re-fire reveal. It is cleared when we leave the tree view: the tree
  // stays MOUNTED (hidden, not unmounted — see the render below), so its expand
  // state persists across tab switches, but a fresh deep-link / Add-to-
  // highlights reveal to the same ?sel after a round-trip should still fire, so
  // we forget the marker on leave.
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
      revealedSelRef.current = null; // left the tree → allow a later re-reveal
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
  // The flow view's Interactions tab (?legs): Tree | Flat | Interaction diagram |
  // Execution Flow | Lineage. `tree` is the default and writes no param — same
  // drop-the-default rule the list view's ?window uses, so a canonical URL never
  // carries `?legs=tree`. Anything unrecognised reads as `tree` rather than
  // throwing, matching parseWindowKey's coercion; the coercion itself lives in
  // lib/flow next to the type it coerces to, so this read and the tab bar's
  // onSelect share one definition of what a valid value is.
  //
  // Nothing here enumerates the non-default values: the read goes through
  // `parseLegViewKey` and the write is "drop the param iff it is the default", so a
  // new presentation needs only the type and the tab — which is why adding
  // `diagram`, and then `lineage`, touched this file's comments and nothing else.
  const legView: LegViewKey = parseLegViewKey(searchParams.get('legs'));
  const handleLegViewChange = useCallback(
    (key: LegViewKey) => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (key === 'tree') next.delete('legs');
          else next.set('legs', key);
          return next;
        },
        // `replace` so flipping between the two presentations of one dataset
        // doesn't stack history entries the Back button has to walk through.
        { replace: true },
      );
    },
    [setSearchParams],
  );

  // The Lineage tab's traced DATA SOURCE (?src): the **Entity natural key** of the
  // ONE source whose flow the reachability walk follows. In the URL for the same
  // reason `?legs` and `?eid` are — a reload, a bookmark or a shared link restores
  // the whole reading of the trace, and on a governance surface "here is what I was
  // looking at" has to be a link rather than a sequence of clicks to reproduce.
  //
  // `parseLineageSource` does only the syntactic half (absent or blank → null, so
  // `?src=` cannot become a request for a source named empty string). The SEMANTIC
  // half — is this a source THIS trace actually has? — cannot be answered here: only
  // the trace's own `data-lineage-summary` knows, and that read lives in the tab. So
  // a stale value is passed DOWN rather than dropped, and
  // `lineageReachability.resolveSourceChoice` reports it as its own `'stale'` state
  // with a notice. That is the same coercion discipline `?legs` follows (never throw,
  // never trust) with the validation pushed to the only place that can perform it —
  // and it is why an unknown `?src` reads as "not one of this trace's sources"
  // instead of silently showing an unexplained bare prompt.
  //
  // NO default and NO auto-pick. Unlike `?legs`, whose default is `tree`, there is no
  // source this page is entitled to choose on the reader's behalf: an unrequested
  // highlight is a claim nobody asked for (see resolveSourceChoice's note). Absent
  // therefore stays absent, and the tab renders an instruction.
  /**
   * Which presentation `FlowTables` actually renders.
   *
   * TWO SOURCES, ONE ANSWER. For the three promoted views the PATH decides (a
   * `ViewKey` of `diagram`/`graph`/`lineage` is also a `LegViewKey` of the same name —
   * they are the same five presentations, which is what made the promotion a
   * navigation change rather than a rewrite). For the flow view, `?legs` decides
   * between its surviving Tree|Flat.
   *
   * Resolved HERE rather than inside `FlowTables` so that component keeps taking one
   * `legView` prop and does not have to know that some of its presentations are now
   * addressed by path and others by query — it renders what it is told.
   *
   * Written as an explicit three-way test rather than a lookup map so no cast is
   * needed: inside the true branch TypeScript has NARROWED `view` to exactly the three
   * keys that are also `LegViewKey`s, which is what makes the correspondence a checked
   * fact instead of an asserted one. Everything else — the flow view, the span tree,
   * and `null` from an unknown segment — falls through to `?legs`. The latter two never
   * reach `FlowTables` (the tree renders its own view; `null` is redirected by the
   * guard below), so the fallback only has to be well-formed, not meaningful.
   */
  const effectiveLegView: LegViewKey =
    view === 'diagram' || view === 'graph' || view === 'lineage' ? view : legView;

  const lineageSource: string | null = parseLineageSource(searchParams.get('src'));
  const handleLineageSourceChange = useCallback(
    (source: string) => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          // Always written, never dropped-as-default: there is no default source, so
          // every value is a real choice worth carrying. The picker offers no "clear",
          // which is why there is no delete arm here — see LineageSourcePicker.
          next.set('src', source);
          return next;
        },
        // `replace`, matching `?legs`: switching which source you are tracing is
        // re-reading one dataset, not navigating, and stacking a history entry per
        // source would make Back walk through every source the reader tried.
        { replace: true },
      );
    },
    [setSearchParams],
  );

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

  // LEGACY DEEP LINKS: `/flow?legs=graph` (and `diagram` / `lineage`) were the URLs
  // for three views that are now their own path segment. Redirect rather than drop, so
  // an existing bookmark or shared link still lands on the reading it named — see
  // LEGACY_LEGS_TO_VIEW.
  //
  // Checked BEFORE the unknown-segment guard below and gated on the flow view, because
  // that is the only place those params were ever written. `?legs=tree` / `?legs=flat`
  // fall through untouched: they are still live values of the surviving sub-tab bar.
  //
  // Every OTHER param is carried across (`?iid`, `?eid`, `?src`), minus `legs` itself —
  // a link to a Lineage view with a chosen source has to keep the source, or the
  // redirect would silently answer a different question than the link asked.
  const legacyLegsView = view === 'flow' ? LEGACY_LEGS_TO_VIEW[searchParams.get('legs') ?? ''] : undefined;
  if (legacyLegsView) {
    const carried = new URLSearchParams(searchParams);
    carried.delete('legs');
    const qs = carried.toString();
    return (
      <Navigate
        to={`../${VIEW_TO_URL[legacyLegsView]}${qs ? `?${qs}` : ''}`}
        relative="path"
        replace
      />
    );
  }

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
                  size="md"
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
        {/* THE FIVE PEER READINGS OF ONE TRACE. `Interaction flow` is the TABLES (with
            its own Tree|Flat sub-tabs, the two renderings of one row set); the three
            after it are the pictures. They were `?legs` sub-tabs of the flow view until
            the requirement moved them up here — see the ViewKey note for what that
            reversed and why the tables' own two stayed nested.

            THE ORDER IS THE ARGUMENT, inherited from the sub-tab bar these three came
            from (it is stated here now rather than there, so there is one copy of it):

            `Interaction diagram` sits immediately after Interaction flow because it is
            the Flat list read down the page — it renders that list's rows, in that
            order, from the same `flatRows` derivation — with the graph's who-called-whom
            axis laid out horizontally. It is the shared middle of its two neighbours
            rather than an unrelated fifth thing.

            `Lineage` sits LAST, immediately after Execution Flow, because it IS the
            Execution Flow picture with one more question asked of it: identical nodes
            and edges (one component, one `deriveGraph` — see ExecutionFlowGraph's
            `EntityGraph`), plus a highlight of where ONE chosen data source's data
            reached the selected entity from and went to. Anywhere earlier would separate
            it from the view it is a reading of. */}
        <Tab eventKey="flow" title={<TabTitleText>Interaction flow</TabTitleText>} />
        <Tab eventKey="diagram" title={<TabTitleText>Interaction diagram</TabTitleText>} />
        <Tab eventKey="graph" title={<TabTitleText>Execution Flow</TabTitleText>} />
        <Tab eventKey="lineage" title={<TabTitleText>Lineage</TabTitleText>} />
      </Tabs>

      {isLoading ? (
        <Spinner aria-label="Loading trace" />
      ) : isError || !root ? (
        <Alert variant="warning" title="Trace not found" isInline>
          This trace has no spans (it may still be draining, or the id is wrong).
        </Alert>
      ) : (
        <div style={{ marginTop: '1rem' }}>
          {/* The tree stays MOUNTED for the page's lifetime and is merely hidden
              on the flow tab (display:none), so its expand/load/selection state
              survives a tab round-trip — otherwise switching to flow would
              unmount it and a return would re-seed from the root (collapsed to
              depth 1), discarding a prior reveal's expanded ancestor chains. */}
          <div style={{ display: view === 'tree' ? undefined : 'none' }}>
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
          </div>

          {/* ALL FOUR INTERACTION VIEWS RENDER THROUGH ONE `FlowTables`, which is the
              component that owns the interactions/entities reads, the evidence fetch,
              the pin state and the single notion of "selected interaction/entity". The
              three promoted views are presentations of exactly that state (which is why
              they were sub-tabs in the first place), so giving each its own top-level
              component would mean three more owners of one selection — the duplication
              the whole flow view is built to avoid.
              What changed is only where the CHOICE comes from: the path segment for the
              three promoted views, `?legs` for the flow view's own Tree|Flat. */}
          {view !== 'tree' && (
            <FlowTables
              traceId={traceId}
              pins={pins}
              onPinsChange={bumpPins}
              onNavigateToSpan={navigateToSpan}
              onRevealSpans={revealInTree}
              initialSelection={flowSelection}
              onSelectionChange={handleFlowSelectionChange}
              legView={effectiveLegView}
              onLegViewChange={handleLegViewChange}
              // The sub-tab bar (Tree|Flat) is only meaningful on the flow view; the
              // three promoted views ARE the presentation, so a bar offering to switch
              // presentation from inside one of them would be a second control for what
              // the top-level tabs now decide.
              showLegTabs={view === 'flow'}
              lineageSource={lineageSource}
              onLineageSourceChange={handleLineageSourceChange}
            />
          )}
        </div>
      )}
    </PageSection>
  );
}
