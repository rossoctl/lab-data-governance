"""Retrieval submodule — typed read-only access to the ``spans`` table.

Part of the :mod:`data_governance.retrieval` package (see its ``__init__`` for
the seam split); this module owns the **Span** read path. The public surface is
the ``get_spans`` function plus its return shape:

- :class:`Span` — one row of the ``spans`` table, including the
  query-time ``in_time_window`` flag.
- :class:`TraceCounts` — per-trace ``{total, in_window, error_count}``
  carried alongside the ``spans`` list when ``root_only=True``.
- :class:`GetSpansResult` — wrapper of ``(spans, counts)``.

Slice history:

- Issue #4: tracer bullet — ``trace_id`` / ``span_id`` filters, ``order``,
  cursor pagination, default/max limit.
- Issue #12 (this slice): ``time_from`` / ``time_to`` window filtering,
  ``root_only=True`` listing roots with ADR-0001 fallback, per-trace
  counts, ``in_time_window`` field, parameter-compatibility raises.
- Issue #13 (future): ``parent_id`` query path. Compatibility raises for
  ``parent_id`` land here in #12 so the surface is closed.
- Issue #49 (this slice): ``Span`` widened to the full row per ADR-0006;
  adds ``ended_at``, ``observed_at``, ``arrival_seq``, ``otlp``,
  ``scope``, ``resource_attributes``.
"""

from __future__ import annotations

import datetime as dt
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator, Literal

from data_governance import db

__all__ = ["GetSpansResult", "Span", "TraceCounts", "get_spans"]

_LIMIT_DEFAULT = 50
_LIMIT_MAX = 500


# ---------------------------------------------------------------------------
# Return types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Span:
    """A single row from the ``spans`` table as returned by ``get_spans``.

    Per ADR-0006, ``Span`` carries every column of the ``spans`` table.

    **Core identity fields**

    ``seq`` — cursor-pagination watermark; may advance once on span
    finalization (ADR-0004). ``trace_id``, ``span_id`` — composite
    primary key (span_id is only unique within a trace). ``parent_id``
    — ``None`` for root spans; set for child spans. ``name`` — OTLP
    span name. ``started_at`` — producer-clock span start (UTC).
    ``attributes`` — OTLP span attributes as a dict; never ``None``
    (empty dict when the row has no attributes).
    ``service_name`` — promoted from ``resource.service.name``; ``None``
    when absent.

    **Query-time projection**

    ``in_time_window`` is ``True`` iff the span's ``started_at`` falls
    inside the request's ``(time_from, time_to)``, ``False`` only on
    out-of-window listing roots returned by ``root_only=True``. Defaults
    to ``True`` when no window was supplied.

    **Trace-tree render columns** (issue #14)

    ``kind`` — OTLP span kind string (``INTERNAL``, ``SERVER``,
    ``CLIENT``, ``PRODUCER``, ``CONSUMER``); ``None`` when absent.
    ``error`` — OTLP ``Status.Code`` projection (``ERROR → True``,
    ``OK → False``, ``UNSET → None``). ``status_message`` — only
    meaningful when ``error IS TRUE``; ``None`` otherwise.
    ``events`` and ``links`` follow the PROJECT.md §3 NULL-vs-empty
    contract: the receiver writes SQL ``NULL`` for the "no events /
    no links" case, so the dataclass surface uses ``None``.

    **Full-row fields** (issue #49 / ADR-0006)

    ``ended_at`` — span end time; ``None`` until the row is finalized
    (ADR-0004). ``observed_at`` — receiver wall-clock at INSERT time;
    ``NOT NULL`` in the schema, always a real datetime. ``arrival_seq``
    — stable per-row identifier drawn from ``spans_seq`` at INSERT;
    never updated even when ``seq`` advances on finalization;
    ``NOT NULL`` in the schema, always an integer. ``otlp`` — envelope
    of ``trace_state``, ``flags``,
    ``dropped_attributes_count``, ``dropped_events_count``,
    ``dropped_links_count``; ``None`` when absent. ``scope`` —
    instrumentation scope ``{name, version, ...}``; ``None`` when
    absent. ``resource_attributes`` — Resource attributes minus the
    promoted ``service.name``; ``None`` when absent.
    """

    seq: int
    trace_id: str
    span_id: str
    parent_id: str | None
    name: str
    started_at: dt.datetime
    attributes: dict[str, Any]
    observed_at: dt.datetime
    arrival_seq: int
    in_time_window: bool = True
    service_name: str | None = None
    kind: str | None = None
    error: bool | None = None
    status_message: str | None = None
    events: list[dict[str, Any]] | None = None
    links: list[dict[str, Any]] | None = None
    ended_at: dt.datetime | None = None
    otlp: dict[str, Any] | None = None
    scope: dict[str, Any] | None = None
    resource_attributes: dict[str, Any] | None = None


