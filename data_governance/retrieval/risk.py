"""Retrieval submodule — the typed read path over the DAS risk tables
(``interaction_risk_records`` / ``trace_risk_records``, migration 0016/0018).

Part of the :mod:`data_governance.retrieval` package (see its ``__init__``
for the seam split). Sibling to :mod:`.interactions`; where that reads the
derived flow forest, this reads the DAS risk pipeline's write-once, versioned
risk computations (issue #101's ``risk/engine/compute.py`` and #102's
``trace_compute.py``).

This slice (issue #109) covers both the interaction-risk half
(:func:`list_interaction_risk`, :func:`get_interaction_risk`,
:func:`get_interaction_risk_history`), the trace-risk half
(:func:`list_trace_risk`, :func:`get_trace_risk`,
:func:`get_trace_risk_history`), and the trace forest read
(:func:`get_trace_risk_detail`), which joins the risk tables against the
derived flow forest (:mod:`.interactions`'s ``interactions`` /
``interaction_legs`` / ``interaction_spans``, migrations 0004/0009).

Both tables are insert-only and versioned (a recompute never mutates a row —
it inserts a new ``version``), so every read here reduces to "latest version
per key" via ``DISTINCT ON (key) ... ORDER BY key, version DESC`` — the same
convention :mod:`.interactions` and ``risk/engine/*`` already use. Filtering
happens strictly AFTER that reduction: filtering the raw table first could
surface a superseded version that happens to match while the current version
does not (AC-DAS-016).

Pagination is DB-level keyset (a cursor carries the last-seen row's actual
sort-key values, not a numeric offset) — an offset cursor is wrong for a
growing, insert-only-and-versioned table: a recompute lands a new "latest"
row for some key between pages and reorders ``computed_at DESC``, so an
offset-based page silently skips or double-serves rows. See
``retrieval/spans.py``'s issue #30 note for the same failure class. The seam
takes/returns a **decoded** cursor dict (a plain ``{field: value}`` mapping);
base64 encode/decode stays in the HTTP layer (``risk/api/http.py``), matching
how :func:`.spans.get_spans` takes an ``int`` seq rather than a token.

``trace_compute._CURRENT_INTERACTION_RISK_SQL``'s "latest current row per
interaction" shape is duplicated here, not imported: that query selects a
narrower column set tailored to trace-aggregation input, and the two are
expected to diverge as each evolves for its own caller.

Type conversion behind the seam, not in the HTTP layer's ``_json_default``:
``Decimal -> float`` (``overall_confidence`` is ``NUMERIC(4,3)``, exactly
representable in float64 well past display precision) and ``UUID -> str``.
Extending ``_json_default`` for this would silently change ``/risk/rules``'
encoding behaviour for a risk-endpoint-specific reason.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from data_governance import db
from data_governance.risk.rules.catalog import RISK_LEVEL_ORDER

__all__ = [
    "SORT_COMPUTED_AT_DESC",
    "SORT_RISK_LEVEL_DESC",
    "InteractionRiskView",
    "InteractionRiskPage",
    "list_interaction_risk",
    "get_interaction_risk",
    "get_interaction_risk_history",
    "TraceRiskView",
    "TraceRiskPage",
    "list_trace_risk",
    "get_trace_risk",
    "get_trace_risk_history",
    "ForestLegView",
    "ForestInteractionView",
    "TraceRiskDetail",
    "get_trace_risk_detail",
]

SORT_COMPUTED_AT_DESC = "computed_at_desc"
SORT_RISK_LEVEL_DESC = "risk_level_desc"

# array_position is 1-based; a bare len(RISK_LEVEL_ORDER) would collide with
# the rank of "unknown" (the last real entry). +1 puts a genuinely novel
# value strictly after every known level, including "unknown" itself.
_RISK_RANK_UNKNOWN = len(RISK_LEVEL_ORDER) + 1


# ---------------------------------------------------------------------------
# Return types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InteractionRiskView:
    """One row of ``interaction_risk_records`` — the current or a historical
    version, as read (no derivation beyond the type conversions below).
    """

    interaction_risk_id: str
    interaction_id: str
    trace_id: str
    parent_interaction_id: str | None
    caller_entity_id: str
    callee_entity_id: str
    version: int
    computed_at: dt.datetime
    risk_level: str
    enforcement_type: str | None
    policy_event_count: int
    triggered_rule_ids: list[str]
    classification_summary: dict[str, Any] | None
    opa_policy_versions_used: list[str]
    overall_confidence: float | None


@dataclass(frozen=True)
class InteractionRiskPage:
    items: list[InteractionRiskView] = field(default_factory=list)
    next_key: dict[str, Any] | None = None


@dataclass(frozen=True)
class TraceRiskView:
    """One row of ``trace_risk_records`` — the current or a historical
    version, as read (no derivation beyond the type conversions below).
    """

    trace_risk_id: str
    trace_id: str
    version: int
    computed_at: dt.datetime
    trace_risk_level: str
    trace_enforcement_type: str | None
    risk_compounding_mode: str | None
    enforcement_aggregation_mode: str | None
    interaction_count: int
    policy_event_count: int
    all_entity_ids: list[str]
    triggered_rule_ids: list[str]
    overall_confidence: float | None
    contributing_interaction_risk_ids: list[str]


@dataclass(frozen=True)
class TraceRiskPage:
    items: list[TraceRiskView] = field(default_factory=list)
    next_key: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Row mapping
# ---------------------------------------------------------------------------

_COLUMNS = (
    "interaction_risk_id",
    "interaction_id",
    "trace_id",
    "parent_interaction_id",
    "caller_entity_id",
    "callee_entity_id",
    "version",
    "computed_at",
    "risk_level",
    "enforcement_type",
    "policy_event_count",
    "triggered_rule_ids",
    "classification_summary",
    "opa_policy_versions_used",
    "overall_confidence",
)
_SELECT_COLS = ", ".join(_COLUMNS)


def _row_to_view(row: tuple[Any, ...]) -> InteractionRiskView:
    r = dict(zip(_COLUMNS, row))
    return InteractionRiskView(
        interaction_risk_id=str(r["interaction_risk_id"]),
        interaction_id=r["interaction_id"],
        trace_id=r["trace_id"],
        parent_interaction_id=r["parent_interaction_id"],
        caller_entity_id=r["caller_entity_id"],
        callee_entity_id=r["callee_entity_id"],
        version=r["version"],
        computed_at=r["computed_at"],
        risk_level=r["risk_level"],
        enforcement_type=r["enforcement_type"],
        policy_event_count=r["policy_event_count"],
        triggered_rule_ids=list(r["triggered_rule_ids"] or []),
        classification_summary=r["classification_summary"],
        opa_policy_versions_used=list(r["opa_policy_versions_used"] or []),
        overall_confidence=(
            float(r["overall_confidence"])
            if r["overall_confidence"] is not None
            else None
        ),
    )


_TRACE_COLUMNS = (
    "trace_risk_id",
    "trace_id",
    "version",
    "computed_at",
    "trace_risk_level",
    "trace_enforcement_type",
    "risk_compounding_mode",
    "enforcement_aggregation_mode",
    "interaction_count",
    "policy_event_count",
    "all_entity_ids",
    "triggered_rule_ids",
    "overall_confidence",
    "contributing_interaction_risk_ids",
)
_TRACE_SELECT_COLS = ", ".join(_TRACE_COLUMNS)


def _row_to_trace_view(row: tuple[Any, ...]) -> TraceRiskView:
    r = dict(zip(_TRACE_COLUMNS, row))
    return TraceRiskView(
        trace_risk_id=str(r["trace_risk_id"]),
        trace_id=r["trace_id"],
        version=r["version"],
        computed_at=r["computed_at"],
        trace_risk_level=r["trace_risk_level"],
        trace_enforcement_type=r["trace_enforcement_type"],
        risk_compounding_mode=r["risk_compounding_mode"],
        enforcement_aggregation_mode=r["enforcement_aggregation_mode"],
        interaction_count=r["interaction_count"],
        policy_event_count=r["policy_event_count"],
        all_entity_ids=list(r["all_entity_ids"] or []),
        triggered_rule_ids=list(r["triggered_rule_ids"] or []),
        overall_confidence=(
            float(r["overall_confidence"])
            if r["overall_confidence"] is not None
            else None
        ),
        contributing_interaction_risk_ids=[
            str(v) for v in (r["contributing_interaction_risk_ids"] or [])
        ],
    )


# ---------------------------------------------------------------------------
# Migration-presence guard
# ---------------------------------------------------------------------------


def _risk_tables_exist(tx: db.Transaction) -> bool:
    """Whether migration 0016 (the DAS risk tables) has run on this DB.

    Distinct from :func:`.interactions._derived_tables_exist`, which probes
    ``interactions`` (migration 0004) — a different table for a different
    migration. Reusing that helper would let ``UndefinedTable`` escape as a
    500 on a DB migrated through 0004-0015 but not yet to 0016.
    """
    return (
        tx.fetch_one(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_name = 'interaction_risk_records'"
        )
        is not None
    )


def _trace_risk_table_exists(tx: db.Transaction) -> bool:
    """Whether ``trace_risk_records`` specifically exists.

    Both tables arrive in migration 0016, but a test (or an operator dropping
    one table for debugging) can leave them independently present/absent — a
    trace-grain read must not rely on the interaction table's presence.
    """
    return (
        tx.fetch_one(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_name = 'trace_risk_records'"
        )
        is not None
    )


# ---------------------------------------------------------------------------
# list_interaction_risk
# ---------------------------------------------------------------------------


def list_interaction_risk(
    *,
    trace_id: str | None = None,
    entity_id: str | None = None,
    risk_level: list[str] | None = None,
    enforcement_type: str | None = None,
    rule_id: str | None = None,
    regulatory_tag: str | None = None,
    time_from: dt.datetime | None = None,
    time_to: dt.datetime | None = None,
    cursor: dict[str, Any] | None = None,
    limit: int = 50,
    sort: str = SORT_COMPUTED_AT_DESC,
) -> InteractionRiskPage:
    """List the latest version of each interaction's risk record.

    Filters apply AFTER the latest-per-``interaction_id`` reduction (AC-DAS-016):
    a filter never surfaces a superseded version. ``risk_level=[]`` matches
    nothing (the caller asked for zero levels), not "no filter" — same
    convention as ``risk/rules/catalog._accepts``.

    ``cursor`` is a decoded keyset dict (the caller's HTTP layer owns
    base64); ``None`` starts from the first page. Returns empty
    (``next_key=None``) before migration 0016 has run.
    """
    with db.transaction() as tx:
        if not _risk_tables_exist(tx):
            return InteractionRiskPage()

        sql, params = _build_list_query(
            trace_id=trace_id,
            entity_id=entity_id,
            risk_level=risk_level,
            enforcement_type=enforcement_type,
            rule_id=rule_id,
            regulatory_tag=regulatory_tag,
            time_from=time_from,
            time_to=time_to,
            cursor=cursor,
            limit=limit,
            sort=sort,
        )
        rows = tx.fetch_all(sql, params)

    return _paginate_rows(rows, limit=limit, sort=sort)


def _paginate_rows(
    rows: list[tuple[Any, ...]], *, limit: int, sort: str
) -> InteractionRiskPage:
    """Fetch limit+1 pattern: drop the extra row (if present) and mint
    next_key from the last *returned* row."""
    has_more = len(rows) > limit
    page_rows = rows[:limit]
    views = [_row_to_view(r) for r in page_rows]

    next_key = None
    if has_more and views:
        next_key = _cursor_key_for(views[-1], sort=sort)

    return InteractionRiskPage(items=views, next_key=next_key)


def _cursor_key_for(view: InteractionRiskView, *, sort: str) -> dict[str, Any]:
    if sort == SORT_RISK_LEVEL_DESC:
        return {
            "risk_rank": _risk_rank(view.risk_level),
            "computed_at": view.computed_at.isoformat(),
            "interaction_id": view.interaction_id,
        }
    return {
        "computed_at": view.computed_at.isoformat(),
        "interaction_id": view.interaction_id,
    }


def _risk_rank(risk_level: str | None) -> int:
    try:
        return RISK_LEVEL_ORDER.index(risk_level) + 1
    except ValueError:
        return _RISK_RANK_UNKNOWN


def _build_list_query(
    *,
    trace_id: str | None,
    entity_id: str | None,
    risk_level: list[str] | None,
    enforcement_type: str | None,
    rule_id: str | None,
    regulatory_tag: str | None,
    time_from: dt.datetime | None,
    time_to: dt.datetime | None,
    cursor: dict[str, Any] | None,
    limit: int,
    sort: str,
) -> tuple[str, list[Any]]:
    # Stage 1: latest-per-interaction_id reduction. Only trace_id is pushed
    # down here — an interaction can't change trace between versions, so
    # this can't alter which version is "latest," and it lets
    # interaction_risk_records_trace_idx prune the scan.
    current_conditions: list[str] = []
    current_params: list[Any] = []
    if trace_id is not None:
        current_conditions.append("trace_id = %s")
        current_params.append(trace_id)
    current_where = (
        " WHERE " + " AND ".join(current_conditions) if current_conditions else ""
    )
    current_cte = (
        f"SELECT DISTINCT ON (interaction_id) {_SELECT_COLS} "
        f"FROM interaction_risk_records{current_where} "
        f"ORDER BY interaction_id, version DESC"
    )

    # Stage 2: rank + filters over the reduced set.
    filter_conditions: list[str] = []
    filter_params: list[Any] = []

    if entity_id is not None:
        filter_conditions.append("(caller_entity_id = %s OR callee_entity_id = %s)")
        filter_params.extend([entity_id, entity_id])

    if risk_level is not None:
        # risk_level = ANY('{}') is false for every row, so an explicit
        # empty list matches nothing rather than skipping the filter.
        filter_conditions.append("risk_level = ANY(%s)")
        filter_params.append(list(risk_level))

    if enforcement_type is not None:
        filter_conditions.append("enforcement_type = %s")
        filter_params.append(enforcement_type)

    if rule_id is not None:
        filter_conditions.append("%s = ANY(triggered_rule_ids)")
        filter_params.append(rule_id)

    if regulatory_tag is not None:
        # jsonb_each over NULL yields zero rows, so a NULL
        # classification_summary never matches. A leg missing the
        # "regulatory_tags" key (PENDING/NO_PAYLOAD legs) fails the `?`
        # containment test rather than erroring.
        filter_conditions.append(
            "EXISTS (SELECT 1 FROM jsonb_each(classification_summary) leg "
            "WHERE leg.value -> 'regulatory_tags' ? %s)"
        )
        filter_params.append(regulatory_tag)

    if time_from is not None:
        filter_conditions.append("computed_at >= %s")
        filter_params.append(time_from)

    if time_to is not None:
        filter_conditions.append("computed_at <= %s")
        filter_params.append(time_to)

    if cursor is not None:
        cursor_sql, cursor_params = _cursor_predicate(cursor, sort=sort)
        filter_conditions.append(cursor_sql)
        filter_params.extend(cursor_params)

    filter_where = (
        " WHERE " + " AND ".join(filter_conditions) if filter_conditions else ""
    )

    order_sql = _order_by_sql(sort)

    sql = (
        f"WITH current AS ({current_cte}), "
        f"ranked AS (SELECT *, COALESCE(array_position(%s::text[], risk_level), %s) "
        f"AS risk_rank FROM current) "
        f"SELECT {_SELECT_COLS} FROM ranked{filter_where} "
        f"ORDER BY {order_sql} LIMIT %s"
    )
    params = (
        list(current_params)
        + [list(RISK_LEVEL_ORDER), _RISK_RANK_UNKNOWN]
        + filter_params
        + [limit + 1]
    )
    return sql, params


def _order_by_sql(sort: str) -> str:
    if sort == SORT_RISK_LEVEL_DESC:
        return "risk_rank ASC, computed_at DESC, interaction_id ASC"
    return "computed_at DESC, interaction_id ASC"


def _cursor_predicate(cursor: dict[str, Any], *, sort: str) -> tuple[str, list[Any]]:
    if sort == SORT_RISK_LEVEL_DESC:
        return (
            "(risk_rank > %s OR (risk_rank = %s AND computed_at < %s) "
            "OR (risk_rank = %s AND computed_at = %s AND interaction_id > %s))",
            [
                cursor["risk_rank"],
                cursor["risk_rank"],
                cursor["computed_at"],
                cursor["risk_rank"],
                cursor["computed_at"],
                cursor["interaction_id"],
            ],
        )
    return (
        "(computed_at < %s OR (computed_at = %s AND interaction_id > %s))",
        [cursor["computed_at"], cursor["computed_at"], cursor["interaction_id"]],
    )


# ---------------------------------------------------------------------------
# get_interaction_risk
# ---------------------------------------------------------------------------


def get_interaction_risk(interaction_id: str) -> InteractionRiskView | None:
    """The latest version of one interaction's risk record, or ``None`` if
    absent (unknown id, or migration 0016 hasn't run)."""
    with db.transaction() as tx:
        if not _risk_tables_exist(tx):
            return None
        row = tx.fetch_one(
            f"SELECT {_SELECT_COLS} FROM interaction_risk_records "
            f"WHERE interaction_id = %s ORDER BY version DESC LIMIT 1",
            (interaction_id,),
        )
    return _row_to_view(row) if row is not None else None


# ---------------------------------------------------------------------------
# get_interaction_risk_history
# ---------------------------------------------------------------------------


def get_interaction_risk_history(
    interaction_id: str,
    *,
    cursor: dict[str, Any] | None = None,
    limit: int = 50,
) -> InteractionRiskPage:
    """All versions of one interaction's risk record, ascending by version
    (AC-DAS-017). Empty page for an unknown id or before migration 0016."""
    with db.transaction() as tx:
        if not _risk_tables_exist(tx):
            return InteractionRiskPage()

        conditions = ["interaction_id = %s"]
        params: list[Any] = [interaction_id]
        if cursor is not None:
            conditions.append("version > %s")
            params.append(cursor["version"])
        where = " WHERE " + " AND ".join(conditions)

        sql = (
            f"SELECT {_SELECT_COLS} FROM interaction_risk_records{where} "
            f"ORDER BY version ASC LIMIT %s"
        )
        params.append(limit + 1)
        rows = tx.fetch_all(sql, params)

    has_more = len(rows) > limit
    page_rows = rows[:limit]
    views = [_row_to_view(r) for r in page_rows]

    next_key = None
    if has_more and views:
        next_key = {"version": views[-1].version}

    return InteractionRiskPage(items=views, next_key=next_key)


# ---------------------------------------------------------------------------
# list_trace_risk
# ---------------------------------------------------------------------------


def list_trace_risk(
    *,
    entity_id: str | None = None,
    risk_level: list[str] | None = None,
    enforcement_type: str | None = None,
    rule_id: str | None = None,
    time_from: dt.datetime | None = None,
    time_to: dt.datetime | None = None,
    cursor: dict[str, Any] | None = None,
    limit: int = 50,
    sort: str = SORT_COMPUTED_AT_DESC,
) -> TraceRiskPage:
    """List the latest version of each trace's risk record.

    Filters apply AFTER the latest-per-``trace_id`` reduction (AC-DAS-016),
    same convention as :func:`list_interaction_risk`. No ``trace_id`` param
    (filtering to one trace is :func:`get_trace_risk`) and no
    ``regulatory_tag`` (``trace_risk_records`` has no ``classification_summary``
    column).

    ``cursor`` is a decoded keyset dict; ``None`` starts from the first page.
    Returns empty (``next_key=None``) before migration 0016 has run.
    """
    with db.transaction() as tx:
        if not _trace_risk_table_exists(tx):
            return TraceRiskPage()

        sql, params = _build_trace_list_query(
            entity_id=entity_id,
            risk_level=risk_level,
            enforcement_type=enforcement_type,
            rule_id=rule_id,
            time_from=time_from,
            time_to=time_to,
            cursor=cursor,
            limit=limit,
            sort=sort,
        )
        rows = tx.fetch_all(sql, params)

    return _paginate_trace_rows(rows, limit=limit, sort=sort)


def _paginate_trace_rows(
    rows: list[tuple[Any, ...]], *, limit: int, sort: str
) -> TraceRiskPage:
    has_more = len(rows) > limit
    page_rows = rows[:limit]
    views = [_row_to_trace_view(r) for r in page_rows]

    next_key = None
    if has_more and views:
        next_key = _trace_cursor_key_for(views[-1], sort=sort)

    return TraceRiskPage(items=views, next_key=next_key)


def _trace_cursor_key_for(view: TraceRiskView, *, sort: str) -> dict[str, Any]:
    if sort == SORT_RISK_LEVEL_DESC:
        return {
            "risk_rank": _risk_rank(view.trace_risk_level),
            "computed_at": view.computed_at.isoformat(),
            "trace_id": view.trace_id,
        }
    return {
        "computed_at": view.computed_at.isoformat(),
        "trace_id": view.trace_id,
    }


def _build_trace_list_query(
    *,
    entity_id: str | None,
    risk_level: list[str] | None,
    enforcement_type: str | None,
    rule_id: str | None,
    time_from: dt.datetime | None,
    time_to: dt.datetime | None,
    cursor: dict[str, Any] | None,
    limit: int,
    sort: str,
) -> tuple[str, list[Any]]:
    # Stage 1: latest-per-trace_id reduction. No push-down filters here —
    # unlike interaction_risk's trace_id, none of this endpoint's filters are
    # version-invariant at trace grain.
    current_cte = (
        f"SELECT DISTINCT ON (trace_id) {_TRACE_SELECT_COLS} "
        f"FROM trace_risk_records ORDER BY trace_id, version DESC"
    )

    # Stage 2: rank + filters over the reduced set.
    filter_conditions: list[str] = []
    filter_params: list[Any] = []

    if entity_id is not None:
        filter_conditions.append("%s = ANY(all_entity_ids)")
        filter_params.append(entity_id)

    if risk_level is not None:
        filter_conditions.append("trace_risk_level = ANY(%s)")
        filter_params.append(list(risk_level))

    if enforcement_type is not None:
        filter_conditions.append("trace_enforcement_type = %s")
        filter_params.append(enforcement_type)

    if rule_id is not None:
        filter_conditions.append("%s = ANY(triggered_rule_ids)")
        filter_params.append(rule_id)

    if time_from is not None:
        filter_conditions.append("computed_at >= %s")
        filter_params.append(time_from)

    if time_to is not None:
        filter_conditions.append("computed_at <= %s")
        filter_params.append(time_to)

    if cursor is not None:
        cursor_sql, cursor_params = _trace_cursor_predicate(cursor, sort=sort)
        filter_conditions.append(cursor_sql)
        filter_params.extend(cursor_params)

    filter_where = (
        " WHERE " + " AND ".join(filter_conditions) if filter_conditions else ""
    )

    order_sql = _trace_order_by_sql(sort)

    sql = (
        f"WITH current AS ({current_cte}), "
        f"ranked AS (SELECT *, COALESCE(array_position(%s::text[], trace_risk_level), %s) "
        f"AS risk_rank FROM current) "
        f"SELECT {_TRACE_SELECT_COLS} FROM ranked{filter_where} "
        f"ORDER BY {order_sql} LIMIT %s"
    )
    params = (
        [list(RISK_LEVEL_ORDER), _RISK_RANK_UNKNOWN]
        + filter_params
        + [limit + 1]
    )
    return sql, params


def _trace_order_by_sql(sort: str) -> str:
    if sort == SORT_RISK_LEVEL_DESC:
        return "risk_rank ASC, computed_at DESC, trace_id ASC"
    return "computed_at DESC, trace_id ASC"


def _trace_cursor_predicate(
    cursor: dict[str, Any], *, sort: str
) -> tuple[str, list[Any]]:
    if sort == SORT_RISK_LEVEL_DESC:
        return (
            "(risk_rank > %s OR (risk_rank = %s AND computed_at < %s) "
            "OR (risk_rank = %s AND computed_at = %s AND trace_id > %s))",
            [
                cursor["risk_rank"],
                cursor["risk_rank"],
                cursor["computed_at"],
                cursor["risk_rank"],
                cursor["computed_at"],
                cursor["trace_id"],
            ],
        )
    return (
        "(computed_at < %s OR (computed_at = %s AND trace_id > %s))",
        [cursor["computed_at"], cursor["computed_at"], cursor["trace_id"]],
    )


# ---------------------------------------------------------------------------
# get_trace_risk
# ---------------------------------------------------------------------------


def get_trace_risk(trace_id: str) -> TraceRiskView | None:
    """The latest version of one trace's risk record, or ``None`` if absent
    (unknown id, or migration 0016 hasn't run)."""
    with db.transaction() as tx:
        if not _trace_risk_table_exists(tx):
            return None
        row = tx.fetch_one(
            f"SELECT {_TRACE_SELECT_COLS} FROM trace_risk_records "
            f"WHERE trace_id = %s ORDER BY version DESC LIMIT 1",
            (trace_id,),
        )
    return _row_to_trace_view(row) if row is not None else None


# ---------------------------------------------------------------------------
# get_trace_risk_history
# ---------------------------------------------------------------------------


def get_trace_risk_history(
    trace_id: str,
    *,
    cursor: dict[str, Any] | None = None,
    limit: int = 50,
) -> TraceRiskPage:
    """All versions of one trace's risk record, ascending by version
    (AC-DAS-017). Empty page for an unknown id or before migration 0016."""
    with db.transaction() as tx:
        if not _trace_risk_table_exists(tx):
            return TraceRiskPage()

        conditions = ["trace_id = %s"]
        params: list[Any] = [trace_id]
        if cursor is not None:
            conditions.append("version > %s")
            params.append(cursor["version"])
        where = " WHERE " + " AND ".join(conditions)

        sql = (
            f"SELECT {_TRACE_SELECT_COLS} FROM trace_risk_records{where} "
            f"ORDER BY version ASC LIMIT %s"
        )
        params.append(limit + 1)
        rows = tx.fetch_all(sql, params)

    has_more = len(rows) > limit
    page_rows = rows[:limit]
    views = [_row_to_trace_view(r) for r in page_rows]

    next_key = None
    if has_more and views:
        next_key = {"version": views[-1].version}

    return TraceRiskPage(items=views, next_key=next_key)


# ---------------------------------------------------------------------------
# get_trace_risk_detail — the trace forest
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ForestLegView:
    """One request/response leg of a forest interaction.

    Unlike :class:`.interactions.InteractionLegView`, ``occurred_at`` stays a
    ``datetime`` (or ``None``) rather than an ISO string — the forest
    assembly sorts on it directly (see :func:`get_trace_risk_detail`).
    """

    leg_type: str
    occurred_at: dt.datetime | None
    payload_hash: str | None
    error: bool | None


@dataclass(frozen=True)
class ForestInteractionView:
    """One interaction of a trace forest, with its legs, current risk record
    (``None`` if not yet computed — eventual consistency, not an error), and
    span COUNTS rather than span rows (a deliberate deviation from issue
    #109's literal "+ evidencing spans" — see the module/PR notes)."""

    interaction_id: str
    trace_id: str
    parent_interaction_id: str | None
    caller_entity_id: str
    callee_entity_id: str
    summary: str
    legs: list[ForestLegView]
    risk: InteractionRiskView | None
    span_count: int
    anchor_count: int


@dataclass(frozen=True)
class TraceRiskDetail:
    trace_risk: TraceRiskView
    interactions: list[ForestInteractionView]


@contextmanager
def _repeatable_read() -> Iterator[db.Transaction]:
    """Open a single REPEATABLE READ transaction.

    The forest read issues several statements (trace risk record, forest
    interactions, legs, span counts, per-interaction risk) that must observe
    the same snapshot — otherwise ``trace_risk.interaction_count`` could
    disagree with the forest actually returned because a concurrent write
    landed mid-read. Mirrors ``retrieval/spans.py``'s helper of the same name.
    """
    with db.transaction() as tx:
        tx.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        yield tx


def get_trace_risk_detail(trace_id: str) -> TraceRiskDetail | None:
    """The trace risk record plus every interaction of that trace.

    Returns ``None`` (404 at the HTTP layer) when there is no trace risk
    record for ``trace_id`` — including when interactions exist but the
    record does not, and before migration 0016 has run. Issues exactly 6
    queries (after the short-circuiting record lookup, the forest queries
    still run bounded-by-count, not per-interaction), so wall-clock and query
    count are independent of trace size (AC-DAS-012).

    ``trace_risk.interaction_count`` (a snapshot taken at ``computed_at``)
    and ``len(interactions)`` (read now) are surfaced without reconciliation
    — the two can legitimately disagree, and FR-DAS-084 forbids a
    completeness/warning field that would paper over that.
    """
    with _repeatable_read() as tx:
        if not _trace_risk_table_exists(tx):
            return None

        trace_risk_row = tx.fetch_one(
            f"SELECT {_TRACE_SELECT_COLS} FROM trace_risk_records "
            f"WHERE trace_id = %s ORDER BY version DESC LIMIT 1",
            (trace_id,),
        )
        if trace_risk_row is None:
            return None
        trace_risk = _row_to_trace_view(trace_risk_row)

        interaction_rows = tx.fetch_all(
            "SELECT id, trace_id, parent_interaction_id, caller_entity_id, "
            "callee_entity_id, summary FROM interactions WHERE trace_id = %s",
            (trace_id,),
        )

        leg_rows = tx.fetch_all(
            "SELECT l.interaction_id, l.leg_type, l.occurred_at, "
            "l.payload_hash, l.error FROM interaction_legs l "
            "JOIN interactions i ON i.id = l.interaction_id "
            "WHERE i.trace_id = %s ORDER BY l.interaction_id, l.leg_type",
            (trace_id,),
        )

        span_count_rows = tx.fetch_all(
            "SELECT interaction_id, COUNT(*), "
            "COUNT(*) FILTER (WHERE role = 'anchor') "
            "FROM interaction_spans WHERE trace_id = %s GROUP BY interaction_id",
            (trace_id,),
        )

        risk_rows = tx.fetch_all(
            f"SELECT DISTINCT ON (interaction_id) {_SELECT_COLS} "
            f"FROM interaction_risk_records WHERE trace_id = %s "
            f"ORDER BY interaction_id, version DESC",
            (trace_id,),
        )

    legs_by_interaction: dict[str, list[ForestLegView]] = {}
    for interaction_id, leg_type, occurred_at, payload_hash, error in leg_rows:
        legs_by_interaction.setdefault(interaction_id, []).append(
            ForestLegView(
                leg_type=leg_type,
                occurred_at=occurred_at,
                payload_hash=payload_hash,
                error=error,
            )
        )

    span_counts_by_interaction: dict[str, tuple[int, int]] = {
        interaction_id: (total, anchors)
        for interaction_id, total, anchors in span_count_rows
    }

    risk_by_interaction: dict[str, InteractionRiskView] = {}
    for row in risk_rows:
        view = _row_to_view(row)
        risk_by_interaction[view.interaction_id] = view

    interactions: list[ForestInteractionView] = []
    for (
        interaction_id,
        ix_trace_id,
        parent_interaction_id,
        caller_entity_id,
        callee_entity_id,
        summary,
    ) in interaction_rows:
        span_count, anchor_count = span_counts_by_interaction.get(
            interaction_id, (0, 0)
        )
        interactions.append(
            ForestInteractionView(
                interaction_id=interaction_id,
                trace_id=ix_trace_id,
                parent_interaction_id=parent_interaction_id,
                caller_entity_id=caller_entity_id,
                callee_entity_id=callee_entity_id,
                summary=summary,
                legs=legs_by_interaction.get(interaction_id, []),
                risk=risk_by_interaction.get(interaction_id),
                span_count=span_count,
                anchor_count=anchor_count,
            )
        )

    def _sort_key(ix: ForestInteractionView) -> tuple[bool, dt.datetime]:
        request_occurred_at = next(
            (leg.occurred_at for leg in ix.legs if leg.leg_type == "request"),
            None,
        )
        return (
            request_occurred_at is None,
            request_occurred_at or dt.datetime.min.replace(tzinfo=dt.timezone.utc),
        )

    interactions.sort(key=_sort_key)

    return TraceRiskDetail(trace_risk=trace_risk, interactions=interactions)
