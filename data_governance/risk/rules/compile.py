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
import json
import os
from pathlib import Path

from data_governance.risk.rules import catalog
from data_governance.risk.rules.rego import compile_policy

__all__ = ["compile_bundle", "reload"]


@functools.lru_cache(maxsize=1)
def _shipped_bundle() -> str:
    """The shipped catalog compiled to Rego, memoized for the process
    lifetime like :func:`catalog.load_rules_source` itself."""
    return compile_policy(catalog.load_rules_source())


def compile_bundle(path: str | os.PathLike[str] | None = None) -> str:
    """Compile a rule catalog to the Rego module OPA serves.

    With no *path* (the production default) this is the shipped catalog
    (``catalog.load_rules_source()``), memoized for the process lifetime;
    call :func:`reload` after the catalog file changes on disk so the next
    call recompiles from the fresh JSON rather than serving a stale bundle.

    With a *path*, the JSON document at that path is loaded, validated
    against ``schema/policy.schema.json`` and compiled — never memoized, so
    two calls with different files never share a result. This is the one
    sanctioned way to put a catalog other than the shipped one in front of
    OPA (``deploy/create-opa-configmap.sh [catalog.json]``): the same
    compiler, the same schema check, the same ConfigMap path, so a test or
    a staging catalog can never reach the cluster by a route production
    rules do not take. Raises ``FileNotFoundError`` for a missing file and
    ``jsonschema.ValidationError`` for a document that does not conform.
    """
    if path is None:
        return _shipped_bundle()
    with Path(path).open(encoding="utf-8") as f:
        policy = json.load(f)
    return compile_policy(policy, validate=True)


def reload() -> None:
    """Clear both this module's compiled-bundle cache and
    :mod:`catalog`'s raw-JSON cache, so the next :func:`compile_bundle` call
    re-reads ``rules_source.json`` from disk and recompiles it — clearing
    only one of the two caches would let the other serve stale data."""
    _shipped_bundle.cache_clear()
    catalog.reload()
