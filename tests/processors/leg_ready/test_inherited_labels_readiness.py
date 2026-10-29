"""Leg-ready with ``RISK_INHERITED_LABELS=on``: readiness waits for label lineage's
row at the leg's current payload, and interactions queued in
``risk_recompute_requests`` are re-delivered after the cursor arm — never moving
the cursor, each request deleted with its write. With the flag off neither
happens."""

from __future__ import annotations

import psycopg
import pytest

from data_governance.processors.leg_ready import driver
from data_governance.risk import config
from tests.processors.leg_ready.test_driver import _classify, _cursor, _insert_leg


@pytest.fixture()
def labels_on(monkeypatch) -> None:
    monkeypatch.setattr(config, "inherited_labels_mode", lambda: "on")
    monkeypatch.setattr(driver, "_label_hold", [None, 0.0])  # no hold clock carried between tests


def _label_row(dsn: str, interaction_id: str, leg_type: str, payload_hash: str) -> None:
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "INSERT INTO leg_inherited_labels (interaction_id, leg_type, trace_id, payload_hash, seq, matcher, "
            "classified) VALUES (%s, %s, 't', %s, 0, 'exact', true) ON CONFLICT (interaction_id, leg_type) "
            "DO UPDATE SET payload_hash = EXCLUDED.payload_hash",
            (interaction_id, leg_type, payload_hash),
        )
        conn.commit()


def _request(dsn: str, interaction_id: str) -> None:
    with psycopg.connect(dsn) as conn:
        conn.execute("INSERT INTO risk_recompute_requests (interaction_id, reason) VALUES (%s, 'test') "
                     "ON CONFLICT (interaction_id) DO UPDATE SET requested_at = clock_timestamp()",
                     (interaction_id,))
        conn.commit()


def _requests(dsn: str) -> set[str]:
    with psycopg.connect(dsn) as conn:
        return {r[0] for r in conn.execute("SELECT interaction_id FROM risk_recompute_requests").fetchall()}


class _Observer:
    def __init__(self, dsn: str | None = None, defer: bool = False, requeue: bool = False) -> None:
        self.seen: list[str] = []
        self.dsn, self.defer, self.requeue = dsn, defer, requeue

    def __call__(self, leg: driver.Leg):
        self.seen.append(leg.interaction_id)
        if self.defer:
            raise driver.LegDeferred("opa down")
        if self.requeue:
            _request(self.dsn, leg.interaction_id)  # label lineage changed it again meanwhile
        return lambda tx: tx.execute("INSERT INTO processor_state (processor_name, last_processed_seq) "
                                     "VALUES (%s, 1) ON CONFLICT DO NOTHING", (f"wrote:{leg.interaction_id}",))


def test_on_a_classified_leg_waits_for_its_label_row(configured_db: str, labels_on) -> None:
    seq = _insert_leg(configured_db, interaction_id="ix", leg_type="request", payload_hash="h1")
    _classify(configured_db, "h1")
    assert driver.drain(0) == 0
    _label_row(configured_db, "ix", "request", "h1")
    assert driver.drain(0) == seq


def test_on_a_hold_on_label_lineage_is_named_in_the_log_once_it_lasts(configured_db: str, labels_on, caplog,
                                                                      monkeypatch) -> None:
    """A wake that races label lineage's derivation is not a hold; the same head
    leg waiting past LABEL_HOLD_WARN_SECONDS is, and is named."""
    _insert_leg(configured_db, interaction_id="ix", leg_type="request", payload_hash="h1")
    _classify(configured_db, "h1")
    with caplog.at_level("WARNING", logger="data_governance.processors.leg_ready.driver"):
        assert driver.drain(0) == 0
        assert not [r for r in caplog.records if "waits for label lineage" in r.getMessage()]
        monkeypatch.setattr(driver, "LABEL_HOLD_WARN_SECONDS", 0.0)
        assert driver.drain(0) == 0
    assert any("waits for label lineage" in r.getMessage() for r in caplog.records)


