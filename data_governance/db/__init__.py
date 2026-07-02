"""Layer 1 db module: a thin generic Postgres wrapper.

Per ADR-0005, this module owns the connection pool, transaction boundaries,
and parameterized SQL execution — and nothing else. It is deliberately
ignorant of spans, the schema, OTLP, listing roots, or any v1 domain concept.

Public surface:

    db.configure(dsn, *, min_size=..., max_size=..., timeout=...)
    db.close_pool()

    with db.transaction() as tx:
        tx.execute(sql, params)
        tx.execute_many(sql, params_seq)
        row = tx.fetch_one(sql, params)
        rows = tx.fetch_all(sql, params)

    with db.listen(channel, dsn) as lst:   # LISTEN/NOTIFY wake (ADR-0015)
        woke = lst.wait(timeout)           # True on notify, False on timeout

    db.ConnectionTimeout      # raised when the pool can't hand out a conn
    db.is_connection_error(e) # ADR-0003 connection-class classifier

Clean exit commits and returns the connection to the pool. Any exception
rolls back and still returns the connection. The pool is a process-singleton
— `configure()` may be called multiple times (e.g. by tests) but only one
pool is live at a time.

`listen()` is the one Layer-1 capability outside the pooled-transaction model
(ADR-0015): a dedicated, autocommit, session-scoped connection for Postgres
`LISTEN`, which the pool's short-lived autocommit-off connections cannot
provide. It stays generic — it knows nothing about the channel's meaning.
"""

from __future__ import annotations

import os
import re
import threading
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from typing import Any

import psycopg
import psycopg_pool

__all__ = [
    "ConnectionTimeout",
    "Listener",
    "Transaction",
    "close_pool",
    "configure",
    "is_connection_error",
    "listen",
    "transaction",
]


class ConnectionTimeout(psycopg.OperationalError):
    """Raised when the pool cannot hand out a connection within the configured timeout.

    Subclasses :class:`psycopg.OperationalError` so callers that already catch
    libpq connection failures see pool exhaustion the same way. ADR-0003 maps
    this to ``kind=connection`` (retryable batch failure).
    """


# --- module-singleton pool ---------------------------------------------------

_pool: psycopg_pool.ConnectionPool | None = None
_pool_lock = threading.Lock()


def configure(
    dsn: str,
    *,
    min_size: int | None = None,
    max_size: int | None = None,
    timeout: float | None = None,
) -> None:
    """(Re)create the process-singleton connection pool.

    Defaults are taken from environment variables when arguments are not
    given:

    - ``DB_POOL_MIN_SIZE`` (default 1)
    - ``DB_POOL_MAX_SIZE`` (default 10)
    - ``DB_POOL_TIMEOUT`` seconds (default 30)

    Calling ``configure()`` a second time closes the previous pool. This is
    intended for test isolation; production code calls it once at startup.
    """
    global _pool

    if min_size is None:
        min_size = int(os.environ.get("DB_POOL_MIN_SIZE", "1"))
    if max_size is None:
        max_size = int(os.environ.get("DB_POOL_MAX_SIZE", "10"))
    if timeout is None:
        timeout = float(os.environ.get("DB_POOL_TIMEOUT", "30"))

    with _pool_lock:
        if _pool is not None:
            _pool.close()
            _pool = None
        _pool = psycopg_pool.ConnectionPool(
            conninfo=dsn,
            min_size=min_size,
            max_size=max_size,
            timeout=timeout,
            # Don't block process startup waiting for the DB to come up; the
            # first transaction() will surface the connection failure with the
            # same classification as a runtime drop.
            open=True,
        )


def close_pool() -> None:
    """Close the singleton pool, if any. Safe to call when no pool exists."""
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.close()
            _pool = None


def _require_pool() -> psycopg_pool.ConnectionPool:
    if _pool is None:
        raise RuntimeError(
            "data_governance.db is not configured; call db.configure(dsn) first."
        )
    return _pool


# --- Transaction handle ------------------------------------------------------


class Transaction:
    """Handle yielded by ``db.transaction()``.

    Wraps a checked-out psycopg connection. The connection has autocommit
    disabled so the wrapping context manager can commit or rollback cleanly.
    Methods are intentionally a small, psycopg-shaped surface:

    - :meth:`execute` — run a parameterized statement, no result rows expected
    - :meth:`execute_many` — run the same statement against many parameter rows
    - :meth:`fetch_one` — run a query and return the first row (or ``None``)
    - :meth:`fetch_all` — run a query and return all rows
    """

    __slots__ = ("_conn",)

    def __init__(self, conn: psycopg.Connection[Any]) -> None:
        self._conn = conn

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> None:
        """Run a parameterized statement; ignore any result set.

        ``params`` must be a sequence of values, one per ``%s`` placeholder.
        Concatenating values into ``sql`` is forbidden — pass them through
        ``params`` so libpq does the binding.
        """
        with self._conn.cursor() as cur:
            cur.execute(sql, params)

    def execute_many(self, sql: str, params_seq: Iterable[Sequence[Any]]) -> None:
        """Run the same statement against many parameter rows in one round trip."""
        with self._conn.cursor() as cur:
            cur.executemany(sql, list(params_seq))

    def fetch_one(self, sql: str, params: Sequence[Any] | None = None) -> tuple[Any, ...] | None:
        """Run a query and return the first row, or ``None`` if no rows match."""
        with self._conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()

    def fetch_all(self, sql: str, params: Sequence[Any] | None = None) -> list[tuple[Any, ...]]:
        """Run a query and return all rows."""
        with self._conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()


