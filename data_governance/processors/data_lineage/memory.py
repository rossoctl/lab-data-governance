"""The kind-driven entity predicates, and the memory node priors pool into (#117).

**This module is the single named place for the data-lineage entity predicates.**
Nothing else in the data-lineage code may test ``kind == "agent"`` or
``kind == "tool"``: the traversal asks :func:`accumulates` and
:func:`is_entity_source`, so redeclaring either is a one-line change here.
(The traversal test proves it for :func:`accumulates` by redeclaring the set and
watching an inbound set grow.)

Two predicates, one module, because **one deferred table supplies both**: the spec's
Entity Taxonomy (``data_lineage_alg.md:24-35``) is meant to declare persistent
storage, source and target per entity, and reading it is deferred (ADR-0027 D12).
Until it lands both answers come from ``entities.kind``, and when it lands both
functions grow the same lookup with no call site changing.

ADR-0027 D2 — **transient/session memory is assumed always present**. Within a
trace an accumulating entity retains every prior payload routed to it, so its
inbound set grows past one; a memoryless entity keeps only its latest inbound.
Since D11 collapsed the algebra to two operations, ``|inbound(i)|`` no longer picks
an *operation* — every non-root leg runs ``merge_lineage`` — but memory still
decides **how many** priors pool and therefore that op's arity. There is
deliberately no separate "has memory" predicate: memory is folded into the size of
``inbound(i)``, and this module is where that folding is parameterized.

ADR-0027 D12 — :func:`is_entity_source` is the *structural* route to origin-hood.
It is orthogonal to the matcher's verdict (``matched`` says whether upstream content
survived; this says whether the entity also contributed content of its own), which
is why it is a parameter of ``merge_lineage`` and not something an op could derive.

**Why entity kind.** ``entities.kind`` is the only signal available today, so both
predicates read it, but each stays in one named place rather than scattering
``kind == 'agent'`` / ``kind == 'tool'`` checks — declared config is where this
belongs eventually. An agent accumulates; an LLM or tool does not. A tool is a data
source; an LLM or agent is not.

**Memory granularity stays open.** ADR-0027 lists blob-vs-keyed (per
session/user/thread) as an unresolved item, and it matters for Step II (unkeyed
memory would reintroduce the mixing bowl *across* traces — a false cross-user
data-flow claim). So the memory node is modelled as :class:`MemoryNode`
``(entity_id, memory_key)`` with ``memory_key = None`` meaning unkeyed/blob, the v1
default. Inbound payloads pool per *memory node*, not per entity, so introducing
keying later is a value change in :func:`memory_node_for_leg` — not a migration and
not a change to the traversal.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .traversal import Entity, Leg

# The **Entity** kinds that retain their prior inbound payloads within a trace
# (ADR-0027 D2's working assumption). The full kind set is `user`, `client`,
# `agent`, `tool`, `llm`, `service` (CONTEXT.md **Entity**); only `agent` holds
# session state today. Editing this frozenset is the whole of "redeclare which
# entities accumulate" — see the module docstring.
ACCUMULATING_KINDS: frozenset[str] = frozenset({"agent"})

# The **Entity** kinds that are declared **data sources** — an entity that
# contributes content of its own, not merely content it was handed. The spec's
# Entity Taxonomy defaults (``data_lineage_alg.md:31-35``): `tool` ✓, `LLM` ✗,
# `agent` ✗. A kind absent from this set is not a source, which is the right
# default for the kinds the taxonomy does not name (`user`, `client`, `service`):
# the routing already makes a genuine trace root an origin structurally (D3(1)),
# so declaring one a source as well would add nothing.
#
# Editing this frozenset is the whole of "redeclare which entities are sources".
SOURCE_KINDS: frozenset[str] = frozenset({"tool"})


@dataclasses.dataclass(frozen=True, slots=True)
class MemoryNode:
    """Where an entity's retained inbound payloads pool.

    ``memory_key = None`` is **unkeyed/blob** — the v1 default, and the reason this
    is a two-field node rather than a bare ``entity_id``: partitioning an entity's
    memory per session/user/thread later becomes a value change (a non-null key)
    instead of a schema or traversal change (ADR-0027's open item).
    """

    entity_id: str
    memory_key: str | None = None


def accumulates(entity: Entity) -> bool:
    """Does *entity* retain its prior inbound payloads within a trace?

    THE accumulating-entity predicate (ADR-0027 D2). Driven by
    :data:`ACCUMULATING_KINDS` today because ``entities.kind`` is the only signal
    available; the eventual home is declared per-entity config, which this function
    would consult without any call site changing.
    """
    return entity.kind in ACCUMULATING_KINDS


def is_entity_source(entity: Entity) -> bool:
    """Is *entity* a **data source** — does it contribute content of its own?

    THE entity-source predicate (ADR-0027 D12), and the answer
    ``merge_lineage``'s ``is_entity_source`` parameter carries. Driven by
    :data:`SOURCE_KINDS`, i.e. the spec's kind defaults, because reading the
    declared taxonomy table is deferred (``data_lineage_alg.md:26-27``).

    This is the **structural** route to origin-hood, independent of the matcher: a
    tool that both consumes its request and returns freshly-read data reports both
    facts. It is deliberately NOT inferred from a tool's name, description or
    payload sizes — that was considered and rejected (only one of the eight tools in
    the live corpus even carries ``tool.description``), so a pass-through
    delegation tool over-reports as a source until the declared table lands. Over-
    reporting an origin is the safe direction for a governance tool; the failure it
    replaces was *under*-reporting an external data ingress.

    Note the taxonomy's other two columns have no consumer yet and so no function
    here: ``target`` is unread, and ``location`` (internal/external) is a
    placeholder with no v1 semantics (D12).
    """
    return entity.kind in SOURCE_KINDS


def memory_node(entity: Entity, memory_key: str | None = None) -> MemoryNode:
    """The memory node for *entity* — unkeyed (blob) unless a key is supplied."""
    return MemoryNode(entity_id=entity.id, memory_key=memory_key)


def memory_node_for_leg(entity: Entity, leg: Leg) -> MemoryNode:
    """The memory node *entity*'s memory is partitioned into while producing *leg*.

    The seam that keeps granularity open. v1 returns the **unkeyed** node for every
    leg (blob memory: one pool per entity, per trace), which is what makes an
    agent a mixing bowl intra-trace. A future keyed policy derives the key from the
    leg's context (session / user / thread) and returns a distinct node per
    partition; the traversal pools inbound payloads per node, so nothing else has
    to change.
    """
    return memory_node(entity)
