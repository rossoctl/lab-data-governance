"""The trivial default matcher — ``simple_match`` (issue #116).

The spec's default implementation: ``simple_match(payload_a, payload_b) -> {true,
null, null}`` — always matched, no transformation, no evidence, "returning to
always without an analysis" (``docs/data_lineage_alg.md``). This is what makes
lineage computable today: complete but full of maybes, since every structural edge
is treated as real flow (ADR-0027). Better matchers prune the maybes later without
changing the lineage algorithm.

Pure: no database, no network, no lineage import.
"""

from __future__ import annotations

from data_governance.matching import MatchResult, Matcher, simple_match


def test_simple_match_always_matches_with_no_transformation() -> None:
    """The spec's ``{true, null, null}``: matched, no transformation identified,
    no evidence."""
    result = simple_match("a payload", "a different payload")

    assert result == MatchResult(matched=True, transformation=None, evidence=None)


def test_simple_match_matches_regardless_of_the_payloads() -> None:
    """Always matched — identical, unrelated, empty, or absent payloads all get
    the same verdict, because no analysis is performed."""
    for payload_a, payload_b in (
        ("same", "same"),
        ("apples", "battleships"),
        ("", ""),
        (None, None),
        ({"role": "user"}, ["not even the same type"]),
    ):
        assert simple_match(payload_a, payload_b).matched is True


def test_simple_match_does_not_inspect_payload_content() -> None:
    """"Without an analysis" is a behavioural claim, not a comment: payloads whose
    every access raises would break any matcher that peeked. The trivial default
    must return its verdict having touched neither payload."""

    class Landmine:
        """Explodes on any attribute access, comparison, iteration, or coercion."""

        def __getattr__(self, name: str) -> object:
            raise AssertionError(f"simple_match inspected the payload (.{name})")

        def __eq__(self, other: object) -> bool:
            raise AssertionError("simple_match compared the payloads")

        def __hash__(self) -> int:
            raise AssertionError("simple_match hashed the payload")

        def __iter__(self) -> object:
            raise AssertionError("simple_match iterated the payload")

        def __len__(self) -> int:
            raise AssertionError("simple_match measured the payload")

        def __str__(self) -> str:
            raise AssertionError("simple_match stringified the payload")

        def __repr__(self) -> str:
            raise AssertionError("simple_match repr'd the payload")

    result = simple_match(Landmine(), Landmine())

    assert result.matched is True
    assert result.transformation is None
    assert result.evidence is None


def test_simple_match_satisfies_the_matcher_seam() -> None:
    """The default sits behind the same seam as any real matcher, so swapping is a
    configuration change, not a call-site change."""
    matcher: Matcher = simple_match

    assert matcher("in", "out").matched is True
