"""End-to-end tests for the risk engine wired into the leg-ready consumer
(issue #158) — the acceptance criteria, proven through the REAL consumer path
(``driver.drain`` + ``make_risk_observer``), not by calling the engine
directly (#101's tests already do that):

- a leg becoming ready produces an interaction risk record with no manual
  invocation;
- AC-DAS-004 through the consumer: a response leg attached later (its
  classification already present) produces version 2 with no "done" signal;
- a compute failure (OPA unreachable) does NOT advance the cursor past the
  failed leg — the leg is re-delivered on a later wake, and legs behind it
  are held (head-of-line), so nothing is lost or reordered;
- a permanently-failing (poison) leg does not wedge the stream: it is
  loudly skipped and the cursor advances past it;
- the OPA round-trip happens with NO DB transaction held (proven with a
  max_size=1 pool: were a connection held across ``evaluate``, the fake
  client's own transaction would exhaust the pool and time out);
- idempotency across re-delivery (FR-DAS-014): a re-delivered leg with
  unchanged evidence writes no duplicate version and triggers no second OPA
  call.

Real DB via ``configured_db`` (tests/processors/conftest.py); the OPA client
is the same duck-typed fake shape as ``tests/risk/engine/test_compute.py``'s.
"""

from __future__ import annotations

import psycopg
import pytest

from data_governance import db
from data_governance.processors.leg_ready import driver
from data_governance.risk.engine import compute
from data_governance.risk.engine.observer import make_risk_observer
from data_governance.risk.engine.opa import OpaDecision, OpaRequestError


# --- helpers (mirroring test_driver.py's shapes) ------------------------------


def _mk_interaction(conn: psycopg.Connection, ix_id: str) -> None:
    for eid in (f"{ix_id}-caller", f"{ix_id}-callee"):
        conn.execute(
            "INSERT INTO entities (id, kind, natural_key, display_name, "
            "detected_from, original_seq) VALUES (%s, 'agent', %s, 'n', 'd', 1) "
            "ON CONFLICT (natural_key) DO NOTHING",
            (eid, eid),
        )
    conn.execute(
        "INSERT INTO interactions (id, trace_id, caller_entity_id, "
        "callee_entity_id, summary) VALUES (%s, 't-158', %s, %s, 's') "
        "ON CONFLICT (id) DO NOTHING",
        (ix_id, f"{ix_id}-caller", f"{ix_id}-callee"),
    )


def _insert_leg(
    dsn: str,
    *,
    interaction_id: str,
    leg_type: str,
    payload_hash: str | None = None,
    with_interaction: bool = True,
) -> int:
    with psycopg.connect(dsn) as conn:
        if with_interaction:
            _mk_interaction(conn, interaction_id)
        (seq,) = conn.execute(
            "INSERT INTO interaction_legs (interaction_id, leg_type, "
            "occurred_at, payload_hash, error) "
            "VALUES (%s, %s, now(), %s, false) RETURNING seq",
            (interaction_id, leg_type, payload_hash),
        ).fetchone()
        conn.commit()
    return int(seq)


def _classify(dsn: str, content_hash: str) -> None:
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "INSERT INTO payload_classifications "
            "(content_hash, sensitivity_level, regulatory_tags, "
            " contains_identity_bundle, is_personalized, primary_domain, "
            " findings, model_version) "
            "VALUES (%s, 'PUBLIC', '{}', false, false, NULL, '[]'::jsonb, 1) "
            "ON CONFLICT (content_hash) DO NOTHING",
            (content_hash,),
        )
        conn.commit()


def _cursor(dsn: str) -> int:
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT last_processed_seq FROM processor_state "
            "WHERE processor_name = %s",
            (driver.PROCESSOR_NAME,),
        ).fetchone()
    return int(row[0]) if row else 0


def _risk_versions(dsn: str, interaction_id: str) -> list[tuple[int, list[str]]]:
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            "SELECT version, legs_evidenced FROM interaction_risk_records "
            "WHERE interaction_id = %s ORDER BY version",
            (interaction_id,),
        ).fetchall()
    return [(r[0], list(r[1])) for r in rows]


