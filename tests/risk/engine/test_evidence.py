"""Tests for evidence gathering (issue #101).

``data_governance.risk.engine.evidence`` is the DB-reading I/O shell that
feeds :mod:`data_governance.risk.engine.utils`'s pure functions: given one
``interaction_id``, read its identity row, its legs (in ``seq`` order), the
span set evidencing it, and — per leg with a payload — the joined
classification verdict from ``payload_classifications`` (keyed by
``content_hash`` == the leg's ``payload_hash``, mirroring
``leg_ready/driver.py``'s existing join).

Real DB via ``configured_db`` (inherited from ``tests/risk/conftest.py``) —
this module's whole job is to read Postgres correctly, so it is not the thing
to mock (ADR-0005 spirit: Postgres is the one dependency this repo never
fakes).
"""

from __future__ import annotations

import psycopg
import pytest

from data_governance.risk.engine import utils
from data_governance.risk.engine.evidence import (
    InteractionNotFoundError,
    gather_evidence,
)

_TID = "trace-ev-1"
_IX_ID = "ix-ev-1"
_ENT_A = "ent-a"
_ENT_B = "ent-b"


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
    error: bool | None = False,
    occurred_at: str = "2026-01-01T00:00:00Z",
) -> None:
    conn.execute(
        "INSERT INTO interaction_legs (interaction_id, leg_type, occurred_at, "
        "payload_hash, error) VALUES (%s, %s, %s, %s, %s)",
        (interaction_id, leg_type, occurred_at, payload_hash, error),
    )


def _insert_span(
    conn: psycopg.Connection,
    *,
    interaction_id: str = _IX_ID,
    span_id: str,
    role: str = "anchor",
    leg_type: str | None = "request",
) -> None:
    conn.execute(
        "INSERT INTO spans (trace_id, span_id, parent_id, kind, name, "
        "started_at, attributes, seq, arrival_seq, observed_at) "
        "VALUES (%s, %s, NULL, 'INTERNAL', 'call', now(), '{}'::jsonb, "
        "nextval('spans_seq'), currval('spans_seq'), now())",
        (_TID, span_id),
    )
    conn.execute(
        "INSERT INTO interaction_spans (interaction_id, trace_id, span_id, "
        "role, leg_type) VALUES (%s, %s, %s, %s, %s)",
        (interaction_id, _TID, span_id, role, leg_type),
    )


def _insert_classification(
    conn: psycopg.Connection,
    *,
    content_hash: str,
    sensitivity_level: str = "RESTRICTED",
    regulatory_tags: list[str] | None = None,
    contains_identity_bundle: bool = False,
    is_personalized: bool = False,
    primary_domain: str | None = "finance",
    findings: list[dict] | None = None,
    model_version: int = 1,
) -> None:
    import json

    conn.execute(
        "INSERT INTO payload_classifications (content_hash, sensitivity_level, "
        "regulatory_tags, contains_identity_bundle, is_personalized, "
        "primary_domain, findings, model_version) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (
            content_hash,
            sensitivity_level,
            regulatory_tags or [],
            contains_identity_bundle,
            is_personalized,
            primary_domain,
            json.dumps(findings or []),
            model_version,
        ),
    )


# --- interaction identity row ------------------------------------------------


def test_gathers_interaction_identity(configured_db: str):
    with psycopg.connect(configured_db) as conn:
        _insert_interaction(conn)
        conn.commit()

    evidence = gather_evidence(_IX_ID)
    assert evidence.interaction_id == _IX_ID
    assert evidence.trace_id == _TID
    assert evidence.caller_entity_id == _ENT_A
    assert evidence.callee_entity_id == _ENT_B


def test_missing_interaction_raises_typed_error(configured_db: str):
    with pytest.raises(InteractionNotFoundError):
        gather_evidence("does-not-exist")


# --- legs ---------------------------------------------------------------------


def test_interaction_with_zero_legs(configured_db: str):
    with psycopg.connect(configured_db) as conn:
        _insert_interaction(conn)
        conn.commit()

    evidence = gather_evidence(_IX_ID)
    assert evidence.legs == []


def test_legs_read_in_seq_order(configured_db: str):
    with psycopg.connect(configured_db) as conn:
        _insert_interaction(conn)
        # Insert response before request — seq order (insertion order via the
        # sequence default) must still be respected regardless of leg_type.
        _insert_leg(conn, leg_type="response", payload_hash="resphash", error=True)
        _insert_leg(conn, leg_type="request", payload_hash="reqhash", error=False)
        conn.commit()

    evidence = gather_evidence(_IX_ID)
    assert [leg.leg_type for leg in evidence.legs] == ["response", "request"]
    assert evidence.legs[0].payload_hash == "resphash"
    assert evidence.legs[1].payload_hash == "reqhash"


