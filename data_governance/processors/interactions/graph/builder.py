"""Graph construction for the p_interactions prototype. THROWAWAY.

Implements the algorithm described in docs/adr/0025-p-interactions-graph-algorithm.md.

**Architecture (per the human spec's "Note about architecture" — one module per
top-level step).** The implementation is split across per-top-level-step modules;
this file is now a thin RE-EXPORT FACADE so every symbol keeps resolving under
its historical `builder.<name>` path (extractor.py, cli.py, and the tests import
from here). The steps, and the modules that realise them:

  Step 1   — build the base graph: one node per span; one White directed
             parent→child edge per traceparent relationship
             (`step1_build_graph.py`).
  Step 2.a–2.d — enrich the execution-flow graph: transport/agentic coloring,
             inferred nodes/edges, and the Step 2.d node-and-edge merge
             (`step2_base_graph.py`).
  Step 3.a/3.b/3.d — build the entity graph: structural grouping + edges,
             and the semantic combine (3.d) (`step3_entity_graph.py`).

Cross-step helpers used by more than one step live in `_shared.py`.

Edge coloring is additive: an edge can carry multiple colors at once. The
underlying White connectivity is preserved when Blue/Teal are added on top.
"""

from __future__ import annotations

# --- Cross-step shared helpers (_shared.py) --------------------------------
from ._shared import (
    _CALL_ORDER,
    _INPUT_TOOL_BASE,
    _KIND_AGENT,
    _KIND_LLM,
    _KIND_TOOL,
    _OUTPUT_TOOL_BASE,
    _RESPONSE_ORDER,
    _ROLE_BOTH,
    _ROLE_SOURCE,
    _ROLE_TARGET,
    _insert_teal_server,
    _is_observed_transport,
    _is_server,
    _node_is_boundary,
    _node_kind,
    _node_role,
    _peer_match_key,
    _server_endpoints,
    _server_ids,
    _teal_ids,
    _white_blue_components,
    _white_blue_neighbors,
)

# --- Step 1 (step1_build_graph.py) -----------------------------------------
from .step1_build_graph import (
    _attrs,
    _scope_name,
    build_base_graph,
)

# --- Step 2.a–2.d (step2_base_graph.py) ------------------------------------
from .step2_base_graph import (
    _KIND_PREFIX,
    _absorb_inferred_call_into_observed_agent,
    _rewire_edges,
    _same_processing_signature,
    _typed_callee_key,
    _white_adjacency,
    color_agentic,
    color_transport,
    duplicate_combined_nodes,
    flag_between_boundaries,
    infer_agent_from_bare_leaf_llms,
    infer_tool_calls_from_attributes,
    merge_identical_interactions,
    synthesize_missing_peers,
)

# --- Step 3.a/3.b/3.d (step3_entity_graph.py) ------------------------------
from .step3_entity_graph import (
    _observed_transport_chains,
    build_entity_graph,
    combine_identical_entities,
)

__all__ = [
    # Step 1
    "build_base_graph",
    # Step 2
    "color_transport",
    "color_agentic",
    "duplicate_combined_nodes",
    "infer_tool_calls_from_attributes",
    "flag_between_boundaries",
    "synthesize_missing_peers",
    "infer_agent_from_bare_leaf_llms",
    "merge_identical_interactions",
    # Step 3
    "build_entity_graph",
    "combine_identical_entities",
]
