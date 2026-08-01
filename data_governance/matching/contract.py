"""The semantic-matching contract — the boundary lineage depends on (issue #116).

The spec (``docs/data_lineage_alg.md``, "Semantic matching") gives one signature::

    match(payload_a, payload_b) -> {matched: bool, transformation: enum, ...evidence}

- ``matched`` — ``True``: the payloads are related, so we believe there is lineage.
  ``False``: no relationship, so we believe there is no lineage — that input
  contributes nothing, and if *every* input of a ``merge_lineage`` reports ``False``
  the op degrades to ``init_lineage`` (ADR-0028 D3(2)).
- ``transformation`` — reported only when matched: ``None`` if no transform was
  performed or none was identified, otherwise which one.
- ``evidence`` — the spec's open ``...evidence`` tail: whatever the matcher wants to
  say about *why*. It exists so a real matcher can explain itself; the contract
  makes no claim about its contents and lineage does not read it.

This module is the whole agreement. Lineage depends on these types and on
:func:`~data_governance.matching.config.get_matcher` — never on a matcher
implementation, because matching is its own component with its own roadmap
(ADR-0028: "Lineage does **not** know how it decides").
"""

from __future__ import annotations

import dataclasses
import enum
from typing import Any, Protocol, runtime_checkable

# A payload is opaque to the contract. The seam takes whatever the caller holds —
# the stored JSONB ``content``, projected text, a dict — because *which* aspect of a
# payload matters is a matcher's business, not the contract's. The trivial default
# looks at neither.
Payload = Any


class Transformation(enum.StrEnum):
    """What transformation connects two matched payloads.

    The spec names exactly two (``docs/data_lineage_alg.md``); "no transform was
    performed or none was identified" is ``None`` rather than a member, so an
    absent transformation cannot masquerade as a kind of transformation.

    Deliberately **open**: the enumeration is still being finalized with a human
    (spec "transformation: to do with a human: work on this list"; ADR-0028
    "Deliberately out of scope"), so further values (masking, redaction, …) are
    expected here. It is a :class:`enum.StrEnum` for that reason — members compare
    and serialize as their plain lowercase string, so adding one is additive at the
    persistence and API boundaries rather than a migration of a closed type. Do not
    write logic that assumes this list is complete.
    """

    ANONYMIZATION = "anonymization"
    """Elements were removed or anonymized, losing the ability to relate the
    information to a specific person."""

    SUMMARIZATION = "summarization"
    """A summary was performed and the semantics are still intact."""


@dataclasses.dataclass(frozen=True, slots=True)
class MatchResult:
    """One matcher verdict over a payload pair — the spec's return shape.

    Frozen: a verdict is a value that lineage reads and never edits (matching's
    decisions stay matching's). ``transformation`` and ``evidence`` default to
    ``None`` so the trivial default's ``{true, null, null}`` is the natural
    construction, and so a matcher only states what it actually determined.

    ``transformation`` is meaningful only when ``matched`` is true — with no
    relationship there is nothing to have transformed.
    """

    matched: bool
    transformation: Transformation | None = None
    evidence: Any | None = None


@runtime_checkable
class Matcher(Protocol):
    """The seam: two payloads in, a :class:`MatchResult` out.

    Structural, and a bare callable rather than a class, because the spec's contract
    is a function — any ``(payload_a, payload_b) -> MatchResult`` callable is a
    matcher. Call sites depend on this shape alone, which is what lets a real
    matcher (value-based, confidential-aware) replace the trivial default as a
    configuration change (ADR-0028).
    """

    def __call__(self, payload_a: Payload, payload_b: Payload, /) -> MatchResult:
        """Decide whether *payload_a* and *payload_b* are related, and how."""
        ...
