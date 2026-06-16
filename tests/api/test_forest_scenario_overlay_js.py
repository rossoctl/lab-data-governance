"""Node-driven tests for the DEMO-ONLY store overlay (ADR-0010).

``forest_scenario_overlay.js`` maps the patent-app's tool calls to D/F datastore
nodes from their args — the explicit, isolated exception to ``forest_logic.js``'s
scenario-agnostic / nothing-synthesized rule (ADR-0009). Same Node harness as
``test_forest_logic_js.py``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_OVERLAY_JS = (
    Path(__file__).resolve().parents[2]
    / "data_governance"
    / "api"
    / "ui"
    / "forest_scenario_overlay.js"
)


def _have_node() -> bool:
    return shutil.which("node") is not None


pytestmark = pytest.mark.skipif(
    not _have_node(), reason="node is not installed; skipping UI-logic tests"
)


def _run_js(script: str) -> str:
    full = f"const M = require({json.dumps(str(_OVERLAY_JS))});\n{script}\n"
    result = subprocess.run(
        ["node", "-e", full], check=False, capture_output=True, text=True
    )
    assert result.returncode == 0, (
        f"node exited {result.returncode}\nstderr:\n{result.stderr}\n"
        f"stdout:\n{result.stdout}"
    )
    return result.stdout


def _tool(name: str, tid: str, sid: str, args: str) -> str:
    """A tool child as buildForest() emits it (args go on span input.value)."""
    return (
        "{ role:'tool', name:%s, span:{ trace_id:%s, span_id:%s,"
        " attributes:{ 'input.value':%s } } }"
        % (json.dumps(name), json.dumps(tid), json.dumps(sid), json.dumps(args))
    )


def test_t1_read_patent_to_D_and_two_writes_converge_on_F():
    """T1: read_patent -> D (read); two write_file (content-only args) converge
    on a single F node (write); each link carries its tool child's key."""
    forest = (
        "const forest = { ok:true, agent:{}, children:["
        + _tool("read_patent", "T1", "rp", '{"patent_id":1}') + ","
        + "{ role:'L', span:{ trace_id:'T1', span_id:'g1', attributes:{} } },"
        + _tool("write_file", "T1", "w1", '{"content":"Foldable display"}') + ","
        + _tool("write_file", "T1", "w2", '{"content":"This patent describes"}')
        + "] };"
    )
    out = _run_js(
        forest
        + "const o = M.storeOverlay(forest);"
        "process.stdout.write(JSON.stringify({"
        " stores: o.stores.map((s)=>({id:s.id,label:s.label,kind:s.kind,ops:s.ops})),"
        " links: o.links }));"
    )
    res = json.loads(out)
    assert res["stores"] == [
        {"id": "D", "label": "D", "kind": "database", "ops": ["read"]},
        {"id": "F", "label": "F", "kind": "filesystem", "ops": ["write"]},
    ]
    assert res["links"] == [
        {"childKey": "T1|rp", "storeId": "D", "op": "read"},
        {"childKey": "T1|w1", "storeId": "F", "op": "write"},
        {"childKey": "T1|w2", "storeId": "F", "op": "write"},
    ]


def test_t2_read_file_splits_by_name_and_web_search_is_omitted():
    """T2: read_file {name} splits F into F·keywords / F·summary; web_search is
    external egress and gets NO store node."""
    forest = (
        "const forest = { ok:true, agent:{}, children:["
        + _tool("read_file", "T2", "r1", '{"name":"keywords"}') + ","
        + _tool("read_file", "T2", "r2", '{"name":"summary"}') + ","
        + _tool("web_search", "T2", "s1", '{"query":"foldable display"}')
        + "] };"
    )
    out = _run_js(
        forest
        + "const o = M.storeOverlay(forest);"
        "process.stdout.write(JSON.stringify({"
        " stores: o.stores.map((s)=>({id:s.id,label:s.label})),"
        " links: o.links.map((l)=>({k:l.childKey,s:l.storeId})) }));"
    )
    res = json.loads(out)
    assert res["stores"] == [
        {"id": "F/keywords", "label": "F · keywords"},
        {"id": "F/summary", "label": "F · summary"},
    ]
    assert res["links"] == [
        {"k": "T2|r1", "s": "F/keywords"},
        {"k": "T2|r2", "s": "F/summary"},
    ]


def test_unmapped_tool_and_non_forest_yield_nothing():
    """Unmapped tool names and a non-forest produce no store nodes — the overlay
    fires only on the patent-app tool identities, synthesizing nothing else."""
    out = _run_js(
        "const a = M.storeOverlay({ ok:true, agent:{}, children:["
        + _tool("frobnicate", "T", "x", '{"foo":1}') + "] });"
        "const b = M.storeOverlay({ ok:false, agent:null, children:[] });"
        "process.stdout.write(JSON.stringify({ a:a.stores.length,"
        " al:a.links.length, b:b.stores.length }));"
    )
    assert json.loads(out) == {"a": 0, "al": 0, "b": 0}


def test_tools_under_sub_agents_are_collected_recursively():
    """Multi-agent (ADR-0011): tools nested under role:'agent' children — at any
    depth — are mapped, not just the top level. read_patent at top, read_file one
    level down, write_file two levels down."""
    forest = (
        "const forest = { ok:true, agent:{}, children:["
        + _tool("read_patent", "T", "rp", '{"patent_id":1}') + ","
        + "{ role:'agent', name:'sub', span:{trace_id:'T',span_id:'a1'}, children:["
        + _tool("read_file", "T", "rf", '{"name":"summary"}') + ","
        + "{ role:'agent', name:'subsub', span:{trace_id:'T',span_id:'a2'}, children:["
        + _tool("write_file", "T", "wf", '{"content":"x"}')
        + "] }"
        + "] }"
        + "] };"
    )
    out = _run_js(
        forest
        + "const o = M.storeOverlay(forest);"
        "process.stdout.write(JSON.stringify({"
        " stores: o.stores.map((s)=>s.id),"
        " links: o.links.map((l)=>({k:l.childKey,s:l.storeId})) }));"
    )
    res = json.loads(out)
    assert res["stores"] == ["D", "F/summary", "F"]
    assert res["links"] == [
        {"k": "T|rp", "s": "D"},
        {"k": "T|rf", "s": "F/summary"},
        {"k": "T|wf", "s": "F"},
    ]