# --- transaction context manager --------------------------------------------


@contextmanager
def transaction() -> Iterator[Transaction]:
    """Acquire a connection, begin a transaction, yield a :class:`Transaction`.

    On clean exit the transaction is committed. On any exception the
    transaction is rolled back and the exception re-raised. In both cases the
    connection is returned to the pool.

    Raises :class:`ConnectionTimeout` if no connection is available within
    the pool's configured timeout. This and any libpq connection error are
    classifiable via :func:`is_connection_error` per ADR-0003.
    """
    pool = _require_pool()
    try:
        cm = pool.connection()
        conn = cm.__enter__()
    except psycopg_pool.PoolTimeout as exc:
        # Re-raise as the connection-class exception ADR-0003's mapping
        # expects. Preserve the cause for debugging.
        raise ConnectionTimeout(str(exc)) from exc

    try:
        # psycopg defaults to autocommit=False which already opens an implicit
        # transaction on first execute; we just need to commit/rollback.
        try:
            yield Transaction(conn)
        except BaseException:
            conn.rollback()
            raise
        else:
            conn.commit()
    finally:
        cm.__exit__(None, None, None)


# --- error classification (ADR-0003) ----------------------------------------


def is_connection_error(exc: BaseException) -> bool:
    """Return True iff *exc* should map to ``kind=connection`` per ADR-0003.

    The receiver's OTLP error policy (ADR-0003) routes connection-class errors
    to the retryable batch-failure path. Anything else (integrity, malformed,
    other) is the caller's problem to classify.

    Recognises:

    - :class:`ConnectionTimeout` (pool exhaustion)
    - libpq connection errors with SQLSTATE class ``08`` (connection_exception)
    - any :class:`psycopg.OperationalError` with no SQLSTATE — typically a
      libpq-level "could not connect" before a session is established
    """
    if isinstance(exc, ConnectionTimeout):
        return True
    if isinstance(exc, psycopg.Error):
        sqlstate = getattr(exc, "sqlstate", None)
        if sqlstate is not None:
            return sqlstate.startswith("08")
        # No SQLSTATE: pre-session libpq failure (DNS, TCP refused, auth-not-
        # yet-negotiated). Treat as connection-class.
        return isinstance(exc, psycopg.OperationalError)
    return False


# --- LISTEN/NOTIFY session connection (ADR-0015) -----------------------------
#
# This is the one Layer-1 capability that does NOT go through the pool. LISTEN
# registration is session-scoped and the consumer must hold an autocommit
# connection to receive notifications as inserts commit — the opposite of the
# pool's short-lived autocommit-off connections. So `listen()` opens its own
# dedicated connection, held for the caller's `with` block. It stays generic:
# the channel name and its meaning belong to the caller, not this module.

# LISTEN cannot parameterize the channel identifier, so the value is
# interpolated into the SQL. Restrict it to a bare SQL identifier so that
# interpolation cannot inject — anything else is a programming error.
_CHANNEL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class Listener:
    """Handle yielded by :func:`listen`. Wraps a dedicated autocommit connection
    already registered (``LISTEN``) on a channel.

    The single method :meth:`wait` blocks for the next notification or the
    timeout. Notification *count* and *payload* are deliberately not surfaced:
    the consumer's reaction to any wake is "drain from the durable cursor,"
    which coalesces a burst, so only "did something arrive" matters.
    """

    __slots__ = ("_conn",)

    def __init__(self, conn: psycopg.Connection[Any]) -> None:
        self._conn = conn

    def wait(self, timeout: float) -> bool:
        """Block up to *timeout* seconds for a notification.

        Returns ``True`` if at least one notification arrived, ``False`` on
        timeout. Lets connection-class errors propagate so the caller can fall
        back to its poll backstop (see the processor driver, issue #71).
        """
        # psycopg 3.2+ `notifies()` blocks up to `timeout` and yields each
        # pending notification; `stop_after=1` returns as soon as one arrives.
        # An empty generator means the timeout elapsed with nothing pending.
        for _ in self._conn.notifies(timeout=timeout, stop_after=1):
            return True
        return False


@contextmanager
def listen(channel: str, dsn: str) -> Iterator[Listener]:
    """Open a dedicated autocommit connection, ``LISTEN`` on *channel*, yield a
    :class:`Listener`.

    The connection is held for the duration of the ``with`` block (typically a
    long-running consumer's lifetime) and closed on exit. It is NOT drawn from
    the pool — a session-scoped ``LISTEN`` on an autocommit connection is a
    different connection shape than the pool provides (ADR-0015).

    *channel* must be a bare SQL identifier (``LISTEN`` cannot bind it as a
    parameter); a non-identifier raises :class:`ValueError`.
    """
    if not _CHANNEL_RE.match(channel):
        raise ValueError(
            f"LISTEN channel must be a bare SQL identifier, got {channel!r}"
        )
    conn = psycopg.connect(dsn, autocommit=True)
    try:
        conn.execute(f"LISTEN {channel}")
        yield Listener(conn)
    finally:
        conn.close()
