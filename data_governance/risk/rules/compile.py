"""Compile the shipped rule catalog into the Rego bundle OPA serves (issue #173).

This is the "triggered by a new policy JSON being created" entry point
agreed at the compiler-library scope for this issue (no ``policies`` table,
no migration, no NOTIFY processor — see ``data_governance.risk.rules.rego``'s
module docstring): a caller — today, a human running
``python -c "from data_governance.risk.rules import compile;
print(compile.compile_bundle())"`` and PUTting the result at OPA's
``/v1/policies/data_governance`` — recompiles on demand by calling
:func:`reload` then :func:`compile_bundle` again. A future issue may wire
this to a runtime "policy created" event; that wiring is out of scope here.
"""

from __future__ import annotations

import functools

from data_governance.risk.config import POLICY_RULE_COMBINING_MODE
from data_governance.risk.rules import catalog
from data_governance.risk.rules.rego import compile_policy

__all__ = ["compile_bundle", "reload"]


@functools.lru_cache(maxsize=1)
def compile_bundle() -> str:
    """The shipped catalog (``catalog.load_rules_source()``) compiled to
    Rego, memoized for the process lifetime like
    :func:`catalog.load_rules_source` itself. Call :func:`reload` after the
    catalog file changes on disk so the next call recompiles from the fresh
    JSON rather than serving a stale bundle."""
    policy = catalog.load_rules_source()
    return compile_policy(policy, default_mode=POLICY_RULE_COMBINING_MODE)


def reload() -> None:
    """Clear both this module's compiled-bundle cache and
    :mod:`catalog`'s raw-JSON cache, so the next :func:`compile_bundle` call
    re-reads ``rules_source.json`` from disk and recompiles it — clearing
    only one of the two caches would let the other serve stale data."""
    compile_bundle.cache_clear()
    catalog.reload()
