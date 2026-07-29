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
    against an EMPTY transformation set, and the entity set is empty."""
    md = init_lineage("agent:(demo,travel-advisor)")

    assert md.data_sources == frozenset({"agent:(demo,travel-advisor)"})
    assert md.source_transformations == {"agent:(demo,travel-advisor)": frozenset()}
    assert md.entities == frozenset()


def test_init_entities_is_empty_not_the_entity() -> None:
    """The spec is explicit: "A new set of entities which is empty" — the
    originating entity is the *source*, it is not yet something the data passed
    *through*. Guarding this because "root it at the entity" invites putting the
    entity in both places."""
    assert init_lineage("user:alice").entities == frozenset()


# --- linear_lineage ----------------------------------------------------------


def test_linear_on_match_inherits_sources_and_extends_the_entity_set() -> None:
    """Spec rule 2, matched branch: sources are the input's (1), transformation
    sets copied (2), entity set copied and extended with the entity (3)."""
    origin = init_lineage("user:alice")
    md = linear_lineage(
        "in", origin, "out", "llm:api.openai.com/gpt-4", matcher=_always()
    )

    assert md.data_sources == frozenset({"user:alice"})
    assert md.source_transformations == {"user:alice": frozenset()}
    assert md.entities == frozenset({"llm:api.openai.com/gpt-4"})


def test_linear_adds_the_returned_transformation_to_all_sets() -> None:
    """Spec rule 2(2): "add the returned transformation (if exists) to **all**
    the transformation sets"."""
    upstream = DataLineage(
        data_sources=frozenset({"user:alice", "tool:db"}),
        source_transformations={
            "user:alice": frozenset(),
            "tool:db": frozenset({Transformation.ANONYMIZATION}),
        },
        entities=frozenset({"agent:a"}),
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
        entities=frozenset({"agent:a"}),
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
        entities=frozenset({"agent:a", "llm:x"}),
    )
    md = linear_lineage("in", upstream, "out", "tool:anonymizer", matcher=_never)

    assert md == init_lineage("tool:anonymizer")
    assert md.data_sources == frozenset({"tool:anonymizer"})
    assert md.entities == frozenset()


def test_linear_does_not_mutate_its_input() -> None:
    """Spec rule 2 says "create a **copy**" twice. The upstream metadata belongs
    to another leg's persisted row; mutating it would corrupt that leg."""
    upstream = init_lineage("user:alice")
    before = (
        upstream.data_sources,
        dict(upstream.source_transformations),
        upstream.entities,
    )

    linear_lineage(
        "in", upstream, "out", "llm:x", matcher=_always(Transformation.SUMMARIZATION)
    )

    assert (
        upstream.data_sources,
        dict(upstream.source_transformations),
        upstream.entities,
    ) == before


# --- merge_lineage -----------------------------------------------------------


def test_merge_unions_sources_maps_and_entities() -> None:
    """Spec rule 3, trivial form: sources are the union (1), maps merged (2),
    the entity SET merged and extended with the entity (3)."""
    a = DataLineage(
        data_sources=frozenset({"user:alice"}),
        source_transformations={"user:alice": frozenset()},
        entities=frozenset({"agent:a"}),
    )
    b = DataLineage(
        data_sources=frozenset({"tool:db"}),
        source_transformations={"tool:db": frozenset()},
        entities=frozenset({"llm:x"}),
    )
    md = merge_lineage([("pa", a), ("pb", b)], "out", "agent:a", matcher=_always())

    assert md.data_sources == frozenset({"user:alice", "tool:db"})
    assert md.source_transformations == {
        "user:alice": frozenset(),
        "tool:db": frozenset(),
    }
    # Both branches' entities, plus the processing entity. `agent:a` is already in
    # branch a's set (the data passed through it earlier), and union is idempotent,
    # so it appears exactly once with no dedup step of its own.
    assert md.entities == frozenset({"agent:a", "llm:x"})


