"""API feature-flag configuration tests."""

from __future__ import annotations

import httpx
import pytest

from data_governance import retrieval
from data_governance.api import build_app
from data_governance.api.__main__ import _bool_env


def test_connect_only_filter_is_enabled_by_default() -> None:
    app = build_app()
    assert app.state.hide_connect_only_traces is True


def test_connect_only_filter_can_be_disabled() -> None:
    app = build_app(hide_connect_only_traces=False)
    assert app.state.hide_connect_only_traces is False


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [True, False])
async def test_collection_threads_feature_flag_to_retrieval(
    monkeypatch,
    enabled: bool,
) -> None:
    calls: list[dict] = []

    def fake_get_spans(**kwargs):
        calls.append(kwargs)
        return retrieval.GetSpansResult(spans=[], counts={})

    monkeypatch.setattr(retrieval, "get_spans", fake_get_spans)
    app = build_app(hide_connect_only_traces=enabled)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        response = await client.get("/api/traces")

    assert response.status_code == 200
    assert calls == [
        {
            "cursor": None,
            "time_from": None,
            "time_to": None,
            "root_only": True,
            "hide_connect_only_traces": enabled,
        }
    ]


@pytest.mark.asyncio
async def test_singular_trace_does_not_apply_collection_filter(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_get_spans(**kwargs):
        calls.append(kwargs)
        return retrieval.GetSpansResult(spans=[], counts={})

    monkeypatch.setattr(retrieval, "get_spans", fake_get_spans)
    app = build_app(hide_connect_only_traces=True)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        response = await client.get("/api/traces/connect-only")

    assert response.status_code == 404
    assert calls == [{"root_only": True, "trace_id": "connect-only"}]


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_bool_env_accepts_true_values(monkeypatch, value: str) -> None:
    monkeypatch.setenv("FLAG", value)
    assert _bool_env("FLAG", False) is True


@pytest.mark.parametrize("value", ["0", "false", "FALSE", "no", "off"])
def test_bool_env_accepts_false_values(monkeypatch, value: str) -> None:
    monkeypatch.setenv("FLAG", value)
    assert _bool_env("FLAG", True) is False


def test_bool_env_uses_default_only_when_unset(monkeypatch) -> None:
    monkeypatch.delenv("FLAG", raising=False)
    assert _bool_env("FLAG", True) is True


def test_bool_env_rejects_unknown_value(monkeypatch) -> None:
    monkeypatch.setenv("FLAG", "sometimes")
    with pytest.raises(ValueError, match="FLAG must be"):
        _bool_env("FLAG", True)
