"""Cursor-drain loop for the P-classification processor (issue #77).

P-classification is a Layer-2 processor (sibling of P-interactions) that drains
``interaction_payloads`` by ``seq`` via the shared cursor-driven driver
(:mod:`data_governance.processors._driver`) and writes one **Classification**
row per **Payload** into ``payload_classifications`` (ADR-0024).

This tracer-bullet increment (issue #77) writes a *trivial stub* verdict —
``sensitivity_level='PUBLIC'``, zero **Findings**, ``model_version=1`` — with no
NER model and no real text projection yet (issue #78 swaps in the real logic
behind this same write path). These tests prove the end-to-end path through the
shared loop: insert a payload -> drain -> a write-once PUBLIC/empty row appears;
a crash mid-item re-processes from the same cursor with no duplicate row.

The drain tests run in-process against a migrated DB (fast), mirroring
``tests/processors/interactions/test_driver.py``.
"""

from __future__ import annotations

import psycopg
import pytest

from data_governance import db
from data_governance.processors.classification import driver


# --- helpers -----------------------------------------------------------------


def _insert_payload(
    dsn: str,
    *,
    content_hash: str,
    content_kind: str = "unknown",
    content: str = "{}",
) -> int:
    """Insert a content-addressed payload row (DEFAULT-allocated seq) the way
    P-interactions does; return its allocated ``seq``."""
    with psycopg.connect(dsn) as conn:
        (seq,) = conn.execute(
            "INSERT INTO interaction_payloads "
            "(content_hash, content_kind, content, byte_size) "
            "VALUES (%s, %s, %s::jsonb, %s) RETURNING seq",
            (content_hash, content_kind, content, len(content)),
        ).fetchone()
        conn.commit()
    return int(seq)


def _classification(dsn: str, content_hash: str) -> dict | None:
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT sensitivity_level, regulatory_tags, contains_identity_bundle, "
            "is_personalized, primary_domain, findings, model_version "
            "FROM payload_classifications WHERE content_hash = %s",
            (content_hash,),
        ).fetchone()
    if row is None:
        return None
    return {
        "sensitivity_level": row[0],
        "regulatory_tags": row[1],
        "contains_identity_bundle": row[2],
        "is_personalized": row[3],
        "primary_domain": row[4],
        "findings": row[5],
        "model_version": row[6],
    }


# --- tracer bullet: drain writes one stub classification ---------------------


def test_drain_writes_a_stub_classification_for_each_payload(configured_db: str) -> None:
    """A drain classifies every payload past the cursor, writing one write-once
    row with the tracer-bullet stub verdict: PUBLIC, zero Findings,
    model_version=1 (issue #77 / ADR-0024)."""
    _insert_payload(configured_db, content_hash="p0")

    with db.transaction() as tx:
        cursor = driver.read_cursor(tx)
    driver.drain(cursor)

    verdict = _classification(configured_db, "p0")
    assert verdict is not None, "P-classification must write a row for the payload"
    assert verdict["sensitivity_level"] == "PUBLIC"
    assert verdict["findings"] == []
    assert verdict["model_version"] == 1


def test_stub_verdict_is_a_clean_public_row(configured_db: str) -> None:
    """The stub is a real clean verdict, not a null-ish placeholder: no
    regulatory tags, no identity bundle, not personalized, no primary domain
    (ADR-0024 — a payload with no sensitive text gets a real PUBLIC/zero-Findings
    verdict, never a null)."""
    _insert_payload(configured_db, content_hash="clean")
    with db.transaction() as tx:
        cursor = driver.read_cursor(tx)
    driver.drain(cursor)

    verdict = _classification(configured_db, "clean")
    assert verdict["regulatory_tags"] == []
    assert verdict["contains_identity_bundle"] is False
    assert verdict["is_personalized"] is False
    assert verdict["primary_domain"] is None


def test_drain_from_empty_is_noop(configured_db: str) -> None:
    """No payloads past the cursor → cursor stays at 0, no rows written."""
    with db.transaction() as tx:
        cursor = driver.read_cursor(tx)
    assert driver.drain(cursor) == 0
    with psycopg.connect(configured_db) as conn:
        (n,) = conn.execute(
            "SELECT count(*) FROM payload_classifications"
        ).fetchone()
    assert n == 0


def _cursor(dsn: str) -> int:
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT last_processed_seq FROM processor_state "
            "WHERE processor_name = 'classification'"
        ).fetchone()
    return int(row[0]) if row else 0