def test_merge_merges_transformation_sets_on_a_key_collision() -> None:
    """Spec rule 3(2): "merge the transformation sets in case a key appears
    twice" — and Example 2(2)'s "either by adding a new data source key or
    merging the transformation set with the existing one". Both inputs carry
    ``user:alice``, each with a different transformation, and each input's match
    contributes its own on top."""
    a = DataLineage(
        data_sources=frozenset({"user:alice"}),
        source_transformations={"user:alice": frozenset({Transformation.ANONYMIZATION})},
        entities=frozenset(),
    )
    b = DataLineage(
        data_sources=frozenset({"user:alice"}),
        source_transformations={"user:alice": frozenset({Transformation.SUMMARIZATION})},
        entities=frozenset(),
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
    summarization added (2), b's entity set extended with the entity (3)."""
    a = DataLineage(
        data_sources=frozenset({"user:alice"}),
        source_transformations={"user:alice": frozenset()},
        entities=frozenset({"agent:a"}),
    )
    b = DataLineage(
        data_sources=frozenset({"tool:db"}),
        source_transformations={"tool:db": frozenset({Transformation.ANONYMIZATION})},
        entities=frozenset({"tool:db_reader"}),
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
    assert md.entities == frozenset({"tool:db_reader", "llm:x"})
    assert "user:alice" not in md.data_sources, "an unmatched source contributes nothing"
    assert "agent:a" not in md.entities, "nor do its entities"


def test_merge_spec_example_2_three_inputs_two_matching() -> None:
    """Spec Example 2 verbatim: false for A, true+anonymize for B, true+summarize
    for C. Sources = B ∪ C; B's sets get anonymization, C's get summarization,
    colliding keys merge; entity sets of B and C merged and extended."""
    a = DataLineage(
        data_sources=frozenset({"src_a"}),
        source_transformations={"src_a": frozenset()},
        entities=frozenset({"e_a"}),
    )
    b = DataLineage(
        data_sources=frozenset({"src_b", "shared"}),
        source_transformations={"src_b": frozenset(), "shared": frozenset()},
        entities=frozenset({"e_b"}),
    )
    c = DataLineage(
        data_sources=frozenset({"src_c", "shared"}),
        source_transformations={"src_c": frozenset(), "shared": frozenset()},
        entities=frozenset({"e_c"}),
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
    assert md.entities == frozenset({"e_b", "e_c", "agent:a"})
    assert "src_a" not in md.data_sources
    assert "e_a" not in md.entities


def test_merge_with_no_matching_source_degrades_to_init() -> None:
    """ADR-0027 D3(2): "if no source matches the op degrades to ``init``". A
    runtime *result*, not a selection branch — merge was still chosen, it simply
    found nothing to inherit."""
    a = init_lineage("user:alice")
    b = init_lineage("tool:db")
    md = merge_lineage([("pa", a), ("pb", b)], "out", "tool:anonymizer", matcher=_never)

    assert md == init_lineage("tool:anonymizer")


def test_merge_unions_overlapping_entity_sets_without_repetition() -> None:
    """Merging two branches that share entities must not repeat them. The field is
    "which entities did the data pass through" (spec rule 3(3), a *set*), not a
    visit log — so an entity on both branches is one member, for free from union."""
    a = DataLineage(
        data_sources=frozenset({"s1"}),
        source_transformations={"s1": frozenset()},
        entities=frozenset({"user:alice", "agent:a"}),
    )
    b = DataLineage(
        data_sources=frozenset({"s2"}),
        source_transformations={"s2": frozenset()},
        entities=frozenset({"user:alice", "llm:x"}),
    )
    md = merge_lineage([("pa", a), ("pb", b)], "out", "agent:a", matcher=_always())

    assert md.entities == frozenset({"user:alice", "agent:a", "llm:x"})


def test_merge_entity_set_does_not_depend_on_input_order() -> None:
    """Order carries **no meaning** — the spec's "Note: this is unordered".

    The same two contributions merged in either order produce the *same* entity
    set. This is the assertion that stops an order from creeping back in as a
    semantic: any implementation that appended per-contribution (as the old
    ``entity_path`` did) would give two different answers here, and a reader would
    then be able to draw a flow sequence out of the result."""
    a = DataLineage(
        data_sources=frozenset({"s1"}),
        source_transformations={"s1": frozenset()},
        entities=frozenset({"user:alice", "agent:a"}),
    )
    b = DataLineage(
        data_sources=frozenset({"s2"}),
        source_transformations={"s2": frozenset()},
        entities=frozenset({"tool:db_reader", "llm:x"}),
    )

    forward = merge_lineage([("pa", a), ("pb", b)], "out", "agent:z", matcher=_always())
    reversed_ = merge_lineage([("pb", b), ("pa", a)], "out", "agent:z", matcher=_always())

    assert forward.entities == reversed_.entities
    # Not just the set — the whole metadata is permutation-invariant, since every
    # element of the triple is a union.
    assert forward == reversed_


def test_linear_entity_set_is_order_free_for_equivalent_upstreams() -> None:
    """The same union, reached through two differently-"ordered" upstream sets, is
    one value. A ``frozenset`` field makes this structurally true, which is the
    point: the type refuses to hold an order the algebra cannot justify."""
    made_one_way = DataLineage(
        data_sources=frozenset({"s"}),
        source_transformations={"s": frozenset()},
        entities=frozenset(["agent:a", "llm:x"]),
    )
    made_the_other = DataLineage(
        data_sources=frozenset({"s"}),
        source_transformations={"s": frozenset()},
        entities=frozenset(["llm:x", "agent:a"]),
    )

    assert linear_lineage(
        "in", made_one_way, "out", "tool:t", matcher=_always()
    ) == linear_lineage("in", made_the_other, "out", "tool:t", matcher=_always())


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
    before = [
        (m.data_sources, dict(m.source_transformations), m.entities) for m in (a, b)
    ]

    merge_lineage(
        [("pa", a), ("pb", b)],
        "out",
        "agent:a",
        matcher=_always(Transformation.ANONYMIZATION),
    )

    assert [
        (m.data_sources, dict(m.source_transformations), m.entities) for m in (a, b)
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