def test_on_a_label_row_derived_from_another_payload_does_not_count(configured_db: str, labels_on) -> None:
    _insert_leg(configured_db, interaction_id="ix", leg_type="request", payload_hash="h-new")
    _classify(configured_db, "h-new")
    _label_row(configured_db, "ix", "request", "h-old")
    assert driver.drain(0) == 0


def test_on_a_leg_without_a_payload_needs_no_label_row(configured_db: str, labels_on) -> None:
    seq = _insert_leg(configured_db, interaction_id="ix", leg_type="request", payload_hash=None)
    assert driver.drain(0) == seq


def test_off_label_rows_are_not_consulted(configured_db: str) -> None:
    assert config.inherited_labels_mode() == "off"
    seq = _insert_leg(configured_db, interaction_id="ix", leg_type="request", payload_hash="h1")
    _classify(configured_db, "h1")
    assert driver.drain(0) == seq


def test_on_a_request_behind_the_cursor_is_recomputed_once_and_deleted(configured_db: str, labels_on) -> None:
    seq = _insert_leg(configured_db, interaction_id="ix", leg_type="request", payload_hash=None)
    observer = _Observer()
    assert driver.drain(0, observer) == seq and observer.seen == ["ix"]
    _request(configured_db, "ix")
    observer.seen.clear()
    assert driver.drain(seq, observer) == seq and _cursor(configured_db) == seq
    assert observer.seen == ["ix"] and _requests(configured_db) == set()
    with psycopg.connect(configured_db) as conn:
        assert conn.execute("SELECT 1 FROM processor_state WHERE processor_name = 'wrote:ix'").fetchone()
    driver.drain(seq, observer)
    assert observer.seen == ["ix"]  # nothing left to recompute


def test_on_a_request_ahead_of_the_cursor_is_left_to_the_cursor_arm(configured_db: str, labels_on) -> None:
    _insert_leg(configured_db, interaction_id="ix", leg_type="request", payload_hash="unclassified")
    _request(configured_db, "ix")
    observer = _Observer()
    assert driver.drain(0, observer) == 0
    assert observer.seen == [] and _requests(configured_db) == {"ix"}


def test_on_a_deferred_recompute_keeps_its_request(configured_db: str, labels_on) -> None:
    seq = _insert_leg(configured_db, interaction_id="ix", leg_type="request", payload_hash=None)
    driver.drain(0, _Observer())
    _request(configured_db, "ix")
    assert driver.drain(seq, _Observer(defer=True)) == seq
    assert _requests(configured_db) == {"ix"}


def test_on_a_request_queued_again_during_the_recompute_survives(configured_db: str, labels_on) -> None:
    seq = _insert_leg(configured_db, interaction_id="ix", leg_type="request", payload_hash=None)
    driver.drain(0, _Observer())
    _request(configured_db, "ix")
    driver.drain(seq, _Observer(configured_db, requeue=True))
    assert _requests(configured_db) == {"ix"}


def test_on_a_request_waits_until_every_leg_of_its_interaction_is_ready(configured_db: str, labels_on) -> None:
    """A leg rewritten in place to a payload not yet classified (or not yet
    derived by label lineage) holds its interaction's request: a recompute on a
    pending verdict would stand, since nothing queues another once it lands."""
    seq = _insert_leg(configured_db, interaction_id="ix", leg_type="request", payload_hash="h1")
    _classify(configured_db, "h1")
    _label_row(configured_db, "ix", "request", "h1")
    assert driver.drain(0, _Observer()) == seq
    with psycopg.connect(configured_db) as conn:
        conn.execute("UPDATE interaction_legs SET payload_hash = 'h2' WHERE interaction_id = 'ix'")
        conn.commit()
    _request(configured_db, "ix")
    observer = _Observer()
    driver.drain(seq, observer)
    assert observer.seen == [] and _requests(configured_db) == {"ix"}  # h2 not classified
    _classify(configured_db, "h2")
    driver.drain(seq, observer)
    assert observer.seen == [] and _requests(configured_db) == {"ix"}  # h2 not derived by label lineage
    _label_row(configured_db, "ix", "request", "h2")
    driver.drain(seq, observer)
    assert observer.seen == ["ix"] and _requests(configured_db) == set()


