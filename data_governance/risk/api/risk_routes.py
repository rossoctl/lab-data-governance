"""`/risk/interactions*` and `/risk/traces*` — DAS risk read API (issue #109).

Thin adapters over `data_governance.retrieval`'s interaction-risk and
trace-risk reads (`list_interaction_risk`/`get_interaction_risk`/
`get_interaction_risk_history`, `list_trace_risk`/`get_trace_risk`/
`get_trace_risk_history`, and the trace forest `get_trace_risk_detail`).
Follows `rules_routes.py`'s adapter style and FR-DAS-081's error triple, but
unlike that module's bounded in-memory catalog, list/history endpoints here
use DB-level keyset pagination (`http.encode_keyset_cursor`/
`decode_keyset_cursor`) — see the issue #109 plan for why an offset cursor is
wrong for a growing, insert-only-and-versioned table.
"""

from __future__ import annotations

from typing import Any

from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import BaseRoute, Route

from data_governance import retrieval
from data_governance.risk import config
from data_governance.risk.api import http

_SORT_KEYS = (retrieval.SORT_COMPUTED_AT_DESC, retrieval.SORT_RISK_LEVEL_DESC)
_DEFAULT_SORT = retrieval.SORT_COMPUTED_AT_DESC

_INTERACTION_CURSOR_FIELDS = {
    retrieval.SORT_COMPUTED_AT_DESC: ("computed_at", "interaction_id"),
    retrieval.SORT_RISK_LEVEL_DESC: ("risk_rank", "computed_at", "interaction_id"),
}
_TRACE_CURSOR_FIELDS = {
    retrieval.SORT_COMPUTED_AT_DESC: ("computed_at", "trace_id"),
    retrieval.SORT_RISK_LEVEL_DESC: ("risk_rank", "computed_at", "trace_id"),
}


def _parse_sort(params: Any) -> str:
    sort = params.get("sort") or _DEFAULT_SORT
    if sort not in _SORT_KEYS:
        raise http.ApiError(
            "bad request", f"sort must be one of {list(_SORT_KEYS)}, got {sort!r}"
        )
    return sort


def _decode_cursor(
    params: Any, *, sort: str, cursor_fields: dict[str, tuple[str, ...]]
) -> dict[str, Any] | None:
    raw = params.get("cursor")
    if raw is None:
        return None
    return http.decode_keyset_cursor(
        raw, expect_sort=sort, expect_fields=cursor_fields[sort]
    )


def _encode_next_cursor(
    next_key: dict[str, Any] | None, *, sort: str
) -> str | None:
    if next_key is None:
        return None
    return http.encode_keyset_cursor(next_key, sort)


def _interaction_risk_to_json(view: retrieval.InteractionRiskView) -> dict[str, Any]:
    return {
        "interaction_risk_id": view.interaction_risk_id,
        "interaction_id": view.interaction_id,
        "trace_id": view.trace_id,
        "parent_interaction_id": view.parent_interaction_id,
        "caller_entity_id": view.caller_entity_id,
        "callee_entity_id": view.callee_entity_id,
        "version": view.version,
        "computed_at": view.computed_at,
        "risk_level": view.risk_level,
        "enforcement_type": view.enforcement_type,
        "policy_event_count": view.policy_event_count,
        "triggered_rule_ids": view.triggered_rule_ids,
        "classification_summary": view.classification_summary,
        "opa_policy_versions_used": view.opa_policy_versions_used,
        "overall_confidence": view.overall_confidence,
    }


def _trace_risk_to_json(view: retrieval.TraceRiskView) -> dict[str, Any]:
    return {
        "trace_risk_id": view.trace_risk_id,
        "trace_id": view.trace_id,
        "version": view.version,
        "computed_at": view.computed_at,
        "trace_risk_level": view.trace_risk_level,
        "trace_enforcement_type": view.trace_enforcement_type,
        "risk_compounding_mode": view.risk_compounding_mode,
        "enforcement_aggregation_mode": view.enforcement_aggregation_mode,
        "interaction_count": view.interaction_count,
        "policy_event_count": view.policy_event_count,
        "all_entity_ids": view.all_entity_ids,
        "triggered_rule_ids": view.triggered_rule_ids,
        "overall_confidence": view.overall_confidence,
        "contributing_interaction_risk_ids": view.contributing_interaction_risk_ids,
    }


