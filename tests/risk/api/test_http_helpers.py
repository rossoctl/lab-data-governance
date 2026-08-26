"""Tests for `data_governance.risk.api.http` (issue #113).

Shared error-format/pagination helpers reused by every `/risk/*` route this
issue and #109-#112/#156 add. No Starlette app, no DB — pure functions over
plain dicts and strings.
"""

from __future__ import annotations

import datetime as dt

import pytest

from data_governance.risk.api import http


# ---------------------------------------------------------------------------
# ApiError / error_response — FR-DAS-081 shape
# ---------------------------------------------------------------------------


def test_api_error_carries_error_and_detail():
    exc = http.ApiError("bad request", "limit must be positive")
    assert exc.error == "bad request"
    assert exc.detail == "limit must be positive"


def test_api_error_defaults_status_code_400():
    exc = http.ApiError("bad request", "x")
    assert exc.status_code == 400


def test_api_error_accepts_explicit_status_code():
    exc = http.ApiError("not found", "no such rule", status_code=404)
    assert exc.status_code == 404


def test_api_error_defaults_detail_to_empty_string():
    exc = http.ApiError("bad request")
    assert exc.detail == ""


def test_error_response_body_has_all_three_keys_always():
    exc = http.ApiError("bad request", "x")
    resp = http.error_response(exc)
    body = _json_body(resp)
    assert set(body.keys()) == {"error", "detail", "timestamp"}


def test_error_response_detail_present_even_when_empty():
    exc = http.ApiError("not found")
    resp = http.error_response(exc)
    body = _json_body(resp)
    assert body["detail"] == ""


def test_error_response_uses_the_exceptions_status_code():
    exc = http.ApiError("not found", "no such rule", status_code=404)
    resp = http.error_response(exc)
    assert resp.status_code == 404


def test_error_response_timestamp_is_iso8601_and_tz_aware():
    exc = http.ApiError("bad request", "x")
    resp = http.error_response(exc)
    body = _json_body(resp)
    parsed = dt.datetime.fromisoformat(body["timestamp"])
    assert parsed.tzinfo is not None


def test_error_response_error_and_detail_match_the_exception():
    exc = http.ApiError("bad request", "limit must be positive")
    resp = http.error_response(exc)
    body = _json_body(resp)
    assert body["error"] == "bad request"
    assert body["detail"] == "limit must be positive"


def test_error_response_media_type_is_json():
    exc = http.ApiError("bad request", "x")
    resp = http.error_response(exc)
    assert resp.media_type == "application/json"


# ---------------------------------------------------------------------------
# json_ok
# ---------------------------------------------------------------------------


def test_json_ok_status_200():
    resp = http.json_ok({"items": []})
    assert resp.status_code == 200


def test_json_ok_serializes_payload():
    resp = http.json_ok({"items": [1, 2, 3]})
    assert _json_body(resp) == {"items": [1, 2, 3]}


# ---------------------------------------------------------------------------
# parse_int
# ---------------------------------------------------------------------------


def test_parse_int_absent_returns_none():
    assert http.parse_int(None, "cursor") is None


def test_parse_int_valid_string_returns_int():
    assert http.parse_int("42", "cursor") == 42


def test_parse_int_non_numeric_raises_api_error():
    with pytest.raises(http.ApiError):
        http.parse_int("abc", "cursor")


def test_parse_int_error_names_the_field():
    with pytest.raises(http.ApiError) as exc_info:
        http.parse_int("abc", "cursor")
    assert "cursor" in exc_info.value.detail


# ---------------------------------------------------------------------------
# parse_csv_param — D7: absent -> None, "" -> [], else split
# ---------------------------------------------------------------------------


def test_parse_csv_param_absent_is_none():
    assert http.parse_csv_param({}, "category") is None


def test_parse_csv_param_empty_string_is_empty_list():
    assert http.parse_csv_param({"category": ""}, "category") == []


def test_parse_csv_param_single_value():
    assert http.parse_csv_param({"category": "a"}, "category") == ["a"]


def test_parse_csv_param_multiple_values():
    assert http.parse_csv_param({"category": "a,b"}, "category") == ["a", "b"]


def test_parse_csv_param_strips_whitespace():
    assert http.parse_csv_param({"category": " a , b "}, "category") == ["a", "b"]


def test_parse_csv_param_drops_blank_segments():
    assert http.parse_csv_param({"category": "a,,b"}, "category") == ["a", "b"]


# ---------------------------------------------------------------------------
# parse_limit — D4: reject over max, reject <= 0, reject non-int
# ---------------------------------------------------------------------------


def test_parse_limit_absent_uses_default():
    assert http.parse_limit({}, default=50, maximum=200) == 50


def test_parse_limit_explicit_value_honored():
    assert http.parse_limit({"limit": "10"}, default=50, maximum=200) == 10


def test_parse_limit_over_max_raises_400():
    with pytest.raises(http.ApiError) as exc_info:
        http.parse_limit({"limit": "201"}, default=50, maximum=200)
    assert exc_info.value.status_code == 400


