"""Retrieval submodule — the typed read path over ``alerts`` (migration 0016,
written by issue #104's ``risk/alerts/generate.py``).

Part of the :mod:`data_governance.retrieval` package (see its ``__init__``
for the seam split). Read half of issue #110: :func:`list_alerts` and
:func:`get_alert`. The status write (``PUT /risk/alerts/{id}/status``) is
not here.

Unlike the risk tables (:mod:`.risk`), ``alerts`` is not versioned: one row
is one alert, and a level rise links the old row to the new one through
``superseded_by`` instead of inserting a version. So there is no
latest-per-key reduction; the "current" alerts are the ones with
``superseded_by IS NULL``, which is what ``include_superseded=False`` (the
default) selects.

Pagination is DB-level keyset with the same contract as :mod:`.risk`: the
seam takes and returns a decoded ``{field: value}`` cursor dict, the HTTP
layer owns base64 and rejects a cursor shaped for another sort. The order
keys are ``timestamp DESC, alert_id ASC`` and, for ``risk_level_desc``, the
severity rank first.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from typing import Any

from data_governance import db
from data_governance.retrieval.risk import SORT_RISK_LEVEL_DESC
from data_governance.risk.rules.catalog import RISK_LEVEL_ORDER

__all__ = [
    "SORT_TIMESTAMP_DESC",
    "AlertView",
    "AlertPage",
    "list_alerts",
    "get_alert",
]

# The severity sort shares its name with the risk-record lists'
# (``SORT_RISK_LEVEL_DESC``, re-exported from :mod:`.risk`); the time sort is
# on ``timestamp`` here, not ``computed_at``.
SORT_TIMESTAMP_DESC = "timestamp_desc"

# Same ranking as :mod:`.risk`: array_position is 1-based, and a value outside
# RISK_LEVEL_ORDER sorts after every known level.
_RISK_RANK_UNKNOWN = len(RISK_LEVEL_ORDER) + 1


@dataclass(frozen=True)
class AlertView:
    """One ``alerts`` row, as read (UUIDs as strings, nothing derived)."""

    alert_id: str
    timestamp: dt.datetime
    trace_id: str
    trace_risk_record_id: str | None
    risk_level: str
    enforcement_type: str | None
    title: str
    description: str
    involved_entities: list[dict[str, Any]]
    triggered_rule_ids: list[str]
    status: str
    alert_type: str | None
    assigned_to: str | None
    dedup_key: str | None
    duplicate_count: int
    superseded_by: str | None


@dataclass(frozen=True)
class AlertPage:
    items: list[AlertView] = field(default_factory=list)
    next_key: dict[str, Any] | None = None


_COLUMNS = (
    "alert_id",
    "timestamp",
    "trace_id",
    "trace_risk_record_id",
    "risk_level",
    "enforcement_type",
    "title",
    "description",
    "involved_entities",
    "triggered_rule_ids",
    "status",
    "alert_type",
    "assigned_to",
    "dedup_key",
    "duplicate_count",
    "superseded_by",
)
_SELECT_COLS = ", ".join(f'"{c}"' for c in _COLUMNS)


def _row_to_view(row: tuple[Any, ...]) -> AlertView:
    r = dict(zip(_COLUMNS, row))
    return AlertView(
        alert_id=str(r["alert_id"]),
        timestamp=r["timestamp"],
        trace_id=r["trace_id"],
        trace_risk_record_id=(
            str(r["trace_risk_record_id"])
            if r["trace_risk_record_id"] is not None
            else None
        ),
        risk_level=r["risk_level"],
        enforcement_type=r["enforcement_type"],
        title=r["title"],
        description=r["description"],
        involved_entities=list(r["involved_entities"] or []),
        triggered_rule_ids=list(r["triggered_rule_ids"] or []),
        status=r["status"],
        alert_type=r["alert_type"],
        assigned_to=r["assigned_to"],
        dedup_key=r["dedup_key"],
        duplicate_count=r["duplicate_count"],
        superseded_by=(
            str(r["superseded_by"]) if r["superseded_by"] is not None else None
        ),
    )


def _alerts_table_exists(tx: db.Transaction) -> bool:
    """Whether migration 0016 has run (``alerts`` arrives with the risk tables)."""
    return (
        tx.fetch_one(
            "SELECT 1 FROM information_schema.tables WHERE table_name = 'alerts'"
        )
        is not None
    )


def list_alerts(
    *,
    risk_level: list[str] | None = None,
    status: list[str] | None = None,
    enforcement_type: list[str] | None = None,
    alert_type: str | None = None,
    trace_id: str | None = None,
    entity_id: str | None = None,
    rule_id: str | None = None,
    assigned_to: str | None = None,
    include_superseded: bool = False,
    time_from: dt.datetime | None = None,
    time_to: dt.datetime | None = None,
    cursor: dict[str, Any] | None = None,
    limit: int = 50,
    sort: str = SORT_TIMESTAMP_DESC,
) -> AlertPage:
    """List alerts, newest first (or most severe first).

    The three list filters take any of their values; an explicit empty list
    matches nothing, not "no filter" (same convention as :mod:`.risk`).
    ``entity_id`` matches an alert whose ``involved_entities`` names that
    entity. ``time_from``/``time_to`` bound ``timestamp``, both inclusive.
    Superseded alerts are left out unless ``include_superseded``.

    ``cursor`` is a decoded keyset dict shaped for ``sort``; the HTTP layer
    rejects a mismatch. Empty page before migration 0016 has run.
    """
    with db.transaction() as tx:
        if not _alerts_table_exists(tx):
            return AlertPage()
        sql, params = _build_list_query(
            risk_level=risk_level,
            status=status,
            enforcement_type=enforcement_type,
            alert_type=alert_type,
            trace_id=trace_id,
            entity_id=entity_id,
            rule_id=rule_id,
            assigned_to=assigned_to,
            include_superseded=include_superseded,
            time_from=time_from,
            time_to=time_to,
            cursor=cursor,
            limit=limit,
            sort=sort,
        )
        rows = tx.fetch_all(sql, params)

    has_more = len(rows) > limit
    views = [_row_to_view(r) for r in rows[:limit]]
    next_key = _cursor_key_for(views[-1], sort=sort) if has_more and views else None
    return AlertPage(items=views, next_key=next_key)


def _risk_rank(risk_level: str | None) -> int:
    try:
        return RISK_LEVEL_ORDER.index(risk_level) + 1
    except ValueError:
        return _RISK_RANK_UNKNOWN


def _cursor_key_for(view: AlertView, *, sort: str) -> dict[str, Any]:
    key: dict[str, Any] = {
        "timestamp": view.timestamp.isoformat(),
        "alert_id": view.alert_id,
    }
    if sort == SORT_RISK_LEVEL_DESC:
        key["risk_rank"] = _risk_rank(view.risk_level)
    return key


def _build_list_query(
    *,
    risk_level: list[str] | None,
    status: list[str] | None,
    enforcement_type: list[str] | None,
    alert_type: str | None,
    trace_id: str | None,
    entity_id: str | None,
    rule_id: str | None,
    assigned_to: str | None,
    include_superseded: bool,
    time_from: dt.datetime | None,
    time_to: dt.datetime | None,
    cursor: dict[str, Any] | None,
    limit: int,
    sort: str,
) -> tuple[str, list[Any]]:
    conditions: list[str] = []
    params: list[Any] = []

    for column, values in (
        ("risk_level", risk_level),
        ("status", status),
        ("enforcement_type", enforcement_type),
    ):
        if values is not None:
            conditions.append(f"{column} = ANY(%s)")
            params.append(list(values))

    for column, value in (
        ("alert_type", alert_type),
        ("trace_id", trace_id),
        ("assigned_to", assigned_to),
    ):
        if value is not None:
            conditions.append(f"{column} = %s")
            params.append(value)

    if entity_id is not None:
        conditions.append("involved_entities @> %s::jsonb")
        params.append(json.dumps([{"entity_id": entity_id}]))

    if rule_id is not None:
        conditions.append("%s = ANY(triggered_rule_ids)")
        params.append(rule_id)

    if not include_superseded:
        conditions.append("superseded_by IS NULL")

    if time_from is not None:
        conditions.append('"timestamp" >= %s')
        params.append(time_from)

    if time_to is not None:
        conditions.append('"timestamp" <= %s')
        params.append(time_to)

    if cursor is not None:
        cursor_sql, cursor_params = _cursor_predicate(cursor, sort=sort)
        conditions.append(cursor_sql)
        params.extend(cursor_params)

    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    sql = (
        f"WITH ranked AS (SELECT *, COALESCE(array_position(%s::text[], risk_level), %s) "
        f"AS risk_rank FROM alerts) "
        f"SELECT {_SELECT_COLS} FROM ranked{where} "
        f"ORDER BY {_order_by_sql(sort)} LIMIT %s"
    )
    return sql, [list(RISK_LEVEL_ORDER), _RISK_RANK_UNKNOWN, *params, limit + 1]


def _order_by_sql(sort: str) -> str:
    if sort == SORT_RISK_LEVEL_DESC:
        return 'risk_rank ASC, "timestamp" DESC, alert_id ASC'
    return '"timestamp" DESC, alert_id ASC'


def _cursor_predicate(cursor: dict[str, Any], *, sort: str) -> tuple[str, list[Any]]:
    after = (
        '("timestamp" < %s OR ("timestamp" = %s AND alert_id > %s::uuid))',
        [cursor["timestamp"], cursor["timestamp"], cursor["alert_id"]],
    )
    if sort != SORT_RISK_LEVEL_DESC:
        return after
    rank = cursor["risk_rank"]
    return (
        f"(risk_rank > %s OR (risk_rank = %s AND {after[0]}))",
        [rank, rank, *after[1]],
    )


def get_alert(alert_id: str) -> AlertView | None:
    """One alert by id, superseded or not; ``None`` if absent (or before
    migration 0016). ``alert_id`` must already be a valid UUID string."""
    with db.transaction() as tx:
        if not _alerts_table_exists(tx):
            return None
        row = tx.fetch_one(
            f"SELECT {_SELECT_COLS} FROM alerts WHERE alert_id = %s::uuid",
            (alert_id,),
        )
    return _row_to_view(row) if row is not None else None
