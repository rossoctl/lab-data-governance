"""Layer 2 retrieval API — typed read-only access to the spans table.

The public surface is the ``get_spans`` function plus its return shape:

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

    ``in_time_window`` is a query-time projection: ``True`` iff the span's
    ``started_at`` falls inside the request's ``(time_from, time_to)``,
    ``False`` only on out-of-window listing roots returned by
    ``root_only=True``. Defaults to ``True`` when no window was supplied.
    """

    seq: int
    trace_id: str
    span_id: str
    parent_id: str | None
    name: str
    started_at: dt.datetime
    attributes: dict[str, Any]
    in_time_window: bool = True
    service_name: str | None = None


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
    - Sort is by listing-root ``started_at DESC``. Cursor is on the
      listing-root ``seq``; the cursor and sort disagree on this path
      (ADR-0001's "duplicates possible, skips impossible" property).
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

    # Outer wrap: cursor + ORDER BY listing-root started_at desc + LIMIT.
    outer_conditions: list[str] = []
    outer_params: list[Any] = list(window_params)
    if cursor is not None:
        # Cursor on seq. On a started_at-desc sort the cursor still serves
        # the "skips impossible" property for the seq-cursored stream.
        outer_conditions.append("seq < %s")
        outer_params.append(cursor)

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

    # Listing-root SELECT and counts must observe the same snapshot,
    # otherwise a span finalising between them can let total/error_count
    # diverge from the selected listing root. Run both in one
    # REPEATABLE READ transaction.
    with _repeatable_read() as tx:
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
    )
