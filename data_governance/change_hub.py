"""The change hub: one ``LISTEN`` session per process, fanned out to every waiter.

Shared by the UI-facing API (the trace page's change stream, :mod:`data_governance.api.live`)
and the verdict service (:mod:`data_governance.risk.api.verdict_app`). It lives outside both so
that ``risk.api`` imports nothing from ``data_governance.api`` (CONTEXT.md, ADR-0005).
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Sequence

from data_governance import db

__all__ = ["BACKSTOP_SECONDS", "CHANNELS", "RECONNECT_SECONDS", "ChangeHub"]

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

#: How long the hub waits before re-opening a failed ``LISTEN`` session, and the
#: ``retry:`` hint the stream gives the browser's EventSource.
RECONNECT_SECONDS = 2.0


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
