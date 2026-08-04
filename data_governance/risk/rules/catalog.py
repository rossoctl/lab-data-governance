"""Read-through rule catalog metadata (issue #107, PRD §6.5/§8.4, FR-DAS-060/061).

Authoring a rule catalog is the policy engineering source-of-truth file's job, not
DAS's (§1.3) — this module never writes ``rules_source.json``, it only serves it.
The file itself ships baked into the package next to this module, resolved
relative to ``__file__`` (mirroring ``processors/classification/config.py``'s
bundled-artifact convention) — no CLI, no caller-supplied path, no env var.

``rules_source.json`` is kept in the real policy-engine shape (nested
``policy_decision`` object, ``rule_categories``) rather than PRD §6.5's flat
sketch, so the file stays a faithful copy of what the policy component
actually emits — specifically, it is a valid instance of the canonical
``schema/policy.schema.json`` vendored next to this module, enforced by
``tests/risk/rules/test_schema_conformance.py``.
``list_rules``/``get_rule`` flatten each rule to the §6.5 serving shape on
read; the mapping lives in one place (:func:`_flatten_rule`) rather than
forcing every caller to know the nested on-disk layout.

A rule states its match criteria structurally, as fields whose *presence*
is the predicate: ``event_type`` (a singular string), ``data_items``
(e.g. ``regulatory_tags: ["PII"]`` or ``classification_level:
"RESTRICTED"``) and ``data_destinations`` (``data_destination_categories:
["external"]``). Read "this rule concerns a data item tagged PII, and when
matched the ``policy_decision`` applies". The schema permits no ``conditions``
array, so there is no predicate-expression language here. Note that
untrusted-external destinations are expressed through the closed
``data_destination_categories`` enum rather than the schema's numeric
``data_destination_trust_level``, because the companion
``schema/recommended_enum_values.md`` defines trust levels as *names*
(``UNTRUSTED_EXTERNAL``, ...) and no numeric scale to map them onto.

The load is memoized for the process lifetime (FR-DAS-060's "refreshed when
the policy bundle version changes" is met at the MVP bar the issue itself
sets — "load-on-startup-plus-manual-reload is sufficient"): call
:func:`reload` after the bundle file changes on disk. Live file-watch or
bundle-version polling is a documented follow-up, not built here.
"""

from __future__ import annotations

import functools
import json
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
]


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
    decision = raw_rule.get("policy_decision") or {}
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


def list_rules() -> list[dict[str, Any]]:
    """Every rule in the catalog, flattened to the §6.5 serving shape, in the
    same order as ``rules_source.json`` (deterministic for a list endpoint)."""
    raw_rules = load_rules_source().get("rules") or []
    return [_flatten_rule(r) for r in raw_rules]


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
