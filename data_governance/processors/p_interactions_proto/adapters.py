"""Per-(scope, framework) span adapters for the P-interactions graph prototype.

This module is the **single place** in the prototype where raw OTel attribute
keys are consulted. Everything else in the algorithm reads from the typed
`SpanFacts` value object that an adapter produces.

WHY THIS LAYER EXISTS
---------------------
Span attribute *keys* depend on three things:

  1. The OTel **scope** that emitted the span (e.g. `openinference.*`,
     `opentelemetry.instrumentation.starlette`, `a2a-python-sdk`). Different
     scopes carry different vocabularies entirely.
  2. The **framework** the scope wraps. The OpenInference scope alone covers
     OpenAI Agents SDK, Claude Agent SDK, LangChain, LiteLLM, etc. — each
     with different conventions for how span name, `tool.name`, `agent.name`,
     and combined-source-and-target spans are emitted. The framework appears
     as the third dotted segment of the scope name:

         openinference.instrumentation.openai_agents
         openinference.instrumentation.claude_agent_sdk
         openinference.instrumentation.langchain
         …

  3. The framework **version**. A future OpenAI-Agents bump may rename
     `llm.model_name` to `llm.model.name`; we want to absorb that without
     touching the algorithm. Adapters therefore receive both `framework` and
     `framework_version` and may switch lookup tables on version when they
     have to.

BEFORE this module existed, attribute keys were sprinkled across
`classifiers.py`, `builder.py`, and `extractor.py`. Every framework rename
required edits in three files; every new framework required adding branches
to attribute-extraction code that had no business knowing about the
distinction.

Now: ONE adapter per `(scope_root, framework)` pair (further split per
version when a vocabulary changes). Each adapter knows its framework's
attribute keys and produces a small, stable `SpanFacts` value object the
algorithm consumes.

WHAT THE ALGORITHM ACTUALLY NEEDS
----------------------------------
Per ADR-0007 the graph algorithm needs four things from a span:

  * Is this span a protocol boundary, and on which side (caller / callee /
    combined)? — Steps 2.a, 2.b.
  * If it is a boundary, what is the natural-key string for the entity
    behind it? (E.g. `tool:get_weather`, `llm:gpt-4o`.) — Step 2.c (inferred
    peer creation), Step 3.a phase 2 (peer combine).
  * If it is a boundary, what's a human-friendly label? — node display.
  * If it is a boundary, what are the request/response payloads? — payload
    extraction in `extractor._derive_interactions`.

That is the entire `SpanFacts` shape.

NOT IN SCOPE FOR THIS MODULE
----------------------------
* Non-agentic scopes (httpx, starlette, botocore, …) — they remain White
  in the base graph and contribute no entities at this stage. Adding them
  here would be premature: the cross-scope enrichment stage that consumes
  their attributes does not yet exist.
* Span IDs, parent IDs, traceparent edges — those are wiring, not facts.
"""

from __future__ import annotations

import dataclasses
from enum import Enum
from typing import Any, Callable, Protocol

from data_governance.retrieval import Span


# ---------------------------------------------------------------------------
# Stable vocabulary the algorithm reads
# ---------------------------------------------------------------------------


class Kind(str, Enum):
    """What the span is *about* — drives natural-key prefix and payload
    shape, NOT boundary-ness. Per ADR-0007 "Boundary promotion is
    role-driven, not kind-driven", the Step 2.a.3 promotion to Black
    reads `Role`, not `Kind`. A span can carry `Kind.AGENT` and
    `Role.NONE` (a wrapper / runner span); that node stays Gray.

    `OTHER` is the default for spans the adapter declines to classify
    (e.g. unknown framework version, internal SDK plumbing, lifecycle
    hooks, custom CHAIN spans, guardrail checks).
    """

    LLM = "LLM"
    TOOL = "TOOL"
    AGENT = "AGENT"
    OTHER = "OTHER"


class Role(str, Enum):
    """Boundary role — drives Step 2.a.3 Black promotion. The adapter is
    the single arbiter of role; the builder reads only this field.

    `SOURCE` — the span represents the caller side of an agentic
    protocol call. Carries enough evidence (target identity, request
    payload, or framework-specific span-name signal) to assert one side.
    `TARGET` — the span represents the callee side. (Reserved; current
    adapters emit only SOURCE and BOTH.)
    `BOTH` — combined source-and-target span (Step 2.b). One span
    carries both the outgoing request and the incoming response, e.g.
    `ClaudeAgentSDK.query`.
    `NONE` — not a boundary. Either non-agentic (`Kind.OTHER`) or an
    agentic wrapper that lacks specific call evidence (a top-level
    agent-run, per-activation `AgentSpanData`, runner span). The node
    stays Gray.

    The asymmetry with `Kind` is deliberate. `Kind.AGENT` covers both
    real agent-call spans and agent-run wrappers; only the adapter can
    tell them apart. Promoting on kind alone would inflate the entity
    graph with non-call edges (see ADR-0007 Key decisions).
    """

    SOURCE = "SOURCE"
    TARGET = "TARGET"
    BOTH = "BOTH"
    NONE = "NONE"


