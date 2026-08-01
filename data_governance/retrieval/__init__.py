"""Layer 2 retrieval API — the sanctioned typed read path over stored and
derived governance data.

The package is split by seam into typed submodules, each returning frozen
dataclasses so the REST layer maps them mechanically to the wire and all
derivation stays behind the interface (ADR-0005):

- :mod:`.spans` — **Span** reads (``get_spans`` + ``Span`` / ``TraceCounts`` /
  ``GetSpansResult``). See ADR-0001 (listing roots) and ADR-0006 (full row).
- :mod:`.interactions` — the derived **Interaction** / **Entity** forest for one
  trace (``get_interactions`` / ``get_entities`` and the span-evidence
  sub-reads ``get_interaction_spans`` / ``get_entity_spans``). Trace-scoped and
  eventually consistent; returns empty typed results before the interactions
  migration has run (never an error).
- :mod:`.payloads` — a content-addressed **Payload** read that inlines the
  **Classification** verdict (``get_payload``). Write-once, cross-trace.
- :mod:`.lineage` — the trace-scoped **Data lineage** read
  (``get_data_lineage``): the persisted per-**Interaction leg** metadata triple
  plus the trace's ``complete``/``partial`` coverage (ADR-0028 D6/D8), a pure
  lookup (ADR-0028 D7). Nullable per leg in the eventual-consistency window,
  ``status=None`` (*unknown*, never ``complete`` — ADR-0028 D6) before the trace
  has been derived, and empty before the lineage migration has run — never an
  error.
- :mod:`.lineage_graph` — the other two **Data lineage** grains (ADR-0028 D14):
  ``get_lineage_graph`` walks one trace's lineage upstream (``fanin``) or
  downstream (``fanout``) from an **Entity**, and ``get_lineage_summary`` serves a
  trace's ``list sources`` / ``list destinations``. Unlike :mod:`.lineage` these
  *derive* — a hop is a leg the trace has whose lineage was actually derived, so
  the walk ends where provenance ends — but they still run no matcher (D7) and
  never cross a trace boundary (D14).

Only the public surface is re-exported here. Consumers of the private
row-mapping helpers (``spans._COLUMNS`` / ``spans._row_to_span`` — the
``P-interactions`` processor) import them from :mod:`.spans` directly, naming
that reach-in rather than laundering it through the package root.
"""

from __future__ import annotations

from data_governance.retrieval.interactions import (
    EntitySpanEvidenceView,
    EntityView,
    GetEntitiesResult,
    GetEntitySpansResult,
    GetInteractionSpansResult,
    GetInteractionsResult,
    InteractionLegView,
    InteractionView,
    SpanEvidenceView,
    get_entities,
    get_entity_spans,
    get_interaction_spans,
    get_interactions,
)
from data_governance.retrieval.lineage import (
    DataLineageLegView,
    DataLineageView,
    GetDataLineageResult,
    get_data_lineage,
)
from data_governance.retrieval.lineage_graph import (
    FANIN,
    FANOUT,
    GetLineageGraphResult,
    GetLineageSummaryResult,
    LineageGraphEntityView,
    LineageGraphLegView,
    UnknownDirection,
    get_lineage_graph,
    get_lineage_summary,
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
    "EntitySpanEvidenceView",
    "EntityView",
    "GetEntitiesResult",
    "GetEntitySpansResult",
    "GetInteractionSpansResult",
    "GetInteractionsResult",
    "InteractionLegView",
    "InteractionView",
    "SpanEvidenceView",
    "get_entities",
    "get_entity_spans",
    "get_interaction_spans",
    "get_interactions",
    # payloads
    "ClassificationView",
    "PayloadView",
    "get_payload",
    # data lineage
    "DataLineageLegView",
    "DataLineageView",
    "GetDataLineageResult",
    "get_data_lineage",
    # data lineage graph / summary (ADR-0028 D14)
    "FANIN",
    "FANOUT",
    "GetLineageGraphResult",
    "GetLineageSummaryResult",
    "LineageGraphEntityView",
    "LineageGraphLegView",
    "UnknownDirection",
    "get_lineage_graph",
    "get_lineage_summary",
]