# --- dg_interaction_leg_ready tap (issue #123, ADR-0027) ---------------------


def test_classification_taps_dg_interaction_leg_ready(configured_db: str) -> None:
    """P-classification fires a blind, payload-less ``dg_interaction_leg_ready``
    notification in the SAME transaction as each classification write (issue #123,
    ADR-0027): classifying a payload may have made a payload-bearing leg ready, so
    the leg-readiness consumer is woken to re-drain. The tap carries no correctness
    weight (the consumer's poll backstop and cursor drain are authoritative) — it
    is latency-only — but it must fire on a real classification write.

    Firing it via ``tx.execute`` inside the per-payload transaction means it does
    NOT fire if the classification rolls back (a payload never falsely announced
    as ready).
    """
    _insert_payload(configured_db, content_hash="tap0")

    with psycopg.connect(configured_db, autocommit=True) as listener:
        listener.execute("LISTEN dg_interaction_leg_ready")
        with db.transaction() as tx:
            cursor = driver.read_cursor(tx)
        driver.drain(cursor)
        notifies = list(listener.notifies(timeout=5.0, stop_after=1))

    assert notifies, "a classification write must tap dg_interaction_leg_ready"
    assert notifies[0].channel == "dg_interaction_leg_ready"
    assert notifies[0].payload == "", "the tap is payload-less (a 'go look' signal)"


def test_drain_advances_cursor_and_is_incremental(configured_db: str) -> None:
    """A drain advances the durable ``classification`` cursor to the last
    payload's seq; a second drain only classifies payloads that arrived since."""
    s0 = _insert_payload(configured_db, content_hash="a")
    with db.transaction() as tx:
        c0 = driver.read_cursor(tx)
    c1 = driver.drain(c0)
    assert c1 == s0
    assert _cursor(configured_db) == s0

    s1 = _insert_payload(configured_db, content_hash="b")
    c2 = driver.drain(c1)
    assert c2 == s1 and c2 > c1
    # Both payloads now classified, exactly once each.
    with psycopg.connect(configured_db) as conn:
        (n,) = conn.execute(
            "SELECT count(*) FROM payload_classifications"
        ).fetchone()
    assert n == 2


# --- idempotency / crash recovery (ADR-0024 write-once, ADR-0007 atomicity) --


def test_reprocessing_a_payload_writes_no_duplicate_row(configured_db: str) -> None:
    """Write-once (ADR-0024): re-classifying the SAME payload (e.g. after a
    cursor reset or a replay) is a no-op — ``ON CONFLICT (content_hash) DO
    NOTHING`` — leaving exactly one row, never a duplicate or an in-place
    mutation."""
    _insert_payload(configured_db, content_hash="dup")

    # Drain twice from cursor 0: the second pass re-processes the same payload.
    driver.drain(0)
    driver.drain(0)

    with psycopg.connect(configured_db) as conn:
        (n,) = conn.execute(
            "SELECT count(*) FROM payload_classifications WHERE content_hash = 'dup'"
        ).fetchone()
    assert n == 1, "re-processing a payload must not duplicate its classification row"


def test_crash_mid_payload_re_processes_from_same_cursor_without_duplicate(
    configured_db: str, monkeypatch
) -> None:
    """ADR-0007 atomicity + ADR-0024 idempotency, the headline acceptance case.

    The classification write and the cursor advance commit in ONE transaction. A
    crash mid-payload rolls back BOTH — the cursor does not advance past the
    crashing payload, and its (partial) write rolls back with it. On restart the
    payload is re-processed from the same cursor and lands exactly one row.
    """
    good = _insert_payload(configured_db, content_hash="ok")
    _insert_payload(configured_db, content_hash="boom")

    # Make classifying "boom" raise, simulating a crash after "ok" committed.
    real_classify = driver.verdict.classify

    def _boom(content_hash, content_kind, content, **kwargs):  # noqa: ANN001 — test stub
        if content_hash == "boom":
            raise RuntimeError("crash mid-payload")
        return real_classify(content_hash, content_kind, content, **kwargs)

    monkeypatch.setattr(driver.verdict, "classify", _boom)

    with pytest.raises(RuntimeError, match="crash mid-payload"):
        driver.drain(0)

    # "ok" committed (row + cursor advance); "boom" rolled back entirely — no
    # row, and the cursor did not advance past "ok".
    assert _cursor(configured_db) == good
    assert _classification(configured_db, "boom") is None, (
        "the crashing payload's write must roll back with the cursor"
    )

    # Recovery: the model is fixed and the processor restarts, re-draining from
    # the durable cursor. "boom" is now classified, exactly one row, no dup of
    # the already-committed "ok".
    monkeypatch.setattr(driver.verdict, "classify", real_classify)
    driver.drain(_cursor(configured_db))

    assert _classification(configured_db, "boom") is not None
    with psycopg.connect(configured_db) as conn:
        (n,) = conn.execute(
            "SELECT count(*) FROM payload_classifications"
        ).fetchone()
    assert n == 2, "recovery must classify boom without duplicating ok"


