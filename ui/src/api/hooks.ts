/**
 * TanStack Query hooks for the `/api/` resource tree (ADR-0018).
 *
 * Each hook wraps {@link fetchJson}, owns its URL construction and unwrapping
 * (e.g. `{traces:[...]}` → the array), and keys the query on its inputs so the
 * cache invalidates correctly. Retrieval policy (staleTime, retry) is the
 * QueryClient's job (see main.tsx).
 */
import {
  useQuery,
  useInfiniteQuery,
  type UseQueryResult,
  type UseInfiniteQueryResult,
  type InfiniteData,
} from '@tanstack/react-query';
import { fetchJson, type QueryParams } from './client';
import { legLineageKey } from '../lib/flow';
import type {
  Span,
  TraceListingEntry,
  SpanEvidence,
  Payload,
  Entity,
  Interaction,
  DataLineageByLeg,
  DataLineageLeg,
} from '../types';

/** Recent-traces feed. `GET /api/traces` → the `traces` array. */
export function useTraces(params: {
  time_from?: string;
  time_to?: string;
  cursor?: number;
  limit?: number;
}): UseQueryResult<TraceListingEntry[]> {
  return useQuery({
    queryKey: ['traces', params],
    queryFn: () =>
      fetchJson<{ traces: TraceListingEntry[] }>('/traces', params as QueryParams).then(
        (r) => r.traces,
      ),
  });
}

const TRACES_PAGE_SIZE = 20;

/**
 * Paginated recent-traces feed. Accumulates cursor pages across "Load more"
 * (the vanilla accumulate-then-dedupe design): each page's cursor is the seq of
 * its last listing root, and getNextPageParam returns undefined once a short
 * page signals the end. The time window is frozen in the query key at fetch
 * start, so it does not drift per render.
 */
export function useTracesInfinite(window: {
  time_from?: string;
  time_to?: string;
}): UseInfiniteQueryResult<InfiniteData<TraceListingEntry[]>> {
  return useInfiniteQuery({
    queryKey: ['traces-infinite', window],
    initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam }) =>
      fetchJson<{ traces: TraceListingEntry[] }>('/traces', {
        limit: TRACES_PAGE_SIZE,
        ...window,
        ...(pageParam !== undefined ? { cursor: pageParam } : {}),
      } as QueryParams).then((r) => r.traces),
    getNextPageParam: (lastPage) => {
      // A short page means no more; otherwise page forward from the last
      // listing root's seq (the server's composite keyset cursor, issue #30).
      if (lastPage.length < TRACES_PAGE_SIZE) return undefined;
      return lastPage[lastPage.length - 1]?.listing_root.seq;
    },
  });
}

/** One TraceListingEntry (cold-open seed). `GET /api/traces/{tid}`. */
export function useTrace(traceId: string): UseQueryResult<TraceListingEntry> {
  return useQuery({
    queryKey: ['trace', traceId],
    queryFn: () => fetchJson<TraceListingEntry>(`/traces/${traceId}`),
  });
}

/** One Span. `GET /api/traces/{tid}/spans/{sid}` (backs the Refresh action). */
export function useSpan(traceId: string, spanId: string): UseQueryResult<Span> {
  return useQuery({
    queryKey: ['span', traceId, spanId],
    queryFn: () => fetchJson<Span>(`/traces/${traceId}/spans/${spanId}`),
  });
}

/**
 * Direct children of a span, keyset-paginated by seq (ADR-0001). The tree's
 * lazy-expansion source. `GET /api/traces/{tid}/spans/{sid}/children`.
 */
export function useSpanChildren(
  traceId: string,
  spanId: string,
  params: { cursor?: number; limit?: number } = {},
): UseQueryResult<Span[]> {
  return useQuery({
    queryKey: ['span-children', traceId, spanId, params],
    queryFn: () =>
      fetchJson<{ spans: Span[] }>(
        `/traces/${traceId}/spans/${spanId}/children`,
        params as QueryParams,
      ).then((r) => r.spans),
  });
}