@dataclass(frozen=True)
class TraceCounts:
    """Per-trace counts ridealong on ``root_only=True`` queries.

    - ``total``: full span count for the trace at query time.
    - ``in_window``: spans whose ``started_at`` falls within the request's
      window, **excluding** the listing root if it is itself out of window.
    - ``error_count``: spans with ``error IS TRUE`` at query time.
    """

    total: int
    in_window: int
    error_count: int


@dataclass(frozen=True)
class GetSpansResult:
    """Return value of ``get_spans``.

    ``counts`` is a ``{trace_id: TraceCounts}`` map when ``root_only=True``,
    otherwise ``None``.
    """

    spans: list[Span]
    counts: dict[str, TraceCounts] | None = None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def get_spans(
    cursor: int | None = None,
    limit: int = _LIMIT_DEFAULT,
    trace_id: str | None = None,
    span_id: str | None = None,
    parent_id: str | None = None,
    time_from: dt.datetime | int | None = None,
    time_to: dt.datetime | int | None = None,
    root_only: bool = False,
    order: Literal["asc", "desc"] | None = None,
) -> GetSpansResult:
    """Read spans from the database, cursor-paginated by ``seq``.

    See PROJECT.md §6 for the full method contract.

    Parameters
    ----------
    cursor:
        Exclusive lower bound on ``seq`` (asc) or upper bound (desc).
    limit:
        Maximum rows; defaults to 50, hard-capped at 500.
    trace_id, span_id:
        Restricts the result to a trace, optionally to a single span.
    parent_id:
        Reserved for slice #13 (children-of-parent query). This slice
        only enforces parameter compatibility — passing it without
        ``trace_id`` raises, and combining it with ``root_only`` raises.
    time_from, time_to:
        Window over ``started_at``. Accepts tz-aware ``datetime`` or
        ``int`` (nanoseconds since epoch). Naive datetimes raise.
        ``None`` on either side means unbounded; both ``None`` skips
        the filter entirely.
    root_only:
        When ``True``, return one **listing root** per trace per
        ADR-0001 (real root, falling back to earliest orphan). The
        result also carries per-trace ``counts``. With ``trace_id``
        the window is **ignored** (single-trace deep-link case).
    order:
        ``"asc"`` (default) or ``"desc"``. Only meaningful for the
        bare-cursor / ``trace_id`` / ``span_id`` paths; ``root_only``
        sorts by listing-root ``started_at desc`` regardless.
    """
    _validate_params(
        limit=limit,
        trace_id=trace_id,
        span_id=span_id,
        parent_id=parent_id,
        root_only=root_only,
        order=order,
    )

    time_from_dt = _coerce_time(time_from, "time_from")
    time_to_dt = _coerce_time(time_to, "time_to")

    effective_order = (order or "asc").lower()

    if root_only:
        return _query_listing_roots(
            cursor=cursor,
            limit=limit,
            trace_id=trace_id,
            time_from=time_from_dt,
            time_to=time_to_dt,
        )

    if parent_id is not None:
        # Subtree expansion (issue #14, PROJECT.md §6 Path 3): direct
        # children of P within T, sorted seq asc and cursored on seq
        # (axes aligned — chronological by arrival, no skips/dups).
        # Parameter compatibility (parent_id requires trace_id) was
        # already enforced by _validate_params above.
        return _query_subtree(
            cursor=cursor,
            limit=limit,
            trace_id=trace_id,  # type: ignore[arg-type]
            parent_id=parent_id,
        )

    return _query_spans(
        cursor=cursor,
        limit=limit,
        trace_id=trace_id,
        span_id=span_id,
        time_from=time_from_dt,
        time_to=time_to_dt,
        order=effective_order,
    )