def test_parse_limit_equal_to_max_is_allowed():
    assert http.parse_limit({"limit": "200"}, default=50, maximum=200) == 200


def test_parse_limit_zero_raises():
    with pytest.raises(http.ApiError):
        http.parse_limit({"limit": "0"}, default=50, maximum=200)


def test_parse_limit_negative_raises():
    with pytest.raises(http.ApiError):
        http.parse_limit({"limit": "-1"}, default=50, maximum=200)


def test_parse_limit_non_integer_raises():
    with pytest.raises(http.ApiError):
        http.parse_limit({"limit": "abc"}, default=50, maximum=200)


# ---------------------------------------------------------------------------
# cursor codec — D2: opaque index + sort fingerprint
# ---------------------------------------------------------------------------


def test_cursor_round_trips():
    token = http.encode_cursor(5, "rule_id_asc")
    assert http.decode_cursor(token, expect_sort="rule_id_asc") == 5


def test_cursor_is_opaque_not_a_bare_int():
    token = http.encode_cursor(5, "rule_id_asc")
    assert token != "5"
    with pytest.raises(ValueError):
        int(token)


def test_cursor_malformed_base64_raises_400():
    with pytest.raises(http.ApiError) as exc_info:
        http.decode_cursor("not-valid-base64!!!", expect_sort="rule_id_asc")
    assert exc_info.value.status_code == 400


def test_cursor_valid_base64_but_wrong_json_raises():
    import base64

    token = base64.urlsafe_b64encode(b"not json").decode("ascii")
    with pytest.raises(http.ApiError):
        http.decode_cursor(token, expect_sort="rule_id_asc")


def test_cursor_sort_fingerprint_mismatch_raises_400():
    token = http.encode_cursor(5, "rule_id_asc")
    with pytest.raises(http.ApiError) as exc_info:
        http.decode_cursor(token, expect_sort="risk_level_desc")
    assert exc_info.value.status_code == 400


def _encode_raw_cursor(payload: dict) -> str:
    import base64
    import json

    return base64.urlsafe_b64encode(json.dumps(payload).encode("ascii")).decode("ascii")


def test_cursor_non_integer_index_raises_400():
    token = _encode_raw_cursor({"i": "5", "s": "rule_id_asc"})
    with pytest.raises(http.ApiError) as exc_info:
        http.decode_cursor(token, expect_sort="rule_id_asc")
    assert exc_info.value.status_code == 400


def test_cursor_negative_index_raises_400():
    token = _encode_raw_cursor({"i": -1, "s": "rule_id_asc"})
    with pytest.raises(http.ApiError) as exc_info:
        http.decode_cursor(token, expect_sort="rule_id_asc")
    assert exc_info.value.status_code == 400


def test_cursor_zero_index_is_valid():
    """`i=0` is a legitimate first-page index, not a malformed cursor —
    only negative or non-integer values are rejected."""
    token = _encode_raw_cursor({"i": 0, "s": "rule_id_asc"})
    assert http.decode_cursor(token, expect_sort="rule_id_asc") == 0


# ---------------------------------------------------------------------------
# paginate
# ---------------------------------------------------------------------------


def test_paginate_first_page_no_cursor():
    rows = list(range(10))
    page = http.paginate(rows, cursor=None, limit=3, sort="rule_id_asc")
    assert page.items == [0, 1, 2]
    assert page.next_cursor is not None


def test_paginate_interior_page():
    rows = list(range(10))
    page1 = http.paginate(rows, cursor=None, limit=3, sort="rule_id_asc")
    page2 = http.paginate(rows, cursor=page1.next_cursor, limit=3, sort="rule_id_asc")
    assert page2.items == [3, 4, 5]


def test_paginate_final_partial_page():
    rows = list(range(10))
    cursor = None
    seen: list[int] = []
    for _ in range(10):
        page = http.paginate(rows, cursor=cursor, limit=3, sort="rule_id_asc")
        seen.extend(page.items)
        cursor = page.next_cursor
        if cursor is None:
            break
    assert seen == rows


def test_paginate_exact_multiple_of_limit_terminates():
    """len(rows) == 2*limit must not yield a phantom third empty page."""
    rows = list(range(6))
    page1 = http.paginate(rows, cursor=None, limit=3, sort="rule_id_asc")
    page2 = http.paginate(rows, cursor=page1.next_cursor, limit=3, sort="rule_id_asc")
    assert page2.items == [3, 4, 5]
    assert page2.next_cursor is None


def test_paginate_cursor_past_end_returns_empty_not_error():
    rows = list(range(3))
    far_cursor = http.encode_cursor(100, "rule_id_asc")
    page = http.paginate(rows, cursor=far_cursor, limit=3, sort="rule_id_asc")
    assert page.items == []
    assert page.next_cursor is None


