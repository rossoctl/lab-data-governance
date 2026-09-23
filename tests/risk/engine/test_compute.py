"""Tests for the interaction risk engine's orchestration/write path (issue #101).

``data_governance.risk.engine.compute`` ties the pieces together: gather
evidence -> decide whether OPA needs a fresh call (comparing the evidence
fingerprint against the interaction's latest stored policy decision) ->
aggregate -> write a new ``interaction_risk_records`` version only when the
computed record actually differs from the latest stored one (FR-DAS-014
idempotency), never mutating a prior version (FR-DAS-013/032 immutability).

Real DB via ``configured_db``. The injected OPA client is a tiny fake (not
``httpx.MockTransport`` — that seam belongs to ``opa.py``'s own tests; here
we only need to prove *how many times* and *with what* ``compute.py`` calls
whatever :class:`OpaClient`-shaped object it is given).
"""

from __future__ import annotations

import threading

import psycopg
import pytest

from data_governance import db
from data_governance.risk.engine.compute import (
    compute_interaction_risk,
    prepare_interaction_risk,
)
from data_governance.risk.engine.opa import OpaDecision

_TID = "trace-cx-1"
_IX_ID = "ix-cx-1"
_ENT_A = "ent-cx-a"
_ENT_B = "ent-cx-b"


class _FakeOpaClient:
    """Records every call; returns queued decisions in order (or the last
    one repeated if the queue is exhausted)."""

    def __init__(self, decisions: list[OpaDecision]):
        self._decisions = decisions
        self.calls: list[dict] = []

    def evaluate(self, **kwargs) -> OpaDecision:
        self.calls.append(kwargs)
        index = min(len(self.calls) - 1, len(self._decisions) - 1)
        return self._decisions[index]


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


def _insert_interaction(conn: psycopg.Connection, *, interaction_id: str = _IX_ID) -> None:
    conn.execute(
        "INSERT INTO interactions (id, trace_id, caller_entity_id, "
        "callee_entity_id, summary) "
        "VALUES (%s, %s, %s, %s, 'did a thing')",
        (interaction_id, _TID, _ENT_A, _ENT_B),
    )


def _insert_leg(
    conn: psycopg.Connection,
    *,
    interaction_id: str = _IX_ID,
    leg_type: str,
    payload_hash: str | None,
) -> None:
    conn.execute(
        "INSERT INTO interaction_legs (interaction_id, leg_type, occurred_at, "
        "payload_hash, error) VALUES (%s, %s, '2026-01-01T00:00:00Z', %s, false)",
        (interaction_id, leg_type, payload_hash),
    )


@pytest.fixture()
def seeded(configured_db: str) -> str:
    with psycopg.connect(configured_db) as conn:
        _insert_interaction(conn)
        _insert_leg(conn, leg_type="request", payload_hash=None)
        conn.commit()
    return configured_db


def _latest_risk_row(dsn: str, interaction_id: str = _IX_ID) -> tuple | None:
    with psycopg.connect(dsn) as conn:
        return conn.execute(
            "SELECT version, risk_level, legs_evidenced, policy_event_count "
            "FROM interaction_risk_records WHERE interaction_id = %s "
            "ORDER BY version DESC LIMIT 1",
            (interaction_id,),
        ).fetchone()


def _all_risk_versions(dsn: str, interaction_id: str = _IX_ID) -> list[int]:
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            "SELECT version FROM interaction_risk_records "
            "WHERE interaction_id = %s ORDER BY version",
            (interaction_id,),
        ).fetchall()
    return [r[0] for r in rows]


def _policy_decision_versions(dsn: str, interaction_id: str = _IX_ID) -> list[int]:
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            "SELECT version FROM interaction_policy_decisions "
            "WHERE interaction_id = %s ORDER BY version",
            (interaction_id,),
        ).fetchall()
    return [r[0] for r in rows]


# --- first compute -------------------------------------------------------------


def test_first_compute_writes_version_1(seeded: str):
    opa = _FakeOpaClient([_decision(risk_level="high", triggered_rules=["DG-001"])])
    compute_interaction_risk(_IX_ID, opa_client=opa)

    row = _latest_risk_row(seeded)
    assert row is not None
    version, risk_level, legs_evidenced, policy_event_count = row
    assert version == 1
    assert risk_level == "high"
    assert legs_evidenced == ["request"]
    assert policy_event_count == 1
    assert len(opa.calls) == 1


