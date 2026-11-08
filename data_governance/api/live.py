"""Live trace changes — the change stream behind the trace page (ADR-0034).

``GET /api/traces/{tid}/events`` is a Server-Sent Events stream that tells an
open trace page *that* the trace's derived picture changed, never *what*
changed. Every derived read the page makes is a whole-trace read, so "changed"
is already the complete instruction: read again. One event type, ``change``,
whose ``id`` and ``data`` are both the **Trace version** token
(:func:`retrieval.get_trace_version`). The first event on every connection is
the current token; the client keeps the last token it saw and acts only when a
later one differs, which also makes the browser's automatic reconnect correct
for free — the server is stateless per stream and never reads
``Last-Event-ID``.

HOW A STREAM LEARNS OF A CHANGE. The same two signals the processors use
(ADR-0015, ADR-0027), in the same roles:

- a **wake** from the one process-wide :class:`ChangeHub`, which holds a single
  ``LISTEN`` session on the derivation's and the risk processors' notify
  channels and fans each notification out to every open stream. Latency only:
  the notifications carry no payload, are global (any trace), and are missed
  if the session is down;
- the **backstop**: every stream re-reads its token at least every
  :data:`BACKSTOP_SECONDS` regardless. This is the correctness guarantee — a
  write the hub never heard of (the lineage processor and the alert generator
  fire no notify; the ``LISTEN`` session may be reconnecting) is seen within
  one backstop.

On either signal the stream re-reads the token and emits a ``change`` only if
it differs. A wake is first allowed :data:`COALESCE_SECONDS` to let the burst
that caused it land, so a trace re-derived span by span costs a handful of
reads per second rather than one per span. A timed-out backstop that found no
change emits an SSE comment instead, which keeps the connection alive through
proxies without the client seeing an event.

The token read goes through the retrieval library like every other read here;
this module owns only the wake plumbing and the wire framing.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import AsyncIterator, Sequence

from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse

from data_governance import db, retrieval

__all__ = [
    "BACKSTOP_SECONDS",
    "CHANNELS",
    "COALESCE_SECONDS",
    "ChangeHub",
    "trace_events_handler",
]

log = logging.getLogger(__name__)

#: The derivation's own notify channels (migrations 0010 and 0012). A leg
#: insert is where every interaction write lands; an entity insert is the one
#: derived write that can precede any leg. Then the risk processors' (0016 and
#: 0023): a risk record lands after derivation has gone quiet, so without
#: these a trace's decision would wait for the backstop. An alert fires no
#: notify of its own; it follows a trace risk record, whose wake usually
#: covers it, and the backstop covers the rest.
CHANNELS: tuple[str, ...] = (
    "dg_legs_inserted",
    "dg_entity_ready",
    "dg_interaction_risk_written",
    "dg_trace_risk_written",
)

#: Longest a stream waits before re-reading its token with no wake — the
#: correctness backstop, and the keepalive cadence.
BACKSTOP_SECONDS = 3.0

#: Grace after a wake before the read, so a burst of per-span re-derivations
#: is read once rather than once per span.
COALESCE_SECONDS = 0.25

#: How long the hub waits before re-opening a failed ``LISTEN`` session, and the
#: ``retry:`` hint the stream gives the browser's EventSource.
RECONNECT_SECONDS = 2.0

EVENT_NAME = "change"


class ChangeHub:
    """One ``LISTEN`` session per process, fanned out to every open stream.

    Lives on ``app.state``. :meth:`start` is called from the app's lifespan with
    the running event loop; the hub thread then holds the session for the life
    of the process and re-opens it after any failure (ADR-0015: a lost session
    costs latency, never correctness — the streams' backstop covers the gap).
    Built with ``dsn=None`` it never listens and the streams run on the backstop
    alone, which is the shape a deployment without a reachable ``LISTEN`` DSN
    degrades to.

    Waiters are plain :class:`asyncio.Event` objects, touched only on the loop
    thread: the hub thread hands each notification to the loop with
    ``call_soon_threadsafe``, so no lock guards the set.
    """

    def __init__(self, dsn: str | None, channels: Sequence[str] = CHANNELS) -> None:
        self._dsn = dsn
        self._channels = tuple(channels)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._waiters: set[asyncio.Event] = set()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        #: Set while the ``LISTEN`` session is registered; cleared on any
        #: failure. Diagnostic (and what a test waits on before it writes).
        self.connected = threading.Event()

    @property
    def listens(self) -> bool:
        """Whether this hub was given a DSN to listen on at all."""
        return self._dsn is not None

    @property
    def waiter_count(self) -> int:
        return len(self._waiters)

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        if self._dsn is None or self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._listen_forever, name="dg-change-hub", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=BACKSTOP_SECONDS + 1.0)
            self._thread = None

    # -- loop side ---------------------------------------------------------

    def subscribe(self) -> asyncio.Event:
        """Register a waiter. Called on the loop thread by a stream."""
        event = asyncio.Event()
        self._waiters.add(event)
        return event

    def unsubscribe(self, event: asyncio.Event) -> None:
        self._waiters.discard(event)

    def _wake_all(self) -> None:
        for event in self._waiters:
            event.set()

    # -- hub thread ---------------------------------------------------------

    def _listen_forever(self) -> None:
        assert self._dsn is not None
        while not self._stop.is_set():
            try:
                with db.listen(self._channels, self._dsn) as listener:
                    self.connected.set()
                    log.info("change hub listening on %s", ", ".join(self._channels))
                    while not self._stop.is_set():
                        if listener.wait(BACKSTOP_SECONDS) and self._loop is not None:
                            self._loop.call_soon_threadsafe(self._wake_all)
            except Exception as exc:  # noqa: BLE001 — any session fault → re-open
                self.connected.clear()
                if self._stop.is_set():
                    break
                log.warning(
                    "change hub LISTEN session failed (%s); re-opening in %.0fs — "
                    "streams run on the %.0fs backstop meanwhile",
                    exc,
                    RECONNECT_SECONDS,
                    BACKSTOP_SECONDS,
                )
                self._stop.wait(RECONNECT_SECONDS)
        self.connected.clear()


def _sse_event(token: str) -> str:
    # ``id`` and ``data`` carry the same token: ``id`` is what EventSource
    # exposes as ``lastEventId`` and replays on reconnect; ``data`` keeps the
    # event self-describing for any non-browser consumer.
    return (
        f"id: {token}\n"
        f"event: {EVENT_NAME}\n"
        f"data: {token}\n"
        f"retry: {int(RECONNECT_SECONDS * 1000)}\n\n"
    )


_KEEPALIVE = ": keepalive\n\n"


async def _stream(hub: ChangeHub, trace_id: str) -> AsyncIterator[str]:
    event = hub.subscribe()
    try:
        token = await asyncio.to_thread(retrieval.get_trace_version, trace_id)
        yield _sse_event(token)
        while True:
            try:
                await asyncio.wait_for(event.wait(), timeout=BACKSTOP_SECONDS)
                woke = True
            except asyncio.TimeoutError:
                woke = False
            if woke:
                await asyncio.sleep(COALESCE_SECONDS)
            # Clear BEFORE the read, never after: a notification that lands
            # once the read has started may or may not be in the read's
            # snapshot, and leaving the event set makes the next pass re-read.
            event.clear()
            current = await asyncio.to_thread(retrieval.get_trace_version, trace_id)
            if current != token:
                token = current
                yield _sse_event(token)
            elif not woke:
                yield _KEEPALIVE
    finally:
        hub.unsubscribe(event)


async def trace_events_handler(request: Request) -> Response:
    """``GET /api/traces/{tid}/events`` — the trace's change stream (SSE)."""
    trace_id = request.path_params.get("tid")
    if not trace_id:
        return JSONResponse({"error": "trace_id required"}, status_code=400)
    hub: ChangeHub = request.app.state.change_hub
    return StreamingResponse(
        _stream(hub, trace_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Tell a buffering reverse proxy to pass events through as written.
            "X-Accel-Buffering": "no",
        },
    )
