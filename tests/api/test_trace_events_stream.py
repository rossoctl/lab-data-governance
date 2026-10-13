"""Wire-contract tests for the trace change stream — ADR-0034.

``GET /api/traces/{tid}/events`` is a Server-Sent Events stream. These tests
drive it over a real socket against a real Postgres (the migration's own
NOTIFY triggers are what wake the hub), and prove the contract the SPA relies
on:

- the first event is a ``change`` carrying the trace's current version token;
- a derived write to THIS trace produces a new ``change`` promptly — through
  the ``LISTEN`` wake, i.e. well inside the poll backstop;
- a write to ANOTHER trace produces no event (the token is trace-scoped even
  though the notification is not);
- a hub with no ``LISTEN`` DSN still delivers, on the backstop alone;
- a closed stream releases its subscription (no waiter leak).
"""

from __future__ import annotations

import socket
import threading
import time
import uuid
from collections.abc import Iterator

import httpx
import psycopg
import pytest

from data_governance import retrieval
from data_governance.api import SpansApiServer
from data_governance.api.live import BACKSTOP_SECONDS, COALESCE_SECONDS

from .conftest import _free_port, _wait_port

_TID = "live-trace"
_OTHER_TID = "other-trace"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_entity(conn, *, entity_id: str, trace_id: str, span_id: str) -> None:
    conn.execute(
        "INSERT INTO spans (trace_id, span_id, parent_id, kind, name, "
        "started_at, attributes, seq, arrival_seq, observed_at) "
        "VALUES (%s, %s, NULL, 'INTERNAL', 'call', now(), '{}'::jsonb, "
        "nextval('spans_seq'), currval('spans_seq'), now())",
        (trace_id, span_id),
    )
    conn.execute(
        "INSERT INTO entities (id, kind, natural_key, display_name, namespace, "
        "detected_from, original_seq) "
        "VALUES (%s, 'agent', %s, %s, 'team1', 'span-attr', 1) "
        "ON CONFLICT (id) DO NOTHING",
        (entity_id, f"nk-{entity_id}", entity_id),
    )
    conn.execute(
        "INSERT INTO entity_spans (entity_id, trace_id, span_id, role) "
        "VALUES (%s, %s, %s, 'identified_via')",
        (entity_id, trace_id, span_id),
    )


def _add_interaction(conn, *, trace_id: str, entity_id: str) -> str:
    """One interaction with a request leg — the derived write a live page sees."""
    ix_id = f"ix-{uuid.uuid4().hex[:8]}"
    conn.execute(
        "INSERT INTO interactions (id, trace_id, caller_entity_id, "
        "callee_entity_id, summary) VALUES (%s, %s, %s, %s, 'did a thing')",
        (ix_id, trace_id, entity_id, entity_id),
    )
    conn.execute(
        "INSERT INTO interaction_legs (interaction_id, leg_type, occurred_at, "
        "payload_hash, error) VALUES (%s, 'request', now(), NULL, false)",
        (ix_id,),
    )
    conn.commit()
    return ix_id


class _SseReader:
    """Read SSE frames off an httpx stream on a thread, so a test can wait for
    the next event with a timeout and the stream's blocking read never pins
    the test thread."""

    def __init__(self, url: str) -> None:
        self.error: Exception | None = None
        self._events: list[dict[str, str]] = []
        self._comments: list[str] = []
        self._lock = threading.Condition()
        self._closed = threading.Event()
        self._client = httpx.Client(timeout=httpx.Timeout(10.0, read=None))
        self._resp = self._client.send(self._client.build_request("GET", url), stream=True)
        assert self._resp.status_code == 200
        assert self._resp.headers["content-type"].startswith("text/event-stream")
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()

    def _pump(self) -> None:
        frame: dict[str, str] = {}
        try:
            for line in self._resp.iter_lines():
                if line == "":
                    if frame:
                        with self._lock:
                            self._events.append(frame)
                            self._lock.notify_all()
                        frame = {}
                    continue
                if line.startswith(":"):
                    with self._lock:
                        self._comments.append(line)
                        self._lock.notify_all()
                    continue
                key, _, value = line.partition(":")
                frame[key] = value.lstrip(" ")
        except Exception as exc:  # noqa: BLE001 — the stream was closed under us
            self.error = exc
        finally:
            self._closed.set()

    def next_event(self, *, timeout: float) -> dict[str, str] | None:
        """The next unconsumed event, or None if none arrives in *timeout*."""
        deadline = time.monotonic() + timeout
        with self._lock:
            while not self._events:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._lock.wait(remaining)
            return self._events.pop(0)

    @property
    def comment_count(self) -> int:
        with self._lock:
            return len(self._comments)

    def close(self) -> None:
        self._resp.close()
        self._client.close()
        self._closed.wait(5.0)


