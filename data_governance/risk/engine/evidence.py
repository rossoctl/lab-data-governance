"""Evidence gathering — the DB-reading I/O shell for the interaction risk
computation engine (issue #101).

Reads everything :mod:`data_governance.risk.engine.aggregate`'s pure
functions need for one interaction: its identity row, its legs (in ``seq``
order — the same cursor order ``leg_ready/driver.py`` drains in), the span
set evidencing it, and — per leg with a payload — the joined classification
verdict from ``payload_classifications`` (keyed by ``content_hash``, mirroring
the existing ``leg_ready/driver.py`` join on ``payload_hash``).

Deliberately returns :class:`aggregate.LegEvidence` / reuses
:class:`aggregate.PENDING` / :class:`aggregate.NO_PAYLOAD` rather than
inventing a parallel shape — this module's whole job is to produce exactly
what ``aggregate.py`` already consumes.
"""

from __future__ import annotations

import dataclasses

from data_governance import db
from data_governance.processors.classification.verdict import Verdict
from data_governance.risk.engine import aggregate

__all__ = ["Evidence", "InteractionNotFoundError", "gather_evidence"]


class InteractionNotFoundError(Exception):
    """No ``interactions`` row exists for the requested id."""


@dataclasses.dataclass(frozen=True)
class Evidence:
    """Everything gathered for one interaction, ready to feed
    :mod:`aggregate`'s pure functions."""

    interaction_id: str
    trace_id: str
    caller_entity_id: str | None
    callee_entity_id: str | None
    legs: list[aggregate.LegEvidence]
    span_ids: list[str]
    classifications: dict[str, Verdict | object]


_INTERACTION_SQL = (
    "SELECT id, trace_id, caller_entity_id, callee_entity_id "
    "FROM interactions WHERE id = %s"
)

_LEGS_SQL = (
    "SELECT leg_type::text, payload_hash, error "
    "FROM interaction_legs WHERE interaction_id = %s ORDER BY seq ASC"
)

_SPAN_IDS_SQL = "SELECT span_id FROM interaction_spans WHERE interaction_id = %s"

_CLASSIFICATION_SQL = (
    "SELECT sensitivity_level, regulatory_tags, contains_identity_bundle, "
    "is_personalized, primary_domain, findings, model_version "
    "FROM payload_classifications WHERE content_hash = %s"
)


def _fetch_legs(tx: db.Transaction, interaction_id: str) -> list[aggregate.LegEvidence]:
    rows = tx.fetch_all(_LEGS_SQL, (interaction_id,))
    return [
        aggregate.LegEvidence(leg_type=r[0], payload_hash=r[1]) for r in rows
    ]


def _fetch_span_ids(tx: db.Transaction, interaction_id: str) -> list[str]:
    rows = tx.fetch_all(_SPAN_IDS_SQL, (interaction_id,))
    return [r[0] for r in rows]


def _fetch_classification(tx: db.Transaction, content_hash: str) -> Verdict | None:
    row = tx.fetch_one(_CLASSIFICATION_SQL, (content_hash,))
    if row is None:
        return None
    return Verdict(
        sensitivity_level=row[0],
        regulatory_tags=list(row[1] or []),
        contains_identity_bundle=row[2],
        is_personalized=row[3],
        primary_domain=row[4],
        findings=list(row[5] or []),
        model_version=row[6],
    )


def _gather_classifications(
    tx: db.Transaction, legs: list[aggregate.LegEvidence]
) -> dict[str, Verdict | object]:
    classifications: dict[str, Verdict | object] = {}
    for leg in legs:
        if leg.payload_hash is None:
            classifications[leg.leg_type] = aggregate.NO_PAYLOAD
            continue
        verdict = _fetch_classification(tx, leg.payload_hash)
        classifications[leg.leg_type] = (
            verdict if verdict is not None else aggregate.PENDING
        )
    return classifications


def gather_evidence(interaction_id: str) -> Evidence:
    """Read everything the engine needs for *interaction_id*.

    Raises :class:`InteractionNotFoundError` if no ``interactions`` row
    exists for this id — callers must not aggregate/write against evidence
    that doesn't identify a real interaction.
    """
    with db.transaction() as tx:
        row = tx.fetch_one(_INTERACTION_SQL, (interaction_id,))
        if row is None:
            raise InteractionNotFoundError(
                f"no interaction found for id {interaction_id!r}"
            )
        legs = _fetch_legs(tx, interaction_id)
        return Evidence(
            interaction_id=row[0],
            trace_id=row[1],
            caller_entity_id=row[2],
            callee_entity_id=row[3],
            legs=legs,
            span_ids=_fetch_span_ids(tx, interaction_id),
            classifications=_gather_classifications(tx, legs),
        )
