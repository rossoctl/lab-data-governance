"""The kind-driven entity predicates, and the memory node priors pool into (#117).

**This module is the single named place for the data-lineage entity predicates.**
Nothing else in the data-lineage code may test ``kind == "agent"`` or
``kind == "tool"``: callers ask :func:`accumulates`, :func:`is_entity_source` or
:func:`is_entity_target`, so redeclaring any of them is a one-line change here.
(The traversal test proves it for :func:`accumulates` by redeclaring the set and
watching an inbound set grow.)

Three predicates, one module, because **one deferred table supplies all of them**:
the spec's Entity Taxonomy (``data_lineage_alg.md:24-35``) is meant to declare
persistent storage, source and target per entity, and reading it is deferred
(ADR-0028 D12). Until it lands every answer comes from ``entities.kind``, and when it
lands all three functions grow the same lookup with no call site changing.

Note the third predicate has a **read-path** consumer rather than an algebra one:
:func:`is_entity_target` backs the trace-grain ``list destinations`` read (ADR-0028
D14). It lives here anyway — beside the other two — because the deferred table
supplies all three columns, so they must move together. Nothing in
:mod:`.operations` or :mod:`.traversal` consults it, which is why D12's "``target``
is unread" remains true *of the algebra*.

ADR-0028 D2 — **transient/session memory is assumed always present**. Within a
trace an accumulating entity retains every prior payload routed to it, so its
inbound set grows past one; a memoryless entity keeps only its latest inbound.
Since D11 collapsed the algebra to two operations, ``|inbound(i)|`` no longer picks
an *operation* — every non-root leg runs ``merge_lineage`` — but memory still
decides **how many** priors pool and therefore that op's arity. There is
deliberately no separate "has memory" predicate: memory is folded into the size of
``inbound(i)``, and this module is where that folding is parameterized.

ADR-0028 D12 — :func:`is_entity_source` is the *structural* route to origin-hood.
It is orthogonal to the matcher's verdict (``matched`` says whether upstream content
survived; this says whether the entity also contributed content of its own), which
is why it is a parameter of ``merge_lineage`` and not something an op could derive.

**Why entity kind.** ``entities.kind`` is the only signal available today, so all
three predicates read it, but each stays in one named place rather than scattering
``kind == 'agent'`` / ``kind == 'tool'`` checks — declared config is where this
belongs eventually. An agent accumulates; an LLM or tool does not. A tool is a data
source *and* a data target; an LLM or agent is neither.

**Memory granularity stays open.** ADR-0028 lists blob-vs-keyed (per
session/user/thread) as an unresolved item, and it matters for Step II (unkeyed
memory would reintroduce the mixing bowl *across* traces — a false cross-user
data-flow claim). So the memory node is modelled as :class:`MemoryNode`
``(entity_id, memory_key)`` with ``memory_key = None`` meaning unkeyed/blob, the v1
default. Inbound payloads pool per *memory node*, not per entity, so introducing
keying later is a value change in :func:`memory_node_for_leg` — not a migration and
not a change to the traversal.

**Memory scope (label lineage only).** Every entity has one scope: ``call`` links
what it received inside one invocation to what it sends later in it;
``conversation`` links across its invocations under one root; ``persistent`` is
memory across conversations. The default comes from the kind
(:data:`SCOPE_BY_KIND`); a declaration per entity, keyed by its ``natural_key``
(``LABEL_MEMORY_SCOPES``, a JSON object, parsed by :func:`parse_scopes`), overrides it.
:func:`scope` answers it. Inside one conversation the label fold asks
:func:`mixes_calls`: what :func:`accumulates` says by kind, unless a declaration
says ``call`` or ``conversation``; an entity declared ``persistent`` keeps what its
kind does there. Nothing reads ``persistent`` yet. A declared key that matches no
entity changes nothing and is not reported. Data lineage reads none of this.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .traversal import Entity, Leg

# The **Entity** kinds that retain their prior inbound payloads within a trace
# (ADR-0028 D2's working assumption). The full kind set is `user`, `client`,
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

# The **Entity** kinds that are declared **data targets** — an entity data flows
# *to*, as opposed to one that contributes content of its own. The spec's Entity
# Taxonomy defaults (``data_lineage_alg.md:31-35``) put `target` ✓ on `tool` alone,
# same as `source`: `LLM` ✗, `agent` ✗.
#
# Read by the trace-grain ``list destinations`` read (ADR-0028 D14), which is this
# column's FIRST consumer — D12 recorded `target` as unread because no *operation*
# in the algebra reads it, and that is still true: nothing in :mod:`.operations` or
# :mod:`.traversal` consults this set. It lives here rather than in the retrieval
# layer because the same deferred taxonomy table supplies all three predicates, so
# they must move together when it lands.
#
# NOTE the v1 coincidence: this is currently the SAME set as `SOURCE_KINDS`, so
# "sources" and "destinations" agree on membership for unrelated reasons. They are
# two independent columns of the taxonomy that happen to share a default, and they
# diverge as soon as the declared table distinguishes a read tool from a write one.
# Do not collapse them into one constant — that would encode the coincidence as a
# rule (ADR-0028 D14).
TARGET_KINDS: frozenset[str] = frozenset({"tool"})


@dataclasses.dataclass(frozen=True, slots=True)
class MemoryNode:
    """Where an entity's retained inbound payloads pool.

    ``memory_key = None`` is **unkeyed/blob** — the v1 default, and the reason this
    is a two-field node rather than a bare ``entity_id``: partitioning an entity's
    memory per session/user/thread later becomes a value change (a non-null key)
    instead of a schema or traversal change (ADR-0028's open item).
    """

    entity_id: str
    memory_key: str | None = None


def accumulates(entity: Entity) -> bool:
    """Does *entity* retain its prior inbound payloads within a trace?

    THE accumulating-entity predicate (ADR-0028 D2). Driven by
    :data:`ACCUMULATING_KINDS` today because ``entities.kind`` is the only signal
    available; the eventual home is declared per-entity config, which this function
    would consult without any call site changing.
    """
    return entity.kind in ACCUMULATING_KINDS


def is_entity_source(entity: Entity) -> bool:
    """Is *entity* a **data source** — does it contribute content of its own?

    THE entity-source predicate (ADR-0028 D12), and the answer
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

    Of the taxonomy's other two columns, ``target`` now has :func:`is_entity_target`
    below (a read-path consumer, not an algebra one — ADR-0028 D14), while
    ``location`` (internal/external) remains a placeholder with no v1 semantics (D12).
    """
    return entity.kind in SOURCE_KINDS


def is_entity_target(entity: Entity) -> bool:
    """Is *entity* a **data target** — a place data flows *to*?

    THE entity-target predicate, driven by :data:`TARGET_KINDS` — the spec's kind
    defaults, since reading the declared taxonomy table is deferred
    (``data_lineage_alg.md:26-27``).

    **Read-path only.** This backs the trace-grain ``list destinations`` read
    (ADR-0028 D14) and is consulted by nothing in the algebra: no operation in
    :mod:`.operations` and no branch in :mod:`.traversal` asks whether an entity is a
    target. That is why D12's "``target`` is unread" still holds where it was
    written — it was a statement about the derivation, and the derivation has not
    changed.

    **Not the complement of :func:`is_entity_source`.** Source and target are two
    independent taxonomy columns, and an entity can be both: the spec's defaults make
    ``tool`` ✓ for each, because a tool legitimately both accepts data and returns
    freshly-read data. So this predicate must never be written as ``not
    is_entity_source(...)``, and the two sets sharing a value today is a coincidence
    of the defaults rather than a rule (see :data:`TARGET_KINDS`).
    """
    return entity.kind in TARGET_KINDS


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


# --- memory scope (label lineage) ---------------------------------------------

SCOPES = ("call", "conversation", "persistent")

# The scope of each **Entity** kind when nothing is declared. An LLM is call scope
# wherever it runs, recognised by its kind, never by a hostname. `service` is an
# external service that is not an LLM: it remembers what it was sent. `user` and
# `client` keep what the fold does with them today.
SCOPE_BY_KIND: Mapping[str, str] = {
    "llm": "call", "tool": "call", "user": "call", "client": "call",
    "agent": "conversation",
    "service": "persistent",
}

# The declaration: a JSON object, natural key -> scope. Read by the label-lineage
# processor at startup (``driver.configured``).
SCOPES_ENV_VAR = "LABEL_MEMORY_SCOPES"

NO_DECLARATION: Mapping[str, str] = {}   # never mutated: a default, read only


class InvalidScopes(ValueError):
    """``LABEL_MEMORY_SCOPES`` is not a JSON object of natural keys to scopes."""


def _no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    out: dict[str, object] = {}
    for key, value in pairs:
        if key in out:
            raise InvalidScopes(f"{SCOPES_ENV_VAR}: the key {key!r} is given twice in one object")
        out[key] = value
    return out


def parse_scopes(raw: str | None) -> dict[str, str]:
    """natural key -> declared scope, from a JSON object ``{"<natural_key>": "<scope>"}``
    (unset or blank: nothing declared). Not JSON this parser can read (nesting too deep
    or an integer too long included), not an object, an empty key, a value that is not a
    scope, or a key given twice (at any depth) raises; nothing falls back."""
    if not (raw or "").strip():
        return {}
    try:
        parsed = json.loads(raw, object_pairs_hook=_no_duplicates)
    except InvalidScopes:
        raise
    except ValueError as exc:   # JSONDecodeError, or an integer longer than the parser's digit limit
        raise InvalidScopes(f"{SCOPES_ENV_VAR} is not JSON this parser can read: {exc}") from None
    except RecursionError:
        raise InvalidScopes(f"{SCOPES_ENV_VAR} is not JSON this parser can read: nested too deeply") from None
    if not isinstance(parsed, dict):
        raise InvalidScopes(f"{SCOPES_ENV_VAR} is a JSON object of natural keys to scopes, got {raw.strip()!r}")
    for key, value in parsed.items():
        if not key:
            raise InvalidScopes(f"{SCOPES_ENV_VAR}: an empty natural key")
        if not isinstance(value, str) or value not in SCOPES:
            raise InvalidScopes(f"{SCOPES_ENV_VAR}: {key!r} declares {value!r}; a scope is one of {', '.join(SCOPES)}")
    return parsed


def scope(entity: Entity, declared: Mapping[str, str] = NO_DECLARATION) -> str:
    """The memory scope of *entity*: its declaration, else its kind's default. A kind
    outside the entity vocabulary raises (``entities.kind`` is a closed enum)."""
    if entity.natural_key in declared:
        return declared[entity.natural_key]
    try:
        return SCOPE_BY_KIND[entity.kind]
    except KeyError:
        raise ValueError(f"entity {entity.natural_key} has kind {entity.kind!r}, which has no memory scope") from None


def mixes_calls(entity: Entity, declared: Mapping[str, str] = NO_DECLARATION) -> bool:
    """Inside one conversation, does label lineage pool *entity*'s calls into one
    memory? What :func:`accumulates` says by kind, unless *entity* is declared
    ``call`` (per call) or ``conversation`` (pooled); ``persistent`` keeps the kind's
    answer here."""
    declared_scope = declared.get(entity.natural_key)
    if declared_scope in ("call", "conversation"):
        return declared_scope == "conversation"
    return accumulates(entity)