def _decision(**overrides) -> OpaDecision:
    defaults = dict(
        risk_level="low",
        enforcement_type=None,
        allowed_actions=[],
        explanation=None,
        triggered_rules=[],
        confidence=0.5,
        policy_version="1",
    )
    defaults.update(overrides)
    return OpaDecision(**defaults)


class _FakeOpaClient:
    """Duck-typed OpaClient: counts calls, can be told to fail, can run an
    arbitrary probe (used to prove no transaction is held during the call)."""

    def __init__(self, *, probe=None):
        self.calls: list[dict] = []
        self.fail_with: Exception | None = None
        self._probe = probe

    def evaluate(self, **kwargs) -> OpaDecision:
        self.calls.append(kwargs)
        if self._probe is not None:
            self._probe()
        if self.fail_with is not None:
            raise self.fail_with
        return _decision()


# --- AC: a ready leg drives a risk record automatically -----------------------


def test_ready_leg_produces_risk_record_via_consumer(configured_db: str) -> None:
    opa = _FakeOpaClient()
    seq = _insert_leg(configured_db, interaction_id="ix-auto", leg_type="request")

    driver.drain(0, observer=make_risk_observer(opa))

    assert _risk_versions(configured_db, "ix-auto") == [(1, ["request"])]
    assert _cursor(configured_db) == seq
    assert len(opa.calls) == 1


def test_response_leg_attached_later_produces_version_2(configured_db: str) -> None:
    """AC-DAS-004 through the real consumer: the request leg computes v1; the
    response leg arriving later (payload already classified) wakes the same
    path and computes v2 — no external completeness signal anywhere."""
    opa = _FakeOpaClient()
    observer = make_risk_observer(opa)

    _insert_leg(configured_db, interaction_id="ix-v2", leg_type="request")
    cursor = driver.drain(0, observer=observer)
    assert _risk_versions(configured_db, "ix-v2") == [(1, ["request"])]

    _classify(configured_db, "hash-v2-resp")
    _insert_leg(
        configured_db,
        interaction_id="ix-v2",
        leg_type="response",
        payload_hash="hash-v2-resp",
    )
    driver.drain(cursor, observer=observer)

    assert _risk_versions(configured_db, "ix-v2") == [
        (1, ["request"]),
        (2, ["request", "response"]),
    ]


# --- AC: OPA failure holds the cursor; recovery catches up --------------------


def test_opa_outage_holds_cursor_then_recovery_catches_up(
    configured_db: str,
) -> None:
    opa = _FakeOpaClient()
    observer = make_risk_observer(opa)

    first = _insert_leg(configured_db, interaction_id="ix-hold", leg_type="request")
    behind = _insert_leg(configured_db, interaction_id="ix-behind", leg_type="request")

    opa.fail_with = OpaRequestError("OPA unreachable (injected)")
    cursor = driver.drain(0, observer=observer)

    # Nothing advanced, nothing written, nothing behind the block delivered.
    assert cursor == 0
    assert _cursor(configured_db) == 0
    assert _risk_versions(configured_db, "ix-hold") == []
    assert _risk_versions(configured_db, "ix-behind") == []

    # "OPA restored": the next wake re-delivers the SAME leg first, then the
    # legs that were held behind it — order preserved, nothing lost.
    opa.fail_with = None
    cursor = driver.drain(0, observer=observer)
    assert cursor == behind
    assert _cursor(configured_db) == behind
    assert _risk_versions(configured_db, "ix-hold") == [(1, ["request"])]
    assert _risk_versions(configured_db, "ix-behind") == [(1, ["request"])]
    assert first < behind


def test_poison_leg_is_skipped_loudly_not_wedging(configured_db: str, caplog) -> None:
    """A leg whose interaction row does not exist can never compute: the
    observer logs ERROR and delivers nothing, the cursor advances, and the
    stream continues to the next leg."""
    opa = _FakeOpaClient()
    observer = make_risk_observer(opa)

    _insert_leg(
        configured_db,
        interaction_id="ix-ghost",
        leg_type="request",
        with_interaction=False,
    )
    good = _insert_leg(configured_db, interaction_id="ix-after", leg_type="request")

    with caplog.at_level("ERROR"):
        cursor = driver.drain(0, observer=observer)

    assert cursor == good, "the stream advances past the poison leg"
    assert _risk_versions(configured_db, "ix-ghost") == []
    assert _risk_versions(configured_db, "ix-after") == [(1, ["request"])]
    assert any("ix-ghost" in message for message in caplog.messages)