# --- detector injection (issue #79) ------------------------------------------
#
# Issue #79 swaps the in-process NER model in behind the narrow detector seam
# (ADR-0023). The seam is injected at the ``verdict.classify(..., detector=...)``
# call site inside ``process_payload`` — the driver threads an
# already-constructed detector (loaded once at startup, not per payload) and the
# model generation it writes. With no detector the drain keeps its #78 behaviour
# (the no-op NullDetector default → a real PUBLIC/zero-Findings verdict). These
# tests use a FAKE detector so the whole drain path is exercised without torch or
# the ~500 MB weights (which are baked into the image, not the dev checkout).


class _FakeDetector:
    """A stand-in :class:`Detector`: returns fixed annotations for any text.

    Lets the drain path be exercised end-to-end without the real torch model —
    the seam is text-in / annotations-out (ADR-0023), so a fake honouring that
    shape drives the whole project → detect → aggregate → write path.
    """

    def __init__(self, annotations: list[tuple[int, int, str]]) -> None:
        self._annotations = annotations

    def detect(self, text: str) -> list[tuple[int, int, str]]:
        return list(self._annotations)


def test_injected_detector_findings_land_in_the_written_row(configured_db: str) -> None:
    """A detector injected into the drain produces real **Findings** in the
    persisted **Classification** row — the model swaps in behind the seam and the
    write path is unchanged (ADR-0023). The projected text of an ``llm_chat_prompt``
    payload is its message body; the fake reports a name + SSN in it, so the row is
    RESTRICTED with two findings and an identity bundle."""
    content = (
        '{"messages": [{"message.role": "user", '
        '"message.content": "John Smith 123-45-6789"}]}'
    )
    _insert_payload(
        configured_db,
        content_hash="ner",
        content_kind="llm_chat_prompt",
        content=content,
    )
    detector = _FakeDetector([(0, 10, "PN"), (11, 22, "SSN")])

    driver.drain(0, detector=detector)

    row = _classification(configured_db, "ner")
    assert row is not None
    # The headline #79 acceptance: an SSN in the payload → a RESTRICTED verdict
    # carrying the corresponding SSN **Finding**, end-to-end through the drain.
    assert row["sensitivity_level"] == "RESTRICTED"
    assert row["contains_identity_bundle"] is True
    assert {f["entity_type"] for f in row["findings"]} == {"PN", "SSN"}

    ssn = next(f for f in row["findings"] if f["entity_type"] == "SSN")
    assert ssn["sensitivity_level"] == "RESTRICTED"
    assert (ssn["start"], ssn["end"]) == (11, 22)
    # The finding's text is sliced from the projected Classifiable text (the
    # message body), NOT the raw JSONB — proving the projection ran before detect.
    assert ssn["text"] == "123-45-6789"


def test_drain_without_a_detector_keeps_the_null_default(configured_db: str) -> None:
    """No injected detector → the #78 behaviour is preserved: the no-op
    NullDetector default yields a real PUBLIC / zero-**Findings** verdict, never a
    null (ADR-0024)."""
    _insert_payload(
        configured_db,
        content_hash="clean79",
        content_kind="llm_chat_prompt",
        content='{"messages": [{"message.role": "user", "message.content": "Book a flight."}]}',
    )
    driver.drain(0)

    row = _classification(configured_db, "clean79")
    assert row is not None
    assert row["sensitivity_level"] == "PUBLIC"
    assert row["findings"] == []


def test_injected_detector_stamps_its_model_version(configured_db: str) -> None:
    """The model generation the detector represents is stamped on every row it
    writes: image tag ↔ ``model_version`` (ADR-0023/0024). A drain with an
    injected model_version writes that version, distinguishing model-era rows from
    the #78 no-model generation (model_version=1)."""
    _insert_payload(configured_db, content_hash="mv", content_kind="unknown")
    detector = _FakeDetector([])

    driver.drain(0, detector=detector, model_version=2)

    row = _classification(configured_db, "mv")
    assert row is not None
    assert row["model_version"] == 2