@dataclasses.dataclass(frozen=True)
class SpanFacts:
    """Framework-independent view of a span. Produced by exactly one
    adapter; consumed by classifiers, builder, and extractor.

    `kind` — what the span is about. Drives natural-key prefix and
    payload shape. Does NOT drive boundary-ness — see `role`.
    `role` — the span's boundary role. Drives Step 2.a.3 Black
    promotion. `Role.NONE` means the node stays Gray (non-agentic, or
    an agentic wrapper without specific call evidence).
    `is_combined` — true iff one span carries BOTH the source and the target
    side of the call (Step 2.b duplication trigger). Implies `role=BOTH`.
    `natural_key` — stable per-boundary identity used as the combine key in
    Step 3.a phase 2. Format: `tool:<n>`, `llm:<model>`, `agent:<n>`.
    None when no identifying attribute is present — Step 3.a phase 2 leaves
    keyless inferred peers distinct.
    `display_label` — human-friendly UI label. Free to be the framework's
    `service.name`, span name, or anything else readable.
    `target_label` — for combined spans only: label for the duplicated
    target node. Ignored when `is_combined` is False.
    `request_messages` / `response_messages` — LLM chat messages, normalised
    to a list of `{role, content, …}` dicts. None when not an LLM call or
    no messages were captured.
    `request_value` / `response_value` — opaque request/response payload
    (e.g. tool call arguments / result). None when not present.
    """

    kind: Kind
    role: Role = Role.NONE
    is_combined: bool = False
    natural_key: str | None = None
    display_label: str | None = None
    target_label: str | None = None
    request_messages: list[dict[str, Any]] | None = None
    response_messages: list[dict[str, Any]] | None = None
    request_value: Any = None
    response_value: Any = None


# Convenience: a SpanFacts that says "I have nothing to say about this span".
_NEUTRAL = SpanFacts(kind=Kind.OTHER, role=Role.NONE)


# ---------------------------------------------------------------------------
# Helpers used by every adapter
# ---------------------------------------------------------------------------


def _attr(span: Span, key: str) -> Any:
    """Single point where raw `span.attributes[key]` reads happen — adapters
    should call this rather than reaching into the dict directly, so that any
    future preprocessing (lowercasing, namespace stripping, …) lives in one
    place.
    """
    return (span.attributes or {}).get(key)


def _first_attr(span: Span, candidates: list[str]) -> Any:
    """Return the first present, non-None attribute among `candidates`. Used
    when a framework version may have renamed a key — the adapter lists every
    historical alias rather than scattering `or` chains across call sites.
    """
    attrs = span.attributes or {}
    for k in candidates:
        v = attrs.get(k)
        if v is not None:
            return v
    return None


def _service(span: Span) -> str | None:
    return span.service_name or None


def _scope_name(span: Span) -> str:
    return ((span.scope or {}).get("name") or "")


def _scope_version(span: Span) -> str | None:
    return (span.scope or {}).get("version") or None


def _strip_provider(model: Any) -> str | None:
    """Some frameworks emit `provider/model` (e.g. `anthropic/claude-3-7`);
    strip the provider so the model alone is the merge key.
    """
    if not isinstance(model, str) or not model:
        return None
    if "/" in model:
        return model.split("/", 1)[1] or None
    return model


def _extract_indexed_messages(span: Span, prefix: str) -> list[dict[str, Any]] | None:
    """Pivot OpenInference-style flat keys back into a list of message dicts.

    OpenInference encodes a list of messages as flat attribute keys:

        llm.input_messages.0.role     = "user"
        llm.input_messages.0.content  = "hi"
        llm.input_messages.1.role     = "assistant"

    This helper rebuilds `[{"role": "user", "content": "hi"}, {"role":
    "assistant", …}]`. Returns None when no matching keys exist.
    """
    attrs = span.attributes or {}
    msgs: dict[int, dict[str, Any]] = {}
    full_prefix = f"{prefix}."
    for key, value in attrs.items():
        if not key.startswith(full_prefix):
            continue
        rest = key[len(full_prefix):]
        parts = rest.split(".", 1)
        if len(parts) != 2 or not parts[0].isdigit():
            continue
        idx = int(parts[0])
        msgs.setdefault(idx, {})[parts[1]] = value
    if not msgs:
        return None
    return [msgs[i] for i in sorted(msgs)]


# ---------------------------------------------------------------------------
# Adapter Protocol
# ---------------------------------------------------------------------------


class SpanAdapter(Protocol):
    """An adapter knows how to extract `SpanFacts` from spans of one
    `(scope_root, framework)` pair. Versioned variants are dispatched inside
    the adapter via `_scope_version(span)` when needed.

    `documented_version` records the framework version(s) this adapter has
    been verified against (per the span-reference docs in the repo root).
    Comma-separated when more than one version has been profiled, e.g.
    `"1.4.1, 1.5.1"`. It's informational only — dispatch matches by
    `(scope_root, framework)` — but it lets a future maintainer compare an
    incoming span's `_scope_version(span)` against the versions we profiled
    and decide whether a key rename needs a version branch.
    """

    scope_root: str
    framework: str
    documented_version: str

    def extract(self, span: Span) -> SpanFacts: ...