def _forest_leg_to_json(leg: retrieval.ForestLegView) -> dict[str, Any]:
    return {
        "leg_type": leg.leg_type,
        "occurred_at": leg.occurred_at,
        "payload_hash": leg.payload_hash,
        "error": leg.error,
    }


def _forest_interaction_to_json(ix: retrieval.ForestInteractionView) -> dict[str, Any]:
    return {
        "interaction_id": ix.interaction_id,
        "trace_id": ix.trace_id,
        "parent_interaction_id": ix.parent_interaction_id,
        "caller_entity_id": ix.caller_entity_id,
        "callee_entity_id": ix.callee_entity_id,
        "summary": ix.summary,
        "legs": [_forest_leg_to_json(leg) for leg in ix.legs],
        "risk": (
            _interaction_risk_to_json(ix.risk) if ix.risk is not None else None
        ),
        "span_count": ix.span_count,
        "anchor_count": ix.anchor_count,
    }


# ---------------------------------------------------------------------------
# GET /risk/interactions
# ---------------------------------------------------------------------------


async def _list_interactions_handler(request: Request) -> Response:
    params = request.query_params
    try:
        sort = _parse_sort(params)
        cursor = _decode_cursor(
            params, sort=sort, cursor_fields=_INTERACTION_CURSOR_FIELDS
        )
        limit = http.parse_limit(
            params,
            default=config.API_RISK_INTERACTIONS_DEFAULT_LIMIT,
            maximum=config.API_RISK_INTERACTIONS_MAX_LIMIT,
        )
        risk_level = http.parse_csv_param(params, "risk_level")
        time_from = http.parse_iso_datetime(params.get("from"), "from")
        time_to = http.parse_iso_datetime(params.get("to"), "to")

        page = retrieval.list_interaction_risk(
            trace_id=params.get("trace_id"),
            entity_id=params.get("entity_id"),
            risk_level=risk_level,
            enforcement_type=params.get("enforcement_type"),
            rule_id=params.get("rule_id"),
            regulatory_tag=params.get("regulatory_tag"),
            time_from=time_from,
            time_to=time_to,
            cursor=cursor,
            limit=limit,
            sort=sort,
        )
    except http.ApiError as exc:
        return http.error_response(exc)

    return http.json_ok(
        {
            "items": [_interaction_risk_to_json(v) for v in page.items],
            "next_cursor": _encode_next_cursor(page.next_key, sort=sort),
        }
    )


# ---------------------------------------------------------------------------
# GET /risk/interactions/{interaction_id}
# ---------------------------------------------------------------------------


async def _get_interaction_handler(request: Request) -> Response:
    interaction_id = request.path_params["interaction_id"]
    view = retrieval.get_interaction_risk(interaction_id)
    if view is None:
        return http.error_response(
            http.ApiError(
                "not found",
                f"no interaction risk record for interaction_id {interaction_id!r}",
                status_code=404,
            )
        )
    return http.json_ok(_interaction_risk_to_json(view))


# ---------------------------------------------------------------------------
# GET /risk/interactions/{interaction_id}/history
# ---------------------------------------------------------------------------


async def _interaction_history_handler(request: Request) -> Response:
    interaction_id = request.path_params["interaction_id"]
    params = request.query_params
    try:
        cursor = None
        raw = params.get("cursor")
        if raw is not None:
            cursor = http.decode_keyset_cursor(
                raw, expect_sort="version", expect_fields=("version",)
            )
        limit = http.parse_limit(
            params,
            default=config.API_RISK_INTERACTIONS_HISTORY_DEFAULT_LIMIT,
            maximum=config.API_RISK_INTERACTIONS_HISTORY_MAX_LIMIT,
        )

        page = retrieval.get_interaction_risk_history(
            interaction_id, cursor=cursor, limit=limit
        )
    except http.ApiError as exc:
        return http.error_response(exc)

    return http.json_ok(
        {
            "items": [_interaction_risk_to_json(v) for v in page.items],
            "next_cursor": _encode_next_cursor(page.next_key, sort="version"),
        }
    )