def test_on_a_request_whose_interaction_lost_its_legs_is_dropped(configured_db: str, labels_on) -> None:
    seq = _insert_leg(configured_db, interaction_id="ix", leg_type="request", payload_hash=None)
    _request(configured_db, "gone")
    observer = _Observer()
    driver.drain(0, observer)
    assert driver.drain(seq, observer) == seq
    assert "gone" not in _requests(configured_db) and "gone" not in observer.seen


def test_on_a_request_another_consumer_took_meanwhile_writes_nothing(configured_db: str, labels_on) -> None:
    """The stale-writer fence: a recompute whose request was claimed by another
    consumer while this one computed lands nothing."""
    seq = _insert_leg(configured_db, interaction_id="ix", leg_type="request", payload_hash=None)
    driver.drain(0, _Observer())
    _request(configured_db, "ix")
    with psycopg.connect(configured_db) as conn:  # forget the cursor arm's own write
        conn.execute("DELETE FROM processor_state WHERE processor_name = 'wrote:ix'")
        conn.commit()

    class _Racing(_Observer):
        def __call__(self, leg):
            with psycopg.connect(configured_db) as conn:  # the replacement drains it first
                conn.execute("DELETE FROM risk_recompute_requests WHERE interaction_id = %s", (leg.interaction_id,))
                conn.commit()
            return super().__call__(leg)

    driver.drain(seq, _Racing())
    with psycopg.connect(configured_db) as conn:
        assert conn.execute("SELECT 1 FROM processor_state WHERE processor_name = 'wrote:ix'").fetchone() is None


def test_on_a_recompute_after_the_cursor_moved_stops_with_lock_lost(configured_db: str, labels_on) -> None:
    from data_governance import db as dgdb
    seq = _insert_leg(configured_db, interaction_id="ix", leg_type="request", payload_hash=None)
    driver.drain(0, _Observer())
    _request(configured_db, "ix")

    class _Overtaken(_Observer):
        def __call__(self, leg):
            with psycopg.connect(configured_db) as conn:  # a replacement advanced past this drain
                conn.execute("UPDATE processor_state SET last_processed_seq = last_processed_seq + 1 "
                             "WHERE processor_name = %s", (driver.PROCESSOR_NAME,))
                conn.commit()
            return super().__call__(leg)

    with pytest.raises(dgdb.LockLost):
        driver.drain(seq, _Overtaken())
    assert _requests(configured_db) == {"ix"}


def test_on_classified_is_part_of_readiness_not_only_the_label_row(configured_db: str, labels_on) -> None:
    """Whole-trace derivation writes rows for legs ahead of label lineage's cursor,
    before they are classified: a label row alone does not make a leg ready."""
    _insert_leg(configured_db, interaction_id="ix", leg_type="request", payload_hash="h1")
    _label_row(configured_db, "ix", "request", "h1")
    assert driver.drain(0) == 0


def test_on_a_request_waits_for_the_interactions_last_leg_to_pass_the_cursor(configured_db: str, labels_on) -> None:
    req = _insert_leg(configured_db, interaction_id="ix", leg_type="request", payload_hash=None)
    _insert_leg(configured_db, interaction_id="ix", leg_type="response", payload_hash="unclassified")
    _request(configured_db, "ix")
    observer = _Observer()
    assert driver.drain(0, observer) == req  # the response holds the cursor
    assert observer.seen == ["ix"]  # delivered once by the cursor arm, for the request leg
    assert _requests(configured_db) == {"ix"}  # the request waits for the response leg


def test_off_requests_are_not_drained(configured_db: str) -> None:
    seq = _insert_leg(configured_db, interaction_id="ix", leg_type="request", payload_hash=None)
    driver.drain(0, _Observer())
    _request(configured_db, "ix")
    observer = _Observer()
    driver.drain(seq, observer)
    assert observer.seen == [] and _requests(configured_db) == {"ix"}
