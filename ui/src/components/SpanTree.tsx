import { useMemo, useState, useCallback } from 'react';
import { Label, Button } from '@patternfly/react-core';
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
}

/**
 * Lazy-expanding span tree. Seeds from the listing-root Span, fetches a node's
 * direct children (`/spans/{sid}/children`, keyset-paginated) on first expand,
 * and stamps the descendant-error badge on loaded ancestors of loaded error
 * spans (v1 limitation: collapsed subtrees don't propagate). Left-edge stripes
 * mark spans belonging to pinned highlight sets.
 */
export function SpanTree({ traceId, root, pins, onSelect }: SpanTreeProps) {
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

  const spans = useMemo(() => Array.from(loaded.values()), [loaded]);
  const descendantErrorKeys = useMemo(
    () => descendantErrorAncestors(spans as TreeSpan[], buildParentIndex(spans as TreeSpan[])),
    [spans],
  );

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

  const renderNode = (
    span: Span,
    depth: number,
    ancestors: ReadonlySet<string>,
  ): React.ReactNode => {
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

  return <ul style={{ margin: 0, padding: 0 }}>{renderNode(root, 0, new Set())}</ul>;
}