@pytest.fixture()
def seeded(configured_db: str) -> str:
    with psycopg.connect(configured_db) as conn:
        _seed_entity(conn, entity_id="ent-live", trace_id=_TID, span_id="s-live")
        _seed_entity(conn, entity_id="ent-other", trace_id=_OTHER_TID, span_id="s-other")
        conn.commit()
    return configured_db


@pytest.fixture()
def listening_server(seeded: str, api_server: SpansApiServer) -> SpansApiServer:
    """The API server with its change hub's LISTEN session registered — so a
    write made right after the stream opens is seen by the wake, not only by
    the backstop (which the latency assertions below depend on)."""
    assert api_server.app is not None
    hub = api_server.app.state.change_hub
    assert hub.listens
    assert hub.connected.wait(5.0), "change hub never registered its LISTEN"
    return api_server


def _events_url(server: SpansApiServer, trace_id: str) -> str:
    return f"http://{server.host}:{server.port}/api/traces/{trace_id}/events"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_first_event_is_the_current_version(listening_server, seeded) -> None:
    reader = _SseReader(_events_url(listening_server, _TID))
    try:
        first = reader.next_event(timeout=5.0)
        assert first is not None, (reader.error, reader._closed.is_set())
        assert first["event"] == "change"
        assert first["id"] == first["data"] == retrieval.get_trace_version(_TID)
        assert first["retry"].isdigit()
    finally:
        reader.close()


def test_unknown_trace_still_streams_an_all_zero_token(listening_server) -> None:
    """A page opened before derivation starts has something to wait on."""
    reader = _SseReader(_events_url(listening_server, "never-seen"))
    try:
        first = reader.next_event(timeout=5.0)
        assert first is not None
        assert first["id"] == "legs=0;ix=0;es=0;lin=0;st=@0"
    finally:
        reader.close()


def test_write_to_this_trace_emits_a_change_via_listen(listening_server, seeded) -> None:
    reader = _SseReader(_events_url(listening_server, _TID))
    try:
        first = reader.next_event(timeout=5.0)
        assert first is not None
        with psycopg.connect(seeded) as conn:
            _add_interaction(conn, trace_id=_TID, entity_id="ent-live")
        started = time.monotonic()
        # Well inside the backstop: this arrival can only be the LISTEN wake
        # (plus the coalescing grace) — the proof that the wake path works.
        nxt = reader.next_event(timeout=BACKSTOP_SECONDS - 1.0)
        elapsed = time.monotonic() - started
        assert nxt is not None, "no change event inside the backstop window"
        assert nxt["event"] == "change"
        assert nxt["id"] != first["id"]
        assert nxt["id"] == retrieval.get_trace_version(_TID)
        assert elapsed < BACKSTOP_SECONDS - 1.0
        assert elapsed >= COALESCE_SECONDS * 0.5
    finally:
        reader.close()


