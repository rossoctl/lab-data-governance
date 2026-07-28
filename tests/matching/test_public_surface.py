"""Only the contract is exported (issue #116).

Lineage must not know how matching decides — that boundary is the point of this
component (ADR-0027: matching is its own component with its own roadmap). So the
package's public surface is the contract and the selection entry point; a matcher's
internals are not part of it, and nothing may depend on them.

Pure: no database, no network, no lineage import.
"""

from __future__ import annotations

import data_governance.matching as matching


def test_the_public_surface_is_exactly_the_contract() -> None:
    """``__all__`` is the whole agreement: the verdict type, the transformation
    enumeration, the seam's type, the selection entry point + its env var, and the
    trivial default. Adding to this list is a deliberate act."""
    assert set(matching.__all__) == {
        "MATCHER_ENV_VAR",
        "MatchResult",
        "Matcher",
        "Transformation",
        "UnknownMatcher",
        "get_matcher",
        "simple_match",
    }


def test_no_matcher_internals_leak_into_the_public_surface() -> None:
    """The registry and the implementation modules are reachable only by explicitly
    importing private names — never from the package's exports. A consumer that
    stays within ``__all__`` cannot couple itself to a matcher implementation."""
    assert not [name for name in matching.__all__ if name.startswith("_")]
    assert "_MATCHERS" not in vars(matching)


def test_matching_does_not_import_lineage_or_the_database() -> None:
    """Pure and standalone: importing the matcher pulls in no lineage module and no
    database driver. Matching is upstream of lineage, and (unlike the rest of the
    pipeline) needs no I/O at all."""
    import importlib
    import sys

    for name in ("psycopg", "data_governance.db"):
        sys.modules.pop(name, None)

    importlib.reload(matching)

    assert "psycopg" not in sys.modules, "importing matching pulled in the DB driver"
    assert "data_governance.db" not in sys.modules, "matching depends on the DB"
    assert not [n for n in sys.modules if "lineage" in n], "matching depends on lineage"
