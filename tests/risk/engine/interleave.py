"""The one interleaving that defeats an unguarded read → compare → insert.

The risk engine's versioned writes (interaction decisions, interaction risk
records, trace risk records) each read the latest version, compare, and
insert the next. Two writers interleaved as below — the second READS while
the first has written but not yet committed, and INSERTS after the commit —
leave no error behind, only an identical duplicate version: the second saw
no row, and its in-statement ``MAX(version) + 1`` sees the committed one.

:func:`read_between_write_and_commit` forces exactly that schedule. Under the
engine's per-key advisory lock the second writer cannot read until the first
commits, so the hand-off below simply times out and the second writer no-ops.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

import psycopg

from data_governance import db

Write = Callable[[db.Transaction], None]

_SECOND = "interleave-second"


def read_between_write_and_commit(
    first: Write,
    second: Write,
    *,
    read_marker: str,
    monkeypatch,
    handoff_seconds: float = 1.0,
) -> list[BaseException]:
    """Run *first* and *second*, each in its own thread and transaction, so
    that *second*'s first statement containing *read_marker* executes after
    *first* has written but before it commits, and *second* resumes only
    once *first* has committed. Returns every exception either raised."""
    first_wrote = threading.Event()
    second_read = threading.Event()
    first_committed = threading.Event()
    real_execute = psycopg.Cursor.execute

    def _execute(self, sql, params=None, *args, **kwargs):
        result = real_execute(self, sql, params, *args, **kwargs)
        if (
            threading.current_thread().name == _SECOND
            and isinstance(sql, str)
            and read_marker in sql
            and not second_read.is_set()
        ):
            second_read.set()
            first_committed.wait(timeout=10)
        return result

    monkeypatch.setattr(psycopg.Cursor, "execute", _execute)
    errors: list[BaseException] = []

    def _first() -> None:
        try:
            with db.transaction() as tx:
                first(tx)
                first_wrote.set()
                # Serialized writers never get here: the second one is
                # blocked before its read, so this just times out.
                second_read.wait(timeout=handoff_seconds)
        except BaseException as exc:  # noqa: BLE001 - surfaced to the caller
            errors.append(exc)
        finally:
            first_wrote.set()
            first_committed.set()

    def _second() -> None:
        try:
            first_wrote.wait(timeout=10)
            with db.transaction() as tx:
                second(tx)
        except BaseException as exc:  # noqa: BLE001 - surfaced to the caller
            errors.append(exc)

    threads = [
        threading.Thread(target=_first),
        threading.Thread(target=_second, name=_SECOND),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not any(thread.is_alive() for thread in threads)
    return errors
