"""The trivial default matcher (issue #116).

The spec's default implementation, verbatim::

    simple_match(payload_a, payload_b) -> { true, null, null }

"Returning to always without an analysis" — always matched, no transformation, no
evidence. With it, lineage is **complete but full of maybes**: every structural edge
in the interaction graph is treated as real data flow (ADR-0027). That is a
deliberate trade — it makes lineage computable today, and better matchers prune the
maybes later without any change to the lineage algorithm.
"""

from __future__ import annotations

from .contract import MatchResult, Payload

# The verdict is a constant: no analysis is performed, so every call returns the
# same immutable value. Building it once makes "does not inspect the payloads"
# structural rather than a promise in a comment.
_ALWAYS_MATCHED = MatchResult(matched=True, transformation=None, evidence=None)


def simple_match(payload_a: Payload, payload_b: Payload, /) -> MatchResult:
    """Report the payloads as related, with no transformation and no evidence.

    The parameters are accepted and **deliberately not read** — not their values,
    types, or presence. Anything a real matcher would do (comparing values,
    embedding, NER) belongs in a different matcher behind this same seam, selected
    by configuration.
    """
    return _ALWAYS_MATCHED
