"""`/risk/alerts*` — DAS alert read API (issue #110, read half).

Thin adapters over `data_governance.retrieval`'s `list_alerts`/`get_alert`,
in `risk_routes.py`'s style: FR-DAS-081's error triple, DB-level keyset
pagination (`http.encode_keyset_cursor`/`decode_keyset_cursor`), no `total`.

The three `by-*` endpoints are the list endpoint with one filter taken from
the path (AC-DAS-025): every other query parameter still applies, and a path
value outside the vocabulary is a 400 rather than an empty page. `trace_id`
is a filter beyond the issue's list — one trace's alerts, the trace page's
question.

`PUT /risk/alerts/{alert_id}/status` is not here.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping
from typing import Any

from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import BaseRoute, Route

from data_governance import retrieval
from data_governance.risk import config
from data_governance.risk.api import http
from data_governance.risk.rules.catalog import ENFORCEMENT_ORDER, RISK_LEVEL_ORDER

# PRD §6.4's alert status vocabulary. #104 writes `new`; the status endpoint
# moves an alert through the rest.
ALERT_STATUSES: tuple[str, ...] = (
    "new",
    "acknowledged",
    "investigating",
    "resolved",
    "false_positive",
)

_SORT_KEYS = (retrieval.SORT_TIMESTAMP_DESC, retrieval.SORT_RISK_LEVEL_DESC)
_DEFAULT_SORT = retrieval.SORT_TIMESTAMP_DESC
_CURSOR_FIELDS = {
    retrieval.SORT_TIMESTAMP_DESC: ("timestamp", "alert_id"),
    retrieval.SORT_RISK_LEVEL_DESC: ("risk_rank", "timestamp", "alert_id"),
}


def alert_to_json(view: retrieval.AlertView) -> dict[str, Any]:
    return {
        "alert_id": view.alert_id,
        "timestamp": view.timestamp,
        "trace_id": view.trace_id,
        "trace_risk_record_id": view.trace_risk_record_id,
        "risk_level": view.risk_level,
        "enforcement_type": view.enforcement_type,
        "title": view.title,
        "description": view.description,
        "involved_entities": view.involved_entities,
        "triggered_rule_ids": view.triggered_rule_ids,
        "status": view.status,
        "alert_type": view.alert_type,
        "assigned_to": view.assigned_to,
        "dedup_key": view.dedup_key,
        "duplicate_count": view.duplicate_count,
        "superseded_by": view.superseded_by,
    }


def _parse_sort(params: Mapping[str, str]) -> str:
    sort = params.get("sort") or _DEFAULT_SORT
    if sort not in _SORT_KEYS:
        raise http.ApiError(
            "bad request", f"sort must be one of {list(_SORT_KEYS)}, got {sort!r}"
        )
    return sort


def _parse_bool(params: Mapping[str, str], name: str, *, default: bool) -> bool:
    raw = params.get(name)
    if raw is None or raw == "":
        return default
    if raw in ("true", "false"):
        return raw == "true"
    raise http.ApiError("bad request", f"{name} must be 'true' or 'false', got {raw!r}")


def _decode_cursor(params: Mapping[str, str], *, sort: str) -> dict[str, Any] | None:
    """The keyset cursor, its values checked too: a hand-edited cursor with the
    right keys but a non-UUID id, a naive or unparsable time, or a non-integer
    rank is a 400, not a database error. The parsed values go on to the query,
    not the raw strings, so a form Python accepts and Postgres would not
    (``urn:uuid:…``, an ISO week date) cannot reach the database."""
    raw = params.get("cursor")
    if raw is None:
        return None
    key = http.decode_keyset_cursor(
        raw, expect_sort=sort, expect_fields=_CURSOR_FIELDS[sort]
    )
    try:
        alert_id = str(uuid.UUID(key["alert_id"]))
        parsed = dt.datetime.fromisoformat(key["timestamp"])
    except (TypeError, ValueError, AttributeError):
        raise http.ApiError("bad request", "cursor is malformed") from None
    rank = key.get("risk_rank", 0)
    if parsed.tzinfo is None or not isinstance(rank, int) or isinstance(rank, bool):
        raise http.ApiError("bad request", "cursor is malformed")
    return {**key, "alert_id": alert_id, "timestamp": parsed}


def _list_alerts(params: Mapping[str, str]) -> Response:
    try:
        sort = _parse_sort(params)
        cursor = _decode_cursor(params, sort=sort)
        limit = http.parse_limit(
            params,
            default=config.API_RISK_ALERTS_DEFAULT_LIMIT,
            maximum=config.API_RISK_ALERTS_MAX_LIMIT,
        )
        page = retrieval.list_alerts(
            risk_level=http.parse_csv_param(params, "risk_level"),
            status=http.parse_csv_param(params, "status"),
            enforcement_type=http.parse_csv_param(params, "enforcement_type"),
            alert_type=params.get("alert_type"),
            trace_id=params.get("trace_id"),
            entity_id=params.get("entity_id"),
            rule_id=params.get("rule_id"),
            assigned_to=params.get("assigned_to"),
            include_superseded=_parse_bool(
                params, "include_superseded", default=False
            ),
            time_from=http.parse_iso_datetime(params.get("from"), "from"),
            time_to=http.parse_iso_datetime(params.get("to"), "to"),
            cursor=cursor,
            limit=limit,
            sort=sort,
        )
    except http.ApiError as exc:
        return http.error_response(exc)

    next_cursor = (
        http.encode_keyset_cursor(page.next_key, sort)
        if page.next_key is not None
        else None
    )
    return http.json_ok(
        {"items": [alert_to_json(v) for v in page.items], "next_cursor": next_cursor}
    )


# ---------------------------------------------------------------------------
# GET /risk/alerts
# ---------------------------------------------------------------------------


async def _list_alerts_handler(request: Request) -> Response:
    return _list_alerts(request.query_params)


# ---------------------------------------------------------------------------
# GET /risk/alerts/by-{status,risk-level,enforcement-type}/{value}
# ---------------------------------------------------------------------------


def _by_filter_handler(name: str, vocabulary: tuple[str, ...]):
    """The list endpoint with `name` pre-applied from the path; a path value
    outside `vocabulary` is a 400. A same-named query parameter is ignored —
    the path decides."""

    async def handler(request: Request) -> Response:
        value = request.path_params["value"]
        if value not in vocabulary:
            return http.error_response(
                http.ApiError(
                    "bad request",
                    f"{name} must be one of {list(vocabulary)}, got {value!r}",
                )
            )
        return _list_alerts({**request.query_params, name: value})

    return handler


# ---------------------------------------------------------------------------
# GET /risk/alerts/{alert_id}
# ---------------------------------------------------------------------------


async def _get_alert_handler(request: Request) -> Response:
    alert_id = request.path_params["alert_id"]
    try:
        alert_id = str(uuid.UUID(alert_id))
    except ValueError:
        view = None
    else:
        view = retrieval.get_alert(alert_id)
    if view is None:
        return http.error_response(
            http.ApiError(
                "not found", f"no alert with alert_id {alert_id!r}", status_code=404
            )
        )
    return http.json_ok(alert_to_json(view))


def routes() -> list[BaseRoute]:
    """The `by-*` paths have two segments after `/risk/alerts`, so the
    one-segment `{alert_id}` route cannot shadow them; they are listed first
    anyway, as `risk_routes.routes()` lists `/history` first."""
    return [
        Route(
            "/risk/alerts/by-status/{value:str}",
            endpoint=_by_filter_handler("status", ALERT_STATUSES),
            methods=["GET"],
        ),
        Route(
            "/risk/alerts/by-risk-level/{value:str}",
            endpoint=_by_filter_handler("risk_level", RISK_LEVEL_ORDER),
            methods=["GET"],
        ),
        Route(
            "/risk/alerts/by-enforcement-type/{value:str}",
            endpoint=_by_filter_handler("enforcement_type", ENFORCEMENT_ORDER),
            methods=["GET"],
        ),
        Route(
            "/risk/alerts/{alert_id:str}",
            endpoint=_get_alert_handler,
            methods=["GET"],
        ),
        Route("/risk/alerts", endpoint=_list_alerts_handler, methods=["GET"]),
    ]