# ---------------------------------------------------------------------------
# OpenInference adapters
# ---------------------------------------------------------------------------
#
# OpenInference is *one* OTel scope (`openinference.instrumentation.<fw>`)
# wrapping many frameworks. Each framework gets its own adapter below.
#
# Shared keys (verified against `openinference_telemetry_spans.md` for the
# packages listed there: openai_agents 1.5.1, claude_agent_sdk 0.1.5,
# google_adk 0.1.15, strands_agents 0.1.2; openai_agents *also* verified
# at 1.4.1 against `openinference_openai_agents_v1.4.1_telemetry_spans.md`):
#   openinference.span.kind         "LLM" | "TOOL" | "AGENT" | "CHAIN" | "GUARDRAIL"
#   llm.model_name                  e.g. "gpt-4o"
#   tool.name                       set on TOOL spans
#   agent.name                      set on AGENT spans (sub-agents, ADK)
#   input.value / output.value      raw request / response payloads
#   llm.input_messages.{i}.{field}  / llm.output_messages.{i}.{field}
#
# Speculative aliases (e.g. `gen_ai.request.model`) were removed: the doc
# does not list them and no observed trace uses them. If a future framework
# version renames a key, add it back to `_OI_ATTRS` and (if needed) branch
# in the framework's adapter on `_scope_version(span)`.
# ---------------------------------------------------------------------------


_OI_ATTRS: dict[str, list[str]] = {
    "kind":         ["openinference.span.kind"],
    "llm_model":    ["llm.model_name"],
    "tool_name":    ["tool.name"],
    "agent_name":   ["agent.name"],
    "input_value":  ["input.value"],
    "output_value": ["output.value"],
}
# Indexed prefixes (rebuilt into a list of dicts by `_extract_indexed_messages`).
_OI_INPUT_MSGS_PREFIX = "llm.input_messages"
_OI_OUTPUT_MSGS_PREFIX = "llm.output_messages"


# ---------------------------------------------------------------------------
# Versioned schema — two symmetric maps, both reading span-side → algorithm-
# side. Adapters that need to absorb per-version schema drift declare a
# `dict[version, _VersionedSchema]` and resolve at extract time.
#
# Two axes of drift are covered declaratively:
#
#   1. Kind-vocabulary changes — `kinds[raw_value] → Kind` maps the raw
#      string the framework emits at `openinference.span.kind` to our
#      internal boundary `Kind`. A version that introduces a new raw value
#      (e.g. 1.5.1's `"GUARDRAIL"`) adds an entry; raw values absent from
#      the map decode to `Kind.OTHER` ("better Gray than mis-Black").
#   2. Attribute renames — `fields[(kind, raw_attr_key)] → logical_field`
#      maps a (decoded kind, physical attribute key the framework emits)
#      pair to the logical field name the adapter consumes (`"model"`,
#      `"name"`, `"input_value"`, `"output_value"`). A version that renames
#      `llm.model_name → llm.model.name` adds one entry to the new
#      version's table; the consuming code does not change. Aliases — one
#      logical field with multiple physical sources during a transition —
#      are expressed as multiple entries with the same RHS.
#
# Both axes read uniformly: each table key is "what the span emits"; each
# value is "what the algorithm should treat it as." A reader scanning either
# table sees the framework's vocabulary directly.
#
# What this design DOES NOT cover (and why):
#
#   * Span-name conventions (handoff `"handoff to {x}"`, Claude SDK
#     `ClaudeAgentSDK.query`, sub-agent prefixes). These are not key→value
#     lookups but string-parsing decisions whose consequences are coupled
#     to behaviour (parsing the suffix, switching to an `agent:` natural-key,
#     emitting a combined span). They live in adapter code.
#   * Combined-span shape changes. Same reason: behavioural, not schema.
#
# The honest division of labour: schema drift in tables; behaviour drift in
# code. Hiding behaviour in tables would falsely advertise that the table
# covers everything.
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class _VersionedSchema:
    """One framework-version's schema for kind decoding and attribute lookup.

    `kinds` — raw string from `openinference.span.kind` → internal `Kind`.
    Raw values absent from the map decode to `Kind.OTHER`. The map IS the
    documentation of which raw values this version emits.

    `fields` — `(Kind, raw_attribute_key) → logical_field`. Both axes
    record what the span emits; the value is what the algorithm should
    treat it as. Logical fields are adapter-internal names (`"model"`,
    `"name"`, `"input_value"`, `"output_value"`). Aliases (one logical
    field with multiple physical sources during a transition) are
    expressed as multiple entries with the same RHS. Absence of any
    entry mapping to a logical field means the framework does not emit
    that field as an attribute (e.g. the agent name lives in the span
    name, not in any attribute).
    """

    kinds: dict[str, Kind]
    fields: dict[tuple[Kind, str], str]


def _resolve_schema(
    table: dict[str, _VersionedSchema], version: str | None
) -> _VersionedSchema:
    """Pick the schema entry matching `version`; fall back to the `"*"`
    entry if no exact match is found. Adapters must always include a `"*"`
    entry so unknown versions degrade gracefully.
    """
    if version is not None and version in table:
        return table[version]
    return table["*"]


def _schema_kind(schema: _VersionedSchema, span: Span) -> Kind:
    """Schema-aware variant of `_oi_kind`: decode using the version's
    kinds vocabulary."""
    raw = _first_attr(span, _OI_ATTRS["kind"])
    if not isinstance(raw, str):
        return Kind.OTHER
    return schema.kinds.get(raw, Kind.OTHER)