# ---------------------------------------------------------------------------
# Parameter validation
# ---------------------------------------------------------------------------


def _validate_params(
    *,
    limit: int,
    trace_id: str | None,
    span_id: str | None,
    parent_id: str | None,
    root_only: bool,
    order: str | None,
) -> None:
    if limit > _LIMIT_MAX:
        raise ValueError(
            f"limit {limit} exceeds the hard cap of {_LIMIT_MAX}"
        )
    if span_id is not None and trace_id is None:
        raise ValueError("span_id requires trace_id")
    if parent_id is not None and trace_id is None:
        raise ValueError("parent_id requires trace_id")
    if root_only and parent_id is not None:
        raise ValueError("root_only=True is incompatible with parent_id")
    if root_only and span_id is not None:
        raise ValueError("root_only=True is incompatible with span_id")
    if order is not None and order.lower() not in ("asc", "desc"):
        raise ValueError(f"order must be 'asc' or 'desc', got {order!r}")


def _coerce_time(
    value: dt.datetime | int | None, name: str
) -> dt.datetime | None:
    """Normalise a caller-supplied time to a tz-aware ``datetime`` or ``None``.

    Accepts:

    - ``None`` → unbounded on that side.
    - ``int`` → nanoseconds since the Unix epoch (UTC).
    - ``datetime`` with non-``None`` ``tzinfo``.

    Naive ``datetime`` raises ``ValueError`` — the library never silently
    assumes a timezone, since `started_at` is trace-clock UTC and
    misinterpreting an ambient local-time naive datetime as UTC would
    silently shift the window.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        # bool is a subclass of int; reject it explicitly to avoid
        # accidentally treating True/False as 1/0 nanoseconds.
        raise ValueError(f"{name} must be datetime or int nanoseconds, got bool")
    if isinstance(value, int):
        seconds, nanos = divmod(value, 1_000_000_000)
        micros = nanos // 1_000
        return dt.datetime.fromtimestamp(seconds, tz=dt.timezone.utc).replace(
            microsecond=micros
        )
    if isinstance(value, dt.datetime):
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError(
                f"{name} must be tz-aware; naive datetimes are rejected"
            )
        return value
    raise ValueError(
        f"{name} must be datetime or int nanoseconds, got {type(value).__name__}"
    )


# ---------------------------------------------------------------------------
# Non-root query path
# ---------------------------------------------------------------------------


_COLUMNS = (
    "seq",
    "trace_id",
    "span_id",
    "parent_id",
    "name",
    "started_at",
    "attributes",
    "service_name",
    "kind",
    "error",
    "status_message",
    "events",
    "links",
    "ended_at",
    "observed_at",
    "arrival_seq",
    "otlp",
    "scope",
    "resource_attributes",
)
_SELECT_COLS = ", ".join(_COLUMNS)


def _query_spans(
    *,
    cursor: int | None,
    limit: int,
    trace_id: str | None,
    span_id: str | None,
    time_from: dt.datetime | None,
    time_to: dt.datetime | None,
    order: str,
) -> GetSpansResult:
    sql, params = _build_query(
        cursor=cursor,
        limit=limit,
        trace_id=trace_id,
        span_id=span_id,
        time_from=time_from,
        time_to=time_to,
        order=order,
    )

    with _repeatable_read() as tx:
        rows = tx.fetch_all(sql, params)

    # Non-root path filters by the window in SQL, so every returned row is
    # by definition in-window. The only path that returns out-of-window
    # spans is ``root_only=True`` (listing roots earlier than their
    # in-window children); see :func:`_listing_roots_paginated`.
    spans = [_row_to_span(r, in_time_window=True) for r in rows]
    return GetSpansResult(spans=spans, counts=None)


def _build_query(
    *,
    cursor: int | None,
    limit: int,
    trace_id: str | None,
    span_id: str | None,
    time_from: dt.datetime | None,
    time_to: dt.datetime | None,
    order: str,
) -> tuple[str, list[Any]]:
    conditions: list[str] = []
    params: list[Any] = []

    if cursor is not None:
        if order == "asc":
            conditions.append("seq > %s")
        else:
            conditions.append("seq < %s")
        params.append(cursor)

    if trace_id is not None:
        conditions.append("trace_id = %s")
        params.append(trace_id)

    if trace_id is not None and span_id is not None:
        conditions.append("span_id = %s")
        params.append(span_id)

    if time_from is not None:
        conditions.append("started_at >= %s")
        params.append(time_from)

    if time_to is not None:
        conditions.append("started_at <= %s")
        params.append(time_to)

    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = (
        f"SELECT {_SELECT_COLS} FROM spans"
        f"{where} ORDER BY seq {order} LIMIT %s"
    )
    params.append(limit)
    return sql, params


# ---------------------------------------------------------------------------
# parent_id query path — direct children of P within T (issue #14)
# ---------------------------------------------------------------------------


def _query_subtree(
    *,
    cursor: int | None,
    limit: int,
    trace_id: str,
    parent_id: str,
) -> GetSpansResult:
    """Return direct children of ``parent_id`` within ``trace_id``.

    PROJECT.md §6 Path 3: ``parent_id`` set → sort by ``seq asc`` and
    cursor on ``seq``. Sort and cursor axes agree, so neither
    duplicates nor skips can occur on this path: each page advances
    strictly forward in ``seq`` and every row that satisfies the
    filter appears on exactly one page.

    Within a single parent's children, ``seq asc`` is chronological by
    arrival at the receiver — the property the trace-tree view
    actually wants. ``started_at`` is the producer's clock and can be
    skewed by async exporters, retries, and span buffering; sorting on
    ``started_at`` while cursoring on ``seq`` would create a
    cursor/sort-axis mismatch (a low-seq, late-started_at child can be
    pushed past the cursor and silently skipped — see #30 for the same
    bug class on the listing-roots path).

    The window is intentionally not a parameter on this path: the trace
    tree view always operates inside a single trace named by id, and the
    listing-row error-count badge already tells the user the trace has
    activity worth looking at. Filtering subtree expansion by window
    would silently hide error spans whose ``started_at`` skewed outside
    the listing window — exactly the failure the user is drilling in to
    find.
    """
    conditions: list[str] = [
        "trace_id = %s",
        "parent_id = %s",
    ]
    params: list[Any] = [trace_id, parent_id]

    if cursor is not None:
        conditions.append("seq > %s")
        params.append(cursor)

    where = " WHERE " + " AND ".join(conditions)
    sql = (
        f"SELECT {_SELECT_COLS} FROM spans"
        f"{where} ORDER BY seq ASC LIMIT %s"
    )
    params.append(limit)

    with _repeatable_read() as tx:
        rows = tx.fetch_all(sql, params)

    # Subtree expansion never filters by window, so every returned row is
    # considered in-window for the caller's purposes.
    spans = [_row_to_span(r, in_time_window=True) for r in rows]
    return GetSpansResult(spans=spans, counts=None)


# ---------------------------------------------------------------------------
# root_only=True query path
# ---------------------------------------------------------------------------


def _query_listing_roots(
    *,
    cursor: int | None,
    limit: int,
    trace_id: str | None,
    time_from: dt.datetime | None,
    time_to: dt.datetime | None,
) -> GetSpansResult:
    """Return one listing root per trace, plus per-trace counts.

    A **listing root** is the trace's earliest real root (``parent_id IS
    NULL``) if any exists, otherwise its earliest orphan (``parent_id``
    set but referenced ``(trace_id, parent_id)`` absent at query time —
    ADR-0001 ``NOT EXISTS`` check using the ``(trace_id, parent_id)``
    index).

    With ``trace_id`` set the window is intentionally ignored — the
    deep-link case in PROJECT.md §6.

    Without ``trace_id``, traces are filtered to those with at least one
    span whose ``started_at`` falls in the window. The listing root
    itself may still be out of window (an earlier real root with later
    in-window children); those are returned with
    ``in_time_window=False`` so the UI can grey them out.
    """
    if trace_id is not None:
        return _listing_root_for_single_trace(trace_id=trace_id)

    return _listing_roots_paginated(
        cursor=cursor,
        limit=limit,
        time_from=time_from,
        time_to=time_to,
    )


def _listing_root_for_single_trace(*, trace_id: str) -> GetSpansResult:
    """Single-trace listing root: ignores window, always answers.

    Listing-root SELECT and counts run inside a single REPEATABLE READ
    transaction so they observe the same snapshot.
    """
    sql = f"""
        SELECT {_SELECT_COLS}
        FROM spans s
        WHERE trace_id = %s
          AND (
            parent_id IS NULL
            OR NOT EXISTS (
              SELECT 1 FROM spans p
              WHERE p.trace_id = s.trace_id
                AND p.span_id  = s.parent_id
            )
          )
        ORDER BY started_at ASC, span_id ASC
        LIMIT 1
    """
    with _repeatable_read() as tx:
        rows = tx.fetch_all(sql, [trace_id])
        if not rows:
            return GetSpansResult(spans=[], counts={})

        # Listing root carries in_time_window=True for the deep-link case
        # (no window applied). Counts ignore the window too.
        span = _row_to_span(rows[0], in_time_window=True)
        counts = _compute_counts_for_traces(
            tx, [trace_id], time_from=None, time_to=None
        )
    return GetSpansResult(spans=[span], counts=counts)


def _listing_roots_paginated(
    *,
    cursor: int | None,
    limit: int,
    time_from: dt.datetime | None,
    time_to: dt.datetime | None,
) -> GetSpansResult:
    """Multi-trace listing-root query.

    Implementation notes:

    - We pick traces with **at least one in-window span**, then materialise
      each trace's listing root via the same earliest-real-root /
      earliest-orphan rule used by the single-trace path.
    - Sort is by listing-root ``started_at DESC, span_id ASC``. Cursor is a
      composite ``(started_at, span_id)`` keyset resolved from the caller's
      ``seq`` value. Both sort and cursor axes now agree, making the
      "skips impossible" property from ADR-0001 hold: every page advances
      strictly forward in the sort order, and no listing root that satisfies
      the filter can be excluded once the cursor is placed.
    - Counts are computed in a separate query over the same trace set;
      keeping it separate keeps the listing-root SQL readable.
    """
    # Pre-build window predicate fragments; used in two CTEs.
    window_clauses: list[str] = []
    window_params: list[Any] = []
    if time_from is not None:
        window_clauses.append("started_at >= %s")
        window_params.append(time_from)
    if time_to is not None:
        window_clauses.append("started_at <= %s")
        window_params.append(time_to)
    window_where = (
        " AND " + " AND ".join(window_clauses) if window_clauses else ""
    )

    # Step 1: traces with at least one in-window span. When no window is
    # supplied, this is "every distinct trace_id in spans".
    trace_filter_sql = f"""
        SELECT DISTINCT trace_id FROM spans
        WHERE TRUE{window_where}
    """

    # Step 2: per qualifying trace, pick the listing root: earliest real
    # root if any, else earliest orphan. We do this with a per-trace
    # ranking that prefers parent_id IS NULL over orphan rows.
    listing_root_sql = f"""
        WITH eligible AS ({trace_filter_sql}),
        candidates AS (
            SELECT s.*,
                   CASE WHEN s.parent_id IS NULL THEN 0 ELSE 1 END AS rank_class
            FROM spans s
            JOIN eligible e USING (trace_id)
            WHERE
                s.parent_id IS NULL
                OR NOT EXISTS (
                    SELECT 1 FROM spans p
                    WHERE p.trace_id = s.trace_id
                      AND p.span_id  = s.parent_id
                )
        ),
        ranked AS (
            SELECT *,
                   ROW_NUMBER() OVER (
                       PARTITION BY trace_id
                       ORDER BY rank_class ASC, started_at ASC, span_id ASC
                   ) AS rn
            FROM candidates
        )
        SELECT {_SELECT_COLS}
        FROM ranked
        WHERE rn = 1
    """

    # Composite keyset cursor: resolve caller's seq to (started_at, span_id)
    # inside the same REPEATABLE READ transaction as the main query so both
    # see the same snapshot. This ensures sort and cursor agree so no listing
    # root is silently skipped (issue #30).
    #
    # The cursor must be the seq of the *last span in sort order* on the
    # previous page — i.e. spans[-1].seq (the span with the oldest started_at
    # / highest span_id on the page), not max(seq). Using max(seq) would
    # anchor the keyset at an interior row and exclude rows that should appear
    # on the next page.
    outer_conditions: list[str] = []
    outer_params: list[Any] = list(window_params)

    with _repeatable_read() as tx:
        if cursor is not None:
            cursor_started_at, cursor_span_id = _resolve_cursor_seq_in_tx(
                tx, cursor
            )
            # Strict descending keyset: rows strictly after the cursor position
            # in (started_at DESC, span_id ASC) order.
            outer_conditions.append(
                "(started_at < %s OR (started_at = %s AND span_id > %s))"
            )
            outer_params.extend(
                [cursor_started_at, cursor_started_at, cursor_span_id]
            )

        outer_where = (
            " WHERE " + " AND ".join(outer_conditions) if outer_conditions else ""
        )

        final_sql = f"""
            SELECT {_SELECT_COLS} FROM (
                {listing_root_sql}
            ) lr
            {outer_where}
            ORDER BY started_at DESC, span_id ASC
            LIMIT %s
        """
        outer_params.append(limit)

        rows = tx.fetch_all(final_sql, outer_params)

        # Compute in_time_window per returned listing root: a listing root
        # may be returned despite being out of window if its trace has any
        # in-window span. The caller distinguishes "row to grey out" from
        # "row to render normally" by this flag.
        spans: list[Span] = []
        for r in rows:
            rec = dict(zip(_COLUMNS, r))
            in_window = _is_in_window(rec["started_at"], time_from, time_to)
            spans.append(_row_to_span(r, in_time_window=in_window))

        trace_ids = [s.trace_id for s in spans]
        counts = _compute_counts_for_traces(
            tx, trace_ids, time_from=time_from, time_to=time_to
        )
    return GetSpansResult(spans=spans, counts=counts)


def _resolve_cursor_seq_in_tx(
    tx: db.Transaction,
    seq: int,
) -> tuple[dt.datetime, str]:
    """Look up ``(started_at, span_id)`` of the span with ``seq`` within *tx*.

    Running inside the caller's REPEATABLE READ transaction ensures the lookup
    and the main listing-root query observe the same snapshot — critical because
    a span's ``seq`` can advance on finalization (ADR-0004), so a lookup in a
    separate transaction could resolve to a different span than the one the
    caller is paginating past.

    Raises ``ValueError`` when no row has the given ``seq``. In v1's
    append-only model this should not happen: the client passes back the
    ``seq`` of a span it received on a previous page, and spans are never
    deleted. If a finalized span's ``seq`` was replaced by a new value
    (ADR-0004 advancement), the old ``seq`` genuinely no longer exists and
    the cursor is stale — surfacing this as an error is safer than silently
    restarting pagination from page 1.
    """
    rows = tx.fetch_all(
        "SELECT started_at, span_id FROM spans WHERE seq = %s LIMIT 1",
        [seq],
    )
    if not rows:
        raise ValueError(
            f"cursor seq={seq} not found; the span may have been finalized "
            "and its seq advanced (ADR-0004). Restart pagination from cursor=None."
        )
    return rows[0][0], rows[0][1]


def _is_in_window(
    started_at: dt.datetime,
    time_from: dt.datetime | None,
    time_to: dt.datetime | None,
) -> bool:
    """Replicates the SQL ``started_at >= time_from AND started_at <= time_to``."""
    if time_from is None and time_to is None:
        return True
    if time_from is not None and started_at < time_from:
        return False
    if time_to is not None and started_at > time_to:
        return False
    return True


# ---------------------------------------------------------------------------
# Per-trace counts
# ---------------------------------------------------------------------------


def _compute_counts_for_traces(
    tx: db.Transaction,
    trace_ids: list[str],
    *,
    time_from: dt.datetime | None,
    time_to: dt.datetime | None,
) -> dict[str, TraceCounts]:
    """Compute ``{trace_id: TraceCounts}`` for the given trace ids.

    Runs on the caller's :class:`db.Transaction` so the counts share a
    snapshot with the listing-root SELECT that produced ``trace_ids``.

    One SQL query, returning three aggregates per ``trace_id``:

    - ``total`` = ``COUNT(*)`` — every span on the trace.
    - ``in_window`` = ``COUNT(*) FILTER (WHERE <window>)`` — spans whose
      ``started_at`` falls inside the window. The PROJECT.md §6
      "excluding the listing root when the listing root itself is out of
      window" rule is satisfied for free: an out-of-window listing root
      fails the FILTER predicate and so never contributes to
      ``in_window`` in the first place, and an in-window listing root
      *should* be counted (it is in-window).
    - ``error_count`` = ``COUNT(*) FILTER (WHERE error IS TRUE)``.
    """
    if not trace_ids:
        return {}

    filter_clauses: list[str] = []
    params: list[Any] = []
    if time_from is not None:
        filter_clauses.append("started_at >= %s")
        params.append(time_from)
    if time_to is not None:
        filter_clauses.append("started_at <= %s")
        params.append(time_to)

    if filter_clauses:
        in_window_filter = " AND ".join(filter_clauses)
        in_window_expr = f"COUNT(*) FILTER (WHERE {in_window_filter})"
    else:
        in_window_expr = "COUNT(*)"

    placeholders = ", ".join(["%s"] * len(trace_ids))
    sql = f"""
        SELECT
            trace_id,
            COUNT(*)                                        AS total,
            {in_window_expr}                                AS in_window,
            COUNT(*) FILTER (WHERE error IS TRUE)           AS error_count
        FROM spans
        WHERE trace_id IN ({placeholders})
        GROUP BY trace_id
    """
    params.extend(trace_ids)

    rows = tx.fetch_all(sql, params)
    out: dict[str, TraceCounts] = {}
    for tid, total, in_window, error_count in rows:
        out[tid] = TraceCounts(
            total=int(total),
            in_window=int(in_window),
            error_count=int(error_count),
        )
    return out


# ---------------------------------------------------------------------------
# Plumbing
# ---------------------------------------------------------------------------


@contextmanager
def _repeatable_read() -> Iterator[db.Transaction]:
    """Open a single REPEATABLE READ transaction.

    The listing-root path issues two statements (root selection + counts)
    that must observe the same snapshot — otherwise a span finalising
    between them could let ``total``/``error_count`` diverge from the
    selected listing root. Both callers therefore share one ``tx`` from
    this helper.
    """
    with db.transaction() as tx:
        tx.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        yield tx


def _row_to_span(
    row: tuple[Any, ...], *, in_time_window: bool
) -> Span:
    r = dict(zip(_COLUMNS, row))
    return Span(
        seq=r["seq"],
        trace_id=r["trace_id"],
        span_id=r["span_id"],
        parent_id=r["parent_id"],
        name=r["name"],
        started_at=r["started_at"],
        attributes=r["attributes"] or {},
        in_time_window=in_time_window,
        service_name=r.get("service_name"),
        kind=r.get("kind"),
        error=r.get("error"),
        status_message=r.get("status_message"),
        # events/links honour PROJECT.md §3 NULL contract: keep None
        # distinct from an empty list.
        events=r.get("events"),
        links=r.get("links"),
        ended_at=r.get("ended_at"),
        observed_at=r["observed_at"],
        arrival_seq=r["arrival_seq"],
        otlp=r.get("otlp"),
        scope=r.get("scope"),
        resource_attributes=r.get("resource_attributes"),
    )
