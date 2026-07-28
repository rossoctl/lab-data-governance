"""The semantic-matching contract — two payloads in, a match verdict out (issue #116).

The matcher is the black box lineage is built on: ``match(payload_a, payload_b) ->
{matched, transformation, ...evidence}`` (``docs/data_lineage_alg.md`` "Semantic
matching"). These tests pin the *contract* — the shape lineage will depend on —
independently of any implementation, because ADR-0027 makes matching its own
component with its own roadmap and lineage must not know how it decides.

Pure: no database, no network, no lineage import.
"""

from __future__ import annotations

import dataclasses

from data_governance.matching import MatchResult, Matcher, Transformation


def test_result_carries_matched_transformation_and_evidence() -> None:
    """The spec's return shape: a ``matched`` flag, an optional ``transformation``,
    and free-form ``evidence`` (the ``...evidence`` tail of the signature)."""
    result = MatchResult(
        matched=True,
        transformation=Transformation.SUMMARIZATION,
        evidence={"overlap": 0.8},
    )

    assert result.matched is True
    assert result.transformation is Transformation.SUMMARIZATION
    assert result.evidence == {"overlap": 0.8}


def test_transformation_and_evidence_are_optional() -> None:
    """``matched`` is the only thing a matcher must decide. A matcher that found a
    relationship but identified no transformation (spec: ``null`` — "no transform
    was performed or none was identified") and offers no evidence constructs a
    result from ``matched`` alone."""
    result = MatchResult(matched=True)

    assert result.matched is True
    assert result.transformation is None
    assert result.evidence is None


def test_result_is_immutable() -> None:
    """A verdict is a value, not a mutable record — lineage reads it, never
    edits it (matching's decisions stay matching's)."""
    result = MatchResult(matched=True)

    assert dataclasses.is_dataclass(result)
    try:
        result.matched = False  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        pass
    else:  # pragma: no cover — only reached if the dataclass stops being frozen
        raise AssertionError("MatchResult must be frozen")


def test_transformation_carries_the_values_the_spec_names() -> None:
    """The spec names exactly two transformations — ``summarization`` (summary
    performed, semantics intact) and ``anonymization`` (elements removed/anonymized,
    losing the link to a specific person). The no-transformation case is ``None``,
    not a member, so "no transform" is unrepresentable as a transformation value.

    The enumeration is still being finalized with a human (ADR-0027 "Deliberately
    out of scope"), so this pins the named values without asserting the list is
    closed."""
    assert Transformation.SUMMARIZATION.value == "summarization"
    assert Transformation.ANONYMIZATION.value == "anonymization"
    assert {t.value for t in Transformation} >= {"summarization", "anonymization"}


def test_transformation_is_a_string_enum_so_new_values_extend_it() -> None:
    """Extensible, not closed: transformations compare and serialize as plain
    strings, so a value added when the human finalizes the list (masking,
    redaction, …) needs no change at the persistence or API boundary."""
    assert Transformation.SUMMARIZATION == "summarization"
    assert str(Transformation.ANONYMIZATION) == "anonymization"
    assert Transformation("summarization") is Transformation.SUMMARIZATION


def test_any_two_payload_callable_satisfies_the_matcher_seam() -> None:
    """The seam is structural: anything shaped ``(payload_a, payload_b) ->
    MatchResult`` is a :class:`Matcher`. That is what lets a real matcher
    (value-based, confidential-aware) swap in without touching call sites."""

    def value_matcher(payload_a: object, payload_b: object) -> MatchResult:
        return MatchResult(matched=payload_a == payload_b)

    matcher: Matcher = value_matcher
    assert matcher("x", "x").matched is True
    assert matcher("x", "y").matched is False