# ---------------------------------------------------------------------------
# GET /risk/traces
# ---------------------------------------------------------------------------


async def _list_traces_handler(request: Request) -> Response:
    params = request.query_params
    try:
        sort = _parse_sort(params)
        cursor = _decode_cursor(params, sort=sort, cursor_fields=_TRACE_CURSOR_FIELDS)
        limit = http.parse_limit(
            params,
            default=config.API_RISK_TRACES_DEFAULT_LIMIT,
            maximum=config.API_RISK_TRACES_MAX_LIMIT,
        )
        risk_level = http.parse_csv_param(params, "risk_level")
        time_from = http.parse_iso_datetime(params.get("from"), "from")
        time_to = http.parse_iso_datetime(params.get("to"), "to")

        page = retrieval.list_trace_risk(
            entity_id=params.get("entity_id"),
            risk_level=risk_level,
            enforcement_type=params.get("enforcement_type"),
            rule_id=params.get("rule_id"),
            time_from=time_from,
            time_to=time_to,
            cursor=cursor,
            limit=limit,
            sort=sort,
        )
    except http.ApiError as exc:
        return http.error_response(exc)

    return http.json_ok(
        {
            "items": [_trace_risk_to_json(v) for v in page.items],
            "next_cursor": _encode_next_cursor(page.next_key, sort=sort),
        }
    )


# ---------------------------------------------------------------------------
# GET /risk/traces/{trace_id}
# ---------------------------------------------------------------------------


async def _get_trace_detail_handler(request: Request) -> Response:
    trace_id = request.path_params["trace_id"]
    detail = retrieval.get_trace_risk_detail(trace_id)
    if detail is None:
        return http.error_response(
            http.ApiError(
                "not found",
                f"no trace risk record for trace_id {trace_id!r}",
                status_code=404,
            )
        )
    return http.json_ok(
        {
            "trace_risk": _trace_risk_to_json(detail.trace_risk),
            "interactions": [
                _forest_interaction_to_json(ix) for ix in detail.interactions
            ],
        }
    )


# ---------------------------------------------------------------------------
# GET /risk/traces/{trace_id}/history
# ---------------------------------------------------------------------------


async def _trace_history_handler(request: Request) -> Response:
    trace_id = request.path_params["trace_id"]
    params = request.query_params
    try:
        cursor = None
        raw = params.get("cursor")
        if raw is not None:
            cursor = http.decode_keyset_cursor(
                raw, expect_sort="version", expect_fields=("version",)
            )
        limit = http.parse_limit(
            params,
            default=config.API_RISK_TRACES_HISTORY_DEFAULT_LIMIT,
            maximum=config.API_RISK_TRACES_HISTORY_MAX_LIMIT,
        )

        page = retrieval.get_trace_risk_history(trace_id, cursor=cursor, limit=limit)
    except http.ApiError as exc:
        return http.error_response(exc)

    return http.json_ok(
        {
            "items": [_trace_risk_to_json(v) for v in page.items],
            "next_cursor": _encode_next_cursor(page.next_key, sort="version"),
        }
    )


def routes() -> list[BaseRoute]:
    """`/history` MUST be registered before `{interaction_id}`/`{trace_id}` in
    each pair. Starlette's `str` converter is `[^/]+` so `{interaction_id}`
    cannot actually match `abc/history` — unlike `/risk/rules/categories`
    this order isn't defusing a live bug, it's kept for explicitness and
    future-proofing (see the issue #109 plan)."""
    return [
        Route(
            "/risk/interactions/{interaction_id:str}/history",
            endpoint=_interaction_history_handler,
            methods=["GET"],
        ),
        Route(
            "/risk/interactions/{interaction_id:str}",
            endpoint=_get_interaction_handler,
            methods=["GET"],
        ),
        Route(
            "/risk/interactions", endpoint=_list_interactions_handler, methods=["GET"]
        ),
        Route(
            "/risk/traces/{trace_id:str}/history",
            endpoint=_trace_history_handler,
            methods=["GET"],
        ),
        Route(
            "/risk/traces/{trace_id:str}",
            endpoint=_get_trace_detail_handler,
            methods=["GET"],
        ),
        Route("/risk/traces", endpoint=_list_traces_handler, methods=["GET"]),
    ]
