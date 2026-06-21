"""Node-driven tests for the proto views' ``getTraceId`` URL parsing.

The P-interactions prototype views (Execution flow + Graphs) are browser
IIFEs that read the trace id out of ``window.location.pathname`` before
fetching ``/proto/interactions/<id>`` / ``/proto/graphs/<id>``. The
original anchored ``^/trace/`` regex returned ``null`` whenever the page
was served under a path prefix (or with a trailing slash), and the
loader then bailed before fetching — surfacing as the "run the prototype
CLI" empty hint even though the scratch tables were populated.

These files are throwaway IIFEs with no module exports, so we can't
``require()`` them like the trace-tree logic. Instead we slice the
``getTraceId`` function body out of the source and run it under Node with
a stubbed ``window``. That still pins the regex behaviour against the
real source text, so a regression in the file is caught.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_UI_DIR = Path(__file__).resolve().parents[2] / "data_governance" / "api" / "ui"
_FILES = [
    _UI_DIR / "graph_view_logic.js",
    _UI_DIR / "execution_flow_logic.js",
]


def _have_node() -> bool:
    return shutil.which("node") is not None


pytestmark = pytest.mark.skipif(
    not _have_node(), reason="node is not installed; skipping UI-logic tests"
)


def _extract_get_trace_id(src: str) -> str:
    """Pull the ``function getTraceId() { ... }`` block out of the source.

    Brace-matches from the function keyword so the test breaks loudly if
    the function is renamed or removed rather than silently passing.
    """
    start = src.index("function getTraceId(")
    i = src.index("{", start)
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[start : j + 1]
    raise AssertionError("unbalanced braces in getTraceId")


def _resolve(fn_src: str, pathname: str) -> str | None:
    script = (
        f"const window = {{ location: {{ pathname: {json.dumps(pathname)} }} }};\n"
        f"{fn_src}\n"
        "const r = getTraceId();\n"
        "process.stdout.write(JSON.stringify(r));\n"
    )
    result = subprocess.run(
        ["node", "-e", script], check=True, capture_output=True, text=True
    )
    return json.loads(result.stdout)


_TID = "8ae1f64d4bb51b750168c6ef1e11a2d8"


@pytest.mark.parametrize("path", [str(f) for f in _FILES])
@pytest.mark.parametrize(
    "pathname, expected",
    [
        (f"/trace/{_TID}", _TID),                 # canonical
        (f"/trace/{_TID}/", _TID),                # trailing slash
        (f"/dg/trace/{_TID}", _TID),              # gateway path prefix
        (f"/trace/{_TID.upper()}", _TID.upper()),  # uppercase hex
    ],
)
def test_get_trace_id_resolves_real_url_shapes(path, pathname, expected):
    fn_src = _extract_get_trace_id(Path(path).read_text())
    assert _resolve(fn_src, pathname) == expected