# --- AC: the OPA round-trip holds no DB transaction ---------------------------


def test_opa_call_happens_with_no_transaction_held(configured_db: str) -> None:
    """With the pool clamped to ONE connection, the fake client's evaluate()
    opens its own transaction. If the drain (or the engine) held any
    transaction across the OPA call, this would exhaust the pool and raise
    ConnectionTimeout — passing proves the round-trip runs unheld."""

    def _probe() -> None:
        with db.transaction() as tx:
            tx.fetch_one("SELECT 1")

    opa = _FakeOpaClient(probe=_probe)
    _insert_leg(configured_db, interaction_id="ix-notx", leg_type="request")

    db.configure(configured_db, min_size=1, max_size=1, timeout=3.0)
    try:
        driver.drain(0, observer=make_risk_observer(opa))
    finally:
        db.configure(configured_db)

    assert _risk_versions(configured_db, "ix-notx") == [(1, ["request"])]
    assert len(opa.calls) == 1


# --- AC: idempotency across re-delivery ---------------------------------------


def test_redelivery_with_unchanged_evidence_is_a_no_op(configured_db: str) -> None:
    """FR-DAS-014 through the consumer: re-draining the same leg (cursor
    reset, simulating a crash after the record write but before the cursor
    commit — or any re-delivery) writes no duplicate version AND makes no
    second OPA call (the evidence fingerprint reuses the cached decision)."""
    opa = _FakeOpaClient()
    observer = make_risk_observer(opa)

    _insert_leg(configured_db, interaction_id="ix-idem", leg_type="request")
    driver.drain(0, observer=observer)
    assert _risk_versions(configured_db, "ix-idem") == [(1, ["request"])]
    assert len(opa.calls) == 1

    driver.drain(0, observer=observer)  # re-deliver from scratch

    assert _risk_versions(configured_db, "ix-idem") == [(1, ["request"])]
    assert len(opa.calls) == 1, "unchanged evidence must not re-call OPA"


# --- AC: a version race is interaction-scoped, not stream-scoped --------------


def test_version_race_does_not_hold_the_legs_behind_it(
    configured_db: str, monkeypatch
) -> None:
    """A lost version race is scoped to ONE interaction, so it must not stop
    the stream the way an unreachable OPA does. The engine retries the
    decision refresh in place; the observer never sees the collision, the
    cursor advances past the affected leg, and the leg queued behind it is
    delivered in the same drain.

    Contrast ``test_opa_outage_holds_cursor_then_recovery_catches_up``: there
    the condition is global, every leg behind would hit the same wall, and
    holding is correct. Here it is neither."""
    opa = _FakeOpaClient()
    observer = make_risk_observer(opa)

    _insert_leg(configured_db, interaction_id="ix-race", leg_type="request")
    behind = _insert_leg(
        configured_db, interaction_id="ix-race-behind", leg_type="request"
    )

    real_once = compute._get_or_refresh_decision_once
    attempts = {"n": 0}

    def _lose_the_first_race(**kwargs):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise psycopg.errors.UniqueViolation(
                "duplicate key value violates unique constraint "
                '"interaction_policy_decisions_version_uq"'
            )
        return real_once(**kwargs)

    monkeypatch.setattr(
        compute, "_get_or_refresh_decision_once", _lose_the_first_race
    )

    cursor = driver.drain(0, observer=observer)

    assert cursor == behind, "an interaction-scoped race must not hold the stream"
    assert _risk_versions(configured_db, "ix-race") == [(1, ["request"])]
    assert _risk_versions(configured_db, "ix-race-behind") == [(1, ["request"])]
    assert attempts["n"] == 3, "one loss, one retry, then the leg behind it"
