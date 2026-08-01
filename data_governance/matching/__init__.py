"""Semantic matching — the black box data lineage is built on (issue #116).

Matching answers one question about two payloads: are they related, and what
transformation connects them?

    match(payload_a, payload_b) -> {matched, transformation, ...evidence}

Lineage is computed *on top of* this and does **not** know how the decision is made
(ADR-0028: matching is deliberately out of scope of lineage — its own component with
its own roadmap). Hence this package sits beside ``processors``/``retrieval`` rather
than inside a lineage module, and hence the whole public surface is the contract:

- :class:`~.contract.MatchResult` / :class:`~.contract.Transformation` /
  :class:`~.contract.Matcher` — the verdict, the transformation enumeration (open;
  still being finalized with a human), and the seam's shape.
- :func:`~.config.get_matcher` — which matcher is active, chosen by
  ``SEMANTIC_MATCHER``. Call sites resolve the matcher through this, so swapping in a
  real one is a configuration change.
- :func:`~.simple.simple_match` — the trivial default: always matched, no
  transformation, no evidence. Lineage under it is **complete but full of maybes**
  (every structural edge is treated as real flow); better matchers (value-based,
  confidential-aware) prune the maybes without touching the lineage algorithm.

Pure: no database, no network, no I/O, and no dependency on lineage. Matcher
versioning and re-derivation of persisted lineage after a matcher change are
deferred (ADR-0028 D7).
"""

from __future__ import annotations

from .config import MATCHER_ENV_VAR, UnknownMatcher, get_matcher
from .contract import Matcher, MatchResult, Transformation
from .simple import simple_match

# The entire agreement. Matcher internals (the registry, the implementation modules)
# are not exported, so nothing can come to depend on how any matcher decides.
__all__ = [
    "MATCHER_ENV_VAR",
    "MatchResult",
    "Matcher",
    "Transformation",
    "UnknownMatcher",
    "get_matcher",
    "simple_match",
]
