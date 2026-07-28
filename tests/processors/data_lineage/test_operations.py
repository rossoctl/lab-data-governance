"""The three data-lineage operations, as pure functions (issue #117).

These are the algebra from ``docs/data_lineage_alg.md`` ("Lineage processing
operations") — ``init_lineage``, ``linear_lineage``, ``merge_lineage``. They are
tested with no database and no trace: metadata in, metadata out, with the matcher
injected. Every assertion below traces to a numbered rule in the spec, cited per
test, because the spec is human-owned and authoritative.

NOTE on naming: "lineage" in ``processors/interactions`` means **span** lineage
(a span's ancestors ∪ subtree). This is **data lineage** — where a payload came
from. Unrelated concepts.
"""

from __future__ import annotations

from data_governance.matching import MatchResult, Transformation
from data_governance.processors.data_lineage.operations import (
    DataLineage,
    init_lineage,
    linear_lineage,
    merge_lineage,
)


# --- matcher stubs -----------------------------------------------------------


def _never(payload_a: object, payload_b: object, /) -> MatchResult:
    """A matcher that refuses to match — the D3(2) degrade-to-init probe."""
    return MatchResult(matched=False)


def _always(transformation: Transformation | None = None):
    def _match(payload_a: object, payload_b: object, /) -> MatchResult:
        return MatchResult(matched=True, transformation=transformation)

    return _match


def _per_payload(table: dict[object, MatchResult]):
    """Match verdict chosen by the *input* payload — lets one merge call get a
    different answer per source, as in the spec's Examples 1 and 2."""

    def _match(payload_a: object, payload_b: object, /) -> MatchResult:
        return table[payload_a]

    return _match


# --- init_lineage ------------------------------------------------------------


def test_init_metadata_is_trivial_and_rooted_at_the_entity() -> None:
    """Spec rule 1: the data source is the entity name, the map has that one key
    against an EMPTY transformation set, and the entity list is empty."""
    md = init_lineage("agent:(demo,travel-advisor)")

    assert md.data_sources == frozenset({"agent:(demo,travel-advisor)"})
    assert md.source_transformations == {"agent:(demo,travel-advisor)": frozenset()}
    assert md.entity_path == ()


def test_init_entity_path_is_empty_not_the_entity() -> None:
    """The spec is explicit: "A new list of entities which is empty" — the
    originating entity is the *source*, it is not yet something the data passed
    *through*. Guarding this because "root it at the entity" invites putting the
    entity in both places."""
    assert init_lineage("user:alice").entity_path == ()


# --- linear_lineage ----------------------------------------------------------


def test_linear_on_match_inherits_sources_and_extends_the_entity_path() -> None:
    """Spec rule 2, matched branch: sources are the input's (1), transformation
    sets copied (2), entity list copied and extended with the entity (3)."""
    origin = init_lineage("user:alice")
    md = linear_lineage(
        "in", origin, "out", "llm:api.openai.com/gpt-4", matcher=_always()
    )

    assert md.data_sources == frozenset({"user:alice"})
    assert md.source_transformations == {"user:alice": frozenset()}
    assert md.entity_path == ("llm:api.openai.com/gpt-4",)


def test_linear_adds_the_returned_transformation_to_all_sets() -> None:
    """Spec rule 2(2): "add the returned transformation (if exists) to **all**
    the transformation sets"."""
    upstream = DataLineage(
        data_sources=frozenset({"user:alice", "tool:db"}),
        source_transformations={
            "user:alice": frozenset(),
            "tool:db": frozenset({Transformation.ANONYMIZATION}),
        },
        entity_path=("agent:a",),
    )
    md = linear_lineage(
        "in", upstream, "out", "llm:x", matcher=_always(Transformation.SUMMARIZATION)
    )

    assert md.source_transformations == {
        "user:alice": frozenset({Transformation.SUMMARIZATION}),
        "tool:db": frozenset(
            {Transformation.ANONYMIZATION, Transformation.SUMMARIZATION}
        ),
    }


def test_linear_with_no_transformation_leaves_the_sets_untouched() -> None:
    """Spec rule 2(2) "(if exists)": the trivial matcher reports ``None``, which
    must not become a member of the set (nor a literal ``None`` entry)."""
    upstream = DataLineage(
        data_sources=frozenset({"user:alice"}),
        source_transformations={"user:alice": frozenset({Transformation.ANONYMIZATION})},
        entity_path=("agent:a",),
    )
    md = linear_lineage("in", upstream, "out", "llm:x", matcher=_always(None))

    assert md.source_transformations == {
        "user:alice": frozenset({Transformation.ANONYMIZATION})
    }


def test_linear_degrades_to_init_when_the_matcher_refuses() -> None:
    """Spec rule 2, false branch ("There is no lineage ... call init lineage") =
    ADR-0027 D3(2). The upstream metadata is discarded entirely; the output is a
    fresh origin rooted at the processing entity."""
    upstream = DataLineage(
        data_sources=frozenset({"user:alice"}),
        source_transformations={"user:alice": frozenset({Transformation.SUMMARIZATION})},
        entity_path=("agent:a", "llm:x"),
    )
    md = linear_lineage("in", upstream, "out", "tool:anonymizer", matcher=_never)

    assert md == init_lineage("tool:anonymizer")
    assert md.data_sources == frozenset({"tool:anonymizer"})
    assert md.entity_path == ()


