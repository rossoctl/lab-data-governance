"""Span -> entity/edge classification (issue #56 / ADR-0007).

Pure functions, no I/O. Two responsibilities:

1. Derive an entity's identity from a span: its ``semantic_kind`` (via a fixed
   ladder), its ``sub_kind`` refinement, the deterministic ``entity_id`` over
   the ``(service_name, semantic_kind, sub_kind)`` grain, and a human
   ``display_name``.
2. Derive an edge's ``edge_kind`` from the two endpoints' semantic kinds.

The ``semantic_kind`` ladder is the load-bearing piece, and its rung order was
fixed against **captured post-transform spans** (the kagenti collector rewrites
``gen_ai.*`` -> ``llm.*`` before the receiver sees a span, so ``llm.*`` is the
signal that actually survives — verified on the live span store, where 138
spans carry an ``llm.*`` key vs only 16 with a residual ``gen_ai.*`` key). Do
not reorder the rungs without re-checking against real data.

Still out of scope here, by design: identity ``attributes`` extras (model id,
namespace) are left NULL by the builder for now; richer entity attributes are a
later refinement (the provenance pointer keeps re-derivation cheap). Finer grain
than ``sub_kind`` (per-deployment, per-namespace) is also deferred.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


__all__ = [
    "semantic_kind",
    "sub_kind",
    "entity_id",
    "display_name",
    "edge_kind",
]

# Entity-id encoding. Real service names and kinds cannot contain ASCII control
# chars, so using the Unit Separator (U+001F) as the field delimiter and the
# Record Separator (U+001E) as the NULL sentinel makes the tuple -> key mapping
# injective: no real (service, kind, sub) value can collide with another, and a
# NULL component cannot be confused with a present one. The key is opaque;
# display_name carries the human label.
_SEP = "\x1f"
_NULL = "\x1e"

# OTLP span kinds that map to a meaningful boundary class. INTERNAL deliberately
# does NOT map here — an internal span with no semantic markers is UNKNOWN, not
# a graph boundary of its own.
_OTLP_BOUNDARY_KINDS = frozenset({"SERVER", "CLIENT", "PRODUCER", "CONSUMER"})


def semantic_kind(attributes: Mapping[str, Any], otlp_kind: str | None) -> str:
    """Classify a span's entity kind by the fixed ladder (highest rung wins).

    1. ``openinference.span.kind`` present -> that value (LLM/TOOL/AGENT/CHAIN/
       RETRIEVER/...), upper-cased.
    2. else any ``llm.*`` key present -> ``LLM`` (the post-transform signal).
    3. else any ``gen_ai.*`` key present -> ``LLM`` (fallback for sources the
       collector did not transform).
    4. else the OTLP span kind, if it is a boundary kind
       (SERVER/CLIENT/PRODUCER/CONSUMER).
    5. else ``UNKNOWN``.
    """
    oi = attributes.get("openinference.span.kind")
    if oi:
        return str(oi).upper()
    if any(k.startswith("llm.") for k in attributes):
        return "LLM"
    if any(k.startswith("gen_ai.") for k in attributes):
        return "LLM"
    if otlp_kind in _OTLP_BOUNDARY_KINDS:
        return otlp_kind  # type: ignore[return-value]
    return "UNKNOWN"


def sub_kind(kind: str, attributes: Mapping[str, Any]) -> str | None:
    """The refinement discriminator for an entity of the given ``kind``.

    - ``LLM`` -> the model name (``llm.model_name``, falling back to
      ``gen_ai.request.model``), so two models do not collapse into one node.
    - ``TOOL`` -> the tool name (``tool.name``), so two tools do not collapse.
    - anything else -> ``None``.

    ``None`` is the safe fallback whenever the discriminator attribute is
    absent.
    """
    if kind == "LLM":
        return attributes.get("llm.model_name") or attributes.get(
            "gen_ai.request.model"
        )
    if kind == "TOOL":
        return attributes.get("tool.name")
    return None


def entity_id(service_name: str | None, kind: str, sub: str | None) -> str:
    """Deterministic opaque key over the ``(service_name, kind, sub)`` grain.

    Idempotent: the same tuple always yields the same key, so re-derivation is a
    no-op. Collision-safe because the separator and NULL sentinel are control
    chars that cannot appear in a service name or kind.
    """
    return _SEP.join(_NULL if c is None else c for c in (service_name, kind, sub))


def display_name(service_name: str | None, kind: str, sub: str | None) -> str:
    """Human-readable label for an entity (the readable counterpart to the
    opaque ``entity_id``)."""
    parts = [service_name or "(no service)", kind]
    if sub:
        parts.append(sub)
    return " · ".join(parts)


def edge_kind(from_kind: str | None, to_kind: str) -> str:
    """Boundary class for an edge, from the two endpoints' semantic kinds.

    ``<from>_<to>`` for a resolved boundary (e.g. ``AGENT_LLM``,
    ``CHAIN_TOOL``, ``CLIENT_SERVER``); ``UNKNOWN_<to>`` for an orphan boundary
    whose parent entity is unknown (``from_kind is None``).
    """
    if from_kind is None:
        return f"UNKNOWN_{to_kind}"
    return f"{from_kind}_{to_kind}"