def _schema_field(schema: _VersionedSchema, span: Span, kind: Kind, field: str) -> Any:
    """Resolve a logical field for the given decoded kind by scanning the
    schema for any `(kind, raw_key) → field` entry whose `raw_key` is
    present on the span. Returns the first such value, or None if no entry
    matches or no matching key has a value.

    The scan is over a small per-kind subset (single-digit entries in
    practice); legibility wins over the O(1) inverted-map alternative.
    """
    attrs = span.attributes or {}
    for (entry_kind, raw_key), logical in schema.fields.items():
        if entry_kind is not kind or logical != field:
            continue
        value = attrs.get(raw_key)
        if value is not None:
            return value
    return None


def _oi_kind(span: Span) -> Kind:
    """Map `openinference.span.kind` to our boundary `Kind`.

    Per the openinference spec the value is one of LLM / TOOL / AGENT /
    CHAIN / GUARDRAIL. CHAIN (orchestration plumbing — runner spans,
    cycles, custom spans) and GUARDRAIL (in-process safety check) are
    NOT protocol boundaries → return OTHER. Unknown / missing values also
    return OTHER (better Gray than mis-Black).
    """
    raw = _first_attr(span, _OI_ATTRS["kind"])
    if raw == "LLM":
        return Kind.LLM
    if raw == "TOOL":
        return Kind.TOOL
    if raw == "AGENT":
        return Kind.AGENT
    return Kind.OTHER


def _oi_natural_key(span: Span, kind: Kind) -> str | None:
    """Build the merge-key string for an openinference boundary span.

    Per ADR-0007 the key is the originating boundary's identifying
    attribute, prefixed by kind. No service/host fallbacks (those would
    over-merge across distinct entities behind the same proxy).

    Framework-specific quirks (TOOL spans without `tool.name`, handoff
    span-name parsing, …) live in the per-framework adapters below; this
    helper is the straight-line case.
    """
    if kind is Kind.LLM:
        model = _strip_provider(_first_attr(span, _OI_ATTRS["llm_model"]))
        return f"llm:{model}" if model else None

    if kind is Kind.TOOL:
        tool = _first_attr(span, _OI_ATTRS["tool_name"])
        return f"tool:{tool}" if tool else None

    if kind is Kind.AGENT:
        agent = _first_attr(span, _OI_ATTRS["agent_name"])
        return f"agent:{agent}" if agent else None

    return None


def _oi_messages(span: Span) -> tuple[list[dict[str, Any]] | None, list[dict[str, Any]] | None]:
    return (
        _extract_indexed_messages(span, _OI_INPUT_MSGS_PREFIX),
        _extract_indexed_messages(span, _OI_OUTPUT_MSGS_PREFIX),
    )


def _oi_payload_values(span: Span) -> tuple[Any, Any]:
    return (
        _first_attr(span, _OI_ATTRS["input_value"]),
        _first_attr(span, _OI_ATTRS["output_value"]),
    )


def _oi_payloads_for_kind(span: Span, kind: Kind):
    """Return `(req_msgs, resp_msgs, req_value, resp_value)` for a boundary
    span. LLM boundaries use the indexed message prefixes; TOOL/AGENT use
    `input.value` / `output.value`. Centralised so each framework adapter
    doesn't reimplement the same branch.
    """
    req_msgs, resp_msgs = (None, None)
    req_value, resp_value = (None, None)
    if kind is Kind.LLM:
        req_msgs, resp_msgs = _oi_messages(span)
    elif kind is Kind.TOOL or kind is Kind.AGENT:
        req_value, resp_value = _oi_payload_values(span)
    return req_msgs, resp_msgs, req_value, resp_value


# --- Per-framework adapters ------------------------------------------------


_OPENAI_AGENTS_HANDOFF_PREFIX = "handoff to "


