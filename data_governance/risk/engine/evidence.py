"""Evidence gathering — the DB-reading I/O shell for the interaction risk
computation engine (issue #101).

Reads everything :mod:`data_governance.risk.engine.utils`'s pure
functions need for one interaction: its identity row, its legs (in ``seq``
order — the same cursor order ``leg_ready/driver.py`` drains in), the span
set evidencing it, and — per leg with a payload — the joined classification
verdict from ``payload_classifications`` (keyed by ``content_hash``, mirroring
the existing ``leg_ready/driver.py`` join on ``payload_hash``).

Deliberately returns :class:`utils.LegEvidence` / reuses
:class:`utils.PENDING` / :class:`utils.NO_PAYLOAD` rather than
inventing a parallel shape — this module's whole job is to produce exactly
what ``utils.py`` already consumes.
"""

from __future__ import annotations

import dataclasses

from data_governance import db
from data_governance.processors.classification.verdict import Verdict
from data_governance.risk import config
from data_governance.risk.engine import labels, utils

__all__ = ["Evidence", "InteractionNotFoundError", "gather_evidence"]


class InteractionNotFoundError(Exception):
    """No ``interactions`` row exists for the requested id."""


@dataclasses.dataclass(frozen=True)
class Evidence:
    """Everything gathered for one interaction, ready to feed
    :mod:`utils`'s pure functions."""

    interaction_id: str
    trace_id: str
    caller_entity_id: str | None
    callee_entity_id: str | None
    legs: list[utils.LegEvidence]
    span_ids: list[str]
    classifications: dict[str, Verdict | object]
    # Issue #163 additions, defaulted and last so pre-existing construction
    # sites (and tests) remain valid.
    parent_interaction_id: str | None = None
    anchor: utils.AnchorFacts | None = None
    # The label-lineage edges into each leg (RISK_INHERITED_LABELS=on); None
    # when off, so nothing downstream changes shape.
    inherited: labels.Inherited | None = None


_INTERACTION_SQL = (
    "SELECT id, trace_id, caller_entity_id, callee_entity_id, "
    "parent_interaction_id "
    "FROM interactions WHERE id = %s"
)

# The interaction's anchor (request) span, as the sidecar interactions
# algorithm assigns it (interaction_spans.role = 'anchor'; exactly one per
# sidecar-derived interaction). Its attributes carry the wire facts issue
# #163 feeds into the OPA input: destination host, scheme/path, direction,
# validated principal. Ordered by seq for determinism should a non-sidecar
# derivation ever write more than one anchor row.
_ANCHOR_SQL = (
    "SELECT s.attributes "
    "FROM interaction_spans isp "
    "JOIN spans s ON s.trace_id = isp.trace_id AND s.span_id = isp.span_id "
    "WHERE isp.interaction_id = %s AND isp.role = 'anchor' "
    "ORDER BY s.seq ASC LIMIT 1"
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


def _fetch_legs(tx: db.Transaction, interaction_id: str) -> list[utils.LegEvidence]:
    rows = tx.fetch_all(_LEGS_SQL, (interaction_id,))
    return [
        utils.LegEvidence(leg_type=r[0], payload_hash=r[1]) for r in rows
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


def _str_or_none(value: object) -> str | None:
    """A span attribute as a non-empty string, else ``None`` — a blank or
    non-string value is an absent fact, never a fact of its own."""
    if isinstance(value, str) and value:
        return value
    return None


def _fetch_anchor(tx: db.Transaction, interaction_id: str) -> utils.AnchorFacts | None:
    """The wire facts of the interaction's anchor (request) span, or ``None``
    when the interaction has no anchor span or the anchor carries none of the
    sidecar's ``lineage.*`` facts (a non-sidecar derivation) — honest
    absence, mirroring the classification sentinels."""
    row = tx.fetch_one(_ANCHOR_SQL, (interaction_id,))
    if row is None:
        return None
    attributes = row[0] or {}
    facts = utils.AnchorFacts(
        direction=_str_or_none(attributes.get("lineage.direction")),
        peer_host=_str_or_none(attributes.get("lineage.peer.host")),
        self_id=_str_or_none(attributes.get("lineage.self.id")),
        url_scheme=_str_or_none(attributes.get("url.scheme")),
        url_path=_str_or_none(attributes.get("url.path")),
        principal_sub=_str_or_none(attributes.get("lineage.principal.sub")),
    )
    if facts == utils.AnchorFacts():
        return None
    return facts


# Each leg's edges are pinned to the payload the leg carries NOW: an edge derived
# from a payload the leg was rewritten away from must not reach OPA (the recompute
# arm has no readiness predicate to hold it back).
_INHERITED_SQL = (
    "SELECT e.to_leg_type::text, e.trace_id, e.entity_type, e.regulatory_tags, e.sensitivity_level, "
    "e.match_kind, e.matched_text, e.matched_start, e.matched_end, e.recombined, e.origin_trace_id "
    "FROM payload_label_edges e "
    "JOIN unnest(%s::text[], %s::text[]) AS cur(leg_type, payload_hash) "
    "  ON cur.leg_type = e.to_leg_type::text AND cur.payload_hash = e.to_payload_hash "
    "WHERE e.to_interaction_id = %s "
    "ORDER BY e.to_leg_type, e.matched_start NULLS LAST, e.entity_type, e.edge_id"
)


def _fetch_inherited(
    tx: db.Transaction, interaction_id: str, legs: list[utils.LegEvidence], mode: str, cross: bool = False
) -> labels.Inherited:
    """The label-lineage edges into the interaction's legs at their current
    payloads — one indexed read, made only with the flag on."""
    current = [leg for leg in legs if leg.payload_hash is not None]
    by_leg: dict[str, list[labels.InheritedEdge]] = {}
    rows = tx.fetch_all(_INHERITED_SQL, ([leg.leg_type for leg in current],
                                         [leg.payload_hash for leg in current], interaction_id))
    for r in rows:
        by_leg.setdefault(r[0], []).append(labels.InheritedEdge(
            trace_id=r[1], entity_type=r[2], regulatory_tags=tuple(r[3] or ()), sensitivity_level=r[4],
            match_kind=r[5], matched_text=r[6], matched_start=r[7], matched_end=r[8],
            recombined=bool(r[9]), origin_trace_id=r[10],
        ))
    return labels.Inherited(mode=mode, by_leg=by_leg, cross=cross)


def _gather_classifications(
    tx: db.Transaction, legs: list[utils.LegEvidence]
) -> dict[str, Verdict | object]:
    classifications: dict[str, Verdict | object] = {}
    for leg in legs:
        if leg.payload_hash is None:
            classifications[leg.leg_type] = utils.NO_PAYLOAD
            continue
        verdict = _fetch_classification(tx, leg.payload_hash)
        classifications[leg.leg_type] = (
            verdict if verdict is not None else utils.PENDING
        )
    return classifications


def gather_evidence(interaction_id: str) -> Evidence:
    """Read everything the engine needs for *interaction_id*.

    Raises :class:`InteractionNotFoundError` if no ``interactions`` row
    exists for this id — callers must not aggregate/write against evidence
    that doesn't identify a real interaction.
    """
    mode = config.inherited_labels_mode()
    cross = config.inherited_cross_conversations()
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
            parent_interaction_id=row[4],
            legs=legs,
            span_ids=_fetch_span_ids(tx, interaction_id),
            classifications=_gather_classifications(tx, legs),
            anchor=_fetch_anchor(tx, interaction_id),
            inherited=(
                _fetch_inherited(tx, interaction_id, legs, mode, cross) if mode != "off" else None
            ),
        )
