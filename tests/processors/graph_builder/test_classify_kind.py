"""Unit tests for the span->entity/edge classifier (pure functions, no DB)."""

from __future__ import annotations

from data_governance.processors.graph_builder import classify_kind as ck


class TestSemanticKind:
    def test_openinference_kind_wins(self) -> None:
        # Rung 1 beats everything below it, even when llm.* is also present.
        attrs = {"openinference.span.kind": "LLM", "llm.model_name": "gpt-4"}
        assert ck.semantic_kind(attrs, "INTERNAL") == "LLM"

    def test_openinference_value_is_upper_cased(self) -> None:
        assert ck.semantic_kind({"openinference.span.kind": "tool"}, None) == "TOOL"

    def test_openinference_passes_through_arbitrary_kinds(self) -> None:
        # The ladder does not restrict rung 1 to a fixed vocabulary.
        assert ck.semantic_kind({"openinference.span.kind": "RETRIEVER"}, None) == "RETRIEVER"

    def test_llm_star_rung(self) -> None:
        # Rung 2: the post-collector-transform signal.
        assert ck.semantic_kind({"llm.token_count.total": 10}, "INTERNAL") == "LLM"

    def test_gen_ai_star_fallback_rung(self) -> None:
        # Rung 3: only when no llm.* survived (un-transformed source).
        assert ck.semantic_kind({"gen_ai.request.model": "x"}, "INTERNAL") == "LLM"

    def test_otlp_boundary_kinds(self) -> None:
        for k in ("SERVER", "CLIENT", "PRODUCER", "CONSUMER"):
            assert ck.semantic_kind({}, k) == k

    def test_internal_otlp_kind_is_unknown(self) -> None:
        # INTERNAL is not a graph boundary on its own.
        assert ck.semantic_kind({}, "INTERNAL") == "UNKNOWN"

    def test_no_signal_is_unknown(self) -> None:
        assert ck.semantic_kind({}, None) == "UNKNOWN"


class TestSubKind:
    def test_llm_sub_kind_is_model_name(self) -> None:
        assert ck.sub_kind("LLM", {"llm.model_name": "claude-haiku"}) == "claude-haiku"

    def test_llm_sub_kind_falls_back_to_gen_ai_model(self) -> None:
        assert ck.sub_kind("LLM", {"gen_ai.request.model": "gpt-4"}) == "gpt-4"

    def test_tool_sub_kind_is_tool_name(self) -> None:
        assert ck.sub_kind("TOOL", {"tool.name": "charge_card"}) == "charge_card"

    def test_other_kinds_have_no_sub_kind(self) -> None:
        assert ck.sub_kind("AGENT", {"tool.name": "x"}) is None
        assert ck.sub_kind("CHAIN", {}) is None

    def test_missing_discriminator_is_none(self) -> None:
        assert ck.sub_kind("LLM", {}) is None
        assert ck.sub_kind("TOOL", {}) is None


class TestEntityId:
    def test_is_deterministic(self) -> None:
        assert ck.entity_id("svc", "LLM", "gpt-4") == ck.entity_id("svc", "LLM", "gpt-4")

    def test_distinct_tuples_distinct_keys(self) -> None:
        a = ck.entity_id("svc", "LLM", "gpt-4")
        b = ck.entity_id("svc", "LLM", "claude-3")
        c = ck.entity_id("svc2", "LLM", "gpt-4")
        assert len({a, b, c}) == 3

    def test_null_component_distinct_from_present(self) -> None:
        # A NULL sub_kind must not collide with any present sub_kind.
        assert ck.entity_id("svc", "TOOL", None) != ck.entity_id("svc", "TOOL", "")
        assert ck.entity_id(None, "UNKNOWN", None) != ck.entity_id("", "UNKNOWN", None)


class TestEdgeKind:
    def test_resolved_boundary(self) -> None:
        assert ck.edge_kind("AGENT", "LLM") == "AGENT_LLM"
        assert ck.edge_kind("CLIENT", "SERVER") == "CLIENT_SERVER"

    def test_orphan_boundary(self) -> None:
        # from_kind None -> the parent entity is unknown.
        assert ck.edge_kind(None, "LLM") == "UNKNOWN_LLM"


class TestDisplayName:
    def test_includes_sub_kind_when_present(self) -> None:
        assert ck.display_name("svc", "LLM", "gpt-4") == "svc · LLM · gpt-4"

    def test_omits_sub_kind_when_absent(self) -> None:
        assert ck.display_name("svc", "AGENT", None) == "svc · AGENT"

    def test_handles_null_service(self) -> None:
        assert ck.display_name(None, "UNKNOWN", None) == "(no service) · UNKNOWN"
