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

- a **wake** from the one process-wide :class:`~data_governance.change_hub.ChangeHub`
  (:mod:`data_governance.change_hub`, shared with the verdict service), which holds a single
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
the hub is :mod:`data_governance.change_hub`'s; this module owns the per-stream
wake and backstop loop and the wire framing.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse

from data_governance import retrieval
from data_governance.change_hub import BACKSTOP_SECONDS, CHANNELS, RECONNECT_SECONDS, ChangeHub

__all__ = [
    "BACKSTOP_SECONDS",
    "CHANNELS",
    "COALESCE_SECONDS",
    "ChangeHub",
    "trace_events_handler",
]

#: Grace after a wake before the read, so a burst of per-span re-derivations
#: is read once rather than once per span.
COALESCE_SECONDS = 0.25

EVENT_NAME = "change"


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