def test_leg_evidence_is_utils_leg_evidence_type(configured_db: str):
    """evidence.py must hand utils.py the type it already expects."""
    with psycopg.connect(configured_db) as conn:
        _insert_interaction(conn)
        _insert_leg(conn, leg_type="request", payload_hash="reqhash")
        conn.commit()

    evidence = gather_evidence(_IX_ID)
    assert isinstance(evidence.legs[0], utils.LegEvidence)


# --- spans ---------------------------------------------------------------------


def test_interaction_with_zero_spans(configured_db: str):
    with psycopg.connect(configured_db) as conn:
        _insert_interaction(conn)
        conn.commit()

    evidence = gather_evidence(_IX_ID)
    assert evidence.span_ids == []


def test_gathers_span_ids_for_interaction(configured_db: str):
    with psycopg.connect(configured_db) as conn:
        _insert_interaction(conn)
        _insert_span(conn, span_id="s-anchor", role="anchor")
        _insert_span(conn, span_id="s-info", role="info")
        conn.commit()

    evidence = gather_evidence(_IX_ID)
    assert set(evidence.span_ids) == {"s-anchor", "s-info"}


def test_spans_scoped_to_the_requested_interaction(configured_db: str):
    other_ix = "ix-ev-other"
    with psycopg.connect(configured_db) as conn:
        _insert_interaction(conn)
        _insert_interaction(conn, interaction_id=other_ix)
        _insert_span(conn, span_id="s-mine")
        _insert_span(conn, interaction_id=other_ix, span_id="s-other")
        conn.commit()

    evidence = gather_evidence(_IX_ID)
    assert evidence.span_ids == ["s-mine"]


# --- classification join ------------------------------------------------------


def test_leg_with_no_payload_has_no_payload_marker(configured_db: str):
    with psycopg.connect(configured_db) as conn:
        _insert_interaction(conn)
        _insert_leg(conn, leg_type="request", payload_hash=None)
        conn.commit()

    evidence = gather_evidence(_IX_ID)
    assert evidence.classifications["request"] is utils.NO_PAYLOAD


def test_leg_with_unclassified_payload_is_pending(configured_db: str):
    with psycopg.connect(configured_db) as conn:
        _insert_interaction(conn)
        _insert_leg(conn, leg_type="request", payload_hash="reqhash")
        conn.commit()

    evidence = gather_evidence(_IX_ID)
    assert evidence.classifications["request"] is utils.PENDING


def test_leg_with_classified_payload_joins_verdict(configured_db: str):
    with psycopg.connect(configured_db) as conn:
        _insert_interaction(conn)
        _insert_leg(conn, leg_type="request", payload_hash="reqhash")
        _insert_classification(
            conn,
            content_hash="reqhash",
            sensitivity_level="RESTRICTED",
            regulatory_tags=["PII"],
            primary_domain="finance",
            findings=[{"entity_type": "SSN"}],
            model_version=2,
        )
        conn.commit()

    evidence = gather_evidence(_IX_ID)
    verdict = evidence.classifications["request"]
    assert verdict.sensitivity_level == "RESTRICTED"
    assert verdict.regulatory_tags == ["PII"]
    assert verdict.primary_domain == "finance"
    assert verdict.findings == [{"entity_type": "SSN"}]
    assert verdict.model_version == 2


def test_missing_leg_is_absent_from_classifications(configured_db: str):
    """A leg that doesn't exist at all has no key in classifications —
    distinct from NO_PAYLOAD (leg exists, no payload)."""
    with psycopg.connect(configured_db) as conn:
        _insert_interaction(conn)
        _insert_leg(conn, leg_type="request", payload_hash="reqhash")
        conn.commit()

    evidence = gather_evidence(_IX_ID)
    assert "response" not in evidence.classifications


def test_mixed_legs_classification_summary(configured_db: str):
    """Request classified, response pending — both keyed independently."""
    with psycopg.connect(configured_db) as conn:
        _insert_interaction(conn)
        _insert_leg(conn, leg_type="request", payload_hash="reqhash")
        _insert_leg(conn, leg_type="response", payload_hash="resphash", error=True)
        _insert_classification(conn, content_hash="reqhash")
        conn.commit()

    evidence = gather_evidence(_IX_ID)
    assert evidence.classifications["request"] is not utils.PENDING
    assert evidence.classifications["request"].sensitivity_level == "RESTRICTED"
    assert evidence.classifications["response"] is utils.PENDING
