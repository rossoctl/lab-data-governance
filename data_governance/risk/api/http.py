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
    index = payload["i"]
    if not isinstance(index, int) or isinstance(index, bool) or index < 0:
        raise ApiError("bad request", "cursor is malformed")
    if payload["s"] != expect_sort:
        raise ApiError("bad request", "cursor does not match the requested sort")
    return index


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


# ---------------------------------------------------------------------------
# DB-level keyset cursor (issue #109) — distinct from the index-offset
# `encode_cursor`/`decode_cursor`/`paginate` above, which materialize the
# full result set (fine for #113's bounded in-memory rule catalog, wrong for
# a growing, insert-only-and-versioned table: an offset cursor skips/dups
# rows across a live recompute — see retrieval/spans.py's issue #30 note).
# The cursor carries the actual sort-key values of the last row seen, so a
# caller resumes "after this row" rather than "at this numeric offset."
# ---------------------------------------------------------------------------


def encode_keyset_cursor(key: dict[str, Any], sort: str) -> str:
    payload = json.dumps({"k": key, "s": sort}, default=_json_default).encode("ascii")
    return base64.urlsafe_b64encode(payload).decode("ascii")


def decode_keyset_cursor(
    token: str, *, expect_sort: str, expect_fields: tuple[str, ...]
) -> dict[str, Any]:
    try:
        raw = base64.urlsafe_b64decode(token.encode("ascii"))
        payload = json.loads(raw)
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise ApiError("bad request", "cursor is malformed") from exc
    if not isinstance(payload, dict) or "k" not in payload or "s" not in payload:
        raise ApiError("bad request", "cursor is malformed")
    key = payload["k"]
    if not isinstance(key, dict):
        raise ApiError("bad request", "cursor is malformed")
    if payload["s"] != expect_sort or set(key.keys()) != set(expect_fields):
        raise ApiError("bad request", "cursor does not match the requested sort")
    return key


def parse_iso_datetime(value: str | None, name: str) -> dt.datetime | None:
    """Parse an ISO-8601 datetime; reject naive (no Z, no explicit offset).

    Local copy of ``data_governance/api/__init__.py::_parse_iso_datetime``
    (per-module, not imported: the risk API must never import
    ``data_governance.api`` — see ``test_risk_api_does_not_import_the_ui_api_module``).
    Raises ``ApiError`` (FR-DAS-081) rather than that copy's bare ``ValueError``,
    since every risk API 400 must carry the error/detail/timestamp triple.
    """
    if value is None or value == "":
        return None
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError:
        raise ApiError(
            "bad request",
            f"'{name}' must be ISO-8601 with Z or explicit offset, got {value!r}",
        ) from None
    if parsed.tzinfo is None or parsed.tzinfo.utcoffset(parsed) is None:
        raise ApiError(
            "bad request",
            f"'{name}' must be ISO-8601 with Z or explicit offset; "
            f"naive datetimes are rejected",
        )
    return parsed
