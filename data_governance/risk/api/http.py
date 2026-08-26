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

    Local copy of the UI API package's own ``_parse_iso_datetime`` helper
    (per-module, not imported: the risk API must never import that package —
    see ``test_risk_api_does_not_import_the_ui_api_module``). Raises
    ``ApiError`` (FR-DAS-081) rather than that copy's bare ``ValueError``,
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


# ---------------------------------------------------------------------------
# window resolution (issue #111, shared with #109-#112/#156) — the PRD's
# `window` query param (`24h`/`7d`/`30d`/`custom`) resolved into a concrete
# `Window`. Metrics-adjacent but not metrics-specific: every one of the
# PRD §7.4 endpoints that takes a time range takes this same param, so it
# lives here rather than in `metrics_routes.py`.
# ---------------------------------------------------------------------------

WINDOW_CUSTOM = "custom"

WINDOW_DELTAS: dict[str, dt.timedelta] = {
    "24h": dt.timedelta(hours=24),
    "7d": dt.timedelta(days=7),
    "30d": dt.timedelta(days=30),
}

DEFAULT_WINDOW = "24h"

# Derived from WINDOW_DELTAS so a new fixed window can't be added there
# without also being accepted here.
WINDOW_VALUES: tuple[str, ...] = (*WINDOW_DELTAS, WINDOW_CUSTOM)


def _now() -> dt.datetime:
    """Module-level seam so tests can freeze "now" (`monkeypatch.setattr(http,
    "_now", lambda: FROZEN)`) without a freezegun dependency (none exists in
    this repo). Deliberately not used by `error_response`'s own `datetime.now`
    call — freezing a window must not also freeze error timestamps."""
    return dt.datetime.now(dt.timezone.utc)


@dataclass(frozen=True)
class Window:
    """A resolved `window`/`from`/`to` triple, ready to pass straight through
    to `data_governance.risk.metrics.aggregate`'s `time_from`/`time_to`
    keyword args.

    Note on interval semantics: `aggregate.py`'s own `_window_predicate` is
    actually a **closed** interval (`>= time_from AND <= time_to`), despite
    that module's docstring claiming half-open `[time_from, time_to)`. This
    dataclass documents the real (closed) behavior rather than the aspirational
    one; a record with `computed_at == time_from` or `== time_to` IS included.
    """

    label: str
    time_from: dt.datetime
    time_to: dt.datetime
    computed_at: dt.datetime


def resolve_window(params: Any) -> Window:
    """Resolve the PRD `window`/`from`/`to` query params into a `Window`.

    - absent/empty `window` -> `DEFAULT_WINDOW` ("24h").
    - a fixed window (`24h`/`7d`/`30d`) -> `time_to = _now()`, `time_from =
      time_to - delta`; any stray `from`/`to` params are ignored, not 400 —
      a client toggling back from `custom` may leave them in the URL.
    - `custom` -> both `from` and `to` are required (400 naming whichever is
      missing), parsed via `parse_iso_datetime` (tz-aware only); `from` must
      not be after `to` as instants (bare wall-clock comparison would be
      wrong across differing UTC offsets) — `from == to` is allowed.
    - unknown `window` value -> 400, case-sensitive.
    """
    raw = params.get("window") or DEFAULT_WINDOW
    if raw not in WINDOW_VALUES:
        raise ApiError(
            "bad request", f"window must be one of {list(WINDOW_VALUES)}, got {raw!r}"
        )

    if raw != WINDOW_CUSTOM:
        now = _now()
        return Window(
            label=raw, time_from=now - WINDOW_DELTAS[raw], time_to=now, computed_at=now
        )

    time_from = parse_iso_datetime(params.get("from"), "from")
    time_to = parse_iso_datetime(params.get("to"), "to")
    missing = [name for name, value in (("from", time_from), ("to", time_to)) if value is None]
    if missing:
        raise ApiError(
            "bad request", f"window=custom requires {' and '.join(missing)}"
        )
    if time_from > time_to:
        raise ApiError("bad request", "'from' must not be after 'to'")
    return Window(
        label=WINDOW_CUSTOM, time_from=time_from, time_to=time_to, computed_at=_now()
    )
