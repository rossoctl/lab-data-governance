"""`/risk/metrics/*` — DAS dashboard metrics REST API (issue #111, PRD §4.6/§7.4).

Thin adapter over `data_governance.risk.metrics.aggregate`'s six query-layer
functions (issue #106). This module does no arithmetic of its own — every
derived value (percentages, totals) is already a property on #106's
dataclasses, including the zero-division guards (`TileCounts.risky_pct`,
`EnforcementDistribution.pct`/`.total`). It also does no pagination: these
are bounded aggregates and top-N rankings, not lists, so `top-rules`/
`top-traces` take `limit` but no cursor (implementation-notes-v3 §8.2's
documented exception for metrics endpoints) and no body here ever carries a
`next_cursor` key.

`window`/`from`/`to` resolution is shared, not reimplemented here — see
`http.resolve_window`. Only `/summary` echoes `from`/`to`; the other five
echo `window` alone (PRD §7.4's asymmetry, not this module's choice).

The top-traces leaderboard reuses `risk_routes.trace_risk_to_json` rather
than re-serializing `TraceRiskView` itself — see that function's docstring.
"""

from __future__ import annotations

from typing import Any

from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import BaseRoute, Route

from data_governance.risk import config
from data_governance.risk.api import http
from data_governance.risk.api.risk_routes import trace_risk_to_json
from data_governance.risk.metrics import aggregate


def _tile_to_json(tile: aggregate.TileCounts) -> dict[str, Any]:
    return {
        "total": tile.total,
        "risky": tile.risky,
        "risky_pct": tile.risky_pct,
    }


def _top_rule_to_json(item: aggregate.TopRuleView) -> dict[str, Any]:
    return {
        "rule_id": item.rule_id,
        "rule_name": item.rule_name,
        "count": item.count,
        "trace_count": item.trace_count,
        "risk_level": item.risk_level,
        "risk_level_distribution": item.risk_level_distribution,
    }


def _category_to_json(item: aggregate.CategoryCount) -> dict[str, Any]:
    return {"category": item.category, "count": item.count}


# ---------------------------------------------------------------------------
# GET /risk/metrics/summary
# ---------------------------------------------------------------------------


async def _summary_handler(request: Request) -> Response:
    try:
        window = http.resolve_window(request.query_params)
    except http.ApiError as exc:
        return http.error_response(exc)

    metrics = aggregate.get_summary(
        time_from=window.time_from, time_to=window.time_to
    )
    return http.json_ok(
        {
            "window": window.label,
            "from": window.time_from,
            "to": window.time_to,
            "agents": _tile_to_json(metrics.agents),
            "users": _tile_to_json(metrics.users),
            "workflows": _tile_to_json(metrics.workflows),
            "evaluated_interactions": _tile_to_json(metrics.evaluated_interactions),
            "rules_fired": {
                "total": metrics.rules_fired.total,
                "critical": metrics.rules_fired.critical,
            },
            "computed_at": window.computed_at,
        }
    )


# ---------------------------------------------------------------------------
# GET /risk/metrics/risk-distribution
# ---------------------------------------------------------------------------


async def _risk_distribution_handler(request: Request) -> Response:
    try:
        window = http.resolve_window(request.query_params)
    except http.ApiError as exc:
        return http.error_response(exc)

    dist = aggregate.get_risk_distribution(
        time_from=window.time_from, time_to=window.time_to
    )
    return http.json_ok(
        {
            "window": window.label,
            "distribution": {
                "critical": dist.critical,
                "high": dist.high,
                "medium": dist.medium,
                "low": dist.low,
                "none": dist.none,
                "total": dist.total,
            },
            "computed_at": window.computed_at,
        }
    )


# ---------------------------------------------------------------------------
# GET /risk/metrics/enforcement-distribution
# ---------------------------------------------------------------------------


async def _enforcement_distribution_handler(request: Request) -> Response:
    try:
        window = http.resolve_window(request.query_params)
    except http.ApiError as exc:
        return http.error_response(exc)

    dist = aggregate.get_enforcement_distribution(
        time_from=window.time_from, time_to=window.time_to
    )
    pct = dist.pct
    distribution = {
        enforcement_type: {"count": count, "pct": pct[enforcement_type]}
        for enforcement_type, count in sorted(dist.counts.items())
    }
    return http.json_ok(
        {
            "window": window.label,
            "distribution": distribution,
            "total": dist.total,
            "computed_at": window.computed_at,
        }
    )


# ---------------------------------------------------------------------------
# GET /risk/metrics/top-rules
# ---------------------------------------------------------------------------


async def _top_rules_handler(request: Request) -> Response:
    params = request.query_params
    try:
        window = http.resolve_window(params)
        limit = http.parse_limit(
            params,
            default=config.API_METRICS_TOP_RULES_DEFAULT_LIMIT,
            maximum=config.API_METRICS_TOP_RULES_MAX_LIMIT,
        )
    except http.ApiError as exc:
        return http.error_response(exc)

    items = aggregate.get_top_rules(
        time_from=window.time_from, time_to=window.time_to, limit=limit
    )
    return http.json_ok(
        {
            "window": window.label,
            "items": [_top_rule_to_json(item) for item in items],
        }
    )


# ---------------------------------------------------------------------------
# GET /risk/metrics/top-traces
# ---------------------------------------------------------------------------


async def _top_traces_handler(request: Request) -> Response:
    params = request.query_params
    try:
        window = http.resolve_window(params)
        limit = http.parse_limit(
            params,
            default=config.API_METRICS_TOP_TRACES_DEFAULT_LIMIT,
            maximum=config.API_METRICS_TOP_TRACES_MAX_LIMIT,
        )
    except http.ApiError as exc:
        return http.error_response(exc)

    items = aggregate.get_top_traces(
        time_from=window.time_from, time_to=window.time_to, limit=limit
    )
    return http.json_ok(
        {
            "window": window.label,
            "items": [trace_risk_to_json(item) for item in items],
        }
    )


# ---------------------------------------------------------------------------
# GET /risk/metrics/risk-by-category
# ---------------------------------------------------------------------------


async def _risk_by_category_handler(request: Request) -> Response:
    try:
        window = http.resolve_window(request.query_params)
    except http.ApiError as exc:
        return http.error_response(exc)

    items = aggregate.get_risk_by_category(
        time_from=window.time_from, time_to=window.time_to
    )
    return http.json_ok(
        {
            "window": window.label,
            "items": [_category_to_json(item) for item in items],
        }
    )


def routes() -> list[BaseRoute]:
    """All six paths are literal (no `{param}`), so unlike `risk_routes.py`'s
    `/history`-before-`{id}` pairs or `rules_routes.py`'s `categories`-before-
    `{rule_id}` catch-all, there is no ordering hazard between any of these
    — order here is arbitrary."""
    return [
        Route("/risk/metrics/summary", endpoint=_summary_handler, methods=["GET"]),
        Route(
            "/risk/metrics/risk-distribution",
            endpoint=_risk_distribution_handler,
            methods=["GET"],
        ),
        Route(
            "/risk/metrics/enforcement-distribution",
            endpoint=_enforcement_distribution_handler,
            methods=["GET"],
        ),
        Route(
            "/risk/metrics/top-rules", endpoint=_top_rules_handler, methods=["GET"]
        ),
        Route(
            "/risk/metrics/top-traces", endpoint=_top_traces_handler, methods=["GET"]
        ),
        Route(
            "/risk/metrics/risk-by-category",
            endpoint=_risk_by_category_handler,
            methods=["GET"],
        ),
    ]