def test_request_only_record_has_no_response_in_legs_evidenced(seeded: str):
    opa = _FakeOpaClient([_decision()])
    compute_interaction_risk(_IX_ID, opa_client=opa)

    _, _, legs_evidenced, _ = _latest_risk_row(seeded)
    assert legs_evidenced == ["request"]


def test_no_is_complete_equivalent_field_is_ever_written(seeded: str):
    opa = _FakeOpaClient([_decision()])
    compute_interaction_risk(_IX_ID, opa_client=opa)

    with psycopg.connect(seeded) as conn:
        cols = [
            r[0]
            for r in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'interaction_risk_records'"
            ).fetchall()
        ]
    assert not any("complete" in c for c in cols)


# --- idempotency (FR-DAS-014) --------------------------------------------------


def test_unchanged_evidence_writes_nothing(seeded: str):
    opa = _FakeOpaClient([_decision()])
    compute_interaction_risk(_IX_ID, opa_client=opa)
    assert _all_risk_versions(seeded) == [1]

    # Recompute with no evidence change and the same OPA client (which would
    # return the same decision again if called) — must be a true no-op.
    compute_interaction_risk(_IX_ID, opa_client=opa)
    assert _all_risk_versions(seeded) == [1]


def test_unchanged_evidence_does_not_call_opa_again(seeded: str):
    """The cached policy decision (matching evidence_fingerprint) is reused —
    no second OPA call — on a recompute with no evidence change."""
    opa = _FakeOpaClient([_decision()])
    compute_interaction_risk(_IX_ID, opa_client=opa)
    assert len(opa.calls) == 1

    compute_interaction_risk(_IX_ID, opa_client=opa)
    assert len(opa.calls) == 1
    assert _policy_decision_versions(seeded) == [1]


# --- new evidence -> new version (AC-DAS-004 at engine level) -----------------


def test_attaching_response_leg_writes_version_2(seeded: str):
    opa = _FakeOpaClient([_decision(risk_level="low"), _decision(risk_level="high")])
    compute_interaction_risk(_IX_ID, opa_client=opa)
    assert _all_risk_versions(seeded) == [1]

    with psycopg.connect(seeded) as conn:
        _insert_leg(conn, leg_type="response", payload_hash="resphash")
        conn.commit()

    compute_interaction_risk(_IX_ID, opa_client=opa)
    assert _all_risk_versions(seeded) == [1, 2]

    row = _latest_risk_row(seeded)
    version, risk_level, legs_evidenced, _ = row
    assert version == 2
    assert risk_level == "high"
    assert legs_evidenced == ["request", "response"]
    assert len(opa.calls) == 2


def test_new_evidence_calls_opa_and_inserts_new_decision_version(seeded: str):
    opa = _FakeOpaClient([_decision(risk_level="low"), _decision(risk_level="high")])
    compute_interaction_risk(_IX_ID, opa_client=opa)

    with psycopg.connect(seeded) as conn:
        _insert_leg(conn, leg_type="response", payload_hash="resphash")
        conn.commit()

    compute_interaction_risk(_IX_ID, opa_client=opa)
    assert _policy_decision_versions(seeded) == [1, 2]


def test_version_monotonically_increases(seeded: str):
    """Each real evidence change (leg attached, then that leg's payload
    classified) versions forward: 1 -> 2 -> 3, never skipping or reusing a
    version number."""
    opa = _FakeOpaClient(
        [_decision(risk_level="low"), _decision(risk_level="medium"), _decision(risk_level="high")]
    )
    compute_interaction_risk(_IX_ID, opa_client=opa)

    with psycopg.connect(seeded) as conn:
        _insert_leg(conn, leg_type="response", payload_hash="resphash")
        conn.commit()
    compute_interaction_risk(_IX_ID, opa_client=opa)

    with psycopg.connect(seeded) as conn:
        conn.execute(
            "INSERT INTO payload_classifications (content_hash, "
            "sensitivity_level, model_version) VALUES ('resphash', 'RESTRICTED', 1)"
        )
        conn.commit()
    compute_interaction_risk(_IX_ID, opa_client=opa)

    assert _all_risk_versions(seeded) == [1, 2, 3]


