"""`/risk/rules*` — DAS rule catalog HTTP adapter (issue #113).

Thin adapter over `data_governance.risk.rules.catalog` (issue #107, read-only,
unchanged here). Response shapes and error format follow FR-DAS-081 and the
cursor/limit pagination convention documented in implementation-notes-v3 §8.1;
see the issue #113 plan for the full deviation rationale from PRD v8 §7.7.
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import BaseRoute, Route

from data_governance.risk import config
from data_governance.risk.api import http
from data_governance.risk.rules import catalog

# D5: HTTP `sort` values, deliberately narrower than catalog.SORT_KEYS — only
# these are exposed. risk_level_desc/enforcement_desc -> descending=False
# because RISK_LEVEL_ORDER/ENFORCEMENT_ORDER are already most-severe-first
# (see plan finding #1).
_SORT_MAP = {
    "rule_id_asc": ("rule_id", False),
    "risk_level_desc": ("risk_level", False),
    "enforcement_desc": ("enforcement", False),
}
_DEFAULT_SORT = "rule_id_asc"


async def _categories_handler(_request: Request) -> Response:
    counts = catalog.category_counts()
    items = [
        {"category": name, "rule_count": count}
        for name, count in sorted(counts.items())
    ]
    return http.json_ok({"items": items})


async def _rule_detail_handler(request: Request) -> Response:
    rule_id = request.path_params["rule_id"]
    rule = catalog.get_rule(rule_id)
    if rule is None:
        return http.error_response(
            http.ApiError("not found", f"no rule with id {rule_id!r}", status_code=404)
        )
    return http.json_ok(rule)


async def _rules_list_handler(request: Request) -> Response:
    params = request.query_params
    try:
        sort = params.get("sort") or _DEFAULT_SORT
        if sort not in _SORT_MAP:
            raise http.ApiError(
                "bad request",
                f"sort must be one of {sorted(_SORT_MAP)}, got {sort!r}",
            )
        sort_by, descending = _SORT_MAP[sort]

        category = http.parse_csv_param(params, "category")
        risk_level = http.parse_csv_param(params, "risk_level")
        cursor = params.get("cursor")
        limit = http.parse_limit(
            params,
            default=config.API_RISK_RULES_DEFAULT_LIMIT,
            maximum=config.API_RISK_RULES_MAX_LIMIT,
        )

        rows = catalog.list_rules(
            category=category,
            risk_level=risk_level,
            sort_by=sort_by,
            descending=descending,
        )
        page = http.paginate(rows, cursor=cursor, limit=limit, sort=sort)
    except http.ApiError as exc:
        return http.error_response(exc)

    return http.json_ok({"items": page.items, "next_cursor": page.next_cursor})


def routes() -> list[BaseRoute]:
    """`/risk/rules/categories` MUST be registered before `/risk/rules/{rule_id}` —
    otherwise a Starlette router matches "categories" as a `rule_id` (D6), the
    same catch-all-ordering hazard as the `/ui/assets` mount in
    `data_governance/api/__init__.py`."""
    return [
        Route("/risk/rules/categories", endpoint=_categories_handler, methods=["GET"]),
        Route("/risk/rules/{rule_id:str}", endpoint=_rule_detail_handler, methods=["GET"]),
        Route("/risk/rules", endpoint=_rules_list_handler, methods=["GET"]),
    ]
