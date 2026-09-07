"""Compile the shipped rule catalog into the Rego bundle OPA serves (issue #173).

This is the "triggered by a new policy JSON being created" entry point
agreed at the compiler-library scope for this issue (no ``policies`` table,
no migration, no NOTIFY processor — see ``data_governance.risk.rules.rego``'s
module docstring): a caller recompiles on demand by calling :func:`reload`
then :func:`compile_bundle` again. A future issue may wire this to a runtime
"policy created" event; that wiring is out of scope here.

Run as a module (``python -m data_governance.risk.rules.compile``) it writes
the compiled bundle to stdout — the one path from repo to cluster:
``deploy/create-opa-configmap.sh`` pipes it into the ``opa-policy`` ConfigMap
the OPA server (``deploy/k8s/95-opa.yaml``, issue #162) loads at startup.
"""

from __future__ import annotations

import functools
import sys

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
    return compile_policy(policy)


def reload() -> None:
    """Clear both this module's compiled-bundle cache and
    :mod:`catalog`'s raw-JSON cache, so the next :func:`compile_bundle` call
    re-reads ``rules_source.json`` from disk and recompiles it — clearing
    only one of the two caches would let the other serve stale data."""
    compile_bundle.cache_clear()
    catalog.reload()


if __name__ == "__main__":
    sys.stdout.write(compile_bundle())