# Per-version schema for the openai_agents framework.
#
# 1.4.1 source: `openinference_openai_agents_v1.4.1_telemetry_spans.md`
#               (pinned commit `c1447128…`, the version that produced the
#               canonical live trace at `/trace/8ae1f64d…`).
# 1.5.1 source: cross-framework `openinference_telemetry_spans.md`.
#
# The two entries are intentionally near-duplicates today — they document
# that we have profiled both versions and confirmed each (kind, field) lookup
# is identical. The only material difference is `kinds`: 1.5.1 emits a
# distinct `"GUARDRAIL"` raw value where 1.4.1 emits `"CHAIN"` for the same
# span shape. Both currently decode to `Kind.OTHER`, so the algorithm sees
# them identically — but the divergence is captured declaratively, not in a
# code branch, so promoting GUARDRAIL to its own boundary kind would be a
# one-cell edit.
#
# `"*"` is the safe-default for unknown future versions: the broadest known
# vocabulary plus the current attribute keys.
_OPENAI_AGENTS_SCHEMAS: dict[str, _VersionedSchema] = {
    "1.4.1": _VersionedSchema(
        kinds={
            "LLM":   Kind.LLM,
            "TOOL":  Kind.TOOL,
            "AGENT": Kind.AGENT,
            "CHAIN": Kind.OTHER,  # incl. GuardrailSpanData in this version
        },
        fields={
            # (kind, raw attribute key the framework emits) → logical field
            (Kind.LLM,   "llm.model_name"): "model",
            (Kind.LLM,   "input.value"):    "input_value",
            (Kind.LLM,   "output.value"):   "output_value",
            (Kind.TOOL,  "tool.name"):      "name",
            (Kind.TOOL,  "input.value"):    "input_value",
            (Kind.TOOL,  "output.value"):   "output_value",
            (Kind.AGENT, "input.value"):    "input_value",
            (Kind.AGENT, "output.value"):   "output_value",
            # No `(Kind.AGENT, ?, "name")` entry: the agent name is the
            # span name in this framework, not an attribute. The adapter
            # falls back to `span.name` in code.
        },
    ),
    "1.5.1": _VersionedSchema(
        kinds={
            "LLM":       Kind.LLM,
            "TOOL":      Kind.TOOL,
            "AGENT":     Kind.AGENT,
            "CHAIN":     Kind.OTHER,
            "GUARDRAIL": Kind.OTHER,  # split out from CHAIN at this version
        },
        fields={
            (Kind.LLM,   "llm.model_name"): "model",
            (Kind.LLM,   "input.value"):    "input_value",
            (Kind.LLM,   "output.value"):   "output_value",
            (Kind.TOOL,  "tool.name"):      "name",
            (Kind.TOOL,  "input.value"):    "input_value",
            (Kind.TOOL,  "output.value"):   "output_value",
            (Kind.AGENT, "input.value"):    "input_value",
            (Kind.AGENT, "output.value"):   "output_value",
        },
    ),
}
_OPENAI_AGENTS_SCHEMAS["*"] = _OPENAI_AGENTS_SCHEMAS["1.5.1"]


@dataclasses.dataclass
class _OpenAIAgentsAdapter:
    """Adapter for `openinference.instrumentation.openai_agents`.

    Verified against two releases (see `_OPENAI_AGENTS_SCHEMAS` for the
    declarative per-version schema):

      * **1.4.1** — produced the canonical live trace at `/trace/8ae1f64d…`.
      * **1.5.1** — cross-framework reference doc.

    The (kind, field) schema is fully resolved per span via
    `_scope_version(span)`. Per-version drift in attribute names or in the
    kinds vocabulary is absorbed by adding a new entry to the schema map;
    no code change is required for those axes of change.

    What stays in code (genuinely behavioural, not schema):

      * Handoff spans are TOOL-kind with span name `"handoff to {target}"`
        and NO `tool.name`. The downstream agent merges these via
        `agent:<target>` parsed from the span name. The string prefix and
        the consequence (override the natural-key kind) are coupled.
      * AGENT spans (root trace span and per-activation `AgentSpanData`)
        do not emit an `agent.name` attribute; the agent's name IS the
        span name. The schema records this with an empty key list for
        `(AGENT, "name")` so the adapter falls back to the span name.
      * `mcp_list_tools` and `CustomSpanData` arrive as kind=CHAIN →
        OTHER (the schema's kinds map handles this).
      * No combined-span case in this framework.
    """

    scope_root: str = "openinference"
    framework: str = "openai_agents"
    documented_version: str = "1.4.1, 1.5.1"

    def extract(self, span: Span) -> SpanFacts:
        schema = _resolve_schema(_OPENAI_AGENTS_SCHEMAS, _scope_version(span))
        kind = _schema_kind(schema, span)
        if kind is Kind.OTHER:
            return SpanFacts(kind=Kind.OTHER, role=Role.NONE, display_label=_service(span))

        natural_key = self._natural_key(schema, span, kind)
        display = _service(span) or natural_key
        req_msgs, resp_msgs, req_value, resp_value = self._payloads(schema, span, kind)

        # Boundary role per ADR-0007 "Boundary promotion is role-driven".
        # LLM-kind: always SOURCE — the kind alone is sufficient call
        # evidence; empty payloads are an instrumentation gap, not absence
        # of a call.
        # TOOL-kind: SOURCE (regular tool call or handoff — both carry
        # target identity in the natural-key, plus input/output payloads).
        # AGENT-kind: NONE — openai_agents emits AGENT only on wrappers
        # (root trace span and per-activation `AgentSpanData`); the real
        # boundary is the more specific child LLM/TOOL span.
        if kind is Kind.LLM or kind is Kind.TOOL:
            role = Role.SOURCE
        else:
            role = Role.NONE

        return SpanFacts(
            kind=kind,
            role=role,
            is_combined=False,
            natural_key=natural_key,
            display_label=display,
            request_messages=req_msgs,
            response_messages=resp_msgs,
            request_value=req_value,
            response_value=resp_value,
        )

    def _natural_key(self, schema: _VersionedSchema, span: Span, kind: Kind) -> str | None:
        name = span.name or ""
        # Handoff TOOL span — name is "handoff to {target}", merges as agent.
        # This is span-name-driven dispatch, not a schema lookup: the prefix
        # string IS the routing signal, and recognising it switches the
        # natural-key prefix from `tool:` to `agent:`.
        if kind is Kind.TOOL and name.startswith(_OPENAI_AGENTS_HANDOFF_PREFIX):
            target = name[len(_OPENAI_AGENTS_HANDOFF_PREFIX):].strip()
            return f"agent:{target}" if target else None

        if kind is Kind.TOOL:
            tool = _schema_field(schema, span, kind, "name")
            if tool:
                return f"tool:{tool}"
            return f"tool:{name}" if name else None

        if kind is Kind.AGENT:
            # Schema records `(AGENT, "name") → []`; lookup returns None,
            # span name is the documented fallback in both versions.
            agent = _schema_field(schema, span, kind, "name") or name or None
            return f"agent:{agent}" if agent else None

        if kind is Kind.LLM:
            model = _strip_provider(_schema_field(schema, span, kind, "model"))
            return f"llm:{model}" if model else None

        return None

    def _payloads(self, schema: _VersionedSchema, span: Span, kind: Kind):
        """Return `(req_msgs, resp_msgs, req_value, resp_value)` resolved
        through the per-version schema. Indexed message prefixes are not
        currently version-keyed (both 1.4.1 and 1.5.1 use the same
        `llm.input_messages.*` / `llm.output_messages.*` shape); if a
        future version renames them, add `(LLM, "input_messages_prefix")`
        and `(LLM, "output_messages_prefix")` to the schema.
        """
        req_msgs, resp_msgs = (None, None)
        req_value, resp_value = (None, None)
        if kind is Kind.LLM:
            req_msgs, resp_msgs = _oi_messages(span)
        elif kind is Kind.TOOL or kind is Kind.AGENT:
            req_value = _schema_field(schema, span, kind, "input_value")
            resp_value = _schema_field(schema, span, kind, "output_value")
        return req_msgs, resp_msgs, req_value, resp_value


