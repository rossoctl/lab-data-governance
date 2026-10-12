"""Unit tests for ``tools/load_trace.py``'s pure ``_reid_rows`` helper.

``_reid_rows`` is the transform behind the ``--reid`` flag: it rewrites a loaded
fixture onto a fresh random ``trace_id`` + ``span_id``s so re-loading an already-
ingested fixture reads as genuinely new data (a plain re-replay is a no-op —
span upserts are finalization-only and the interactions cursor has passed the
seqs). Two properties matter: the *parent/child tree is preserved* under the
rewrite, since the graph algorithm derives interactions from it, and *every id a
span names inside itself follows the rewrite* (``lineage.exchange.id``, links,
event attributes, a ``tracestate`` member value), since the sidecar algorithm
refuses a request span whose exchange id is not its own. These tests
exercise the helper in memory only — no OTLP, no network, no DB.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from tools.load_trace import _reid_rows

_GOLDEN = Path(__file__).resolve().parents[1] / "fixtures" / "golden_two_span.json"

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


# --- ids referenced from inside a span travel with the span ------------------


def _sidecar_rows() -> list[dict]:
    """One sidecar exchange plus the places an id can hide: the request span's
    own id in ``lineage.exchange.id`` (the contract fixes the two as identical),
    the same id on the paired response span, a link to the request, an event
    attribute, a nested attribute, and a captured body that merely *mentions*
    the trace inside a longer string."""
    t = "f" * 32
    req, resp = "aaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbb"
    return [
        {
            "trace_id": t, "span_id": req, "parent_id": None, "name": "req",
            "attributes": {
                "lineage.role": "request",
                "lineage.exchange.id": req,
                "nested": {"ids": [req, "not-an-id"]},
                "body": f"traceparent: 00-{t}-{req}-01",
            },
            "events": [{"name": "e", "attributes": {"span": req}}],
            "otlp": {"trace_state": f"vendor=abc,lineage-parent={req}", "flags": 1},
        },
        {
            "trace_id": t, "span_id": resp, "parent_id": req, "name": "resp",
            "attributes": {"lineage.role": "response", "lineage.exchange.id": req},
            "links": [{"trace_id": t, "span_id": req, "attributes": {}}],
        },
    ]


def test_exchange_id_follows_the_request_span():
    new_rows, _ = _reid_rows(_sidecar_rows())
    by_name = {r["name"]: r for r in new_rows}
    req_id = by_name["req"]["span_id"]

    assert by_name["req"]["attributes"]["lineage.exchange.id"] == req_id
    assert by_name["resp"]["attributes"]["lineage.exchange.id"] == req_id


def test_links_events_and_nested_attributes_are_remapped():
    new_rows, new_trace_id = _reid_rows(_sidecar_rows())
    by_name = {r["name"]: r for r in new_rows}
    req_id = by_name["req"]["span_id"]

    link = by_name["resp"]["links"][0]
    assert link == {"trace_id": new_trace_id, "span_id": req_id, "attributes": {}}
    assert by_name["req"]["events"][0]["attributes"]["span"] == req_id
    assert by_name["req"]["attributes"]["nested"] == {"ids": [req_id, "not-an-id"]}


def test_tracestate_stamp_follows_the_request_span():
    new_rows, _ = _reid_rows(_sidecar_rows())
    by_name = {r["name"]: r for r in new_rows}
    req_id = by_name["req"]["span_id"]

    assert by_name["req"]["otlp"] == {
        "trace_state": f"vendor=abc,lineage-parent={req_id}", "flags": 1
    }


def test_ids_inside_longer_strings_are_left_alone():
    rows = _sidecar_rows()
    body = rows[0]["attributes"]["body"]
    new_rows, _ = _reid_rows(rows)

    assert new_rows[0]["attributes"]["body"] == body


def test_no_old_id_survives_as_a_whole_string():
    rows = _sidecar_rows()
    old_ids = {"f" * 32, "aaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbb"}
    new_rows, _ = _reid_rows(rows)

    def strings(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for v in value.values():
                yield from strings(v)
        elif isinstance(value, list):
            for v in value:
                yield from strings(v)

    for row in new_rows:
        for field in ("attributes", "events", "links"):
            assert old_ids.isdisjoint(strings(row.get(field)))
        trace_state = (row.get("otlp") or {}).get("trace_state", "")
        assert old_ids.isdisjoint(m.partition("=")[2] for m in trace_state.split(","))


def test_golden_fixture_keeps_the_contract_after_reid():
    """The committed sidecar fixture, re-id'd, must still be a trace the producer
    could have emitted: every request span's exchange id is its own span id and
    every response span's exchange id names a request span of the trace."""
    rows = json.loads(_GOLDEN.read_text())
    new_rows, _ = _reid_rows(rows)
    request_ids = set()
    for r in new_rows:
        if r["attributes"].get("lineage.role") == "request":
            assert r["attributes"]["lineage.exchange.id"] == r["span_id"]
            request_ids.add(r["span_id"])
    for r in new_rows:
        if r["attributes"].get("lineage.role") == "response":
            assert r["attributes"]["lineage.exchange.id"] in request_ids
