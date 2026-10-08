"""Tests for the Layer 1 db module (ADR-0005).

These tests exercise the public surface only — `db.transaction()` and the
methods on the yielded `tx` object. They use a real Postgres (no mocking,
per ADR-0005's reasoning that a mocked test of a Postgres wrapper only
asserts the mock).
"""

from __future__ import annotations

import threading

import psycopg
import pytest

from data_governance import db


@pytest.fixture(autouse=True)
def _reset_pool():
    """Each test gets a fresh process-singleton pool."""
    db.close_pool()
    yield
    db.close_pool()


def _create_kv_table(dsn: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("CREATE TABLE kv (k TEXT PRIMARY KEY, v INT NOT NULL)")


class TestTransactionExecute:
    def test_execute_and_fetch_one_round_trip(self, pg_dsn: str) -> None:
        _create_kv_table(pg_dsn)
        db.configure(pg_dsn)

        with db.transaction() as tx:
            tx.execute("INSERT INTO kv (k, v) VALUES (%s, %s)", ("a", 1))

        with db.transaction() as tx:
            row = tx.fetch_one("SELECT v FROM kv WHERE k = %s", ("a",))

        assert row == (1,)

    def test_fetch_all_returns_all_matching_rows(self, pg_dsn: str) -> None:
        _create_kv_table(pg_dsn)
        db.configure(pg_dsn)

        with db.transaction() as tx:
            tx.execute_many(
                "INSERT INTO kv (k, v) VALUES (%s, %s)",
                [("a", 1), ("b", 2), ("c", 3)],
            )

        with db.transaction() as tx:
            rows = tx.fetch_all("SELECT k, v FROM kv ORDER BY k")

        assert rows == [("a", 1), ("b", 2), ("c", 3)]

    def test_fetch_one_returns_none_when_no_match(self, pg_dsn: str) -> None:
        _create_kv_table(pg_dsn)
        db.configure(pg_dsn)

        with db.transaction() as tx:
            row = tx.fetch_one("SELECT v FROM kv WHERE k = %s", ("missing",))

        assert row is None


class TestTransactionLifecycle:
    def test_clean_exit_commits(self, pg_dsn: str) -> None:
        _create_kv_table(pg_dsn)
        db.configure(pg_dsn)

        with db.transaction() as tx:
            tx.execute("INSERT INTO kv (k, v) VALUES (%s, %s)", ("a", 1))

        # Read via a separate libpq connection — proves the row survived
        # outside the writing transaction.
        with psycopg.connect(pg_dsn) as conn:
            row = conn.execute("SELECT v FROM kv WHERE k = %s", ("a",)).fetchone()
        assert row == (1,)

    def test_exception_rolls_back(self, pg_dsn: str) -> None:
        _create_kv_table(pg_dsn)
        db.configure(pg_dsn)

        class Boom(Exception):
            pass

        with pytest.raises(Boom):
            with db.transaction() as tx:
                tx.execute("INSERT INTO kv (k, v) VALUES (%s, %s)", ("a", 1))
                raise Boom

        with psycopg.connect(pg_dsn) as conn:
            count = conn.execute("SELECT COUNT(*) FROM kv").fetchone()
        assert count == (0,)

    def test_connection_returns_to_pool_after_clean_exit(self, pg_dsn: str) -> None:
        _create_kv_table(pg_dsn)
        # Pool of size 1 — if the connection is leaked, the second transaction
        # will block forever (we cap the wait via a short pool timeout).
        db.configure(pg_dsn, min_size=1, max_size=1, timeout=5.0)

        for i in range(3):
            with db.transaction() as tx:
                tx.execute("INSERT INTO kv (k, v) VALUES (%s, %s)", (f"k{i}", i))

        with psycopg.connect(pg_dsn) as conn:
            count = conn.execute("SELECT COUNT(*) FROM kv").fetchone()
        assert count == (3,)

    def test_connection_returns_to_pool_after_exception(self, pg_dsn: str) -> None:
        _create_kv_table(pg_dsn)
        db.configure(pg_dsn, min_size=1, max_size=1, timeout=5.0)

        class Boom(Exception):
            pass

        for _ in range(3):
            with pytest.raises(Boom):
                with db.transaction() as tx:
                    tx.execute("INSERT INTO kv (k, v) VALUES (%s, %s)", ("a", 1))
                    raise Boom

        # All three borrows must have been returned — otherwise a fourth
        # transaction blocks until pool timeout fires.
        with db.transaction() as tx:
            count = tx.fetch_one("SELECT COUNT(*) FROM kv")
        assert count == (0,)


class TestParameterization:
    def test_string_with_quotes_is_safely_parameterized(self, pg_dsn: str) -> None:
        """A naive string-concat implementation would break on this input."""
        _create_kv_table(pg_dsn)
        db.configure(pg_dsn)

        nasty = "'); DROP TABLE kv; --"
        with db.transaction() as tx:
            tx.execute("INSERT INTO kv (k, v) VALUES (%s, %s)", (nasty, 42))

        with db.transaction() as tx:
            row = tx.fetch_one("SELECT v FROM kv WHERE k = %s", (nasty,))
        assert row == (42,)

        # Table still exists.
        with db.transaction() as tx:
            tx.fetch_one("SELECT COUNT(*) FROM kv")

    def test_execute_many_parameterizes_each_row(self, pg_dsn: str) -> None:
        _create_kv_table(pg_dsn)
        db.configure(pg_dsn)

        rows = [(f"k{i}", i) for i in range(50)]
        with db.transaction() as tx:
            tx.execute_many("INSERT INTO kv (k, v) VALUES (%s, %s)", rows)

        with db.transaction() as tx:
            count = tx.fetch_one("SELECT COUNT(*) FROM kv")
        assert count == (50,)


class TestProcessSingletonPool:
    def test_configure_creates_one_pool(self, pg_dsn: str) -> None:
        _create_kv_table(pg_dsn)
        db.configure(pg_dsn, min_size=1, max_size=1, timeout=5.0)

        # Two interleaved transactions on a pool of size 1: the second must
        # wait for the first, but both must succeed (proves the pool is
        # actually a pool — not a per-call connection).
        results = []
        barrier = threading.Barrier(2)

        def worker(key: str) -> None:
            barrier.wait()
            with db.transaction() as tx:
                tx.execute("INSERT INTO kv (k, v) VALUES (%s, %s)", (key, 1))
                results.append(key)

        threads = [threading.Thread(target=worker, args=(k,)) for k in ("a", "b")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert sorted(results) == ["a", "b"]

    def test_configure_replaces_pool(self, pg_dsn: str) -> None:
        """Calling configure() twice closes the old pool and uses the new one."""
        _create_kv_table(pg_dsn)
        db.configure(pg_dsn)

        with db.transaction() as tx:
            tx.execute("INSERT INTO kv (k, v) VALUES (%s, %s)", ("a", 1))

        # Reconfigure (e.g. test reset). The old pool should be closed; the
        # new pool talks to the same DB. Both transactions must succeed.
        db.configure(pg_dsn)
        with db.transaction() as tx:
            count = tx.fetch_one("SELECT COUNT(*) FROM kv")
        assert count == (1,)


class TestPoolAcquireTimeout:
    """Pool acquire-timeout must surface as a connection-class exception
    that ADR-0003's mapping can identify."""

    def test_acquire_timeout_raises_connection_class_exception(self, pg_dsn: str) -> None:
        db.configure(pg_dsn, min_size=1, max_size=1, timeout=0.5)

        # Hold the only connection — the second acquire must time out.
        with db.transaction() as _holding:
            with pytest.raises(db.ConnectionTimeout) as excinfo:
                with db.transaction():
                    pass

        # The exception must be classifiable as a connection-class error so
        # ADR-0003's SQLSTATE dispatcher can route it to the retryable
        # batch-failure path.
        assert db.is_connection_error(excinfo.value)
        # And conventionally a subclass of psycopg's OperationalError so
        # callers that already handle libpq connection failures see it
        # uniformly.
        assert isinstance(excinfo.value, psycopg.OperationalError)

    def test_libpq_connection_failure_classifies_as_connection_error(self) -> None:
        """ADR-0003 routes libpq connection errors (SQLSTATE class 08) to the
        same connection-class path. Surface a libpq error from a bad DSN and
        confirm the classifier agrees."""
        db.configure(
            "postgresql://nope:nope@127.0.0.1:1/nope",
            min_size=0,
            max_size=1,
            timeout=2.0,
        )
        with pytest.raises(Exception) as excinfo:  # noqa: BLE001 — accept any
            with db.transaction():
                pass
        assert db.is_connection_error(excinfo.value)


class TestListen:
    """`db.listen()` LISTEN/NOTIFY session connection (ADR-0015).

    `listen()` opens its own connection from the DSN and does not touch the
    pool, so these tests pass `pg_dsn` directly. They exercise the public
    contract: wait() returns True on a notification, False on timeout, and the
    channel is validated as a bare identifier.
    """

    def test_wait_returns_true_when_notified(self, pg_dsn: str) -> None:
        with db.listen("dg_test_chan", pg_dsn) as lst:
            # NOTIFY from a separate connection; the listener must wake.
            with psycopg.connect(pg_dsn, autocommit=True) as notifier:
                notifier.execute("NOTIFY dg_test_chan")
            assert lst.wait(timeout=5.0) is True

    def test_wait_returns_false_on_timeout(self, pg_dsn: str) -> None:
        with db.listen("dg_test_chan", pg_dsn) as lst:
            # Nobody notifies — wait must time out and report False.
            assert lst.wait(timeout=0.3) is False

    def test_payload_is_irrelevant_count_coalesces(self, pg_dsn: str) -> None:
        """A burst of payload-less NOTIFYs still satisfies a single wait() —
        the consumer only needs to learn 'something happened' (issue #71)."""
        with db.listen("dg_test_chan", pg_dsn) as lst:
            with psycopg.connect(pg_dsn, autocommit=True) as notifier:
                for _ in range(5):
                    notifier.execute("NOTIFY dg_test_chan, ''")
            assert lst.wait(timeout=5.0) is True

    def test_rejects_non_identifier_channel(self, pg_dsn: str) -> None:
        """LISTEN can't bind the channel as a parameter, so a non-identifier
        (the injection vector) is refused before any SQL runs."""
        with pytest.raises(ValueError):
            with db.listen("bad; DROP TABLE spans", pg_dsn):
                pass


class TestSessionLock:
    """`db.session_lock()`: one holder at a time, on its own connection.

    Like `listen()` it opens its own connection from the DSN, so these tests
    pass `pg_dsn` directly."""

    def test_a_second_holder_waits_for_the_first(self, pg_dsn: str) -> None:
        events: list[str] = []
        first_holds = threading.Event()
        release_first = threading.Event()

        def first() -> None:
            with db.session_lock("dg-test-lock", pg_dsn):
                events.append("first acquired")
                first_holds.set()
                release_first.wait(timeout=10)
                events.append("first releasing")

        def second() -> None:
            with db.session_lock("dg-test-lock", pg_dsn):
                events.append("second acquired")

        holder = threading.Thread(target=first)
        holder.start()
        assert first_holds.wait(timeout=10)
        waiter = threading.Thread(target=second)
        waiter.start()
        waiter.join(timeout=0.5)
        assert waiter.is_alive(), "the second holder must block while the first holds"

        release_first.set()
        holder.join(timeout=10)
        waiter.join(timeout=10)
        assert events == ["first acquired", "first releasing", "second acquired"]

    def test_a_lost_holder_connection_frees_the_lock(self, pg_dsn: str) -> None:
        """A holder that dies loses its connection, and with it the lock —
        simulated by terminating the holding session_lock's backend while a
        second session_lock waits for it."""
        acquired = threading.Event()

        def take() -> None:
            with db.session_lock("dg-test-lock", pg_dsn, poll_seconds=0.1):
                acquired.set()

        with db.session_lock("dg-test-lock", pg_dsn) as holder:
            taker = threading.Thread(target=take)
            taker.start()
            assert not acquired.wait(timeout=1), "the lock is held"
            with psycopg.connect(pg_dsn, autocommit=True) as admin:
                admin.execute("SELECT pg_terminate_backend(%s)", (holder._conn.info.backend_pid,))
            assert acquired.wait(timeout=10)
        taker.join(timeout=10)

    def test_the_lock_session_sets_keepalives(self, pg_dsn: str) -> None:
        """A holder whose node vanishes without closing its socket must free
        the lock in seconds, not after the kernel's two-hour default."""
        with db.session_lock("dg-test-lock", pg_dsn) as lock:
            (idle,) = lock._conn.execute("SHOW tcp_keepalives_idle").fetchone()
            (timeout,) = lock._conn.execute("SHOW tcp_user_timeout").fetchone()
        assert (idle, timeout) == ("15", "30000")

    def test_check_raises_once_the_lock_session_is_gone(self, pg_dsn: str) -> None:
        with db.session_lock("dg-test-lock", pg_dsn) as lock:
            lock.check()  # alive: no error
            with psycopg.connect(pg_dsn, autocommit=True) as admin:
                admin.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_locks "
                    "WHERE locktype = 'advisory' AND granted AND database = "
                    "(SELECT oid FROM pg_database WHERE datname = current_database())"
                )
            with pytest.raises(db.LockLost):
                lock.check()

    def test_a_stop_while_waiting_raises_promptly(self, pg_dsn: str) -> None:
        stop = threading.Event()
        outcome: list[BaseException] = []

        with db.session_lock("dg-test-lock", pg_dsn):

            def waiter() -> None:
                try:
                    with db.session_lock("dg-test-lock", pg_dsn, stop=stop, poll_seconds=0.1):
                        pass
                except BaseException as exc:  # noqa: BLE001 - asserted below
                    outcome.append(exc)

            thread = threading.Thread(target=waiter)
            thread.start()
            thread.join(timeout=0.5)
            assert thread.is_alive(), "still waiting while the lock is held"
            stop.set()
            thread.join(timeout=2)
            assert not thread.is_alive()
        assert len(outcome) == 1 and isinstance(outcome[0], db.LockWaitStopped)

    def test_different_names_do_not_contend(self, pg_dsn: str) -> None:
        with db.session_lock("dg-test-lock-a", pg_dsn):
            done = threading.Event()

            def other() -> None:
                with db.session_lock("dg-test-lock-b", pg_dsn):
                    done.set()

            thread = threading.Thread(target=other)
            thread.start()
            assert done.wait(timeout=5)
            thread.join(timeout=5)