def test_paginate_empty_input():
    page = http.paginate([], cursor=None, limit=3, sort="rule_id_asc")
    assert page.items == []
    assert page.next_cursor is None


def _json_body(resp) -> dict:
    import json

    return json.loads(resp.body)


# ---------------------------------------------------------------------------
# encode_keyset_cursor / decode_keyset_cursor (issue #109)
# ---------------------------------------------------------------------------


def test_keyset_cursor_round_trips():
    token = http.encode_keyset_cursor({"computed_at": "2026-01-01T00:00:00+00:00", "id": "a"}, "computed_at_desc")
    key = http.decode_keyset_cursor(
        token, expect_sort="computed_at_desc", expect_fields=("computed_at", "id")
    )
    assert key == {"computed_at": "2026-01-01T00:00:00+00:00", "id": "a"}


def test_keyset_cursor_is_opaque():
    token = http.encode_keyset_cursor({"version": 3}, "version_asc")
    assert token != "3"
    assert "version" not in token


def test_keyset_cursor_malformed_base64_raises_400():
    with pytest.raises(http.ApiError) as exc_info:
        http.decode_keyset_cursor(
            "not-valid-base64!!!", expect_sort="version_asc", expect_fields=("version",)
        )
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "cursor is malformed"


def test_keyset_cursor_valid_base64_but_wrong_json_raises():
    import base64

    token = base64.urlsafe_b64encode(b"not json").decode("ascii")
    with pytest.raises(http.ApiError) as exc_info:
        http.decode_keyset_cursor(
            token, expect_sort="version_asc", expect_fields=("version",)
        )
    assert exc_info.value.detail == "cursor is malformed"


def test_keyset_cursor_sort_mismatch_raises_400():
    token = http.encode_keyset_cursor({"version": 3}, "version_asc")
    with pytest.raises(http.ApiError) as exc_info:
        http.decode_keyset_cursor(
            token, expect_sort="computed_at_desc", expect_fields=("version",)
        )
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "cursor does not match the requested sort"


def test_keyset_cursor_field_mismatch_raises_400():
    """A cursor minted for /risk/interactions replayed against /risk/traces
    (or history vs. list) must be rejected even if `sort` happens to match —
    the field set is a second fingerprint axis."""
    token = http.encode_keyset_cursor(
        {"computed_at": "2026-01-01T00:00:00+00:00", "id": "a"}, "computed_at_desc"
    )
    with pytest.raises(http.ApiError) as exc_info:
        http.decode_keyset_cursor(
            token, expect_sort="computed_at_desc", expect_fields=("version",)
        )
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "cursor does not match the requested sort"


def test_keyset_cursor_missing_key_field_raises_400():
    token = http.encode_keyset_cursor({"computed_at": "x"}, "computed_at_desc")
    with pytest.raises(http.ApiError) as exc_info:
        http.decode_keyset_cursor(
            token, expect_sort="computed_at_desc", expect_fields=("computed_at", "id")
        )
    assert exc_info.value.status_code == 400


def test_keyset_cursor_not_a_dict_payload_raises_400():
    token = _encode_raw_cursor({"k": "not-a-dict", "s": "version_asc"})
    with pytest.raises(http.ApiError) as exc_info:
        http.decode_keyset_cursor(
            token, expect_sort="version_asc", expect_fields=("version",)
        )
    assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# parse_iso_datetime (issue #109) — local copy of api/__init__._parse_iso_datetime,
# raising http.ApiError (400) instead of ValueError, per FR-DAS-081.
# ---------------------------------------------------------------------------


def test_parse_iso_datetime_none_returns_none():
    assert http.parse_iso_datetime(None, "time_from") is None


def test_parse_iso_datetime_empty_string_returns_none():
    assert http.parse_iso_datetime("", "time_from") is None


def test_parse_iso_datetime_accepts_z_suffix():
    parsed = http.parse_iso_datetime("2026-01-01T00:00:00Z", "time_from")
    assert parsed == dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)


def test_parse_iso_datetime_accepts_explicit_offset():
    parsed = http.parse_iso_datetime("2026-01-01T00:00:00+02:00", "time_from")
    assert parsed.utcoffset() == dt.timedelta(hours=2)


def test_parse_iso_datetime_rejects_naive_datetime():
    with pytest.raises(http.ApiError) as exc_info:
        http.parse_iso_datetime("2026-01-01T00:00:00", "time_from")
    assert exc_info.value.status_code == 400
    assert "time_from" in exc_info.value.detail


def test_parse_iso_datetime_rejects_malformed_string():
    with pytest.raises(http.ApiError) as exc_info:
        http.parse_iso_datetime("not-a-date", "time_from")
    assert exc_info.value.status_code == 400
    assert "time_from" in exc_info.value.detail


def test_parse_iso_datetime_names_the_field_in_error():
    with pytest.raises(http.ApiError) as exc_info:
        http.parse_iso_datetime("garbage", "time_to")
    assert "time_to" in exc_info.value.detail