# --- NOTIFY trigger -------------------------------------------------------------


def test_notify_fires_on_insert_not_on_skipped_write(seeded: str):
    with psycopg.connect(seeded, autocommit=True) as listen_conn:
        listen_conn.execute("LISTEN dg_interaction_risk_written")

        opa = _FakeOpaClient([_decision()])
        compute_interaction_risk(_IX_ID, opa_client=opa)

        notifications = list(listen_conn.notifies(timeout=2, stop_after=1))
        assert len(notifications) == 1

        # Unchanged evidence -> no write -> no notification.
        compute_interaction_risk(_IX_ID, opa_client=opa)
        notifications = list(listen_conn.notifies(timeout=0.5))
        assert notifications == []


# --- retry on UNIQUE (interaction_id, version) collision -----------------------


def test_racing_computes_collide_and_the_retry_recovers(seeded: str, monkeypatch):
    """Two concurrent ``compute_interaction_risk(_IX_ID, ...)`` calls both see
    no cached policy decision (fresh interaction), both call OPA, and both
    race to insert the first ``interaction_policy_decisions`` version. A
    barrier holds both threads right before that insert so they submit
    concurrently: one wins, the other's insert hits
    ``interaction_policy_decisions_version_uq``, is caught by
    ``compute_interaction_risk``'s retry loop, and retries — the retry
    re-gathers evidence, finds the winner's decision already cached (matching
    fingerprint), reuses it, and writes (or, if the winner already wrote it,
    no-ops on) the risk record. Final state: no unhandled exception, exactly
    one decision version, exactly one risk record version."""
    barrier = threading.Barrier(2)
    real_execute = psycopg.Cursor.execute

    def _gated_execute(self, sql, params=None, *args, **kwargs):
        if isinstance(sql, str) and "INSERT INTO interaction_policy_decisions" in sql:
            barrier.wait(timeout=20)
        return real_execute(self, sql, params, *args, **kwargs)

    monkeypatch.setattr(psycopg.Cursor, "execute", _gated_execute)

    errors: list[BaseException] = []

    def _run() -> None:
        try:
            # Each thread gets its own fake OPA client — `_FakeOpaClient`
            # isn't thread-safe, and both racing computes are expected to
            # produce the identical decision anyway.
            opa = _FakeOpaClient([_decision(risk_level="high")])
            compute_interaction_risk(_IX_ID, opa_client=opa)
        except BaseException as exc:  # noqa: BLE001 - surfaced to the main thread below
            errors.append(exc)

    t1 = threading.Thread(target=_run)
    t2 = threading.Thread(target=_run)
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)

    assert not t1.is_alive() and not t2.is_alive()
    assert errors == []
    assert _policy_decision_versions(seeded) == [1]
    assert _all_risk_versions(seeded) == [1]
    row = _latest_risk_row(seeded)
    assert row is not None
    assert row[1] == "high"


