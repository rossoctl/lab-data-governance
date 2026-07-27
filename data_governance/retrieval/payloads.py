"""Retrieval submodule — the content-addressed **Payload** read, inlining the
**Classification** verdict.

Part of the :mod:`data_governance.retrieval` package. Unlike **Interaction
retrieval** (trace-scoped, eventually consistent), this read is keyed on a
``content_hash`` and its target is write-once and cross-trace: a body referenced
by many interactions or traces is stored — and classified — exactly once.

``get_payload`` LEFT JOINs ``payload_classifications`` so the **Classification**
verdict rides along inline (ADR-0024): ``None`` while the payload exists but
P-classification has not yet written its verdict (the eventual-consistency
window — null means *exactly* "not yet processed", never "processed but
skipped"), the verdict object once the row lands. A missing payload is ``None``
for the whole read (the REST layer maps that to 404).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from data_governance import db

__all__ = ["ClassificationView", "PayloadView", "get_payload"]


@dataclass(frozen=True)
class ClassificationView:
    """The P-classification verdict for one **Payload** (ADR-0024).

    Document-level fields plus the **Findings** inline (served verbatim, keyed
    under ``entity_type`` per CONTEXT.md) and the monotonic ``model_version``.
    """

    sensitivity_level: str
    regulatory_tags: list[str]
    contains_identity_bundle: bool
    is_personalized: bool
    primary_domain: str | None
    findings: Any
    model_version: int


@dataclass(frozen=True)
class PayloadView:
    """A content-addressed **Payload** with its inline nullable
    **Classification**. ``classification`` is ``None`` in the
    eventual-consistency window before P-classification has run.
    """

    content_hash: str
    content_kind: str
    content: Any
    byte_size: int
    classification: ClassificationView | None


def _classification_view(row: tuple) -> ClassificationView | None:
    """Shape a ``payload_classifications`` LEFT JOIN slice into the nullable
    verdict (ADR-0024).

    The join columns are all-NULL when P-classification has not yet written a
    verdict for the payload (its PK ``content_hash`` is NOT NULL, so a present
    row always has one) — that is the eventual-consistency window, surfaced as
    ``None``.
    """
    if row[0] is None:  # no payload_classifications row (LEFT JOIN miss)
        return None
    return ClassificationView(
        sensitivity_level=row[0],
        regulatory_tags=list(row[1]) if row[1] is not None else [],
        contains_identity_bundle=row[2],
        is_personalized=row[3],
        primary_domain=row[4],
        findings=row[5],
        model_version=row[6],
    )


def get_payload(content_hash: str) -> PayloadView | None:
    """Read a **Payload** by content hash, with its inline **Classification**
    verdict. ``None`` when no payload with that hash exists.
    """
    with db.transaction() as tx:
        row = tx.fetch_one(
            "SELECT p.content_hash, p.content_kind, p.content, p.byte_size, "
            "       c.sensitivity_level, c.regulatory_tags, "
            "       c.contains_identity_bundle, c.is_personalized, "
            "       c.primary_domain, c.findings, c.model_version "
            "FROM interaction_payloads p "
            "LEFT JOIN payload_classifications c "
            "  ON c.content_hash = p.content_hash "
            "WHERE p.content_hash = %s",
            (content_hash,),
        )
        if row is None:
            return None
        return PayloadView(
            content_hash=row[0],
            content_kind=row[1],
            content=row[2],
            byte_size=row[3],
            classification=_classification_view(row[4:]),
        )
