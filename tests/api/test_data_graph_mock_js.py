"""Node-driven tests for the MOCKED data-graph view (ADR-0012).

``data_graph_mock.js`` returns the hardcoded scenario.md §8 data graph (the
lineage view the execution forest cannot draw). It is pure data — nothing is
computed from spans — so these tests pin its STRUCTURE and CLASSIFICATIONS
against the scenario's truth table (§5), not any derivation. Same Node harness
as ``test_forest_scenario_overlay_js.py``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_DG_JS = (
    Path(__file__).resolve().parents[2]
    / "data_governance"
    / "api"
    / "ui"
    / "data_graph_mock.js"
)


def _have_node() -> bool:
    return shutil.which("node") is not None


pytestmark = pytest.mark.skipif(
    not _have_node(), reason="node is not installed; skipping UI-logic tests"
)


def _graph() -> dict:
    """Return dataGraph() as a Python dict (the whole mocked literal)."""
    full = (
        f"const M = require({json.dumps(str(_DG_JS))});\n"
        "process.stdout.write(JSON.stringify(M.dataGraph()));\n"
    )
    result = subprocess.run(
        ["node", "-e", full], check=False, capture_output=True, text=True
    )
    assert result.returncode == 0, (
        f"node exited {result.returncode}\nstderr:\n{result.stderr}"
    )
    return json.loads(result.stdout)


def _node(g: dict, nid: str) -> dict:
    return next(n for n in g["nodes"] if n["id"] == nid)


def _edge(g: dict, frm: str, to: str) -> dict:
    return next(e for e in g["edges"] if e["from"] == frm and e["to"] == to)


def test_mocked_flag_and_referential_integrity():
    """It is labeled mocked, and every edge / identity endpoint references a real
    node id (no dangling edges the renderer would silently drop)."""
    g = _graph()
    assert g["mocked"] is True
    ids = {n["id"] for n in g["nodes"]}
    for e in g["edges"]:
        assert e["from"] in ids and e["to"] in ids, f"dangling edge {e}"
    for e in g["identity"]:
        assert e["from"] in ids and e["to"] in ids, f"dangling identity {e}"
    # the river is the §8 shape: D, two agents, the transform, two files, two egress
    assert ids == {"D", "A1", "L", "F1", "F2", "A2", "W1", "W2"}


def test_t1_precision_fork_written_by_the_agent():
    """Axis 1 (T1): the LLM transforms d→d1 on a y-axis round-trip with the agent,
    then the AGENT writes the fork — keywords (d2) and summary (d3) from the same
    d1, one clean, one confidential. The split the execution forest cannot show."""
    g = _graph()
    # the LLM is an off-spine node branching over its agent (not a storage hop)
    assert _node(g, "L")["type"] == "transform"
    assert _node(g, "L").get("lane") == "top" and _node(g, "L").get("over") == "A1"
    # the agent⇄LLM round-trip: d up to the LLM, d′ back down to the agent
    assert _edge(g, "A1", "L")["data"] == "d"
    assert _edge(g, "L", "A1")["data"] == "d1"
    # the AGENT (not the LLM) writes both files — the fork leaves A1
    f_kw, f_sum = _edge(g, "A1", "F1"), _edge(g, "A1", "F2")
    assert f_kw["fork"] and f_sum["fork"], "both fork edges leave the agent (post-d′)"
    assert f_kw["verdict"] == "clean" and f_sum["verdict"] == "confidential"
    # the files carry the classifier verdict; ground-truth TN/TP tags were removed
    # (external, not auto-derivable — ADR-0012 auto-generatable pass)
    assert _node(g, "F1")["verdict"] == "clean"
    assert _node(g, "F2")["verdict"] == "confidential"
    assert "tag" not in _node(g, "F1") and "tag" not in _node(g, "F2")


def test_t2_recall_leak_on_summary_only():
    """Axis 2 (T2): web_search(summary) is the leak (confidential); only it is
    flagged — web_search(keywords) is approved (clean)."""
    g = _graph()
    assert _node(g, "W1")["verdict"] == "clean"
    assert _node(g, "W2")["verdict"] == "confidential"
    assert "tag" not in _node(g, "W1") and "tag" not in _node(g, "W2")
    assert _node(g, "W2").get("leak") is True
    assert _node(g, "W1").get("leak") in (None, False)
    assert _edge(g, "A2", "W2").get("leak") is True
    assert _edge(g, "A2", "W1").get("leak") in (None, False)


def test_recall_identity_edge_traces_back_to_D():
    """The single explicit cross-session identity edge runs from the leak
    (web_search summary) back to the confidential DB D — the edge a session-scoped
    trace structurally cannot have."""
    g = _graph()
    assert len(g["identity"]) == 1
    rec = g["identity"][0]
    assert rec["from"] == "W2" and rec["to"] == "D" and rec["kind"] == "recall"


def test_files_are_single_shared_nodes_across_the_session_gap():
    """F1/F2 appear once: written in T1, read in T2. One node = the same bytes =
    the persistent data identity across the store + session gap."""
    g = _graph()
    assert sum(1 for n in g["nodes"] if n["id"] == "F1") == 1
    assert sum(1 for n in g["nodes"] if n["id"] == "F2") == 1
    # boundary divider sits right after the files column
    assert g["boundaryAfter"] == _node(g, "F1")["col"] == _node(g, "F2")["col"]


def test_graph_carries_only_auto_derivable_signal():
    """Auto-generatable pass (ADR-0012): the view shows only what a builder could
    derive from spans — no human content-names glossed onto the data flow, no
    external ground-truth tags, no recall-arc gloss. The classifier verdict
    (clean/confidential) and the d/d′ data tokens stay; they are derivable."""
    g = _graph()
    # 1. edges label the bytes with a neutral data token, never our content names
    for e in g["edges"]:
        assert "keywords" not in e["data"] and "summary" not in e["data"], e
    # 2. no node carries an external ground-truth tag (TN/TP)
    for n in g["nodes"]:
        assert "tag" not in n, f"{n['id']} still has a ground-truth tag"
    # 3. the single-letter type labels (D/A/L) that merely duplicate the icon are gone
    for nid in ("D", "A1", "L", "A2"):
        assert _node(g, nid)["label"] not in ("D", "A", "L"), nid
    # 4. the recall identity edge is unlabeled (its summary⟵…⟵D gloss is dropped)
    assert not g["identity"][0].get("label")
    # 5. the fork's two branches are DISTINCT parts of d1 (not the same token), and
    #    each part keeps the SAME token across write → read → egress (data identity)
    assert _edge(g, "A1", "F1")["data"] != _edge(g, "A1", "F2")["data"]
    clean = _edge(g, "A1", "F1")["data"]
    assert _edge(g, "F1", "A2")["data"] == clean == _edge(g, "A2", "W1")["data"]
