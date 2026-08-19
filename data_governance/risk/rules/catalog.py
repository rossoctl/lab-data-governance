"""Read-through rule catalog metadata (issue #107, PRD §6.5/§8.4, FR-DAS-060/061).

Authoring a rule catalog is the policy engineering source-of-truth file's job, not
DAS's (§1.3) — this module never writes ``rules_source.json``, it only serves it.
The file itself ships baked into the package next to this module, resolved
relative to ``__file__`` (mirroring ``processors/classification/config.py``'s
bundled-artifact convention) — no CLI, no caller-supplied path, no env var.

``rules_source.json`` is kept in the real policy-engine shape (nested
``rule_decision`` object per rule, ``rule_categories``) rather than PRD §6.5's
flat sketch, so the file stays a faithful copy of what the policy component
actually emits — specifically, it is a valid instance of the canonical
``schema/policy.schema.json`` vendored next to this module, enforced by
``tests/risk/rules/test_schema_conformance.py``. The shape of OPA's
*returned* decision — ``schema/opa_output.schema.json`` — is a separate,
compiler-only concern this module has nothing to do with; see
``data_governance.risk.rules.rego`` (issue #173) for the Rego compiler that
produces it.
``list_rules``/``get_rule`` flatten each rule to the §6.5 serving shape on
read; the mapping lives in one place (:func:`_flatten_rule`) rather than
forcing every caller to know the nested on-disk layout.
:func:`list_rules` also takes optional, keyword-only filter and sort
criteria (risk level, enforcement type, category, event type) so a caller
such as #112's ``GET /risk/rules`` can narrow and order server-side rather
than fetching everything and post-processing.

A rule states its match criteria structurally, as fields whose *presence*
is the predicate: ``event_type`` (a singular string), ``data_items``
(e.g. ``regulatory_tags: ["PII"]`` or ``classification_level:
"RESTRICTED"``) and ``data_destinations`` (``data_destination_categories:
["external"]``). Read "this rule concerns a data item tagged PII, and when
matched the ``rule_decision`` applies". The schema permits no ``conditions``
array, so there is no predicate-expression language here.

Destinations carry two independent axes: ``data_destination_categories`` (a
coarse ``local``/``internal``/``external``/... bucket) and
``data_destination_trust_level`` (a named level such as
``UNTRUSTED_EXTERNAL``). Trust is a category rather than a magnitude, so the
level is an enum *string* — ``schema/policy.schema.json``'s
``$defs/trustLevelValues`` defines eight names and no numeric scale to map
them onto.

The load is memoized for the process lifetime (FR-DAS-060's "refreshed when
the policy bundle version changes" is met at the MVP bar the issue itself
sets — "load-on-startup-plus-manual-reload is sufficient"): call
:func:`reload` after the bundle file changes on disk. Live file-watch or
bundle-version polling is a documented follow-up, not built here.
"""

from __future__ import annotations

import functools
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

# The baked-in rule catalog source, resolved relative to this module — same
# convention as ``processors/classification/config.py``'s ``_CONFIG_DIR``.
_POLICY_DATA_DIR = Path(__file__).parent / "_policy_data"
_RULES_SOURCE = _POLICY_DATA_DIR / "rules_source.json"

__all__ = [
    "load_rules_source",
    "bundle_version",
    "list_rules",
    "get_rule",
    "category_counts",
    "reload",
    "RISK_LEVEL_ORDER",
    "ENFORCEMENT_ORDER",
    "SORT_KEYS",
]

# Severity order for ``sort_by="risk_level"``, most severe first. Risk level
# is a sortable field with an inherent ranking — sorting it alphabetically
# would interleave "high"/"low"/"medium" meaninglessly — so the ranking is
# encoded here rather than inferred. Confirmed against
# ``schema/policy.schema.json``'s ``$defs/riskLevelValues`` by
# :func:`test_risk_order_matches_the_schema_vocabulary`. Values outside this
# tuple (including a rule with no ``rule_decision``, whose risk level is
# ``None``) sort after every ranked value instead of raising on a ``None``
# comparison.
RISK_LEVEL_ORDER: tuple[str, ...] = (
    "critical",
    "high",
    "medium",
    "low",
    "none",
    "unknown",
)