_CLAUDE_QUERY_NAMES = ("ClaudeAgentSDK.query", "ClaudeAgentSDK.ClaudeSDKClient.receive_response")
_CLAUDE_SUBAGENT_PREFIX = "ClaudeAgentSDK."  # ClaudeAgentSDK.{tool_name} | ClaudeAgentSDK.Subagent


@dataclasses.dataclass
class _ClaudeAgentSDKAdapter:
    """Adapter for `openinference.instrumentation.claude_agent_sdk`.

    Per `openinference_telemetry_spans.md` (package 0.1.5) this framework
    emits four span shapes:

      * `ClaudeAgentSDK.query` — AGENT kind. Wraps an entire async query
        from the user-process side; carries `llm.model_name` and
        `llm.output_messages.*`. The agent (source) is the local process;
        the target is the remote Claude API. Modelled here as a **combined
        source-and-target** span: kind=AGENT, is_combined=True,
        target_label=`llm:<model>`. Step 2.b duplicates so the entity graph
        carries both the agent and the LLM endpoints.
      * `ClaudeAgentSDK.ClaudeSDKClient.receive_response` — same shape as
        above, per-turn for stateful clients.
      * `ClaudeAgentSDK.{tool_name}` / `ClaudeAgentSDK.Subagent` —
        AGENT kind, tool / sub-agent dispatch. `agent.name` carries the
        dispatched target's name (the span-name suffix is the fallback).
        A SOURCE boundary (ADR-0007 Step 2.b case 2): the dispatched
        target emits no observed span, so Step 2.b's one-sided stubbing
        infers the target peer keyed on `natural_key`. Sub-agent and
        local tool take the same path — the suffix is the peer identity
        either way. NOT combined: only the dispatch side is on this span.
      * `{tool_name}` (raw) — TOOL kind, local tool invocation.
        `tool.name` IS present, so the natural-key is `tool:<tool.name>`
        directly.
    """

    scope_root: str = "openinference"
    framework: str = "claude_agent_sdk"
    documented_version: str = "0.1.5"

    def extract(self, span: Span) -> SpanFacts:
        name = span.name or ""
        kind = _oi_kind(span)

        # Combined agent-to-LLM span. Source = local agent process; Target
        # = the remote LLM identified by `llm.model_name`. role=BOTH.
        if name in _CLAUDE_QUERY_NAMES:
            model = _strip_provider(_first_attr(span, _OI_ATTRS["llm_model"]))
            target_label = f"llm:{model}" if model else "llm:?"
            req_msgs, resp_msgs = _oi_messages(span)
            req_value, resp_value = _oi_payload_values(span)
            return SpanFacts(
                kind=kind if kind is not Kind.OTHER else Kind.AGENT,
                role=Role.BOTH,
                is_combined=True,
                # No natural-key on the source side: the local agent process
                # is identified by service.name in the entity-graph stage,
                # not by an openinference attribute.
                natural_key=None,
                display_label=_service(span),
                target_label=target_label,
                request_messages=req_msgs,
                response_messages=resp_msgs,
                request_value=req_value,
                response_value=resp_value,
            )

        if kind is Kind.OTHER:
            return SpanFacts(kind=Kind.OTHER, role=Role.NONE, display_label=_service(span))

        # Tool / sub-agent dispatch: ClaudeAgentSDK.{tool_name} | Subagent.
        # ADR-0007 Step 2.b case 2: the span name carries the dispatched
        # target's name and kind=AGENT, but the target emits no observed
        # span of its own. Treat it as a SOURCE boundary keyed on
        # `agent:<name>`; Step 2.b's one-sided stubbing then synthesizes
        # the inferred target peer and its bidirectional Black edges.
        # Sub-agent vs. local tool take the same path — the suffix is the
        # peer identity in both cases.
        if kind is Kind.AGENT and name.startswith(_CLAUDE_SUBAGENT_PREFIX):
            agent = _first_attr(span, _OI_ATTRS["agent_name"])
            if not agent:
                suffix = name[len(_CLAUDE_SUBAGENT_PREFIX):]
                agent = suffix or None
            natural_key = f"agent:{agent}" if agent else None
            req_msgs, resp_msgs, req_value, resp_value = _oi_payloads_for_kind(span, kind)
            return SpanFacts(
                kind=kind,
                role=Role.SOURCE,
                is_combined=False,
                natural_key=natural_key,
                display_label=_service(span) or natural_key,
                request_messages=req_msgs,
                response_messages=resp_msgs,
                request_value=req_value,
                response_value=resp_value,
            )

        # Local TOOL invocation — `tool.name` is present per the doc.
        # role=SOURCE: target identity (tool.name) and payloads present.
        natural_key = _oi_natural_key(span, kind)
        display = _service(span) or natural_key
        req_msgs, resp_msgs, req_value, resp_value = _oi_payloads_for_kind(span, kind)
        # Bare AGENT-kind wrappers (if any reach here) stay Gray; only
        # LLM/TOOL with kind-decoded role are real boundaries on this path.
        if kind is Kind.LLM or kind is Kind.TOOL:
            role = Role.SOURCE
        else:
            role = Role.NONE
        return SpanFacts(
            kind=kind,
            role=role,
            is_combined=False,
            natural_key=natural_key,
            display_label=display,
            request_messages=req_msgs,
            response_messages=resp_msgs,
            request_value=req_value,
            response_value=resp_value,
        )


