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
  DataLineageLeg,
  LineageStatus,
  TraceDataLineage,
  LineageDirection,
  LineageGraphEntity,
  LineageReachability,
  LineageSummary,
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
 * Persisted **Data lineage** for a whole trace: the per-leg lookup plus the
 * trace's coverage status. `GET /api/traces/{tid}/data-lineage` (ADR-0028, issues
 * #118 / #120).
 *
 * **One fetch per trace, not per payload.** The resource is trace-scoped while
 * the flow view's payload blocks are per-leg, so the hook unwraps `{legs:[…]}`
 * into a `(interaction_id, leg_type)` → lineage Map (ADR-0028 D5 — never keyed on
 * `payload_hash`). Every expanded payload then reads the one cached query,
 * keyed on `traceId` alone. The trace-level `status` / `stopped_at_seq` ride on
 * the same response and therefore the same query — the coverage warning and the
 * per-leg blocks are two views of one read, not two reads.
 *
 * `enabled` gates the read for callers that have nothing to show yet. Note the
 * flow view no longer gates on having a selection the way {@link usePayload}
 * gates on a hash: since #120 the coverage warning is part of the view's first
 * paint, and a truncation a reader has to click to discover is not a warning.
 *
 * Every absence is a normal shape, not an error: an empty `legs` array (trace has
 * no interactions, or the lineage migration hasn't run) yields an empty map, a
 * leg whose lineage hasn't been derived yet is a present key with a `null` value
 * (the eventual-consistency window), and a missing/absent `status` normalises to
 * `null` — *unknown*, never `'complete'`.
 */
export function useDataLineage(
  traceId: string,
  enabled = true,
): UseQueryResult<TraceDataLineage> {
  return useQuery({
    enabled,
    queryKey: ['data-lineage', traceId],
    queryFn: () =>
      fetchJson<{
        legs: DataLineageLeg[];
        status?: LineageStatus;
        stopped_at_seq?: number | null;
      }>(`/traces/${traceId}/data-lineage`).then((r) => ({
        byLeg: new Map(
          (r.legs ?? []).map((leg) => [
            legLineageKey(leg.interaction_id, leg.leg_type),
            leg.lineage,
          ]),
        ),
        // `?? null` rather than a default of `'complete'`: an older server, or a
        // DB without migration 0012, omits the field, and treating that silence
        // as full coverage is precisely the claim D6's flag exists to withhold.
        status: r.status ?? null,
        stoppedAtSeq: r.stopped_at_seq ?? null,
      })),
  });
}

/**
 * One direction's **Lineage reachability** for one entity.
 * `GET /api/traces/{tid}/entities/{eid}/data-lineage-graph?direction=…`
 * (ADR-0028 D14, edge rule in D15).
 *
 * `direction` is **required** by the server and is part of the query key, so the
 * two directions are two independently cached entries rather than one that
 * overwrites itself — which is what lets both be on screen at once.
 *
 * Deliberately NOT a hook that fetches both directions itself. `direction` is
 * single-valued on the wire, so "both" is two requests; expressing that as two
 * `useQuery` calls keeps each direction's loading and error state its own, so one
 * failing does not blank the other. {@link useLineageReachability} is the pair.
 *
 * `enabled` gates on having a seed: with nothing selected there is no question to
 * ask, and firing the request with an empty entity id would ask about an entity
 * that cannot exist.
 *
 * Every absence is a normal shape, not an error — an unknown trace or entity is a
 * 200 with an empty result (the collection-read convention). The one genuine
 * failure mode is a bad `direction`, which is a 400 and a programming error here
 * rather than a data case, since the argument is typed. Note the response is
 * passed through UNREDUCED: unlike `useDataLineage`, whose per-leg map is the
 * shape every consumer wants, `state` / `pending_frontier` / `truncated` must all
 * reach the view intact, and a reduction is exactly where a tri-state gets
 * flattened into an empty list.
 */
export function useLineageGraph(
  traceId: string,
  entityId: string | null,
  direction: LineageDirection,
): UseQueryResult<LineageReachability> {
  return useQuery({
    enabled: entityId !== null,
    queryKey: ['lineage-graph', traceId, entityId, direction],
    queryFn: () =>
      fetchJson<LineageReachability>(
        `/traces/${traceId}/entities/${entityId}/data-lineage-graph`,
        { direction },
      ),
  });
}

/**
 * Both directions of **Lineage reachability** for one entity — the pair of
 * {@link useLineageGraph} calls the Lineage tab needs.
 *
 * Two requests, because `direction` is required and single-valued (ADR-0028 D14).
 * They are two hooks rather than one combined query so that each direction keeps
 * its OWN loading and error state: fan-in and fan-out are different claims, and a
 * failed fan-out must not be able to erase a perfectly good fan-in — collapsing
 * them into one `isError` would make "we could not ask downstream" look like "we
 * know nothing at all".
 *
 * A fixed-length tuple, not an array built in a loop, so the two hooks are called
 * unconditionally and in a stable order (the rules of hooks).
 */
export function useLineageReachability(
  traceId: string,
  entityId: string | null,
): {
  fanin: UseQueryResult<LineageReachability>;
  fanout: UseQueryResult<LineageReachability>;
} {
  const fanin = useLineageGraph(traceId, entityId, 'fanin');
  const fanout = useLineageGraph(traceId, entityId, 'fanout');
  return { fanin, fanout };
}

/**
 * A trace's `list sources` / `list destinations` roll-up.
 * `GET /api/traces/{tid}/data-lineage-summary` (ADR-0028 D14).
 *
 * **This is the only supplier of the source list**, and `sources` is the union of
 * the trace's derived `data_sources` — NOT a taxonomy read of "entities declared
 * sources", which is a different set (D14). Substituting the taxonomy is the
 * specific mistake the ADR warns about, so there is no second path to this fact.
 *
 * Not gated on a selection: the trace's sources are a standing fact about the
 * trace, so the Lineage tab shows them from its first paint with nothing selected.
 * That is the same reasoning `useDataLineage`'s note gives for the coverage
 * warning — a fact a reader has to click to discover is not being reported.
 *
 * `status` / `stopped_at_seq` are renamed to the camelCase the rest of the app
 * uses (matching `useDataLineage`'s reduction) and `?? null` is *unknown*, never
 * defaulted to `'complete'` (ADR-0028 D6 "Reading the status"). Nothing else is
 * reshaped: `sources` stays natural keys and `destinations` stays entity rows,
 * because they are different grains and merging them here would be the "two views
 * of one list" error D14 names.
 */
export function useLineageSummary(traceId: string): UseQueryResult<LineageSummary> {
  return useQuery({
    queryKey: ['lineage-summary', traceId],
    queryFn: () =>
      fetchJson<{
        sources?: string[];
        destinations?: LineageGraphEntity[];
        status?: LineageStatus;
        stopped_at_seq?: number | null;
      }>(`/traces/${traceId}/data-lineage-summary`).then((r) => ({
        // `?? []` for an older server omitting either list: an absent list is an
        // empty roll-up, which is a different (and safe) claim from a missing key
        // crashing the view. It is NOT read as "no sources exist" anywhere — the
        // view says "none attributed" only alongside the coverage status.
        sources: r.sources ?? [],
        destinations: r.destinations ?? [],
        status: r.status ?? null,
        stoppedAtSeq: r.stopped_at_seq ?? null,
      })),
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
