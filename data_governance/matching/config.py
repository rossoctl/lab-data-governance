"""Selecting the active matcher by configuration (issue #116).

Lineage calls :func:`get_matcher` and never names an implementation, so shipping a
real matcher is a ``SEMANTIC_MATCHER`` change plus one registry entry — not an edit
at any call site. ADR-0028 requires exactly that separation: matching is its own
component, and lineage quality is bounded by matcher quality without lineage
knowing which matcher ran.

Follows the repo's env-var convention (``INTERACTIONS_ALGORITHM`` in
``processors/interactions/__main__.py``): a name → callable registry, one explicit
default, and a hard failure on an unrecognized name.

Matcher **versioning** and re-derivation of already-persisted lineage when the
matcher changes are explicitly deferred (ADR-0028 D7), so nothing here stamps or
records which matcher produced a verdict.
"""

from __future__ import annotations

import os

from .contract import Matcher
from .simple import simple_match

# The environment variable naming the active matcher.
MATCHER_ENV_VAR = "SEMANTIC_MATCHER"

# The registry: matcher name → the callable behind the seam. Private — consumers go
# through `get_matcher`, so nothing outside this module can bind to a specific
# implementation. A real matcher lands as one entry here.
_MATCHERS: dict[str, Matcher] = {"simple": simple_match}

# The default when the environment says nothing: the trivial matcher, which is what
# makes lineage computable before any real matcher exists (ADR-0028).
_DEFAULT_MATCHER = "simple"


class UnknownMatcher(ValueError):
    """The configured matcher name is not registered.

    Raised rather than silently defaulting: falling back to the trivial matcher on a
    typo'd name would emit maybe-everywhere lineage while the operator believed a
    real matcher was running — a governance tool must not quietly overstate flow.
    """


def get_matcher(name: str | None = None) -> Matcher:
    """Return the active matcher.

    *name* selects explicitly (for a CLI or a test); by default the name comes from
    ``SEMANTIC_MATCHER``, and an unset or blank value means the trivial default.
    Raises :class:`UnknownMatcher` for a name that is not registered.
    """
    if name is None:
        name = os.environ.get(MATCHER_ENV_VAR, "")
    name = name.strip() or _DEFAULT_MATCHER

    try:
        return _MATCHERS[name]
    except KeyError:
        raise UnknownMatcher(
            f"{MATCHER_ENV_VAR} must be one of {sorted(_MATCHERS)}; got {name!r}"
        ) from None
