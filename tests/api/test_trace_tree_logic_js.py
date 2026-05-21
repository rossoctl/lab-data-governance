"""Node-driven tests for the trace-tree UI's data-layer helpers.

The trace-tree view's non-trivial JS lives in
``data_governance/api/ui/trace_tree_logic.js`` so it can be exercised
from ``node`` without a browser engine. This file is the test shape's
exact mirror of ``test_recent_traces_logic_js.py`` — same Node skipif
guard, same require-and-print pattern.

Helpers under test:

- ``descendantErrorAncestors(spans, parentByChild)`` — returns the set
  of ``(trace_id, span_id)`` keys of every loaded ancestor of an
  ``error === true`` span. The trace-tree UI feeds this set into the
  per-row render to drive the descendant-error badge per
  ``docs/ui-design.md`` §3 / PROJECT.md §8. The "v1 limitation" — that
  errors inside *collapsed* (= not yet loaded) subtrees do NOT
  propagate — is captured by the function only walking ancestors via
  the loaded ``parentByChild`` map.

- ``buildParentIndex(spans)`` — produces the
  ``{(trace_id|span_id): parent_id}`` map the badge walker needs. Wraps
  the boring loop so the trace-tree code does not duplicate it.
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
    / "trace_tree_logic.js"
)


def _have_node() -> bool:
    return shutil.which("node") is not None


pytestmark = pytest.mark.skipif(
    not _have_node(), reason="node is not installed; skipping UI-logic tests"
)


def _run_js(script: str) -> str:
    full = (
        f"const M = require({json.dumps(str(_LOGIC_JS))});\n"
        f"{script}\n"
    )
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


# ---------------------------------------------------------------------------
# buildParentIndex
# ---------------------------------------------------------------------------


def test_build_parent_index_maps_each_span_to_its_parent():
    out = _run_js(
        """
        const spans = [
          { trace_id: 'T', span_id: 'root', parent_id: null },
          { trace_id: 'T', span_id: 'c1',   parent_id: 'root' },
          { trace_id: 'T', span_id: 'c2',   parent_id: 'root' },
          { trace_id: 'T', span_id: 'gc',   parent_id: 'c1' },
        ];
        process.stdout.write(JSON.stringify(M.buildParentIndex(spans)));
        """
    )
    idx = json.loads(out)
    assert idx == {
        "T|root": None,
        "T|c1": "root",
        "T|c2": "root",
        "T|gc": "c1",
    }


# ---------------------------------------------------------------------------
# descendantErrorAncestors
# ---------------------------------------------------------------------------


def test_descendant_error_ancestors_walks_loaded_chain():
    """An ``error === true`` leaf flags every loaded ancestor up to the
    root. The set is keyed by ``trace_id|span_id`` for the UI to
    consult per-row."""
    out = _run_js(
        """
        const spans = [
          { trace_id: 'T', span_id: 'root', parent_id: null, error: false },
          { trace_id: 'T', span_id: 'c1',   parent_id: 'root', error: false },
          { trace_id: 'T', span_id: 'gc',   parent_id: 'c1',   error: true  },
        ];
        const idx = M.buildParentIndex(spans);
        const out = Array.from(M.descendantErrorAncestors(spans, idx)).sort();
        process.stdout.write(JSON.stringify(out));
        """
    )
    keys = json.loads(out)
    assert keys == ["T|c1", "T|root"]


def test_descendant_error_does_not_flag_the_error_span_itself():
    """The error badge is the span's own affair; descendant-error is for
    *ancestors* only, otherwise the row carries both badges."""
    out = _run_js(
        """
        const spans = [
          { trace_id: 'T', span_id: 'root', parent_id: null, error: false },
          { trace_id: 'T', span_id: 'leaf', parent_id: 'root', error: true },
        ];
        const idx = M.buildParentIndex(spans);
        const out = Array.from(M.descendantErrorAncestors(spans, idx));
        process.stdout.write(JSON.stringify(out));
        """
    )
    assert "T|leaf" not in json.loads(out)


def test_descendant_error_handles_multiple_error_spans():
    """Two error leaves share an ancestor — the ancestor appears once."""
    out = _run_js(
        """
        const spans = [
          { trace_id: 'T', span_id: 'root', parent_id: null,  error: false },
          { trace_id: 'T', span_id: 'a',    parent_id: 'root', error: false },
          { trace_id: 'T', span_id: 'b',    parent_id: 'root', error: false },
          { trace_id: 'T', span_id: 'aerr', parent_id: 'a',    error: true  },
          { trace_id: 'T', span_id: 'berr', parent_id: 'b',    error: true  },
        ];
        const idx = M.buildParentIndex(spans);
        const out = Array.from(M.descendantErrorAncestors(spans, idx)).sort();
        process.stdout.write(JSON.stringify(out));
        """
    )
    keys = json.loads(out)
    # root flagged once even though two error descendants reach it.
    assert keys == ["T|a", "T|b", "T|root"]


def test_descendant_error_for_unloaded_subtree_does_not_propagate():
    """v1 limitation. The error span lives in a subtree that has not
    been loaded yet — the parent index does not contain it — so its
    ancestors do NOT receive a descendant-error badge. The user must
    expand the failing branch to surface the badge."""
    out = _run_js(
        """
        // The error span exists in the data but is omitted from the
        // ``spans`` list passed in (simulating "subtree not loaded").
        // descendantErrorAncestors walks only the loaded set and so
        // cannot flag root.
        const loadedSpans = [
          { trace_id: 'T', span_id: 'root', parent_id: null,  error: false },
          { trace_id: 'T', span_id: 'a',    parent_id: 'root', error: false },
        ];
        const idx = M.buildParentIndex(loadedSpans);
        const out = Array.from(M.descendantErrorAncestors(loadedSpans, idx));
        process.stdout.write(JSON.stringify(out));
        """
    )
    assert json.loads(out) == []


def test_descendant_error_appears_progressively_after_subtree_load():
    """Same setup as the v1-limitation test, but now the failing
    subtree has been *loaded* — both the intermediate parent and the
    root receive the badge. This is what "appears progressively as the
    user expands subtrees" looks like at the data-layer."""
    out = _run_js(
        """
        const loadedSpans = [
          { trace_id: 'T', span_id: 'root',   parent_id: null,    error: false },
          { trace_id: 'T', span_id: 'a',      parent_id: 'root',  error: false },
          { trace_id: 'T', span_id: 'a-leaf', parent_id: 'a',     error: true  },
        ];
        const idx = M.buildParentIndex(loadedSpans);
        const out = Array.from(M.descendantErrorAncestors(loadedSpans, idx)).sort();
        process.stdout.write(JSON.stringify(out));
        """
    )
    assert json.loads(out) == ["T|a", "T|root"]


def test_descendant_error_ignores_unset_status():
    """OTLP UNSET status maps to ``error === null``; only ``true`` is a
    failure. ``null``/``false`` must not flag ancestors."""
    out = _run_js(
        """
        const spans = [
          { trace_id: 'T', span_id: 'root', parent_id: null,  error: false },
          { trace_id: 'T', span_id: 'unset', parent_id: 'root', error: null },
          { trace_id: 'T', span_id: 'ok',    parent_id: 'root', error: false },
        ];
        const idx = M.buildParentIndex(spans);
        const out = Array.from(M.descendantErrorAncestors(spans, idx));
        process.stdout.write(JSON.stringify(out));
        """
    )
    assert json.loads(out) == []
