"""Shared FR-DAS-081 error format and cursor/limit pagination helpers.

Reused by every ``/risk/*`` route (issue #113's routes, plus #109-#112/#156).
The error shape here is deliberately richer than the older ``/api/`` routes'
plain ``{"error": "..."}`` — see PRD implementation-notes-v3 §8.1.
"""

from __future__ import annotations

import base64
import binascii
import datetime as dt
import json
from dataclasses import dataclass
from typing import Any

from starlette.responses import Response


class ApiError(Exception):
    """Carries the FR-DAS-081 error triple; caught at the route boundary."""

    def __init__(self, error: str, detail: str = "", *, status_code: int = 400):
        super().__init__(error)
        self.error = error
        self.detail = detail
        self.status_code = status_code


def _json_default(obj: Any) -> Any:
    if isinstance(obj, dt.datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _dumps(payload: Any) -> str:
    return json.dumps(payload, default=_json_default)


def json_ok(payload: Any) -> Response:
    return Response(_dumps(payload), status_code=200, media_type="application/json")


def error_response(exc: ApiError) -> Response:
    body = {
        "error": exc.error,
        "detail": exc.detail,
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    return Response(
        _dumps(body), status_code=exc.status_code, media_type="application/json"
    )


def parse_int(value: str | None, name: str) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ApiError("bad request", f"{name} must be an integer") from None


def parse_csv_param(params: Any, name: str) -> list[str] | None:
    raw = params.get(name)
    if raw is None:
        return None
    if raw == "":
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def parse_limit(params: Any, *, default: int, maximum: int) -> int:
    name = "limit"
    raw = params.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ApiError("bad request", f"{name} must be an integer") from None
    if value <= 0 or value > maximum:
        raise ApiError(
            "bad request", f"{name} must be between 1 and {maximum}"
        )
    return value


def encode_cursor(index: int, sort: str) -> str:
    payload = json.dumps({"i": index, "s": sort}).encode("ascii")
    return base64.urlsafe_b64encode(payload).decode("ascii")


def decode_cursor(token: str, *, expect_sort: str) -> int:
    try:
        raw = base64.urlsafe_b64decode(token.encode("ascii"))
        payload = json.loads(raw)
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise ApiError("bad request", "cursor is malformed") from exc
    if not isinstance(payload, dict) or "i" not in payload or "s" not in payload:
        raise ApiError("bad request", "cursor is malformed")
    if payload["s"] != expect_sort:
        raise ApiError("bad request", "cursor does not match the requested sort")
    return payload["i"]


@dataclass
class Page:
    items: list
    next_cursor: str | None


def paginate(rows: list, *, cursor: str | None, limit: int, sort: str) -> Page:
    start = decode_cursor(cursor, expect_sort=sort) if cursor is not None else 0
    items = rows[start : start + limit]
    end = start + len(items)
    next_cursor = encode_cursor(end, sort) if end < len(rows) else None
    return Page(items=items, next_cursor=next_cursor)