@dataclasses.dataclass
class _GoogleADKAdapter:
    """Adapter for `openinference.instrumentation.google_adk` (package 0.1.15).

    Per the doc:
      * `"invocation [{app_name}]"` — CHAIN kind → OTHER (not a boundary).
      * `"agent_run [{agent.name}]"` — AGENT kind. `agent.name` is set as
        an attribute → standard OI agent-key.
      * LLM span — augmented in place by ADK; `llm.model_name` set.
      * TOOL span — augmented in place; `tool.name` set.
    No combined spans; the standard OI helpers cover everything.
    """

    scope_root: str = "openinference"
    framework: str = "google_adk"
    documented_version: str = "0.1.15"

    def extract(self, span: Span) -> SpanFacts:
        return _generic_oi_extract(span)


@dataclasses.dataclass
class _StrandsAgentsAdapter:
    """Adapter for `openinference.instrumentation.strands_agents` (0.1.2).

    Per the doc the StrandsAgentsToOpenInferenceProcessor rewrites Strands
    GenAI spans into OpenInference shape:
      * `invoke_agent` — AGENT (top-level run; not a protocol boundary
        per the doc's Send/Receive table, but the agent boundary itself).
        `llm.model_name` is set; we still use `agent:<name>` if `agent.name`
        is present.
      * `chat` — LLM. `llm.model_name` set.
      * `execute_tool {tool_name}` — TOOL. `tool.name` set.
      * `execute_event_loop_cycle` — CHAIN → OTHER.
    Standard OI helpers apply; no combined spans.
    """

    scope_root: str = "openinference"
    framework: str = "strands_agents"
    documented_version: str = "0.1.2"

    def extract(self, span: Span) -> SpanFacts:
        return _generic_oi_extract(span)


@dataclasses.dataclass
class _MCPAdapter:
    """Adapter for `openinference.instrumentation.mcp` (2.0.3).

    Per the doc the MCP instrumentor emits **no application spans** — it
    only injects/extracts W3C `traceparent` headers across MCP transports.
    Returning OTHER for every span keeps these scopes White-but-agentic in
    the algorithm's view (i.e. the dispatch recognises the scope so future
    enrichment can find them, but no boundary is asserted here).
    """

    scope_root: str = "openinference"
    framework: str = "mcp"
    documented_version: str = "2.0.3"

    def extract(self, span: Span) -> SpanFacts:
        return SpanFacts(kind=Kind.OTHER, display_label=_service(span))


def _generic_oi_extract(span: Span) -> SpanFacts:
    """Straight-line OpenInference extraction: no combined spans, no
    framework-specific span-name parsing. Used by Google ADK, Strands
    Agents, and the `*` fallback for unprofiled frameworks (LangChain,
    LiteLLM, Haystack, …).

    Boundary role per ADR-0007: LLM and TOOL kinds map to `Role.SOURCE`
    (LLM-kind alone is sufficient call evidence; TOOL-kind carries
    target identity in `tool.name` and payloads in `input.value` /
    `output.value`). AGENT-kind wrappers map to `Role.NONE` — without
    framework-specific knowledge we can't tell a real agent-call span
    from a top-level run wrapper, and the conservative default is to
    stay Gray. Frameworks that emit AGENT-kind real boundaries will
    need a dedicated adapter (or version-specific schema entry) that
    overrides this default.
    """
    kind = _oi_kind(span)
    if kind is Kind.OTHER:
        return SpanFacts(kind=Kind.OTHER, role=Role.NONE, display_label=_service(span))

    natural_key = _oi_natural_key(span, kind)
    display = _service(span) or natural_key
    req_msgs, resp_msgs, req_value, resp_value = _oi_payloads_for_kind(span, kind)

    if kind is Kind.LLM or kind is Kind.TOOL:
        role = Role.SOURCE
    else:
        role = Role.NONE

    return SpanFacts(
        kind=kind,
        role=role,
        is_combined=False,
        natural_key=natural_key,
        display_label=display,
        request_messages=req_msgs,
        response_messages=resp_msgs,
        request_value=req_value,
        response_value=resp_value,
    )


