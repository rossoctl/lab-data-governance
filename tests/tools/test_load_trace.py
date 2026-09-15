"""Unit tests for ``tools/load_trace.py``'s pure ``_reid_rows`` helper.

``_reid_rows`` is the transform behind the ``--reid`` flag: it rewrites a loaded
fixture onto a fresh random ``trace_id`` + ``span_id``s so re-loading an already-
ingested fixture reads as genuinely new data (a plain re-replay is a no-op —
span upserts are finalization-only and the interactions cursor has passed the
seqs). The correctness property that matters is that the *parent/child tree is
preserved* under the rewrite, since the graph algorithm derives interactions from
it. These tests exercise the helper in memory only — no OTLP, no network, no DB.
"""

from __future__ import annotations

import re

from tools.load_trace import _reid_rows

_HEX32 = re.compile(r"\A[0-9a-f]{32}\Z")
_HEX16 = re.compile(r"\A[0-9a-f]{16}\Z")


def _sample_rows() -> list[dict]:
    """A tiny two-tree trace: a root with one child, plus a second root.

    Covers every parent shape the helper distinguishes: a real parent link
    (``child`` -> ``root``), a ``None`` parent, an empty-string parent, and a
    missing ``parent_id`` key. The ``name`` field rides along to prove other
    fields are untouched.
    """
    return [
        {"trace_id": "f" * 32, "span_id": "aaaaaaaaaaaaaaaa", "parent_id": None, "name": "root"},
        {"trace_id": "f" * 32, "span_id": "bbbbbbbbbbbbbbbb", "parent_id": "aaaaaaaaaaaaaaaa", "name": "child"},
        {"trace_id": "f" * 32, "span_id": "cccccccccccccccc", "parent_id": "", "name": "empty-parent root"},
        {"trace_id": "f" * 32, "span_id": "dddddddddddddddd", "name": "no-parent-key root"},
    ]


def test_new_trace_id_is_fresh_32_hex():
    rows = _sample_rows()
    new_rows, new_trace_id = _reid_rows(rows)

    assert _HEX32.match(new_trace_id), new_trace_id
    assert new_trace_id != "f" * 32
    # every row carries the one new trace_id
    assert {r["trace_id"] for r in new_rows} == {new_trace_id}


def test_every_span_gets_a_new_16_hex_id_one_to_one():
    rows = _sample_rows()
    old_ids = [r["span_id"] for r in rows]
    new_rows, _ = _reid_rows(rows)
    new_ids = [r["span_id"] for r in new_rows]

    # all new, all valid 16-hex, none collides with an old id
    assert all(_HEX16.match(sid) for sid in new_ids), new_ids
    assert len(set(new_ids)) == len(new_ids)  # 1:1, no collisions
    assert set(new_ids).isdisjoint(old_ids)


def test_parent_child_tree_is_preserved():
    rows = _sample_rows()
    new_rows, _ = _reid_rows(rows)
    by_name = {r["name"]: r for r in new_rows}

    # the child's remapped parent_id points at the root's remapped span_id
    assert by_name["child"]["parent_id"] == by_name["root"]["span_id"]


def test_root_and_missing_parents_are_left_as_is():
    rows = _sample_rows()
    new_rows, _ = _reid_rows(rows)
    by_name = {r["name"]: r for r in new_rows}

    assert by_name["root"]["parent_id"] is None
    assert by_name["empty-parent root"]["parent_id"] == ""
    assert "parent_id" not in by_name["no-parent-key root"]


def test_mapping_is_consistent_across_span_and_parent_use():
    # A span that is both referenced as a parent and present as a row must get
    # the same new id in both places — otherwise the tree would fracture.
    rows = _sample_rows()
    new_rows, _ = _reid_rows(rows)
    by_name = {r["name"]: r for r in new_rows}

    root_new_id = by_name["root"]["span_id"]
    # the only reference to root-as-parent is the child; assert they agree
    assert by_name["child"]["parent_id"] == root_new_id