/** Derived interactions for a trace. `GET /api/traces/{tid}/interactions`. */
export function useInteractions(traceId: string): UseQueryResult<Interaction[]> {
  return useQuery({
    queryKey: ['interactions', traceId],
    queryFn: () =>
      fetchJson<{ interactions: Interaction[] }>(
        `/traces/${traceId}/interactions`,
      ).then((r) => r.interactions),
  });
}

/** Derived entities for a trace. `GET /api/traces/{tid}/entities`. */
export function useEntities(traceId: string): UseQueryResult<Entity[]> {
  return useQuery({
    queryKey: ['entities', traceId],
    queryFn: () =>
      fetchJson<{ entities: Entity[] }>(`/traces/${traceId}/entities`).then(
        (r) => r.entities,
      ),
  });
}

/** Span-evidence for one interaction (lazy, on drill-in). */
export function useInteractionSpans(
  traceId: string,
  interactionId: string,
  enabled = true,
): UseQueryResult<SpanEvidence[]> {
  return useQuery({
    enabled,
    queryKey: ['interaction-spans', traceId, interactionId],
    queryFn: () =>
      fetchJson<{ spans: SpanEvidence[] }>(
        `/traces/${traceId}/interactions/${interactionId}/spans`,
      ).then((r) => r.spans),
  });
}

/** Span-evidence for one entity (lazy, on drill-in). */
export function useEntitySpans(
  traceId: string,
  entityId: string,
  enabled = true,
): UseQueryResult<SpanEvidence[]> {
  return useQuery({
    enabled,
    queryKey: ['entity-spans', traceId, entityId],
    queryFn: () =>
      fetchJson<{ spans: SpanEvidence[] }>(
        `/traces/${traceId}/entities/${entityId}/spans`,
      ).then((r) => r.spans),
  });
}

/**
 * Persisted **Data lineage** for a whole trace, reduced to a per-leg lookup.
 * `GET /api/traces/{tid}/data-lineage` (ADR-0027, issue #118).
 *
 * **One fetch per trace, not per payload.** The resource is trace-scoped while
 * the flow view's payload blocks are per-leg, so the hook unwraps `{legs:[…]}`
 * into a `(interaction_id, leg_type)` → lineage Map (ADR-0027 D5 — never keyed on
 * `payload_hash`). Every expanded payload then reads the one cached query,
 * keyed on `traceId` alone.
 *
 * `enabled` gates the read the same way {@link usePayload} gates on its hash: the
 * flow view only needs lineage once a row's detail panel is open, so an
 * untouched table costs nothing.
 *
 * Both absences are normal shapes, not errors: an empty `legs` array (trace has
 * no interactions, or the lineage migration hasn't run) yields an empty map, and
 * a leg whose lineage hasn't been derived yet is a present key with a `null`
 * value (the eventual-consistency window).
 */
export function useDataLineage(
  traceId: string,
  enabled = true,
): UseQueryResult<DataLineageByLeg> {
  return useQuery({
    enabled,
    queryKey: ['data-lineage', traceId],
    queryFn: () =>
      fetchJson<{ legs: DataLineageLeg[] }>(`/traces/${traceId}/data-lineage`).then(
        (r) =>
          new Map(
            (r.legs ?? []).map((leg) => [
              legLineageKey(leg.interaction_id, leg.leg_type),
              leg.lineage,
            ]),
          ),
      ),
  });
}

/** A payload by content hash. `GET /api/payloads/{hash}` (backs Req/Resp cells). */
export function usePayload(hash: string | null): UseQueryResult<Payload> {
  return useQuery({
    enabled: hash !== null,
    queryKey: ['payload', hash],
    queryFn: () => fetchJson<Payload>(`/payloads/${hash}`),
  });
}
