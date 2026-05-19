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
