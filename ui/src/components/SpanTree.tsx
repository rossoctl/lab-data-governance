import {
  useMemo,
  useState,
  useCallback,
  useEffect,
  useRef,
  forwardRef,
  useImperativeHandle,
} from 'react';
import { Label, Button, Checkbox } from '@patternfly/react-core';
import { fetchJson } from '../api/client';
import {
  buildParentIndex,
  descendantErrorAncestors,
  spanKey,
  type TreeSpan,
} from '../lib/traceTree';
import type { PinStore } from '../lib/pins';
import type { Span } from '../types';

const PAGE_SIZE = 50;

/** Imperative surface the parent drives (cross-view reveal). */
export interface SpanTreeHandle {
  /**
   * Best-effort reveal: for each span id, load + expand its ancestor chain so
   * the row becomes visible, then scroll the first target into view and select
   * it. Spans whose lineage can't be resolved are skipped silently.
   */
  reveal(spanIds: string[]): Promise<void>;
}

// Per-kind glyph, ported from the vanilla KIND_GLYPH table.
const KIND_GLYPH: Record<string, string> = {
  INTERNAL: '■',
  SERVER: '≡',
  CLIENT: '→',
  PRODUCER: '↑',
  CONSUMER: '↓',
};

export interface SpanTreeProps {
  traceId: string;
  root: Span;
  pins: PinStore;
  onSelect: (span: Span) => void;
  /** Re-render trigger when pins change elsewhere (legend/flow). */
  onPinsChange: () => void;
  /**
   * Client-side service filter: the selected `service_name`s, or null for all.
   * The page owns the URL mirror (ADR-0021 `?svc=`, comma-separated; absent =
   * all) and passes the parsed list down.
   */
  serviceFilter?: string[] | null;
  /** Fired when a service checkbox toggles; null = all services selected. */
  onServiceFilterChange?: (services: string[] | null) => void;
}

/**
 * Lazy-expanding span tree. Seeds from the listing-root Span, fetches a node's
 * direct children (`/spans/{sid}/children`, keyset-paginated) on first expand,
 * and stamps the descendant-error badge on loaded ancestors of loaded error
 * spans (v1 limitation: collapsed subtrees don't propagate). Left-edge stripes
 * mark spans belonging to pinned highlight sets.
 */
