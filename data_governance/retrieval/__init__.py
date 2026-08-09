"""Layer 2 retrieval API — the sanctioned typed read path over stored and
derived governance data.

The package is split by seam into typed submodules, each returning frozen
dataclasses so the REST layer maps them mechanically to the wire and all
derivation stays behind the interface (ADR-0005):

- :mod:`.spans` — **Span** reads (``get_spans`` + ``Span`` / ``TraceCounts`` /
  ``GetSpansResult``). See ADR-0001 (listing roots) and ADR-0006 (full row).
- :mod:`.interactions` — the derived **Interaction** / **Entity** forest for one
  trace (``get_interactions`` / ``get_entities`` and the span-evidence
  sub-reads ``get_interaction_spans`` / ``get_entity_spans``), plus the
  cross-trace cursor feed over the same interactions
  (``get_interactions_feed``). Eventually consistent; returns empty typed
  results before the interactions migration has run (never an error).
- :mod:`.payloads` — a content-addressed **Payload** read that inlines the
  **Classification** verdict (``get_payload``). Write-once, cross-trace.

Only the public surface is re-exported here. Consumers of the private
row-mapping helpers (``spans._COLUMNS`` / ``spans._row_to_span`` — the
``P-interactions`` processor) import them from :mod:`.spans` directly, naming
that reach-in rather than laundering it through the package root.
"""

from __future__ import annotations

from data_governance.retrieval.interactions import (
    DestinationView,
    EntitySpanEvidenceView,
    EntityView,
    GetEntitiesResult,
    GetEntitySpansResult,
    GetInteractionSpansResult,
    GetInteractionsFeedResult,
    GetInteractionsResult,
    HttpView,
    InteractionKindsView,
    InteractionLegView,
    InteractionView,
    SpanEvidenceView,
    get_entities,
    get_entity_spans,
    get_interaction_spans,
    get_interactions,
    get_interactions_feed,
)
from data_governance.retrieval.payloads import (
    ClassificationView,
    PayloadView,
    get_payload,
)
from data_governance.retrieval.spans import (
    GetSpansResult,
    Span,
    TraceCounts,
    get_spans,
)

__all__ = [
    # spans
    "GetSpansResult",
    "Span",
    "TraceCounts",
    "get_spans",
    # interactions / entities forest
    "DestinationView",
    "EntitySpanEvidenceView",
    "EntityView",
    "GetEntitiesResult",
    "GetEntitySpansResult",
    "GetInteractionSpansResult",
    "GetInteractionsFeedResult",
    "GetInteractionsResult",
    "HttpView",
    "InteractionKindsView",
    "InteractionLegView",
    "InteractionView",
    "SpanEvidenceView",
    "get_entities",
    "get_entity_spans",
    "get_interaction_spans",
    "get_interactions",
    "get_interactions_feed",
    # payloads
    "ClassificationView",
    "PayloadView",
    "get_payload",
]
