"""Layer 2 retrieval API — typed read-only access to the spans table.

This is the tracer-bullet stage of the retrieval library introduced in
issue #4. It exposes ``get_spans`` and the supporting types ``Span`` and
``GetSpansResult``.

Out of scope here, by design:
- ``parent_id``, ``time_from``, ``time_to``, ``root_only`` — slices #11, #13.
- Per-trace ``counts`` — slice #11.
- ``in_time_window`` field on ``Span`` — slice #13.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

from data_governance import db

__all__ = ["GetSpansResult", "Span", "get_spans"]

_LIMIT_DEFAULT = 50
_LIMIT_MAX = 500


@dataclass(frozen=True)
class Span:
    """A single row from the ``spans`` table as returned by ``get_spans``."""

    seq: int
    trace_id: str
    span_id: str
    parent_id: str | None
    name: str
    started_at: dt.datetime
    attributes: dict[str, Any]


@dataclass(frozen=True)
class GetSpansResult:
    """Return value of ``get_spans``.

    ``counts`` is always ``None`` at this tracer-bullet stage; the
    per-trace counts shape is reserved for slice #11.
    """

    spans: list[Span]
    counts: None = None


def get_spans(
    cursor: int | None = None,
    limit: int = _LIMIT_DEFAULT,
    trace_id: str | None = None,
    span_id: str | None = None,
    order: str | None = None,
) -> GetSpansResult:
    """Read spans from the database, cursor-paginated by ``seq``.

    Parameters
    ----------
    cursor:
        Exclusive lower bound on ``seq`` (asc) or upper bound (desc).
        ``None`` starts from the beginning (asc) or end (desc).
    limit:
        Maximum number of rows to return. Defaults to 50; hard cap 500.
        Values above 500 raise ``ValueError``.
    trace_id:
        When set, restrict results to spans with this ``trace_id``.
    span_id:
        When set alongside ``trace_id``, return the single matching span
        (or an empty list). Ignored when ``trace_id`` is ``None``.
    order:
        ``"asc"`` (default) or ``"desc"``. Controls the ``seq`` sort
        direction and which side of the cursor the query reads.
    """
    if limit > _LIMIT_MAX:
        raise ValueError(
            f"limit {limit} exceeds the hard cap of {_LIMIT_MAX}"
        )

    effective_order = (order or "asc").lower()
    if effective_order not in ("asc", "desc"):
        raise ValueError(f"order must be 'asc' or 'desc', got {order!r}")

    sql, params = _build_query(cursor, limit, trace_id, span_id, effective_order)

    with db.transaction() as tx:
        tx.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        rows = tx.fetch_all(sql, params)

    spans = [_row_to_span(r) for r in rows]
    return GetSpansResult(spans=spans, counts=None)


# ---------------------------------------------------------------------------
# Query builder
# ---------------------------------------------------------------------------

_SELECT = """
    SELECT seq, trace_id, span_id, parent_id, name, started_at, attributes
    FROM spans
"""


def _build_query(
    cursor: int | None,
    limit: int,
    trace_id: str | None,
    span_id: str | None,
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

    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = f"{_SELECT}{where} ORDER BY seq {order} LIMIT %s"
    params.append(limit)
    return sql, params


def _row_to_span(row: tuple[Any, ...]) -> Span:
    seq, trace_id, span_id, parent_id, name, started_at, attributes = row
    if attributes is None:
        attributes = {}
    return Span(
        seq=seq,
        trace_id=trace_id,
        span_id=span_id,
        parent_id=parent_id,
        name=name,
        started_at=started_at,
        attributes=attributes,
    )