def test_linear_does_not_mutate_its_input() -> None:
    """Spec rule 2 says "create a **copy**" twice. The upstream metadata belongs
    to another leg's persisted row; mutating it would corrupt that leg."""
    upstream = init_lineage("user:alice")
    before = (upstream.data_sources, dict(upstream.source_transformations), upstream.entity_path)

    linear_lineage(
        "in", upstream, "out", "llm:x", matcher=_always(Transformation.SUMMARIZATION)
    )

    assert (
        upstream.data_sources,
        dict(upstream.source_transformations),
        upstream.entity_path,
    ) == before


# --- merge_lineage -----------------------------------------------------------


def test_merge_unions_sources_maps_and_paths() -> None:
    """Spec rule 3, trivial form: sources are the union (1), maps merged (2),
    entity lists merged and extended with the entity (3)."""
    a = DataLineage(
        data_sources=frozenset({"user:alice"}),
        source_transformations={"user:alice": frozenset()},
        entity_path=("agent:a",),
    )
    b = DataLineage(
        data_sources=frozenset({"tool:db"}),
        source_transformations={"tool:db": frozenset()},
        entity_path=("llm:x",),
    )
    md = merge_lineage([("pa", a), ("pb", b)], "out", "agent:a", matcher=_always())

    assert md.data_sources == frozenset({"user:alice", "tool:db"})
    assert md.source_transformations == {
        "user:alice": frozenset(),
        "tool:db": frozenset(),
    }
    # Both branches' entities, plus the processing entity. `agent:a` is already on
    # branch a's path (the data passed through it earlier), so it keeps its
    # first-arrival position rather than being re-appended at the end — see
    # ``test_merge_entity_path_dedupes_while_keeping_first_arrival_order``.
    assert md.entity_path == ("agent:a", "llm:x")


def test_merge_merges_transformation_sets_on_a_key_collision() -> None:
    """Spec rule 3(2): "merge the transformation sets in case a key appears
    twice" — and Example 2(2)'s "either by adding a new data source key or
    merging the transformation set with the existing one". Both inputs carry
    ``user:alice``, each with a different transformation, and each input's match
    contributes its own on top."""
    a = DataLineage(
        data_sources=frozenset({"user:alice"}),
        source_transformations={"user:alice": frozenset({Transformation.ANONYMIZATION})},
        entity_path=(),
    )
    b = DataLineage(
        data_sources=frozenset({"user:alice"}),
        source_transformations={"user:alice": frozenset({Transformation.SUMMARIZATION})},
        entity_path=(),
    )
    md = merge_lineage([("pa", a), ("pb", b)], "out", "agent:a", matcher=_always())

    assert md.data_sources == frozenset({"user:alice"})
    assert md.source_transformations == {
        "user:alice": frozenset(
            {Transformation.ANONYMIZATION, Transformation.SUMMARIZATION}
        )
    }


def test_merge_spec_example_1_drops_the_unmatched_source() -> None:
    """Spec Example 1 verbatim: match false for payload_a, true+summarization for
    payload_b. Output = metadata_b's sources only (1), each of b's entries with
    summarization added (2), b's entity list extended with the entity (3)."""
    a = DataLineage(
        data_sources=frozenset({"user:alice"}),
        source_transformations={"user:alice": frozenset()},
        entity_path=("agent:a",),
    )
    b = DataLineage(
        data_sources=frozenset({"tool:db"}),
        source_transformations={"tool:db": frozenset({Transformation.ANONYMIZATION})},
        entity_path=("tool:db_reader",),
    )
    matcher = _per_payload(
        {
            "pa": MatchResult(matched=False),
            "pb": MatchResult(
                matched=True, transformation=Transformation.SUMMARIZATION
            ),
        }
    )
    md = merge_lineage([("pa", a), ("pb", b)], "out", "llm:x", matcher=matcher)

    assert md.data_sources == frozenset({"tool:db"}), "metadata_b's sources only"
    assert md.source_transformations == {
        "tool:db": frozenset(
            {Transformation.ANONYMIZATION, Transformation.SUMMARIZATION}
        )
    }
    assert md.entity_path == ("tool:db_reader", "llm:x")
    assert "user:alice" not in md.data_sources, "an unmatched source contributes nothing"
    assert "agent:a" not in md.entity_path, "nor does its entity path"