@dataclasses.dataclass
class _GenericOpenInferenceAdapter:
    """Fallback adapter for openinference frameworks we haven't profiled
    yet (LangChain, LiteLLM, Haystack, …). Uses the shared `_OI_ATTRS`
    table only — no per-framework span-name detection. Safe-by-default:
    if a framework emits combined spans we don't recognise, those spans
    will just be classified as one-sided boundaries; Step 2.c will
    synthesize the missing peer.

    Add a dedicated adapter above when a new framework needs combined-span
    handling, custom natural-key logic, or version-specific aliases.
    """

    scope_root: str = "openinference"
    framework: str = "*"
    # No specific framework profiled; the table this adapter relies on is
    # the openinference cross-framework spec, not a particular release.
    documented_version: str = "*"

    def extract(self, span: Span) -> SpanFacts:
        return _generic_oi_extract(span)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
#
# Registry: scope_root → framework → adapter. The framework key `*` is a
# fallback used when we recognise the scope but not the specific framework.
# Each adapter is stateless, so single instances are fine.
# ---------------------------------------------------------------------------


# One adapter per documented openinference framework
# (`openinference_telemetry_spans.md`); `*` is the safe-default fallback for
# any framework not yet profiled. Adding a new framework: write an adapter
# class, drop it in this dict.
_OPENINFERENCE_ADAPTERS: dict[str, SpanAdapter] = {
    "openai_agents":    _OpenAIAgentsAdapter(),
    "claude_agent_sdk": _ClaudeAgentSDKAdapter(),
    "google_adk":       _GoogleADKAdapter(),
    "strands_agents":   _StrandsAgentsAdapter(),
    "mcp":              _MCPAdapter(),
    "*":                _GenericOpenInferenceAdapter(),
}


_REGISTRY: dict[str, dict[str, SpanAdapter]] = {
    "openinference": _OPENINFERENCE_ADAPTERS,
}


def _parse_scope(scope_name: str) -> tuple[str | None, str | None]:
    """Split `openinference.instrumentation.openai_agents` into
    (`openinference`, `openai_agents`). Returns (None, None) for non-agentic
    scopes.
    """
    if scope_name.startswith("openinference.instrumentation."):
        framework = scope_name[len("openinference.instrumentation."):] or None
        return ("openinference", framework)
    # Other agentic scopes (a2a, mcp) deferred per ADR-0007.
    return (None, None)


def is_agentic_scope(scope_name: str) -> bool:
    root, _ = _parse_scope(scope_name)
    return root is not None


def get_adapter(span: Span) -> SpanAdapter | None:
    """Return the adapter for this span, or None if no agentic adapter
    matches. Callers that get None should treat the span as non-agentic
    (White / no boundary)."""
    root, framework = _parse_scope(_scope_name(span))
    if root is None:
        return None
    by_fw = _REGISTRY.get(root)
    if by_fw is None:
        return None
    if framework and framework in by_fw:
        return by_fw[framework]
    return by_fw.get("*")


def extract_facts(span: Span) -> SpanFacts:
    """Convenience: dispatch and extract in one call. Returns the neutral
    `OTHER`/no-label `SpanFacts` when no adapter matches.
    """
    adapter = get_adapter(span)
    if adapter is None:
        return _NEUTRAL
    return adapter.extract(span)


# ---------------------------------------------------------------------------
# Payload shapes
# ---------------------------------------------------------------------------
#
# The extractor materialises request/response payload rows from `SpanFacts`.
# The mapping from boundary `Kind` to (content_kind tag, payload value) lives
# here so the extractor neither dispatches on the natural-key prefix string
# nor re-decides what an LLM vs. tool payload looks like.
# ---------------------------------------------------------------------------


def payload_shapes_for_facts(
    facts: SpanFacts,
) -> tuple[tuple[str, Any] | None, tuple[str, Any] | None]:
    """Return `((req_content_kind, req_content), (resp_content_kind, resp_content))`
    for the given facts. Either side is None when the corresponding payload
    field is absent. Returns `(None, None)` for non-boundary facts.
    """
    if facts.kind is Kind.LLM:
        req = (
            ("llm_chat_prompt", {"messages": facts.request_messages})
            if facts.request_messages is not None
            else None
        )
        resp = (
            ("llm_completion", {"messages": facts.response_messages})
            if facts.response_messages is not None
            else None
        )
        return req, resp
    if facts.kind is Kind.TOOL or facts.kind is Kind.AGENT:
        req = (
            ("tool_call_arguments", facts.request_value)
            if facts.request_value is not None
            else None
        )
        resp = (
            ("tool_call_result", facts.response_value)
            if facts.response_value is not None
            else None
        )
        return req, resp
    return None, None