export const SpanTree = forwardRef<SpanTreeHandle, SpanTreeProps>(function SpanTree(
  { traceId, root, pins, onSelect, serviceFilter = null, onServiceFilterChange },
  ref,
) {
  // All spans loaded so far, keyed by (trace_id|span_id).
  const [loaded, setLoaded] = useState<Map<string, Span>>(
    () => new Map([[spanKey(root.trace_id, root.span_id), root]]),
  );
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [childrenOf, setChildrenOf] = useState<Map<string, string[]>>(new Map());
  // Parents whose children are fully paged in (last page was < PAGE_SIZE). A
  // parent absent from this set with a loaded page of exactly PAGE_SIZE has
  // more children behind a "Load more" affordance.
  const [exhausted, setExhausted] = useState<Set<string>>(new Set());
  const [selectedKey, setSelectedKey] = useState<string | null>(null);

  // Live DOM refs for rendered span rows, keyed by span key — reveal() scrolls
  // the first target into view. Kept current by renderNode via the ref
  // callback (rows removed on collapse delete their entry).
  const rowEls = useRef<Map<string, HTMLElement>>(new Map());

  const spans = useMemo(() => Array.from(loaded.values()), [loaded]);
  const descendantErrorKeys = useMemo(
    () => descendantErrorAncestors(spans as TreeSpan[], buildParentIndex(spans as TreeSpan[])),
    [spans],
  );

  // Distinct service names among the spans loaded so far — the checkbox options
  // (the option list grows as more of the tree pages in).
  const services = useMemo(
    () =>
      Array.from(
        new Set(spans.map((s) => s.service_name).filter((s): s is string => s != null)),
      ).sort(),
    [spans],
  );
  // null = all selected (no ?svc param).
  const selectedServices = useMemo(
    () => (serviceFilter === null ? null : new Set(serviceFilter)),
    [serviceFilter],
  );
  const toggleService = (svc: string, checked: boolean) => {
    const next = new Set(selectedServices ?? services);
    if (checked) next.add(svc);
    else next.delete(svc);
    // All present services selected collapses back to null (the param-less
    // default), so re-checking the last box yields a clean URL.
    onServiceFilterChange?.(services.every((s) => next.has(s)) ? null : Array.from(next).sort());
  };

  // Fetch one page of a parent's children (keyset-paginated by seq, ADR-0001)
  // and append it. `cursor` is the max seq of the children already loaded, so
  // the next page continues past the last one. Marks the parent exhausted when
  // a short page comes back.
  const loadChildrenPage = useCallback(
    async (span: Span) => {
      const k = spanKey(span.trace_id, span.span_id);
      const existing = childrenOf.get(k) ?? [];
      const cursor =
        existing.length > 0
          ? Math.max(
              ...existing
                .map((ck) => loaded.get(ck)?.seq ?? -Infinity)
                .filter((n) => Number.isFinite(n)),
            )
          : undefined;
      const page = await fetchJson<{ spans: Span[] }>(
        `/traces/${traceId}/spans/${span.span_id}/children`,
        { limit: PAGE_SIZE, ...(cursor !== undefined ? { cursor } : {}) },
      )
        .then((r) => r.spans)
        .catch(() => [] as Span[]);
      setLoaded((prev) => {
        const next = new Map(prev);
        for (const c of page) next.set(spanKey(c.trace_id, c.span_id), c);
        return next;
      });
      setChildrenOf((prev) => {
        const next = new Map(prev);
        next.set(k, [...(prev.get(k) ?? []), ...page.map((c) => spanKey(c.trace_id, c.span_id))]);
        return next;
      });
      if (page.length < PAGE_SIZE) {
        setExhausted((prev) => new Set(prev).add(k));
      }
    },
    [traceId, childrenOf, loaded],
  );

  const expand = useCallback(
    async (span: Span) => {
      const k = spanKey(span.trace_id, span.span_id);
      // Toggle collapse if already expanded.
      if (expanded.has(k)) {
        setExpanded((prev) => {
          const next = new Set(prev);
          next.delete(k);
          return next;
        });
        return;
      }
      // Fetch the first page of children on first expand.
      if (!childrenOf.has(k)) {
        await loadChildrenPage(span);
      }
      setExpanded((prev) => new Set(prev).add(k));
    },
    [expanded, childrenOf, loadChildrenPage],
  );

  const select = useCallback(
    (span: Span) => {
      setSelectedKey(spanKey(span.trace_id, span.span_id));
      onSelect(span);
    },
    [onSelect],
  );

  // Auto-expand the root on mount so the user sees the first level without a
  // click, mirroring the vanilla trace_tree renderRoot() (issue #14). Guarded
  // by a ref keyed on the root so it fires exactly once per trace — not on
  // StrictMode's double-invoke, and not fighting a later user collapse of the
  // root. `expand` (a toggle) is safe here because the root starts collapsed.
  const autoExpandedRoot = useRef<string | null>(null);
  useEffect(() => {
    const rootKey = spanKey(root.trace_id, root.span_id);
    if (autoExpandedRoot.current === rootKey) return;
    autoExpandedRoot.current = rootKey;
    void expand(root);
  }, [root, expand]);

  // Latest state snapshots for the imperative reveal (which runs a sequential
  // async walk and must observe its own freshly-fetched data, not a stale
  // closure). Refs are updated on every render below.
  const loadedRef = useRef(loaded);
  const childrenOfRef = useRef(childrenOf);
  const exhaustedRef = useRef(exhausted);
  loadedRef.current = loaded;
  childrenOfRef.current = childrenOf;
  exhaustedRef.current = exhausted;

  const reveal = useCallback(
    async (spanIds: string[]) => {
      // Local working copies seeded from current state; all fetches accumulate
      // here and are merged back in one batch at the end (no awaiting React
      // state between hops).
      const wLoaded = new Map(loadedRef.current);
      const wChildren = new Map(childrenOfRef.current);
      const wExhausted = new Set(exhaustedRef.current);
      const toExpand = new Set<string>();

      // Fetch a single span by id into wLoaded if absent. Returns it or null.
      const ensureSpan = async (spanId: string): Promise<Span | null> => {
        const inMap = [...wLoaded.values()].find((s) => s.span_id === spanId);
        if (inMap) return inMap;
        const fetched = await fetchJson<Span>(`/traces/${traceId}/spans/${spanId}`).catch(
          () => null,
        );
        if (fetched) wLoaded.set(spanKey(fetched.trace_id, fetched.span_id), fetched);
        return fetched;
      };

      // Ensure `childKey` is listed under `parent` in wChildren so the tree
      // renders it. Pages forward (keyset by seq) until the child appears or
      // the parent is exhausted. Because keyset paging only moves forward, a
      // target whose seq is BELOW an already-loaded sibling (parent partially
      // paged before reveal) can't be reached that way — so as a fallback we
      // splice the already-fetched child span (we hold it via ensureSpan)
      // directly into the list. The tree renders from wChildren, so this makes
      // it visible even when forward paging skipped it.
      const ensureChildLoaded = async (parent: Span, childKey: string) => {
        const pk = spanKey(parent.trace_id, parent.span_id);
        for (let guard = 0; guard < 1000; guard++) {
          if ((wChildren.get(pk) ?? []).includes(childKey)) return;
          if (wExhausted.has(pk)) break; // fully paged; fall through to splice
          const existing = wChildren.get(pk) ?? [];
          const cursor =
            existing.length > 0
              ? Math.max(
                  ...existing
                    .map((ck) => wLoaded.get(ck)?.seq ?? -Infinity)
                    .filter((n) => Number.isFinite(n)),
                )
              : undefined;
          const page = await fetchJson<{ spans: Span[] }>(
            `/traces/${traceId}/spans/${parent.span_id}/children`,
            { limit: PAGE_SIZE, ...(cursor !== undefined ? { cursor } : {}) },
          )
            .then((r) => r.spans)
            .catch(() => [] as Span[]);
          for (const c of page) wLoaded.set(spanKey(c.trace_id, c.span_id), c);
          wChildren.set(pk, [...existing, ...page.map((c) => spanKey(c.trace_id, c.span_id))]);
          if (page.length < PAGE_SIZE) wExhausted.add(pk);
        }
        // Fallback: forward paging couldn't surface the child (lower-seq, or a
        // short/failed page). If we already hold the child span, list it so it
        // still renders. (A "Load more" affordance stays if not exhausted.)
        if (!(wChildren.get(pk) ?? []).includes(childKey) && wLoaded.has(childKey)) {
          wChildren.set(pk, [...(wChildren.get(pk) ?? []), childKey]);
        }
      };

      const firstTargets: string[] = [];
      for (const targetId of spanIds) {
        // Resolve the ancestor chain, fetching any unloaded hop.
        const leaf = await ensureSpan(targetId);
        if (!leaf) continue;
        const chain: Span[] = [leaf];
        const seen = new Set<string>([spanKey(leaf.trace_id, leaf.span_id)]);
        let cursor = leaf.parent_id;
        while (cursor) {
          const parent = await ensureSpan(cursor);
          if (!parent) break; // orphan / unresolvable — best-effort stop
          const pk = spanKey(parent.trace_id, parent.span_id);
          if (seen.has(pk)) break; // cycle guard
          seen.add(pk);
          chain.push(parent);
          cursor = parent.parent_id;
        }
        // Walk root→…→leaf: expand every ancestor and make sure the next hop's
        // row is paged in under it.
        chain.reverse();
        for (let i = 0; i < chain.length - 1; i++) {
          const parent = chain[i];
          const childKey = spanKey(chain[i + 1].trace_id, chain[i + 1].span_id);
          toExpand.add(spanKey(parent.trace_id, parent.span_id));
          await ensureChildLoaded(parent, childKey);
        }
        firstTargets.push(spanKey(leaf.trace_id, leaf.span_id));
      }

      // One batched merge → a single re-render with everything revealed.
      // Functional updaters fold the working copy over the LATEST state, so any
      // expand/loadChildrenPage the user triggered during reveal's awaits
      // survives (a plain `set(wCopy)` would clobber it with the stale seed).
      setLoaded((prev) => new Map([...prev, ...wLoaded]));
      setChildrenOf((prev) => new Map([...prev, ...wChildren]));
      setExhausted((prev) => new Set([...prev, ...wExhausted]));
      setExpanded((prev) => {
        const next = new Set(prev);
        toExpand.forEach((k) => next.add(k));
        return next;
      });

      // Select + scroll the first target after the DOM updates. Resolve this
      // promise only AFTER the scroll rAF fires, so a caller bracketing a
      // "highlighting…" spinner around reveal() keeps it up until the revealed
      // row is actually painted and scrolled into view (not merely queued).
      const firstKey = firstTargets[0];
      if (!firstKey) return;
      const first = wLoaded.get(firstKey);
      if (first) select(first);
      await new Promise<void>((resolve) => {
        requestAnimationFrame(() => {
          rowEls.current.get(firstKey)?.scrollIntoView({ block: 'nearest' });
          resolve();
        });
      });
    },
    [traceId, select],
  );

  useImperativeHandle(ref, () => ({ reveal }), [reveal]);

  const renderNode = (
    span: Span,
    depth: number,
    ancestors: ReadonlySet<string>,
  ): React.ReactNode => {
    // Service filter: a deselected service's span renders neither its row nor
    // its subtree (the branch is pruned, not just the row). Spans with no
    // service_name are never filtered — they have no checkbox to re-enable them.
    if (selectedServices && span.service_name != null && !selectedServices.has(span.service_name)) {
      return null;
    }
    const k = spanKey(span.trace_id, span.span_id);
    // Cycle guard: if this span is already on the path from the root (a
    // self-loop or A→B→A parent_id, which orphan/malformed lineage can
    // produce), render it without recursing so a bad trace can't stack-overflow.
    if (ancestors.has(k)) {
      return (
        <li key={k} style={{ listStyle: 'none', margin: 0 }}>
          <div
            data-testid="span-row"
            style={{ paddingLeft: depth * 20 + 4, color: '#888', fontStyle: 'italic' }}
          >
            ↻ {span.name || '(unnamed)'} (cycle)
          </div>
        </li>
      );
    }
    const childAncestors = new Set(ancestors).add(k);
    const isExpanded = expanded.has(k);
    const kidKeys = childrenOf.get(k) ?? [];
    // First pinned-set color striping this span's row (a span may be in several
    // sets; the border shows the lowest-slot color).
    const stripe = pins.spanColors(span.span_id)[0] ?? null;
    return (
      <li key={k} style={{ listStyle: 'none', margin: 0 }}>
        <div
          data-testid="span-row"
          data-span-key={k}
          ref={(el) => {
            if (el) rowEls.current.set(k, el);
            else rowEls.current.delete(k);
          }}
          role="button"
          tabIndex={0}
          onClick={() => select(span)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault();
              select(span);
            }
          }}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            padding: '2px 4px',
            paddingLeft: depth * 20 + 4,
            cursor: 'pointer',
            borderLeft: stripe ? `3px solid ${stripe}` : '3px solid transparent',
            background: selectedKey === k ? '#3a4a66' : undefined,
          }}
        >
          <Button
            variant="plain"
            aria-label={`expand ${span.name}`}
            onClick={(e) => {
              e.stopPropagation();
              expand(span);
            }}
            style={{ padding: 0, minWidth: '1rem' }}
          >
            {isExpanded ? '▼' : '▶'}
          </Button>
          <span className="dg-mono" title={span.kind ?? 'INTERNAL'}>
            {KIND_GLYPH[span.kind ?? 'INTERNAL'] ?? KIND_GLYPH.INTERNAL}
          </span>
          <span style={{ fontWeight: 600 }}>{span.name || '(unnamed)'}</span>
          {span.service_name && (
            <span style={{ color: '#888', fontSize: '0.85em' }}>{span.service_name}</span>
          )}
          {span.error === true && (
            <Label color="red" isCompact>
              Error
            </Label>
          )}
          {descendantErrorKeys.has(k) && span.error !== true && (
            <Label color="gold" isCompact>
              Child error
            </Label>
          )}
        </div>
        {isExpanded && (
          <ul style={{ margin: 0, paddingLeft: 0 }}>
            {kidKeys
              .map((ck) => loaded.get(ck))
              .filter((s): s is Span => s !== undefined)
              .map((child) => renderNode(child, depth + 1, childAncestors))}
            {/* Wide-fanout parent: a full page came back and there may be more.
                Offer a "Load more" affordance that pages forward by seq. */}
            {kidKeys.length > 0 && !exhausted.has(k) && (
              <li style={{ listStyle: 'none' }}>
                <Button
                  variant="link"
                  isInline
                  onClick={() => loadChildrenPage(span)}
                  style={{ paddingLeft: (depth + 1) * 20 + 4 }}
                >
                  Load more children
                </Button>
              </li>
            )}
          </ul>
        )}
      </li>
    );
  };

  return (
    <>
      {/* Service filter row — only worth screen space once ≥2 services have
          loaded (the app-framework-span firehose case); a lone service has
          nothing to narrow. Sits above the tree so it stays reachable even
          when the current selection filters out every row. */}
      {services.length > 1 && (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            flexWrap: 'wrap',
            gap: '0 1rem',
            marginBottom: '0.5rem',
          }}
        >
          <span style={{ color: '#888', fontSize: '0.85em' }}>Services:</span>
          {services.map((svc) => (
            <Checkbox
              key={svc}
              id={`svc-${svc}`}
              label={svc}
              isChecked={selectedServices === null || selectedServices.has(svc)}
              onChange={(_e, checked) => toggleService(svc, checked)}
            />
          ))}
        </div>
      )}
      <ul style={{ margin: 0, padding: 0 }}>{renderNode(root, 0, new Set())}</ul>
    </>
  );
});