def test_racing_prepares_collide_and_the_decision_retry_recovers(
    seeded: str, monkeypatch
):
    """The same race as above, but on the SPLIT path the leg-ready consumer
    actually runs (issue #158). Two concurrent ``prepare_interaction_risk``
    calls both see no cached decision, both call OPA, and the same barrier
    makes them submit the ``interaction_policy_decisions`` insert together.

    ``compute_interaction_risk``'s retry loop is not in play here — the
    caller holds the write half — so this proves the recovery lives low
    enough to protect the split path on its own:
    ``_get_or_refresh_decision`` absorbs the collision, re-enters through
    the cache lookup, finds the winner's decision under the same evidence
    fingerprint, and reuses it. Both callers get a usable write closure, no
    exception escapes toward the observer, and the table holds exactly one
    decision version."""
    barrier = threading.Barrier(2)
    real_execute = psycopg.Cursor.execute

    def _gated_execute(self, sql, params=None, *args, **kwargs):
        if isinstance(sql, str) and "INSERT INTO interaction_policy_decisions" in sql:
            barrier.wait(timeout=20)
        return real_execute(self, sql, params, *args, **kwargs)

    monkeypatch.setattr(psycopg.Cursor, "execute", _gated_execute)

    errors: list[BaseException] = []
    closures: list[object] = []

    def _run() -> None:
        try:
            # Per-thread fake: `_FakeOpaClient` isn't thread-safe, and both
            # racing computes are expected to produce the same decision.
            opa = _FakeOpaClient([_decision(risk_level="high")])
            closures.append(prepare_interaction_risk(_IX_ID, opa_client=opa))
        except BaseException as exc:  # noqa: BLE001 - surfaced below
            errors.append(exc)

    t1 = threading.Thread(target=_run)
    t2 = threading.Thread(target=_run)
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)

    assert not t1.is_alive() and not t2.is_alive()
    assert errors == [], "a lost decision race must not escape the engine"
    assert len(closures) == 2
    assert _policy_decision_versions(seeded) == [1]

    # The write half still works after the race: running one closure writes
    # the single expected record version.
    with db.transaction() as tx:
        closures[0](tx)
    assert _all_risk_versions(seeded) == [1]
    row = _latest_risk_row(seeded)
    assert row is not None
    assert row[1] == "high"


# --- corner cases ---------------------------------------------------------------


def test_zero_legs_still_computes_a_record(configured_db: str):
    with psycopg.connect(configured_db) as conn:
        _insert_interaction(conn)
        conn.commit()

    opa = _FakeOpaClient([_decision(risk_level="none")])
    compute_interaction_risk(_IX_ID, opa_client=opa)

    row = _latest_risk_row(configured_db)
    version, risk_level, legs_evidenced, _ = row
    assert version == 1
    assert risk_level == "none"
    assert legs_evidenced == []


# --- the compiled bundle's fallback rule (#173/#176) ---------------------------
#
# When no catalog rule fires, the compiled Rego's default decision reports
# triggered_rules == ["0000"] (rego.FALLBACK_RULE_ID). That sentinel means
# "nothing fired" and must not be stored as a fired rule on the risk record —
# metrics unnest triggered_rule_ids to count rules fired, and alerts name
# rules from it. The decision cache keeps OPA's answer verbatim.


def _rule_columns(dsn: str, interaction_id: str = _IX_ID) -> tuple[list[str], list[str]]:
    with psycopg.connect(dsn) as conn:
        (record_rules,) = conn.execute(
            "SELECT triggered_rule_ids FROM interaction_risk_records "
            "WHERE interaction_id = %s ORDER BY version DESC LIMIT 1",
            (interaction_id,),
        ).fetchone()
        (cached_rules,) = conn.execute(
            "SELECT triggered_rules FROM interaction_policy_decisions "
            "WHERE interaction_id = %s ORDER BY version DESC LIMIT 1",
            (interaction_id,),
        ).fetchone()
    return list(record_rules), list(cached_rules)


def test_fallback_rule_id_is_not_stored_as_a_fired_rule(seeded: str):
    from data_governance.risk.rules.rego import FALLBACK_RULE_ID

    opa = _FakeOpaClient(
        [_decision(risk_level="none", enforcement_type="allow",
                   triggered_rules=[FALLBACK_RULE_ID])]
    )
    compute_interaction_risk(_IX_ID, opa_client=opa)

    record_rules, cached_rules = _rule_columns(seeded)
    assert record_rules == [], "the fallback sentinel is not a catalog rule"
    assert cached_rules == [FALLBACK_RULE_ID], "the decision cache keeps OPA's raw answer"
    *_, policy_event_count = _latest_risk_row(seeded)
    assert policy_event_count == 0, "a fallback-only decision is not a policy event"


def test_real_rule_ids_are_stored_verbatim(seeded: str):
    opa = _FakeOpaClient(
        [_decision(risk_level="critical", triggered_rules=["DG-002", "DG-001"])]
    )
    compute_interaction_risk(_IX_ID, opa_client=opa)

    record_rules, _cached = _rule_columns(seeded)
    assert sorted(record_rules) == ["DG-001", "DG-002"]
    *_, policy_event_count = _latest_risk_row(seeded)
    assert policy_event_count == 1, "one evaluation, not one per fired rule"
