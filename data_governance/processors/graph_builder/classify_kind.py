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
    "lineage_hop",
    "inproc_llm_hop",
]


# The authbridge sidecar self-tags ``source=sidecar`` and stamps a hop kind
# (``trust.hop_kind`` / ``lineage.hop.kind``) plus the hop's two endpoint
# identities (``trust.source_id``/``lineage.source.id`` -> the caller,
# ``trust.target_id``/``lineage.target.id`` -> the callee). Unlike an
# in-process span — whose graph edge is the parent->child boundary — a sidecar
# hop encodes *both* endpoints in one span, so its edge is derived from the
# span's own attributes (see ``lineage_hop``). This is what lets the DG pod
# serve arielf's lineage REST contract (runs/hops/edges) off the same
# ``entities``/``edges`` tables.
_HOP_TARGET_ROLE = {
    "principal_to_agent": "AGENT",
    "agent_to_agent": "AGENT",
    "agent_to_tool": "TOOL",
    "agent_to_llm": "LLM",
}
_HOP_SOURCE_ROLE = {
    "principal_to_agent": "PRINCIPAL",
    "agent_to_agent": "AGENT",
    "agent_to_tool": "AGENT",
    "agent_to_llm": "AGENT",
}


def lineage_hop(attributes: Mapping[str, Any]) -> dict[str, Any] | None:
    """Extract a lineage hop from a sidecar span, or ``None`` if it isn't one.

    Returns ``{hop_kind, source_id, target_id, source_role, target_role}``.
    ``source_id`` may be ``None`` (an anonymous ``principal_to_agent`` inbound
    with no authenticated principal). Prefers the ``trust.*`` keys and falls
    back to ``lineage.*`` (both are stamped by the authbridge plugin).
    """
    if attributes.get("source") != "sidecar":
        return None
    hop_kind = attributes.get("trust.hop_kind") or attributes.get("lineage.hop.kind")
    if not hop_kind:
        return None
    hop_kind = str(hop_kind)
    source_id = attributes.get("trust.source_id") or attributes.get("lineage.source.id")
    target_id = attributes.get("trust.target_id") or attributes.get("lineage.target.id")
    if not target_id:
        return None
    return {
        "hop_kind": hop_kind,
        "source_id": source_id,
        "target_id": str(target_id),
        "source_role": _HOP_SOURCE_ROLE.get(hop_kind, "AGENT"),
        "target_role": _HOP_TARGET_ROLE.get(hop_kind, "TOOL"),
    }


def inproc_llm_hop(
    attributes: Mapping[str, Any], service_name: str | None
) -> dict[str, Any] | None:
    """Derive an ``agent_to_llm`` hop from an in-process LLM span, or ``None``.

    This is the enrichment the sidecar cannot provide: the agent->LLM call rides
    an HTTPS connection the authbridge Envoy TLS-passthroughs, so no sidecar hop
    exists for it. The in-process OpenInference instrumentor *does* capture it
    (model, token counts, full prompt/response), and DG stored it — so the
    DG-backed lineage view shows the LLM reasoning steps a sidecar-only service
    never could. The hop runs ``<agent service_name> -> <model>``.
    """
    if attributes.get("source") != "in-process":
        return None
    if str(attributes.get("openinference.span.kind") or "").upper() != "LLM":
        return None
    if not service_name:
        return None
    model = attributes.get("llm.model_name") or attributes.get("gen_ai.request.model")
    if not model:
        return None
    return {
        "hop_kind": "agent_to_llm",
        "source_id": service_name,
        "target_id": str(model),
        "source_role": "AGENT",
        "target_role": "LLM",
    }

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