# Severity order for ``sort_by="enforcement"``, most severe first. Unlike
# ``RISK_LEVEL_ORDER``, this ranking is not derivable from the schema's own
# ``$defs/enforcementTypeValues`` listing order (which is not meaningfully
# ordered) — it was supplied explicitly and is pinned here verbatim.
# ``throttle`` was part of an earlier vocabulary revision and has since been
# dropped upstream; it does not appear in ``enforcementTypeValues`` and is
# deliberately absent here too.
ENFORCEMENT_ORDER: tuple[str, ...] = (
    "block",
    "quarantine",
    "require_approval",
    "redact",
    "mask",
    "anonymize",
    "encrypt",
    "escalate",
    "notify",
    "warn",
    "log_only",
    "audit",
    "allow",
)

# Fields ``list_rules`` accepts for ``sort_by``. Restricted to the stable,
# meaningfully-orderable ones: notably not ``categories`` (a list, so any
# ordering would be arbitrary) and not ``confidence`` (not part of the §6.5
# serving shape).
SORT_KEYS: frozenset[str] = frozenset(
    {"risk_level", "enforcement", "rule_id", "rule_name", "event_type"}
)

# Sortable fields with a severity ranking, rather than a lexicographic one.
# Both tuples put unranked/missing values last via the same
# ``ranks.get(value, len(order))`` fallback (see :func:`_sort_key`).
_RANKED_FIELDS: dict[str, tuple[str, ...]] = {
    "risk_level": RISK_LEVEL_ORDER,
    "enforcement": ENFORCEMENT_ORDER,
}


@functools.lru_cache(maxsize=1)
def load_rules_source() -> dict[str, Any]:
    """Load the raw ``rules_source.json`` payload, memoized for the process
    lifetime. Returns the whole envelope (``policy_id``, ``version``,
    ``status``, ``rules``, ...), not just the rule list."""
    with _RULES_SOURCE.open(encoding="utf-8") as f:
        return json.load(f)


def reload() -> None:
    """Clear the memoized load so the next call re-reads from disk.

    The issue's stated MVP bar for "refreshed when the policy bundle version
    changes" — call this after the bundle file is updated on disk. No
    automatic file-watch or version-polling is implemented.
    """
    load_rules_source.cache_clear()


def bundle_version() -> str:
    """The rule catalog's ``version`` field, e.g. for cache-invalidation
    checks by a future caller."""
    return load_rules_source().get("version", "")


def _flatten_rule(raw_rule: dict[str, Any]) -> dict[str, Any]:
    """Map one nested on-disk rule entry to the flat PRD §6.5 serving shape."""
    decision = raw_rule.get("rule_decision") or {}
    return {
        "rule_id": raw_rule.get("rule_id"),
        "rule_name": raw_rule.get("rule_name"),
        "categories": list(raw_rule.get("rule_categories") or []),
        "risk_level": decision.get("risk_level"),
        "enforcement": decision.get("enforcement_type"),
        "explanation": decision.get("explanation"),
        "event_type": raw_rule.get("event_type"),
        "data_items": list(raw_rule.get("data_items") or []),
        "data_destinations": list(raw_rule.get("data_destinations") or []),
        "allowed_actions": list(decision.get("allowed_actions") or []),
        "rule_sources": list(raw_rule.get("rule_sources") or []),
    }


def _accepts(value: Any, criterion: str | Iterable[str] | None) -> bool:
    """Whether ``value`` satisfies one optional filter criterion.

    ``None`` means "no filter" and accepts everything. A bare string matches
    exactly; any other iterable matches if ``value`` is among its members —
    so an *empty* collection accepts nothing. That distinction is deliberate:
    a caller narrowing a computed set down to zero accepted values should get
    no rules back, not silently get all of them.

    Matching is case-sensitive and exact. These values originate from OPA and
    are treated as opaque strings (implementation-notes-v3 §3.3), so there is
    no normalization or enum coercion here.
    """
    if criterion is None:
        return True
    if isinstance(criterion, str):
        return value == criterion
    return value in set(criterion)


