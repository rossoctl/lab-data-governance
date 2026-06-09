"""Graph-builder processor: derive the entity/edge graph from spans.

The second class of writer in the system (alongside ``P-otel-receiver``), and
the first table->table derived flow: it reads ``spans`` and writes the
``entities`` + ``edges`` graph pinned by ADR-0007. The receiver and the
``spans`` schema are untouched.

Public surface:

    from data_governance.processors.graph_builder import run_backfill
    result = run_backfill()            # one transaction, idempotent

Classifier internals (``semantic_kind``/``sub_kind``/``edge_kind``/
``entity_id``) live in :mod:`.classify_kind`; the backfill in :mod:`.build`.

Still out of scope here, by design: the marks layer (classification/policy/
taint) — that's ADR-0008 / issue #60 — and incremental, watermark-driven
derivation — that's issue #62.
"""

from __future__ import annotations

from .build import BackfillResult, run_backfill
from .classify_kind import (
    display_name,
    edge_kind,
    entity_id,
    semantic_kind,
    sub_kind,
)


__all__ = [
    "BackfillResult",
    "run_backfill",
    "semantic_kind",
    "sub_kind",
    "entity_id",
    "display_name",
    "edge_kind",
]
