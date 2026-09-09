"""Wire contract v1.6: the producer forwards a traceparent when the request
arrived without one (``mint_traceparent``, default on) and records
``lineage.parent.source=none`` on the request span that rooted the trace.

Two shapes pin what the consumer does with that — and what it deliberately
does not do:

* The v1.6 entry is a TRUE root (``parent_id`` NULL, ``parent.source=none``)
  rather than the golden's dangling ghost parent. Entry detection must treat
  both the same: one root, everything the entry caused underneath it.
* The pre-v1.6 failure shape — an entry that could not stamp, so the app's
  outbound calls fell to app-internal, never-exported parents — derives as
  one root per outbound call. The consumer does NOT weld those onto the entry
  ("no mechanism may guess"); the fix is the producer's, and this test is the
  shape it removes (measured live 2026-09-02: 9 roots for one turn).

Pure, no DB, on the golden trace.
"""
from __future__ import annotations

import dataclasses

from data_governance.processors.interactions.sidecar import plan_trace

from . import sidecar_golden as golden

# App-internal parents the propagate-only shim would hand each outbound call
# when the entry's stamp never reached it: real span ids, never exported.
_APP_SPAN = {golden.B1: "0a0a0a0a0a0a0a01", golden.B3: "0a0a0a0a0a0a0a03", golden.B5: "0a0a0a0a0a0a0a05"}


def _with(spans, **by_id):
    """Copies of *spans* with per-span-id overrides: ``parent_id`` and/or
    extra ``attributes`` (``attrs=``)."""
    out = []
    for s in spans:
        if s.span_id in by_id:
            o = by_id[s.span_id]
            attrs = dict(s.attributes, **o.get("attrs", {}))
            s = dataclasses.replace(s, parent_id=o.get("parent_id", s.parent_id), attributes=attrs)
        out.append(s)
    return out


def _rows(plan):
    return {r.anchor_span_id: r for r in plan.want.values()}


def _minted_entry():
    """The v1.6 shape: the entry rooted the trace itself."""
    return _with(golden.build_spans(),
                 **{golden.A1: {"parent_id": None, "attrs": {"lineage.parent.source": "none"}}})


def test_minted_entry_is_a_true_root_and_still_the_one_anchor_root():
    plan = plan_trace(golden.TRACE, _minted_entry())
    assert plan.anchor_ids == golden.ANCHORS
    rows = _rows(plan)
    assert rows[golden.A1].parent_anchor_span_id is None
    assert [aid for aid, r in rows.items() if r.parent_anchor_span_id is None] == [golden.A1]
    # Everything the entry caused hangs under it, exactly as with a ghost parent.
    for aid in (golden.B1, golden.B3, golden.B5):
        assert rows[aid].parent_anchor_span_id == golden.A1
    assert golden.D1 not in plan.anchor_ids  # the echo still folds under B3


def test_minted_entry_derives_the_same_rows_as_the_dangling_entry():
    """``parent.source`` is a fact for auditing attribution; the consumer
    derives nothing from it, and a NULL parent on the entry is the simpler
    case of the dangling one — the plan must not differ."""
    ghost = _rows(plan_trace(golden.TRACE, golden.build_spans()))
    minted = _rows(plan_trace(golden.TRACE, _minted_entry()))
    assert ghost.keys() == minted.keys()
    for aid in ghost:
        g, m = ghost[aid], minted[aid]
        assert (g.caller.natural_key, g.callee.natural_key) == (m.caller.natural_key, m.callee.natural_key)
        assert g.parent_anchor_span_id == m.parent_anchor_span_id


def test_unstamped_entry_fragments_into_one_root_per_outbound_call():
    """The pre-v1.6 shape the producer fix removes. The consumer must NOT
    repair it by guessing: each outbound whose parent is an unexported
    app-internal span is its own root, visibly."""
    spans = _with(golden.build_spans(),
                  **{golden.A1: {"parent_id": None, "attrs": {"lineage.parent.source": "none"}}},
                  **{sid: {"parent_id": app, "attrs": {"lineage.parent.source": "wire"}}
                     for sid, app in _APP_SPAN.items()})
    plan = plan_trace(golden.TRACE, spans)
    assert plan.anchor_ids == golden.ANCHORS
    rows = _rows(plan)
    roots = sorted(aid for aid, r in rows.items() if r.parent_anchor_span_id is None)
    assert roots == sorted(golden.ANCHORS)  # 4 rows, 4 roots — the entry welds nothing
    assert rows[golden.B3].callee.natural_key == "tool:team1/weather-tool"  # the echo still folds
