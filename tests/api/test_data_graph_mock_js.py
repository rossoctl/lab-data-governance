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


def test_t1_precision_fork_from_one_transform():
    """Axis 1 (T1): keywords and summary FORK from the same transform output d′ —
    one clean, one confidential. The split the execution forest cannot show."""
    g = _graph()
    assert _node(g, "L")["type"] == "transform"
    f_kw, f_sum = _edge(g, "L", "F1"), _edge(g, "L", "F2")
    assert f_kw["fork"] and f_sum["fork"], "both fork edges leave the same d′ node"
    assert f_kw["verdict"] == "clean" and f_sum["verdict"] == "confidential"
    # the files themselves carry the truth-table verdict + tag
    assert (_node(g, "F1")["verdict"], _node(g, "F1")["tag"]) == ("clean", "TN")
    assert (_node(g, "F2")["verdict"], _node(g, "F2")["tag"]) == ("confidential", "TP")


def test_t2_recall_leak_on_summary_only():
    """Axis 2 (T2): web_search(summary) is the leak (confidential, TP); only it is
    flagged — web_search(keywords) is approved (clean, TN)."""
    g = _graph()
    assert _node(g, "W1")["verdict"] == "clean" and _node(g, "W1")["tag"] == "TN"
    assert _node(g, "W2")["verdict"] == "confidential" and _node(g, "W2")["tag"] == "TP"
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