def test_other_fields_untouched_and_input_not_mutated():
    rows = _sample_rows()
    original = [dict(r) for r in rows]
    new_rows, _ = _reid_rows(rows)

    # names (a stand-in for "every other field") survive verbatim
    assert [r["name"] for r in new_rows] == [r["name"] for r in original]
    # the helper returns copies; the caller's rows are unchanged
    assert rows == original


# --- id references inside attributes / events / links ----------------------
#
# span_id and parent_id are not the only places an id appears. Instrumentation
# records ids inside attributes (an attribute pointing at another span), and
# links carry span_id/trace_id structurally. Rewriting the identity but not
# those references yields a trace that contradicts itself — one a consumer
# checking such an attribute against the span's own id will reject.


def _rows_with_refs() -> list[dict]:
    """A root and a child, where the child's attributes and links reference
    ids: its own span id, the root's, the trace id, and one nested inside a
    list. ``payload`` holds an id as a *substring* of a larger body."""
    return [
        {
            "trace_id": "f" * 32,
            "span_id": "aaaaaaaaaaaaaaaa",
            "parent_id": None,
            "attributes": {"exchange.id": "aaaaaaaaaaaaaaaa"},
        },
        {
            "trace_id": "f" * 32,
            "span_id": "bbbbbbbbbbbbbbbb",
            "parent_id": "aaaaaaaaaaaaaaaa",
            "attributes": {
                "exchange.id": "bbbbbbbbbbbbbbbb",
                "peer.span": "aaaaaaaaaaaaaaaa",
                "of.trace": "f" * 32,
                "nested": {"ids": ["aaaaaaaaaaaaaaaa", "not-an-id"]},
                "payload": '{"traceparent":"00-' + "f" * 32 + '-aaaaaaaaaaaaaaaa-01"}',
                "count": 3,
            },
            "links": [{"trace_id": "f" * 32, "span_id": "aaaaaaaaaaaaaaaa"}],
            "events": [{"name": "e", "attributes": {"span": "aaaaaaaaaaaaaaaa"}}],
        },
    ]


def test_attribute_id_references_follow_the_rewrite():
    """An attribute holding a span's own id still holds it after the rewrite —
    the property the sidecar wire contract fixes as an equality."""
    new_rows, _ = _reid_rows(_rows_with_refs())
    for row in new_rows:
        assert row["attributes"]["exchange.id"] == row["span_id"]


def test_cross_span_and_trace_references_are_remapped():
    """References to *another* span, to the trace, and to ids nested inside
    containers are all carried onto the new ids."""
    new_rows, new_trace_id = _reid_rows(_rows_with_refs())
    root, child = new_rows
    attrs = child["attributes"]

    assert attrs["peer.span"] == root["span_id"] == child["parent_id"]
    assert attrs["of.trace"] == new_trace_id
    assert attrs["nested"]["ids"] == [root["span_id"], "not-an-id"]
    assert attrs["count"] == 3, "non-string values ride through untouched"

    assert child["links"][0] == {
        "trace_id": new_trace_id,
        "span_id": root["span_id"],
    }
    assert child["events"][0]["attributes"]["span"] == root["span_id"]


def test_ids_inside_payload_bodies_are_left_alone():
    """The match is whole-string, never substring: a captured body that merely
    mentions an id is opaque content, and the store may have content-addressed
    it."""
    rows = _rows_with_refs()
    original_payload = rows[1]["attributes"]["payload"]
    new_rows, _ = _reid_rows(rows)
    assert new_rows[1]["attributes"]["payload"] == original_payload


def test_reid_does_not_mutate_the_callers_nested_structures():
    """The remap deep-copies: mutating the result must not reach back into the
    input's nested dicts/lists."""
    rows = _rows_with_refs()
    new_rows, _ = _reid_rows(rows)
    new_rows[1]["attributes"]["nested"]["ids"].append("scribble")
    assert rows[1]["attributes"]["nested"]["ids"] == ["aaaaaaaaaaaaaaaa", "not-an-id"]