def _sort_key(field: str):
    """Ordering key for one sortable field.

    ``risk_level`` and ``enforcement`` order by their :data:`_RANKED_FIELDS`
    severity tuple; everything else orders lexicographically. Both styles
    put missing/unrecognized values last so a rule lacking a
    ``rule_decision`` (or one whose ``rule_decision`` omits the ranked
    field) cannot crash the sort on a ``None`` comparison.
    """
    if field in _RANKED_FIELDS:
        order = _RANKED_FIELDS[field]
        ranks = {value: rank for rank, value in enumerate(order)}
        return lambda rule: ranks.get(rule.get(field), len(order))
    # (0, value) for present values, (1, "") for missing — tuples keep absent
    # values after every present one under both ascending and reversed order.
    return lambda rule: (0, rule[field]) if rule.get(field) is not None else (1, "")


def list_rules(
    *,
    risk_level: str | Iterable[str] | None = None,
    enforcement: str | Iterable[str] | None = None,
    category: str | Iterable[str] | None = None,
    event_type: str | Iterable[str] | None = None,
    sort_by: str | None = None,
    descending: bool = False,
) -> list[dict[str, Any]]:
    """Rules flattened to the §6.5 serving shape, optionally filtered and sorted.

    With no arguments, returns every rule in ``rules_source.json`` order —
    the deterministic default a list endpoint needs.

    Filters are keyword-only and **conjunctive**: a rule must satisfy every
    criterion given. Each accepts either a single value or a collection of
    accepted values (see :func:`_accepts` for the empty-collection rule).
    ``category`` matches a rule whose ``categories`` list contains the value,
    since that field is a list rather than a scalar.

    ``sort_by`` must be one of :data:`SORT_KEYS`; ``risk_level`` and
    ``enforcement`` sort by severity (most severe first, per
    :data:`RISK_LEVEL_ORDER` / :data:`ENFORCEMENT_ORDER`) rather than
    alphabetically, and the remaining keys sort lexicographically.
    ``descending`` reverses whichever order applies. Sorting is stable, so
    rules tied on the sort field keep their relative file order. Unknown
    keys raise ``ValueError`` rather than silently returning unsorted
    results, which would be indistinguishable from a working sort on a
    uniform catalog.
    """
    if sort_by is not None and sort_by not in SORT_KEYS:
        raise ValueError(
            f"sort_by must be one of {sorted(SORT_KEYS)}, got {sort_by!r}"
        )

    raw_rules = load_rules_source().get("rules") or []
    rules = [_flatten_rule(r) for r in raw_rules]

    rules = [
        rule
        for rule in rules
        if _accepts(rule["risk_level"], risk_level)
        and _accepts(rule["enforcement"], enforcement)
        and _accepts(rule["event_type"], event_type)
        and (
            category is None
            or any(_accepts(c, category) for c in rule["categories"])
        )
    ]

    if sort_by is not None:
        rules.sort(key=_sort_key(sort_by), reverse=descending)
    return rules


def get_rule(rule_id: str) -> dict[str, Any] | None:
    """A single flattened rule by ``rule_id``, or ``None`` if unknown.

    Returns ``None`` rather than raising so a caller (e.g. #112's REST
    handler) owns 404 shaping instead of catching an exception. Lookup is
    case-sensitive — rule IDs are opaque catalog keys, not user input to
    normalize.
    """
    for rule in list_rules():
        if rule["rule_id"] == rule_id:
            return rule
    return None


def category_counts() -> dict[str, int]:
    """FR-DAS-061: count of rules per category, across every category
    present in the catalog. A category listed more than once on the same
    rule counts once for that rule. Sorted by category name for stable
    output."""
    counts: dict[str, int] = {}
    for rule in list_rules():
        for category in set(rule["categories"]):
            counts[category] = counts.get(category, 0) + 1
    return dict(sorted(counts.items()))