def test_merge_spec_example_2_three_inputs_two_matching() -> None:
    """Spec Example 2 verbatim: false for A, true+anonymize for B, true+summarize
    for C. Sources = B ∪ C; B's sets get anonymization, C's get summarization,
    colliding keys merge; entity paths of B and C merged and extended."""
    a = DataLineage(
        data_sources=frozenset({"src_a"}),
        source_transformations={"src_a": frozenset()},
        entity_path=("e_a",),
    )
    b = DataLineage(
        data_sources=frozenset({"src_b", "shared"}),
        source_transformations={"src_b": frozenset(), "shared": frozenset()},
        entity_path=("e_b",),
    )
    c = DataLineage(
        data_sources=frozenset({"src_c", "shared"}),
        source_transformations={"src_c": frozenset(), "shared": frozenset()},
        entity_path=("e_c",),
    )
    matcher = _per_payload(
        {
            "pa": MatchResult(matched=False),
            "pb": MatchResult(
                matched=True, transformation=Transformation.ANONYMIZATION
            ),
            "pc": MatchResult(
                matched=True, transformation=Transformation.SUMMARIZATION
            ),
        }
    )
    md = merge_lineage(
        [("pa", a), ("pb", b), ("pc", c)], "out", "agent:a", matcher=matcher
    )

    assert md.data_sources == frozenset({"src_b", "shared", "src_c"})
    assert md.source_transformations == {
        "src_b": frozenset({Transformation.ANONYMIZATION}),
        "src_c": frozenset({Transformation.SUMMARIZATION}),
        # `shared` appears in BOTH B and C, so it collects both transformations.
        "shared": frozenset(
            {Transformation.ANONYMIZATION, Transformation.SUMMARIZATION}
        ),
    }
    assert set(md.entity_path) == {"e_b", "e_c", "agent:a"}
    assert md.entity_path[-1] == "agent:a"
    assert "src_a" not in md.data_sources
    assert "e_a" not in md.entity_path


def test_merge_with_no_matching_source_degrades_to_init() -> None:
    """ADR-0027 D3(2): "if no source matches the op degrades to ``init``". A
    runtime *result*, not a selection branch — merge was still chosen, it simply
    found nothing to inherit."""
    a = init_lineage("user:alice")
    b = init_lineage("tool:db")
    md = merge_lineage([("pa", a), ("pb", b)], "out", "tool:anonymizer", matcher=_never)

    assert md == init_lineage("tool:anonymizer")


def test_merge_entity_path_dedupes_while_keeping_first_arrival_order() -> None:
    """Merging two paths that share a prefix must not repeat entities. The path
    is "which entities did the data pass through" — a set-with-order, not a
    visit log, so an entity appearing on both branches appears once."""
    a = DataLineage(
        data_sources=frozenset({"s1"}),
        source_transformations={"s1": frozenset()},
        entity_path=("user:alice", "agent:a"),
    )
    b = DataLineage(
        data_sources=frozenset({"s2"}),
        source_transformations={"s2": frozenset()},
        entity_path=("user:alice", "llm:x"),
    )
    md = merge_lineage([("pa", a), ("pb", b)], "out", "agent:a", matcher=_always())

    assert md.entity_path == ("user:alice", "agent:a", "llm:x")


def test_merge_of_a_single_input_matches_linear() -> None:
    """Consistency check on the algebra: merge over one matching input produces
    exactly what linear does. The ops differ in arity, not in semantics."""
    upstream = init_lineage("user:alice")
    matcher = _always(Transformation.SUMMARIZATION)

    assert merge_lineage(
        [("in", upstream)], "out", "llm:x", matcher=matcher
    ) == linear_lineage("in", upstream, "out", "llm:x", matcher=matcher)


def test_merge_does_not_mutate_its_inputs() -> None:
    a = init_lineage("user:alice")
    b = init_lineage("tool:db")
    before = [(m.data_sources, dict(m.source_transformations), m.entity_path) for m in (a, b)]

    merge_lineage(
        [("pa", a), ("pb", b)],
        "out",
        "agent:a",
        matcher=_always(Transformation.ANONYMIZATION),
    )

    assert [
        (m.data_sources, dict(m.source_transformations), m.entity_path) for m in (a, b)
    ] == before


# --- the ops are matcher-agnostic -------------------------------------------


def test_ops_pass_input_then_output_to_the_matcher() -> None:
    """The spec's call order is ``match(payload, output_payload)``. Pinned
    because a matcher that reports a *directional* transformation
    (summarization) would report it backwards if the arguments were swapped."""
    seen: list[tuple[object, object]] = []

    def _record(payload_a: object, payload_b: object, /) -> MatchResult:
        seen.append((payload_a, payload_b))
        return MatchResult(matched=True)

    linear_lineage("IN", init_lineage("s"), "OUT", "e", matcher=_record)
    merge_lineage(
        [("A", init_lineage("s1")), ("B", init_lineage("s2"))],
        "OUT",
        "e",
        matcher=_record,
    )

    assert seen == [("IN", "OUT"), ("A", "OUT"), ("B", "OUT")]


def test_ops_never_read_matcher_evidence() -> None:
    """ADR-0027: "lineage does not read evidence". A matcher returning an
    exploding evidence object must not break lineage."""

    class _Boom:
        def __getattr__(self, name: str) -> object:  # pragma: no cover - must not run
            raise AssertionError("lineage read the matcher's evidence")

    def _with_evidence(payload_a: object, payload_b: object, /) -> MatchResult:
        return MatchResult(matched=True, evidence=_Boom())

    md = linear_lineage("in", init_lineage("s"), "out", "e", matcher=_with_evidence)
    assert md.data_sources == frozenset({"s"})
