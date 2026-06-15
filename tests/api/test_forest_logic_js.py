"""Node-driven tests for the execution-forest view's data-layer (ADR-0009).

The forest view's non-trivial JS lives in
``data_governance/api/ui/forest_logic.js`` so it can be exercised from
``node`` without a browser engine — the exact test shape of
``test_trace_tree_logic_js.py`` (same Node skipif guard, same
require-and-print pattern).

``buildForest(spans)`` takes the raw per-trace spans (as ``GET /spans``
returns them) and produces the unified per-invocation forest
``U -> A -> { L, tools }``: anchor on the inner agent, re-parent the LLM
spans flat, dedup the in-process TOOL vs sidecar TOOL for one call (matched
by trace STRUCTURE — the sidecar span nests under the TOOL's httpx client —
preferring the sidecar), and drop the framework/handshake noise.

Scenario-agnostic: the rules are expressed only via OpenInference span kinds
(AGENT/LLM/TOOL) and the lineage hop kind (agent_to_tool) — no tool-name
table, no name munging, no hardcoded routes or stores.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_LOGIC_JS = (
    Path(__file__).resolve().parents[2]
    / "data_governance"
    / "api"
    / "ui"
    / "forest_logic.js"
)


def _have_node() -> bool:
    return shutil.which("node") is not None


pytestmark = pytest.mark.skipif(
    not _have_node(), reason="node is not installed; skipping UI-logic tests"
)


def _run_js(script: str) -> str:
    full = f"const M = require({json.dumps(str(_LOGIC_JS))});\n{script}\n"
    result = subprocess.run(
        ["node", "-e", full],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"node exited {result.returncode}\nstderr:\n{result.stderr}\n"
        f"stdout:\n{result.stdout}"
    )
    return result.stdout


# A small but representative single-turn trace: the POST/ root, the outer
# "Agent workflow" Runner wrapper AGENT, the inner "patent-agent" AGENT (the
# anchor), a turn CHAIN (noise), one generation LLM, one in-process TOOL + its
# httpx client + the sidecar twin nested under it, an mcp_tools CHAIN (noise)
# and an MCP handshake (agent_to_service, noise). The tool names are arbitrary
# (read_patent here) — nothing in the logic keys on them.
_FIXTURE = r"""
const spans = [
  { trace_id:'T', span_id:'root', parent_id:null, name:'POST /',
    started_at:'2026-01-01T00:00:01.000Z', attributes:{ source:'in-process' } },
  { trace_id:'T', span_id:'wrap', parent_id:'root', name:'Agent workflow',
    started_at:'2026-01-01T00:00:02.000Z',
    attributes:{ source:'in-process', 'openinference.span.kind':'AGENT' } },
  { trace_id:'T', span_id:'agent', parent_id:'wrap', name:'patent-agent',
    started_at:'2026-01-01T00:00:03.000Z',
    attributes:{ source:'in-process', 'openinference.span.kind':'AGENT' } },
  { trace_id:'T', span_id:'turn', parent_id:'agent', name:'turn',
    started_at:'2026-01-01T00:00:04.000Z',
    attributes:{ source:'in-process', 'openinference.span.kind':'CHAIN' } },
  { trace_id:'T', span_id:'gen1', parent_id:'turn', name:'generation',
    started_at:'2026-01-01T00:00:05.000Z',
    attributes:{ source:'in-process', 'openinference.span.kind':'LLM',
      'llm.output_messages.0.message.content':'d-prime' } },
  { trace_id:'T', span_id:'tool_ip', parent_id:'turn', name:'read_patent',
    started_at:'2026-01-01T00:00:06.000Z',
    attributes:{ source:'in-process', 'openinference.span.kind':'TOOL' } },
  { trace_id:'T', span_id:'httpx', parent_id:'tool_ip', name:'POST',
    started_at:'2026-01-01T00:00:06.500Z',
    attributes:{ source:'in-process' } },
  { trace_id:'T', span_id:'tool_sc', parent_id:'httpx', name:'patent-agent.mcp read_patent',
    started_at:'2026-01-01T00:00:07.000Z',
    attributes:{ source:'sidecar', 'openinference.span.kind':'TOOL',
      'lineage.hop.kind':'agent_to_tool', 'lineage.target.id':'read-patent-mcp',
      'input.value':'{"patent_id":1}' } },
  { trace_id:'T', span_id:'mcp_tools', parent_id:'agent', name:'mcp_tools',
    started_at:'2026-01-01T00:00:08.000Z',
    attributes:{ source:'in-process', 'openinference.span.kind':'CHAIN' } },
  { trace_id:'T', span_id:'handshake', parent_id:'root', name:'patent-agent.mcp tools/list',
    started_at:'2026-01-01T00:00:09.000Z',
    attributes:{ source:'sidecar', 'lineage.hop.kind':'agent_to_service' } },
];
"""


def test_anchor_is_inner_agent_and_user_is_its_root_ancestor():
    """Two AGENT spans nest (Agent workflow -> patent-agent); the anchor is the
    inner one. U is the agent's root ancestor (the inbound POST / span) —
    derived structurally, not by route name."""
    out = _run_js(
        _FIXTURE
        + "const f = M.buildForest(spans);"
        "process.stdout.write(JSON.stringify({ agent: f.agent.name,"
        " agent_id: f.agent.span_id, user: f.user.name, user_id: f.user.span_id }));"
    )
    assert json.loads(out) == {
        "agent": "patent-agent", "agent_id": "agent",
        "user": "POST /", "user_id": "root",
    }


def test_llm_reparented_flat_and_noise_dropped():
    """generation (L) becomes a flat child of A; the framework wrappers
    (Agent workflow / turn / mcp_tools), httpx plumbing, and the MCP handshake
    never appear as nodes."""
    out = _run_js(
        _FIXTURE
        + "const f = M.buildForest(spans);"
        "const ids = f.children.map((c) => c.span.span_id);"
        "process.stdout.write(JSON.stringify({ roles: f.children.map((c)=>c.role), ids }));"
    )
    res = json.loads(out)
    assert res["roles"] == ["L", "tool"]
    assert "gen1" in res["ids"]
    for noise in ("wrap", "turn", "mcp_tools", "httpx", "handshake"):
        assert noise not in res["ids"]


def test_tool_dedup_prefers_sidecar_matched_by_structure():
    """The in-process TOOL span and the sidecar TOOL span for the SAME call
    collapse to one node — matched because the sidecar span nests under the
    TOOL's httpx client (so the TOOL is its ancestor). The sidecar is preferred;
    both real spans are kept as provenance. The displayed name comes off the
    in-process TOOL span — no lookup table."""
    out = _run_js(
        _FIXTURE
        + "const f = M.buildForest(spans);"
        "const t = f.children.find((c) => c.role === 'tool');"
        "process.stdout.write(JSON.stringify({ name: t.name, source: t.source,"
        " disp: t.span.span_id, target: t.target, prov: t.provenance.slice().sort(),"
        " n: f.toolCount }));"
    )
    assert json.loads(out) == {
        "name": "read_patent",
        "source": "sidecar",
        "disp": "tool_sc",
        "target": "read-patent-mcp",
        "prov": ["T|tool_ip", "T|tool_sc"],
        "n": 1,
    }


def test_two_tool_calls_stay_distinct_per_invocation():
    """Per-invocation grain: two calls of the same tool are two nodes, each
    zipped to its own sidecar twin by structure — never collapsed. (The patent
    case is two write_file calls, F1 then F2; the names are incidental.)"""
    out = _run_js(
        """
        const spans = [
          { trace_id:'T', span_id:'root', parent_id:null, name:'POST /',
            started_at:'t00', attributes:{ source:'in-process' } },
          { trace_id:'T', span_id:'agent', parent_id:'root', name:'patent-agent',
            started_at:'t01',
            attributes:{ source:'in-process', 'openinference.span.kind':'AGENT' } },
          { trace_id:'T', span_id:'gen', parent_id:'agent', name:'generation',
            started_at:'t02',
            attributes:{ source:'in-process', 'openinference.span.kind':'LLM' } },
          { trace_id:'T', span_id:'w1ip', parent_id:'agent', name:'write_file',
            started_at:'t03',
            attributes:{ source:'in-process', 'openinference.span.kind':'TOOL' } },
          { trace_id:'T', span_id:'w1sc', parent_id:'w1ip', name:'mcp write_file',
            started_at:'t04', attributes:{ source:'sidecar',
              'lineage.hop.kind':'agent_to_tool', 'lineage.target.id':'write-file-mcp',
              'input.value':'{"content":"keywords"}' } },
          { trace_id:'T', span_id:'w2ip', parent_id:'agent', name:'write_file',
            started_at:'t05',
            attributes:{ source:'in-process', 'openinference.span.kind':'TOOL' } },
          { trace_id:'T', span_id:'w2sc', parent_id:'w2ip', name:'mcp write_file',
            started_at:'t06', attributes:{ source:'sidecar',
              'lineage.hop.kind':'agent_to_tool', 'lineage.target.id':'write-file-mcp',
              'input.value':'{"content":"summary"}' } },
        ];
        const f = M.buildForest(spans);
        const tools = f.children.filter((c) => c.role === 'tool');
        process.stdout.write(JSON.stringify({
          n: tools.length,
          disp: tools.map((t) => t.span.span_id),
          names: tools.map((t) => t.name),
          bodies: tools.map((t) => t.span.attributes['input.value']),
        }));
        """
    )
    assert json.loads(out) == {
        "n": 2,
        "disp": ["w1sc", "w2sc"],
        "names": ["write_file", "write_file"],
        "bodies": ['{"content":"keywords"}', '{"content":"summary"}'],
    }


def test_falls_back_to_in_process_tool_when_no_sidecar():
    """Sidecar off: the in-process TOOL span stands alone as the tool node."""
    out = _run_js(
        """
        const spans = [
          { trace_id:'T', span_id:'root', parent_id:null, name:'POST /',
            started_at:'t0', attributes:{ source:'in-process' } },
          { trace_id:'T', span_id:'agent', parent_id:'root', name:'a',
            started_at:'t1',
            attributes:{ source:'in-process', 'openinference.span.kind':'AGENT' } },
          { trace_id:'T', span_id:'gen', parent_id:'agent', name:'generation',
            started_at:'t2',
            attributes:{ source:'in-process', 'openinference.span.kind':'LLM' } },
          { trace_id:'T', span_id:'rp', parent_id:'agent', name:'read_patent',
            started_at:'t3',
            attributes:{ source:'in-process', 'openinference.span.kind':'TOOL' } },
        ];
        const f = M.buildForest(spans);
        const t = f.children.find((c) => c.role === 'tool');
        process.stdout.write(JSON.stringify(
          { name: t.name, source: t.source, disp: t.span.span_id, prov: t.provenance }));
        """
    )
    assert json.loads(out) == {
        "name": "read_patent", "source": "in-process", "disp": "rp", "prov": ["T|rp"],
    }


def test_sidecar_only_call_with_no_in_process_twin_is_kept():
    """A sidecar agent_to_tool span whose ancestor chain has no in-process TOOL
    (in-process tool instrumentation off) is kept as its own node, named by its
    lineage target id."""
    out = _run_js(
        """
        const spans = [
          { trace_id:'T', span_id:'root', parent_id:null, name:'POST /',
            started_at:'t0', attributes:{ source:'in-process' } },
          { trace_id:'T', span_id:'agent', parent_id:'root', name:'a',
            started_at:'t1',
            attributes:{ source:'in-process', 'openinference.span.kind':'AGENT' } },
          { trace_id:'T', span_id:'gen', parent_id:'agent', name:'generation',
            started_at:'t2',
            attributes:{ source:'in-process', 'openinference.span.kind':'LLM' } },
          { trace_id:'T', span_id:'sc', parent_id:'agent', name:'a.mcp do_thing',
            started_at:'t3', attributes:{ source:'sidecar',
              'lineage.hop.kind':'agent_to_tool', 'lineage.target.id':'do-thing-mcp' } },
        ];
        const f = M.buildForest(spans);
        const t = f.children.find((c) => c.role === 'tool');
        process.stdout.write(JSON.stringify(
          { name: t.name, source: t.source, disp: t.span.span_id, n: f.toolCount }));
        """
    )
    assert json.loads(out) == {
        "name": "do-thing-mcp", "source": "sidecar", "disp": "sc", "n": 1,
    }


def test_children_in_true_started_at_order():
    """The tool-use loop interleaves L and tools; children are in real
    started_at order, not grouped."""
    out = _run_js(
        _FIXTURE
        + "const f = M.buildForest(spans);"
        "process.stdout.write(JSON.stringify(f.children.map((c)=>c.span.span_id)));"
    )
    assert json.loads(out) == ["gen1", "tool_sc"]


def test_no_agent_span_is_not_a_forest():
    """A trace with no in-process AGENT span is not an execution forest."""
    out = _run_js(
        """
        const spans = [
          { trace_id:'T', span_id:'root', parent_id:null, name:'POST /',
            started_at:'t0', attributes:{ source:'in-process' } },
          { trace_id:'T', span_id:'x', parent_id:'root', name:'something',
            started_at:'t1', attributes:{ source:'in-process' } },
        ];
        const f = M.buildForest(spans);
        process.stdout.write(JSON.stringify({ ok: f.ok, agent: f.agent, kids: f.children.length }));
        """
    )
    assert json.loads(out) == {"ok": False, "agent": None, "kids": 0}
