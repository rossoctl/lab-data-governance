"""SPA-serving contract for the React UI (ADR-0019).

ADR-0019 restructures the ``/ui/`` namespace: the vanilla-shell handlers
(``_ui_handler`` / ``_trace_tree_handler`` / ``_ui_asset_handler``) and the
``_UI_ASSETS`` whitelist are replaced by

  - a ``StaticFiles`` mount at ``/ui/assets`` (Vite's content-hashed bundles), and
  - a catch-all ``/ui`` + ``/ui/{path:path}`` that returns the SPA ``index.html``

so client-side routing (React Router ``basename="/ui"``) resolves deep links
like ``/ui/traces/{tid}`` without a server-side page per route. ``/api/`` and
``/`` → 302 ``/ui/`` and ``/healthz`` are untouched (ADR-0017 namespacing).

These tests pin that wire contract. They serve a **fixture** ``dist/`` (a
minimal ``index.html`` + one hashed ``assets/`` bundle, mimicking Vite output)
rather than running a real ``npm run build`` each run — the real build is
validated once at verification time. ``_UI_DIR`` is pointed at the fixture via
monkeypatch before the server starts, exactly as the runtime image points it at
the baked-in Vite output.
"""

from __future__ import annotations

import socket
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

import data_governance.api as api_mod
from data_governance.api import SpansApiServer

_FIXTURE_DIST = Path(__file__).resolve().parents[1] / "fixtures" / "ui_dist"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_port(host: str, port: int, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.2)
            try:
                s.connect((host, port))
                return
            except OSError:
                time.sleep(0.05)
    raise TimeoutError(f"port {host}:{port} did not open within {timeout}s")


@pytest.fixture()
def spa_server(
    configured_db: str, monkeypatch: pytest.MonkeyPatch
) -> Iterator[SpansApiServer]:
    """An API server serving the SPA from the committed fixture ``dist/``.

    Points ``_UI_DIR`` at the fixture before ``build_app()`` runs (the mount and
    catch-all resolve their paths at app-construction time), then starts a real
    uvicorn instance — same code path the runtime image uses.
    """
    monkeypatch.setattr(api_mod, "_UI_DIR", _FIXTURE_DIST)
    port = _free_port()
    server = SpansApiServer(host="127.0.0.1", port=port)
    server.start()
    try:
        _wait_port("127.0.0.1", port)
        yield server
    finally:
        server.stop(grace=1.0)


def _base(server: SpansApiServer) -> str:
    return f"http://127.0.0.1:{server.port}"


# ---------------------------------------------------------------------------
# /ui/ shell + client-side routing catch-all
# ---------------------------------------------------------------------------


def test_ui_root_serves_spa_index(spa_server):
    """``GET /ui`` returns the SPA shell HTML (the built ``index.html``)."""
    resp = httpx.get(f"{_base(spa_server)}/ui")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert '<div id="root"></div>' in resp.text


def test_ui_trailing_slash_serves_spa_index(spa_server):
    """``GET /ui/`` also returns the shell (the 302 target of ``/``)."""
    resp = httpx.get(f"{_base(spa_server)}/ui/")
    assert resp.status_code == 200
    assert '<div id="root"></div>' in resp.text


def test_deep_link_serves_spa_index_for_client_routing(spa_server):
    """A deep link like ``/ui/traces/{tid}`` returns the SPA shell, not a 404 —
    React Router resolves the route client-side (ADR-0019 catch-all)."""
    resp = httpx.get(f"{_base(spa_server)}/ui/traces/abc123")
    assert resp.status_code == 200
    assert '<div id="root"></div>' in resp.text


def test_nested_deep_link_serves_spa_index(spa_server):
    """The catch-all matches arbitrary depth (``{path:path}``)."""
    resp = httpx.get(f"{_base(spa_server)}/ui/traces/abc123/graph")
    assert resp.status_code == 200
    assert '<div id="root"></div>' in resp.text


# ---------------------------------------------------------------------------
# /ui/assets — Vite's content-hashed bundles via StaticFiles
# ---------------------------------------------------------------------------


def test_hashed_asset_served_with_js_media_type(spa_server):
    """The fixture bundle under ``/ui/assets/`` is served by the StaticFiles
    mount with a JavaScript media type — content-hashed names can't be
    whitelisted ahead of time, so the mount replaces ``_UI_ASSETS``."""
    resp = httpx.get(f"{_base(spa_server)}/ui/assets/index-fixture.js")
    assert resp.status_code == 200
    assert "javascript" in resp.headers["content-type"]
    assert "fixture bundle" in resp.text


def test_unknown_asset_returns_404(spa_server):
    """An asset that isn't in ``dist/assets`` is a 404 from the mount — the
    catch-all must NOT swallow ``/ui/assets/*`` and return index.html for it."""
    resp = httpx.get(f"{_base(spa_server)}/ui/assets/does-not-exist.js")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Untouched namespaces (ADR-0017) still hold
# ---------------------------------------------------------------------------


def test_root_redirects_to_ui(spa_server):
    resp = httpx.get(f"{_base(spa_server)}/", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/ui/"


def test_healthz_still_ok(spa_server):
    resp = httpx.get(f"{_base(spa_server)}/healthz")
    assert resp.status_code == 200
    assert resp.text == "ok"


def test_api_namespace_still_serves_json(spa_server):
    """``/api/...`` is untouched by the ``/ui/`` restructure — the traces feed
    still returns JSON, not the SPA shell."""
    resp = httpx.get(f"{_base(spa_server)}/api/traces")
    assert resp.status_code == 200
    assert "application/json" in resp.headers["content-type"]
    assert "traces" in resp.json()


# ---------------------------------------------------------------------------
# Missing build → clean 503, not an opaque 500
# ---------------------------------------------------------------------------


@pytest.fixture()
def buildless_server(
    configured_db: str, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> Iterator[SpansApiServer]:
    """A server whose ``_UI_DIR`` has no ``index.html`` (a build-less checkout).

    The ``/ui/assets`` mount already tolerates a missing dir via
    ``check_dir=False``; this exercises the symmetrical catch-all path.
    """
    monkeypatch.setattr(api_mod, "_UI_DIR", tmp_path)  # empty dir, no index.html
    port = _free_port()
    server = SpansApiServer(host="127.0.0.1", port=port)
    server.start()
    try:
        _wait_port("127.0.0.1", port)
        yield server
    finally:
        server.stop(grace=1.0)


def test_missing_build_serves_503_not_500(buildless_server):
    """Without a Vite build, ``/ui`` returns a clean 503 (deploy problem to
    surface), not an opaque 500 from an unhandled FileNotFoundError."""
    resp = httpx.get(f"{_base(buildless_server)}/ui")
    assert resp.status_code == 503
    assert "UI build not found" in resp.text