def test_write_to_another_trace_emits_nothing(listening_server, seeded) -> None:
    reader = _SseReader(_events_url(listening_server, _TID))
    try:
        first = reader.next_event(timeout=5.0)
        assert first is not None
        with psycopg.connect(seeded) as conn:
            _add_interaction(conn, trace_id=_OTHER_TID, entity_id="ent-other")
        # The notification is global, so the hub DID wake this stream; the
        # trace-scoped token is what keeps the wake from becoming an event.
        assert reader.next_event(timeout=BACKSTOP_SECONDS + 1.0) is None
        # …and the stream is still live: a write to OUR trace gets through.
        with psycopg.connect(seeded) as conn:
            _add_interaction(conn, trace_id=_TID, entity_id="ent-live")
        nxt = reader.next_event(timeout=BACKSTOP_SECONDS)
        assert nxt is not None and nxt["id"] != first["id"]
    finally:
        reader.close()


def test_quiet_stream_sends_keepalive_comments(listening_server, seeded) -> None:
    reader = _SseReader(_events_url(listening_server, _TID))
    try:
        assert reader.next_event(timeout=5.0) is not None
        deadline = time.monotonic() + BACKSTOP_SECONDS * 2 + 1.0
        while reader.comment_count == 0 and time.monotonic() < deadline:
            time.sleep(0.1)
        assert reader.comment_count >= 1
    finally:
        reader.close()


def test_closing_the_stream_releases_the_waiter(listening_server, seeded) -> None:
    hub = listening_server.app.state.change_hub
    reader = _SseReader(_events_url(listening_server, _TID))
    assert reader.next_event(timeout=5.0) is not None
    deadline = time.monotonic() + 2.0
    while hub.waiter_count == 0 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert hub.waiter_count == 1
    reader.close()
    deadline = time.monotonic() + BACKSTOP_SECONDS + 2.0
    while hub.waiter_count != 0 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert hub.waiter_count == 0


@pytest.fixture()
def backstop_only_server(seeded: str) -> Iterator[SpansApiServer]:
    """A server whose hub has no LISTEN DSN: the streams poll on the backstop."""
    port = _free_port()
    server = SpansApiServer(host="127.0.0.1", port=port, listen_dsn=None)
    server.start()
    try:
        _wait_port("127.0.0.1", port)
        assert server.app is not None
        assert not server.app.state.change_hub.listens
        yield server
    finally:
        server.stop(grace=1.0)


def test_backstop_delivers_without_listen(backstop_only_server, seeded) -> None:
    reader = _SseReader(_events_url(backstop_only_server, _TID))
    try:
        first = reader.next_event(timeout=5.0)
        assert first is not None
        with psycopg.connect(seeded) as conn:
            _add_interaction(conn, trace_id=_TID, entity_id="ent-live")
        nxt = reader.next_event(timeout=BACKSTOP_SECONDS + 2.0)
        assert nxt is not None and nxt["id"] != first["id"]
    finally:
        reader.close()


def test_stream_survives_a_dropped_listen_session(listening_server, seeded) -> None:
    """Kill the hub's LISTEN backend: the hub re-opens it, and a write made in
    the gap is still delivered (by the backstop or the re-opened session)."""
    hub = listening_server.app.state.change_hub
    reader = _SseReader(_events_url(listening_server, _TID))
    try:
        first = reader.next_event(timeout=5.0)
        assert first is not None
        with psycopg.connect(seeded, autocommit=True) as conn:
            conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE pid <> pg_backend_pid() AND query ILIKE 'LISTEN %'"
            )
        with psycopg.connect(seeded) as conn:
            _add_interaction(conn, trace_id=_TID, entity_id="ent-live")
        nxt = reader.next_event(timeout=BACKSTOP_SECONDS + 3.0)
        assert nxt is not None and nxt["id"] != first["id"]
        assert hub.connected.wait(10.0), "hub did not re-open its LISTEN session"
    finally:
        reader.close()


def test_bad_port_sanity(listening_server) -> None:
    """Guard for the helpers above: the server really is reachable."""
    with socket.create_connection((listening_server.host, listening_server.port), timeout=2.0):
        pass
