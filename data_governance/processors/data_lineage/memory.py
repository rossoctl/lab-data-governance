"""Which entities accumulate, and the memory node their priors pool into (#117).

**This module is the single named place for the accumulating-entity predicate.**
Nothing else in the data-lineage code may test ``kind == "agent"``: the traversal
asks :func:`accumulates`, so redeclaring which kinds accumulate is a one-line
change here and the op selection follows. (The traversal test proves this by
redeclaring the set and watching a ``linear`` turn into a ``merge``.)

ADR-0027 D2 — **transient/session memory is assumed always present**. Within a
trace an accumulating entity retains every prior payload routed to it, so its
inbound set grows past one and op selection lands on ``merge``; a memoryless entity
keeps only its latest inbound and stays on ``linear``. There is deliberately no
separate "has memory" predicate in the selection logic — memory is folded into the
size of ``inbound(i)``, and this module is where that folding is parameterized.

**Why entity kind.** The issue is explicit: "Entity kind is the only signal
available today, so drive it from that, but keep the predicate in one named place
rather than scattering ``kind == 'agent'`` checks — declared config is where this
belongs eventually." An agent accumulates; an LLM or tool does not. When declared
per-entity config arrives, :func:`accumulates` grows a config lookup and every
call site is already routed through it.

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
