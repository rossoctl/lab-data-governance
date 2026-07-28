"""P-data-lineage — the intra-trace **data lineage** processor (issue #117).

Answers two questions about every interaction leg's payload: **where did it
originate** (its data sources), and **what did it pass through** (entities and the
transformations applied). Derived at ingest and persisted to ``lineage_metadata``,
so a governance read is a lookup rather than a recompute (ADR-0027 D7).

The layering, innermost first — each layer is testable without the one outside it:

- :mod:`.operations` — the three-op algebra (``init_lineage`` / ``linear_lineage`` /
  ``merge_lineage``) from ``docs/data_lineage_alg.md``. Pure: metadata in, metadata
  out, matcher injected.
- :mod:`.memory` — **the** accumulating-entity predicate (ADR-0027 D2) and the
  ``(entity_id, memory_key)`` memory node. The single named place; nothing else
  tests ``kind == "agent"``.
- :mod:`.traversal` — op selection (D4) and structural inbound routing (D1) over one
  trace's legs in leg-``seq`` order. Pure.
- :mod:`.driver` — the DB adapter over the shared cursor loop: drains the
  ``interaction_legs`` stream, re-derives the arriving leg's whole trace, upserts.

Matching is a black box reached only through
:func:`data_governance.matching.get_matcher` (ADR-0027: lineage does not know how
matching decides), so lineage quality improves with the matcher and lineage is
computable today with the trivial default.

**Naming.** "Lineage" already means **span** lineage elsewhere in this package (a
span's ancestors ∪ subtree under a ``seq`` horizon — ``interactions/state.py``,
ADR-0007/0016). This is **data lineage**, an unrelated concept: span lineage is
about graph structure, data lineage about where content came from.
"""

from __future__ import annotations
